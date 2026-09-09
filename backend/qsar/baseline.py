"""Baselines: Random Forest / XGBoost over three featurizations.

Fingerprints come from backend.molrepr.service with include_chirality=True, per
the "Which fingerprint column to read" table in backend/molrepr/README.md: the
stereo-blind flavour is for scaffold-hopping search, not property prediction.

Descriptors are here because Delaney's original ESOL model used exactly this kind
of global physicochemistry and was competitive. Solubility is driven by polarity
and surface area, not by which local substructures are present, so Morgan bits may
simply be the wrong question. This tests that directly.

    python -m backend.qsar.baseline
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors, rdMolDescriptors
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from xgboost import XGBRegressor

from backend.molrepr import service
from backend.qsar.dataset import Compound, load
from backend.qsar.splits import describe, random_split, scaffold_split

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "models/qsar/solubility"
SEED = 0xC0FFEE

DESCRIPTOR_NAMES = ["logP", "MW", "TPSA", "rotatable_bonds", "aromatic_proportion"]


def descriptors(smiles: str) -> list[float]:
    """Delaney's physicochemical set: logP, MW, TPSA, rotatable bonds, aromatic
    proportion. Global properties, which is what solubility actually depends on."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise service.InvalidSmilesError(f"could not parse SMILES: {smiles!r}")
    heavy = mol.GetNumHeavyAtoms()
    aromatic = sum(1 for atom in mol.GetAtoms() if atom.GetIsAromatic())
    return [
        Crippen.MolLogP(mol),
        Descriptors.MolWt(mol),
        rdMolDescriptors.CalcTPSA(mol),
        rdMolDescriptors.CalcNumRotatableBonds(mol),
        aromatic / heavy if heavy else 0.0,
    ]


def featurize_fingerprint(smiles_list: list[str]) -> np.ndarray:
    features = np.zeros((len(smiles_list), service.DEFAULT_N_BITS), dtype=np.float32)
    for row, smiles in enumerate(smiles_list):
        fingerprint = service.morgan_fingerprint(smiles, include_chirality=True)
        for bit in fingerprint.GetOnBits():
            features[row, bit] = 1.0
    return features


def featurize_descriptors(smiles_list: list[str]) -> np.ndarray:
    return np.array([descriptors(s) for s in smiles_list], dtype=np.float32)


def featurize_combined(smiles_list: list[str]) -> np.ndarray:
    return np.hstack(
        [featurize_fingerprint(smiles_list), featurize_descriptors(smiles_list)]
    )


FEATURIZERS = {
    "fingerprint": featurize_fingerprint,
    "descriptors": featurize_descriptors,
    "fp+desc": featurize_combined,
}


@dataclass
class Metrics:
    rmse: float
    mae: float
    r2: float

    def __str__(self) -> str:
        return f"RMSE {self.rmse:5.3f}  MAE {self.mae:5.3f}  R2 {self.r2:6.3f}"


def evaluate(model, features: np.ndarray, labels: np.ndarray) -> Metrics:
    predicted = model.predict(features)
    return Metrics(
        rmse=float(np.sqrt(mean_squared_error(labels, predicted))),
        mae=float(mean_absolute_error(labels, predicted)),
        r2=float(r2_score(labels, predicted)),
    )


def make_models() -> dict[str, object]:
    return {
        "random_forest": RandomForestRegressor(
            n_estimators=500, n_jobs=-1, random_state=SEED
        ),
        "xgboost": XGBRegressor(
            n_estimators=500,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            n_jobs=-1,
            random_state=SEED,
        ),
    }


def labels_of(compounds: list[Compound]) -> np.ndarray:
    return np.array([c.label for c in compounds], dtype=np.float64)


def main() -> None:
    compounds, report = load()
    print(report.summary())
    print()

    splits = {"scaffold": scaffold_split(compounds), "random": random_split(compounds)}
    for name, split in splits.items():
        print(describe(name, split, compounds))
    print()

    results: dict[tuple[str, str, str], Metrics] = {}
    for split_name, (train, _val, test) in splits.items():
        train_smiles = [c.smiles for c in train]
        test_smiles = [c.smiles for c in test]
        y_train, y_test = labels_of(train), labels_of(test)

        for feature_name, featurizer in FEATURIZERS.items():
            x_train = featurizer(train_smiles)
            x_test = featurizer(test_smiles)
            for model_name, model in make_models().items():
                started = time.time()
                model.fit(x_train, y_train)
                metrics = evaluate(model, x_test, y_test)
                results[(split_name, feature_name, model_name)] = metrics
                print(
                    f"  {split_name:9s} {feature_name:12s} {model_name:14s} "
                    f"{metrics}   ({time.time() - started:.1f}s)"
                )

    print()
    print(f"{'features':13s} {'model':15s} {'scaffold R2':>12s} {'random R2':>11s} {'gap':>7s}")
    for feature_name in FEATURIZERS:
        for model_name in make_models():
            scaffold_r2 = results[("scaffold", feature_name, model_name)].r2
            random_r2 = results[("random", feature_name, model_name)].r2
            print(
                f"{feature_name:13s} {model_name:15s} {scaffold_r2:12.3f} "
                f"{random_r2:11.3f} {random_r2 - scaffold_r2:7.3f}"
            )

    best = min(
        ((k, v) for k, v in results.items() if k[0] == "scaffold"),
        key=lambda kv: kv[1].rmse,
    )
    print()
    print(f"best on scaffold split: {best[0][1]} + {best[0][2]}  ->  {best[1]}")


if __name__ == "__main__":
    main()
