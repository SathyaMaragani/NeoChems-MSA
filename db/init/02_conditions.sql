-- Reaction-condition evidence index.
--
-- Runs once on first container start, after 01_schema.sql. Separate file so the
-- evidence layer can be dropped or rebuilt without touching the molecule table.
--
-- DATA LICENCE: rows in ord_reactions are derived from the Open Reaction
-- Database, licensed CC-BY-SA-4.0. That is a ShareAlike licence - see
-- docs/data-provenance.md before redistributing this table or any database
-- built from it.

-- One row per ORD reaction, flattened out of the protobuf at ingest time so the
-- serving environment never needs ord-schema or protobuf.
CREATE TABLE IF NOT EXISTS ord_reactions (
    reaction_id      text PRIMARY KEY,
    dataset_id       text,
    dataset_name     text,
    -- sha256 of the normalised "sorted reactants>>sorted products"; the join key
    -- for exact matching. Not the template hash: one template spans many
    -- substrates, so template identity alone would match unrelated chemistry.
    reaction_key     text NOT NULL,
    reaction_smiles  text NOT NULL,
    reactants        text[] NOT NULL,
    products         text[] NOT NULL,
    -- Cartridge fingerprint of the major product, GiST-indexed. Used only to
    -- PREFILTER candidates cheaply; final ranking is done on the reaction
    -- difference fingerprint in Python, which keys on the transformation
    -- rather than on overall molecular resemblance.
    product_bfp      bfp,
    conditions       jsonb NOT NULL DEFAULT '{}'::jsonb,
    provenance       jsonb NOT NULL DEFAULT '{}'::jsonb,
    has_temperature  boolean NOT NULL DEFAULT false,
    has_time         boolean NOT NULL DEFAULT false,
    has_yield        boolean NOT NULL DEFAULT false,
    dataset_version  text NOT NULL,
    ingested_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ord_reactions_key_idx  ON ord_reactions (reaction_key);
CREATE INDEX IF NOT EXISTS ord_reactions_bfp_idx  ON ord_reactions USING gist (product_bfp);
CREATE INDEX IF NOT EXISTS ord_reactions_prod_idx ON ord_reactions USING gin (products);

-- Cached evidence, keyed on reaction identity AND the versions that produced it.
-- Including dataset_version in the key means a dataset refresh cannot leave old
-- evidence masquerading as current: the lookup simply misses and re-runs.
CREATE TABLE IF NOT EXISTS reaction_evidence_cache (
    reaction_key     text NOT NULL,
    provider         text NOT NULL,
    provider_version text NOT NULL,
    dataset_version  text NOT NULL,
    evidence         jsonb NOT NULL,
    retrieved_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (reaction_key, provider, provider_version, dataset_version)
);

-- Which ORD datasets have been ingested, and under what terms.
CREATE TABLE IF NOT EXISTS ord_ingest_log (
    dataset_id      text PRIMARY KEY,
    dataset_name    text,
    source_url      text,
    license         text NOT NULL,
    reaction_count  integer NOT NULL,
    ord_schema_ver  text,
    dataset_version text NOT NULL,
    ingested_at     timestamptz NOT NULL DEFAULT now()
);
