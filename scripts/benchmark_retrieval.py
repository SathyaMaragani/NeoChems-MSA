"""Retrieval benchmark for the reaction-evidence layer.

Answers two questions:

  1. When the system says "similar experimental precedent", is the reaction it
     found chemically relevant, or merely fingerprint-near?
  2. Is retrieval better keyed on the TRANSFORMATION (V2.2) than on the PRODUCT
     molecule (V2.1)?

    conda activate ord-ingest && python scripts/extract_benchmark_labels.py
    conda activate retrosynth  && python scripts/build_reaction_index.py
    python scripts/benchmark_retrieval.py --split validation   # tune here
    python scripts/benchmark_retrieval.py --split test         # report here, once

GROUND TRUTH is the `REACTION_TYPE` identifier ORD contributors wrote on their
own reactions (SUZUKI, BUCHWALD, MITSUNOBU, …). It was never derived from a
fingerprint, so grading retrieval against it is not circular.

LEAKAGE CONTROL is why these numbers are lower than the first V2.1 run's, and
why they are the ones to trust. Every ORD campaign - a screen, a plate, a
notebook - contains exactly ONE reaction type. Retrieving a reaction's own
plate-mate is therefore automatically "correct" while proving nothing: no real
query arrives with its own plate already in the index. This harness

  * excludes same-campaign candidates from every query's candidate pool,
  * splits campaigns disjointly into validation and test,
  * keeps only reaction types spread across several campaigns, since a type
    confined to one campaign has no cross-campaign precedent to find,

and reports the uncontrolled numbers alongside, so the size of the leakage is
measured rather than assumed.

TUNE ON VALIDATION, REPORT ON TEST. The split is a hash of the campaign id, so
it means the same thing between runs, machines and sample sizes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import pathlib
import random
import statistics
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from backend.conditions import retrieval  # noqa: E402
from backend.conditions.normalize import normalize  # noqa: E402
from backend.conditions.ord_provider import TRANSFORMATION_WEIGHT  # noqa: E402
from backend.conditions.service import MIN_SIMILARITY  # noqa: E402
from backend.molrepr.search import pool  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LABELS = ROOT / "data/external/ord/benchmark_labels.csv"
RESULTS = ROOT / "docs/retrieval-benchmark-results.json"

#: A type needs candidates in campaigns other than the query's, so one confined
#: to a couple of campaigns cannot be graded cross-campaign at all.
MIN_CAMPAIGNS_PER_TYPE = 4
TOP_K = 10
STRATEGY_ORDER = ("product", "transformation", "substrate", "hybrid")


# ------------------------------------------------------------------ data ----


def load_labels() -> dict[str, dict]:
    if not LABELS.exists():
        raise SystemExit(
            f"{LABELS} not found - run scripts/extract_benchmark_labels.py "
            "in the ord-ingest env first."
        )
    with LABELS.open(encoding="utf-8") as handle:
        return {row["reaction_id"]: row for row in csv.DictReader(handle)}


def split_of(campaign_id: str) -> str:
    """Deterministic campaign -> split; a hash rather than a shuffle so the
    assignment is stable across runs, machines and sample sizes."""
    digest = hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()
    return "validation" if int(digest[:8], 16) % 2 == 0 else "test"


# --------------------------------------------------------------- metrics ----


def _dcg(hits: list[bool]) -> float:
    return sum(1.0 / math.log2(i + 2) for i, hit in enumerate(hits) if hit)


def _ndcg(hits: list[bool], relevant_available: int) -> float:
    ideal = _dcg([True] * min(TOP_K, relevant_available))
    return (_dcg(hits[:TOP_K]) / ideal) if ideal else 0.0


def score_query(ranked_ids: list[str], truth: str, labels: dict,
                relevant_available: int, candidates: int,
                candidate_recall: float) -> dict:
    hits = [labels[rid]["reaction_type"] == truth for rid in ranked_ids]
    top = hits[:TOP_K]
    first = next((i for i, hit in enumerate(hits) if hit), None)
    return {
        "returned": len(ranked_ids),
        "candidates": candidates,
        "candidate_recall": candidate_recall,
        "had_relevant": relevant_available > 0,
        "p1": float(hits[0]) if hits else 0.0,
        "p5": (sum(hits[:5]) / 5) if hits else 0.0,
        "p10": (sum(top) / TOP_K) if hits else 0.0,
        "r10": (sum(top) / min(TOP_K, relevant_available)) if relevant_available else 0.0,
        "mrr": (1.0 / (first + 1)) if first is not None else 0.0,
        "ndcg10": _ndcg(hits, relevant_available),
    }


def aggregate(rows: list[dict]) -> dict:
    def mean(key, subset=None):
        values = [r[key] for r in (rows if subset is None else subset)]
        return round(statistics.mean(values), 4) if values else 0.0

    reachable = [r for r in rows if r["had_relevant"]]
    answered = [r for r in rows if r["returned"] > 0]
    return {
        "queries": len(rows),
        "precision_at_1": mean("p1"),
        "precision_at_5": mean("p5"),
        "precision_at_10": mean("p10"),
        "recall_at_10": mean("r10"),
        "mrr": mean("mrr"),
        "ndcg_at_10": mean("ndcg10"),
        #  Separates "the ranker put the wrong thing first" from "stage 1 never
        #  offered a right thing to put first" - two failures one P@1 hides.
        "precision_at_1_when_relevant_available": mean("p1", reachable),
        "queries_offered_any_relevant": round(len(reachable) / len(rows), 4) if rows else 0.0,
        #  Of every relevant reaction that EXISTS outside the query's campaign,
        #  what share did stage 1 actually hand over. The ceiling on recall.
        "candidate_recall": mean("candidate_recall"),
        "silent_rate": round(1 - len(answered) / len(rows), 4) if rows else 0.0,
        "mean_candidates": mean("candidates"),
    }


# ------------------------------------------------------------ evaluation ----


def evaluate(strategy_name: str, queries: list[dict], labels: dict,
             reachable_totals: dict, *, exclude_campaign: bool,
             transformation_weight: float, floor: float,
             thresholds: dict, limit: int) -> tuple[dict, list[float]]:
    strategy = retrieval.STRATEGIES[strategy_name]
    rows: list[dict] = []
    latencies: list[float] = []

    for query in queries:
        #  Leakage control is a SQL predicate the strategies know nothing about,
        #  so none of them can "help" by seeing the split.
        extra_sql, extra_params = "", ()
        if exclude_campaign:
            extra_sql = " AND (i.campaign_id IS NULL OR i.campaign_id <> %s)"
            extra_params = (query["campaign"],)

        started = time.perf_counter()
        try:
            with pool().connection() as conn:
                candidates = strategy(
                    conn, query["reaction"],
                    threshold=thresholds[strategy_name],
                    limit=limit, extra_sql=extra_sql, extra_params=extra_params)
        except Exception as err:                       # noqa: BLE001
            print(f"    ! {strategy_name} failed on {query['id'][:22]}: {err}")
            candidates = []
        latencies.append((time.perf_counter() - started) * 1000)

        judged = [c for c in candidates if c.reaction_id in labels]
        truth = query["type"]
        relevant_available = sum(
            1 for c in judged if labels[c.reaction_id]["reaction_type"] == truth)
        total_reachable = reachable_totals[truth] - (
            query["campaign_size"] if exclude_campaign else 1)
        recall = (relevant_available / total_reachable) if total_reachable > 0 else 0.0

        scored = sorted(((c.combined(transformation_weight), c) for c in judged),
                        key=lambda pair: -pair[0])
        ranked = [c.reaction_id for value, c in scored if value >= floor]
        rows.append(score_query(ranked, truth, labels, relevant_available,
                                len(judged), min(1.0, recall)))

    return aggregate(rows), latencies


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["validation", "test"], default="validation")
    parser.add_argument("--queries", type=int, default=400)
    parser.add_argument("--per-type", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    #  No single --threshold: the keys are on different similarity scales, and
    #  sharing one is exactly the mistake that made transformation retrieval
    #  return zero candidates. Each strategy uses its own default unless
    #  overridden as name=value, e.g. --threshold transformation=0.5
    parser.add_argument("--threshold", action="append", default=[],
                        metavar="STRATEGY=VALUE")
    parser.add_argument("--limit", type=int, default=retrieval.DEFAULT_LIMIT)
    parser.add_argument("--weight", type=float, default=TRANSFORMATION_WEIGHT)
    parser.add_argument("--floor", type=float, default=MIN_SIMILARITY)
    parser.add_argument("--strategies", default=",".join(STRATEGY_ORDER))
    args = parser.parse_args()

    #  Per-strategy thresholds; hybrid is keyed on the transformation branch.
    thresholds = dict(retrieval.DEFAULT_THRESHOLDS)
    thresholds["hybrid"] = thresholds["transformation"]
    for override in args.threshold:
        name, _, value = override.partition("=")
        if name not in thresholds:
            raise SystemExit(f"unknown strategy in --threshold: {name}")
        thresholds[name] = float(value)
    print(f"thresholds: {thresholds}")

    labels = load_labels()
    with pool().connection() as conn:
        indexed = conn.execute(
            """SELECT i.reaction_id, i.campaign_id, r.reactants, r.products
               FROM ord_reaction_index i JOIN ord_reactions r USING (reaction_id)
               WHERE i.reaction_id = ANY(%s)""",
            (list(labels),),
        ).fetchall()
    rows_by_id = {r["reaction_id"]: r for r in indexed}
    print(f"{len(labels)} labelled reactions; {len(rows_by_id)} present in the index")
    if not rows_by_id:
        raise SystemExit("index is empty - run scripts/build_reaction_index.py")

    population = Counter(labels[rid]["reaction_type"] for rid in rows_by_id)
    campaign_counts = Counter(labels[rid]["campaign_id"] for rid in rows_by_id)
    campaigns_per_type: dict[str, set] = defaultdict(set)
    for rid in rows_by_id:
        campaigns_per_type[labels[rid]["reaction_type"]].add(labels[rid]["campaign_id"])
    eligible = {t for t, c in campaigns_per_type.items()
                if len(c) >= MIN_CAMPAIGNS_PER_TYPE}
    print(f"{len(eligible)} of {len(population)} types span >= "
          f"{MIN_CAMPAIGNS_PER_TYPE} campaigns and can be graded cross-campaign")

    #  Queries come from one split; candidates may come from anywhere, because a
    #  real query is answered against the whole index. What must not leak is the
    #  campaign, and that is excluded per query.
    pools: dict[str, list[str]] = defaultdict(list)
    for rid in rows_by_id:
        label = labels[rid]
        if (label["reaction_type"] in eligible
                and split_of(label["campaign_id"]) == args.split):
            pools[label["reaction_type"]].append(rid)

    rng = random.Random(args.seed)
    chosen: list[str] = []
    for reaction_type in sorted(pools):
        ids = sorted(pools[reaction_type])
        rng.shuffle(ids)
        chosen.extend(ids[: args.per_type])
    rng.shuffle(chosen)
    chosen = chosen[: args.queries]

    queries = []
    for rid in chosen:
        row = rows_by_id[rid]
        try:
            reaction = normalize(list(row["reactants"]), list(row["products"]))
        except Exception:                              # noqa: BLE001
            continue
        queries.append({
            "id": rid, "reaction": reaction,
            "type": labels[rid]["reaction_type"],
            "campaign": labels[rid]["campaign_id"],
            "campaign_size": campaign_counts[labels[rid]["campaign_id"]],
        })
    print(f"split={args.split}: {len(queries)} queries, "
          f"{len({q['type'] for q in queries})} types, "
          f"{len({q['campaign'] for q in queries})} campaigns\n")

    report = {
        "split": args.split,
        "queries": len(queries),
        "seed": args.seed,
        "settings": {
            "thresholds": thresholds, "limit": args.limit,
            "transformation_weight": args.weight, "floor": args.floor,
            "min_campaigns_per_type": MIN_CAMPAIGNS_PER_TYPE,
        },
        "leakage_controlled": {},
        "uncontrolled_same_campaign_allowed": {},
        "latency_ms": {},
    }
    wanted = [s for s in STRATEGY_ORDER if s in args.strategies.split(",")]

    for exclude in (True, False):
        section = ("leakage_controlled" if exclude
                   else "uncontrolled_same_campaign_allowed")
        print("LEAKAGE-CONTROLLED (same-campaign candidates excluded)" if exclude
              else "UNCONTROLLED (same-campaign allowed - the V2.1 measurement)")
        print(f"{'strategy':<15}{'P@1':>7}{'P@5':>7}{'P@10':>7}{'R@10':>7}{'MRR':>7}"
              f"{'nDCG':>7}{'P@1|rel':>9}{'any-rel':>9}{'cand-rec':>10}"
              f"{'silent':>8}{'cands':>7}")
        for name in wanted:
            metrics, latencies = evaluate(
                name, queries, labels, population,
                exclude_campaign=exclude, transformation_weight=args.weight,
                floor=args.floor, thresholds=thresholds, limit=args.limit)
            report[section][name] = metrics
            if exclude:
                report["latency_ms"][name] = {
                    "p50": round(statistics.median(latencies), 1),
                    "p95": round(sorted(latencies)[int(len(latencies) * 0.95)], 1),
                }
            print(f"{name:<15}{metrics['precision_at_1']:>7.3f}"
                  f"{metrics['precision_at_5']:>7.3f}{metrics['precision_at_10']:>7.3f}"
                  f"{metrics['recall_at_10']:>7.3f}{metrics['mrr']:>7.3f}"
                  f"{metrics['ndcg_at_10']:>7.3f}"
                  f"{metrics['precision_at_1_when_relevant_available']:>9.3f}"
                  f"{metrics['queries_offered_any_relevant']:>9.3f}"
                  f"{metrics['candidate_recall']:>10.3f}"
                  f"{metrics['silent_rate']:>8.3f}{metrics['mean_candidates']:>7.0f}")
        print()

    out = RESULTS.with_name(f"retrieval-benchmark-{args.split}.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("latency p50/p95 ms:", json.dumps(report["latency_ms"]))
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
