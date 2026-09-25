# Retrieval Benchmark

Answers the question that had to be answered before anything else got built:

> **When the system says "similar experimental precedent", is the reaction it
> found chemically relevant — or merely fingerprint-near?**

Before this, the `0.65 / 0.35` transformation/substrate weighting and the `0.35`
similarity floor were reasoned guesses. They are now measured, and **both were
wrong** — the weighting was on the wrong side, and the floor was the single
worst setting tested.

Reproduce with:

```bash
conda activate ord-ingest
python scripts/extract_benchmark_labels.py
conda activate retrosynth
python scripts/benchmark_retrieval.py --queries 700 --labels fine
python scripts/benchmark_retrieval.py --queries 700 --labels coarse
python scripts/benchmark_retrieval.py --queries 200 --prefilter-sweep
```

Results land in `docs/retrieval-benchmark-results*.json`.

---

## Ground truth

Two of the six ingested ORD datasets carry a `REACTION_TYPE` identifier written
by the chemists who ran the campaign:

| Dataset | Reactions | Labels |
| --- | --- | --- |
| `805ad863feef…` | 50,688 | all `C-N COUPLING` |
| `d92976309c3a…` | 39,347 | 41 types — SUZUKI, BUCHWALD, HYDROGENATION, MITSUNOBU, SNAR, HECK, NEGISHI, … |

**90,035 labelled reactions, 42 distinct types.** Those labels were never
derived from a fingerprint, so grading retrieval against them is not circular —
which is the whole reason this benchmark means anything.

The retrieval pool is restricted to the labelled subset, so every candidate
carries a judgment and precision is never computed over a partly-judged list.
A candidate is **relevant** iff its reaction type equals the query's. The
distractors are hard by construction: the same HTE campaigns, the same papers,
structurally similar molecules, different transformation.

### Both label granularities are wrong, in opposite directions

- **fine** (`REACTION_TYPE`, 42 values) **understates** precision: SUZUKI,
  BUCHWALD, HECK and NEGISHI are separate labels while all being Pd couplings a
  chemist might well accept as precedent.
- **coarse** (`ReactionClass`, 8 values) **overstates** it — and the dataset's
  own `UNTAGGED` bucket lumps Lewis-acid, photochemical, SNAr and fluorination
  chemistry together, so `UNTAGGED` rows are dropped rather than scored.

The truth sits between the two. Reporting only one would be a choice about
which way to be wrong, so both are run.

---

## Results — before and after tuning

700 queries, stratified so that 50k C–N couplings cannot drown out the other 41
transformations. Fine labels, seed 0.

| Metric | Before<br>`0.5/400`, `0.65/0.35`, floor `0.35` | **Shipped**<br>`0.4/1000`, `0.30/0.70`, floor `0.20` | Best measured<br>(`0.3`, not shipped) |
| --- | --- | --- | --- |
| Precision@1 | 0.434 | **0.516** | 0.536 |
| Lift over random | +0.040 | **+0.158** | +0.227 |
| Precision@1 *given stage 1 found anything relevant* | 0.869 | **0.921** | 0.891 |
| Precision@5 | 0.429 | **0.511** | 0.529 |
| Precision@10 | 0.425 | **0.507** | 0.524 |
| Recall@10 | 0.433 | **0.516** | 0.532 |
| Silent (returns nothing) | 43.4% | **27.6%** | 12.9% |
| Queries offered ≥1 relevant candidate by stage 1 | 50.0% | **56.0%** | 60.1% |
| Median candidates reranked | 123 | 180 | 514 |
| Enrichment latency per matching step | ~1.4 s | **~0.6 s** | ~1.6 s |

`0.3` retrieves better and is **not** shipped: stage 2 re-normalises and
re-fingerprints every candidate on every request, so cost scales directly with
candidate count, and 1.6 s per step made an interactive plan take ~10 s. That is
an implementation problem, not a retrieval one — precompute candidate
fingerprints into Postgres and 0.3 or lower becomes affordable. Until then 0.4
keeps most of the gain at a third of the cost.

**Read the lift column, not the raw precision.** The pool is 56% C–N coupling,
so a naive ranker scores well by base rate alone. Random ordering of the same
candidates is the honest floor, and it is reported for every variant.

### Why two precision numbers

A low P@1 hides two completely different failures: the reranker put the wrong
thing first, or **stage 1 never offered a right thing to put first**.
Conditioning on the second separates them — and that is where the real problem
turned out to be.

---

## What the benchmark established

**1. Stage 2 reranking earns its cost.** Stage 1 alone (product similarity, no
rerank) gets P@1 0.453 / lift +0.144; with reranking, 0.536 / +0.227. Against
random at 0.280. On individual queries the difference is stark — one sampled
SUZUKI query returns 0/5 relevant with product similarity alone and 5/5 with any
reranking at all.

**2. The similarity floor was actively harmful.** At 0.35 the system was silent
on 43% of queries; at 0.20 it is silent on 28% at the shipped threshold (13% at
0.3), and every precision and recall figure is better. Higher floors degrade monotonically — 0.70 drops P@1 to 0.300.
The floor was discarding good results, not filtering bad ones.

`0.20` rather than `0.00` is a **product judgement, not a measurement**: 0.00
scored marginally better on every metric, but it would let a reaction scoring
0.05 appear under the heading "SIMILAR EXPERIMENTAL PRECEDENT". A floor prevents
that; 0.20 keeps most of the measured gain.

**3. The weighting was on the wrong side, and it matters less than expected.**
Stable across two seeds: 0.2–0.4 transformation is optimal (P@1 0.536–0.551),
0.5 is slightly worse, and pure transformation similarity is worst
(0.459–0.480). **Substrate similarity is the stronger signal here** — the
opposite of what `0.65/0.35` assumed.

> ⚠️ **Likely partly an artifact.** The labelled chemistry is industrial HTE,
> where each reaction-type campaign uses characteristic substrate classes.
> Substrate similarity may therefore be proxying *"which campaign is this
> from"*, which would not generalise outside HTE data. `0.30` is the measured
> optimum; anything in 0.2–0.4 is within noise; a higher transformation share
> may well be safer on non-HTE chemistry. Re-run this before trusting the number
> elsewhere.

**4. Stage 1 is the bottleneck, and tuning cannot fix it.**

| threshold | limit | queries offered ≥1 relevant | median candidates | SQL p50 |
| --- | --- | --- | --- | --- |
| 0.5 | 400 | 52.6% | 123 | 10.5 ms |
| **0.4** | **1000** | **57.7%** | **197** | **11.6 ms** ← shipped |
| 0.3 | 1000 | 61.1% | 514 | 13.3 ms |
| 0.3 | 3000 | 62.3% | 514 | 13.9 ms |
| 0.2 | 3000 | 68.0% | 3000 | 40.8 ms |

Loosening helps and then saturates. Even at threshold 0.2 with 3000 candidates,
a third of queries are still offered nothing relevant.

The reason is structural: **stage 1 retrieves by product-molecule similarity,
which is a poor proxy for "same transformation"**. Two Suzuki couplings can make
products that look nothing alike. Component separation confirms it — product
similarity separates relevant from irrelevant by only +0.243, against +0.376 for
transformation and +0.329 for substrate.

Fixing this properly means indexing something transformation-keyed (storing the
reaction difference fingerprint in Postgres with its own index) rather than
loosening a product-similarity threshold further. That is a schema change and
its own milestone — deliberately **not** done here.

---

## After USPTO: the corpus grew 9× (Sep 2026)

Adding 1.77M USPTO grant reactions took the index from 216k to 1.98M rows, on
settings tuned for ORD alone. Same code, same 400 validation queries (21 types,
109 campaigns, seed 0), leakage-controlled, hybrid, with USPTO candidates
excluded (`--ord-only`) and included:

| Metric | ORD only | ORD + USPTO |
| --- | --- | --- |
| Precision@1 | 0.427 | 0.420 |
| Queries offered ≥1 relevant candidate | 63.2% | 58.3% |
| Candidate recall | 0.127 | 0.103 |
| Silent (on graded candidates) | 19.3% | 24.0% |
| Production top-1 is ungraded | 38.0% | **55.5%** |

- **Ranking held; recall slipped.** Each hybrid branch keeps an *unordered*
  1000 rows, and USPTO rows now fill slots that relevant ORD rows used to reach.
- **Ordering the branches by similarity does not fix it**, and it was measured
  rather than assumed: P@1 fell to 0.405, 56.0% offered, silent rose to 27.3%,
  and the pass ran ~3× slower, because an `ORDER BY` must fetch and score
  every match instead of stopping at the cap. Not shipped.
- **Human grading is blind to most of what production shows.** It grades
  ORD's labels only, and USPTO carries none, so in 55.5% of queries the
  precedent a user sees first cannot be graded. "Silent" here means silent on
  graded candidates; production answers some of those queries with USPTO rows.
  The rule grader below closes that gap.
- **Latency is acceptable.** Hybrid retrieval p50 334 ms / p95 658 ms per step
  (was 63 / 172 ms). End to end, a plan with conditions costs 0.2–0.5 s more
  per step when cold (aspirin 2.6 s → 7.6 s over 16 steps) and nothing extra
  when cached.

### Graded by rule, the patents help

To grade what production actually shows, `--grader rules` labels the query and
**every** candidate, ORD and USPTO alike, with one function
([scripts/reaction_rules.py](../scripts/reaction_rules.py)) that reads the
bond change: a C–B bond consumed with an aryl halide is a Suzuki, a C–F bond
gained on an unchanged skeleton is a fluorination, and so on. One grader on
both corpora makes the comparison fair; rules rather than people make it a
proxy, so it was checked twice before use:

- **Against the human labels on ORD** (`python scripts/reaction_rules.py`):
  precision 98.5–100% for 11 of the 13 rule groups. SUZUKI (77%) and BORYLATION
  (76%) are lower for reasons that are not rule errors. The SUZUKI misses are
  Fe-, photo- and Cu-catalysed boron couplings, which make the same bond with
  another catalyst. The BORYLATION misses are real Miyaura borylations inside
  Suzuki-labelled campaigns. Types defined by catalyst or conditions (PD/RH
  COUPLING, CH ACTIVATION, LEWIS ACID, PHOTOCHEMISTRY) and NEGISHI (ORD never
  lists the zinc reagent) get no rule.
- **By hand on 39 sampled USPTO rows**: 11 groups label the chemistry right.
  CARBONYLATION and OXIDATION do not. On ORD they are exact, but on patents they
  also match lithiation/DMF formylations and mCPBA N-oxidations, so their
  queries are dropped (`reaction_rules.UNGRADED`).

That leaves 217 of the 400 queries (14 types, 62 campaigns), leakage-controlled:

| Metric | ORD only | ORD + USPTO |
| --- | --- | --- |
| Precision@1 | 0.539 | **0.700** |
| Precision@5 | 0.536 | 0.676 |
| MRR | 0.570 | 0.714 |
| Queries offered ≥1 relevant candidate | 93.1% | 100% |
| P@1 when production's top-1 is USPTO / ORD | – | 0.727 / 0.663 |

The gain comes from chemistry ORD barely covers: fluorination 0.05 → 0.84,
hydrogenation 0.09 → 0.73, Heck 0.00 → 0.56, cyanation 0.11 → 0.44. **One
regression: borylation 0.90 → 0.58** (19 queries), diagnosed below. Stille is
flat at 0.30. The per-type figures in this table are inflated by repeated
queries; the distinct-reaction numbers below are the ones to use.

The human-graded recall slip above is real but is outweighed. These
rule-graded numbers are not comparable to the human-graded ones earlier in this
document: they use a different grader and a different query subset.

### The borylation regression, and the queries were mostly repeats

**The benchmark samples repeats.** HTE campaigns run one reaction under many
conditions, so plain sampling draws the same reaction again and again: the 217
graded queries above are only **67 distinct reactions** (Heck: 16 queries, 2
reactions), and the 400 human-graded queries only about 205. `--distinct` samples
one query per reaction. It is off by default so the recorded numbers above
still reproduce. **Use it.**

**The regression was one reaction.** The 19 borylation queries were 5
reactions, and one of them, a C–H borylation of methyl 2-chloroisonicotinate
repeated 6 times, flipped from hit to miss. Production's USPTO top-1 was a
*Suzuki of the same chloropyridine*. Its reactants (aryl-Bpin + the
chloropyridine) share pinacol-boronate bits and the whole chloropyridine with
the query's (B₂pin₂ + the chloropyridine), so substrate similarity was 0.68–0.72,
while transformation similarity correctly said "different chemistry"
(0.27–0.46, against 0.80 for the real borylation ranked second). At 0.70
substrate weight, the wrong chemistry won. That is the failure the weighting
caveat predicted for non-HTE data, and USPTO is the first non-HTE data here.

**Fix: transformation weight 0.30 → 0.40.** Swept on validation and confirmed
once on the held-out test split, distinct reactions, leakage-controlled:

| Test split | ORD only | + USPTO, 0.30 | **+ USPTO, 0.40** |
| --- | --- | --- | --- |
| P@1, rule-graded, all candidates (100 reactions) | 0.570 | 0.660 | **0.720** |
| P@1, human-graded, ORD candidates only (212 reactions) | – | 0.434 | **0.443** |

On test, 0.40 matches or beats 0.30 in every type: borylation 0.63 → 0.75,
Suzuki 0.84 → 0.95, aryl C–N/C–O 0.77 → 0.85, hydrogenation 0.76 → 0.82.
On validation, 0.6 scored higher on the rules (0.718 against 0.709) but cost 3
points on human labels (0.385 against 0.419), so it was not taken. Patents
still help on data never tuned on: 0.570 → 0.720.

```bash
python scripts/benchmark_retrieval.py --split test --strategies hybrid --distinct --grader rules
python scripts/benchmark_retrieval.py --split test --strategies hybrid --distinct --grader human
```

```bash
python scripts/reaction_rules.py        # grader vs human labels
python scripts/benchmark_retrieval.py --split validation --strategies hybrid --grader rules --ord-only --out ord_only.json
python scripts/benchmark_retrieval.py --split validation --strategies hybrid --grader rules --out full.json
```

### The SIMILAR badge floor: 0.20 → 0.40

At 0.20 every step in the 2M-reaction index found a "similar" precedent (100%
of top routes on the complex-drug set), so the SIMILAR badge stopped telling
the user anything. The floor was recalibrated with the rule grader on distinct
reactions, leakage-controlled, by the precision of the top precedent among the
steps that keep a badge:

| Floor | Validation: badged | precision when badged | Test: badged | precision when badged |
| --- | --- | --- | --- | --- |
| 0.20 (was) | 100% | 0.71 | 100% | 0.72 |
| 0.30 | 99% | 0.72 | | |
| **0.40 (now)** | **82.5%** | **0.82** | **83%** | **0.81** |
| 0.50 | 76% | 0.82 | | |
| 0.60 | 52% | 0.89 | | |

0.40 is the knee: precision flattens above it until 0.60, which removes half
the badges. On test, 12 of the 17 badges it removes had a wrong top precedent.

On real routes the effect is smaller: SIMILAR coverage of the complex-drug
set's top-route steps goes 100% → 96%. Those steps are common chemistry
(acylations, amide couplings, deprotections) with genuinely close precedents
among 2M reactions, so the badge there is now mostly true rather than
uninformative. The number that separates routes is still the direct share
(7.2% of steps).
A step below the floor now reports "no verified condition evidence", which is
what the evidence supports.

```bash
python scripts/benchmark_retrieval.py --split test --strategies hybrid --distinct --grader rules --floor 0.4
```

---

## What this does NOT establish

- **That rule-graded relevance is what a chemist would call relevant.** The
  rules read the bond change and never the catalyst, so a Cu-catalysed boron
  coupling "is" a Suzuki. Validated against ORD's labels and spot-checked on
  39 USPTO rows, not audited at scale.

- **Anything outside industrial HTE chemistry.** The labelled subset is
  couplings, alkylations and hydrogenations from two campaigns. These numbers
  describe that chemistry.
- **That the retrieved conditions would work on your substrate.** Retrieval
  relevance is not experimental validity. Nothing here has been run in a lab.
- **Fine-grained relevance.** Ground truth is a reaction-type name. "Same named
  transformation" is a coarse proxy for "a precedent a chemist would find
  useful".
- **Generalisation of the tuned constants.** They were fitted on 700 queries
  over two datasets. Treat them as the best current estimate, not as settled.

---

## Reproducibility

Seeded (`--seed`), stratified, and the harness **imports the production
constants** rather than duplicating them — so the benchmark always grades the
settings that actually ship, and retuning cannot silently desynchronise the two.

The labels are regenerated by `scripts/extract_benchmark_labels.py` and written
to `data/external/ord/benchmark_labels.csv`, which is gitignored: it is a
derivative of CC-BY-SA-4.0 data, so it is regenerated rather than committed. See
[data-provenance.md](data-provenance.md).
