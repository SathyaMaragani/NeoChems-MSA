import pytest
from backend.retrosynthesis.route_assessment import aggregate_route_assessment

def test_all_strong_support():
    tree = {
        "reactions": [
            {
                "reaction_smiles": "A>>B",
                "assessment": {"summary": "STRONG_SUPPORT"},
                "reactants": [
                    {
                        "reactions": [
                            {
                                "reaction_smiles": "C>>A",
                                "assessment": {"summary": "STRONG_SUPPORT"}
                            }
                        ]
                    }
                ]
            }
        ]
    }
    result = aggregate_route_assessment(tree)
    assert result["summary"] == "STRONGLY_SUPPORTED"
    assert len(result["critical_steps"]) == 0
    assert len(result["flags"]) == 0

def test_weakest_link_review_required():
    tree = {
        "reactions": [
            {
                "reaction_smiles": "A>>B",
                "assessment": {"summary": "STRONG_SUPPORT"},
                "reactants": [
                    {
                        "reactions": [
                            {
                                "reaction_smiles": "C>>A",
                                "assessment": {"summary": "REVIEW_REQUIRED", "flags": ["some_flag"]}
                            }
                        ]
                    }
                ]
            }
        ]
    }
    result = aggregate_route_assessment(tree)
    assert result["summary"] == "REVIEW_REQUIRED"
    assert len(result["critical_steps"]) == 1
    assert result["critical_steps"][0]["reaction_smiles"] == "C>>A"
    assert "some_flag" in result["flags"]

def test_technical_failure_aggregation():
    tree = {
        "reactions": [
            {
                "reaction_smiles": "A>>B",
                "assessment": {"summary": "SUPPORTED", "flags": ["forward_validation_unavailable"]},
                "reactants": [
                    {
                        "reactions": [
                            {
                                "reaction_smiles": "C>>A",
                                "assessment": {"summary": "STRONGLY_SUPPORTED"}
                            }
                        ]
                    }
                ]
            }
        ]
    }
    result = aggregate_route_assessment(tree)
    # The worst scientific state is SUPPORTED
    assert result["summary"] == "SUPPORTED"
    assert "forward_validation_unavailable" in result["flags"]

def test_route_score_affected_is_false():
    tree = {"reactions": [{"assessment": {"summary": "STRONG_SUPPORT"}}]}
    result = aggregate_route_assessment(tree)
    assert result["route_score_affected"] is False
