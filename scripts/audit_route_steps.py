"""Step-level correctness of the routes we show: verified, flagged or unverified.

    python scripts/audit_route_steps.py --set hard --out hard.json
    python scripts/audit_route_steps.py --set n1 --sample 100 --out n1.json

Needs the forward-model service on :8435 (backend/forward_model_service, run
in venv-t5). For each target's first route - the one a user sees first - every
step gets one label:

  verified    a direct experimental precedent exists: this exact reaction, on
              these substrates, was run and reported (ORD or a US patent).
  flagged     no direct precedent, and the forward model (ReactionT5), given the
              step's reactants, does not predict the step's product in its top 5.
  unverified  neither: no precedent and no objection. "similar" records whether
              a similar precedent above the badge floor exists.

On PaRoutes targets a verified step may be one of the target's own reference
steps, taken from the patent the target came from. That is real evidence the
reaction works, but it makes n1 unrepresentative of novel targets, so those
steps are counted separately ("from_reference"). The complex-drug set is the
primary baseline.

A forward-model mismatch is a flag, not a verdict: on calibration the model had
precision 0.91 and recall 0.46, so it misses most wrong steps and is sometimes
wrong itself. Flagged steps are the list for a chemist to review.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import statistics
import sys
from multiprocessing import Pool

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import benchmark_routes as bench  # noqa: E402


def steps(tree: dict):
    """(product, reactants) for every reaction in an AiZynthFinder route dict."""
    for reaction in tree.get("children", []):
        yield tree["smiles"], [m["smiles"] for m in reaction.get("children", [])]
        for mol in reaction.get("children", []):
            yield from steps(mol)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", choices=["n1", "hard"], required=True)
    parser.add_argument("--sample", type=int, default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()

    from backend.conditions.normalize import normalize
    from backend.conditions.ord_provider import OrdProvider
    from backend.conditions.service import MIN_SIMILARITY
    from backend.retrosynthesis.validation import MicroserviceLearnedForwardModel

    if args.set == "n1":
        tasks, stock = bench.n1_targets(args.sample or 100, args.seed)
        workers = args.workers or 8
    else:
        tasks, stock = bench.hard_targets(args.sample or 60, args.seed), None
        workers = args.workers or 4
    settings = json.loads(json.dumps(bench.PROFILES["production"]))
    with Pool(workers, initializer=bench._init,
              initargs=(settings, str(stock) if stock else None)) as pool:
        searched = {r["id"]: r for r in pool.imap_unordered(bench._search, tasks)}
    print(f"{len(tasks)} targets searched, {sum(r['solved'] for r in searched.values())} solved",
          flush=True)

    provider, forward = OrdProvider(), MicroserviceLearnedForwardModel()
    routes = []
    for target_id, _, reference in tasks:
        route = searched[target_id]["top1_route"]
        if route is None:
            continue
        reference_keys = set()
        if reference is not None:
            for product, reactants in steps(reference):
                try:
                    reference_keys.add(normalize(reactants, [product]).reaction_key)
                except Exception:
                    pass
        labelled = []
        for product, reactants in steps(route):
            step = {"product": product, "reactants": reactants}
            try:
                reaction = normalize(reactants, [product])
                step["direct"] = len(provider.find_exact_precedents(reaction, limit=5))
                step["similar"] = bool(step["direct"] == 0 and provider.find_similar_precedents(
                    reaction, limit=1, min_similarity=MIN_SIMILARITY))
                step["from_reference"] = reaction.reaction_key in reference_keys
            except Exception as err:            # noqa: BLE001
                step.update(direct=0, similar=False, from_reference=False,
                            error=f"normalize/evidence: {err}")
            result = forward.validate_step(product, reactants)
            step["forward"] = getattr(result.status, "value", str(result.status))
            step["label"] = ("verified" if step["direct"]
                             else "flagged" if step["forward"] == "MISMATCH"
                             else "unverified")
            labelled.append(step)
        routes.append({"id": target_id, "steps": labelled})
        print(f"  {target_id[:30]:30s} " + " ".join(s["label"][0] for s in labelled), flush=True)

    all_steps = [s for r in routes for s in r["steps"]]
    labels = collections.Counter(s["label"] for s in all_steps)
    summary = {
        "set": args.set, "targets": len(tasks), "routes": len(routes),
        "steps": len(all_steps),
        "labels": {k: round(v / len(all_steps), 3) for k, v in labels.items()},
        "label_counts": dict(labels),
        "unverified_with_similar": sum(s["label"] == "unverified" and s["similar"]
                                       for s in all_steps),
        "verified_from_reference": sum(s["label"] == "verified" and s["from_reference"]
                                       for s in all_steps),
        "forward_status": dict(collections.Counter(s["forward"] for s in all_steps)),
        #  The forward model rejecting a reaction that was demonstrably run:
        #  a direct estimate of how often its flags are false alarms.
        "forward_mismatch_on_verified": sum(s["label"] == "verified"
                                            and s["forward"] == "MISMATCH"
                                            for s in all_steps),
        "route_verified_share_mean": round(statistics.mean(
            sum(s["label"] == "verified" for s in r["steps"]) / len(r["steps"])
            for r in routes), 3) if routes else None,
        "routes_fully_verified": sum(all(s["label"] == "verified" for s in r["steps"])
                                     for r in routes),
        "routes_with_a_flag": sum(any(s["label"] == "flagged" for s in r["steps"])
                                  for r in routes),
    }
    print(json.dumps(summary, indent=2))
    args.out.write_text(json.dumps({"summary": summary, "routes": routes}, indent=1),
                        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
