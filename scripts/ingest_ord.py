"""Ingest Open Reaction Database datasets into the local evidence index.

MUST run in the `ord-ingest` conda env, not `retrosynth`: ord-schema pins
protobuf<6 while the serving environment runs protobuf 7 under onnxruntime, and
it pulls rdkit>=2026 against AiZynthFinder's rdkit<2024. Keeping ingestion in its
own env means the serving environment never sees ord-schema at all - it only
reads the flattened rows this script writes.

    conda activate ord-ingest
    python scripts/ingest_ord.py --list
    python scripts/ingest_ord.py --dataset <id> [--limit N]

DATA LICENCE: ORD datasets are CC-BY-SA-4.0 (ShareAlike). See
docs/data-provenance.md before redistributing anything derived from this index.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data/external/ord"
HF_REPO = "open-reaction-database/ord-data"
#: Pinned so a re-ingest fetches the same bytes rather than whatever is on main
#: today. Verified 2026-09-11: every locally ingested parquet is byte-identical
#: to this revision (sha256 == the HF LFS oid). Override with RAMCHEMS_ORD_REV
#: to move deliberately; the content-addressed dataset_version will change and
#: retire the cached evidence derived from the old files.
HF_REVISION = os.environ.get(
    "RAMCHEMS_ORD_REV", "93475c46949f9218e1dfb6624096025135db2add"
)
HF_TREE = (
    f"https://huggingface.co/api/datasets/{HF_REPO}/tree/{HF_REVISION}?recursive=true"
)
HF_FILE = f"https://huggingface.co/datasets/{HF_REPO}/resolve/{HF_REVISION}/"
#: Verified against the repo's own LICENSE, not assumed. Full text is checked in
#: at docs/licenses/ord-data-LICENSE.txt - CC-BY-SA requires the licence to
#: travel with the material.
LICENSE = "CC-BY-SA-4.0"
DSN = os.environ.get(
    "RAMCHEMS_DSN", "postgresql://ramchems:ramchems@127.0.0.1:5434/ramchems"
)


def list_datasets() -> list[dict]:
    """Parquet files on the Hugging Face mirror, smallest first.

    The mirror is used rather than `git clone` of ord-data: that repo stores
    data through Git LFS, so cloning pulls the whole 1.26 GB regardless of what
    you need. Here each dataset is a separate file that can be fetched alone.
    """
    with urllib.request.urlopen(HF_TREE, timeout=90) as response:
        tree = json.load(response)
    files = [f for f in tree if f["path"].endswith(".parquet")]
    files.sort(key=lambda f: f.get("size", 0))
    return files


def download(path: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = DATA_DIR / Path(path).name
    if target.exists():
        print(f"  already present: {target.name} ({target.stat().st_size/1e6:.1f} MB)")
        return target
    print(f"  downloading {path} …")
    urllib.request.urlretrieve(HF_FILE + path, target)
    print(f"  saved {target.name} ({target.stat().st_size/1e6:.1f} MB)")
    return target


#  --- protobuf -> our domain shape ----------------------------------------


def _identifier(component, wanted: int) -> str | None:
    for ident in component.identifiers:
        if ident.type == wanted and ident.value:
            return ident.value
    return None


def _finite(value) -> float | None:
    """Reject NaN/inf. ORD carries occasional NaN numerics, which are not valid
    JSON and are not measurements either - a missing value must stay missing."""
    import math

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _canonical(smiles: str | None) -> str | None:
    from rdkit import Chem, RDLogger

    RDLogger.DisableLog("rdApp.*")
    if not smiles:
        return None
    mol = Chem.MolFromSmiles(smiles)
    return Chem.MolToSmiles(mol) if mol is not None else None


def extract(rxn, dataset_id: str, dataset_name: str) -> dict | None:
    """Flatten one ORD Reaction into index columns.

    Missing fields stay missing. A reaction with no temperature is still a
    useful precedent for its reagents and solvent, so it is kept rather than
    discarded for being incomplete.
    """
    from ord_schema.proto import reaction_pb2

    Role = reaction_pb2.ReactionRole.ReactionRoleType
    SMILES_ID, NAME_ID = 2, 6

    reactants: list[str] = []
    reagents: list[dict] = []
    catalysts: list[dict] = []
    solvents: list[dict] = []

    for _key, inp in rxn.inputs.items():
        for component in inp.components:
            smiles = _canonical(_identifier(component, SMILES_ID))
            name = _identifier(component, NAME_ID)
            if not smiles and not name:
                continue
            role = component.reaction_role
            entry = {"name": name, "smiles": smiles,
                     "role": Role.Name(role) if role is not None else None}
            if role == Role.REACTANT:
                if smiles:
                    reactants.append(smiles)
            elif role == Role.CATALYST:
                catalysts.append(entry)
            elif role == Role.SOLVENT:
                solvents.append(entry)
            elif role in (Role.REAGENT, Role.INTERNAL_STANDARD):
                reagents.append(entry)

    products: list[str] = []
    yield_pct: float | None = None
    time_value = time_unit = None
    for outcome in rxn.outcomes:
        if outcome.reaction_time.value and outcome.reaction_time.units:
            unit_name = reaction_pb2.Time.TimeUnit.Name(outcome.reaction_time.units)
            if unit_name != "UNSPECIFIED":
                candidate = _finite(outcome.reaction_time.value)
                if candidate is not None:
                    time_value = candidate
                    time_unit = unit_name.lower()
        for product in outcome.products:
            smiles = _canonical(_identifier(product.identifiers and product, SMILES_ID)) \
                if False else _canonical(_identifier(product, SMILES_ID))
            if smiles:
                products.append(smiles)
            for measurement in product.measurements:
                # YIELD measurements carry a percentage; other types do not.
                if measurement.HasField("percentage"):
                    value = _finite(measurement.percentage.value)
                    if value is not None and 0.0 <= value <= 100.0:
                        yield_pct = value if yield_pct is None else max(yield_pct, value)

    if not reactants or not products:
        return None

    temperature_value = temperature_unit = None
    setpoint = rxn.conditions.temperature.setpoint
    if setpoint.units:
        unit_name = reaction_pb2.Temperature.TemperatureUnit.Name(setpoint.units)
        if unit_name != "UNSPECIFIED":
            temperature_value = _finite(setpoint.value)
            if temperature_value is not None:
                temperature_unit = {
                    "CELSIUS": "C", "KELVIN": "K", "FAHRENHEIT": "F"
                }.get(unit_name, unit_name)

    workups = []
    for workup in rxn.workups:
        label = reaction_pb2.ReactionWorkup.ReactionWorkupType.Name(workup.type)
        if label and label != "UNSPECIFIED":
            workups.append(label.replace("_", " ").title())

    provenance = rxn.provenance
    prov = {
        "source_type": "patent" if provenance.patent else "paper",
        "source_id": rxn.reaction_id,
        "dataset_name": dataset_name,
        "doi": provenance.doi or None,
        "patent_number": provenance.patent or None,
        # Only a URL the record actually supplied. Never constructed from a DOI.
        "url": provenance.publication_url or None,
        "license": LICENSE,
        "reaction_identifier": rxn.reaction_id,
    }
    if not provenance.doi and not provenance.patent:
        prov["source_type"] = "dataset"

    reactants = sorted(set(reactants))
    products = sorted(set(products))
    normalized = f"{'.'.join(reactants)}>>{'.'.join(products)}"

    return {
        "reaction_id": rxn.reaction_id,
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "reaction_key": hashlib.sha256(normalized.encode()).hexdigest(),
        "reaction_smiles": normalized,
        "reactants": reactants,
        "products": products,
        "major_product": products[0],
        "conditions": {
            "reagents": reagents,
            "catalysts": catalysts,
            "solvents": solvents,
            "temperature": (
                {"value": temperature_value, "unit": temperature_unit}
                if temperature_value is not None else None
            ),
            "time": (
                {"value": time_value, "unit": time_unit}
                if time_value is not None else None
            ),
            "yield": {"value": yield_pct} if yield_pct is not None else None,
            "workup": workups,
            "procedure_excerpt": (rxn.notes.procedure_details or "")[:400] or None,
        },
        "provenance": prov,
        "has_temperature": temperature_value is not None,
        "has_time": time_value is not None,
        "has_yield": yield_pct is not None,
    }


def ingest(parquet_path: Path, limit: int | None) -> None:
    import psycopg
    import pyarrow.parquet as pq
    from importlib.metadata import version
    from ord_schema.proto import reaction_pb2

    dataset_id = parquet_path.stem.replace("ord_dataset-", "")
    table = pq.read_table(parquet_path)
    total = table.num_rows
    raw_column = table.column("reaction")
    print(f"  {parquet_path.name}: {total} reactions")

    # Dataset version is content-addressed: a refreshed file changes the hash,
    # which changes the cache key, which retires stale cached evidence.
    digest = hashlib.sha256(parquet_path.read_bytes()).hexdigest()[:16]

    rows, skipped, dataset_name = [], 0, dataset_id
    for index in range(total if limit is None else min(limit, total)):
        rxn = reaction_pb2.Reaction()
        rxn.ParseFromString(raw_column[index].as_py())
        record = extract(rxn, dataset_id, dataset_name)
        if record is None:
            skipped += 1
            continue
        rows.append(record)

    print(f"  extracted {len(rows)}, skipped {skipped} (no parseable reactant/product)")
    if not rows:
        return

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        for record in rows:
            cur.execute(
                """
                INSERT INTO ord_reactions
                    (reaction_id, dataset_id, dataset_name, reaction_key,
                     reaction_smiles, reactants, products, product_bfp,
                     conditions, provenance, has_temperature, has_time,
                     has_yield, dataset_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,
                        morganbv_fp(mol_from_smiles(%s::cstring)),
                        %s,%s,%s,%s,%s,%s)
                ON CONFLICT (reaction_id) DO UPDATE SET
                    conditions = EXCLUDED.conditions,
                    provenance = EXCLUDED.provenance,
                    dataset_version = EXCLUDED.dataset_version,
                    ingested_at = now()
                """,
                (
                    record["reaction_id"], record["dataset_id"], record["dataset_name"],
                    record["reaction_key"], record["reaction_smiles"],
                    record["reactants"], record["products"], record["major_product"],
                    json.dumps(record["conditions"]), json.dumps(record["provenance"]),
                    record["has_temperature"], record["has_time"], record["has_yield"],
                    digest,
                ),
            )
        cur.execute(
            """
            INSERT INTO ord_ingest_log
                (dataset_id, dataset_name, source_url, license, reaction_count,
                 ord_schema_ver, dataset_version)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (dataset_id) DO UPDATE SET
                reaction_count = EXCLUDED.reaction_count,
                dataset_version = EXCLUDED.dataset_version,
                ingested_at = now()
            """,
            (dataset_id, dataset_name, HF_FILE + parquet_path.name, LICENSE,
             len(rows), version("ord-schema"), digest),
        )
        conn.commit()

    with_temp = sum(r["has_temperature"] for r in rows)
    with_time = sum(r["has_time"] for r in rows)
    with_yield = sum(r["has_yield"] for r in rows)
    with_doi = sum(bool(r["provenance"].get("doi")) for r in rows)
    print(f"  indexed {len(rows)} | temperature {with_temp} | time {with_time} "
          f"| yield {with_yield} | DOI {with_doi}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list available datasets")
    parser.add_argument("--dataset", help="parquet path on the mirror, or local file")
    parser.add_argument("--max-mb", type=float, default=12.0,
                        help="with --auto, largest file to take")
    parser.add_argument("--auto", type=int, metavar="N",
                        help="ingest the N largest datasets under --max-mb")
    parser.add_argument("--limit", type=int, help="cap reactions per dataset")
    args = parser.parse_args()

    if args.list:
        files = list_datasets()
        print(f"{len(files)} datasets on {HF_REPO} (licence {LICENSE})")
        for f in files[-12:]:
            print(f"  {f['size']/1e6:9.2f} MB  {f['path']}")
        return 0

    targets: list[str] = []
    if args.auto:
        files = [f for f in list_datasets() if f.get("size", 0) <= args.max_mb * 1e6]
        targets = [f["path"] for f in files[-args.auto:]]
        print(f"selected {len(targets)} datasets under {args.max_mb} MB")
    elif args.dataset:
        targets = [args.dataset]
    else:
        parser.error("give --list, --dataset or --auto")

    print(f"\nSource: {HF_REPO}  |  Licence: {LICENSE} (ShareAlike)")
    for path in targets:
        local = Path(path)
        ingest(local if local.exists() else download(path), args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
