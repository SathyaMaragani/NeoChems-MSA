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

## What this does NOT establish

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
