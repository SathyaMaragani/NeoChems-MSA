import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.retrosynthesis.validation import MicroserviceLearnedForwardModel

def analyze():
    with open("calibration_set.json", "r") as f:
        dataset = json.load(f)
        
    learned = MicroserviceLearnedForwardModel(endpoint_url="http://localhost:8435/predict")
    
    fn_count = 0
    print("False Negatives Analysis:")
    
    for case in dataset:
        if case["ground_truth"] != "MATCH":
            continue
            
        target = case["target"]
        reactants = case["reactants"]
        
        res = learned.validate_step(target, reactants)
        if res.status.name == "MISMATCH":
            fn_count += 1
            print(f"\nFN #{fn_count}")
            print(f"Reaction Type: {case.get('reaction_type', 'Unknown')}")
            print(f"Reactants: {reactants}")
            print(f"Target:    {target}")
            print(f"Predicted: {res.predicted_product}")

if __name__ == "__main__":
    analyze()
