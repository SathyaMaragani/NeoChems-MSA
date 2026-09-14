-- Transformation-keyed retrieval index (V2.2).
--
-- The V2.1 retriever found candidates by PRODUCT-molecule similarity and then
-- reranked them on the transformation. Benchmarking showed that is keyed on the
-- wrong thing: two Suzuki couplings can make products that look nothing alike,
-- so stage 1 offered no relevant candidate at all for ~44% of queries, and no
-- amount of reranking can recover from that.
--
-- This table stores, per reaction, several representations that can each serve
-- as a retrieval key, so retrieval can be keyed on the TRANSFORMATION and
-- reranked on the substrates - the opposite order. Fingerprints are computed by
-- the RDKit cartridge itself, so the index and the query use identical code.
--
-- Derived data only: rebuild it from ord_reactions at any time, and nothing in
-- here is a source of truth.

CREATE TABLE IF NOT EXISTS ord_reaction_index (
    reaction_id      text PRIMARY KEY
                     REFERENCES ord_reactions(reaction_id) ON DELETE CASCADE,

    -- normalised reaction representation (mirrors ord_reactions for join-free
    -- retrieval; the reaction_key is the substrate-specific identity used for
    -- exact-precedent lookup)
    reaction_key     text NOT NULL,
    reaction_smiles  text NOT NULL,

    -- reaction centre: the atom environments that CHANGE between reactants and
    -- products. Two reactions sharing a centre hash make the same bond changes,
    -- which is a high-precision transformation key independent of substrate.
    centre_hash      text,
    centre_size      integer,

    -- retrieval keys, all GiST-indexed below
    transformation_sfp sfp,   -- reaction_difference_fp: what changes
    transformation_bfp bfp,   -- reaction_structural_bfp: bit form of the above
    substrate_bfp      bfp,   -- Morgan over the reactants
    product_bfp        bfp,   -- Morgan over the major product (V2.1's only key)

    -- identity / provenance metadata
    template_hash    text,
    dataset_id       text,
    provider         text NOT NULL DEFAULT 'ord',
    dataset_version  text NOT NULL,

    -- ⚠️ BENCHMARK GROUND TRUTH - METADATA ONLY, NEVER A RETRIEVAL SIGNAL.
    -- reaction_class is the label the retrieval benchmark grades against.
    -- Using it to select or rank candidates would make the benchmark measure
    -- nothing at all. It is stored so the API can display "this precedent is a
    -- Suzuki", and a test asserts no retrieval query references it.
    reaction_class   text,
    -- The experimental campaign (screen / plate / notebook) a reaction came
    -- from. Every campaign in ORD holds exactly ONE reaction type, so
    -- retrieving a campaign-mate is trivially "correct" - benchmark splits must
    -- hold campaigns disjoint or the score is measuring plate-mates.
    campaign_id      text,

    built_at         timestamptz NOT NULL DEFAULT now()
);

-- Transformation-keyed similarity search, backed by the bit-vector form.
--
-- NO GiST index on transformation_sfp, deliberately. A GiST index over `sfp`
-- silently returns ZERO rows for the `%` operator at the thresholds this system
-- uses - measured at 0 where a sequential scan of the same predicate returned
-- 557, under both gist_sfp_ops and gist_sfp_low_ops. The sfp column is for
-- SCORING candidates the bfp index already selected; indexing it would look
-- like "transformation retrieval finds nothing" rather than like a bad index.
CREATE INDEX IF NOT EXISTS ord_rxn_index_transformation_bfp
    ON ord_reaction_index USING gist (transformation_bfp);
CREATE INDEX IF NOT EXISTS ord_rxn_index_substrate_bfp
    ON ord_reaction_index USING gist (substrate_bfp);
CREATE INDEX IF NOT EXISTS ord_rxn_index_product_bfp
    ON ord_reaction_index USING gist (product_bfp);

-- Exact transformation identity, and the keys the benchmark splits on.
CREATE INDEX IF NOT EXISTS ord_rxn_index_centre   ON ord_reaction_index (centre_hash);
CREATE INDEX IF NOT EXISTS ord_rxn_index_key      ON ord_reaction_index (reaction_key);
CREATE INDEX IF NOT EXISTS ord_rxn_index_campaign ON ord_reaction_index (campaign_id);
