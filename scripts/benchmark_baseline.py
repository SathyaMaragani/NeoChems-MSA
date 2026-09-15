"""Benchmark script for V2.3 Forward Validation on AiZynthFinder routes."""
import sys
import time
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.retrosynthesis.service import load_config
from aizynthfinder.aizynthfinder import AiZynthFinder
from backend.retrosynthesis.validation import RDKitTemplateReversalModel, ValidationStatus

TARGETS = {
    "aspirin": "CC(=O)Oc1ccccc1C(=O)O",
    "ibuprofen": "CC(C)Cc1ccc(C(C)C(=O)O)cc1",
    "paracetamol": "CC(=O)Nc1ccc(O)cc1",
    "caffeine": "Cn1cnc2c1c(=O)n(C)c(=O)n2C",
    "sildenafil": "CCCC1=NN(C2=C1N=C(NC2=O)C3=C(C=CC(=C3)S(=O)(=O)N4CCN(CC4)C)OCCC)C",
    "imatinib": "CC1=C(C=C(C=C1)NC(=O)C2=CC=C(C=C2)CN3CCN(CC3)C)NC4=NC=CC(=N4)C5=CC=NC=C5",
}

CONFIG = ROOT / "config.yml"

def extract_steps(node, target_mol_smiles):
    steps = []
    for rxn in node.get("children", []):
        reactants = [c["smiles"] for c in rxn.get("children", [])]
        meta = rxn.get("metadata", {})
        steps.append({
            "target": target_mol_smiles,
            "reactants": reactants,
            "template": meta.get("template_code", meta.get("template", "")),
        })
        for child in rxn.get("children", []):
            steps.extend(extract_steps(child, child["smiles"]))
    return steps

def main():
    if not CONFIG.exists():
        sys.exit("Missing config.yml")

    print("Loading AiZynthFinder...")
    finder = AiZynthFinder(configdict=load_config(CONFIG))
    finder.stock.select("zinc")
    finder.expansion_policy.select("uspto")
    finder.filter_policy.select("uspto")
    finder.config.search.iteration_limit = 200

    model = RDKitTemplateReversalModel()
    
    all_steps = []
    dedup = set()

    print("Generating routes...")
    for name, smiles in TARGETS.items():
        finder.target_smiles = smiles
        finder.tree_search()
        finder.build_routes()
        
        for idx in range(len(finder.routes)):
            route = finder.routes[idx]["dict"]
            steps = extract_steps(route, route["smiles"])
            for s in steps:
                t = (s["target"], tuple(s["reactants"]), s["template"])
                if t not in dedup:
                    dedup.add(t)
                    all_steps.append(s)

    print(f"Extracted {len(all_steps)} unique reaction steps from generated routes.")
    
    counts = {
        "MATCH": 0,
        "PARTIAL_MATCH": 0,
        "MISMATCH": 0,
        "MODEL_UNAVAILABLE": 0,
        "VALIDATION_ERROR": 0,
    }
    
    total_time_ms = 0
    mismatches = []
    errors = []
    
    for i, step in enumerate(all_steps):
        if not step["template"]:
            counts["VALIDATION_ERROR"] += 1
            errors.append(step)
            continue
            
        res = model.validate_step(
            step["target"],
            step["reactants"],
            template_smarts=step["template"]
        )
        
        counts[res.status.value] += 1
        total_time_ms += res.inference_time_ms
        if res.status == ValidationStatus.MISMATCH:
            mismatches.append((step, res))
        elif res.status == ValidationStatus.VALIDATION_ERROR:
            errors.append((step, res))

    print("=======================================")
    print("FORWARD VALIDATION BASELINE BENCHMARK")
    print("=======================================")
    print(f"Total steps evaluated: {len(all_steps)}")
    for k, v in counts.items():
        print(f"  {k}: {v} ({(v/max(1,len(all_steps)))*100:.1f}%)")
        
    print(f"\nAverage inference time per step: {total_time_ms / max(1, len(all_steps)):.2f} ms")
    
    print("\nSAMPLE MISMATCHES:")
    for step, res in mismatches[:5]:
        print(f"  Target: {step['target']}")
        print(f"  Reactants: {step['reactants']}")
        print(f"  Template: {step['template']}")
        print(f"  Predicted: {res.predicted_product}")
        print()
        
if __name__ == "__main__":
    main()
