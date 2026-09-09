"""Fetch ChEMBL approved small-molecule drugs to a TSV, so ingestion runs offline.

Filters at the API so the ingestion reject log stays a signal about malformed
structures, not ~900 predictable nulls from biologics with no SMILES.
"""
import csv
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data/external/chembl/chembl_approved.tsv"
BASE = "https://www.ebi.ac.uk/chembl/api/data/molecule.json?"
FILTERS = {
    "max_phase": 4,                     # approved
    "molecule_type": "Small molecule",  # excludes antibodies/proteins/oligos
    "molecule_structures__canonical_smiles__isnull": "false",
    "limit": 100,
}


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    version = json.load(
        urllib.request.urlopen("https://www.ebi.ac.uk/chembl/api/data/status.json", timeout=60)
    )["chembl_db_version"]

    url = BASE + urllib.parse.urlencode(FILTERS)
    rows, total = [], None
    while url:
        page = json.load(urllib.request.urlopen(url, timeout=120))
        total = total or page["page_meta"]["total_count"]
        for m in page["molecules"]:
            structures = m.get("molecule_structures") or {}
            smiles = structures.get("canonical_smiles")
            if smiles:
                rows.append((m["molecule_chembl_id"], m.get("pref_name") or "", smiles))
        print(f"\r  {len(rows)}/{total}", end="", flush=True)
        nxt = page["page_meta"].get("next")
        url = "https://www.ebi.ac.uk" + nxt if nxt else None

    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["chembl_id", "pref_name", "smiles"])
        w.writerows(rows)

    print(f"\n{version}: wrote {len(rows)} rows -> {OUT} ({OUT.stat().st_size/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
