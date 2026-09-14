# Data Provenance and Licensing

Every dataset and model this platform depends on, where it came from, and what
its licence permits. Read this before shipping, redistributing or selling
anything built on RamChems.

**This is an engineering record, not legal advice.** The ShareAlike question in
§1 in particular warrants a lawyer's opinion before commercial release.

---

## 1. Open Reaction Database — reaction conditions ⚠️ **CC-BY-SA-4.0**

| | |
| --- | --- |
| Source | `open-reaction-database/ord-data` on Hugging Face (parquet mirror of the GitHub repo) |
| **Pinned revision** | `93475c46949f9218e1dfb6624096025135db2add` (HF, last modified 2026-08-30) |
| Schema library | `ord-schema` 0.8.3 — **Apache-2.0**, verified from the installed `dist-info` (`License-Expression: Apache-2.0`) |
| **Data licence** | **CC-BY-SA-4.0 — Attribution + ShareAlike**, verified from the repo's own `LICENSE` |
| Licence text | checked in at [licenses/ord-data-LICENSE.txt](licenses/ord-data-LICENSE.txt) (427 lines, md5 `bb082061306cc1dc0afcd128f972d344`) |
| Ingested | 216,681 reactions, 6 datasets, 11 Sep 2026 |
| On disk | 35 MB parquet (`data/external/ord/`), 391 MB in Postgres |

### Verification performed

The licence was read from two independent authoritative sources, not inferred:

- `https://raw.githubusercontent.com/open-reaction-database/ord-data/main/LICENSE`
  → first line: `Attribution-ShareAlike 4.0 International`
- the HF dataset card frontmatter → `license: cc-by-sa-4.0`

The HF repo self-describes as a mirror ("Mirror … from
github.com/open-reaction-database/ord-data"), so the two agree by construction.

All six local parquet files were confirmed **byte-identical** to that revision
by comparing their sha256 against the HF LFS oid:

```
ord_dataset-47eaacc46c3a4487bbdf99adb1a15e41.parquet  7e09c98aa540e15d…  MATCH
ord_dataset-488402f6ec0d441ca2f7d6fabea7c220.parquet  d1b658a9ed389f22…  MATCH
ord_dataset-5481550056a14935b76e031fb94b88be.parquet  48508c30931b04b4…  MATCH
ord_dataset-5c9a10329a8a48968d18879a48bb8ab2.parquet  a2ea1363cb895b59…  MATCH
ord_dataset-805ad863feef48579d95d86a728035f4.parquet  31b7c4762145e5da…  MATCH
ord_dataset-d92976309c3a48a3a64a4cf5e7048086.parquet  78c17145099d2945…  MATCH
```

Those same sha256 prefixes are what `ord_ingest_log.dataset_version` stores, so
the database's own version strings are verifiable against the upstream mirror.

> **Gap closed during the V2.1 audit:** the ingest script hard-coded the licence
> string as a constant and never stored the licence document. CC-BY-SA requires
> the licence to travel with the material, so the full text is now checked in
> under `docs/licenses/`.

### ⚠️ The ShareAlike problem

An earlier note in this project's history described ORD as "CC-BY 4.0 —
commercial OK with attribution". **That was wrong.** The `LICENSE` file in
`ord-data` is **CC-BY-SA-4.0**. The `-SA` is a copyleft term and it is the
single most commercially significant fact in this document.

What it means in practice:

- **Attribution is required** wherever the data or a derivative is distributed.
- **ShareAlike**: if you distribute an *adapted* version of the data, that
  adaptation must be licensed under CC-BY-SA-4.0 as well.
- Commercial use is **permitted** — SA is not a non-commercial clause.
- The open question is what counts as an "adaptation" you are "distributing".
  Running a private local index and showing a customer the conditions for one
  reaction is not obviously distribution of an adapted database; shipping the
  392 MB index inside a product, or exposing it wholesale through an API,
  plausibly is.

**Before commercial release**, pick one:

1. Get a licensing opinion on whether your specific distribution model triggers
   SA on your own code or data.
2. Keep the ORD index server-side and private, and treat what a user sees as
   attributed quotation rather than redistribution of the database.
3. Replace ORD with a commercially licensed source (Reaxys, CAS, Pistachio) or
   with internal ELN data. `LiteratureProvider` exists so this is a one-class
   change — see
   [reaction-condition-intelligence.md](reaction-condition-intelligence.md#adding-another-evidence-provider).

The licence string travels with the data at every layer: it is stored per row in
`ord_reactions.provenance.license`, per dataset in `ord_ingest_log.license`,
returned on every precedent, and reported by
`GET /retrosynthesis/evidence/status`. A consumer of this API cannot see the
evidence without also seeing the terms.

### Required attribution

Anywhere ORD-derived conditions are displayed or distributed:

> Reaction condition data from the Open Reaction Database
> (https://open-reaction-database.org), licensed CC-BY-SA-4.0.

### Ingested datasets

| Dataset ID | Reactions | Content hash |
| --- | --- | --- |
| `805ad863feef48579d95d86a728035f4` | 50,688 | `31b7c4762145e5da` |
| `47eaacc46c3a4487bbdf99adb1a15e41` | 47,015 | `7e09c98aa540e15d` |
| `488402f6ec0d441ca2f7d6fabea7c220` | 40,000 | `d1b658a9ed389f22` |
| `d92976309c3a48a3a64a4cf5e7048086` | 39,347 | `78c17145099d2945` |
| `5481550056a14935b76e031fb94b88be` | 29,999 | `48508c30931b04b4` |
| `5c9a10329a8a48968d18879a48bb8ab2` | 9,632 | `a2ea1363cb895b59` |

Field coverage across the 216,681 ingested reactions, counted from the database
itself (`SELECT count(*) FILTER (WHERE …) FROM ord_reactions`):

| Field | Records | Share |
| --- | --- | --- |
| Temperature | 145,971 | 67.4% |
| Time | 144,944 | 66.9% |
| Yield | 146,210 | 67.5% |
| DOI | 169,666 | 78.3% |
| Publication URL | 99,667 | 46.0% |
| Patent number | **0** | **0.0%** |

Every one of the 216,681 rows carries `license = CC-BY-SA-4.0`.

**Patent coverage is zero**, and that is a significant gap for pharmaceutical
work: a large share of medicinal and process chemistry is disclosed in patents
rather than papers. Closing it means adding a patent-derived provider (Lowe's
USPTO extraction is the obvious first candidate) — one new
`LiteratureProvider` subclass, no change to anything else.

The 54% with a DOI but no URL is exactly the case where a link must **not** be
synthesised — see §6.

### How it was acquired

Selectively, per dataset, from the Hugging Face parquet mirror:

```bash
conda activate ord-ingest
python scripts/ingest_ord.py --list          # sorted smallest-first
python scripts/ingest_ord.py --dataset <id>
```

Not `git clone` of `ord-data`: that repo stores its data through Git LFS, so
cloning pulls the entire 1.26 GB regardless of what you need. The mirror serves
each dataset as a separate file.

**The application never loads ORD into RAM.** Ingestion streams one parquet at a
time into Postgres; serving reads indexed rows. Adding all ~3 M ORD reactions
would grow the table, not the process.

`dataset_version` is the sha256 of the source parquet, truncated to 16 chars. It
is part of the evidence cache key, so re-ingesting a refreshed dataset retires
the cached evidence derived from the old one instead of letting it look current.

---

## 2. AiZynthFinder + USPTO templates — retrosynthesis

| | |
| --- | --- |
| Software | AiZynthFinder 4.4.1 (AstraZeneca) — **MIT** |
| Models | Public USPTO expansion policy, filter policy, ringbreaker; ZINC stock |
| Template source | Reactions extracted from US patents (public domain documents) |
| On disk | 754 MB, `models/` |

Commercially usable. The templates carry **no condition data** — five columns,
of which `library_occurence` is a count of how often a template appears in the
extracted library.

**`library_occurence` is not**: a count of successful reactions, a yield, a
reaction probability, or an experimental success rate. It is surfaced to the API
as `template_occurrence` and labelled in the UI accordingly.

**`policy_probability` is not** a yield or a probability that the reaction will
succeed. It is the expansion model's score for applying that template to that
molecule. Surfaced as `score`, labelled "policy" in the UI, and the route
footnote says so.

---

## 3. ChEMBL — approved-drug search library

| | |
| --- | --- |
| Source | ChEMBL approved small-molecule drugs |
| Licence | **CC-BY-SA-3.0** |
| Size | 2,269 molecules in `molecules` |

Also ShareAlike. The same §1 considerations apply, at much smaller scale and
lower commercial significance (structures of approved drugs are public
knowledge; the value is the curation).

---

## 4. QSAR solubility model

| | |
| --- | --- |
| Training data | AqSolDB |
| Method | Locally trained; conformal prediction with Wilson-interval coverage |
| Weights | Ours |

Trained in-house, so no third-party weight licence. Check AqSolDB's own terms
before redistributing anything derived from the training set.

---

## 5. PubChem — name resolution

`POST /molecules/resolve` calls PubChem's public PUG REST API to turn a typed
compound name into a SMILES.

**This is the only outbound network call in the serving path**, it is triggered
only when a user types a name rather than a structure, and it sends only that
name. PubChem data is public domain. Draw or paste a structure and nothing
leaves the machine.

The conditions subsystem makes **no** outbound calls at all — enforced by a test
that greps the serving path for HTTP clients.

---

## 6. Rules this codebase enforces about provenance

These are implemented and tested, not aspirations.

**Never fabricated**: paper titles, authors, journals, DOIs, patent numbers,
URLs, reaction examples, yields, temperatures, catalysts, solvents, or any other
experimental condition.

**A URL is never constructed from a DOI.** `https://doi.org/<doi>` would render
as an authoritative-looking link to something nobody verified — and 54% of the
index has a DOI with no recorded URL. A link is emitted only when the source
record supplied one; otherwise the UI says "No link supplied by the source
record; DOI shown exactly as recorded."
Tested by `test_url_is_never_constructed_from_a_doi`.

**A partial DOI stays partial.** Some ORD records store `10.1038/s41557`. It is
shown as recorded, never extended to a guessed suffix.

**A name is never resolved to a guessed structure.** An unresolvable reagent name
keeps `smiles: null`. Guessing is how "TEA" becomes triethanolamine when the
paper meant triethylamine. Tested by
`test_chemical_name_is_never_resolved_to_a_guessed_structure`.

**Missing means absent, not zero.** A record with no temperature omits the field
entirely, so a reader can never mistake a missing measurement for a measured
zero. Tested by `test_missing_temperature_is_omitted_not_defaulted` and
`test_missing_yield_is_omitted_not_zero`.

**Reported values survive normalisation.** 353.15 K becomes 80 °C for
comparison, and the response still carries `353.15 K` in `value`/`unit`/
`original_text`. An unknown unit yields `None` rather than a guessed conversion.

**Out-of-range values are dropped as data errors**, not reported as findings — a
940% yield is not a finding.

**Predicted is never presented as experimental.** `ConditionValue` raises if a
non-predicted value is given a `confidence`, and the four evidence levels are
visually distinct in the UI by colour, icon and wording.

**Aggregates report what was observed.** Median plus observed range plus
observation count, with "Frequencies and ranges describe what was reported, not
a recommended procedure." No aggregate is ever labelled "optimal" or
"recommended" — tested by `test_aggregate_never_labels_a_range_as_optimal`.

---

## 7. Summary for a commercial decision

| Component | Licence | Commercial | Action needed |
| --- | --- | --- | --- |
| AiZynthFinder + USPTO models | MIT / public-domain source | ✅ | none |
| **ORD reaction data** | **CC-BY-SA-4.0** (verified from `LICENSE`) | ⚠️ **with ShareAlike obligations** | **legal review, or swap the provider** |
| `ord-schema` | Apache-2.0 | ✅ | none (ingest-only anyway) |
| ChEMBL drug library | CC-BY-SA-3.0 | ⚠️ same class of issue | attribution; review |
| QSAR model (ours) | — | ✅ | check AqSolDB terms |
| PubChem resolution | public domain | ✅ | none |
| Condition prediction | — | — | none shipped, deliberately |

The one item needing a decision before commercial release is **ORD's
ShareAlike**. Everything else is either permissive or ours.

Until that decision is made, the ORD provider should be described internally and
externally as a **development / research evidence provider — commercial
clearance pending**, and treated as provider #1 of several rather than as the
platform's permanent data foundation.
