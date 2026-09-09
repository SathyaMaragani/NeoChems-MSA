# Molecular representation + search

RDKit representation utilities and exact / similarity / substructure search over a
local Postgres database carrying the RDKit cartridge.

## Start the database

```bash
docker compose up -d
```

Postgres on `127.0.0.1:5434` (5432 and 5433 were already taken by other projects
on this machine). Loopback only, never exposed to the network.

The image is `informaticsmatters/rdkit-cartridge-debian:Release_2023_09_3`, pinned
**deliberately**: it ships RDKit 2023.09, matching the Python-side `rdkit==2023.09.6`
that AiZynthFinder's `rdkit<2024.0.0` constraint forces. If the two drift, the
symptom is "search returns weird results", not a clean error. **Bump the image tag
and the Python rdkit pin together, never separately.**

The cluster is created with `POSTGRES_INITDB_ARGS: --encoding=UTF8`. This image's
initdb otherwise defaults to `SQL_ASCII`, which skips encoding validation entirely
and makes psycopg return `bytes` instead of `str` for text columns. Encoding cannot
be changed after creation without recreating the volume.

## Ingest

```bash
python scripts/download_chembl.py
python scripts/ingest_molecules.py
```

Add `--truncate` to `ingest_molecules.py` to reload from scratch.

Source is **ChEMBL 37**, filtered at the API to `max_phase=4` (approved) +
`molecule_type=Small molecule` + has-a-structure. Filtering server-side keeps the
reject log meaningful instead of ~900 predictable nulls from biologics.

```
attempted       3311
inserted        2269
duplicate       1042  (same parent SMILES as an earlier row)
rejected        0     (failed to parse)
salt-stripped   1101  (parent differs from source structure)
mineral salts   95    (stripping skipped, counter-ion is active)
```

## Parent-compound normalization

`canonical_smiles`, `inchikey`, `molecular_weight` and `morgan_fingerprint` are all
computed from the **parent compound**: largest fragment, then charges neutralised
where chemically valid (`rdMolStandardize.ChargeParent`). `original_smiles` keeps
the source structure verbatim, so nothing is discarded.

Largest-fragment stripping alone is not enough:

| aspirin sodium salt vs free-acid aspirin | Tanimoto |
|---|---|
| no stripping | 0.6667 |
| largest fragment only | 0.6897 |
| largest fragment + neutralise (**used**) | 1.0000 |

Stripping to the largest fragment leaves the carboxylate, which still scores 0.69.
Groups that cannot lose a proton stay charged - neostigmine's quaternary ammonium
survives normalization intact.

Switch strategies in one place: `parent()` in `service.py`.

### Mineral salts are exempt

For some records the counter-ion **is** the drug. Lithium carbonate (bipolar
disorder) and calcium carbonate (antacid) both reduce to carbonic acid, which would
make them indistinguishable in search - silently wrong results, not an error.
`is_mineral_salt` marks these, and `parent()` returns them untouched.

Two rules, in `service.is_mineral_salt`:

1. **No carbon at all** produces an inorganic record (`[Na+].[Cl-]`, zinc sulfate).
2. **A metal counter-ion on a parent of 6 or fewer heavy atoms** means the metal is
   the drug.

Rule 2 is what catches lithium carbonate. **"No carbon in the parent after
stripping" alone does not** - carbonate contains a carbon, so that test returns
False for the exact case it needs to catch. Verified against ChEMBL, not assumed.

Threshold sensitivity is mild - a limit of 4 flags 92 records, 6 flags 95, 8 flags
103, 10 flags 110 - so `MINERAL_PARENT_MAX_HEAVY_ATOMS` is not a knife-edge. It is
what separates lithium carbonate (parent 4 heavy atoms) from naproxen sodium
(parent 17, an inactive salt former that still strips normally).

95 of 3,311 records are flagged. Because they no longer collapse, the table holds
2,269 rows rather than 2,221.

Every caller routes through `parent()`, so ingestion and search cannot disagree
about what was stripped - a query for lithium carbonate matches the stored row.

## Which fingerprint column to read

Two Morgan columns, same settings (radius 2, 2048 bits) apart from chirality.
**Pick by use case, do not relitigate per caller:**

| column | `includeChirality` | read by | why |
|---|---|---|---|
| `morgan_fingerprint` | False | **similarity search** | Scaffold-hopping wants enantiomers to look alike. This is the default from `service.morgan_fingerprint()`. |
| `morgan_fingerprint_chiral` | True | **QSAR / property prediction** | Enantiomers can have different activity, so they must not share a feature vector. `service.morgan_fingerprint(..., include_chirality=True)`. |

The concrete case, racemic ibuprofen (id 187) vs dexibuprofen (id 1025):

```
stereo-blind column   1.00    same molecule, as far as search is concerned
chirality-aware col   0.75    different molecules, as far as QSAR is concerned
```

Both are correct for their own job. Feeding the stereo-blind column to a QSAR model
means handing it one feature vector for two rows that may carry different measured
activities — contradictory training signal with no error to signal it. 1,026 of
2,269 rows differ between the two columns.

Similarity search behaviour is unchanged by the second column and stays stereo-blind.

## Endpoints

### POST /search/similarity

```bash
curl -X POST localhost:8000/search/similarity -H 'Content-Type: application/json' -d '{"smiles": "CC(=O)Oc1ccccc1C(=O)O", "top_n": 5}'
```

```
1.0000  id=42    CC(=O)Oc1ccccc1C(=O)O                       aspirin itself
0.5128  id=969   CC(=O)Nc1ccc(OC(=O)c2ccccc2OC(C)=O)cc1      benorilate
0.4483  id=46    O=C(O)c1ccccc1O                             salicylic acid
0.4167  id=1052  CC(=O)Oc1ccccc1C(=O)Nc1ncc([N+](=O)[O-])s1
0.3590  id=386   Cc1cccc(Nc2ccccc2C(=O)O)c1C                 mefenamic acid
```

### POST /search/exact

Canonical-SMILES match, falling back to InChIKey. Both sides are parent-stripped,
so a salt form finds the parent record. 404 when nothing matches.

```bash
curl -X POST localhost:8000/search/exact -H 'Content-Type: application/json' -d '{"smiles": "CC(=O)Oc1ccccc1C(=O)[O-].[Na+]"}'
```

### POST /search/substructure

Cartridge `mol @> qmol`, GiST-indexed.

```bash
curl -X POST localhost:8000/search/substructure -H 'Content-Type: application/json' -d '{"smiles_pattern": "C(=O)[OH]", "top_n": 500}'
```

Aspirin and ibuprofen match; paracetamol (an anilide + phenol) does not.

### POST /molecules/represent

Stateless, no DB. Canonical form, InChIKey, MW, Morgan on-bits, base64 PNG. Useful
for validating a molecule before deciding whether to search or store it.

```bash
curl -X POST localhost:8000/molecules/represent -H 'Content-Type: application/json' -d '{"smiles": "CC(=O)Oc1ccccc1C(=O)O"}'
```

### GET /molecules/{id}

Full record plus `image_png_base64`. Add `?include_image=false` to skip the
depiction. Base64-inline matches how retrosynthesis returns route diagrams.

Invalid SMILES returns **400** with a clear message on every endpoint.

## Performance, and why no FAISS

Measured in-process at ~2.2k rows:

| operation | latency |
|---|---|
| `exact_search` | 9 ms |
| `similarity_search` (top 10) | 57 ms |
| `substructure_search` (387 hits) | 64 ms |

Similarity is a **full linear scan in Python** - every fingerprint is fetched and
compared per query. At a few thousand rows that is milliseconds and keeps the
Python fingerprints as the single source of truth.

**FAISS is deliberately not here.** It earns its keep past roughly **100k rows**,
where fetching every fingerprint per query stops being free. At that point the
options are an ANN index (FAISS) or the cartridge's own GiST-indexed `%` operator
on a `bfp` column - the latter avoids a second copy of the fingerprints but means
trusting cartridge-computed fingerprints, which reintroduces the version-coupling
question. Marked with a `ponytail:` comment at the scan in `search.py`.

Substructure search uses the cartridge rather than a Python `HasSubstructMatch`
loop, so it stays indexed as the table grows.

## Known limitations

- **Organic counter-ions above the size threshold still collapse.** Potassium
  citrate and sodium citrate both reduce to citric acid, because citrate is 13
  heavy atoms and exceeds `MINERAL_PARENT_MAX_HEAVY_ATOMS`. If that distinction
  matters, raise the threshold - but it is a size proxy, not a pharmacological
  judgement, and raising it starts catching genuine inactive salt formers.
  `original_smiles` always preserves the source structure.
- **Similarity search is stereo-blind; exact search is not.** This is deliberate,
  not an oversight - see "Which fingerprint column to read" above. A similarity
  query for (S)-ibuprofen returns both the racemate (id 187) and the single
  enantiomer (id 1025) tied at 1.0000, ordered by id, while exact search separates
  them (different InChIKeys: `HEFNNWSXXWATRW-JTQLQIEISA-N` vs `-UHFFFAOYSA-N`).
  1,305 of 2,269 rows carry at least one chiral centre, and 42 flat structures have
  more than one stereo variant in the table (86 rows). QSAR must read
  `morgan_fingerprint_chiral`, not `morgan_fingerprint`.
- **`molecular_weight` is the parent's**, not the salt's as-dispensed weight.
- **Approved drugs only** - 2,269 parents. Not a screening library, not a vendor
  catalogue. Similarity neighbors come only from approved chemical space.
- **Searches hit the DB on every call**; no caching. Fine at this size.
