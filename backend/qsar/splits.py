"""Train/val/test splits: Bemis-Murcko scaffold, plus random for comparison.

A random split puts structurally near-identical analogues on both sides of the
train/test boundary, so the test score measures interpolation between neighbours
rather than generalisation. The scaffold split groups molecules by their
Bemis-Murcko framework and keeps whole scaffold groups together, which is the
honest number. Both are reported so the gap is visible.
"""
from __future__ import annotations

import random
from collections import defaultdict

from rdkit import Chem
from rdkit.Chem.Scaffolds import MurckoScaffold

from backend.qsar.dataset import Compound

Split = tuple[list[Compound], list[Compound], list[Compound]]

SEED = 0xC0FFEE


def murcko_scaffold(smiles: str, include_chirality: bool = False) -> str:
    """Bemis-Murcko framework. Acyclic molecules yield '' - they all group
    together, which is the standard (if crude) convention."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return ""
    return MurckoScaffold.MurckoScaffoldSmiles(
        mol=mol, includeChirality=include_chirality
    )


def scaffold_split(
    compounds: list[Compound], frac_train=0.8, frac_val=0.1
) -> Split:
    """Largest scaffold groups go to train, smallest to test.

    Putting the rare scaffolds in test is deliberate: it makes test the hardest,
    most novel chemistry, which is what an applicability question actually asks.
    """
    groups: dict[str, list[Compound]] = defaultdict(list)
    for compound in compounds:
        groups[murcko_scaffold(compound.smiles)].append(compound)

    # Big groups first; ties broken by scaffold string so runs are reproducible.
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    n = len(compounds)
    n_train, n_val = int(frac_train * n), int(frac_val * n)
    train: list[Compound] = []
    val: list[Compound] = []
    test: list[Compound] = []
    for _, members in ordered:
        if len(train) + len(members) <= n_train:
            train.extend(members)
        elif len(val) + len(members) <= n_val:
            val.extend(members)
        else:
            test.extend(members)
    return train, val, test


def random_split(compounds: list[Compound], frac_train=0.8, frac_val=0.1) -> Split:
    shuffled = list(compounds)
    random.Random(SEED).shuffle(shuffled)
    n = len(shuffled)
    n_train, n_val = int(frac_train * n), int(frac_val * n)
    return (
        shuffled[:n_train],
        shuffled[n_train : n_train + n_val],
        shuffled[n_train + n_val :],
    )


def describe(name: str, split: Split, compounds: list[Compound]) -> str:
    train, val, test = split
    scaffolds = {
        part: {murcko_scaffold(c.smiles) for c in group}
        for part, group in (("train", train), ("val", val), ("test", test))
    }
    overlap = len(scaffolds["train"] & scaffolds["test"])
    return (
        f"{name:9s} train {len(train):4d} | val {len(val):4d} | test {len(test):4d} "
        f"| distinct scaffolds {len(set().union(*scaffolds.values())):4d} "
        f"| train/test scaffold overlap {overlap:4d}"
    )
