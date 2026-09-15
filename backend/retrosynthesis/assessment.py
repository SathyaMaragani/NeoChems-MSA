import enum
from typing import Optional, Dict, Any, List

class AssessmentSummary(str, enum.Enum):
    STRONGLY_SUPPORTED = "STRONGLY_SUPPORTED"
    SUPPORTED = "SUPPORTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

def generate_reaction_assessment(
    structural_status: Optional[str],
    forward_status: Optional[str],
    ord_level: Optional[str]
) -> Dict[str, Any]:
    """
    Synthesizes the three independent evidence signals into a single
    chemist-facing assessment with explicit technical flags.
    """
    
    struct = structural_status if structural_status else "MODEL_UNAVAILABLE"
    fwd = forward_status if forward_status else "MODEL_UNAVAILABLE"
    ev = ord_level if ord_level else "unavailable"
    
    is_direct = ev == "experimental"
    is_similar = ev == "similar_experimental"
    is_none = ev in ["unavailable", "predicted"]

    is_struct_match = struct == "MATCH"
    is_struct_mismatch = struct in ["MISMATCH", "PARTIAL_MATCH"]
    
    is_fwd_match = fwd == "MATCH"
    is_fwd_mismatch = fwd in ["MISMATCH", "PARTIAL_MATCH"]
    
    flags = []
    
    if struct == "MODEL_UNAVAILABLE":
        flags.append("structural_validation_unavailable")
    elif struct in ["VALIDATION_ERROR", "MODEL_OUTPUT_INVALID"]:
        flags.append("structural_validation_error")
        
    if fwd == "MODEL_UNAVAILABLE":
        flags.append("forward_validation_unavailable")
    elif fwd in ["VALIDATION_ERROR", "MODEL_OUTPUT_INVALID"]:
        flags.append("forward_validation_error")

    # 1. Any clear MISMATCH from any model is REVIEW_REQUIRED
    if is_struct_mismatch or is_fwd_mismatch:
        return _make_result(AssessmentSummary.REVIEW_REQUIRED, flags)
        
    # If the forward model output was completely invalid but we have a structural match and evidence
    if "MODEL_OUTPUT_INVALID" in [struct, fwd]:
        # Still REVIEW_REQUIRED because output invalidity is highly suspicious
        return _make_result(AssessmentSummary.REVIEW_REQUIRED, flags)

    # 2. STRONG SUPPORT
    if is_struct_match and is_fwd_match and is_direct:
        return _make_result(AssessmentSummary.STRONGLY_SUPPORTED, flags)
        
    # 3. SUPPORTED
    if is_struct_match and is_fwd_match and is_similar:
        return _make_result(AssessmentSummary.SUPPORTED, flags)
        
    if (is_struct_match or "structural_validation_unavailable" in flags) and \
       (is_fwd_match or "forward_validation_unavailable" in flags or "forward_validation_error" in flags) and \
       (is_direct or is_similar):
        # We have at least direct/similar evidence, plus a match OR unavailable/error for models
        return _make_result(AssessmentSummary.SUPPORTED, flags)
        
    # If we have no models and no evidence
    if (struct in ["MODEL_UNAVAILABLE", "VALIDATION_ERROR"] and 
        fwd in ["MODEL_UNAVAILABLE", "VALIDATION_ERROR"] and 
        is_none):
        return _make_result(AssessmentSummary.INSUFFICIENT_EVIDENCE, flags)
        
    if is_none and struct != "MATCH" and fwd != "MATCH":
        return _make_result(AssessmentSummary.INSUFFICIENT_EVIDENCE, flags)
        
    if is_struct_match and is_none and fwd in ["MODEL_UNAVAILABLE", "VALIDATION_ERROR"]:
        return _make_result(AssessmentSummary.INSUFFICIENT_EVIDENCE, flags)

    # Fallback to insufficient evidence
    return _make_result(AssessmentSummary.INSUFFICIENT_EVIDENCE, flags)


def _make_result(summary: AssessmentSummary, flags: List[str]) -> Dict[str, Any]:
    interpretations = {
        AssessmentSummary.STRONGLY_SUPPORTED: "Multiple independent signals support the proposed reaction.",
        AssessmentSummary.SUPPORTED: "Independent signals support the reaction.",
        AssessmentSummary.REVIEW_REQUIRED: "The available evidence contains a meaningful disagreement or structural problem requiring inspection.",
        AssessmentSummary.INSUFFICIENT_EVIDENCE: "The available signals are not sufficient to support or flag a meaningful conclusion."
    }
    
    labels = {
        AssessmentSummary.STRONGLY_SUPPORTED: "Strongly supported",
        AssessmentSummary.SUPPORTED: "Supported",
        AssessmentSummary.REVIEW_REQUIRED: "Review required",
        AssessmentSummary.INSUFFICIENT_EVIDENCE: "Insufficient evidence"
    }
    
    return {
        "summary": summary.value,
        "label": labels[summary],
        "interpretation": interpretations[summary],
        "flags": flags,
        "route_score_affected": False
    }
