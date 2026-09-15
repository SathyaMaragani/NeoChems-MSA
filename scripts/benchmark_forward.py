"""Benchmark the forward validation layer on known reactions."""
import sys
import time
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.retrosynthesis.validation import RDKitTemplateReversalModel, ValidationStatus

def main():
    # A tiny mock benchmark just to prove the harness works.
    # A real benchmark would load thousands of reactions from ORD.
    test_cases = [
        {
            "target": "CC(=O)Oc1ccccc1C(=O)O",
            "reactants": ["O=C(O)c1ccccc1O", "CC(=O)OC(C)=O"],
            "template": "[c:1]-[OH:2].[C:3](=[O:4])-[O:5]-[C:6](=[O:7]) >> [c:1]-[O:2]-[C:3](=[O:4])",
            "expected_status": ValidationStatus.MATCH
        },
        {
            "target": "CC(C)Cc1ccc(C(C)C(=O)O)cc1", # Ibuprofen
            "reactants": ["CC(C)Cc1ccc(Br)cc1", "CB(O)O"],
            "template": "[c:1]-[Br:2].[C:3]-[B:4] >> [c:1]-[C:3]",
            "expected_status": ValidationStatus.MISMATCH
        }
    ]

    model = RDKitTemplateReversalModel()
    
    print(f"Benchmarking model: {model.name} v{model.version}")
    print("=" * 60)
    
    correct = 0
    total = len(test_cases)
    total_time_ms = 0

    for i, case in enumerate(test_cases):
        res = model.validate_step(
            case["target"], 
            case["reactants"], 
            template_smarts=case["template"]
        )
        
        total_time_ms += res.inference_time_ms
        is_correct = (res.status == case["expected_status"])
        if is_correct:
            correct += 1
            
        print(f"Case {i+1}:")
        print(f"  Target: {case['target']}")
        print(f"  Predicted: {res.predicted_product} (Status: {res.status.value})")
        print(f"  Expected Status: {case['expected_status'].value}")
        print(f"  Result: {'PASS' if is_correct else 'FAIL'} ({res.inference_time_ms} ms)")
        print("-" * 60)

    print(f"Accuracy: {correct}/{total} ({correct/total*100:.1f}%)")
    print(f"Average Inference Time: {total_time_ms/total:.1f} ms")
    
if __name__ == "__main__":
    main()
