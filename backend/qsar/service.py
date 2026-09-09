"""QSAR property prediction service.

Models are trained once (see backend/qsar/baseline.py) and loaded once at process
startup, matching the retrosynthesis pattern - not per request.

Adding a second property is a PROPERTY_REGISTRY entry plus a trained artifact, not
a new endpoint: the route layer reads the registry.
"""
from __future__ import annotations

import pickle
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from backend.molrepr.service import InvalidSmilesError
from backend.qsar import baseline
from backend.qsar.applicability import ApplicabilityIndex

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "models/qsar"


class UnknownPropertyError(ValueError):
    """Property or model name not in the registry -> HTTP 400."""


@dataclass(frozen=True)
class ModelSpec:
    name: str
    featurizer: Callable[[list[str]], np.ndarray]
    description: str
    # Held-out test metrics on the SCAFFOLD split, reported with every prediction
    # so a caller sees the number and its expected error together.
    test_rmse: float
    test_mae: float
    test_r2: float


@dataclass(frozen=True)
class PropertySpec:
    name: str
    units: str
    description: str
    dataset: str
    default_model: str
    models: dict[str, ModelSpec]


# Filled in by train_and_cache() / loaded from disk; metrics are written at train
# time so they cannot drift from the served artifact.
PROPERTY_REGISTRY: dict[str, PropertySpec] = {}

SOLUBILITY_ARTIFACT = MODEL_DIR / "solubility" / "baseline.pkl"


def _register_solubility(artifact: dict) -> None:
    metrics = artifact["metrics"]
    models = {
        name: ModelSpec(
            name=name,
            featurizer=baseline.FEATURIZERS[artifact["featurizers"][name]],
            description=artifact["descriptions"][name],
            test_rmse=metrics[name]["rmse"],
            test_mae=metrics[name]["mae"],
            test_r2=metrics[name]["r2"],
        )
        for name in artifact["models"]
    }
    PROPERTY_REGISTRY["solubility"] = PropertySpec(
        name="solubility",
        units="log10(mol/L)",
        description="Aqueous solubility at 25 C, log molar",
        dataset=artifact["dataset"],
        default_model=artifact["default_model"],
        models=models,
    )


class QsarService:
    """Loads every trained property model once and answers predictions."""

    def __init__(self, artifact_path: Path = SOLUBILITY_ARTIFACT) -> None:
        if not artifact_path.exists():
            raise FileNotFoundError(
                f"{artifact_path} not found - run: python -m backend.qsar.train"
            )
        started = time.time()
        with artifact_path.open("rb") as fh:
            artifact = pickle.load(fh)

        self._estimators: dict[tuple[str, str], object] = {
            ("solubility", name): estimator
            for name, estimator in artifact["models"].items()
        }
        _register_solubility(artifact)

        # One index per property: the applicability question is "unlike this
        # model's training set", which differs per property.
        self._domains = {
            "solubility": ApplicabilityIndex(artifact["training_smiles"])
        }
        self.ready = True
        self.load_time_seconds = round(time.time() - started, 2)

    def properties(self) -> list[dict]:
        return [
            {
                "property": spec.name,
                "units": spec.units,
                "description": spec.description,
                "dataset": spec.dataset,
                "default_model": spec.default_model,
                "models": [
                    {
                        "model": model.name,
                        "description": model.description,
                        "test_rmse": model.test_rmse,
                        "test_mae": model.test_mae,
                        "test_r2": model.test_r2,
                    }
                    for model in spec.models.values()
                ],
            }
            for spec in PROPERTY_REGISTRY.values()
        ]

    def predict(
        self, smiles: str, property_name: str = "solubility", model: str | None = None
    ) -> dict:
        spec = PROPERTY_REGISTRY.get(property_name)
        if spec is None:
            raise UnknownPropertyError(
                f"unknown property {property_name!r}; available: "
                f"{sorted(PROPERTY_REGISTRY)}"
            )
        model_name = model or spec.default_model
        model_spec = spec.models.get(model_name)
        if model_spec is None:
            raise UnknownPropertyError(
                f"unknown model {model_name!r} for {property_name}; available: "
                f"{sorted(spec.models)}"
            )

        if not isinstance(smiles, str) or not smiles.strip():
            raise InvalidSmilesError("smiles must be a non-empty string")

        # Raises InvalidSmilesError for unparseable input -> 400 at the route layer.
        features = model_spec.featurizer([smiles])
        estimator = self._estimators[(property_name, model_name)]
        value = float(estimator.predict(features)[0])

        return {
            "property": spec.name,
            "predicted_value": round(value, 4),
            "units": spec.units,
            "model_used": model_name,
            "model_performance": {
                "test_rmse": model_spec.test_rmse,
                "test_mae": model_spec.test_mae,
                "test_r2": model_spec.test_r2,
                "split": "scaffold",
                "note": (
                    "Held-out scaffold-split metrics. The prediction should be read "
                    f"as roughly +/- {model_spec.test_rmse:.2f} {spec.units}."
                ),
            },
            "applicability": self._domains[property_name].score(smiles).to_dict(),
        }
