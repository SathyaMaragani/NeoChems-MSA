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

## Brand assets

The source artwork is `assets/logo.png` (1536x1024, transparent). Web copies are
generated from it, cropped to the artwork's own alpha bounds:

| File | Use |
|---|---|
| `src/assets/logo-mark.png` | 135x88 lockup — sidebar, collapsed rail, loading screen |
| `src/assets/logo-icon.png` | 256x256 padded square |
| `public/favicon.png`, `public/apple-touch-icon.png` | browser icons |

Regenerate them with the snippet in the commit that added them; the 1.5 MB
original is never shipped (the lockup is 16 KB). The background is genuinely
transparent, so the mark sits directly on the dark sidebar with no plate behind
it — do not wrap it in a square.

## Names as well as SMILES

The structure field and the top-bar box both accept a **compound name**. Typing
`glucose` used to fail as an unparseable SMILES, which reads as the search being
broken rather than the wrong input format.

`POST /molecules/resolve` tries to parse the input as a structure first — locally,
no network — and only falls back to a PubChem name lookup for genuine names. The
result is canonicalised through the same service everything else uses, so a
resolved name and a pasted SMILES land on identical strings. When a name is
resolved the UI says so: *Resolved "glucose" to D-Glucose via PubChem*.

Names need network; SMILES never do. An unreachable PubChem produces a clear
message rather than a generic parse error.

## Architecture: one workspace, one state

The sidebar is the only navigation. Whichever feature is selected owns the entire
main area — there is no permanent results panel and no second row of tabs.

**Every workspace shares one editor.** Only the header, the options block and the
top-right action change with the selected feature — Plan retrosynthesis / Search
library / Predict properties / Analyse structure. Each workspace keeps its own
result, so switching tabs does not throw work away.

Each is the same three-state machine:

```
EDITOR ──Plan──> LOADING ──> RESULTS ──Edit molecule──> EDITOR ──Plan──> ...
```

Only one state is on screen at a time. The editor never appears beside results.

**The editor is hidden, not unmounted.** Ketcher boots a WASM structure service,
so remounting it costs seconds — "Edit molecule" would feel like restarting the
app. Hiding it keeps the drawing, the zoom level and the boot.

> One gotcha worth knowing: the `hidden` attribute is overridden by any `display`
> declaration, so `.stage[hidden] { display: none !important }` is load-bearing.
> Without it the editor renders *beside* the results — the exact layout this
> architecture removes.

### Stale results

Results belong to the structure they were computed from. Editing the molecule
marks the state dirty, which:

- changes the primary action to **Plan new retrosynthesis**
- shows a "Target molecule modified" warning in the editor
- warns on the results view that they are out of date
- asks before going back to results, since those routes describe a different
  structure

This is a scientific correctness point, not a nicety: routes shown against the
wrong molecule are wrong.

### Routing

`/retrosynthesis`, `/search`, `/properties`, `/structure` via the History API —
`pushState` on navigate, `popstate` on back. No router dependency for four routes.

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
