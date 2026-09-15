import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.retrosynthesis.validation import RDKitTemplateReversalModel, MicroserviceLearnedForwardModel, ValidationStatus

def calculate_metrics(matrix):
    tp = matrix["TP"]
    fp = matrix["FP"]
    tn = matrix["TN"]
    fn = matrix["FN"]
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    specificity = tn / (tn + fp) if (tn + fp) > 0 else 0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0
    fnr = fn / (fn + tp) if (fn + tp) > 0 else 0
    
    return {
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "f1": f1,
        "fpr": fpr,
        "fnr": fnr
    }

def print_matrix(name, matrix, metrics):
    print(f"\n{'='*40}")
    print(f"Validator: {name}")
    print(f"{'='*40}")
    print("Confusion Matrix:")
    print(f"  True Positives  (TP): {matrix['TP']}")
    print(f"  False Positives (FP): {matrix['FP']}")
    print(f"  True Negatives  (TN): {matrix['TN']}")
    print(f"  False Negatives (FN): {matrix['FN']}")
    print("\nMetrics:")
    print(f"  Precision:   {metrics['precision']:.3f}")
    print(f"  Recall:      {metrics['recall']:.3f}")
    print(f"  Specificity: {metrics['specificity']:.3f}")
    print(f"  F1 Score:    {metrics['f1']:.3f}")
    print(f"  False Pos Rate: {metrics['fpr']:.3f}")
    print(f"  False Neg Rate: {metrics['fnr']:.3f}")

def main():
    try:
        with open("calibration_set.json", "r") as f:
            dataset = json.load(f)
    except FileNotFoundError:
        print("Run generate_calibration_set.py first.")
        sys.exit(1)
        
    structural = RDKitTemplateReversalModel()
    learned = MicroserviceLearnedForwardModel(endpoint_url="http://localhost:8435/predict")
    
    print(f"Loaded {len(dataset)} calibration cases.")
    
    # Check microservice
    res = learned.validate_step("C", ["C"])
    if res.status == ValidationStatus.MODEL_UNAVAILABLE:
        print("ERROR: Microservice is not running on 8435.")
        sys.exit(1)
        
    # Counters
    mat_s = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
    mat_l = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
    
    start_time = time.time()
    count = 0
    
    for case in dataset:
        target = case["target"]
        reactants = case["reactants"]
        template = case["template"]
        ground_truth = case["ground_truth"] # "MATCH" or "MISMATCH"
        
        # 1. Structural
        res_s = structural.validate_step(target, reactants, template_smarts=template)
        s_pred = res_s.status.name
        if s_pred == "MATCH" and ground_truth == "MATCH": mat_s["TP"] += 1
        elif s_pred == "MATCH" and ground_truth == "MISMATCH": mat_s["FP"] += 1
        elif s_pred == "MISMATCH" and ground_truth == "MISMATCH": mat_s["TN"] += 1
        elif s_pred == "MISMATCH" and ground_truth == "MATCH": mat_s["FN"] += 1
        
        # 2. Learned
        res_l = learned.validate_step(target, reactants)
        l_pred = res_l.status.name
        if l_pred == "MATCH" and ground_truth == "MATCH": mat_l["TP"] += 1
        elif l_pred == "MATCH" and ground_truth == "MISMATCH": mat_l["FP"] += 1
        elif l_pred == "MISMATCH" and ground_truth == "MISMATCH": mat_l["TN"] += 1
        elif l_pred == "MISMATCH" and ground_truth == "MATCH": mat_l["FN"] += 1
        
        count += 1
        if count % 20 == 0:
            print(f"Processed {count}/{len(dataset)}...")
            
    print(f"\nCalibration finished in {time.time()-start_time:.1f} seconds.")
    
    # Save report
    report = {
        "dataset_size": len(dataset),
        "structural": {
            "matrix": mat_s,
            "metrics": calculate_metrics(mat_s)
        },
        "learned": {
            "matrix": mat_l,
            "metrics": calculate_metrics(mat_l)
        }
    }
    
    with open("calibration_results.json", "w") as f:
        json.dump(report, f, indent=2)
        
    print_matrix("RDKit Structural Validator", report["structural"]["matrix"], report["structural"]["metrics"])
    print_matrix("ReactionT5v2 Learned Validator", report["learned"]["matrix"], report["learned"]["metrics"])

if __name__ == "__main__":
    main()
