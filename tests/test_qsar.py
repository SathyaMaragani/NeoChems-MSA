"""QSAR prediction tests. Requires models/qsar/solubility/baseline.pkl:
   python -m backend.qsar.train
"""
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
IBUPROFEN = "CC(C)Cc1ccc(C(C)C(=O)O)cc1"
PARACETAMOL = "CC(=O)Nc1ccc(O)cc1"

# Cisplatin: a square-planar platinum(II) coordination complex. ESOL is entirely
# small organic molecules, so this shares essentially no Morgan bits with the
# training set (max Tanimoto 0.125). Deliberately chosen as out-of-domain.
CISPLATIN = "N.N.Cl[Pt]Cl"
# Perfluorooctane, a second out-of-domain case: a fully fluorinated chain
# (max Tanimoto 0.25).
PERFLUOROOCTANE = "FC(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)F"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def predict(client, **body):
    return client.post("/predict/property", json=body)


def test_properties_lists_solubility_and_models(client):
    body = client.get("/predict/properties").json()
    solubility = next(
        p for p in body["properties"] if p["property"] == "solubility"
    )
    assert solubility["units"] == "log10(mol/L)"
    assert solubility["default_model"] == "baseline"
    names = {m["model"] for m in solubility["models"]}
    assert {"baseline", "descriptors", "fingerprint"} <= names
    # Metrics travel with the listing so a frontend can warn before predicting.
    for model in solubility["models"]:
        assert 0.0 < model["test_rmse"] < 3.0
        assert model["test_r2"] <= 1.0


@pytest.mark.parametrize(
    "name,smiles,low,high",
    [
        # Literature aqueous solubility, log10 mol/L. Wide bands on purpose - the
        # served model's scaffold-split RMSE is ~0.96, so tighter bounds would be
        # testing luck rather than correctness.
        ("aspirin", ASPIRIN, -4.0, 0.0),
        ("ibuprofen", IBUPROFEN, -6.0, -1.5),
        ("paracetamol", PARACETAMOL, -3.0, 0.5),
    ],
)
def test_prediction_is_plausible(client, name, smiles, low, high):
    body = predict(client, smiles=smiles, property="solubility").json()
    assert body["property"] == "solubility"
    assert body["units"] == "log10(mol/L)"
    assert body["model_used"] == "baseline"
    assert low < body["predicted_value"] < high, f"{name} out of plausible range"


def test_prediction_carries_its_own_error_bar(client):
    """A number without its uncertainty gets read as more precise than it is."""
    body = predict(client, smiles=ASPIRIN, property="solubility").json()
    performance = body["model_performance"]
    assert performance["split"] == "scaffold"
    assert performance["test_rmse"] > 0
    assert "+/-" in performance["note"]


def test_applicability_true_for_training_like_molecule(client):
    """Paracetamol is in ESOL, so it should match itself exactly."""
    applicability = predict(
        client, smiles=PARACETAMOL, property="solubility"
    ).json()["applicability"]
    assert applicability["max_train_similarity"] == 1.0
    assert applicability["structurally_familiar"] is True


@pytest.mark.parametrize("smiles", [CISPLATIN, PERFLUOROOCTANE])
def test_applicability_false_for_out_of_domain_molecule(client, smiles):
    body = predict(client, smiles=smiles, property="solubility").json()
    applicability = body["applicability"]
    assert applicability["structurally_familiar"] is False
    assert applicability["max_train_similarity"] < applicability["threshold"]
    # It still returns a number - that is exactly why the flag has to be there.
    assert isinstance(body["predicted_value"], float)


def test_applicability_does_not_claim_reliability(client):
    """The field measures structural novelty. Calibration found no relationship
    between it and error, so the response must not imply one - a reader who takes
    `structurally_familiar` as `prediction is trustworthy` is being misled."""
    applicability = predict(
        client, smiles=ASPIRIN, property="solubility"
    ).json()["applicability"]
    assert "in_domain" not in applicability, "renamed: it asserted trustworthiness"
    assert "structural novelty" in applicability["note"]
    assert "no relationship" in applicability["note"]


def test_model_can_be_chosen_explicitly(client):
    body = predict(
        client, smiles=ASPIRIN, property="solubility", model="descriptors"
    ).json()
    assert body["model_used"] == "descriptors"


@pytest.mark.parametrize("bad", ["not_a_molecule", "C(C(C", "", "   "])
def test_invalid_smiles_returns_400(client, bad):
    response = predict(client, smiles=bad, property="solubility")
    assert response.status_code == 400
    assert "detail" in response.json()


def test_unknown_property_returns_400(client):
    response = predict(client, smiles=ASPIRIN, property="unobtainium")
    assert response.status_code == 400
    assert "unknown property" in response.json()["detail"]


def test_unknown_model_returns_400(client):
    response = predict(client, smiles=ASPIRIN, property="solubility", model="nope")
    assert response.status_code == 400
    assert "unknown model" in response.json()["detail"]
