"""Benchmark V2.3.1 Forward Validation Microservice (RDKit vs T5v2)."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.retrosynthesis.validation import RDKitTemplateReversalModel, MicroserviceLearnedForwardModel, ValidationStatus

# Tier 1: Controlled reactions with known RDKit weaknesses
TIER_1_TARGETS = [
    {
        "name": "ibuprofen (incomplete regression)",
        "target": "CC(C)Cc1ccc(C(C)C(=O)O)cc1",
        "reactants": ["CC(C)Cc1ccc(Br)cc1", "CB(O)O"],
        "template": "[c:1]-[Br:2].[C:3]-[B:4] >> [c:1]-[C:3]",
        "expected_rdkit": ValidationStatus.MATCH,
        "expected_t5": ValidationStatus.MISMATCH,
        "note": "RDKit blindly matches; T5 should realize missing CO and predict byproduct or wrong skeleton."
    },
    {
        "name": "regioselectivity conflict",
        "target": "Cc1ccc(O)cc1",
        "reactants": ["Cc1ccc(Br)cc1", "O"],
        "template": "[c:1]-[Br:2].[O:3] >> [c:1]-[O:3]",
        "expected_rdkit": ValidationStatus.MATCH,
        "expected_t5": ValidationStatus.MISMATCH,  # Usually fails or predicts para instead of meta if we swap? 
        "note": "RDKit accepts the template application; T5 should prefer the most stable regioisomer."
    },
    {
        "name": "aspirin (standard match)",
        "target": "CC(=O)Oc1ccccc1C(=O)O",
        "reactants": ["O=C(O)c1ccccc1O", "CC(=O)OC(C)=O"],
        "template": "[c:1]-[OH:2].[C:3](=[O:4])-[O:5]-[C:6](=[O:7]) >> [c:1]-[O:2]-[C:3](=[O:4])",
        "expected_rdkit": ValidationStatus.MATCH,
        "expected_t5": ValidationStatus.MATCH,
        "note": "Both should match cleanly."
    }
]

def assert_real_model(learned_model):
    """Fail loudly if the microservice is in MOCK mode."""
    res = learned_model.validate_step("C", ["C"])
    if res.status == ValidationStatus.MODEL_UNAVAILABLE:
        print("ERROR: Microservice is unavailable. Ensure it is running on port 8435.")
        sys.exit(1)
        
    if "mock" in res.model_name.lower() or "mock" in res.model_version.lower():
        print(f"ERROR: Microservice is in MOCK mode ({res.model_name}).")
        print("The benchmark must run against the real ReactionT5v2 model to be valid.")
        sys.exit(1)
        
    print(f"Verified Real Model Loaded: {res.model_name} (v{res.model_version})")


def run_tier_1(structural, learned):
    print("\n" + "="*60)
    print("TIER 1: CONTROLLED REACTION BENCHMARK")
    print("="*60)
    
    metrics = {"match": 0, "mismatch": 0, "latency_ms": 0}
    
    for case in TIER_1_TARGETS:
        print(f"\n--- Target: {case['name']} ---")
        print(f"Reaction: {case['reactants']} -> {case['target']}")
        print(f"Note: {case['note']}")
        
        # Structural check
        res_s = structural.validate_step(case["target"], case["reactants"], template_smarts=case["template"])
        print(f"  [RDKit]  Status: {res_s.status.name:<15} (Expected: {case['expected_rdkit'].name}) - Predicted: {res_s.predicted_product} ({res_s.inference_time_ms} ms)")
        
        # Learned check
        res_l = learned.validate_step(case["target"], case["reactants"])
        metrics["latency_ms"] += res_l.inference_time_ms
        if res_l.status == ValidationStatus.MATCH: metrics["match"] += 1
        else: metrics["mismatch"] += 1
        
        print(f"  [{res_l.model_name}] Status: {res_l.status.name:<15} (Expected: {case['expected_t5'].name}) - Predicted: {res_l.predicted_product} ({res_l.inference_time_ms} ms)")
        
        if res_s.status == ValidationStatus.MATCH and res_l.status == ValidationStatus.MISMATCH:
            print("  *** VALUE ADDED: Learned model caught a structural false-positive! ***")


def run_tier_2():
    print("\n" + "="*60)
    print("TIER 2: RAMCHEMS ROUTE BENCHMARK")
    print("="*60)
    
    # We must load RetrosynthesisService, but benchmark is run independently.
    from backend.retrosynthesis.service import RetrosynthesisService
    from backend.api.routes_retrosynthesis import PlanRequest
    import os
    
    # Only run if AiZynthFinder config exists
    config_path = Path(ROOT) / "data" / "external" / "aizynthfinder" / "config.yml"
    if not config_path.exists():
        print(f"Skipping Tier 2: AiZynthFinder config not found at {config_path}")
        return
        
    try:
        service = RetrosynthesisService(config_path)
    except Exception as e:
        print(f"Skipping Tier 2: Could not load RetrosynthesisService ({e})")
        return
        
    targets = [
        "CC(=O)Oc1ccccc1C(=O)O", # Aspirin
        "CC(C)Cc1ccc(C(C)C(=O)O)cc1", # Ibuprofen
        "CC(=O)Nc1ccc(O)cc1", # Paracetamol
        "Cn1cnc2c1c(=O)n(C)c(=O)n2C", # Caffeine
        "CCCC1=NN(C)C(=C1C(=O)NC2=C(OCC)C=CC(=C2)S(=O)(=O)N3CCN(C)CC3)C4=CC=C(O4)C", # Sildenafil
        "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc4nccc(n4)c5cccnc5" # Imatinib
    ]
    
    for smiles in targets:
        print(f"\n--- Searching routes for {smiles} ---")
        try:
            req = PlanRequest(smiles=smiles, max_steps=3, return_first=False)
            res = service.plan_route(req, include_validation=True, include_evidence=False)
            
            routes = res.get("routes", [])
            print(f"Found {len(routes)} routes.")
            
            # Aggregate validation summaries
            matches, mismatches, partials = 0, 0, 0
            for r in routes:
                val = r.get("validation_summary", {})
                matches += val.get("steps_match", 0)
                mismatches += val.get("steps_mismatch", 0)
                partials += val.get("steps_partial_match", 0)
                
            print(f"  Aggregated Validated Steps - Match: {matches}, Mismatch: {mismatches}, Partial: {partials}")
        except Exception as e:
            print(f"  Error generating route: {e}")


def main():
    structural = RDKitTemplateReversalModel()
    learned = MicroserviceLearnedForwardModel(endpoint_url="http://localhost:8435/predict")

    print("Checking microservice status...")
    assert_real_model(learned)
    
    run_tier_1(structural, learned)
    run_tier_2()

if __name__ == "__main__":
    main()

