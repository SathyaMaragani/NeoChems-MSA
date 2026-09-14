# V2.2 — Transformation-Keyed Precedent Retrieval

Whether retrieval should be keyed on the **transformation** rather than on the
**product molecule**, measured rather than argued.

V2.1 selected candidates by product-molecule similarity and reranked them on the
transformation. Benchmarking showed that is keyed on the wrong thing: two Suzuki
couplings can make products that look nothing alike, so stage 1 frequently
offered no relevant candidate at all, and no reranking can recover from that.

This milestone builds a reaction index carrying several alternative retrieval
keys, and compares four strategies on a **leakage-controlled** benchmark.

> **Production is unchanged.** `OrdProvider` still runs the V2.1 product-keyed
> query. `backend/conditions/retrieval.py` is wired into the benchmark only. The
> recommendation at the end of this document is a recommendation, not a
> deployment.

---

## 1. The measurement problem that had to be fixed first

The V2.1 benchmark numbers were inflated, and by a lot.

**Every ORD campaign contains exactly one reaction type** — 371 campaigns
checked, 0 of them mixed. A campaign is a screen, a plate, a notebook: dozens to
thousands of reactions run by one group, on related substrates, varying
conditions.

So retrieving a reaction's own plate-mate scores as "correct" while proving
nothing. No real query arrives with its own plate already in the index. The V2.1
benchmark allowed exactly that, and a large share of its apparent performance
was it.

This harness therefore:

* **excludes same-campaign candidates** from every query's candidate pool;
* **splits campaigns disjointly** into validation and test, by a hash of the
  campaign id so the split is stable across runs and sample sizes;
* **keeps only reaction types spread across ≥ 4 campaigns** (21 of 42), because
  a type confined to one campaign has no cross-campaign precedent to find;
* reports the **uncontrolled numbers alongside**, so the size of the leakage is
  measured rather than assumed.

Tuning happened on validation. The test split was run once, at the end.

---

## 2. The index

`ord_reaction_index`, 216,681 rows, built by `scripts/build_reaction_index.py`.
Derived data only — droppable and rebuildable, never a source of truth.

| Column | What it is | Role |
| --- | --- | --- |
| `reaction_key` | sha256 of sorted canonical reactants ≫ products | exact-precedent identity |
| `reaction_smiles` | normalised reaction | representation |
| `centre_hash`, `centre_size` | hash of the atom environments that CHANGE | exact transformation key |
| `transformation_bfp` | `reaction_structural_bfp` | **transformation retrieval key** (GiST) |
| `transformation_sfp` | `reaction_difference_fp` | transformation **scoring** (not indexed — see §3) |
| `substrate_bfp` | Morgan over the reactants | substrate retrieval key (GiST) |
| `product_bfp` | Morgan over the major product | V2.1's only key (GiST) |
| `template_hash`, `dataset_id`, `provider`, `dataset_version` | provenance | metadata |
| `reaction_class` | ⚠️ the benchmark's ground truth | **display metadata only** |
| `campaign_id` | screen / plate / notebook | benchmark splitting |

Coverage: 216,455 reaction centres, **54,042 distinct** — so the centre is
selective rather than degenerate.

### reaction_class is fenced off

`reaction_class` is the label the benchmark grades against. If retrieval ever
selected or ranked on it, every number here would measure the retriever reading
the answer key. It is stored for display ("this precedent is a Suzuki") and a
test — `test_retrieval_never_selects_or_ranks_on_reaction_class` — fails the
build if `retrieval.py` references it outside a comment.

### The reaction centre

ORD reaction SMILES reach us unmapped, so a mapping-based reaction centre is not
available. Instead the centre is the **symmetric difference of the multisets of
Morgan atom-environment invariants** on each side: an environment present among
the products but not the reactants was created, and vice versa.

Verified behaviour:

```
aspirin esterification      centre=d3d7d117b24b9cbf  size=12
same reaction, other acid   centre=d3d7d117b24b9cbf  size=12   ← collides ✓
paracetamol amidation       centre=3dd3b82ee44d7fbd  size=15   ← differs ✓
unrelated SN2               centre=8e95315052908791  size=8    ← differs ✓
```

Approximate by construction — two different changes producing identical
environments would collide — so it is used to *find* candidates, never to assert
that two reactions are the same reaction.

---

## 3. ⚠️ A GiST index over `sfp` silently returns nothing

The obvious design is to index the reaction **difference** fingerprint, since it
discriminates best (esterification vs amidation: 0.10, against 0.64 for the
structural fingerprint). That does not work, and it fails silently.

```
SET rdkit.tanimoto_threshold = 0.2;

-- with the GiST index                     -> 0 rows
-- with enable_indexscan/bitmapscan = off  -> 557 rows
```

Measured under **both** `gist_sfp_ops` and `gist_sfp_low_ops`. The operator is
correct; the index prunes everything.

Had this gone unnoticed, "transformation-keyed retrieval finds nothing" would
have looked like a scientific result instead of a broken index — and the whole
milestone would have concluded the opposite of the truth.

The design therefore splits the two roles:

* **select** on `transformation_bfp` (a bit vector — indexes correctly,
  verified: 998 via index == 998 via sequential scan);
* **score** on `transformation_sfp`, over the few hundred rows already selected,
  where a scan is cheap.

Guarded by `test_the_sfp_difference_fingerprint_is_never_used_as_an_index_key`.

---

## 4. Two harness bugs that would have faked the result

Recorded because both produced plausible-looking numbers.

**Every strategy must be scored by the same formula.** `by_product` initially
returned only product similarity, while the ranker combined transformation and
substrate. Every candidate therefore scored 0, fell below the floor, and the
V2.1 baseline measured **100% silent, P@1 0.000** — a spectacular and entirely
false defeat for the incumbent. All strategies now share one `_score()` pass, so
the comparison isolates candidate *selection*.

**Query fingerprints must be built once.** Written inline in a `SELECT` list or
`ORDER BY`, `reaction_from_smiles(...)` is rebuilt per candidate row — a
thousand reaction parses per call. A benchmark pass took tens of minutes and
looked like a database problem. Materialising the query's fingerprints in a CTE
took per-call latency from minutes to ~100 ms.

---

## 5. Strategies compared

All four select differently and are then scored and ranked identically.

| Strategy | Selects on | Notes |
| --- | --- | --- |
| `product` | product Morgan fingerprint | the **V2.1 baseline** |
| `transformation` | structural reaction fingerprint | V2.2 core idea |
| `substrate` | reactant Morgan fingerprint | control |
| `hybrid` | union of transformation ∪ substrate ∪ exact centre | V2.2 proposal |

The keys sit on different similarity scales and **cannot share a threshold** —
Morgan similarity runs around 0.5, difference-fingerprint similarity averages
0.013 with only 0.2% of pairs above 0.30. A 0.4 threshold that selects sensibly
for a product fingerprint selects *nothing at all* for a difference fingerprint.
Per-key defaults live in `retrieval.DEFAULT_THRESHOLDS`.
