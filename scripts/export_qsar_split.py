"""Export the exact scaffold split to CSV so Chemprop trains on identical data.

Chemprop lives in a separate conda env (`qsar-chemprop`) because it wants
rdkit>=2026 and networkx>=3, which would break AiZynthFinder's rdkit<2024 /
networkx<3 pins and the Postgres cartridge version coupling. Passing the split
through files keeps the two environments from having to import each other.

    python scripts/export_qsar_split.py
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.qsar.dataset import UNITS, load  # noqa: E402
from backend.qsar.splits import random_split, scaffold_split  # noqa: E402

OUT_DIR = ROOT / "data/external/esol"


def write(path: Path, compounds) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["smiles", "logSolubility"])
        writer.writerows([[c.smiles, c.label] for c in compounds])


def write_with_split_column(path: Path, split) -> None:
    """Single file with a `split` column - what chemprop's --splits-column wants.
    Keeps chemprop on exactly the same partition as the sklearn baselines rather
    than letting it re-split the data itself."""
    train, val, test = split
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["smiles", "logSolubility", "split"])
        for part_name, part in (("train", train), ("val", val), ("test", test)):
            writer.writerows([[c.smiles, c.label, part_name] for c in part])


def main() -> int:
    compounds, report = load()
    print(f"{report.unique} unique compounds, label units {UNITS}")

    for split_name, split in (
        ("scaffold", scaffold_split(compounds)),
        ("random", random_split(compounds)),
    ):
        for part_name, part in zip(("train", "val", "test"), split):
            path = OUT_DIR / f"{split_name}_{part_name}.csv"
            write(path, part)
            print(f"  {path.name:24s} {len(part):4d} rows")
        combined = OUT_DIR / f"{split_name}_all.csv"
        write_with_split_column(combined, split)
        print(f"  {combined.name:24s} {sum(len(p) for p in split):4d} rows (split column)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
