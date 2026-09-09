-- Runs once, on first container start (empty pgdata volume).
CREATE EXTENSION IF NOT EXISTS rdkit;

CREATE TYPE molecule_source AS ENUM ('public', 'internal');

CREATE TABLE molecules (
    id               serial PRIMARY KEY,
    -- Parent compound: largest fragment, charges neutralised where chemically
    -- valid. Drives exact + similarity search, so a salt and its free acid match.
    canonical_smiles text            NOT NULL UNIQUE,
    -- Exactly what the source gave us, before stripping. Nothing is discarded.
    original_smiles  text            NOT NULL,
    inchikey         text            NOT NULL,
    source           molecule_source NOT NULL,
    -- True when parent-stripping was deliberately skipped because the counter-ion
    -- is the active species (lithium carbonate vs calcium carbonate).
    is_mineral_salt  boolean         NOT NULL DEFAULT false,
    molecular_weight double precision,
    -- Morgan radius 2 / 2048 bits, packed to bytes by Python RDKit, which is the
    -- single source of truth for canonical_smiles, inchikey and this fingerprint.
    morgan_fingerprint bytea,
    -- Same Morgan settings but includeChirality=True. Read by QSAR, NOT by
    -- similarity search: enantiomers score 1.0 under the stereo-blind
    -- fingerprint above, which is correct for scaffold-hopping and wrong for
    -- activity prediction. (S)- vs (R)-ibuprofen: 1.00 blind, 0.75 chiral.
    morgan_fingerprint_chiral bytea,
    -- Cartridge type, used ONLY for GiST-indexed substructure matching. Graph
    -- matching is version-tolerant, so this does not reintroduce the drift risk.
    mol              mol,
    created_at       timestamptz     NOT NULL DEFAULT now()
);

CREATE INDEX molecules_inchikey_idx ON molecules (inchikey);
CREATE INDEX molecules_mol_idx      ON molecules USING gist (mol);
