from typing import Dict, Any, List

# Define the hierarchy of states. Lower index = higher precedence when degrading the route score.
STATE_HIERARCHY = {
    "STRONGLY_SUPPORTED": 0,
    "STRONG_SUPPORT": 0,  # Map reaction state to route state
    "SUPPORTED": 1,
    "INSUFFICIENT_EVIDENCE": 2,
    "REVIEW_REQUIRED": 3
}

# The display labels for the route states
STATE_LABELS = {
    "STRONGLY_SUPPORTED": "Strongly supported",
    "SUPPORTED": "Supported",
    "REVIEW_REQUIRED": "Review required",
    "INSUFFICIENT_EVIDENCE": "Insufficient evidence"
}

def aggregate_route_assessment(route_tree: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aggregates the reaction-level assessments into a single route-level assessment
    using the 'weakest link' principle. Also aggregates technical flags.
    """
    worst_state = "STRONGLY_SUPPORTED"
    critical_steps = []
    all_flags = set()
    
    def walk(node: Dict[str, Any]):
        nonlocal worst_state
        for reaction in node.get("reactions", []):
            assessment = reaction.get("assessment") or {}
            reaction_state = assessment.get("summary", "INSUFFICIENT_EVIDENCE")
            
            # Aggregate flags
            flags = assessment.get("flags", [])
            for flag in flags:
                all_flags.add(flag)

            if reaction_state == "STRONG_SUPPORT":
                reaction_state = "STRONGLY_SUPPORTED"
            
            # Compare and downgrade worst_state if necessary
            curr_rank = STATE_HIERARCHY.get(reaction_state, 2)
            worst_rank = STATE_HIERARCHY.get(worst_state, 0)
            
            if curr_rank > worst_rank:
                worst_state = reaction_state
                # New worst state resets critical steps
                critical_steps.clear()
                
            if curr_rank == STATE_HIERARCHY.get(worst_state, 0) and curr_rank >= STATE_HIERARCHY["REVIEW_REQUIRED"]:
                # Collect critical steps for the worst state
                critical_steps.append({
                    "reaction_smiles": reaction.get("reaction_smiles", ""),
                    "reason": reaction_state,
                    "flags": flags
                })
            
            for child in reaction.get("reactants", []):
                walk(child)

    walk(route_tree)
    
    return {
        "summary": worst_state,
        "label": STATE_LABELS.get(worst_state, worst_state),
        "critical_steps": critical_steps,
        "flags": list(all_flags),
        "route_score_affected": False
    }
