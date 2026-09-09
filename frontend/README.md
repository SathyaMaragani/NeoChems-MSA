# RamChems frontend

React + Vite + TypeScript UI for visually testing the retrosynthesis and molecular
search backends. Ketcher provides the structure editor.

## Run

```bash
npm install
npm run dev
```

Opens on <http://localhost:5173>. The port is `strictPort: true` on purpose — if
5173 is taken, Vite fails loudly instead of drifting to 5174, which the backend's
CORS allow-list does not cover.

**The backend must be running separately.** From the repo root:

```bash
docker compose up -d
uvicorn backend.api.main:app --port 8000
```

See [../backend/retrosynthesis/README.md](../backend/retrosynthesis/README.md) and
[../backend/molrepr/README.md](../backend/molrepr/README.md) for what each backend
needs (model downloads, database ingestion). The header shows a live
**backend connected / backend not running** badge, polled every 15 s, so a missing
backend is visible rather than showing up as a hung button.

Point at a different API with `VITE_API_BASE`:

```bash
VITE_API_BASE=http://127.0.0.1:9000 npm run dev
```

## Layout

Structure editor on the left, action tabs on the right. The current SMILES is
lifted state in `App.tsx` and shared by all three tabs — draw or paste once, then
switch tabs freely without re-entering the molecule.

Covers **3 of 3** backend modules.

| Tab | Endpoint |
|---|---|
| **Represent** | `POST /molecules/represent` — canonical SMILES, InChIKey, MW, depiction. No database. Start here to confirm the editor is producing what you expect. |
| **Retrosynthesis** | `POST /retrosynthesis/plan` — routes with images, scores, per-step templates. |
| **Search** | `POST /search/exact`, `/search/similarity`, `/search/substructure` |
| **Properties** | `GET /predict/properties`, `POST /predict/property` — solubility with a conformal prediction interval. |

## The Properties tab shows measured coverage, not nominal

Everything selectable is populated from `GET /predict/properties` — properties,
models, and the calibrated alpha list. Nothing is hardcoded, so a new property
appears in the dropdowns without a frontend change.

The confidence selector deliberately labels each option with its **measured**
coverage:

```
alpha = 0.05 - measured 92% coverage
alpha = 0.1  - measured 81% coverage
alpha = 0.2  - measured 68% coverage
```

A dropdown offering "90%" would undo the backend's honesty work: intervals
under-cover their nominal label because of the scaffold split. The measured
figure carries its own 95% CI (n = 113) beside it, because coverage is itself an
estimate.

Two other display choices worth keeping:

- The interval is drawn as a **bar** with the point estimate marked, not just two
  numbers. A +/-1.3 log-unit range reads as abstract in text and obvious as a bar.
- `structurally_familiar` is labelled **"structural similarity to training data"**
  with a caption saying it does *not* predict accuracy. Calibration measured that
  directly and found no relationship (p = 0.44), so presenting it as a confidence
  signal would be false.

The "not experimentally validated" caption is permanent and not dismissible - the
person reading a number in a browser is not the person who read the README.

## Editor and SMILES field

They stay in sync in both directions: typing a SMILES loads it onto the canvas
(debounced 600 ms, so half-typed strings are not pushed at Ketcher), and drawing
updates the field.

**Ketcher's SMILES output is not canonical.** Aspirin drawn on the canvas comes back
as `CC(Oc1c(C(O)=O)cccc1)=O`, not `CC(=O)Oc1ccccc1C(=O)O`. Both are the same
molecule and RDKit canonicalizes them identically — this is not a bug. The canonical
form is whatever the **Represent** tab reports, because the backend owns
canonicalization. The field will visibly rewrite itself when you touch the canvas.

Quick-load buttons for aspirin, ibuprofen and paracetamol sit under the field.

## Retrosynthesis timing

Searches are not spinner-length. The default 100 iterations runs 2–20 s; raising the
limit to 500 under **Advanced** takes about 95 s. The tab shows a live elapsed
counter rather than an opaque spinner, because a 95-second silent wait is
indistinguishable from a hang.

If nothing solves, the tab says so explicitly and names the iteration count, rather
than rendering an empty result area — an unsolved search is a real answer, not an
empty one.

## Substructure searches use the drawn structure as the *pattern*

The **Substructure** sub-tab looks for database molecules that *contain* what you
drew. Drawing aspirin and searching substructure asks "which approved drugs contain
aspirin?" — not "what does aspirin contain". Draw a fragment (a carboxylic acid,
a benzene ring) rather than a whole drug.

## Ketcher under Vite

Three things Ketcher 3.18 needs that its docs do not mention, all in
[vite.config.ts](vite.config.ts):

- `events` npm package installed and aliased. Ketcher imports Node's `events`; Vite
  otherwise substitutes a browser-external stub whose `EventEmitter` is not
  constructable, and the editor renders as a blank box.
- `define: { global: 'globalThis' }`. Ketcher references Node's `global`.
- `define: { 'process.env': {} }`.

Also, `<Editor>` has **no `onChange` prop** in 3.18 (it is a TypeScript error).
Drawing events come from `ketcher.editor.subscribe('change', ...)` on the instance
handed back by `onInit`.

`ketcher-standalone` runs the structure service in-browser via WASM, so there is no
Ketcher server to run.
