# Drug Discovery Platform — Roadmap

Local drug discovery platform. Three backend modules + a React frontend, all running locally. No personal data, no deployment, no authentication.

**Status as of 11 Sep 2026:** 4 modules built and working. 196 backend tests passing (100 pre-existing + 96 new). Frontend covers all four.

---

## Currently working

Things you can run right now, end to end.

| Capability | Endpoint | Status |
| --- | --- | --- |
| Retrosynthetic route planning | `POST /retrosynthesis/plan` | Working |
| Model readiness check | `GET /retrosynthesis/health` | Working |
| Canonicalize / InChIKey / depict | `POST /molecules/represent` | Working |
| Exact structure search | `POST /search/exact` | Working |
| Similarity search (Tanimoto) | `POST /search/similarity` | Working |
| Substructure search (SQL cartridge) | `POST /search/substructure` | Working |
| Molecule record + depiction | `GET /molecules/{id}` | Working |
| Solubility prediction | `POST /predict/property` | Working |
| Available properties/models | `GET /predict/properties` | Working |
| Reaction conditions + literature evidence | `POST /retrosynthesis/conditions` | Working |
| Evidence provider + licence status | `GET /retrosynthesis/evidence/status` | Working |
| Structure editor + 4 workspaces | frontend on :5173 | Working |

**To start everything:** `docker compose up -d` → `uvicorn backend.api.main:app --port 8434` → `npm run dev --prefix frontend`

---

## Module 1 — Retrosynthesis ✅

AiZynthFinder MCTS route planning over USPTO-derived templates.

- [x] Conda env `retrosynth`, Python 3.11, pinned in `requirements.txt`
- [x] AiZynthFinder 4.4.1 + RDKit 2023.9.6 verified
- [x] 754 MB public model data downloaded (USPTO expansion, filter, ringbreaker, ZINC stock)
- [x] `config.yml` with repo-relative paths, MCTS defaults untuned
- [x] Standalone sanity script on aspirin / ibuprofen / paracetamol
- [x] Model loads once at startup (~8 s), not per request
- [x] `POST /retrosynthesis/plan` + `GET /retrosynthesis/health`
- [x] `iteration_limit` exposed, capped at 500 server-side (400 above)
- [x] Only *solved* routes returned — no confident-looking unsolved fragments
- [x] Base64 PNG route diagrams
- [x] 11 tests

**Working notes**

- Runs on **CPU only** — AiZynthFinder 4.x uses ONNX Runtime, not TensorFlow. GPU untouched.
- Aspirin solves in ~3 s; paracetamol ~2 s.
- **Ibuprofen does not solve at the default 100 iterations.** Needs `iteration_limit: 500`, which takes ~95 s.
- Searches are serialised behind a lock — concurrent requests queue.

---

## Module 2 — Molecular representation + search ✅

RDKit + PostgreSQL with the RDKit cartridge.

- [x] Postgres 13 + RDKit cartridge 4.4.0 in Docker, port **5434**
- [x] Image tag pinned to `Release_2023_09_3` to match Python RDKit exactly
- [x] Cluster forced to UTF-8 (image defaults to SQL_ASCII)
- [x] Schema: canonical/original SMILES, InChIKey, source, MW, 2 fingerprints, cartridge `mol`, GiST index
- [x] **2,269** ChEMBL 37 approved small molecules ingested (from 3,311 rows, 0 parse failures)
- [x] Parent-compound normalization — salt and free acid score identically
- [x] **95** mineral salts exempted from stripping (lithium vs calcium carbonate stay distinct)
- [x] Two fingerprint columns: stereo-blind for search, chirality-aware for QSAR
- [x] Exact / similarity / substructure search + represent + molecule record
- [x] 27 tests

**Working notes**

- Latency: exact 9 ms, similarity 57 ms, substructure 64 ms.
- Similarity is a linear scan in Python. Fine to ~100k rows, then needs an ANN index.
- **Similarity search is stereo-blind** — enantiomers score 1.0 against each other. Deliberate; exact search does distinguish them.

---

## Module 3 — QSAR property prediction ✅

Aqueous solubility from structure.

- [x] ESOL / Delaney dataset, 1,128 rows → **1,117** unique after dedup
- [x] Trained on the *measured* column, not Delaney's own model output
- [x] Bemis-Murcko scaffold split **and** random split, both reported
- [x] Baselines: RF + XGBoost over fingerprints, descriptors, and both
- [x] Chemprop D-MPNN trained on GPU for comparison
- [x] Applicability domain, reusing the existing Tanimoto code
- [x] Threshold **calibrated empirically** — result was negative, see below
- [x] `POST /predict/property` + `GET /predict/properties`, registry-driven
- [x] 16 tests

**Results (scaffold split, held out)**

| Features | Model | RMSE | R² | Random-split R² |
| --- | --- | --- | --- | --- |
| Morgan fingerprint | XGBoost | 1.700 | 0.392 | 0.735 |
| RDKit descriptors | XGBoost | 1.094 | 0.748 | 0.882 |
| **Fingerprint + descriptors** | **XGBoost** | **0.988** | **0.794** | 0.917 |
| D-MPNN | Chemprop | 1.004 | 0.788 | 0.892 |

**Working notes**

- ⚠️ **This is a working pipeline, not a working predictor.** R² 0.79 / ±0.96 log units ≈ a factor of 9 in mol/L. Nothing is experimentally validated.
- **Five physicochemical descriptors beat 2048 Morgan bits** (R² 0.392 → 0.748), and shrink the scaffold/random gap from 0.34 to 0.13. Descriptors transfer across scaffolds; fingerprint bits are scaffold-bound. *Carry this forward to toxicity and activity — check descriptors before assuming fingerprints.*
- **Chemprop does not beat the baseline** on 893 training molecules. It is kept as a comparison artifact, not served.
- Chemprop lives in a **separate conda env** (`qsar-chemprop`) — it wants rdkit ≥ 2026 / networkx ≥ 3, which would break AiZynthFinder's pins and the cartridge coupling.
- **The applicability flag does not predict error.** Calibration found Pearson −0.074, permutation p = 0.44, and every candidate threshold's bootstrap CI spans 1.0. The field was renamed `in_domain` → `structurally_familiar` because the old name asserted trustworthiness the data doesn't support.

---

## Module 4 — Reaction conditions + literature evidence ✅

Answers "what goes on the arrow?" for each retrosynthesis step, from reported
experiments — or says ⚪ when nothing is known.

- [x] Provider-agnostic domain model (`backend/conditions/schema.py`) — imports neither AiZynthFinder nor ORD
- [x] `LiteratureProvider` / `ConditionPredictionProvider` interfaces + `NullProvider`
- [x] Substrate-specific reaction identity (sha256 of sorted canonical reactants >> products)
- [x] ORD provider: 216,681 reactions, 6 datasets, ingested selectively from the Hugging Face parquet mirror
- [x] V2.2 Transformation-keyed hybrid retrieval — using exact centre hashes, structural transformation fingerprints, and substrate fingerprints to prefilter and score.
- [x] Aggregation across precedents: median + observed range + observation count
- [x] Postgres evidence cache keyed on `(reaction_key, provider, provider_version, dataset_version)` — no Redis
- [x] `POST /retrosynthesis/plan` gains `include_conditions`, **default off**
- [x] Frontend: conditions on the arrow, 4-level evidence badges (DIRECT / SIMILAR / AI-PREDICTED / NO VERIFIED), expandable precedents, route coverage strip
- [x] 96 tests (69 unit + 27 API), including regressions for four defects found in the V2.1 audit
- [ ] **No condition-prediction model** — deliberately. See the working notes.

**Working notes**

- ⚠️ **ORD data is CC-BY-SA-4.0 (ShareAlike)**, not CC-BY as first assumed. Copyleft. Needs a legal decision before commercial release — [docs/data-provenance.md](data-provenance.md).
- ⚠️ **Implemented and tested ≠ experimentally validated.** No condition this system returns has been run in a lab by us.
- **AiZynthFinder templates carry no condition data at all** — five columns, and the reaction SMILES has an empty agents slot. Conditions *must* come from outside the route search; this is not a tuning problem.
- **ORD over Lowe/USPTO** because only ORD records temperature and time. Lowe has agents + yield + patent number but no temperature, no time.
- **Identity is substrate-specific, not template-specific.** One template spans many substrates, so a template-keyed lookup would return precedents for different molecules and label them "experimental".
- **Coverage is thin and that is visible, not hidden.** Aspirin's acetylation is not in the index; it honestly reports ⚪. Most targets will. That is a data-volume problem.
- **`ord-ingest` is a third conda env** — `ord-schema` pins protobuf < 6 against the serving env's 7, and rdkit ≥ 2026 against AiZynthFinder's < 2024. The serving env never imports it.
- **Evidence can never promote an unsolved route.** Ibuprofen stays unsolved at 100 iterations with five precedents on a step; asserted by test.
- ⚠️ **The similar-precedent path was dead on arrival** and nobody could tell. Two stacked bugs in three lines, both swallowed by a bare `except` — see the Known defects section of [reaction-condition-intelligence.md](reaction-condition-intelligence.md). Fixing it changed aspirin from "no evidence" to 6 similar precedents. Every swallow in the subsystem now logs.
- **Retrieval is now benchmarked** against ORD's own `REACTION_TYPE` labels (90,035 labelled reactions, 42 types) — see [retrieval-benchmark.md](retrieval-benchmark.md). All three tuned constants were guessed wrong: the weighting was on the wrong side (substrate matters more than transformation), the 0.35 floor was the single worst setting tested, and the prefilter threshold was too tight. Retuned: P@1 0.434 → 0.516, silent 43% → 28%.
- **The ranking is fine; recall is the problem.** Addressed in V2.2. Stage 1 now offers candidates based on transformation difference, substrate fingerprints, and exact reaction centers. This increases the relevant candidate offering rate compared to V2.1 product-based prefiltering.

---

## Frontend ✅ (partial)

- [x] React 19 + Vite 8 + TypeScript
- [x] Ketcher 3.18 structure editor, two-way synced with a SMILES field
- [x] Represent tab
- [x] Retrosynthesis tab with live elapsed timer + advanced iteration limit
- [x] Search tab (exact / similarity / substructure sub-tabs)
- [x] Error handling: invalid SMILES inline, backend-down banner
- [x] QSAR / Properties workspace
- [x] Reaction conditions on the arrow + evidence badges + route coverage

**Working notes**

- Ketcher needs three Vite shims (`events` polyfill, `global`, `process.env`) — all documented in `vite.config.ts`.
- Ketcher's SMILES output is not canonical; the backend owns canonical form.

---

## To do

### Next up

- [x] ~~**Retrieval benchmark for the evidence layer**~~ — done; see [retrieval-benchmark.md](retrieval-benchmark.md).
- [x] ~~**Transformation-keyed stage 1**~~ — Done in V2.2. Stored the reaction difference fingerprint in Postgres with its own index, so retrieval is keyed on the transformation rather than on product-molecule similarity.
- [ ] **Forward reaction validation** — run each proposed step through a forward model and flag steps whose predicted product is not the target. Directly addresses the class of problem the ibuprofen route exposed.
- [x] **Patent evidence** — USPTO patent grants 1976–Sep 2016 (Lowe, CC0) ingested beside ORD; precedents show the patent number and a Google Patents link built from it. See data-provenance.md §1b.
- [ ] **USPTO patent applications 2001–2016** — figshare blocks the download (HTTP 403, including in a browser); retry or find a mirror.
- [ ] **Toxicity (Tox21)** — the substantive next capability. ~7,800 molecules, 12 assays, classification not regression, ~5% actives. Breaks several assumptions this codebase was built on, in useful ways.
- [ ] **A real reliability signal for QSAR** — conformal prediction or per-tree variance. Structural distance demonstrably isn't one.

### Deferred, with reasons

- [ ] **logP model** — cheap but near-pointless: RDKit's `Crippen.MolLogP` computes it analytically and we already call it as a feature.
- [ ] **FAISS for similarity search** — unnecessary below ~100k rows.
- [ ] **Stereochemistry handling for activity data** — the chirality-aware fingerprint column exists and is populated, unused until activity data lands.
- [ ] **Serve Chemprop** — only worth it if it beats the baseline on a larger dataset.

### Known debt

- [ ] Postgres 13 is EOL (Nov 2025). Pinned deliberately for the RDKit version match; revisit if this leaves local dev.
- [ ] CORS allows `localhost:5173` permissively — must tighten before any non-local deployment.
- [ ] No authentication, no deployment, no CI.
- [ ] `scripts/test_retrosynthesis.py` exits 1 by design (flags ibuprofen). Do not wire into CI as-is.
- [ ] Mannitol/sorbitol collapse to one ESOL row with a ~0.5 log irreducible error.
- [ ] Potassium vs sodium citrate still collapse — citrate exceeds the mineral-salt size threshold.

---

## Environments

| Env | Holds | Why separate |
| --- | --- | --- |
| `retrosynth` | Everything the API serves | rdkit 2023.9.6 + networkx 2.x, pinned by AiZynthFinder and coupled to the Postgres cartridge |
| `qsar-chemprop` | Chemprop + torch cu128 | Needs rdkit ≥ 2026 / networkx ≥ 3, which would break the above |

They never import each other; data crosses as CSV.

**Version coupling to remember:** the Postgres image tag and the Python `rdkit` pin both ship RDKit 2023.09 so Python and the cartridge cannot disagree about canonicalization. **Move them together.** Drift here surfaces as "search returns weird results", not as a clean error.

## Ports

| Port | Service |
| --- | --- |
| 5173 | Vite dev server (fixed — CORS allow-list names it) |
| 8434 | FastAPI |
| 5434 | Postgres (5432/5433 taken by other projects) |
