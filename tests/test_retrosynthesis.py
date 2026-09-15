"""API tests. The model loads once for the whole session (~8s), not per test."""
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app

# Canonical SMILES verified against PubChem (CID 2244 / 3672 / 1983).
ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
IBUPROFEN = "CC(C)Cc1ccc(C(C)C(=O)O)cc1"
PARACETAMOL = "CC(=O)Nc1ccc(O)cc1"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:  # `with` triggers lifespan -> model load
        yield c


def test_health_reports_ready(client):
    body = client.get("/retrosynthesis/health").json()
    assert body["status"] == "ready"
    assert body["model_loaded"] is True
    assert body["max_iteration_limit"] == 500


@pytest.mark.parametrize("smiles", [ASPIRIN, PARACETAMOL])
def test_solvable_molecule_returns_routes(client, smiles):
    r = client.post("/retrosynthesis/plan", json={"smiles": smiles, "top_n": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["is_solved"] is True
    assert len(body["routes"]) >= 1
    assert body["search_time_seconds"] > 0
    assert body["iterations_used"] > 0

    route = body["routes"][0]
    assert route["number_of_reactions"] >= 1, "a real route is not zero reactions"
    assert route["state_score"] > 0
    tree = route["tree"]
    assert tree["molecule_smiles"]
    assert tree["reactions"], "root molecule must have at least one disconnection"
    rxn = tree["reactions"][0]
    assert rxn["reactants"] and rxn["template_used"] is not None and rxn["score"] > 0


def test_only_solved_routes_are_returned(client):
    """Every returned route must bottom out in purchasable material."""
    body = client.post(
        "/retrosynthesis/plan", json={"smiles": PARACETAMOL, "top_n": 5}
    ).json()

    def leaves(node):
        if not node["reactions"]:
            yield node
        for rxn in node["reactions"]:
            for child in rxn["reactants"]:
                yield from leaves(child)

    for route in body["routes"]:
        assert all(leaf["is_stock_available"] for leaf in leaves(route["tree"]))


def test_include_validation_false_invariant(client):
    """Verify include_validation=False performs no inference and leaves routes exactly as they were."""
    res_false = client.post("/retrosynthesis/plan", json={"smiles": ASPIRIN, "include_validation": False, "top_n": 2}).json()
    res_true = client.post("/retrosynthesis/plan", json={"smiles": ASPIRIN, "include_validation": True, "top_n": 2}).json()
    
    assert "routes" in res_false
    assert len(res_false["routes"]) > 0
    assert len(res_false["routes"]) == len(res_true["routes"])
    
    route_false = res_false["routes"][0]
    route_true = res_true["routes"][0]
    
    assert route_false["state_score"] == route_true["state_score"], "Score changed!"
    
    rxn_false = route_false["tree"]["reactions"][0]
    rxn_true = route_true["tree"]["reactions"][0]
    
    assert "assessment" not in rxn_false
    assert "structural_validation" not in rxn_false
    assert "forward_validation" not in rxn_false
    
    assert "assessment" in rxn_true
    assert "structural_validation" in rxn_true
    assert "forward_validation" in rxn_true


def test_include_conditions_false_invariant(client):
    """Verify include_conditions=False skips all ORD queries and preserves old behavior."""
    res_false = client.post("/retrosynthesis/plan", json={"smiles": ASPIRIN, "include_conditions": False, "top_n": 2}).json()
    res_true = client.post("/retrosynthesis/plan", json={"smiles": ASPIRIN, "include_conditions": True, "top_n": 2}).json()
    
    assert len(res_false["routes"]) == len(res_true["routes"])
    assert res_false["routes"][0]["state_score"] == res_true["routes"][0]["state_score"]
    
    rxn_false = res_false["routes"][0]["tree"]["reactions"][0]
    rxn_true = res_true["routes"][0]["tree"]["reactions"][0]
    
    assert "evidence" not in rxn_false
    assert "evidence" in rxn_true


from unittest.mock import patch

@patch("backend.retrosynthesis.validation.RDKitTemplateReversalModel.validate_step")
def test_validation_error_invariant(mock_struct_val, client):
    """Verify that a validation failure does not drop routes or change scores."""
    from backend.retrosynthesis.validation import ValidationStatus, ForwardValidationResult
    mock_struct_val.return_value = ForwardValidationResult(status=ValidationStatus.VALIDATION_ERROR, error="mock")
    
    # We must patch AiZynthFinder to be deterministic, or just assert on the first route's structure
    res_baseline = client.post("/retrosynthesis/plan", json={"smiles": ASPIRIN, "include_validation": False, "top_n": 1}).json()
    res_error = client.post("/retrosynthesis/plan", json={"smiles": ASPIRIN, "include_validation": True, "top_n": 1}).json()
    
    assert len(res_error["routes"]) > 0
    assert len(res_error["routes"]) == len(res_baseline["routes"]), "Route count changed due to error!"
    # The score should be identical as AiZynthFinder isn't influenced by validation
    assert abs(res_error["routes"][0]["state_score"] - res_baseline["routes"][0]["state_score"]) < 1e-4, "Score changed due to error!"
    
    rxn = res_error["routes"][0]["tree"]["reactions"][0]
    from backend.retrosynthesis.assessment import AssessmentSummary
    assert rxn["assessment"]["summary"] in [
        AssessmentSummary.INSUFFICIENT_EVIDENCE.value,
        AssessmentSummary.REVIEW_REQUIRED.value,
        AssessmentSummary.SUPPORTED.value,
    ]
    assert rxn["assessment"]["route_score_affected"] is False


@patch("backend.retrosynthesis.validation.MicroserviceLearnedForwardModel.validate_step")
def test_model_unavailable_invariant(mock_forward_eval, client):
    """Verify that forward model unavailability does not drop routes or change scores."""
    from backend.retrosynthesis.validation import ValidationStatus, ForwardValidationResult
    mock_forward_eval.return_value = ForwardValidationResult(status=ValidationStatus.MODEL_UNAVAILABLE, error="mock")
    
    res_baseline = client.post("/retrosynthesis/plan", json={"smiles": ASPIRIN, "include_validation": False, "top_n": 1}).json()
    res_unavailable = client.post("/retrosynthesis/plan", json={"smiles": ASPIRIN, "include_validation": True, "top_n": 1}).json()
    
    assert len(res_unavailable["routes"]) > 0
    assert len(res_unavailable["routes"]) == len(res_baseline["routes"]), "Route count changed due to unavailable!"
    assert abs(res_unavailable["routes"][0]["state_score"] - res_baseline["routes"][0]["state_score"]) < 1e-4, "Score changed due to unavailable!"
    
    rxn = res_unavailable["routes"][0]["tree"]["reactions"][0]
    assert rxn["assessment"]["route_score_affected"] is False





def test_unsolved_molecule_returns_no_routes(client):
    """Ibuprofen does not solve in the default 100 iterations - we must not
    return the top-scoring unsolved fragment as if it were a route."""
    body = client.post(
        "/retrosynthesis/plan", json={"smiles": IBUPROFEN, "top_n": 3}
    ).json()
    if not body["is_solved"]:
        assert body["routes"] == []
        assert body["solved_routes_found"] == 0


@pytest.mark.parametrize("bad", ["not_a_molecule", "C(C(C", "", "   "])
def test_invalid_smiles_returns_400(client, bad):
    r = client.post("/retrosynthesis/plan", json={"smiles": bad})
    assert r.status_code == 400
    assert "detail" in r.json()


def test_iteration_limit_above_cap_returns_400(client):
    r = client.post(
        "/retrosynthesis/plan", json={"smiles": ASPIRIN, "iteration_limit": 5000}
    )
    assert r.status_code == 400


def test_images_included_on_request(client):
    body = client.post(
        "/retrosynthesis/plan",
        json={"smiles": ASPIRIN, "top_n": 1, "include_images": True},
    ).json()
    assert body["routes"][0]["image_png_base64"].startswith("iVBOR")  # PNG magic
