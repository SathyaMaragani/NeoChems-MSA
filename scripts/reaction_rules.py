"""Rule-based reaction-type labels, so the retrieval benchmark can grade USPTO.

ORD's human REACTION_TYPE labels cover 90k reactions; USPTO's 1.77M carry none,
so the benchmark could not grade the precedent production shows first in 55% of
queries. These rules label ANY reaction - ORD or USPTO - from what changes
between its reactants and products, so both corpora are graded by one grader.

    python scripts/reaction_rules.py        # agreement with the human labels

BENCHMARK ONLY. This lives in scripts/, not backend/, so retrieval cannot import
it: a grader the retriever can see measures nothing.

What the rules can and cannot see
  * They read the bond change, never the catalyst. BUCHWALD, ULLMANN, C-N
    COUPLING (and SNAr) make the same aryl C-N/C-O bond with different metals,
    so they share one group. PD/RH COUPLING, CH ACTIVATION, LEWIS ACID and
    PHOTOCHEMISTRY are defined by conditions and get no rule at all.
  * A reagent the record omits is invisible. ORD's NEGISHI rows list no zinc
    reagent (it is made in the flask), so a Negishi rule could never be
    validated against them - and there is none.
  * A rule fires if ANY product shows the change, so an internal standard in
    the product list (C-N COUPLING rows carry caffeine) cannot trigger one.
The validation run reports how often each rule agrees with the human label,
which is the only reason to trust the grading at all.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
from typing import Optional

from rdkit import Chem, RDLogger

RDLogger.DisableLog("rdApp.*")

_S = {name: Chem.MolFromSmarts(smarts) for name, smarts in {
    "c_B": "[#6]-[B]",
    "c_Sn": "[#6]-[Sn]",
    "ar_X": "[c,$([CX3]=[CX3])]-[Cl,Br,I,$(OS(=O)(=O)C(F)(F)F)]",
    "nitrile": "[#6]-[CX2]#[NX1]",
    "c_F": "[#6]-F",
    "c_hal": "[#6]-[Cl,Br,I]",
    "ar_carbonyl": "c-[CX3]=O",
    #  n included: N-arylation of an azole is a C-N coupling too. O only as an
    #  ether, so a triflate's own C-O is not mistaken for a new one.
    "ar_N_or_O": "c-[NX3,n,$([OX2](c)[#6])]",
    "amide": "[CX3](=O)-[NX3]",
    "acid": "[CX3](=O)[OX2H1,Cl]",
    "ar_alkene": "c-[CX3]=[CX3]",
    "alkyl_LG": "[CX4]-[Cl,Br,I,$(OS(=O)=O)]",
    "het_sp3C": "[N,O,S;!$(*=*)]-[CX4]",
}.items()}

#: Human REACTION_TYPE -> the rule group that should fire on it. Types missing
#: here have no structural signature; the benchmark cannot grade them by rule.
HUMAN_TO_RULE = {
    "SUZUKI": "SUZUKI", "STILLE": "STILLE",
    "PD BORYLATION": "BORYLATION", "CYANATION": "CYANATION",
    "FLUORINATION": "FLUORINATION", "HALOGENATION": "HALOGENATION",
    "CARBONYLATION": "CARBONYLATION", "HECK": "HECK",
    "BUCHWALD": "ARYL C-N/C-O", "ULLMANN": "ARYL C-N/C-O",
    "C-N COUPLING": "ARYL C-N/C-O", "SNAR": "ARYL C-N/C-O",
    "AMIDE COUPLING": "AMIDE COUPLING", "ALKYLATION": "ALKYLATION",
    "HYDROGENATION": "HYDROGENATION", "BIOMIMETIC OXIDATION": "OXIDATION",
}


#: Rules that are exact on ORD (100% precision) but loose on USPTO, where the
#: same net change comes from different chemistry: aryl-Br -> CHO by lithiation
#: and DMF "is" a carbonylation, a mCPBA N-oxide "is" a C-H oxidation. A query
#: of these types cannot be graded fairly across both corpora, so it is dropped.
UNGRADED = {"CARBONYLATION", "OXIDATION"}


@lru_cache(maxsize=None)
def _mol(smiles: str):
    return Chem.MolFromSmiles(smiles)


def _count(mols, name: str) -> int:
    return sum(len(m.GetSubstructMatches(_S[name])) for m in mols)


def _formula(mol) -> Counter:
    atoms = Counter(a.GetSymbol() for a in mol.GetAtoms())
    atoms["H"] = sum(a.GetTotalNumHs() for a in mol.GetAtoms())
    return atoms


def classify(reactants: list[str], products: list[str]) -> Optional[str]:
    """The rule group for a reaction, or None when no rule fires.

    First match wins, most specific first: a Suzuki also consumes an aryl
    halide and forms an aryl-carbon bond, so it must be claimed before the
    generic couplings are tried.
    """
    rs = [m for m in map(_mol, reactants) if m is not None]
    ps = [m for m in map(_mol, products) if m is not None]
    if not rs or not ps:
        return None
    r = {name: _count(rs, name) for name in _S}
    reactant_carbons = {_formula(m)["C"] for m in rs}

    for p in ps:
        c = {name: _count([p], name) for name in _S}
        #  Halogen added to an existing skeleton. Without this, a coupling
        #  whose chloroaryl partner the record omits looks like a chlorination.
        same_skeleton = _formula(p)["C"] in reactant_carbons

        def lost(name):
            return r[name] > c[name]

        def gained(name):
            return c[name] > r[name]

        if gained("c_B"):
            return "BORYLATION"
        #  Cyanation and carbonylation before the couplings: both also consume
        #  an aryl halide, and their records often carry organometallic reagents.
        if gained("nitrile"):
            return "CYANATION"
        if lost("ar_X") and gained("ar_carbonyl"):
            return "CARBONYLATION"
        if lost("c_B") and (lost("ar_X") or lost("alkyl_LG")):
            return "SUZUKI"
        if lost("c_Sn"):
            return "STILLE"
        if gained("c_F") and same_skeleton:
            return "FLUORINATION"
        if gained("c_hal") and same_skeleton:
            return "HALOGENATION"
        if lost("ar_X") and gained("ar_N_or_O"):
            return "ARYL C-N/C-O"
        if lost("ar_X") and gained("ar_alkene"):
            return "HECK"
        if gained("amide") and r["acid"]:
            return "AMIDE COUPLING"
        if lost("alkyl_LG") and gained("het_sp3C"):
            return "ALKYLATION"

    #  Whole-formula rules: the product is one reactant plus H2 (or minus the
    #  oxygens of a reduced nitro group), or plus exactly one oxygen.
    for p in ps:
        fp = _formula(p)
        for m in rs:
            fr = _formula(m)
            if fp["C"] != fr["C"] or fp["N"] != fr["N"]:
                continue
            heavy_same = all(fp[k] == fr[k] for k in set(fp) | set(fr)
                             if k not in ("H", "O"))
            if not heavy_same:
                continue
            if fp["H"] > fr["H"] and fp["O"] <= fr["O"]:
                return "HYDROGENATION"
            if fp["O"] == fr["O"] + 1 and fp["H"] == fr["H"]:
                return "OXIDATION"
    return None


def main() -> int:
    import csv
    import pathlib
    import sys
    from collections import defaultdict

    root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from backend.molrepr.search import pool

    with (root / "data/external/ord/benchmark_labels.csv").open(encoding="utf-8") as fh:
        human = {row["reaction_id"]: row["reaction_type"] for row in csv.DictReader(fh)}
    with pool().connection() as conn:
        rows = conn.execute(
            "SELECT reaction_id, reactants, products FROM ord_reactions "
            "WHERE reaction_id = ANY(%s)", (list(human),)).fetchall()

    by_human: dict[str, Counter] = defaultdict(Counter)
    by_rule: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        truth = human[row["reaction_id"]]
        rule = classify(list(row["reactants"]), list(row["products"])) or "-"
        by_human[truth][rule] += 1
        by_rule[rule][HUMAN_TO_RULE.get(truth, truth)] += 1

    print(f"{len(rows)} labelled ORD reactions\n")
    print("RECALL: of each human type, the share its own rule group caught")
    for truth, counts in sorted(by_human.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(counts.values())
        want = HUMAN_TO_RULE.get(truth)
        hit = counts[want] / total if want else None
        top = ", ".join(f"{k} {v / total:.0%}" for k, v in counts.most_common(3))
        print(f"  {truth:22s} n={total:6d}  "
              f"{'n/a (no rule)' if hit is None else f'{hit:6.1%}':>13}   [{top}]")

    print("\nPRECISION: of everything a rule fired on, the share whose human "
          "label maps to it")
    for rule, counts in sorted(by_rule.items(), key=lambda kv: -sum(kv[1].values())):
        if rule == "-":
            continue
        total = sum(counts.values())
        top = ", ".join(f"{k} {v / total:.0%}" for k, v in counts.most_common(3))
        print(f"  {rule:16s} n={total:6d}  {counts[rule] / total:6.1%}   [{top}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
