# Route Benchmark

Phase 0 of the [ChemAIRS parity plan](chemairs-parity-plan.md): before changing
the route engine, measure it. Every Phase 1 change is judged against these
numbers.

```bash
python scripts/benchmark_routes.py --set n1   --profile production --sample 500
python scripts/benchmark_routes.py --set n1   --profile paroutes   --sample 500
python scripts/benchmark_routes.py --set hard --profile production --workers 4 --evidence
```

Results: `docs/route-benchmark-<set>-<profile>.json`, one record per target.

---

## What is measured

**Two target sets.**

| Set | Targets | Stock | Reference route | Source |
| --- | --- | --- | --- | --- |
| `n1` | seeded 500 of PaRoutes 2.0 set-n1's 10,000 | PaRoutes' n1 stock (13,431) | the patent's own route | USPTO patents, via PaRoutes (CC-BY-4.0) |
| `hard` | seeded 60 of 352 complex approved drugs | ZINC, as served | none | local ChEMBL |

"Complex" is fixed criteria, not hand-picking: 2–5 stereocentres, 3 or more
rings, 20–45 heavy atoms, no ring larger than 8 atoms, deduplicated by
structure (30% of ChEMBL's approved entries are salts of a drug already
listed). A chemist should curate this set; until one does, it is at least not
chosen to flatter.

**Two search profiles.**

| Profile | Iterations | Time limit | Depth | Filter policy | Routes built |
| --- | --- | --- | --- | --- | --- |
| `production` | 100 | 120 s | 6 | on | up to 25 |
| `paroutes` | 500 | 3600 s | 10 | off | all solved |

`production` is what the API does. `paroutes` is PaRoutes' published MCTS
configuration, run to check the harness against their numbers.

**Accuracy** is computed exactly as PaRoutes' `route_quality.py` does it:
every extracted route is ranked by the Badowski score, and the reference is
found at rank *k* when a route ranked ≤ *k* has tree edit distance 0 to it,
compared on molecules and reactions. It is also reported in **production
order**, solved routes only and ranked by state score, which is what a user is
shown.

**No model leakage.** The served expansion model is PaRoutes 2.0's own, trained
on USPTO with the n1/n5 reference reactions removed: `uspto_templates.csv.gz`
is byte-identical to their `uspto_unique_templates.csv.gz`. See
[data-provenance.md §2](data-provenance.md).

**Workers.** AiZynthFinder gives each ONNX session two threads here, so 8
workers fill the 16 logical cores without oversubscription, which would
otherwise let production's 120 s limit cut searches short.

---

## Baseline, production profile, PaRoutes n1 (500 targets)

| Metric | Value (95% CI) |
| --- | --- |
| Solved | **89.2%** (86.2–91.6) |
| Reference found, top-1 / top-5 / top-10 (PaRoutes ranking) | **0.212 / 0.364 / 0.366** |
| Same, in production order | 0.200 / 0.332 / 0.360 |
| Median search time | 11.5 s |
| Median time to first solution | 0.13 s |
| Median steps, top route | 3 |

**The engine is good at short routes and poor at long ones.** By the length of
the reference route (longest linear path):

| Reference length | Targets | Solved | Top-5 |
| --- | --- | --- | --- |
| 2 steps | 123 | 96% | 74% |
| 3 | 232 | 92% | 34% |
| 4 | 91 | 86% | 11% |
| 5+ | 54 | 69% | 6% |

A 5-step reference route is recovered 6% of the time. That is the complex-target
gap the parity plan is about, now as a number.

---

## The harness reproduces PaRoutes

The `paroutes` profile on the same 500 targets, against PaRoutes' published
AiZynthFinder MCTS result on all 10,000 (their README, 2.0 version):

| Metric | Ours, 500 targets (95% CI) | Published, 10,000 |
| --- | --- | --- |
| Solved | 97.4% (95.6–98.5) | 97.2% |
| Top-1 | 0.232 (0.197–0.271) | 0.237 |
| Top-5 | 0.488 (0.444–0.532) | 0.511 |
| Top-10 | 0.528 (0.484–0.571) | 0.541 |

Every published value sits inside the interval. The model, the stock, the
ranking and the TED comparison are therefore behaving as PaRoutes' own tooling
does, and a change in these numbers is a change in the engine, not the harness.

---

## Production settings leave accuracy on the table

Same 500 targets, paired:

| | `production` | `paroutes` | Paired |
| --- | --- | --- | --- |
| Solved | 89.2% | 97.4% | 41 targets solved only by `paroutes`, 0 only by `production` |
| Top-5 (PaRoutes ranking) | 0.364 | 0.488 | 65 found only by `paroutes`, 3 only by `production` |
| Top-5 (production order) | 0.332 | 0.346 | |
| Median search time | 11.5 s | 54.3 s | |

| Reference length | Top-5 `production` | Top-5 `paroutes` |
| --- | --- | --- |
| 2 | 74% | 84% |
| 3 | 34% | 49% |
| 4 | 11% | 26% |
| 5+ | 6% | 6% |

Three findings, each a Phase 1 lead:

1. **The search budget costs 8 points of solve rate and 12 of top-5**, at ~5×
   the time. Which of the three differences matters (100 → 500 iterations,
   depth 6 → 10, filter on → off) is measured below, in Phase 1a.
2. **Route ordering loses a third of what the search finds.** Under the
   `paroutes` search, the API's order (solved routes by state score) reaches
   top-5 0.346, against 0.488 when the same routes are ranked by the Badowski
   score. Recovering the patent's route is a proxy for "a route a chemist would
   run", not the definition of it, so this is a lead to test, not a verdict.
   **Tested at production settings (26 Sep 2026) and refuted**: on 200 targets
   at depth 10, re-ranking the solved routes by Badowski score gives top-1
   0.205 against 0.245 for the current state-score order (2 targets gained,
   10 lost) and top-5 0.360 against 0.365. The gain above needs the
   all-routes `paroutes` search; with production's 25 routes it is gone. The
   API order is unchanged. The harness keeps reporting both orders
   (`rank_solved_badowski`).
3. **Five-step routes stay at 6% whatever the budget.** More search does not
   reach them. That points at the model (Phase 1c, a template-free expansion
   model) rather than the settings.

---

## Phase 1a: which setting drives the gain

One change at a time from `production`, same 500 targets, paired against the
baseline (`+gained / −lost` targets). Results:
`docs/route-benchmark-n1-ablation-<change>.json`.

| Change | Solved | Top-5 | Paired solved | Paired top-5 | Median time |
| --- | --- | --- | --- | --- | --- |
| none (baseline) | 89.2% | 0.364 | | | 11.5 s |
| depth 6 → 10 | **93.4%** | 0.366 | **+22 / −1** | +1 / −0 | 26.7 s |
| iterations 100 → 500 (time limit lifted) | **94.0%** | **0.410** | **+24 / −0** | **+39 / −16** | 64.6 s |
| filter off | 90.2% | 0.370 | +12 / −7 | +9 / −6 | 8.5 s |
| ringbreaker beside uspto | 87.0% | 0.354 | +5 / −16 | +10 / −15 | 21.6 s |
| all of `paroutes` | 97.4% | 0.488 | +41 / −0 | +65 / −3 | 54.3 s |

```bash
python scripts/benchmark_routes.py --set n1 --sample 500 --depth 10
python scripts/benchmark_routes.py --set n1 --sample 500 --iterations 500 --time-limit 3600
python scripts/benchmark_routes.py --set n1 --sample 500 --filter off
python scripts/benchmark_routes.py --set n1 --sample 500 --ringbreaker
```

- **Depth is the solve-rate lever, not an accuracy one.** Four points more
  targets solved, one lost, top-5 unchanged, at 2.3× the time.
- **Iterations are the only accuracy lever** (+4.6 points top-5, +4.8 solved),
  at 5.6× the time; 3.6% of searches would pass production's 120 s cap. The
  API already accepts `iteration_limit` up to 500, so this is available to a
  user who asks for it.
- **The filter changes nothing measurable**, and is kept: it rejects infeasible
  steps, which recovering the patent's route cannot see.
- **Ringbreaker makes it worse at this budget**: 16 targets lost for 5 gained.
  Two expansion policies split 100 iterations between them. The parity plan's
  suggestion to run it alongside uspto is not supported here.
- **Adopted: depth 10**, in `config.yml` since 25 Sep 2026 (+4 points solved
  for ~2.3× the median search time). The baseline tables above ran at depth 6;
  the `production` profile now reads its depth from `config.yml`.
- **Ibuprofen is a search-budget problem.** Its industrial starting materials
  (isobutylbenzene, 4-isobutylacetophenone) are in ZINC and the templates
  exist: it solves at 500 iterations (just inside 120 s) and not at 100. The
  UI's iteration limit already reaches it; no default change is justified by
  one target.
- **The two levers stack**, and they are not free: nothing here reaches the
  `paroutes` result at interactive speed. The rest of its top-10 gain comes
  from extracting every solved route and ranking them by Badowski score, which
  the API does not do.

---

## Hard set: complex approved drugs (60, production profile, ZINC stock)

| Metric | Value (95% CI) |
| --- | --- |
| Solved | **38.3%** (27.1–51.0), 23 of 60 |
| Median search time | 16.8 s |
| Median steps, top route (solved) | 2 |
| Steps of the top route with **a direct precedent** | **7.2%** |
| Steps of the top route with any precedent (direct or similar) | 100% |

Complex drugs solve at less than half the PaRoutes rate. Those that do solve
are mostly short routes from advanced purchasable intermediates; semisynthetic
β-lactams are one acylation from a ZINC-listed 6-APA or 7-ACA core.

**"Any precedent" has stopped meaning anything.** With 2M indexed reactions and
a 0.20 similarity floor, every step finds a "similar" one. Track the direct
share. It is also the fairer number on this set than on n1: n1 targets come
from USPTO patents, which are now in the evidence index, so an n1 step can find
its own source patent as a direct precedent.

---

## What this does NOT establish

- **That the routes are good.** Recovering the patent's route is a proxy. A
  different route can be better, and only a chemist can say so.
- **The full 10,000.** 500 targets, seeded; the intervals are the honest width.
- **Anything about ChemAIRS.** The same targets run on ChemAIRS are still the
  missing half of Phase 0.
- **A curated hard set.** Fixed criteria, not a chemist's list of the targets
  that matter.
