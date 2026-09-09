"""ESOL (Delaney) aqueous solubility dataset: load, canonicalize, deduplicate.

Source: DeepChem's MoleculeNet copy of Delaney's supplementary data,
https://raw.githubusercontent.com/deepchem/deepchem/master/datasets/delaney-processed.csv
1,128 rows -> 1,117 unique structures after deduplication.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from backend.molrepr import service

ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "data/external/esol/delaney-processed.csv"

# The file carries TWO solubility columns. "ESOL predicted..." is Delaney's own
# model output; training on it would fit a model to another model's guesses.
LABEL_COLUMN = "measured log solubility in mols per litre"
UNITS = "log10(mol/L)"

# Collapsed pairs disagreeing by more than this are worth a human look rather than
# being silently averaged away.
SPREAD_WARN = 0.5


@dataclass
class Compound:
    smiles: str          # canonical, parent-stripped, via backend.molrepr.service
    label: float         # measured log solubility, averaged if duplicated
    names: list[str]
    n_measurements: int
    spread: float        # max - min across duplicate measurements; 0.0 if unique


@dataclass
class LoadReport:
    rows_read: int
    parsed: int
    rejected: list[tuple[str, str, str]]
    unique: int
    collapsed: list[tuple[str, list[str], list[float], float]]

    def summary(self) -> str:
        lines = [
            "=" * 62,
            f"  source          {CSV_PATH.name}",
            f"  rows read       {self.rows_read}",
            f"  parsed          {self.parsed}",
            f"  rejected        {len(self.rejected)}",
            f"  unique          {self.unique}  (deduplicated on canonical SMILES)",
            f"  collapsed       {len(self.collapsed)} structures with >1 measurement",
            "=" * 62,
        ]
        for smiles, names, labels, spread in sorted(
            self.collapsed, key=lambda c: -c[3]
        ):
            flag = "  <-- SPREAD" if spread > SPREAD_WARN else ""
            lines.append(
                f"  {spread:5.3f}  {' / '.join(names)[:44]:46s} "
                f"{[round(v, 3) for v in labels]}{flag}"
            )
            lines.append(f"         {smiles}")
        for compound_id, smiles, err in self.rejected:
            lines.append(f"  REJECT {compound_id}: {smiles[:40]} - {err}")
        return "\n".join(lines)


def load(csv_path: Path = CSV_PATH) -> tuple[list[Compound], LoadReport]:
    """Load ESOL, canonicalizing through backend.molrepr.service.

    Duplicates are averaged rather than dropped: the same structure appearing in
    both train and test would leak, and a scaffold split cannot prevent it because
    identical structures share a scaffold by definition.
    """
    if not csv_path.exists():
        raise FileNotFoundError(
            f"{csv_path} not found - run: python scripts/download_esol.py"
        )

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    grouped: dict[str, list[tuple[str, float]]] = defaultdict(list)
    rejected: list[tuple[str, str, str]] = []
    parsed = 0

    for row in rows:
        raw_smiles = row["smiles"]
        name = row.get("Compound ID", "?")
        try:
            canonical = service.canonicalize(raw_smiles)
            label = float(row[LABEL_COLUMN])
        except (service.InvalidSmilesError, ValueError, KeyError) as err:
            rejected.append((name, raw_smiles, str(err)[:70]))
            continue
        parsed += 1
        grouped[canonical].append((name, label))

    compounds: list[Compound] = []
    collapsed: list[tuple[str, list[str], list[float], float]] = []
    for canonical, entries in grouped.items():
        names = [name for name, _ in entries]
        labels = [label for _, label in entries]
        spread = max(labels) - min(labels) if len(labels) > 1 else 0.0
        if len(labels) > 1:
            collapsed.append((canonical, names, labels, spread))
        compounds.append(
            Compound(
                smiles=canonical,
                label=sum(labels) / len(labels),
                names=names,
                n_measurements=len(labels),
                spread=spread,
            )
        )

    compounds.sort(key=lambda c: c.smiles)  # deterministic order for reproducibility
    report = LoadReport(
        rows_read=len(rows),
        parsed=parsed,
        rejected=rejected,
        unique=len(compounds),
        collapsed=collapsed,
    )
    return compounds, report


if __name__ == "__main__":
    _, report = load()
    print(report.summary())
