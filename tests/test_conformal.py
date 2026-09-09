"""Conformal prediction intervals.

Requires models/qsar/solubility/baseline.pkl (python -m backend.qsar.train).
"""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.qsar.baseline import FEATURIZERS, labels_of
from backend.qsar.conformal import conformal_quantile
from backend.qsar.dataset import load
from backend.qsar.splits import scaffold_split

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
IBUPROFEN = "CC(C)Cc1ccc(C(C)C(=O)O)cc1"
PARACETAMOL = "CC(=O)Nc1ccc(O)cc1"

CALIBRATED_ALPHAS = [0.05, 0.1, 0.2]

# Coverage on this scaffold split is KNOWN to undershoot nominal - calibration and
# test come from different scaffold distributions, which breaks the exchangeability
# conformal assumes. These bounds pin the measured behaviour so a regression in the
# calibration path is caught; they are deliberately not centred on 1 - alpha.
# Verified against a random split, where coverage does land at ~0.903.
MEASURED_COVERAGE_BOUNDS = {0.05: (0.85, 0.98), 0.1: (0.75, 0.93), 0.2: (0.60, 0.86)}


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def predict(client, **body):
    return client.post("/predict/property", json=body)


def interval(client, smiles=ASPIRIN, **body):
    return predict(client, smiles=smiles, **body).json()["prediction_interval"]


def test_interval_contains_the_point_prediction(client):
    body = predict(client, smiles=ASPIRIN).json()
    bounds = body["prediction_interval"]
    assert bounds["lower"] <= body["predicted_value"] <= bounds["upper"]


@pytest.mark.parametrize("smiles", [ASPIRIN, IBUPROFEN, PARACETAMOL])
def test_interval_is_symmetric_about_the_prediction(client, smiles):
    """Plain split conformal is a constant half-width either side."""
    body = predict(client, smiles=smiles).json()
    bounds = body["prediction_interval"]
    below = body["predicted_value"] - bounds["lower"]
    above = bounds["upper"] - body["predicted_value"]
    assert below == pytest.approx(above, abs=1e-3)


def test_interval_widens_as_alpha_decreases(client):
    """A 95% interval must be wider than an 80% one."""
    widths = {
        alpha: interval(client, alpha=alpha)["upper"] - interval(client, alpha=alpha)["lower"]
        for alpha in CALIBRATED_ALPHAS
    }
    assert widths[0.05] > widths[0.1] > widths[0.2]


def test_plain_conformal_gives_every_molecule_the_same_width(client):
    """Plain conformal knows nothing molecule-specific, and must not pretend to.
    A varying width would assert a difficulty signal the calibration showed the
    model does not have. Tolerance is 2e-4: the bounds are rounded to 4 dp for
    display, so widths can differ in the last place without the half-width
    varying."""
    widths = [
        interval(client, smiles=smiles)["upper"] - interval(client, smiles=smiles)["lower"]
        for smiles in (ASPIRIN, IBUPROFEN, PARACETAMOL)
    ]
    assert max(widths) - min(widths) < 2e-4


def test_response_reports_measured_coverage_not_just_nominal(client):
    bounds = interval(client, alpha=0.1)
    assert bounds["nominal_coverage"] == 0.9
    assert bounds["empirical_coverage"] is not None
    # The whole point: the two differ, and the response says so.
    assert bounds["empirical_coverage"] != bounds["nominal_coverage"]
    assert "marginal" in bounds["note"]
    assert bounds["method"].startswith("split-conformal/")


@pytest.mark.parametrize("alpha", [0.01, 0.15, 0.5, 0.9])
def test_uncalibrated_alpha_returns_400(client, alpha):
    """Interpolating would return an interval whose coverage nobody measured."""
    response = predict(client, smiles=ASPIRIN, alpha=alpha)
    assert response.status_code == 400
    assert "not calibrated" in response.json()["detail"]


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1, 1.5])
def test_out_of_range_alpha_returns_400(client, alpha):
    assert predict(client, smiles=ASPIRIN, alpha=alpha).status_code == 400


def test_interval_supplements_rather_than_replaces_existing_fields(client):
    body = predict(client, smiles=ASPIRIN).json()
    assert body["model_performance"]["test_rmse"] > 0
    assert "structurally_familiar" in body["applicability"]
    assert "prediction_interval" in body


def test_properties_lists_calibrated_alphas_and_task_type(client):
    body = client.get("/predict/properties").json()
    solubility = next(p for p in body["properties"] if p["property"] == "solubility")
    assert solubility["task_type"] == "regression"
    assert sorted(solubility["calibrated_alphas"]) == CALIBRATED_ALPHAS
    served = next(m for m in solubility["models"] if m["model"] == "baseline")
    assert sorted(row["alpha"] for row in served["coverage"]) == CALIBRATED_ALPHAS
    for row in served["coverage"]:
        assert row["nominal"] == pytest.approx(1 - row["alpha"])
        assert 0.0 < row["empirical"] <= 1.0
        assert row["mean_width"] > 0


def test_conformal_quantile_refuses_alphas_the_calibration_set_cannot_certify():
    """With n points the smallest honest alpha is 1/(n+1); below that the
    quantile is undefined and must not silently become the max residual."""
    scores = np.linspace(0.1, 1.0, 20)
    assert np.isfinite(conformal_quantile(scores, 0.1))
    assert conformal_quantile(scores, 0.001) == float("inf")


def test_empirical_coverage_on_test_set_matches_what_the_api_reports():
    """Recompute coverage from scratch on the untouched test set and check the
    number the API hands out is real, not copied from a stale artifact."""
    import pickle
    from backend.qsar.service import SOLUBILITY_ARTIFACT

    with SOLUBILITY_ARTIFACT.open("rb") as fh:
        artifact = pickle.load(fh)

    compounds, _ = load()
    train, _val, test = scaffold_split(compounds)
    assert not (set(c.smiles for c in train) & set(c.smiles for c in _val)), (
        "calibration set must not be in the served model's training data"
    )

    model = artifact["models"]["baseline"]
    featurize = FEATURIZERS[artifact["featurizers"]["baseline"]]
    residuals = np.abs(
        labels_of(test) - model.predict(featurize([c.smiles for c in test]))
    )
    calibration = artifact["conformal"]["baseline"]

    for alpha in CALIBRATED_ALPHAS:
        recomputed = float((residuals <= calibration.quantiles[alpha]).mean())
        assert recomputed == pytest.approx(
            calibration.empirical_coverage[alpha], abs=1e-3
        )
        low, high = MEASURED_COVERAGE_BOUNDS[alpha]
        assert low <= recomputed <= high, (
            f"coverage {recomputed:.3f} at alpha={alpha} outside the documented "
            f"scaffold-split band [{low}, {high}]"
        )
