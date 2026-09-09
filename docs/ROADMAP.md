# Drug Discovery Platform — Roadmap

Local drug discovery platform. Three backend modules + a React frontend, all running locally. No personal data, no deployment, no authentication.

**Status as of 10 Sep 2026:** 3 of 3 planned modules built and working. 67 backend tests passing. Frontend covers 2 of 3 modules.

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
| Structure editor + 3 action tabs | frontend on :5173 | Working |

**To start everything:** `docker compose up -d` → `uvicorn backend.api.main:app --port 8000` → `npm run dev --prefix frontend`

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

## Frontend ✅ (partial)

- [x] React 19 + Vite 8 + TypeScript
- [x] Ketcher 3.18 structure editor, two-way synced with a SMILES field
- [x] Represent tab
- [x] Retrosynthesis tab with live elapsed timer + advanced iteration limit
- [x] Search tab (exact / similarity / substructure sub-tabs)
- [x] Error handling: invalid SMILES inline, backend-down banner
- [ ] **QSAR tab — not built.** The API is live but has no UI.

**Working notes**

- Ketcher needs three Vite shims (`events` polyfill, `global`, `process.env`) — all documented in `vite.config.ts`.
- Ketcher's SMILES output is not canonical; the backend owns canonical form.

---

## To do

### Next up

- [ ] **Add a QSAR tab to the frontend** — smallest gap between built and usable. `GET /predict/properties` already returns everything needed to populate a dropdown.
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
| 8000 | FastAPI |
| 5434 | Postgres (5432/5433 taken by other projects) |
