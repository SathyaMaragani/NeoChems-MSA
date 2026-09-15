import pytest
import math
from backend.conditions.retrieval import Candidate, DEFAULT_THRESHOLDS

def test_combined_scoring_weights():
    # The scoring formula was adjusted to 0.30/0.70 based on 700-query benchmark.
    # We test that the Candidate class actually uses these weights correctly.
    # Note: TRANSFORMATION_WEIGHT is defined in ord_provider but Candidate uses it as an arg.
    
    from backend.conditions.ord_provider import TRANSFORMATION_WEIGHT, SUBSTRATE_WEIGHT
    
    # Enforce the invariants as demanded by the codebase.
    assert math.isclose(TRANSFORMATION_WEIGHT, 0.30), "Transformation weight drifted!"
    assert math.isclose(SUBSTRATE_WEIGHT, 0.70), "Substrate weight drifted!"
    assert math.isclose(TRANSFORMATION_WEIGHT + SUBSTRATE_WEIGHT, 1.0)
    
    # Test Candidate.combined()
    c = Candidate(reaction_id="test", transformation=0.8421, substrate=0.3947)
    score = c.combined(TRANSFORMATION_WEIGHT)
    
    # User's manual check from the CSV
    expected = (0.30 * 0.8421) + (0.70 * 0.3947)
    
    assert math.isclose(score, expected), f"Combined score {score} != {expected}"
    assert math.isclose(score, 0.5289, abs_tol=1e-4)
    
    # Test SNAr
    c2 = Candidate(reaction_id="test2", transformation=0.6364, substrate=0.6923)
    score2 = c2.combined(TRANSFORMATION_WEIGHT)
    expected2 = (0.30 * 0.6364) + (0.70 * 0.6923)
    assert math.isclose(score2, expected2)
    assert math.isclose(score2, 0.6755, abs_tol=1e-4)

    # Test Alcohol Oxidation
    c3 = Candidate(reaction_id="test3", transformation=1.0, substrate=0.7368)
    score3 = c3.combined(TRANSFORMATION_WEIGHT)
    expected3 = (0.30 * 1.0) + (0.70 * 0.7368)
    assert math.isclose(score3, expected3)
    assert math.isclose(score3, 0.8158, abs_tol=1e-4)
