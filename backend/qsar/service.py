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
from backend.qsar.conformal import Calibration

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "models/qsar"


class UnknownPropertyError(ValueError):
    """Property or model name not in the registry -> HTTP 400."""


class UncalibratedAlphaError(ValueError):
    """An alpha with no measured coverage -> HTTP 400.

    Interpolating between calibrated alphas would hand back an interval whose
    real coverage nobody has checked, which is the whole thing conformal is
    supposed to stop.
    """


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
    conformal: Calibration | None = None


@dataclass(frozen=True)
class PropertySpec:
    name: str
    units: str
    description: str
    dataset: str
    default_model: str
    models: dict[str, ModelSpec]
    # "regression" -> conformal intervals. Classification (Tox21) will produce
    # conformal prediction SETS from the same calibration machinery; the
    # uncertainty block is built by task type, not assumed to be an interval.
    task_type: str = "regression"
    alphas: tuple[float, ...] = ()


# Filled in by train_and_cache() / loaded from disk; metrics are written at train
# time so they cannot drift from the served artifact.
PROPERTY_REGISTRY: dict[str, PropertySpec] = {}

SOLUBILITY_ARTIFACT = MODEL_DIR / "solubility" / "baseline.pkl"


def _register_solubility(artifact: dict) -> None:
    metrics = artifact["metrics"]
    conformal = artifact.get("conformal", {})
    models = {
        name: ModelSpec(
            name=name,
            featurizer=baseline.FEATURIZERS[artifact["featurizers"][name]],
            description=artifact["descriptions"][name],
            test_rmse=metrics[name]["rmse"],
            test_mae=metrics[name]["mae"],
            test_r2=metrics[name]["r2"],
            conformal=conformal.get(name),
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
        task_type=artifact.get("task_type", "regression"),
        alphas=tuple(artifact.get("alphas", ())),
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
                "task_type": spec.task_type,
                "calibrated_alphas": list(spec.alphas),
                "models": [
                    {
                        "model": model.name,
                        "description": model.description,
                        "test_rmse": model.test_rmse,
                        "test_mae": model.test_mae,
                        "test_r2": model.test_r2,
                        # A list, not a dict keyed by float: JSON stringifies
                        # numeric keys, and this is the nominal -> empirical
                        # mapping a caller needs to pick alpha by real coverage.
                        "coverage": (
                            [
                                {
                                    "alpha": alpha,
                                    "nominal": round(1 - alpha, 4),
                                    "empirical": model.conformal.empirical_coverage[alpha],
                                    # Coverage is itself an estimate from a finite
                                    # test set; adjacent rows overlap.
                                    "empirical_ci_95": list(
                                        model.conformal.coverage_ci.get(alpha, ())
                                    ),
                                    "n_test": model.conformal.n_test,
                                    "mean_width": model.conformal.mean_width[alpha],
                                }
                                for alpha in sorted(model.conformal.quantiles)
                            ]
                            if model.conformal
                            else []
                        ),
                    }
                    for model in spec.models.values()
                ],
            }
            for spec in PROPERTY_REGISTRY.values()
        ]

    @staticmethod
    def _uncertainty(spec: PropertySpec, model_spec: ModelSpec, value: float, alpha: float) -> dict:
        """Conformal uncertainty, shaped by task type.

        Regression yields an interval. Classification (Tox21 next) will yield a
        prediction SET from the same calibrated quantiles - hence the dispatch
        here rather than an interval baked into predict().
        """
        calibration = model_spec.conformal
        if calibration is None:
            return {}
        if alpha not in calibration.quantiles:
            raise UncalibratedAlphaError(
                f"alpha {alpha} is not calibrated for {spec.name}/{model_spec.name}; "
                f"calibrated values: {sorted(calibration.quantiles)}. "
                "Interpolating would return an interval whose real coverage is unmeasured."
            )
        if spec.task_type != "regression":
            raise UncalibratedAlphaError(
                f"no conformal implementation for task_type {spec.task_type!r}"
            )

        half_width = calibration.half_width(alpha)
        coverage = calibration.empirical_coverage.get(alpha)
        ci = calibration.coverage_ci.get(alpha)
        ci_text = f" (95% CI {ci[0]:.0%}-{ci[1]:.0%}, n={calibration.n_test})" if ci else ""
        return {
            "lower": round(value - half_width, 4),
            "upper": round(value + half_width, 4),
            "alpha": alpha,
            "nominal_coverage": round(1 - alpha, 4),
            "method": f"split-conformal/{calibration.method}",
            "empirical_coverage": coverage,
            "empirical_coverage_ci_95": list(ci) if ci else [],
            "n_calibration": calibration.n_calibration,
            "n_test": calibration.n_test,
            "note": (
                f"Nominal {1 - alpha:.0%} interval; MEASURED coverage on the held-out "
                f"test set is {coverage:.0%}{ci_text}. Coverage falls short of nominal because "
                "the scaffold split puts calibration and test in different scaffold "
                "distributions, violating the exchangeability conformal assumes. "
                "Coverage is marginal across the test distribution, NOT a "
                "molecule-specific confidence for this compound."
            ),
        }

    def predict(
        self,
        smiles: str,
        property_name: str = "solubility",
        model: str | None = None,
        alpha: float = 0.1,
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
            "prediction_interval": self._uncertainty(spec, model_spec, value, alpha),
            "applicability": self._domains[property_name].score(smiles).to_dict(),
        }
