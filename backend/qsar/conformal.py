"""Split conformal prediction: per-molecule intervals with a coverage guarantee.

Why this exists: the applicability calibration showed max-Tanimoto has no
measurable relationship to error, so the only error information in a prediction
was a single global RMSE, identical for every molecule. Conformal gives an
interval with a finite-sample marginal coverage guarantee instead.

This module is the plain-vs-normalized comparison bench; train.py builds the
served artifact. Both fit on TRAIN ONLY so val is a genuine calibration set - an
earlier train.py fit on train+val, which made val in-sample and would have given
intervals 6.8x too narrow. The test set is only ever touched to measure coverage.

    python -m backend.qsar.conformal
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import KFold

from backend.molrepr import search, service
from backend.qsar.baseline import FEATURIZERS, SEED, labels_of, make_models
from backend.qsar.dataset import load
from backend.qsar.splits import scaffold_split

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "models/qsar/solubility/conformal.pkl"

ALPHAS = (0.05, 0.1, 0.2)
# Stabiliser in the normalized score denominator, so a near-zero difficulty
# estimate cannot blow the score up (Papadopoulos et al.).
BETA = 0.1
KNN_K = 5


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Coverage is itself an estimate from n test molecules. At n=113 the sampling
    error is roughly +/-0.07, so reporting 0.8142 asserts a precision that is not
    there. Wilson rather than normal-approximation because it stays inside [0, 1]
    and behaves at proportions near the ends, where coverage lives.
    """
    if n == 0:
        return (0.0, 1.0)
    phat = successes / n
    denominator = 1 + z**2 / n
    centre = (phat + z**2 / (2 * n)) / denominator
    half = (z / denominator) * math.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2))
    return (max(0.0, centre - half), min(1.0, centre + half))


def conformal_quantile(scores: np.ndarray, alpha: float) -> float:
    """The ceil((n+1)(1-alpha))/n empirical quantile of calibration scores.

    Returns inf when n is too small to certify this alpha - with n calibration
    points the smallest achievable alpha is 1/(n+1), and pretending otherwise
    would quietly hand back an uncovered interval.
    """
    n = len(scores)
    k = math.ceil((n + 1) * (1 - alpha))
    if k > n:
        return float("inf")
    return float(np.sort(scores)[k - 1])


@dataclass
class Calibration:
    """One calibrated method: alpha -> quantile of its nonconformity scores."""

    method: str
    quantiles: dict[float, float]
    n_calibration: int
    # Measured on the held-out test set, not assumed from alpha.
    empirical_coverage: dict[float, float] = field(default_factory=dict)
    # 95% Wilson interval on that measurement. Coverage is an estimate from a
    # finite test set and must carry its own uncertainty like anything else.
    coverage_ci: dict[float, tuple[float, float]] = field(default_factory=dict)
    mean_width: dict[float, float] = field(default_factory=dict)
    n_test: int = 0

    def half_width(self, alpha: float, difficulty: float | None = None) -> float:
        quantile = self.quantiles[alpha]
        if self.method == "normalized":
            if difficulty is None:
                raise ValueError("normalized conformal needs a difficulty estimate")
            return quantile * (difficulty + BETA)
        return quantile


# --- difficulty estimators for normalized conformal ------------------------


def rf_dispersion(forest: RandomForestRegressor, features: np.ndarray) -> np.ndarray:
    """Std of predictions across the Random Forest's trees.

    Not XGBoost's trees: boosting builds additive corrections, so the spread
    across them measures how much the fit kept correcting itself, not predictive
    uncertainty. Random Forest trees are independent estimates of the target, so
    their spread is a genuine dispersion measure.
    """
    per_tree = np.stack([tree.predict(features) for tree in forest.estimators_])
    return per_tree.std(axis=0)


def knn_residual_difficulty(
    query_smiles: list[str],
    reference_smiles: list[str],
    reference_abs_residual: np.ndarray,
    k: int = KNN_K,
) -> np.ndarray:
    """Mean |residual| of the k nearest reference molecules by Tanimoto.

    Reference residuals must be out-of-fold, or this just reports how well the
    model memorised its own training set. Scored through the shared
    rank_by_similarity, same as molecule search and the applicability index.
    """
    reference = [
        (index, service.morgan_fingerprint(smiles))
        for index, smiles in enumerate(reference_smiles)
    ]
    out = np.zeros(len(query_smiles))
    for row, smiles in enumerate(query_smiles):
        ranked = search.rank_by_similarity(
            service.morgan_fingerprint(smiles), reference, top_n=k
        )
        out[row] = np.mean([reference_abs_residual[i] for i, _ in ranked])
    return out


def out_of_fold_residuals(
    features: np.ndarray, labels: np.ndarray, folds: int = 5
) -> np.ndarray:
    """|residual| on training molecules from models that did not see them."""
    residuals = np.zeros(len(labels))
    for train_idx, held_idx in KFold(folds, shuffle=True, random_state=SEED).split(features):
        model = make_models()["xgboost"]
        model.fit(features[train_idx], labels[train_idx])
        residuals[held_idx] = np.abs(labels[held_idx] - model.predict(features[held_idx]))
    return residuals


# --- evaluation -------------------------------------------------------------


def coverage_and_width(
    actual: np.ndarray, predicted: np.ndarray, half_widths: np.ndarray
) -> tuple[float, float]:
    inside = np.abs(actual - predicted) <= half_widths
    return float(inside.mean()), float(2 * half_widths.mean())


def correlation_report(
    width: np.ndarray, error: np.ndarray, rng: np.random.Generator, label: str
) -> str:
    """Does interval width actually track error? Permutation test + bootstrap CI,
    the same treatment the applicability calibration got."""
    if np.allclose(width, width[0]):
        return f"  {label:26s} constant width - correlation undefined by construction"
    observed = float(np.corrcoef(width, error)[0, 1])
    null = np.array(
        [np.corrcoef(rng.permutation(width), error)[0, 1] for _ in range(10_000)]
    )
    p_value = float(np.mean(np.abs(null) >= abs(observed)))
    boot = []
    for _ in range(4000):
        idx = rng.integers(0, len(width), len(width))
        if np.allclose(width[idx], width[idx][0]):
            continue
        boot.append(np.corrcoef(width[idx], error[idx])[0, 1])
    low, high = np.percentile(boot, [2.5, 97.5])
    verdict = "spans 0 -> no signal" if low <= 0 <= high else "EXCLUDES 0"
    return (
        f"  {label:26s} Pearson {observed:+.3f}  p={p_value:.3f}  "
        f"95% CI [{low:+.3f}, {high:+.3f}]  {verdict}"
    )


def main() -> int:
    rng = np.random.default_rng(SEED)
    compounds, _ = load()
    train, val, test = scaffold_split(compounds)

    featurize = FEATURIZERS["fp+desc"]
    x_train, y_train = featurize([c.smiles for c in train]), labels_of(train)
    x_cal, y_cal = featurize([c.smiles for c in val]), labels_of(val)
    x_test, y_test = featurize([c.smiles for c in test]), labels_of(test)

    print(f"train {len(train)} | calibration (val) {len(val)} | test {len(test)}\n")

    # Refit on TRAIN ONLY so the calibration set is genuinely held out.
    model = make_models()["xgboost"]
    model.fit(x_train, y_train)

    served = make_models()["xgboost"]
    served.fit(np.vstack([x_train, x_cal]), np.concatenate([y_train, y_cal]))
    rmse = lambda r: float(np.sqrt(np.mean(r**2)))
    print("cost of refitting on train only, so val can calibrate:")
    print(f"  train+val fit (current served)  test RMSE {rmse(y_test - served.predict(x_test)):.3f}")
    print(f"  train-only fit (conformal)      test RMSE {rmse(y_test - model.predict(x_test)):.3f}\n")

    cal_pred, test_pred = model.predict(x_cal), model.predict(x_test)
    cal_abs = np.abs(y_cal - cal_pred)
    test_abs = np.abs(y_test - test_pred)

    forest = RandomForestRegressor(n_estimators=500, n_jobs=-1, random_state=SEED)
    forest.fit(x_train, y_train)

    oof = out_of_fold_residuals(x_train, y_train)
    train_smiles = [c.smiles for c in train]

    difficulties = {
        "rf_dispersion": (
            rf_dispersion(forest, x_cal),
            rf_dispersion(forest, x_test),
        ),
        "knn_residual": (
            knn_residual_difficulty([c.smiles for c in val], train_smiles, oof),
            knn_residual_difficulty([c.smiles for c in test], train_smiles, oof),
        ),
    }

    calibrations: dict[str, Calibration] = {}

    plain = Calibration("plain", {a: conformal_quantile(cal_abs, a) for a in ALPHAS}, len(cal_abs))
    calibrations["plain"] = plain

    for name, (cal_difficulty, _test_difficulty) in difficulties.items():
        scores = cal_abs / (cal_difficulty + BETA)
        calibrations[f"normalized[{name}]"] = Calibration(
            "normalized", {a: conformal_quantile(scores, a) for a in ALPHAS}, len(scores)
        )

    print("=" * 78)
    print(f"{'method':26s} {'alpha':>6s} {'nominal':>8s} {'empirical':>10s} {'mean width':>11s}")
    print("=" * 78)
    widths_at_10: dict[str, np.ndarray] = {}
    for name, calibration in calibrations.items():
        for alpha in ALPHAS:
            if calibration.method == "plain":
                half = np.full(len(test_abs), calibration.half_width(alpha))
            else:
                key = name[len("normalized[") : -1]
                half = calibration.quantiles[alpha] * (difficulties[key][1] + BETA)
            coverage, width = coverage_and_width(y_test, test_pred, half)
            calibration.empirical_coverage[alpha] = round(coverage, 4)
            calibration.mean_width[alpha] = round(width, 4)
            flag = "" if abs(coverage - (1 - alpha)) <= 0.05 else "   <-- OFF"
            print(f"{name:26s} {alpha:6.2f} {1 - alpha:8.2f} {coverage:10.3f} {width:11.3f}{flag}")
            if alpha == 0.1:
                widths_at_10[name] = 2 * half
        print("-" * 78)

    print("\ndoes interval width track |error| on the test set?  (alpha = 0.1)")
    for name, width in widths_at_10.items():
        print(correlation_report(width, test_abs, rng, name))

    import pickle

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    with ARTIFACT.open("wb") as fh:
        pickle.dump({"calibrations": calibrations, "alphas": list(ALPHAS), "beta": BETA}, fh)
    print(f"\nwrote {ARTIFACT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
