"""Ingest Lowe's USPTO patent-grant reactions into the evidence index.

Source: "Chemical reactions from US patents (1976-Sep2016)", D. Lowe,
figshare DOI 10.6084/m9.figshare.5104873, licence CC0 - commercially clean,
unlike ORD's CC-BY-SA.

    conda activate retrosynth
    7z x data/external/uspto/1976_Sep2016_USPTOgrants_cml.7z -odata/external/uspto
    python scripts/ingest_uspto.py [--years 1976 1977] [--limit-files N]
    python scripts/build_reaction_index.py        # then index the new rows

Rows land in `ord_reactions` beside ORD, so exact matching, similar-precedent
retrieval and the evidence cache all pick them up without a second provider.
# ponytail: table is still named ord_reactions; rename when a third source lands.

WHAT IS RECORDED, AND THE RULES THAT KEEP IT HONEST

  identity    reactants = components of the reaction SMILES that carry atom
              maps, i.e. contribute atoms to the product. Unmapped species in
              the reactant slot (solvents, bases) are conditions, not reactants,
              so the reaction_key matches how a retrosynthesis step is keyed.
  source      one row per (patent, reaction). The same reaction in three
              patents is three rows, so a step links to every patent.
  temperature only when every temperature mentioned in the procedure resolves
              to the same exact number. Ranges, "~N", "<N" and "room
              temperature" are left out rather than turned into a number.
  time        the longest single stated step duration.
  yield       the yield as written in the patent (PERCENTYIELD) only. The
              extractor's CALCULATEDPERCENTYIELD is derived from masses, can
              exceed 100%, and is not what the patent reported.
  link        a Google Patents URL built from the patent number, marked
              url_origin = "derived_from_patent_number". Never from a DOI.

Resumable: each finished XML file is appended to a progress file, and reaction
ids are content-derived, so a re-run skips finished files and conflicting rows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import time
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.conditions.extract import patent_url  # noqa: E402
from backend.conditions.normalize import NormalizationError, canonical, normalize  # noqa: E402
from backend.molrepr.search import DSN  # noqa: E402

DATA = ROOT / "data/external/uspto"
ARCHIVE = DATA / "1976_Sep2016_USPTOgrants_cml.7z"
GRANTS = DATA / "grants"
PROGRESS = DATA / "ingested_files.txt"

DATASET_ID = "uspto-grants-1976-2016"
DATASET_NAME = "USPTO patent grants 1976–Sep 2016 (Lowe)"
SOURCE_URL = "https://doi.org/10.6084/m9.figshare.5104873"
LICENSE = "CC0-1.0"
BATCH = 1000

C = "{http://www.xml-cml.org/schema}"
DL = "{http://bitbucket.org/dan2097}"
ATOM_MAP = re.compile(r":\d+\]")
EXACT_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
WORKUP_ACTIONS = {
    "Concentrate", "Distill", "Dry", "Extract", "Filter", "Partition",
    "Precipitate", "Purify", "Recrystallize", "Remove", "Wash",
}


# ------------------------------------------------------------- parsing ----


def split_reaction_smiles(text: str) -> tuple[list[str], list[str], list[str]] | None:
    """(mapped reactants, agents, products) with atom maps stripped."""
    smiles = (text or "").strip().split(" ")[0]          # drop a CXSMILES |...| tail
    parts = smiles.split(">")
    if len(parts) != 3:
        return None
    reactant_slot, agent_slot, product_slot = (
        [c for c in part.split(".") if c] for part in parts
    )
    reactants = [c for c in reactant_slot if ATOM_MAP.search(c)]
    agents = [c for c in reactant_slot if not ATOM_MAP.search(c)] + agent_slot
    mapped_products = [c for c in product_slot if ATOM_MAP.search(c)]
    products = mapped_products or product_slot

    def strip(component: str) -> str:
        return ATOM_MAP.sub("]", component)

    return [strip(c) for c in reactants], [strip(c) for c in agents], [strip(c) for c in products]


def _entity(element, role: str) -> dict | None:
    name_el = element.find(f"{C}molecule/{C}name")
    name = (name_el.text or "").strip() if name_el is not None else ""
    smiles = None
    for ident in element.iter(f"{C}identifier"):
        if ident.get("dictRef") == "cml:smiles":
            smiles = canonical(ident.get("value", ""))
            break
    if not name and not smiles:
        return None
    return {"name": name or None, "smiles": smiles, "role": role}


def extract(reaction, year: int) -> dict | None:
    source = reaction.find(f"{DL}source")
    patent = (source.findtext(f"{DL}documentId") or "").strip() if source is not None else ""
    split = split_reaction_smiles(reaction.findtext(f"{DL}reactionSmiles"))
    if not patent or split is None:
        return None
    reactants, _agents, products = split
    try:
        norm = normalize(reactants, products)
    except NormalizationError:
        return None

    #  conditions, in the roles the source assigned
    solvents, catalysts, reagents, seen = [], [], [], set()
    for spectator in reaction.iter(f"{C}spectator"):
        role = spectator.get("role", "")
        item = _entity(spectator, role.upper() or "REAGENT")
        if item is None:
            continue
        key = (item["name"], item["smiles"])
        if key in seen:
            continue
        seen.add(key)
        {"solvent": solvents, "catalyst": catalysts}.get(role, reagents).append(item)
    reactant_set = set(norm.reactants)
    for reactant in reaction.iter(f"{C}reactant"):
        item = _entity(reactant, "REAGENT")
        if item is None or item["smiles"] in reactant_set:
            continue
        key = (item["name"], item["smiles"])
        if key not in seen:
            seen.add(key)
            reagents.append(item)

    temperatures, durations, workup = [], [], []
    for action in reaction.iter(f"{DL}reactionAction"):
        name = action.get("action", "")
        if name in WORKUP_ACTIONS and name not in workup:
            workup.append(name)
        for parameter in action.iter(f"{DL}parameter"):
            kind = parameter.get("propertyType")
            value = parameter.get("normalizedValue", "")
            if kind == "Temperature":
                temperatures.append(value)
            elif kind == "Time":
                try:
                    durations.append(float(value))
                except ValueError:
                    pass

    temperature = None
    if temperatures and all(EXACT_NUMBER.match(t) for t in temperatures) \
            and len({float(t) for t in temperatures}) == 1:
        temperature = {"value": float(temperatures[0]), "unit": "C"}
    time_value = (
        {"value": round(max(durations) / 3600, 4), "unit": "h"} if durations else None
    )
    yield_pct = None
    for amount in reaction.iter(f"{C}amount"):
        if amount.get(f"{DL}propertyType") == "PERCENTYIELD":
            try:
                value = float(amount.get(f"{DL}normalizedValue", ""))
            except ValueError:
                continue
            if 0.0 <= value <= 100.0:
                yield_pct = value
                break

    url = patent_url(patent)
    paragraph = (source.findtext(f"{DL}paragraphText") or "").strip()
    reaction_id = "uspto-" + hashlib.sha1(
        f"{patent}|{norm.reaction_key}".encode()).hexdigest()[:24]
    return {
        "reaction_id": reaction_id,
        "reaction_key": norm.reaction_key,
        "reaction_smiles": norm.reaction_smiles,
        "reactants": norm.reactants,
        "products": norm.products,
        "major_product": norm.products[0],
        "conditions": {
            "reagents": reagents,
            "catalysts": catalysts,
            "solvents": solvents,
            "temperature": temperature,
            "time": time_value,
            "yield": {"value": yield_pct} if yield_pct is not None else None,
            "workup": workup,
            "procedure_excerpt": paragraph[:400] or None,
        },
        "provenance": {
            "source_type": "patent",
            "source_id": patent,
            "dataset_name": DATASET_NAME,
            "title": (source.findtext(f"{DL}headingText") or "").strip() or None,
            "year": year,
            "patent_number": patent,
            "url": url,
            "url_origin": "derived_from_patent_number" if url else None,
            "license": LICENSE,
            "reaction_identifier": reaction_id,
        },
        "has_temperature": temperature is not None,
        "has_time": time_value is not None,
        "has_yield": yield_pct is not None,
    }


def reactions_in(path: pathlib.Path):
    """Stream reactions out of one weekly file without holding it in memory."""
    context = ET.iterparse(str(path), events=("start", "end"))
    _, root = next(context)
    for event, element in context:
        if event == "end" and element.tag == f"{C}reaction":
            yield element
            root.clear()


# ------------------------------------------------------------- loading ----


INSERT = """
    INSERT INTO ord_reactions
        (reaction_id, dataset_id, dataset_name, reaction_key, reaction_smiles,
         reactants, products, product_bfp, conditions, provenance,
         has_temperature, has_time, has_yield, dataset_version)
    VALUES (%s,%s,%s,%s,%s,%s,%s,
            morganbv_fp(mol_from_smiles(%s::cstring)),
            %s,%s,%s,%s,%s,%s)
    ON CONFLICT (reaction_id) DO NOTHING
"""


def archive_version() -> str:
    digest = hashlib.sha256()
    with ARCHIVE.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def main() -> int:
    import psycopg

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", nargs="*", type=int, help="only these grant years")
    parser.add_argument("--limit-files", type=int, help="stop after N files (trial runs)")
    args = parser.parse_args()

    if not GRANTS.exists():
        raise SystemExit(f"{GRANTS} not found - extract {ARCHIVE.name} first (see docstring)")
    files = sorted(GRANTS.rglob("*.xml"))
    if args.years:
        files = [f for f in files if int(f.parent.name) in set(args.years)]
    done = set(PROGRESS.read_text(encoding="utf-8").split()) if PROGRESS.exists() else set()
    todo = [f for f in files if f.relative_to(DATA).as_posix() not in done]
    if args.limit_files:
        todo = todo[: args.limit_files]
    version = archive_version()
    print(f"{len(files)} files selected, {len(done)} already ingested, {len(todo)} to do "
          f"(dataset_version {version})")

    totals = {"parsed": 0, "skipped": 0, "temperature": 0, "time": 0, "yield": 0}
    started = time.time()
    with psycopg.connect(DSN) as conn:
        for number, path in enumerate(todo, 1):
            year = int(path.parent.name)
            batch = []
            with conn.cursor() as cur:
                for element in reactions_in(path):
                    record = extract(element, year)
                    if record is None:
                        totals["skipped"] += 1
                        continue
                    totals["parsed"] += 1
                    totals["temperature"] += record["has_temperature"]
                    totals["time"] += record["has_time"]
                    totals["yield"] += record["has_yield"]
                    batch.append((
                        record["reaction_id"], DATASET_ID, DATASET_NAME,
                        record["reaction_key"], record["reaction_smiles"],
                        record["reactants"], record["products"], record["major_product"],
                        json.dumps(record["conditions"]), json.dumps(record["provenance"]),
                        record["has_temperature"], record["has_time"], record["has_yield"],
                        version,
                    ))
                    if len(batch) >= BATCH:
                        cur.executemany(INSERT, batch)
                        batch.clear()
                if batch:
                    cur.executemany(INSERT, batch)
            conn.commit()
            with PROGRESS.open("a", encoding="utf-8") as handle:
                handle.write(path.relative_to(DATA).as_posix() + "\n")
            if number % 10 == 0 or number == len(todo):
                rate = totals["parsed"] / max(1.0, time.time() - started)
                print(f"  {number}/{len(todo)} files | {totals['parsed']} reactions "
                      f"| {totals['skipped']} skipped | {rate:.0f}/s")

        count = conn.execute(
            "SELECT count(*) FROM ord_reactions WHERE dataset_id = %s", (DATASET_ID,)
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO ord_ingest_log
                   (dataset_id, dataset_name, source_url, license, reaction_count,
                    ord_schema_ver, dataset_version)
               VALUES (%s,%s,%s,%s,%s,NULL,%s)
               ON CONFLICT (dataset_id) DO UPDATE SET
                   reaction_count = EXCLUDED.reaction_count,
                   dataset_version = EXCLUDED.dataset_version,
                   ingested_at = now()""",
            (DATASET_ID, DATASET_NAME, SOURCE_URL, LICENSE, count, version),
        )
        conn.commit()

    print(f"\nthis run: {totals}")
    print(f"{DATASET_ID}: {count} reactions in the index, "
          f"{time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
