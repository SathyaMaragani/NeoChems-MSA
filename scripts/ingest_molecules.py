"""Ingest a SMILES TSV into Postgres: canonicalize, strip to parent, fingerprint.

  python scripts/ingest_molecules.py [--source public] [--file <tsv>]
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.molrepr import service  # noqa: E402
from backend.molrepr.search import DSN  # noqa: E402

DEFAULT_FILE = ROOT / "data/external/chembl/chembl_approved.tsv"

INSERT = """
    INSERT INTO molecules
        (canonical_smiles, original_smiles, inchikey, source,
         is_mineral_salt, molecular_weight, morgan_fingerprint,
         morgan_fingerprint_chiral, mol)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, mol_from_smiles(%s::cstring))
    ON CONFLICT (canonical_smiles) DO NOTHING
    RETURNING id
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE)
    ap.add_argument("--source", choices=["public", "internal"], default="public")
    ap.add_argument("--truncate", action="store_true", help="empty the table first")
    args = ap.parse_args()

    if not args.file.exists():
        sys.exit(f"missing {args.file} - run: python scripts/download_chembl.py")

    attempted = inserted = rejected = duplicate = stripped = mineral = 0
    rejects: list[tuple[str, str]] = []

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        if args.truncate:
            cur.execute("TRUNCATE molecules RESTART IDENTITY")

        with args.file.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                attempted += 1
                original = (row.get("smiles") or "").strip()
                try:
                    is_mineral = service.is_mineral_salt_smiles(original)
                    canonical = service.canonicalize(original)
                    inchikey = service.to_inchikey(original)
                    weight = service.molecular_weight(original)
                    fp = service.fingerprint_to_bytes(service.morgan_fingerprint(original))
                    fp_chiral = service.fingerprint_to_bytes(
                        service.morgan_fingerprint(original, include_chirality=True)
                    )
                except service.InvalidSmilesError as err:
                    rejected += 1
                    if len(rejects) < 20:
                        rejects.append((row.get("chembl_id", "?"), str(err)))
                    continue
                except Exception as err:  # RDKit can raise on exotic valences
                    rejected += 1
                    if len(rejects) < 20:
                        rejects.append((row.get("chembl_id", "?"), f"{type(err).__name__}: {err}"))
                    continue

                # Did parent-stripping actually change the structure?
                if canonical != service.canonicalize(original, strip_to_parent=False):
                    stripped += 1
                if is_mineral:
                    mineral += 1

                cur.execute(
                    INSERT,
                    (canonical, original, inchikey, args.source, is_mineral,
                     weight, fp, fp_chiral, canonical),
                )
                if cur.fetchone() is None:
                    duplicate += 1
                else:
                    inserted += 1

                if attempted % 500 == 0:
                    print(f"\r  {attempted} processed", end="", flush=True)
        conn.commit()

        cur.execute("SELECT count(*) FROM molecules")
        total = cur.fetchone()[0]

    print(f"\r{'':30}\r", end="")
    print("=" * 52)
    print(f"  file            {args.file.name}")
    print(f"  attempted       {attempted}")
    print(f"  inserted        {inserted}")
    print(f"  duplicate       {duplicate}  (same parent SMILES as an earlier row)")
    print(f"  rejected        {rejected}  (failed to parse)")
    print(f"  salt-stripped   {stripped}  (parent differs from source structure)")
    print(f"  mineral salts   {mineral}  (stripping skipped, counter-ion is active)")
    print(f"  table total     {total}")
    print("=" * 52)
    if rejects:
        print("first rejects:")
        for cid, msg in rejects:
            print(f"  {cid}: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
