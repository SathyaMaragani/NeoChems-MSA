"""Build the transformation-keyed retrieval index (V2.2).

Populates `ord_reaction_index` from `ord_reactions`. Derived data only - safe to
drop and rebuild, and it never becomes a source of truth.

    conda activate retrosynth
    python scripts/build_reaction_index.py [--rebuild] [--limit N]

The reaction fingerprints are computed BY THE CARTRIDGE, in SQL, so the index
and the query use byte-identical code. Only the reaction centre (which the
cartridge has no function for) is computed in Python.

Why this exists: V2.1 retrieved candidates by product-molecule similarity, which
benchmarking showed is keyed on the wrong thing - it offered no relevant
candidate at all for ~44% of queries. See docs/retrieval-benchmark.md.
"""
from __future__ import annotations

import argparse
import csv
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from backend.conditions.normalize import normalize, reaction_centre  # noqa: E402
from backend.molrepr.search import pool  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "db/init/03_reaction_index.sql"
LABELS = ROOT / "data/external/ord/benchmark_labels.csv"

#: Cartridge fingerprint type arguments. 2 = atom-pair style difference
#: fingerprint, 5 = the default structural fingerprint for reactions.
DIFFERENCE_FP_TYPE = 2
STRUCTURAL_FP_TYPE = 5

BATCH = 2000


def load_metadata() -> dict[str, dict]:
    """Campaign and reaction-class metadata, if the label file has been built.

    Optional: the index works without it. Only the benchmark needs campaigns,
    and reaction_class is display metadata that must never reach retrieval.
    """
    if not LABELS.exists():
        print(f"  (no {LABELS.name}; campaign/class columns will be null)")
        return {}
    with LABELS.open(encoding="utf-8") as handle:
        return {row["reaction_id"]: row for row in csv.DictReader(handle)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true",
                        help="drop and recreate the index table first")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    metadata = load_metadata()
    started = time.time()

    with pool().connection() as conn:
        if args.rebuild:
            conn.execute("DROP TABLE IF EXISTS ord_reaction_index CASCADE")
            conn.commit()
            print("  dropped existing index")
        conn.execute(SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
        print("  schema applied")

        rows = conn.execute(
            #  Incremental: a new source adds millions of rows, and re-deriving
            #  fingerprints for everything already indexed would waste hours.
            "SELECT r.reaction_id FROM ord_reactions r WHERE NOT EXISTS "
            "(SELECT 1 FROM ord_reaction_index i WHERE i.reaction_id = r.reaction_id)"
            + (" LIMIT %s" if args.limit else ""),
            (args.limit,) if args.limit else (),
        ).fetchall()
    ids = [r["reaction_id"] for r in rows]
    print(f"  {len(ids)} reactions to index")

    #  Pass 1: everything the cartridge can compute, in SQL, in batches.
    #  A malformed reaction SMILES raises rather than returning NULL, so a
    #  failed batch is retried row by row and the offenders are skipped - one
    #  bad record must not cost the other 1999.
    inserted = skipped = 0
    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        try:
            with pool().connection() as conn:
                inserted += _insert(conn, chunk)
                conn.commit()
        except Exception:
            for reaction_id in chunk:
                try:
                    with pool().connection() as conn:
                        inserted += _insert(conn, [reaction_id])
                        conn.commit()
                except Exception:
                    skipped += 1
        if (start // BATCH) % 10 == 0:
            print(f"    fingerprints {min(start + BATCH, len(ids))}/{len(ids)}"
                  f"  (skipped {skipped})")
    print(f"  fingerprints done: {inserted} rows, {skipped} unparseable")

    #  Pass 2: the reaction centre, which needs RDKit in Python. Selected from
    #  the index itself, not from `ids`: a run that died here has already
    #  inserted every row, so on resume `ids` is empty while centres are still
    #  missing. The ~1% of reactions with no centre at all are retried each run
    #  (~20 s at 2M rows) rather than tracked.
    with pool().connection() as conn:
        pending = [r["reaction_id"] for r in conn.execute(
            "SELECT reaction_id FROM ord_reaction_index WHERE centre_hash IS NULL"
            + (" LIMIT %s" if args.limit else ""),
            (args.limit,) if args.limit else (),
        ).fetchall()]
    print(f"  {len(pending)} reactions without a centre")
    centres = 0
    for start in range(0, len(pending), BATCH):
        chunk = pending[start:start + BATCH]
        with pool().connection() as conn:
            records = conn.execute(
                "SELECT reaction_id, reactants, products FROM ord_reactions "
                "WHERE reaction_id = ANY(%s)", (chunk,)
            ).fetchall()
            updates = []
            for record in records:
                try:
                    reaction = normalize(list(record["reactants"]),
                                         list(record["products"]))
                    digest, size = reaction_centre(reaction)
                except Exception:
                    continue
                if digest:
                    updates.append((digest, size, record["reaction_id"]))
            if updates:
                conn.cursor().executemany(
                    "UPDATE ord_reaction_index SET centre_hash = %s, centre_size = %s "
                    "WHERE reaction_id = %s", updates)
                centres += len(updates)
            conn.commit()
        if (start // BATCH) % 10 == 0:
            print(f"    centres {min(start + BATCH, len(pending))}/{len(pending)}")
    print(f"  reaction centres: {centres}")

    #  Pass 3: campaign / class metadata, for benchmark splitting and display.
    if metadata:
        tagged = 0
        for start in range(0, len(ids), BATCH):
            chunk = [i for i in ids[start:start + BATCH] if i in metadata]
            if not chunk:
                continue
            with pool().connection() as conn:
                conn.cursor().executemany(
                    "UPDATE ord_reaction_index SET campaign_id = %s, reaction_class = %s "
                    "WHERE reaction_id = %s",
                    [(metadata[i]["campaign_id"], metadata[i]["reaction_type"], i)
                     for i in chunk],
                )
                conn.commit()
            tagged += len(chunk)
        print(f"  campaign/class metadata: {tagged}")

    with pool().connection() as conn:
        summary = conn.execute(
            """SELECT count(*) AS rows,
                      count(transformation_sfp) AS with_transformation,
                      count(substrate_bfp)      AS with_substrate,
                      count(centre_hash)        AS with_centre,
                      count(DISTINCT centre_hash) AS distinct_centres,
                      count(campaign_id)        AS with_campaign
               FROM ord_reaction_index"""
        ).fetchone()
    print("\nindex summary:")
    for key, value in summary.items():
        print(f"  {key:22s} {value}")
    print(f"built in {time.time() - started:.0f}s")
    return 0


def _insert(conn, reaction_ids: list[str]) -> int:
    """Insert one batch, computing every fingerprint inside the database.

    ON CONFLICT DO NOTHING makes the build resumable: rerun it after a failure
    and it picks up where it stopped rather than starting over.
    """
    cursor = conn.execute(
        f"""INSERT INTO ord_reaction_index (
                reaction_id, reaction_key, reaction_smiles,
                transformation_sfp, transformation_bfp, substrate_bfp, product_bfp,
                dataset_id, dataset_version)
            SELECT r.reaction_id, r.reaction_key, r.reaction_smiles,
                   reaction_difference_fp(
                       reaction_from_smiles(r.reaction_smiles::cstring),
                       {DIFFERENCE_FP_TYPE}),
                   reaction_structural_bfp(
                       reaction_from_smiles(r.reaction_smiles::cstring),
                       {STRUCTURAL_FP_TYPE}),
                   morganbv_fp(mol_from_smiles(
                       array_to_string(r.reactants, '.')::cstring)),
                   r.product_bfp,
                   r.dataset_id, r.dataset_version
            FROM ord_reactions r
            WHERE r.reaction_id = ANY(%s)
            ON CONFLICT (reaction_id) DO NOTHING""",
        (reaction_ids,),
    )
    return cursor.rowcount


if __name__ == "__main__":
    raise SystemExit(main())
