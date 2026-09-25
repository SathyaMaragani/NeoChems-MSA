"""Why does a target fail? Stock, model, or search budget.

    python scripts/diagnose_unsolved.py                 # the 60 hard-set drugs
    python scripts/diagnose_unsolved.py --iterations 500

For every target the production search does not solve, classify the failure
from what the search reached:

  no-disconnection  the expansion model proposes no feasible reaction for the
                    target itself (no template applies, or the filter rejects
                    every one). A model problem: nothing downstream can help.
  stuck-at-core     the best route found still has a leaf holding at least
                    half the target's heavy atoms. The key disconnections are
                    missing - a model problem (parity plan Phase 1c).
  stock-limited     every leaf the best route could not buy is small
                    (<= 15 heavy atoms), the size of a catalogue building
                    block. A bigger stock may solve it (Phase 1b).
  intermediate      missing leaves between those sizes; either could apply.
  known-compound    the largest missing leaf is itself an approved drug
                    (ChEMBL), e.g. codeine for dihydrocodeine. The model found
                    the disconnection; the stock lacks a natural-product or API
                    starting material. A stock problem that looks like a model
                    one, so it is checked before the size rules.

"Best route" is the unsolved route with the fewest heavy atoms left unbought,
i.e. the one closest to purchasable. The classes are heuristics on sizes, not
chemistry; the per-target record keeps the numbers behind each call.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from multiprocessing import Pool

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import benchmark_routes as bench  # noqa: E402

SMALL = 15          # heavy atoms: catalogue building-block size
CORE_SHARE = 0.5    # a leaf this large a share of the target is "the core"


def _diagnose(task: tuple[str, str, None]) -> dict:
    from aizynthfinder.chem import TreeMolecule
    from aizynthfinder.utils.exceptions import RejectionException

    name, smiles, _ = task
    finder = bench._finder
    target = TreeMolecule(parent=None, smiles=smiles)
    actions, _ = finder.expansion_policy.get_actions([target])
    applicable = [a for a in actions if a.reactants and a.reactants[0]]
    feasible = 0
    for action in applicable:
        try:
            if finder.filter_policy.selection:
                finder.filter_policy(action)
            feasible += 1
        except RejectionException:
            pass

    finder.target_smiles = smiles
    finder.tree_search()
    finder.build_routes()
    solved = bool(finder.extract_statistics()["is_solved"])
    target_ha = target.rd_mol.GetNumHeavyAtoms()

    record = {"id": name, "solved": solved, "target_heavy_atoms": target_ha,
              "root_templates": len(applicable), "root_feasible": feasible}
    if solved:
        return record

    best = None
    for tree in finder.routes.reaction_trees:
        missing = [(leaf.rd_mol.GetNumHeavyAtoms(), leaf.smiles)
                   for leaf in tree.leafs() if not tree.in_stock(leaf)]
        if missing and (best is None or sum(m[0] for m in missing)
                        < sum(m[0] for m in best)):
            best = missing
    best = sorted(best or [(target_ha, smiles)], reverse=True)
    sizes = [size for size, _ in best]
    record.update(missing_leaves=len(best), largest_missing=max(sizes),
                  unbought_heavy_atoms=sum(sizes),
                  missing_smiles=[s for _, s in best])
    best = sizes
    if feasible == 0:
        record["class"] = "no-disconnection"
    elif max(best) >= CORE_SHARE * target_ha:
        record["class"] = "stuck-at-core"
    elif max(best) <= SMALL:
        record["class"] = "stock-limited"
    else:
        record["class"] = "intermediate"
    return record


def _approved_drug_keys() -> dict[str, str]:
    """InChIKey -> name for ChEMBL approved drugs, parent structure only."""
    import csv
    from rdkit import Chem

    keys: dict[str, str] = {}
    with bench.CHEMBL.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="	"):
            mol = Chem.MolFromSmiles(row["smiles"] or "")
            if mol is None:
                continue
            mol = max(Chem.GetMolFrags(mol, asMols=True), key=lambda m: m.GetNumHeavyAtoms())
            keys.setdefault(Chem.MolToInchiKey(mol), row["pref_name"] or row["chembl_id"])
    return keys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", type=int, default=60)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--iterations", type=int, default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()

    settings = json.loads(json.dumps(bench.PROFILES["production"]))
    if args.iterations:
        settings["search"]["iteration_limit"] = args.iterations
    tasks = bench.hard_targets(args.sample, args.seed)
    with Pool(args.workers, initializer=bench._init, initargs=(settings, None)) as pool:
        records = list(pool.imap_unordered(_diagnose, tasks))

    unsolved = [r for r in records if not r["solved"]]
    #  A missing leaf that is itself an approved drug is an unstocked starting
    #  material (codeine, 6-APA), not a missing disconnection.
    drugs = _approved_drug_keys()
    from rdkit import Chem
    for r in unsolved:
        key = Chem.MolToInchiKey(Chem.MolFromSmiles(r["missing_smiles"][0]))
        if key in drugs:
            r["class"] = "known-compound"
            r["largest_missing_is"] = drugs[key]
    classes = collections.Counter(r["class"] for r in unsolved)
    print(f"{len(records) - len(unsolved)} of {len(records)} solved; "
          f"{len(unsolved)} unsolved: {dict(classes)}")
    for r in sorted(unsolved, key=lambda r: r["class"]):
        print(f"  {r['class']:17s} {r['id'][:34]:34s} target {r['target_heavy_atoms']:2d} HA  "
              f"root {r['root_feasible']:2d}/{r['root_templates']:2d} feasible  "
              f"missing {r['missing_leaves']} leaves, largest {r['largest_missing']} HA  "
              f"{r.get('largest_missing_is') or r['missing_smiles'][0]}")
    args.out.write_text(json.dumps({"settings": settings, "classes": dict(classes),
                                    "targets": records}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
