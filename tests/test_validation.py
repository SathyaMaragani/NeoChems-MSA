"""Unit tests for the forward reaction validation layer."""
import pytest
from backend.retrosynthesis.validation import (
    ValidationStatus,
    RDKitTemplateReversalModel,
)


def test_aizynth_template_reversal():
    # Simple reaction
    assert RDKitTemplateReversalModel._reverse_aizynth_template("A>>B") == "B>>A"
    # Multi-reactant
    assert RDKitTemplateReversalModel._reverse_aizynth_template("A>>B.C") == "B.C>>A"
    # Atom-mapped
    assert RDKitTemplateReversalModel._reverse_aizynth_template("[c:1]>>[c:1]-[Br:2]") == "[c:1]-[Br:2]>>[c:1]"
    # With agents
    assert RDKitTemplateReversalModel._reverse_aizynth_template("A>B>C.D") == "C.D>B>A"
    # Malformed
    assert RDKitTemplateReversalModel._reverse_aizynth_template("A.B.C") == "A.B.C"

def test_validation_exact_match():
    model = RDKitTemplateReversalModel()
    
    # Esterification of salicylic acid and acetic anhydride to aspirin
    # AiZynthFinder outputs Product >> Reactants
    template = "[c:1]-[O:2]-[C:3](=[O:4]) >> [c:1]-[OH:2].[C:3](=[O:4])-[O:5]-[C:6](=[O:7])"
    
    # Reactants: Salicylic acid, Acetic anhydride
    reactants = ["O=C(O)c1ccccc1O", "CC(=O)OC(C)=O"]
    # Target: Aspirin
    target = "CC(=O)Oc1ccccc1C(=O)O"
    
    result = model.validate_step(target, reactants, template_smarts=template)
    
    assert result.status == ValidationStatus.MATCH
    # The predicted product canonical SMILES should match the target
    assert result.predicted_product == "CC(=O)Oc1ccccc1C(=O)O"
    assert result.target_product == "CC(=O)Oc1ccccc1C(=O)O"


def test_validation_mismatch_ibuprofen_regression():
    """
    Simulates the ibuprofen failure mode: the MCTS returns a chemically incomplete
    fragment, but claims a template produces it from some precursors.
    """
    model = RDKitTemplateReversalModel()
    
    # AiZynthFinder retrosynthetic template (Product >> Reactants)
    template = "[c:1]-[C:3] >> [c:1]-[Br:2].[C:3]-[B:4]"
    reactants = ["CC(C)Cc1ccc(Br)cc1", "CB(O)O"]
    
    # Target is Ibuprofen
    target = "CC(C)Cc1ccc(C(C)C(=O)O)cc1"
    
    result = model.validate_step(target, reactants, template_smarts=template)
    
    assert result.status == ValidationStatus.MISMATCH
    assert result.predicted_product != result.target_product
    # The product is actually just 1-isobutyl-4-methylbenzene
    assert result.predicted_product == "Cc1ccc(CC(C)C)cc1"


def test_validation_partial_match_stereochemistry():
    model = RDKitTemplateReversalModel()
    
    # Reactant: 2-butene (Z)
    # Template: hydration (Product >> Reactants)
    template = "[C:1](-[O:3])-[C:2] >> [C:1]=[C:2].[O:3]"
    reactants = [r"C/C=C\C", "O"]
    target = "C[C@@H](O)CC"
    
    result = model.validate_step(target, reactants, template_smarts=template)
    
    # The model will predict CC(O)CC without stereochem, target has stereochem.
    # So it should be a PARTIAL_MATCH
    assert result.status == ValidationStatus.PARTIAL_MATCH
    assert result.predicted_product == "CCC(C)O"


def test_validation_error_invalid_smiles():
    model = RDKitTemplateReversalModel()
    result = model.validate_step("INVALID", ["C"], template_smarts="[C:1]>>[C:1]")
    
    assert result.status == ValidationStatus.VALIDATION_ERROR
    assert "Invalid target SMILES" in result.error


def test_validation_error_missing_template():
    model = RDKitTemplateReversalModel()
    result = model.validate_step("C", ["C"])
    
    assert result.status == ValidationStatus.VALIDATION_ERROR
    assert "No template_smarts provided" in result.error


def test_learned_model_exact_match(mocker):
    from backend.retrosynthesis.validation import MicroserviceLearnedForwardModel
    
    # Mock the requests.post call
    mock_resp = mocker.Mock()
    mock_resp.json.return_value = {
        "predicted_products": [
            {"smiles": "CC(=O)Oc1ccccc1C(=O)O", "confidence": 0.99}
        ],
        "model_name": "mock-t5",
        "model_version": "v1"
    }
    mocker.patch("requests.post", return_value=mock_resp)
    
    model = MicroserviceLearnedForwardModel()
    result = model.validate_step("CC(=O)Oc1ccccc1C(=O)O", ["O=C(O)c1ccccc1O", "CC(=O)OC(C)=O"])
    
    assert result.status == ValidationStatus.MATCH
    assert result.predicted_product == "CC(=O)Oc1ccccc1C(=O)O"


def test_learned_model_mismatch(mocker):
    from backend.retrosynthesis.validation import MicroserviceLearnedForwardModel
    
    # Mock the model predicting a different regioisomer or byproduct
    mock_resp = mocker.Mock()
    mock_resp.json.return_value = {
        "predicted_products": [
            {"smiles": "CCc1ccc(CC(C)C)cc1", "confidence": 0.95}
        ],
        "model_name": "mock-t5",
        "model_version": "v1"
    }
    mocker.patch("requests.post", return_value=mock_resp)
    
    model = MicroserviceLearnedForwardModel()
    result = model.validate_step("CC(C)Cc1ccc(C(C)C(=O)O)cc1", ["CC(C)Cc1ccc(Br)cc1", "CB(O)O"])
    
    assert result.status == ValidationStatus.MISMATCH
    assert result.predicted_product == "CCc1ccc(CC(C)C)cc1"


def test_learned_model_timeout_handling(mocker):
    from backend.retrosynthesis.validation import MicroserviceLearnedForwardModel
    import requests
    
    mocker.patch("requests.post", side_effect=requests.exceptions.Timeout("Connection timed out"))
    
    model = MicroserviceLearnedForwardModel()
    result = model.validate_step("C", ["C"])
    
    assert result.status == ValidationStatus.MODEL_UNAVAILABLE
    assert "Microservice error:" in result.error

