"""Route benchmark: does the planner find routes, and are they the known ones?

    python scripts/benchmark_routes.py --set n1 --profile production --sample 200
    python scripts/benchmark_routes.py --set n1 --profile paroutes --sample 200
    python scripts/benchmark_routes.py --set hard --profile production --evidence

TARGET SETS
  n1    PaRoutes 2.0 set-n1 (Genheden & Bjerrum 2022, CC-BY-4.0): 10,000 targets
        from USPTO patents, each with the patent's own route as reference. A
        seeded sample. Stock = PaRoutes' n1 stock, so every reference route is
        solvable. Needs data/external/paroutes/ (see docs/route-benchmark.md).
  hard  complex approved drugs from local ChEMBL (2-5 stereocentres, 3+ rings,
        20-45 heavy atoms, no ring over 8 atoms), a seeded sample. Stock = ZINC,
        as served. No reference routes, so no accuracy - solve rate, steps, time.

SEARCH PROFILES
  production  what the API does: config.yml's search section (depth 10 since
              25 Sep 2026; the recorded baseline ran at 6), 100 iterations,
              120 s, filter policy on, up to 25 routes built.
  paroutes    PaRoutes' published MCTS config (their
              publication/aizynthfinder_config_mcts_n1.yml): 500 iterations,
              3600 s, depth 10, no filter, all solved routes. Reproducing their
              numbers (top-1/5/10 0.24/0.51/0.54 on all 10k) checks the harness.

NO MODEL LEAKAGE. The served expansion model is PaRoutes 2.0's own, trained on
USPTO with the n1/n5 reference reactions removed: its template file is
byte-identical to their uspto_unique_templates.csv.gz (md5 e8fb29d4...).

ACCURACY exactly as PaRoutes' analysis/route_quality.py: rank every extracted
route by the Badowski score, and the reference is "found at rank k" when a
route at rank <= k has tree edit distance 0 to it (molecules and reactions).
The same is also reported in production order - solved routes only, by state
score - which is what the API actually returns.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import pathlib
import random
import statistics
import sys
import time
from multiprocessing import Pool
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PAROUTES = ROOT / "data/external/paroutes"
CHEMBL = ROOT / "data/external/chembl/chembl_approved.tsv"
TOP_KS = (1, 5, 10)

PROFILES = {
    #  Depth comes from config.yml, so this profile cannot drift from what the
    #  API serves.
    "production": {"search": {"iteration_limit": 100, "time_limit": 120},
                   "filter": True},
    "paroutes": {"search": {"iteration_limit": 500, "time_limit": 3600,
                            "max_transforms": 10},
                 "post_processing": {"all_routes": True},
                 "filter": False},
}

ORGANIC = {"C", "N", "O", "S", "P", "F", "Cl", "Br", "I", "B"}


# ------------------------------------------------------------- targets ----


def n1_targets(sample: int, seed: int) -> tuple[list[tuple[str, str, dict]], pathlib.Path]:
    """(id, smiles, reference route) for a seeded sample, and the stock file."""
    from rdkit import Chem

    stock = PAROUTES / "stock_n1_inchikeys.txt"
    if not stock.exists():
        #  AiZynthFinder reads a .txt stock as InChI keys. Derived from the v2
        #  SMILES stock, not PaRoutes' published key file, which is v1's.
        smiles = (PAROUTES / "stock_n1.txt").read_text().split()
        stock.write_text("\n".join(Chem.MolToInchiKey(Chem.MolFromSmiles(s))
                                   for s in smiles) + "\n")
    targets = (PAROUTES / "targets_n1.txt").read_text().split()
    refs = json.loads((PAROUTES / "ref_routes_n1.json").read_text())
    picked = sorted(random.Random(seed).sample(range(len(targets)), sample))
    return [(f"n1-{i}", targets[i], refs[i]) for i in picked], stock


def hard_targets(sample: int, seed: int) -> list[tuple[str, str, None]]:
    """Complex approved drugs, chosen by fixed criteria rather than by hand."""
    from rdkit import Chem

    eligible: dict[str, str] = {}
    with CHEMBL.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            mol = Chem.MolFromSmiles(row["smiles"] or "")
            if mol is None:
                continue
            mol = max(Chem.GetMolFrags(mol, asMols=True), key=lambda m: m.GetNumHeavyAtoms())
            rings = mol.GetRingInfo().AtomRings()
            if (all(a.GetSymbol() in ORGANIC for a in mol.GetAtoms())
                    and 20 <= mol.GetNumHeavyAtoms() <= 45
                    and 2 <= len(Chem.FindMolChiralCenters(
                        mol, includeUnassigned=True, useLegacyImplementation=False)) <= 5
                    and len(rings) >= 3 and max(map(len, rings)) <= 8):
                #  Keyed on structure: two salts of one drug (biperiden HCl and
                #  lactate) are one target once the counter-ion is stripped.
                name, smiles = row["pref_name"] or row["chembl_id"], Chem.MolToSmiles(mol)
                eligible[smiles] = min(name, eligible.get(smiles, name))
    pool = sorted((name, smiles) for smiles, name in eligible.items())
    picked = random.Random(seed).sample(pool, min(sample, len(pool)))
    print(f"hard set: {len(picked)} of {len(eligible)} eligible approved drugs")
    return [(name, smiles, None) for name, smiles in picked]


# -------------------------------------------------------------- worker ----

_finder = None
_profile: dict = {}


def _init(settings: dict, stock: Optional[str]) -> None:
    """One AiZynthFinder per worker process, loaded once."""
    global _finder, _profile
    from aizynthfinder.aizynthfinder import AiZynthFinder
    from backend.retrosynthesis.service import DEFAULT_CONFIG, load_config

    _profile = settings
    config = load_config(DEFAULT_CONFIG)
    if stock:
        config["stock"] = {"bench": stock}
    config["search"] = {**config.get("search", {}), **_profile["search"]}
    config["post_processing"] = dict(_profile.get("post_processing", {}))
    _finder = AiZynthFinder(configdict=config)
    _finder.stock.select("bench" if stock else "zinc")
    _finder.expansion_policy.select(_profile.get("expansion", ["uspto"]))
    if _profile["filter"]:
        _finder.filter_policy.select("uspto")
    else:
        _finder.filter_policy.deselect()


def _steps(tree: dict) -> int:
    return sum(1 + sum(_steps(m) for m in r.get("children", []))
               for r in tree.get("children", []))


def _longest_linear(tree: dict) -> int:
    return max((1 + max((_longest_linear(m) for m in r.get("children", [])), default=0)
                for r in tree.get("children", [])), default=0)


def _found_at(routes: list[dict], order: list[int], ranks: list[int],
              reference: dict) -> Optional[int]:
    """Rank of the first route at tree edit distance 0 to the reference."""
    from rxnutils.routes.base import SynthesisRoute
    from rxnutils.routes.ted.reactiontree import ReactionTreeWrapper

    ref = ReactionTreeWrapper(SynthesisRoute(reference), content="both")
    for rank, idx in zip(ranks, order):
        if rank > max(TOP_KS):
            break
        try:
            other = ReactionTreeWrapper(SynthesisRoute(routes[idx]), content="both")
        except ValueError:
            continue
        if ref.distance_to(other) < 1e-6:
            return rank
    return None


def _search(task: tuple[str, str, Optional[dict]]) -> dict:
    from rxnutils.routes.base import SynthesisRoute
    from rxnutils.routes.scoring import badowski_route_score, route_ranks

    target_id, smiles, reference = task
    _finder.target_smiles = smiles
    _finder.tree_search()
    _finder.build_routes()
    stats = _finder.extract_statistics()
    routes = _finder.routes.dicts
    solved = [i for i, tree in enumerate(_finder.routes.reaction_trees) if tree.is_solved]

    record = {
        "id": target_id, "smiles": smiles,
        "solved": bool(stats["is_solved"]),
        "routes": len(routes), "solved_routes": len(solved),
        "search_time": round(float(stats["search_time"]), 2),
        "first_solution_time": round(float(stats["first_solution_time"]), 2),
        "iterations": int(_finder.search_stats["iterations"]),
        "top1_steps": _steps(routes[solved[0]]) if solved else None,
        "min_steps": min(_steps(routes[i]) for i in solved) if solved else None,
        "top1_route": routes[solved[0]] if solved else None,
    }
    if reference is not None:
        record["ref_steps"] = _steps(reference)
        record["ref_longest_linear"] = _longest_linear(reference)
        scores = [badowski_route_score(SynthesisRoute(r)) for r in routes]
        order = sorted(range(len(routes)), key=scores.__getitem__)
        record["rank_paroutes"] = _found_at(
            routes, order, route_ranks([scores[i] for i in order]), reference)
        record["rank_production"] = _found_at(
            routes, solved, list(range(1, len(solved) + 1)), reference)
    return record


# ------------------------------------------------------------- summary ----


def wilson(hits: int, n: int) -> list[float]:
    """95% Wilson interval: a 200-target sample is not the 10,000."""
    if not n:
        return [0.0, 0.0]
    p, z = hits / n, 1.96
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(centre - half, 3), round(centre + half, 3)]


def rate(records: list[dict], test) -> dict:
    hits = sum(1 for r in records if test(r))
    return {"value": round(hits / len(records), 3) if records else None,
            "ci95": wilson(hits, len(records)), "n": len(records)}


def median(values) -> Optional[float]:
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else None


def summarise(records: list[dict]) -> dict:
    solved = [r for r in records if r["solved"]]
    summary = {
        "targets": len(records),
        "solved": rate(records, lambda r: r["solved"]),
        "median_search_time_s": median(r["search_time"] for r in records),
        "median_first_solution_time_s": median(r["first_solution_time"] for r in solved),
        "median_top1_steps": median(r["top1_steps"] for r in solved),
        "median_min_steps": median(r["min_steps"] for r in solved),
    }
    if records and "rank_paroutes" in records[0]:
        for order in ("paroutes", "production"):
            summary[f"top_k_{order}_ranking"] = {
                f"top-{k}": rate(records, lambda r, k=k, o=order:
                                 r[f"rank_{o}"] is not None and r[f"rank_{o}"] <= k)
                for k in TOP_KS}
        #  Longer reference routes are the harder targets; this is where a
        #  better engine should show.
        by_length: dict[str, list] = {}
        for r in records:
            by_length.setdefault(str(min(r["ref_longest_linear"], 5)), []).append(r)
        summary["by_reference_length"] = {
            ("5+" if k == "5" else k): {
                "targets": len(v),
                "solved": rate(v, lambda r: r["solved"])["value"],
                "top-5": rate(v, lambda r: r["rank_paroutes"] is not None
                              and r["rank_paroutes"] <= 5)["value"]}
            for k, v in sorted(by_length.items())}
    if records and "evidence_coverage" in records[0]:
        covered = [r for r in solved if r.get("evidence_coverage") is not None]
        for key in ("evidence_coverage", "direct_share"):
            summary[f"top1_route_{key}_mean"] = (
                round(statistics.mean(r[key] for r in covered), 3) if covered else None)
    return summary


def add_evidence(records: list[dict]) -> None:
    """Share of the top route's steps with precedent: any (direct or similar),
    and direct only. With 2M indexed reactions and a 0.20 floor nearly every
    step finds a "similar" one, so the direct share is the informative number."""
    from backend.conditions.service import ConditionsService
    from backend.retrosynthesis.service import (_enrich_with_evidence,
                                                _evidence_summary, _molecule_node)

    service = ConditionsService()
    for r in records:
        if r["top1_route"] is None:
            r["evidence_coverage"] = r["direct_share"] = None
            continue
        tree = _enrich_with_evidence(_molecule_node(r["top1_route"]), service)
        evidence = _evidence_summary(tree)
        r["evidence_coverage"] = evidence["evidence_coverage"]
        r["direct_share"] = (round(evidence["steps_with_experimental_evidence"]
                                   / evidence["steps"], 3) if evidence["steps"] else None)


# ---------------------------------------------------------------- main ----


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", choices=["n1", "hard"], required=True)
    parser.add_argument("--profile", choices=list(PROFILES), default="production")
    #  One-at-a-time overrides on top of the profile, for ablations.
    parser.add_argument("--iterations", type=int, default=None)
    parser.add_argument("--depth", type=int, default=None, help="max_transforms")
    parser.add_argument("--time-limit", type=int, default=None)
    parser.add_argument("--filter", choices=["on", "off"], default=None)
    parser.add_argument("--ringbreaker", action="store_true",
                        help="expand with the ringbreaker policy beside uspto")
    parser.add_argument("--sample", type=int, default=None,
                        help="targets to run (default: 200 for n1, 60 for hard)")
    parser.add_argument("--seed", type=int, default=0)
    #  AiZynthFinder gives each ONNX session logical/physical threads (2 here),
    #  so 8 workers fill 16 logical cores without oversubscribing - which
    #  matters, because production's 120 s limit would otherwise cut searches
    #  short and the benchmark would measure the harness.
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--evidence", action="store_true",
                        help="also measure precedent coverage of each top route")
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args()

    if args.set == "n1":
        tasks, stock = n1_targets(args.sample or 200, args.seed)
    else:
        tasks, stock = hard_targets(args.sample or 60, args.seed), None
    settings = json.loads(json.dumps(PROFILES[args.profile]))
    for key, value in (("iteration_limit", args.iterations), ("max_transforms", args.depth),
                       ("time_limit", args.time_limit)):
        if value is not None:
            settings["search"][key] = value
    if args.filter:
        settings["filter"] = args.filter == "on"
    if args.ringbreaker:
        settings["expansion"] = ["uspto", "ringbreaker"]
    print(f"{len(tasks)} targets, {args.workers} workers, settings {settings}")

    started = time.time()
    records: list[dict] = []
    with Pool(args.workers, initializer=_init,
              initargs=(settings, str(stock) if stock else None)) as pool:
        for record in pool.imap_unordered(_search, tasks):
            records.append(record)
            if len(records) % 10 == 0 or len(records) == len(tasks):
                done = sum(r["solved"] for r in records)
                print(f"  {len(records)}/{len(tasks)}  solved {done}  "
                      f"{time.time() - started:.0f}s", flush=True)
    position = {task[0]: i for i, task in enumerate(tasks)}
    records.sort(key=lambda r: position[r["id"]])

    if args.evidence:
        add_evidence(records)
    summary = summarise(records)
    print(json.dumps(summary, indent=2))

    out = args.out or ROOT / f"docs/route-benchmark-{args.set}-{args.profile}.json"
    for r in records:
        r.pop("top1_route", None)
        #  Ids only in the committed file: hard-set SMILES are ChEMBL data
        #  (CC-BY-SA, ShareAlike). Seeded selection makes the ids reproducible.
        r.pop("smiles", None)
    out.write_text(json.dumps({
        "set": args.set, "profile": args.profile, "seed": args.seed,
        "settings": settings, "wall_time_s": round(time.time() - started),
        "summary": summary, "targets": records}, indent=1), encoding="utf-8")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
