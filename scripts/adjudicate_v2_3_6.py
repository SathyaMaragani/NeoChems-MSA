import sys
import csv
import argparse
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.conditions.service import ConditionsService
from backend.conditions.normalize import normalize

def main():
    parser = argparse.ArgumentParser(description="Adjudicate Tier C Disagreements")
    parser.add_argument("--input", required=True, help="Input CSV file")
    parser.add_argument("--output", required=True, help="Output annotated CSV file")
    args = parser.parse_args()

    input_file = Path(args.input)
    output_file = Path(args.output)

    if not input_file.exists():
        sys.exit(f"Input file not found: {input_file}")

    print("Checking ORD availability...")
    cond_service = ConditionsService()
    probe_rxn = normalize(["C"], ["CC"])
    probe_ev = cond_service.for_reaction(probe_rxn)
    if not probe_ev or probe_ev.get("evidence_level") == "unavailable" and probe_ev.get("reason", "").startswith("No experimental evidence source is configured"):
        sys.exit("ORD availability probe failed: No experimental evidence source configured or ORD unreachable.")
    print("ORD is reachable.")
    
    cases = []
    with open(input_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cases.append(row)

    print(f"Processing {len(cases)} cases...")
    direct = 0
    similar = 0
    none = 0
    failed = 0

    for i, row in enumerate(cases):
        target = row["proposed_product"]
        reactants = row["reactants"].split(".")
        try:
            norm_rxn = normalize(reactants, [target])
            ev = cond_service.for_reaction(norm_rxn)
            
            ord_status = ev.get("evidence_level", "unavailable")
            if ord_status == "experimental":
                ord_exact = len(ev.get("direct_precedents", []))
                ord_similar = len(ev.get("similar_precedents", []))
                direct += 1
            elif ord_status == "similar_experimental":
                ord_exact = 0
                ord_similar = len(ev.get("similar_precedents", []))
                similar += 1
            else:
                ord_exact = 0
                ord_similar = 0
                none += 1

            row["ord_evidence_status"] = ord_status
            row["ord_exact_count"] = ord_exact
            row["ord_similar_count"] = ord_similar
            row["adjudication_label"] = ""
            row["adjudication_reason"] = ""
            row["adjudicator"] = ""
            row["adjudication_date"] = ""
            row["evidence_basis"] = ""
            row["adjudication_confidence"] = ""

        except Exception as e:
            failed += 1
            row["ord_evidence_status"] = f"error: {str(e)}"
            row["ord_exact_count"] = 0
            row["ord_similar_count"] = 0
            
        sys.stdout.write(f"\rProcessed {i+1}/{len(cases)}")
        sys.stdout.flush()

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        keys = cases[0].keys()
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(cases)

    print("\nAdjudication preparation complete!")
    print(f"Exported to {output_file}")
    print(f"Stats:")
    print(f"  Direct precedent : {direct}")
    print(f"  Similar precedent: {similar}")
    print(f"  No evidence      : {none}")
    print(f"  Retrieval failed : {failed}")

if __name__ == "__main__":
    main()
