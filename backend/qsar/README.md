# QSAR property prediction

Aqueous solubility from structure. First property; the design is registry-driven so
a second one is configuration, not a new endpoint.

> **These predictions are not experimentally validated.** Nothing here has been
> checked against a measurement made in this lab. The served model has a held-out
> scaffold-split RMSE of **0.99 log units** — roughly a factor of 10 in mol/L. This
> is a working pipeline, not a working predictor. Do not use it to make a decision
> you would not also make by guessing from logP.

## Dataset

ESOL / Delaney aqueous solubility, from DeepChem's MoleculeNet copy:

```
https://raw.githubusercontent.com/deepchem/deepchem/master/datasets/delaney-processed.csv
```

The usual S3 URL (`deepchem.data.s3-us-west-1.amazonaws.com`) fails TLS
verification — the dotted bucket name breaks the wildcard cert — and path-style
404s. `python scripts/download_esol.py` fetches the working one.

```
rows read       1128
parsed          1128   (0 rejected; canonicalized via backend/molrepr/service.py)
unique          1117   (deduplicated on canonical SMILES, labels averaged)
label range     -11.60 .. 1.58, mean -3.05
```

**The file has two solubility columns.** `ESOL predicted log solubility in mols per
litre` is Delaney's own *model output*; `measured log solubility in mols per litre`
is the experimental value. We train on `measured`. Training on the predicted column
would fit a model to another model's guesses — it inflates metrics and teaches
nothing, and the column ordering makes it easy to grab by accident.

### Deduplication

Eleven structures appeared more than once under different names. They are averaged
rather than dropped, because the same structure in both train and test leaks — and
a scaffold split cannot catch it, since identical structures share a scaffold by
definition. Spreads are logged per pair on every load, so a future dataset with
disagreeing duplicates is visible rather than silently averaged:

```
1.030  mannitol / Sorbitol                     [0.06, 1.09]   <-- SPREAD
0.242  Epiandrosterone / Androsterone          [-4.16, -4.402]
0.190  Amobarbital / 5-Ethyl-5-(3-methylbutyl)barbital
0.100  eucalyptol / 1,8-Cineole
0.062  probarbital / 5-Ethyl-5-isopropylbarbituric acid
0.040  Metronidazole / Metranidazole
0.000  x5  (2-bromonaphthalene, Dialifor, phenobarbital, DDD, Guaiacol)
```

**Mannitol and sorbitol are a known irreducible error.** They are genuinely
different molecules — stereoisomers differing at one hydroxyl — but ESOL's SMILES
omit stereochemistry for both, so they canonicalize to one structure carrying labels
1.03 log units apart. Averaging leaves a **~0.5 log unit floor on that row that no
model can beat**. This is the same stereochemistry blindness documented in
[../molrepr/README.md](../molrepr/README.md); solubility datasets rarely expose it,
but biological activity data will, and far more often.

## Splits

Scaffold (Bemis-Murcko) is the honest split. Random is reported alongside only to
show the size of the gap.

```
scaffold  train 893 | val 111 | test 113 | train/test scaffold overlap    0
random    train 893 | val 111 | test 113 | train/test scaffold overlap   21
```

That **overlap count is the leakage made concrete**: a random split puts 21
scaffolds on both sides of the boundary, so the test set is partly molecules whose
close analogues the model already trained on. Any published ESOL R² near 0.9 is
almost certainly a random split. Do not compare this module's 0.79 against it.

## Results

All test-set numbers. Every model saw the identical partition — chemprop reads a
`split` column exported from the same code, not its own re-split.

| features | model | scaffold RMSE | scaffold R² | random R² | gap |
|---|---|---|---|---|---|
| Morgan fingerprint | Random Forest | 1.765 | 0.344 | 0.706 | 0.362 |
| Morgan fingerprint | XGBoost | 1.700 | 0.392 | 0.735 | 0.343 |
| RDKit descriptors | Random Forest | 1.168 | 0.713 | 0.888 | 0.175 |
| RDKit descriptors | XGBoost | 1.094 | 0.748 | 0.882 | 0.134 |
| **fingerprint + descriptors** | **XGBoost** | **0.988** | **0.794** | 0.917 | 0.123 |

| D-MPNN (chemprop, 50 epochs) | — | 1.004 | 0.788 | 0.892 | 0.104 |

Two findings worth keeping:

**Five descriptors beat 2048 Morgan bits, by a lot** (R² 0.392 → 0.748). Solubility
is driven by whole-molecule polarity and surface area, not by which local
substructures are present, so substructure bits are close to the wrong question.
Delaney's original model used exactly this kind of physicochemistry for the same
reason.

**Descriptors also shrink the scaffold/random gap** (0.343 → 0.134). Fingerprints
memorize substructures and collapse on unfamiliar scaffolds; descriptors generalize
across them because logP and TPSA mean the same thing on chemistry the model has
never seen.

### Chemprop is a comparison artifact, not a dormant model

**It is not served, not loaded by the API, and nothing imports it.** It exists as
evidence that the baseline is a measured choice rather than untested laziness, and
as the natural thing to retry on a larger dataset. If you are looking for the
production model, it is the XGBoost pickle in `models/qsar/solubility/baseline.pkl`.

**RMSE 1.004 / R² 0.788 versus the baseline's 0.988 / 0.794.** Essentially tied, and
marginally worse. On 893 training molecules a D-MPNN does not have enough data to
learn a representation better than logP + TPSA + MW + a fingerprint. This is a real
outcome, not a tuning failure to paper over — and it is the reason the baseline is
what gets served.

```
device      NVIDIA GeForce RTX 5060 Laptop GPU, sm_120, CUDA 12.8
wall clock  19.0s (scaffold) / 20.0s (random), 50 epochs, batch size 50
```

GPU was genuinely used. Getting there needed `torch 2.11.0+cu128` from PyTorch's
CUDA index — Blackwell is `sm_120`, and PyPI's default Windows wheel is
`torch+cpu` with no CUDA at all.

### Chemprop lives in a separate conda env

`chemprop` requires **rdkit ≥ 2026 and networkx ≥ 3**, which would break
AiZynthFinder's `rdkit<2024.0.0` / `networkx<3.0` pins *and* the Postgres cartridge
version coupling documented in [docker-compose.yml](../../docker-compose.yml). A
`pip install --dry-run` confirmed it would have upgraded rdkit 2023.9.6 → 2026.3.5
in place.

```bash
conda activate qsar-chemprop     # chemprop, torch+cu128, rdkit 2026
python scripts/train_chemprop.py --split scaffold --epochs 50
```

The two environments never import each other; the split crosses between them as
CSV. **The served API does not load chemprop** — it serves the baseline, which is
both better here and free of the dependency conflict.

## Applicability domain — calibrated, and it does not work

A regression model asked about unfamiliar chemistry still returns a
confident-looking number. `applicability` was meant to be the signal that says not
to trust it. **It is not that signal, and the response now says so.**

```json
"applicability": {
  "max_train_similarity": 0.4,
  "structurally_familiar": true,
  "threshold": 0.4,
  "nearest_training_smiles": ["COC(=O)c1ccccc1C(=O)OC", "CC(=O)c1ccccc1", "..."],
  "note": "max_train_similarity measures structural novelty only. Calibration ... found no relationship between it and prediction error (Pearson -0.074, permutation p=0.44) ..."
}
```

Max Tanimoto over stereo-blind Morgan fingerprints against the 1,004 molecules the
served model fit on, scored through `backend.molrepr.search.rank_by_similarity` —
the same function molecule search uses. That needed a small refactor: the Tanimoto
scan was split out of `similarity_search` so it accepts an arbitrary
`(payload, fingerprint)` list, not just database rows. One Tanimoto implementation
in this repo, not two.

### What calibration found

`python -m backend.qsar.calibrate` bins the 113 held-out test molecules by their
similarity to training and reports actual error per bin:

```
  similarity bin     n    RMSE     MAE  max err
         0.0-0.3    34   1.037   0.788    2.740
         0.3-0.4    30   0.893   0.672    2.114
         0.4-0.5    25   0.896   0.690    2.169
         0.5-1.0    24   1.008   0.704    3.100

Pearson(similarity, |error|) = -0.074   permutation p = 0.440

 threshold   ratio   95% CI (bootstrap)   verdict
      0.25    1.17   [0.77, 1.62]         includes 1.0 -> no effect
      0.30    1.11   [0.76, 1.52]         includes 1.0 -> no effect
      0.35    0.98   [0.69, 1.34]         includes 1.0 -> no effect
      0.40    1.02   [0.75, 1.45]         includes 1.0 -> no effect
      0.50    0.94   [0.64, 1.76]         includes 1.0 -> no effect
```

**There is no measurable relationship between max-Tanimoto similarity and
prediction error.** Error does not rise as similarity falls; the most-similar bin
(0.5–1.0) has essentially the same RMSE as the least-similar. The correlation is
indistinguishable from shuffled data (p = 0.44), and every candidate threshold's
below/above RMSE ratio has a 95% bootstrap CI spanning 1.0. No threshold is
supported by the data — including the 0.4 that was originally guessed.

Checking whether the *featurization* explains it — the plausible story being that
descriptors transfer across scaffolds so structural novelty stops mattering — the
answer is no, and the sign runs the wrong way:

| model | Pearson(sim, \|err\|) | RMSE at sim<0.3 | RMSE at sim>=0.5 | ratio |
|---|---|---|---|---|
| baseline (fp+desc) | -0.074 | 1.037 | 1.008 | 1.03 |
| descriptors | -0.003 | 1.080 | 1.129 | 0.96 |
| fingerprint | **+0.130** | 1.354 | 1.812 | **0.75** |

The fingerprint-only model is *more* accurate on unfamiliar molecules than familiar
ones. Whatever max-Tanimoto captures here, it is not reliability.

### What the field means now

`in_domain` was renamed to **`structurally_familiar`**. The old name asserted the
prediction was trustworthy; the calibration does not support that claim, and a
field name is the part of an API people actually read. The response carries a
`note` stating the finding, and the honest source of expected error is
`model_performance.test_rmse`, plus the conformal `prediction_interval` below.

Aspirin sits at exactly 0.400 and is flagged familiar only because the comparison is
`>=`. With no calibrated threshold behind it, that boundary is arbitrary — treat it
as a novelty marker, not a verdict.

Out-of-domain molecules still get a number back: cisplatin returns
-3.07 log10(mol/L), meaningless for a platinum complex whose nearest training
neighbours are chloroform and dichloromethane. The flag is still worth having as a
*novelty* signal for exactly that case — it just cannot promise more.

### If a real reliability signal is needed later

Options that actually estimate per-prediction error, in rough order of effort:
Random Forest / XGBoost per-tree prediction variance; conformal prediction
(distribution-free intervals with coverage guarantees, and chemprop 2.3 ships
`--conformal-alpha`); or an ensemble trained on different seeds. All of them
estimate error directly rather than using structural distance as a proxy for it.

## Prediction intervals (split conformal)

Every prediction carries a calibrated interval. This replaced quoting one global
RMSE for every molecule.

```json
"prediction_interval": {
  "lower": -3.5216, "upper": -0.8749,
  "alpha": 0.1, "nominal_coverage": 0.9,
  "method": "split-conformal/plain",
  "empirical_coverage": 0.8142,
  "n_calibration": 111
}
```

### Coverage is measured, not nominal — and it undershoots

**Use this table to pick alpha, not the label.** Measured on the untouched
scaffold test set (113 molecules) for the served `baseline` model:

| alpha | nominal | **measured** | 95% CI | mean width |
|---|---|---|---|---|
| 0.05 | 95% | **92%** | 86–96% | 3.24 |
| 0.10 | 90% | **81%** | 73–88% | 2.65 |
| 0.20 | 80% | **68%** | 59–76% | 1.99 |

Ask for `alpha: 0.05` if you want roughly 90% real coverage. The same mapping is
served from `GET /predict/properties` as a `coverage` list per model, so a client
can choose by measured coverage without hardcoding this table.

**Coverage is itself an estimate, from 113 test molecules.** The Wilson intervals
above are wide, and **adjacent rows overlap** — 0.05 (86–96%) and 0.10 (73–88%)
are not cleanly separated. Read this table as a lookup for choosing alpha, not as
evidence that one alpha is significantly better covered than the next. The same
caution applies to the plain-versus-normalized comparison below: plain's 92% and
normalized[kNN]'s 88.5% have overlapping CIs at this n. The decision rests on
plain being *both* higher and narrower, plus the argument about varying widths -
not on that gap being statistically established.

Two decimal places, not four: reporting 0.8142 on 113 molecules asserts a
precision the sample cannot support.

**This mapping does not transfer.** It is a property of *this* model on *this*
scaffold split of *this* dataset — not a general correction factor for conformal
prediction. When Tox21 lands, its coverage must be measured from scratch. Do not
reuse these numbers as an adjustment for another property.

### Why it undershoots: exchangeability

Conformal's guarantee assumes calibration and test points are exchangeable. A
scaffold split deliberately breaks that: calibration comes from larger, commoner
scaffold groups and test is the rarest scaffolds, so test is genuinely harder
(test MAE 0.764 vs calibration 0.639) and a quantile learned on calibration is
too small.

Verified rather than assumed — the same procedure under a random split, where
exchangeability does hold:

```
scaffold  alpha=0.10  ->  0.814      random  alpha=0.10  ->  0.903
```

Random lands essentially on nominal. **The undercoverage is the split, not a bug
in the implementation** — and the scaffold split is still the right choice, since
it is the honest measure of generalisation. The interval simply has to be read
with its measured coverage.

### What conformal does NOT give you

- **Coverage is marginal, not conditional.** A 90%-labelled interval means ~81%
  of predictions *across the test distribution* land inside. It does **not** mean
  81% confidence for the molecule in front of you. Some regions of chemical space
  are covered far better than others, and this gives you no way to tell which.
- **Not a molecule-specific difficulty signal.** Plain conformal's width is
  identical for every input, by construction (see below).
- **No guarantee off-distribution.** For a molecule unlike anything in
  calibration, the interval carries no assurance at all — that is the same
  territory the `structurally_familiar` flag reports on.

### Plain was served, not normalized

Normalized (locally adaptive) conformal was implemented and rejected. Both
difficulty estimates were tried and neither produced widths that track error:

| difficulty estimate | Pearson(width, abs error) | p | 95% CI |
|---|---|---|---|
| RF per-tree dispersion | +0.110 | 0.251 | [−0.114, +0.336] |
| kNN out-of-fold residual | +0.003 | 0.974 | [−0.177, +0.185] |

Permutation test and bootstrap CIs, the same treatment the applicability
calibration got. Both span zero.

Two reasons plain wins:

1. **Plain dominates on both axes.** Plain at alpha=0.05 gives 92.0% coverage at
   width 3.24; normalized[kNN] at alpha=0.10 gives 88.5% at width 3.56. Better
   coverage *and* narrower. If you want ~90% real coverage, plain gets there more
   efficiently — you just ask at a different alpha.
2. **A varying width is itself a claim.** It tells the reader "this molecule is
   harder", and that claim is false here: widths vary but are uncorrelated with
   error, so two similar compounds would get different intervals for no reason.
   Plain's constant width is honest about knowing nothing molecule-specific.
   Noise wearing the costume of a signal is worse than saying nothing.

On the difficulty estimator: **Random Forest per-tree dispersion, not XGBoost's.**
Boosting builds additive corrections, so spread across its trees measures how much
the fit kept correcting itself, not predictive uncertainty. Random Forest trees are
independent estimates of the target, so their spread is a genuine dispersion
measure. The kNN variant used **out-of-fold** (5-fold CV) training residuals;
in-sample residuals would only have measured memorisation.

### The calibration set must never be in the served model's training data

**This is the invariant, and it is enforced in `train.py`, not by convention.**

An earlier version fit the served model on train+val and would have calibrated on
val. Residuals there are in-sample and tiny:

```
90th percentile |residual|:   val (in-sample) 0.225   vs   test (held out) 1.519
```

Intervals would have been **6.8x too narrow** — and nothing about the output would
have looked wrong. Every number stays plausible; only measuring coverage on a
genuinely held-out set exposes it. Whoever adds the next property hits this same
fork, so `train.py` now fits on **train only**, uses **val** solely for
calibration, and touches **test** only to measure coverage.

Cost of the invariant: 111 fewer training molecules, test RMSE 0.964 -> 0.988.

### Recalibrating

```bash
python -m backend.qsar.train        # refits, recalibrates, re-measures coverage
python -m backend.qsar.conformal    # the plain vs normalized comparison
```

Coverage numbers in the artifact are written at train time from the test set, so
they cannot drift from the model being served.

## API

### `POST /predict/property`

```bash
curl -X POST localhost:8000/predict/property -H 'Content-Type: application/json' -d '{"smiles": "CC(=O)Oc1ccccc1C(=O)O", "property": "solubility"}'
```

```json
{
  "property": "solubility",
  "predicted_value": -2.1879,
  "units": "log10(mol/L)",
  "model_used": "baseline",
  "model_performance": {
    "test_rmse": 0.9884, "test_mae": 0.7642, "test_r2": 0.7936,
    "split": "scaffold",
    "note": "Held-out scaffold-split metrics. The prediction should be read as roughly +/- 0.99 log10(mol/L)."
  },
  "applicability": { "max_train_similarity": 0.4, "in_domain": true, "threshold": 0.4, "nearest_training_smiles": [...] }
}
```

`model` is optional (`baseline`, `descriptors`, `fingerprint`); omit it for the
property's default. `alpha` is optional (default 0.1) and must be one of the
**calibrated** values — an uncalibrated alpha returns 400 rather than being
interpolated, because an interpolated interval has a coverage nobody measured. **Every prediction carries its own error bar** — a number that
arrives without its uncertainty gets treated as more precise than it is, and at
±0.99 log units this one especially should not be.

### `GET /predict/properties`

Lists properties, their units, and every model with its metrics, so a frontend can
populate a dropdown and warn about accuracy without hardcoding anything.

Invalid SMILES and unknown property/model names return **400** with a clear message.

## Adding a property

1. Build a dataset loader beside `dataset.py`.
2. Train and pickle estimators + metrics + training SMILES, as `train.py` does.
3. Register a `PropertySpec` in `service.py`.

No new route. `POST /predict/property` reads the registry.

## Retraining

```bash
conda activate retrosynth
python scripts/download_esol.py
python -m backend.qsar.baseline        # full split/featurization comparison
python -m backend.qsar.train           # writes models/qsar/solubility/baseline.pkl
python -m backend.qsar.calibrate       # applicability calibration
python scripts/export_qsar_split.py    # CSVs for the chemprop env
```

The served artifact is refit on scaffold train+val (1,004 molecules); the test set
stays untouched, so the metrics stored in the artifact — and returned with every
prediction — remain genuinely held out. `models/` is gitignored.

## Limitations

- **Not experimentally validated.** See the note at the top.
- **±0.99 log units** on novel scaffolds. Useful for coarse ranking, not for
  deciding a formulation.
- **1,117 molecules, 269 scaffolds.** Small, and skewed toward the small neutral
  organics and agrochemicals in Delaney's set. Charged species, organometallics and
  large biologics are absent — hence the applicability flag.
- **Aqueous solubility at ~25 °C only.** No pH, no buffer, no salt form, no
  polymorph. Real solubility depends on all of them.
- **Labels are averaged across duplicate measurements**, with a known ~0.5 log unit
  floor on the mannitol/sorbitol row.
- **The applicability flag does not predict error.** Calibration measured this
  directly: no relationship, p = 0.44. It reports structural novelty, which is
  real and useful, but a familiar molecule is not thereby a reliable prediction.
  See the conformal section for what replaced it.
