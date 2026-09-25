# Route Correctness: Step-Level Baseline

The route benchmark ([route-benchmark.md](route-benchmark.md)) measures whole
routes against patent routes on patent targets. It cannot say whether a single
step we show is chemically right. This is the baseline for that, recorded
before any change aimed at route correctness, so later changes are judged
against it.

```bash
# needs the forward-model service: venv-t5, uvicorn backend.forward_model_service.main:app --port 8435
python scripts/audit_route_steps.py --set hard --out data/external/route-audit/audit_hard.json
python scripts/audit_route_steps.py --set n1 --sample 100 --out data/external/route-audit/audit_n1.json
```

Per-step results, with structures, are in `data/external/route-audit/`
(gitignored: the hard set is ChEMBL-derived).

---

## Method

For each target, the **first route a user sees** (production settings,
depth 10). Every step gets one label:

| Label | Meaning |
| --- | --- |
| **verified** | a direct experimental precedent exists: this exact reaction, on these substrates, was run and reported (ORD or a US patent) |
| **flagged** | no direct precedent, and the forward model (ReactionT5), given the step's reactants, does not predict its product in the top 5 |
| **unverified** | neither: no precedent, no objection |

On PaRoutes targets a verified step can be one of the target's own
reference-route steps, from the patent the target came from. That is real
evidence the reaction works, but it overstates what a novel target would get,
so it is counted separately.

---

## Baseline, 26 Sep 2026

| | Complex drugs (primary) | PaRoutes n1 |
| --- | --- | --- |
| Routes (solved targets) | 23 of 60 | 97 of 100 |
| Steps | 58 | 348 |
| **Verified** | **6.9%** (4) | 32.5% (113), of which 91 are the target's own reference steps |
| Verified independently of the target's own patent | 6.9% | **6.3%** (22) |
| **Flagged** | **24.1%** (14) | 18.4% (64) |
| Unverified | 69.0% | 49.1% |
| Routes with every step verified | 0 | 20 (mostly reproductions of the patent route) |
| **Routes containing a flagged step** | **43%** (10) | 42% (41) |

**About 1 step in 15 that we show has direct experimental support**, on both
sets once the n1 targets' own patents are set aside. Nearly half of the routes
contain a step the forward model objects to. Almost every unverified step has
a similar precedent above the 0.40 badge floor (40 of 40, 167 of 171), which is
support for the kind of chemistry, not for the step.

---

## The flags are noisy

**The forward model rejects reactions known to work.** Of the 113 verified
steps on n1, it flagged 17 (15%) as not producing their product, and it returned
unusable output for 15% of all steps. Its calibration precision of 0.91 does not
hold on real route steps.

A first read of the 14 flagged complex-drug steps, which is **not a chemist's
review**:

| Read | Steps | Examples |
| --- | --- | --- |
| Probably a real problem | 5 | SNAr on an unactivated aryl fluoride (reboxetine); amide coupling with an unprotected amine on the acid partner (ceforanide); alkylation and ketal deprotection merged into one step (lumateperone); POCl₃ with a free hydroxyl present (abacavir); a questionable transesterification (tetramethrin) |
| Probably a false alarm | 5 | TsOH + SOCl₂ → TsCl; ketalization; triazole N-alkylation; epoxy-ketone reduction; morpholine ring closure (reboxetine) |
| Unclear | 4 | two inavolisib ring steps; riboflavin condensation; biperiden making acetonitrile, which is a stock artefact |

So a flag means "look at this", not "this is wrong". The flagged list in
`data/external/route-audit/` is the review list for a chemist, and a chemist's
review of it is the only ground truth this baseline lacks.

---

## What this does NOT establish

- **That unverified steps are wrong, or verified ones right on these
  substrates.** A precedent shows the reaction was run, not that it transfers.
- **Anything about routes we do not show.** Unsolved targets have no route to
  audit: 37 of the 60 complex drugs.
- **A calibrated error rate.** Without a chemist's labels, the flags are
  a noisy screen with a measured false-alarm floor (15% of known-good steps),
  not a measurement of wrong chemistry.
