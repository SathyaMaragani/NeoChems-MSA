"""Train and persist the served solubility models.

Refits on scaffold train+val (the split's test set stays untouched, so the metrics
stored in the artifact remain honest held-out numbers), then pickles the estimators
together with their metrics and training SMILES.

    python -m backend.qsar.train
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np

from backend.qsar.baseline import (
    FEATURIZERS,
    evaluate,
    labels_of,
    make_models,
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


def main() -> int:
    compounds, report = load()
    print(report.summary())

    train, val, test = scaffold_split(compounds)
    fit_smiles = [c.smiles for c in train + val]
    y_fit = labels_of(train + val)
    test_smiles = [c.smiles for c in test]
    y_test = labels_of(test)

    models: dict[str, object] = {}
    metrics: dict[str, dict[str, float]] = {}
    featurizer_keys: dict[str, str] = {}
    descriptions: dict[str, str] = {}

    print(f"\nfitting on {len(fit_smiles)} (train+val), holding out {len(test_smiles)}")
    for name, (feature_key, estimator_key, description) in SERVED.items():
        featurizer = FEATURIZERS[feature_key]
        estimator = make_models()[estimator_key]
        estimator.fit(featurizer(fit_smiles), y_fit)
        scores = evaluate(estimator, featurizer(test_smiles), y_test)
        models[name] = estimator
        metrics[name] = {"rmse": round(scores.rmse, 4), "mae": round(scores.mae, 4),
                         "r2": round(scores.r2, 4)}
        featurizer_keys[name] = feature_key
        descriptions[name] = description
        print(f"  {name:12s} {scores}")

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    with ARTIFACT.open("wb") as fh:
        pickle.dump(
            {
                "models": models,
                "metrics": metrics,
                "featurizers": featurizer_keys,
                "descriptions": descriptions,
                "default_model": DEFAULT_MODEL,
                "dataset": f"ESOL / Delaney, {report.unique} unique compounds",
                # Applicability is scored against what the served model actually
                # saw, so train+val, not train alone.
                "training_smiles": fit_smiles,
            },
            fh,
        )
    size_mb = ARTIFACT.stat().st_size / 1e6
    print(f"\nwrote {ARTIFACT.relative_to(ROOT)}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
