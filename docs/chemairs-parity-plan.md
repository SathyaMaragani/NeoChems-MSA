# Reaching ChemAIRS Parity

A staged plan to bring RamChems level with ChemAIRS, then past it on the one
thing that matters most: routes for **complex** molecules.

Written 15 Sep 2026. Baseline is commit `e074b38`.

---

## What "parity" means, and why it is not yet measurable

ChemAIRS' own claim is that a top-tier CRO blind-tested 9 retrosynthesis
algorithms on **60 structurally diverse, difficult targets**, judged by expert
synthetic chemists, and ChemAIRS won. Their deck carries no per-target numbers,
so nothing in it can be checked.

That means parity cannot currently be claimed in either direction. The first
deliverable is therefore not code, it is a **measurement**:

1. Request a ChemAIRS trial (the deck invites demos — `anbu@molecularsolutions.co.in`).
2. Build a fixed hard-target set: **PaRoutes** (AstraZeneca, MIT-licensed,
   10,000 patent-derived routes with `n1`/`n5` splits) plus ~60 hand-picked
   difficult drugs of our own.
3. Run both platforms on the same targets. Record solve rate, route length,
   agreement with the known route, and chemist judgement.

Until that exists, "better than ChemAIRS" is a guess. After it exists, every
phase below has a number attached.

---

## Where RamChems actually stands

**Honest summary: strong evidence layer, weak route engine.**

| Capability | RamChems | Note |
| --- | --- | --- |
| Reaction corpus | 216,681 ORD reactions | ChemAIRS claims 73M literature + patent. **Their moat.** |
| Route search | AiZynthFinder, public USPTO templates, MCTS at defaults | Does not solve **ibuprofen** at 100 iterations |
| Known vs predicted labelling | Four evidence levels, full provenance | **Ahead of them.** Their deck shows two |
| Condition search | ORD evidence, benchmarked, leakage-controlled | Thin coverage; V2.2 hybrid retrieval not yet in production |
| Forward step validation | Built, uncommitted quality | precision 0.91 / recall 0.46, 29% false positives, ~5 s per reaction |
| Route metrics & filters | State score, step count | No difficulty, cost, cycle time, or sorting |
| Hazard / toxicity flags | none | They ship 12 legend classes |
| SA score | none | Days of work |
| Batch search, route import, manual disconnection, image input | none | Name → SMILES works |
| Condition prediction, scale-up conditions | none | |
| Process chemistry, impurity prediction, Bayesian optimisation | none | Each its own project |
| Cost from vendors | none | ZINC gives purchasability, not price |
| Deployment, auth, ELN / inventory integration | none | Local only |
| QSAR solubility with calibrated intervals | working | Not in their deck |

---

## Phase 0 — Make the ground stable and measurable

*Prerequisite for everything. Nothing below is trustworthy without it.*

- Docker/Postgres keeps stopping between sessions; test runs currently fail with
  `PoolClosed` rather than real failures. Make the stack start reliably and get
  a green suite on record.
- Build `scripts/benchmark_routes.py`: the hard-target harness described above.
  Report solve rate, steps, top-`k` route agreement, search time, and the share
  of steps carrying real precedent.
- Record the RamChems baseline on it. Publish the numbers even though they will
  be poor — that is the point of a baseline.

```
EXIT  green test suite on record
EXIT  baseline solve rate + route agreement on PaRoutes n1 and the hard set
EXIT  same numbers for ChemAIRS, if a trial can be obtained
```

---

## Phase 1 — The route engine

*The whole reason for the project. Everything else is breadth; this is depth.*

Strategy: **generate more candidate steps, then filter them harder.** We cannot
out-data 73M reactions, so we must out-search and out-verify.

**1a. Exhaust the search we already have.** The config admits it: *"MCTS search
settings are left at AiZynthFinder defaults on purpose (not tuned yet)."*
Ibuprofen failing is a search-and-stock problem, not a missing-model problem.
The installed AiZynthFinder already offers `mcts`, `retrostar`, `dfpn`,
`breadth_first` and `andor_trees` — compare them on the Phase 0 harness. Raise
iteration budgets. Run the `ringbreaker` policy (already downloaded) alongside
`uspto` instead of leaving it idle.

**1b. Expand the building-block stock.** ZINC alone truncates routes early. Add
Enamine / Mcule / eMolecules catalogues so "purchasable" matches what a chemist
can actually buy.

**1c. Add a template-free expansion model.** This is the specific fix for
complex targets: their key step is often a ring system or core that exists in no
USPTO template, so a template engine cannot propose it at any budget.
**Chemformer** (AstraZeneca, Apache-2.0) runs beside AiZynthFinder, not instead
of it.

**1d. Ingest Lowe's USPTO extraction** (~1.8M reactions, **CC0** — commercially
clean, unlike ORD). Closes our 0% patent coverage and gives a much larger
template library to retrain the expansion policy on.

**1e. Stereochemistry and strategy.** Complex drugs are chiral; public USPTO
templates handle stereocentres poorly, and ChemAIRS marks chiral-resolution
steps explicitly. Rank routes on convergence, protecting-group count and
step-precedent quality — not step count alone.

```
EXIT  ibuprofen solved at default settings
EXIT  solve rate on the hard set beats the Phase 0 baseline by a stated margin
EXIT  template-free model contributes steps templates could not propose,
      counted on the benchmark
```

---

## Phase 2 — Route results a chemist can act on

*Cheap relative to Phase 1, and closes most of the visible feature gap.*

- **Total steps / prediction steps.** We can do this more honestly than they
  can: "prediction steps" is exactly our count of steps with no direct
  precedent, which the evidence layer already computes.
- **Difficulty score** on a stated scale, with the inputs named.
- **Cost per gram** from supplier price lists, and an estimated synthesis cycle.
- **Sorting and filtering** on all of the above.
- **Hazard legends** — toxic, explosive, high-energy, allergen, green solvent —
  as rule-based SMARTS plus GHS data from PubChem. Rules must be auditable, not
  a black box.
- **Inputs**: SMILES and name already work; add CAS lookup via PubChem synonyms,
  and structure-from-image (DECIMER or MolScribe, both MIT).
- Route export to PDF/CDXML.

```
EXIT  every ChemAIRS route-list column present, each with a documented source
EXIT  no hazard flag without a citable rule or a PubChem GHS record
```

---

## Phase 3 — Condition intelligence

- Promote **V2.2 hybrid retrieval** to production. It is already measured on a
  leakage-controlled test split: P@1 0.502 against the product-keyed baseline's
  0.431, with steps returning nothing down from 26.4% to 14.4%.
- Precompute candidate fingerprints in Postgres. This is what currently forces a
  worse retrieval threshold than the benchmark prefers.
- Feed Lowe USPTO conditions into the evidence layer beside ORD.
- **Train our own condition-prediction model.** The strong published
  recommenders carry non-commercial terms; ours must be trained in-house on CC0
  and permissively-licensed data to stay sellable.
- **"Experience conditions"** is their term for customer data. Our
  `LiteratureProvider` interface already accepts an internal-data provider —
  this is a differentiator, since it learns from a customer's own failures.
- **Scale-up conditions** honestly: mg→kg guidance needs process data we do not
  have. Start with solvent-class, exotherm and hazard warnings, not invented
  numbers.

```
EXIT  hybrid retrieval live, with the benchmark re-run against production
EXIT  condition model trained only on commercially clean data, with a
      calibrated confidence
```

---

## Phase 4 — The workflow modules

*Parallelisable. Ordered by effort, not importance.*

| Module | Approach | Effort |
| --- | --- | --- |
| SA score | RDKit `sascorer`; add RAscore (MIT) for retrosynthetic accessibility | days |
| Batch search | queue over the existing plan endpoint | ~1 week |
| Manual search | chemist draws their own disconnection; we score and condition it | ~2 weeks |
| Import route | CDX/CDXML/RXN via Indigo, then condition it | ~2 weeks |
| Forward synthesis | scaffold + building blocks + reaction SMARTS → virtual library, filtered on properties and SA | 3–4 weeks |
| Process chemistry | constrained search: exclude reagents/solvents/catalysts, prefer green solvents, bound temperature and pressure | 3–4 weeks |
| Impurity prediction | side-reaction templates + forward-model top-`k` by-products, matched against an observed mass | 4–6 weeks |
| Bayesian optimisation | BoTorch or EDBO+, standalone first, then wired to condition search | 3–4 weeks |

Their impurity module claims >90% recognition for HATU/DCC-type reagent
by-products. That is a narrow, checkable claim and a good first target.

---

## Phase 5 — Enterprise

Authentication, multi-tenancy, cloud and on-premise deployment, ELN and
inventory integration, audit trails, PDF reporting. Real work, but none of it
differentiates the chemistry. Deferred deliberately.

---

## Then: past parity

Four places where we can be better rather than equal:

1. **Complex-target solve rate** — the generate-more/filter-harder combination
   of Phase 1 plus forward validation. This is the actual goal.
2. **Traceability** — every condition already carries its source, licence and
   evidence level, and never fabricates a DOI or URL. We are ahead here today.
3. **Calibrated uncertainty** — the QSAR module already ships measured coverage
   rather than a bare number. Extend that discipline to route and condition
   confidence.
4. **Published benchmarks** — ChemAIRS asserts wins without per-target data. Our
   harnesses are reproducible and leakage-controlled. Publishing them is a
   credibility advantage with exactly the CRO audience they sell to.

---

## Risks, stated plainly

- **Data gap.** 216k versus 73M. Lowe USPTO narrows it to ~2M; the rest needs
  licensing (Pistachio, Reaxys) or customer data partnerships.
- **Licensing.** ORD is CC-BY-SA-4.0, which is copyleft and still needs legal
  review. Vendor pricing and CAS lookups need agreements. Keep every commercial
  path on CC0/permissive data.
- **No chemist in the loop.** Their advantage is expert rules accumulated since
  2008 and evaluation by experienced synthetic chemists. We need a chemist
  partner to judge routes; benchmark agreement is not the same as feasibility.
- **Compute.** Template-free models and forward validation want a GPU.
- **Forward validation is not ready.** 46% recall and 29% false positives are
  not a filter yet.
- **Timeline.** ChemAIRS is a 2008→2026 product. Phases 0–3 are the credible
  near-term target; Phases 4–5 are a roadmap, not a quarter.
