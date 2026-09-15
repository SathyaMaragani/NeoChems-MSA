import json
import sys
import time
from pathlib import Path
from rdkit import Chem

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.retrosynthesis.validation import MicroserviceLearnedForwardModel

def check_match(pred, target):
    if pred == target:
        return "RAW_EXACT"
        
    pmol = Chem.MolFromSmiles(pred)
    tmol = Chem.MolFromSmiles(target)
    
    if not pmol:
        return "INVALID_SMILES"
        
    if not tmol:
        return "INVALID_TARGET"
        
    p_canon = Chem.MolToSmiles(pmol, isomericSmiles=True)
    t_canon = Chem.MolToSmiles(tmol, isomericSmiles=True)
    
    if p_canon == t_canon:
        return "CANONICAL_EXACT"
        
    p_conn = Chem.MolToSmiles(pmol, isomericSmiles=False)
    t_conn = Chem.MolToSmiles(tmol, isomericSmiles=False)
    
    if p_conn == t_conn:
        return "CONNECTIVITY_ONLY"
        
    return "NO_MATCH"

def main():
    try:
        with open("real_calibration_set.json", "r") as f:
            dataset = json.load(f)
    except FileNotFoundError:
        print("Run generate_v2_3_3_dataset.py first.")
        sys.exit(1)
        
    learned = MicroserviceLearnedForwardModel(endpoint_url="http://localhost:8435/predict")
    
    results = {
        "tier_a": {"total": 0, "raw_exact": 0, "canonical_exact": 0, "connectivity_only": 0, "invalid_smiles": 0, "no_match": 0},
        "tier_b": {"total": 0, "true_negative": 0, "false_positive": 0}
    }
    
    print(f"Loaded {len(dataset)} calibration cases.")
    
    start_time = time.time()
    
    # We will sample 200 cases to keep benchmark time reasonable (~10 minutes at 3s/reaction)
    # 100 MATCH and 100 MISMATCH
    import random
    random.seed(42)
    match_cases = [c for c in dataset if c["ground_truth"] == "MATCH"]
    mismatch_cases = [c for c in dataset if c["ground_truth"] == "MISMATCH"]
    sample_dataset = random.sample(match_cases, min(100, len(match_cases))) + random.sample(mismatch_cases, min(100, len(mismatch_cases)))
    
    print(f"Subsampled to {len(sample_dataset)} cases for Tier A & B evaluation.")
    
    count = 0
    for case in sample_dataset:
        target = case["target"]
        reactants = case["reactants"]
        ground_truth = case["ground_truth"]
        
        # We query the microservice
        res = learned.validate_step(target, reactants)
        pred = res.predicted_product
        
        match_level = check_match(pred, target) if pred else "NO_MATCH"
        
        if ground_truth == "MATCH":
            results["tier_a"]["total"] += 1
            if match_level == "RAW_EXACT":
                results["tier_a"]["raw_exact"] += 1
            elif match_level == "CANONICAL_EXACT":
                results["tier_a"]["canonical_exact"] += 1
            elif match_level == "CONNECTIVITY_ONLY":
                results["tier_a"]["connectivity_only"] += 1
            elif match_level == "INVALID_SMILES":
                results["tier_a"]["invalid_smiles"] += 1
            else:
                results["tier_a"]["no_match"] += 1
        else:
            results["tier_b"]["total"] += 1
            # In Tier B, we want the model to REJECT (disagree with the adversarial target)
            if res.status.name == "MISMATCH":
                results["tier_b"]["true_negative"] += 1
            else:
                results["tier_b"]["false_positive"] += 1
                
        count += 1
        if count % 10 == 0:
            print(f"Processed {count}/{len(sample_dataset)}...")
            
    total_time = time.time() - start_time
    
    report = {
        "metrics": results,
        "latency_sec_per_reaction": total_time / len(sample_dataset)
    }
    
    with open("v2_3_3_tier_ab_results.json", "w") as f:
        json.dump(report, f, indent=2)
        
    print("\nBenchmark Complete!")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
