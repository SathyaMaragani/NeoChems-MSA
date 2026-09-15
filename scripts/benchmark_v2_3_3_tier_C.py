"""Benchmark script for V2.3.3 Tier C: Forward Validation on AiZynthFinder routes."""
import sys
import time
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.retrosynthesis.service import load_config
from aizynthfinder.aizynthfinder import AiZynthFinder
from backend.retrosynthesis.validation import (
    RDKitTemplateReversalModel,
    MicroserviceLearnedForwardModel,
    ValidationStatus
)

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
        template_smarts = meta.get("smarts")
        if not template_smarts:
            template_smarts = meta.get("template")
            if isinstance(template_smarts, int):
                template_smarts = None
        
        steps.append({
            "target": target_mol_smiles,
            "reactants": reactants,
            "template": template_smarts,
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

    s_model = RDKitTemplateReversalModel()
    l_model = MicroserviceLearnedForwardModel()
    
    all_steps = []
    dedup = set()

    print("Generating routes...")
    for name, smiles in TARGETS.items():
        finder.target_smiles = smiles
        finder.tree_search()
        finder.build_routes()
        finder.routes.dicts # materialise route dicts
        
        for idx in range(len(finder.routes)):
            route = finder.routes[idx]["dict"]
            steps = extract_steps(route, route["smiles"])
            for s in steps:
                t = (s["target"], tuple(s["reactants"]), s["template"])
                if t not in dedup:
                    dedup.add(t)
                    all_steps.append(s)

    print(f"Extracted {len(all_steps)} unique reaction steps from generated routes.")
    
    counts_s = {k: 0 for k in ValidationStatus}
    counts_l = {k: 0 for k in ValidationStatus}
    
    disagreements = 0

    import collections
    import csv
    import hashlib
    import uuid
    from backend.conditions.normalize import normalize
    from backend.conditions.service import ConditionsService
    
    matrix = collections.defaultdict(int)
    disagreements_data = []
    
    print("Initializing Conditions Service for ORD lookup...")
    cond_service = ConditionsService()

    print("Running Tier C evaluation...")
    for i, step in enumerate(all_steps):
        s_res = s_model.validate_step(step["target"], step["reactants"], template_smarts=step["template"])
        l_res = l_model.validate_step(step["target"], step["reactants"])
        
        counts_s[s_res.status] += 1
        counts_l[l_res.status] += 1
        matrix[(s_res.status.value, l_res.status.value)] += 1
        
        if s_res.status != l_res.status:
            disagreements += 1
            
            # Fetch ORD evidence
            ord_status = "unavailable"
            ord_count = 0
            rxn_key = ""
            try:
                norm_rxn = normalize(step["reactants"], [step["target"]])
                rxn_key = norm_rxn.reaction_key
                ev = cond_service.for_reaction(norm_rxn)
                if ev:
                    ord_status = ev.get('evidence_level', 'unavailable')
                    if ord_status == 'unavailable' and ev.get('reason', '').startswith('No experimental evidence source is configured'):
                        sys.exit("ORD availability probe failed: No experimental evidence source configured or ORD unreachable.")
                    
                    if ord_status == "experimental":
                        ord_count = len(ev.get("direct_precedents", []))
                    elif ord_status == "similar_experimental":
                        ord_count = len(ev.get("similar_precedents", []))
            except Exception as e:
                ord_status = f"error: {str(e)}"
                
            template_hash = hashlib.sha256((step["template"] or "").encode()).hexdigest()[:12]
            
            disagreements_data.append({
                "target": step["target"],
                "route_id": f"R_{uuid.uuid4().hex[:6]}",
                "step_id": f"S_{uuid.uuid4().hex[:6]}",
                "proposed_product": step["target"],
                "reactants": ".".join(step["reactants"]),
                "reagents": "",
                "t5_predicted_product": l_res.predicted_product or "",
                "t5_status": l_res.status.value,
                "rdkit_status": s_res.status.value,
                "template_hash": template_hash,
                "template_smarts": step["template"] or "",
                "reaction_key": rxn_key,
                "ord_precedent_status": ord_status,
                "ord_precedent_count": ord_count
            })
            
        sys.stdout.write(f"\rProcessed {i+1}/{len(all_steps)}")
        sys.stdout.flush()
        
    print("\n\nTier C Benchmark Complete!")
    print("=" * 40)
    print("RDKit Structural Model:")
    for k, v in counts_s.items():
        print(f"  {k.value}: {v}")
    print("-" * 40)
    print("T5v2 Learned Model:")
    for k, v in counts_l.items():
        print(f"  {k.value}: {v}")
    print("-" * 40)
    print(f"Total Disagreements: {disagreements} / {len(all_steps)}")
    print("-" * 40)
    print("Cross-Validator Matrix (RDKit, T5v2):")
    for (s, l), count in sorted(matrix.items()):
        print(f"  {s:15} | {l:15} : {count}")
        
    if disagreements_data:
        csv_path = ROOT / "data" / "validation" / "tier_c_disagreements.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        keys = disagreements_data[0].keys()
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(disagreements_data)
        print(f"\nExported {len(disagreements_data)} disagreements to {csv_path}")
        assert len(disagreements_data) == counts_l[ValidationStatus.MISMATCH], "CSV row count does not match T5 mismatch count!"

if __name__ == "__main__":
    main()
