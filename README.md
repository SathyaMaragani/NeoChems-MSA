<div align="center">

<img src="frontend/src/assets/logo-icon.png" alt="RamChems" width="120" />

# RamChems

**A local drug discovery platform.**
Draw a molecule, get its canonical representation, search a chemical library,
predict its solubility, and plan a synthesis route — with the literature
evidence behind each step.

<br />

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![Postgres](https://img.shields.io/badge/Postgres%20+%20RDKit-4169E1?logo=postgresql&logoColor=white)
![Tests](https://img.shields.io/badge/tests-252%20passing-success)
![Runs](https://img.shields.io/badge/runs-100%25%20local-informational)

</div>

---

## What's inside

Three backend modules and a frontend for driving them. Every module is
documented on its own; the table is the map.

| Module | What it does | Docs |
|---|---|---|
| **Retrosynthesis** | Plans synthesis routes with AiZynthFinder, scored against a purchasable stock | [README](backend/retrosynthesis/README.md) |
| **Molecular representation + search** | Canonical SMILES, InChIKey, descriptors; similarity and substructure search over a Postgres/RDKit cartridge | [README](backend/molrepr/README.md) |
| **QSAR property prediction** | Aqueous solubility, with an applicability-domain flag rather than a bare number | [README](backend/qsar/README.md) |
| **Reaction conditions + evidence** | Real conditions on the arrow, drawn from the Open Reaction Database and cited | [Docs](docs/reaction-condition-intelligence.md) |
| **Retrieval benchmark** | Asks the uncomfortable question: is "similar" actually *relevant*? | [Docs](docs/retrieval-benchmark.md) |
| **Route benchmark** | Solve rate and route recovery on PaRoutes and complex drugs; reproduces PaRoutes' published numbers | [Docs](docs/route-benchmark.md) |
| **Frontend** | React + Vite, with Ketcher as the structure editor | [README](frontend/README.md) |

Where the project is heading: [docs/ROADMAP.md](docs/ROADMAP.md).

---

## Quick start

Everything runs on your machine. Nothing is sent anywhere.

**Prerequisites** — Docker, conda, and Node 20+. First run also needs ~1 GB of
downloads; see [First-run setup](#first-run-setup) below.

Three things must be up, **in this order**. Use three terminals.

### 1 · Database

```bash
docker compose up -d
```

Postgres with the RDKit cartridge, on `127.0.0.1:5434`.

### 2 · Backend API

```bash
conda activate retrosynth
uvicorn backend.api.main:app --port 8434
```

> [!NOTE]
> Startup takes ~8 s — the AiZynthFinder expansion model and the ZINC stock load
> once, at boot. `GET /retrosynthesis/health` returns **503** until it is ready,
> which is the honest answer rather than a hang.

### 3 · Frontend

```bash
npm install --prefix frontend
npm run dev --prefix frontend
```

Open **<http://localhost:5173>**. The header badge shows whether the backend is
reachable, so a forgotten step 2 is obvious immediately.

---

## First-run setup

<details>
<summary><b>Populate the molecule database</b> — required once</summary>

```bash
conda activate retrosynth
python scripts/download_chembl.py
python scripts/ingest_molecules.py
```

</details>

<details>
<summary><b>Download retrosynthesis models + train QSAR</b> — required once (~754 MB, ~30 s training)</summary>

```bash
conda activate retrosynth
download_public_data data/external/aizynthfinder
python scripts/download_esol.py
python -m backend.qsar.train
```

</details>

<details>
<summary><b>Ingest reaction conditions from ORD</b> — optional</summary>

Adds verified conditions to the arrows in a route. Uses a **separate conda env**
— see [docs/reaction-condition-intelligence.md](docs/reaction-condition-intelligence.md).

```bash
conda activate ord-ingest
python scripts/ingest_ord.py --list
python scripts/ingest_ord.py --dataset <id>
```

Skip it and the platform works exactly as before: steps report
*"no verified evidence"* rather than inventing conditions.

</details>

---

## Checking it works

Paste `CC(=O)Oc1ccccc1C(=O)O` into the SMILES field — or click the **Aspirin**
quick-load button — and expect:

| Action | Expected result |
|---|---|
| **Represent** | canonical SMILES `CC(=O)Oc1ccccc1C(=O)O`, InChIKey `BSYNRYMUTXBXSQ-UHFFFAOYSA-N`, MW 180.159 |
| **Retrosynthesis** | solves in ~3 s → acetic anhydride + salicylic acid |
| **Search → Similarity** | aspirin 1.0000, then benorilate 0.5128, salicylic acid 0.4483 |
| `POST /predict/property` | solubility −2.19 log10(mol/L), plus an applicability flag |

---

## Ports

Deliberately not the defaults — this machine runs several projects at once.

| Port | Service | Why this one |
|---|---|---|
| `5173` | Vite dev server | fixed; the backend CORS allow-list names it |
| `8434` | FastAPI | **not** 8000 — every framework's default, and contended. Override with `VITE_API_BASE` (see [`frontend/.env.example`](frontend/.env.example)) |
| `5434` | Postgres | 5432/5433 were already taken |

Postgres also holds the reaction-evidence index and its cache — no Redis, no
second datastore.

---

## Tests

```bash
conda activate retrosynth
pytest                                  # 252 backend tests
python scripts/test_retrosynthesis.py   # 3-molecule sanity check, exits 1 by design
npx --prefix frontend tsc -b --noEmit frontend  # frontend typecheck
```

> [!WARNING]
> `scripts/test_retrosynthesis.py` exits non-zero on purpose: it flags ibuprofen
> as unsolved at the default iteration limit. That is the script doing its job as
> a review tool — **do not wire it into CI as-is.**

---

## Environments

Three conda envs, deliberately separate. They never import each other; QSAR
splits cross between them as CSV, and the API loads only `retrosynth`.

| Env | Holds | Why it is its own env |
|---|---|---|
| `retrosynth` | everything served by the API | rdkit 2023.09.6 and networkx 2.x, pinned by AiZynthFinder and coupled to the Postgres cartridge |
| `qsar-chemprop` | chemprop + torch cu128 only | chemprop needs rdkit ≥ 2026 and networkx ≥ 3, which would break the above |
| `ord-ingest` | `ord-schema` + pyarrow, for ORD ingestion only | `ord-schema` pins protobuf < 6 and rdkit ≥ 2026 — both break the serving env |

Python 3.11 throughout: [environment.yml](environment.yml),
[requirements.txt](requirements.txt). Details in
[backend/qsar/README.md](backend/qsar/README.md).

> [!IMPORTANT]
> The Postgres image tag and the Python `rdkit` pin are **coupled** — both ship
> RDKit 2023.09 so Python and the cartridge cannot disagree about
> canonicalization. Move them together. See the comment in
> [docker-compose.yml](docker-compose.yml).

---

## Data and licences

Every dataset, its licence, and what that licence permits is documented in
**[docs/data-provenance.md](docs/data-provenance.md)**.

> [!CAUTION]
> The reaction-condition data from the Open Reaction Database is
> **CC-BY-SA-4.0** — an attribution *and* copyleft licence. ChEMBL is
> CC-BY-SA-3.0. Read the provenance doc before shipping, redistributing or
> selling anything built on this.

Reaction data from the [Open Reaction Database](https://open-reaction-database.org),
licensed CC-BY-SA-4.0; the full licence text travels with the repo at
[docs/licenses/ord-data-LICENSE.txt](docs/licenses/ord-data-LICENSE.txt).
