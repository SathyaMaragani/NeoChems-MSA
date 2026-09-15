import pytest
from backend.retrosynthesis.assessment import generate_reaction_assessment, AssessmentSummary

def test_assessment_semantics():
    # 1. Strong + Strong + Direct
    res = generate_reaction_assessment("MATCH", "MATCH", "experimental")
    assert res["summary"] == AssessmentSummary.STRONGLY_SUPPORTED
    assert not res["flags"]

    # 2. Strong + Strong + Similar
    res = generate_reaction_assessment("MATCH", "MATCH", "similar_experimental")
    assert res["summary"] == AssessmentSummary.SUPPORTED
    assert not res["flags"]

    # 3. Strong + Strong + None
    res = generate_reaction_assessment("MATCH", "MATCH", "unavailable")
    assert res["summary"] == AssessmentSummary.INSUFFICIENT_EVIDENCE

    # 4. Strong + MISMATCH + Direct
    res = generate_reaction_assessment("MATCH", "MISMATCH", "experimental")
    assert res["summary"] == AssessmentSummary.REVIEW_REQUIRED

    # 5. Strong + MISMATCH + Similar
    res = generate_reaction_assessment("MATCH", "MISMATCH", "similar_experimental")
    assert res["summary"] == AssessmentSummary.REVIEW_REQUIRED

    # 6. Strong + MODEL_UNAVAILABLE + Direct
    res = generate_reaction_assessment("MATCH", "MODEL_UNAVAILABLE", "experimental")
    assert res["summary"] == AssessmentSummary.SUPPORTED
    assert "forward_validation_unavailable" in res["flags"]

    # 7. Strong + MODEL_UNAVAILABLE + Similar
    res = generate_reaction_assessment("MATCH", "MODEL_UNAVAILABLE", "similar_experimental")
    assert res["summary"] == AssessmentSummary.SUPPORTED
    assert "forward_validation_unavailable" in res["flags"]

    # 8. Strong + VALIDATION_ERROR + Direct
    res = generate_reaction_assessment("MATCH", "VALIDATION_ERROR", "experimental")
    assert res["summary"] == AssessmentSummary.SUPPORTED
    assert "forward_validation_error" in res["flags"]

    # 9. MODEL_OUTPUT_INVALID + Direct
    res = generate_reaction_assessment("MODEL_OUTPUT_INVALID", "MATCH", "experimental")
    assert res["summary"] == AssessmentSummary.REVIEW_REQUIRED
    assert "structural_validation_error" in res["flags"]

    # 10. all validation/evidence unavailable
    res = generate_reaction_assessment(None, None, None)
    assert res["summary"] == AssessmentSummary.INSUFFICIENT_EVIDENCE
    assert "structural_validation_unavailable" in res["flags"]
    assert "forward_validation_unavailable" in res["flags"]
