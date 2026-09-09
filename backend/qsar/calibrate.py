"""Does max-Tanimoto-to-training actually predict error?

The applicability threshold started as a guessed 0.4. This measures whether error
genuinely rises as similarity falls, on the held-out scaffold test set, and what
cutoff (if any) the data supports.

If error does not rise, max Tanimoto is not a useful domain signal for this model
and the flag is claiming more than it knows.

    python -m backend.qsar.calibrate
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np

from backend.molrepr import search, service
from backend.qsar.baseline import FEATURIZERS, labels_of
from backend.qsar.dataset import load
from backend.qsar.splits import scaffold_split

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "models/qsar/solubility/baseline.pkl"

BINS = [(0.0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 1.01)]


def max_train_similarity(smiles: str, candidates) -> float:
    query_fp = service.morgan_fingerprint(smiles)
    ranked = search.rank_by_similarity(query_fp, candidates, top_n=1)
    return float(ranked[0][1]) if ranked else 0.0


def rmse(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(values**2)))


def main() -> int:
    with ARTIFACT.open("rb") as fh:
        artifact = pickle.load(fh)
    model = artifact["models"][artifact["default_model"]]
    featurizer = FEATURIZERS[artifact["featurizers"][artifact["default_model"]]]
    training_smiles = artifact["training_smiles"]

    compounds, _ = load()
    _train, _val, test = scaffold_split(compounds)
    test_smiles = [c.smiles for c in test]
    actual = labels_of(test)
    predicted = model.predict(featurizer(test_smiles))
    error = np.abs(actual - predicted)

    candidates = [(s, service.morgan_fingerprint(s)) for s in training_smiles]
    similarity = np.array(
        [max_train_similarity(s, candidates) for s in test_smiles]
    )

    print(f"scaffold test set: {len(test_smiles)} molecules")
    print(f"served model     : {artifact['default_model']}")
    print(f"overall RMSE     : {rmse(actual - predicted):.3f}\n")

    print(f"{'similarity bin':>16s} {'n':>5s} {'RMSE':>7s} {'MAE':>7s} {'max err':>8s}")
    print("-" * 48)
    for low, high in BINS:
        mask = (similarity >= low) & (similarity < high)
        n = int(mask.sum())
        if n == 0:
            print(f"{f'{low:.1f}-{high:.1f}':>16s} {n:5d}       -       -        -")
            continue
        residual = (actual - predicted)[mask]
        print(
            f"{f'{low:.1f}-{high:.1f}':>16s} {n:5d} {rmse(residual):7.3f} "
            f"{np.mean(np.abs(residual)):7.3f} {np.max(np.abs(residual)):8.3f}"
        )

    print()
    # Rank correlation is the honest summary: does error trend with similarity at
    # all, independent of where any cutoff is drawn?
    order_sim = np.argsort(np.argsort(similarity))
    order_err = np.argsort(np.argsort(error))
    n = len(similarity)
    spearman = 1 - 6 * np.sum((order_sim - order_err) ** 2) / (n * (n**2 - 1))
    pearson = float(np.corrcoef(similarity, error)[0, 1])
    print(f"Spearman(similarity, |error|) = {spearman:+.3f}")
    print(f"Pearson (similarity, |error|) = {pearson:+.3f}")
    print("  (negative = less similar means larger error, which is what the flag assumes)")

    print()
    print("Split at each candidate threshold:")
    print(f"{'threshold':>10s} {'n below':>8s} {'RMSE below':>11s} "
          f"{'n above':>8s} {'RMSE above':>11s} {'ratio':>7s}")
    print("-" * 62)
    for threshold in (0.25, 0.3, 0.35, 0.4, 0.45, 0.5):
        below = similarity < threshold
        above = ~below
        if below.sum() < 3 or above.sum() < 3:
            continue
        rmse_below = rmse((actual - predicted)[below])
        rmse_above = rmse((actual - predicted)[above])
        print(
            f"{threshold:10.2f} {int(below.sum()):8d} {rmse_below:11.3f} "
            f"{int(above.sum()):8d} {rmse_above:11.3f} "
            f"{rmse_below / rmse_above:7.2f}"
        )

    # Eyeballing bin RMSEs on n=113 invites seeing trends that are not there.
    rng = np.random.default_rng(0xC0FFEE)
    observed = float(np.corrcoef(similarity, error)[0, 1])
    null = np.array(
        [np.corrcoef(rng.permutation(similarity), error)[0, 1] for _ in range(10_000)]
    )
    p_value = float(np.mean(np.abs(null) >= abs(observed)))
    print()
    print(f"permutation test on Pearson: p = {p_value:.3f} "
          f"({'no detectable relationship' if p_value > 0.05 else 'significant'})")

    print()
    print(f"{'threshold':>10s} {'ratio':>7s}   95% CI (bootstrap)   verdict")
    residual = actual - predicted
    for threshold in (0.25, 0.3, 0.35, 0.4, 0.5):
        ratios = []
        for _ in range(4000):
            idx = rng.integers(0, len(similarity), len(similarity))
            sampled_sim, sampled_res = similarity[idx], residual[idx]
            below, above = sampled_res[sampled_sim < threshold], sampled_res[sampled_sim >= threshold]
            if len(below) < 3 or len(above) < 3:
                continue
            ratios.append(rmse(below) / rmse(above))
        low_ci, high_ci = np.percentile(ratios, [2.5, 97.5])
        observed_ratio = rmse(residual[similarity < threshold]) / rmse(
            residual[similarity >= threshold]
        )
        verdict = "includes 1.0 -> no effect" if low_ci <= 1.0 <= high_ci else "EXCLUDES 1.0"
        print(f"{threshold:10.2f} {observed_ratio:7.2f}   [{low_ci:.2f}, {high_ci:.2f}]"
              f"          {verdict}")

    print()
    print(f"similarity range on test set: {similarity.min():.3f} - {similarity.max():.3f}, "
          f"median {np.median(similarity):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
