"""Extract human-assigned reaction-type labels from ORD, for retrieval benchmarking.

Two of the six ingested datasets carry a `REACTION_TYPE` identifier written by
the people who ran the experiments:

  * ord_dataset-d92976309c3a…  ~39k reactions, 36 distinct types
                               (SUZUKI, BUCHWALD, MITSUNOBU, SNAR, HECK, …)
  * ord_dataset-805ad863feef…  ~51k reactions, all "C-N coupling"

That label is the only ground truth in this project that is genuinely
independent of the retrieval system being measured: it was assigned by a chemist
describing their own campaign, not derived from any fingerprint, so scoring
retrieval against it is not circular.

Deliberately NOT written into `ord_reactions`. Adding a column would change the
table the evidence layer serves from, and the labels exist only to grade a
benchmark - they must not become something retrieval could learn from or return.

    conda activate ord-ingest
    python scripts/extract_benchmark_labels.py

Writes data/external/ord/benchmark_labels.csv (gitignored - regenerate it rather
than committing a derivative of CC-BY-SA data; see docs/data-provenance.md).
"""
from __future__ import annotations

import csv
import pathlib
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data/external/ord"
OUT = DATA_DIR / "benchmark_labels.csv"


def normalise(value: str) -> str:
    """Fold the label to a stable comparable form.

    ORD carries "C-N coupling" with a real en-dash, and case is inconsistent
    across records ("ALKYLATION" vs "Alkylation"). Neither difference is
    chemistry, so both are normalised away before anything is compared.
    """
    text = unicodedata.normalize("NFKC", value or "").strip()
    for dash in ("‐", "‑", "‒", "–", "—", "−"):
        text = text.replace(dash, "-")
    return " ".join(text.split()).upper()


#: CUSTOM identifier keys naming the experimental campaign a reaction belongs
#: to, best first. Reactions from one screen or plate share substrates,
#: conditions and authors, so they must stay on the same side of a train/test
#: split - otherwise retrieval is graded on finding a reaction's own plate-mates,
#: which no real query would have.
CAMPAIGN_KEYS = ("SCREEN_ID", "experiment", "NOTEBOOK_ID", "ReactionGroup")


def campaign_of(custom: dict[str, str], dataset_id: str) -> str:
    for key in CAMPAIGN_KEYS:
        if custom.get(key):
            return f"{dataset_id[:8]}:{key}={custom[key]}"
    return f"{dataset_id[:8]}:whole-dataset"


def main() -> int:
    import pyarrow.parquet as pq
    from ord_schema.proto import reaction_pb2

    kind = reaction_pb2.ReactionIdentifier.ReactionIdentifierType
    rows: list[dict] = []

    for path in sorted(DATA_DIR.glob("*.parquet")):
        dataset_id = path.stem.replace("ord_dataset-", "")
        table = pq.read_table(path)
        column = table.column("reaction")
        found = 0
        for index in range(table.num_rows):
            reaction = reaction_pb2.Reaction()
            reaction.ParseFromString(column[index].as_py())
            reaction_type = reaction_class = None
            custom: dict[str, str] = {}
            for ident in reaction.identifiers:
                name = kind.Name(ident.type)
                if name == "REACTION_TYPE":
                    reaction_type = normalise(ident.value)
                elif name == "CUSTOM":
                    detail = (ident.details or "").strip()
                    custom[detail] = ident.value.strip()
                    if "class" in detail.lower():
                        reaction_class = normalise(ident.value)
            if not reaction_type:
                continue
            found += 1
            rows.append({
                "reaction_id": reaction.reaction_id,
                "dataset_id": dataset_id,
                "reaction_type": reaction_type,
                "reaction_class": reaction_class or "",
                "campaign_id": campaign_of(custom, dataset_id),
            })
        print(f"  {path.stem[-12:]}: {found:6d} / {table.num_rows} labelled")

    if not rows:
        print("no REACTION_TYPE labels found - nothing to benchmark against")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["reaction_id", "dataset_id", "reaction_type",
                                "reaction_class", "campaign_id"]
        )
        writer.writeheader()
        writer.writerows(rows)

    types: dict[str, int] = {}
    for row in rows:
        types[row["reaction_type"]] = types.get(row["reaction_type"], 0) + 1
    campaigns = {row["campaign_id"] for row in rows}
    print("")
    print(
        f"{len(rows)} labelled reactions, {len(types)} distinct types, "
        f"{len(campaigns)} campaigns -> {OUT}"
    )
    for name, count in sorted(types.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {count:6d}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
