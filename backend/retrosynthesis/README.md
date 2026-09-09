# Retrosynthesis service

AiZynthFinder (USPTO-trained MCTS retrosynthesis) behind a FastAPI wrapper.

## Setup

```bash
conda env create -f environment.yml
conda activate retrosynth
```

Pins live in `requirements.txt`. AiZynthFinder 4.x runs its expansion model on
**ONNX Runtime CPU** — no TensorFlow, no GPU, no VRAM. The MCTS search is CPU-bound.

## Download the model data

```bash
download_public_data data/external/aizynthfinder
```

Fetches ~754 MB into `data/external/aizynthfinder/` (gitignored):

| file | size | what |
|---|---|---|
| `zinc_stock.hdf5` | 633M | purchasable building blocks |
| `uspto_model.onnx` | 88M | expansion policy |
| `uspto_filter_model.onnx` | 17M | filter policy |
| `uspto_ringbreaker_model.onnx` | 15M | ringbreaker expansion |
| `uspto_templates.csv.gz` | 3.2M | template library |
| `uspto_ringbreaker_templates.csv.gz` | 368K | ringbreaker templates |

The tool also writes its own `config.yml` there with absolute paths. The repo
uses the root [`config.yml`](../../config.yml) instead, which holds repo-relative
paths resolved at load time — so it stays portable and CWD doesn't matter.

## Run

```bash
uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

The model loads once at startup (~8 s). `/retrosynthesis/health` returns 503 until
it's ready, and reports the load failure if the data files are missing.

## Endpoints

### `POST /retrosynthesis/plan`

```json
{"smiles": "CC(=O)Oc1ccccc1C(=O)O", "top_n": 3, "iteration_limit": 100, "include_images": false}
```

| field | default | notes |
|---|---|---|
| `smiles` | required | rejected with 400 if RDKit can't parse it |
| `top_n` | 5 | 1–25 |
| `iteration_limit` | 100 | 1–500; **400 above 500** so a request can't hang |
| `include_images` | false | base64 PNG per route |

```bash
curl -X POST localhost:8000/retrosynthesis/plan \
  -H 'Content-Type: application/json' \
  -d '{"smiles": "CC(=O)Oc1ccccc1C(=O)O", "top_n": 3}'
```

The `Content-Type: application/json` header is required — curl's `-d` otherwise
sends form encoding and you'll get a 400.

```json
{
  "target_smiles": "CC(=O)Oc1ccccc1C(=O)O",
  "is_solved": true,
  "search_time_seconds": 5.49,
  "iterations_used": 100,
  "iteration_limit": 100,
  "solved_routes_found": 12,
  "routes_returned": 3,
  "routes": [
    {
      "route_id": 0,
      "state_score": 0.9976287063411217,
      "scores": {"state score": 0.9976287063411217},
      "number_of_reactions": 1,
      "tree": {
        "molecule_smiles": "CC(=O)Oc1ccccc1C(=O)O",
        "is_stock_available": false,
        "reactions": [
          {
            "reactants": [
              {"molecule_smiles": "CC(=O)OC(C)=O",   "is_stock_available": true, "reactions": []},
              {"molecule_smiles": "O=C(O)c1ccccc1O", "is_stock_available": true, "reactions": []}
            ],
            "template_used": 40152,
            "template_smarts": "[C;D1;H3:2]-[C;H0;D3;+0:1](=[O;D1;H0:3])-[O;H0;D2;+0:4]-[c:5]>>...",
            "score": 0.7261999845504761,
            "reaction_smiles": "[C:1]([CH3:2])(=[O:3])[O:4][cH3:5]>>CC(=O)O[C:1]([CH3:2])=[O:3].[O:4][cH3:5]",
            "classification": "0.0 Unrecognized"
          }
        ]
      },
      "image_png_base64": null
    }
  ]
}
```

`tree` is recursive: every entry in `reactants` is itself a molecule node with its
own `reactions`, bottoming out at stock compounds with `reactions: []`.

### `GET /retrosynthesis/health`

```json
{"status": "ready", "model_loaded": true, "load_time_seconds": 7.8,
 "config": "...", "default_iteration_limit": 100, "max_iteration_limit": 500}
```

## Route images

Pass `include_images: true` and each route carries `image_png_base64` — the PNG
AiZynthFinder draws itself (green frame = in stock, orange = not). Rendered only
for the routes actually returned.

Done this way rather than as a `GET /plan/{route_id}/image` endpoint because that
would need the server to hold every planned route in memory keyed by id, with
eviction, and it would break the moment you run more than one uvicorn worker.
Add it if you later want to fetch images without re-planning.

## Known limitations

- **Only solved routes are returned.** If nothing solves within the budget you get
  `is_solved: false` and `routes: []`. This is deliberate: an unsolved search still
  produces top-scoring fragments that look confident and are chemically wrong.
  Ibuprofen at the default 100 iterations proposed acetone + a primary alcohol as
  precursors to a carboxylic acid, and by step 5 the isobutyl group had vanished
  from the skeleton entirely.
- **The default budget is not enough for every molecule.** Aspirin and paracetamol
  solve in 2–6 s. Ibuprofen needs `iteration_limit: 500` and ~95 s. Latency varies
  by more than an order of magnitude per target, which is why `search_time_seconds`
  and `iterations_used` are in every response.
- **Searches are serialised.** One AiZynthFinder instance behind a lock, since it
  keeps per-search state on the instance. Concurrent requests queue. Each extra
  copy would cost ~1 GB of RAM.
- **USPTO-derived chemistry only.** Templates come from US patent reactions up to
  ~2019. No proprietary routes, no post-2019 methodology, nothing novel. It
  recovers textbook syntheses well and will not invent new chemistry.
- **"In stock" means present in the ZINC file**, not that it's cheap, available at
  the scale you need, or from a supplier you use.
- `classification` reads `"0.0 Unrecognized"` throughout — no reaction
  classification model is loaded, and none ships with the public data.
- **Scores are search heuristics, not yield or feasibility predictions.**
  `state_score` reflects how completely the route bottoms out in stock; the
  per-reaction `score` is the expansion policy's probability for that template.
  Neither says the reaction will work in a flask.

## Sanity check

```bash
python scripts/test_retrosynthesis.py   # 3 known drugs, flags degenerate routes
pytest                                   # API tests
```
