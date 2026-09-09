"""Standalone sanity check: run AiZynthFinder programmatically on 3 known drugs.

Run:  python scripts/test_retrosynthesis.py
Must produce plausible routes before anything gets built on top of it.
"""
import sys
import time
from pathlib import Path

from aizynthfinder.aizynthfinder import AiZynthFinder

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.yml"
sys.path.insert(0, str(ROOT))

from backend.retrosynthesis.service import load_config  # noqa: E402

# Canonical SMILES verified against PubChem (CID 2244 / 3672 / 1983), not guessed.
MOLECULES = {
    "aspirin": "CC(=O)Oc1ccccc1C(=O)O",
    "ibuprofen": "CC(C)Cc1ccc(C(C)C(=O)O)cc1",
    "paracetamol": "CC(=O)Nc1ccc(O)cc1",
}


def walk(node, depth=0):
    """Yield (depth, reaction_smiles, reactants, template_code, policy_prob) per reaction."""
    for rxn in node.get("children", []):
        reactants = [c["smiles"] for c in rxn.get("children", [])]
        meta = rxn.get("metadata", {})
        yield depth, rxn.get("smiles", ""), reactants, meta
        for mol in rxn.get("children", []):
            yield from walk(mol, depth + 1)


def main():
    if not CONFIG.exists():
        sys.exit(f"missing {CONFIG} - run download_public_data first")

    t0 = time.time()
    finder = AiZynthFinder(configdict=load_config(CONFIG))
    finder.stock.select("zinc")
    finder.expansion_policy.select("uspto")
    finder.filter_policy.select("uspto")
    print(f"model + stock loaded in {time.time() - t0:.1f}s\n")

    problems = []
    for name, smiles in MOLECULES.items():
        print("=" * 70)
        print(f"{name}  {smiles}")
        print("=" * 70)

        finder.target_smiles = smiles
        finder.tree_search()
        finder.build_routes()
        stats = finder.extract_statistics()

        print(f"  solved: {stats['is_solved']}  "
              f"routes found: {len(finder.routes)}  "
              f"solved routes: {stats['number_of_solved_routes']}")
        print(f"  search: {stats['search_time']:.1f}s over {finder.search_stats['iterations']} iterations, "
              f"first solution at iteration {stats['first_solution_iteration']}")

        if not len(finder.routes):
            problems.append(f"{name}: no routes at all")
            continue

        finder.routes.dicts  # materialises top["dict"]
        top = finder.routes[0]
        scores = finder.routes.scores[0]
        tree = top["reaction_tree"]
        n_rxn = len(list(tree.reactions()))

        print(f"\n  --- top route ---  scores: "
              + ", ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}"
                          for k, v in scores.items()))

        if n_rxn == 0:
            problems.append(f"{name}: DEGENERATE - top route has 0 reactions "
                            f"(target itself is in stock)")
            print("  !! 0 reactions - target is directly purchasable")
        else:
            for depth, rxn_smiles, reactants, meta in walk(top["dict"]):
                pad = "    " + "  " * depth
                prob = meta.get("policy_probability")
                tmpl = meta.get("template_code", meta.get("template", "?"))
                cls = meta.get("classification", "")
                prob_s = f"{prob:.4f}" if isinstance(prob, float) else str(prob)
                print(f"{pad}{rxn_smiles}")
                print(f"{pad}  reactants: {reactants}")
                print(f"{pad}  template={tmpl}  policy_prob={prob_s}")
                if cls:
                    print(f"{pad}  class: {cls}")

            leaves = [(m.smiles, tree.in_stock(m)) for m in tree.leafs()]
            print(f"\n  leaves ({len(leaves)}):")
            for smi, in_stock in leaves:
                print(f"    {'IN STOCK ' if in_stock else 'NOT STOCK'}  {smi}")
            if not all(s for _, s in leaves):
                problems.append(f"{name}: top route has leaves not in stock")
        print()

    print("=" * 70)
    if problems:
        print("REVIEW NEEDED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("All 3 molecules produced non-trivial, fully-in-stock top routes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
