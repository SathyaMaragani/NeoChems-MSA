"""Train, conformally calibrate, and persist the served solubility models.

INVARIANT - THE CALIBRATION SET MUST NEVER BE IN THE SERVED MODEL'S TRAINING DATA.

Models are fit on the scaffold split's TRAIN portion only. `val` is the conformal
calibration set and `test` measures coverage; neither is ever fit on. An earlier
version fit on train+val, which made val in-sample and would have produced
conformal intervals 6.8x too narrow (90th percentile |residual| 0.225 on val
versus 1.519 on genuinely held-out data). That failure ships quietly - every
number still looks plausible from the outside - so the split is enforced here, in
one place, rather than left to a convention.

Cost of the invariant: 111 fewer training molecules, test RMSE 0.964 -> 0.988.

    python -m backend.qsar.train
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np

from backend.qsar.baseline import FEATURIZERS, evaluate, labels_of, make_models
from backend.qsar.conformal import (
    ALPHAS,
    Calibration,
    conformal_quantile,
    wilson_interval,
)
from backend.qsar.dataset import load
from backend.qsar.splits import scaffold_split

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "models/qsar/solubility/baseline.pkl"

# name -> (featurizer key, estimator key, human description)
SERVED = {
    "baseline": (
        "fp+desc",
        "xgboost",
        "XGBoost over chirality-aware Morgan bits + RDKit descriptors "
        "(logP, MW, TPSA, rotatable bonds, aromatic proportion)",
    ),
    "descriptors": (
        "descriptors",
        "xgboost",
        "XGBoost over RDKit physicochemical descriptors only",
    ),
    "fingerprint": (
        "fingerprint",
        "xgboost",
        "XGBoost over chirality-aware Morgan fingerprints only",
    ),
}
DEFAULT_MODEL = "baseline"
TASK_TYPE = "regression"


def main() -> int:
    compounds, report = load()
    print(report.summary())

    train, val, test = scaffold_split(compounds)
    train_smiles = [c.smiles for c in train]
    cal_smiles = [c.smiles for c in val]
    test_smiles = [c.smiles for c in test]
    y_train, y_cal, y_test = labels_of(train), labels_of(val), labels_of(test)

    assert not (set(train_smiles) & set(cal_smiles)), "calibration set leaked into train"

    models: dict[str, object] = {}
    metrics: dict[str, dict[str, float]] = {}
    featurizer_keys: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    conformal: dict[str, Calibration] = {}

    print(
        f"\nfitting on {len(train_smiles)} (train only) | "
        f"calibrating on {len(cal_smiles)} (val) | "
        f"measuring coverage on {len(test_smiles)} (test)"
    )
    for name, (feature_key, estimator_key, description) in SERVED.items():
        featurizer = FEATURIZERS[feature_key]
        estimator = make_models()[estimator_key]
        estimator.fit(featurizer(train_smiles), y_train)

        scores = evaluate(estimator, featurizer(test_smiles), y_test)
        models[name] = estimator
        metrics[name] = {
            "rmse": round(scores.rmse, 4),
            "mae": round(scores.mae, 4),
            "r2": round(scores.r2, 4),
        }
        featurizer_keys[name] = feature_key
        descriptions[name] = description

        # Plain split conformal. Normalized variants were evaluated and rejected:
        # their widths do not correlate with error (p >= 0.25), so a varying width
        # would assert a molecule-specific difficulty the model cannot actually
        # detect. See backend/qsar/conformal.py.
        cal_residuals = np.abs(y_cal - estimator.predict(featurizer(cal_smiles)))
        calibration = Calibration(
            method="plain",
            quantiles={a: conformal_quantile(cal_residuals, a) for a in ALPHAS},
            n_calibration=len(cal_residuals),
            n_test=len(test_smiles),
        )

        # Coverage is MEASURED on the untouched test set, never assumed from alpha.
        test_residuals = np.abs(y_test - estimator.predict(featurizer(test_smiles)))
        for alpha in ALPHAS:
            half = calibration.quantiles[alpha]
            covered = int((test_residuals <= half).sum())
            n = len(test_residuals)
            # Two decimals: a third would imply precision n=113 cannot support.
            calibration.empirical_coverage[alpha] = round(covered / n, 2)
            low, high = wilson_interval(covered, n)
            calibration.coverage_ci[alpha] = (round(low, 2), round(high, 2))
            calibration.mean_width[alpha] = round(2 * half, 4)
        conformal[name] = calibration

        coverage = "  ".join(
            f"a={a}: {calibration.empirical_coverage[a]:.2f} "
            f"[{calibration.coverage_ci[a][0]:.2f},{calibration.coverage_ci[a][1]:.2f}]"
            for a in ALPHAS
        )
        print(f"  {name:12s} {scores}   coverage {coverage}")

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    with ARTIFACT.open("wb") as fh:
        pickle.dump(
            {
                "models": models,
                "metrics": metrics,
                "featurizers": featurizer_keys,
                "descriptions": descriptions,
                "default_model": DEFAULT_MODEL,
                "task_type": TASK_TYPE,
                "conformal": conformal,
                "alphas": list(ALPHAS),
                "dataset": f"ESOL / Delaney, {report.unique} unique compounds",
                # Applicability is scored against what the model actually saw.
                "training_smiles": train_smiles,
            },
            fh,
        )
    print(f"\nwrote {ARTIFACT.relative_to(ROOT)}  ({ARTIFACT.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
