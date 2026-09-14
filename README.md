# RamChems

A local drug discovery platform. Three backend modules and a frontend for testing them.

| Module | Docs |
|---|---|
| Retrosynthesis (AiZynthFinder) | [backend/retrosynthesis/README.md](backend/retrosynthesis/README.md) |
| Molecular representation + search (RDKit + Postgres cartridge) | [backend/molrepr/README.md](backend/molrepr/README.md) |
| QSAR property prediction (solubility) | [backend/qsar/README.md](backend/qsar/README.md) |
| Reaction conditions + literature evidence (ORD) | [docs/reaction-condition-intelligence.md](docs/reaction-condition-intelligence.md) |
| Retrieval benchmark (is "similar" actually relevant?) | [docs/retrieval-benchmark.md](docs/retrieval-benchmark.md) |
| Frontend (React + Vite + Ketcher) | [frontend/README.md](frontend/README.md) |

Every dataset, its licence, and what that licence permits:
[docs/data-provenance.md](docs/data-provenance.md). Read it before shipping —
the reaction-condition data is **CC-BY-SA-4.0**, which is copyleft.

## Running everything locally

Three things must be up, in this order. Use three terminals.

**1. Database** (repo root)

```bash
docker compose up -d
```

Postgres with the RDKit cartridge on `127.0.0.1:5434`. First run only, populate it:

```bash
conda activate retrosynth
python scripts/download_chembl.py
python scripts/ingest_molecules.py
```

**2. Backend API** (repo root)

```bash
conda activate retrosynth
uvicorn backend.api.main:app --port 8434
```

Takes ~8 s to start — it loads the AiZynthFinder expansion model and the ZINC stock
once, at startup. `GET /retrosynthesis/health` returns 503 until it is ready.

First run only, download the retrosynthesis model data (~754 MB) and train the
QSAR models (~30 s):

```bash
download_public_data data/external/aizynthfinder
python scripts/download_esol.py
python -m backend.qsar.train
```

Optional, for reaction conditions on the arrow — ingest Open Reaction Database
datasets into the evidence index (separate conda env; see
[docs/reaction-condition-intelligence.md](docs/reaction-condition-intelligence.md)):

```bash
conda activate ord-ingest
python scripts/ingest_ord.py --list
python scripts/ingest_ord.py --dataset <id>
```

Without this the platform works exactly as before; steps simply report
"no verified evidence" rather than inventing conditions.

**3. Frontend**

```bash
npm install --prefix frontend
npm run dev --prefix frontend
```

Then open <http://localhost:5173>.

## Checking it works

Paste `CC(=O)Oc1ccccc1C(=O)O` into the SMILES field (or click the **Aspirin**
quick-load button) and:

- **Represent** returns canonical SMILES `CC(=O)Oc1ccccc1C(=O)O`, InChIKey
  `BSYNRYMUTXBXSQ-UHFFFAOYSA-N`, MW 180.159.
- **Retrosynthesis** solves in ~3 s: acetic anhydride + salicylic acid.
- **Search → Similarity** returns aspirin at 1.0000, then benorilate 0.5128 and
  salicylic acid 0.4483.
- `POST /predict/property` returns solubility -2.19 log10(mol/L) with an
  applicability flag.

The header badge shows whether the backend is reachable, so a forgotten step 2 is
obvious immediately.

## Ports

| Port | What | Note |
|---|---|---|
| 5173 | Vite dev server | fixed; the backend CORS allow-list names it |
| 8434 | FastAPI | **not** 8000 — that is every framework's default and is contended on a multi-project machine. Override in `frontend/.env` via `VITE_API_BASE` |
| 5434 | Postgres | 5432/5433 were already taken by other projects on this machine |

Postgres also holds the reaction-evidence index and its cache — no Redis, no
second datastore.

## Tests

```bash
conda activate retrosynth
pytest                                  # 197 backend tests
python scripts/test_retrosynthesis.py   # 3-molecule sanity check, exits 1 by design
npx tsc -b --noEmit --project frontend  # frontend typecheck
```

`scripts/test_retrosynthesis.py` exits non-zero because it flags ibuprofen as
unsolved at the default iteration limit. That is the script doing its job as a
review tool — do not wire it into CI as-is.

## Environments

Three conda envs, deliberately separate:

| env | holds | why |
|---|---|---|
| `retrosynth` | everything served by the API | rdkit 2023.09.6, networkx 2.x, pinned by AiZynthFinder and coupled to the Postgres cartridge |
| `qsar-chemprop` | chemprop + torch cu128 only | chemprop needs rdkit >= 2026 and networkx >= 3, which would break the above |
| `ord-ingest` | `ord-schema` + pyarrow, for ORD ingestion only | `ord-schema` pins protobuf < 6 and rdkit >= 2026, both of which break the serving env |

They never import each other; QSAR splits cross between them as CSV. The API loads
only the `retrosynth` env. See [backend/qsar/README.md](backend/qsar/README.md).

Python side is the conda env `retrosynth` (Python 3.11); see
[environment.yml](environment.yml) and [requirements.txt](requirements.txt).

The Postgres image tag and the Python `rdkit` pin are **coupled** — both ship RDKit
2023.09 so that Python and the cartridge cannot disagree about canonicalization.
Move them together. See the comment in [docker-compose.yml](docker-compose.yml).
