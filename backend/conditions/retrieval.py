"""Candidate retrieval strategies for experimental precedents.

One module holding every way of answering "which indexed reactions might be
precedents for this one", so the benchmark and production run the *same* code.
A strategy that wins the benchmark is then adopted by changing which one the
provider calls - not by reimplementing it.

Each strategy is stage 1 only: it selects candidates and returns the similarity
components for each. Ranking, thresholding and aggregation stay in the service,
because those are policy rather than retrieval.

    V2.1 (product)       product similarity  -> rerank on transformation+substrate
    V2.2 (transformation) transformation similarity -> rerank on substrate

The V2.1 order was measured to be keyed on the wrong thing: two Suzuki couplings
can make products that look nothing alike, so ~44% of queries were offered no
relevant candidate at all. See docs/retrieval-benchmark.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from backend.conditions.normalize import NormalizedReaction, reaction_centre

#: Candidate-set size. Retrieval returns at most this many rows for ranking.
DEFAULT_LIMIT = 1000

#: Per-key similarity thresholds. These CANNOT be shared: a Morgan bit vector
#: and a reaction difference fingerprint live on different scales entirely.
#: Measured over 20k indexed reactions against one query:
#:
#:     Morgan bfp (product/substrate)   typical similarity ~0.5
#:     difference sfp (transformation)  mean 0.013, only 0.2% above 0.30
#:
#: A 0.4 threshold that selects sensibly for a product fingerprint selects
#: NOTHING AT ALL for a difference fingerprint - which is exactly what happened
#: the first time this ran. The sfp scale is compressed but sharp: low absolute
#: numbers still separate the signal cleanly.
DEFAULT_THRESHOLDS = {
    "product": 0.4,
    "substrate": 0.4,
    #  Applies to the STRUCTURAL reaction fingerprint (a bit vector), which is
    #  what the transformation prefilter actually searches - see the note below
    #  on why the difference fingerprint cannot be the index key.
    "transformation": 0.6,
}

#: Rerank floor on the difference fingerprint, which is a different scale again
#: (mean ~0.013 across the index). Used to score, never to index.
DIFFERENCE_FP_TYPE = 2
STRUCTURAL_FP_TYPE = 5


@dataclass
class Candidate:
    """One retrieved reaction and the similarity components scored for it.

    The components are kept separate rather than pre-combined so the caller
    decides the weighting - and so the benchmark can sweep weightings without
    re-running retrieval.
    """

    reaction_id: str
    transformation: float = 0.0
    substrate: float = 0.0
    product: float = 0.0
    centre_match: bool = False

    def combined(self, transformation_weight: float) -> float:
        return (
            transformation_weight * self.transformation
            + (1.0 - transformation_weight) * self.substrate
        )


def _set_threshold(conn, value: float) -> None:
    """Set the cartridge similarity threshold for this transaction.

    Two traps, both of which silently disabled retrieval once already:
      * the GUC does not exist until the cartridge's shared library is loaded
        into the backend, which happens on first use of an rdkit function;
      * plain SET takes no bind parameter, so `SET LOCAL x = %s` is a syntax
        error. set_config's third argument is is_local.
    """
    conn.execute("SELECT morganbv_fp(mol_from_smiles('CC'::cstring))")
    conn.execute("SELECT set_config('rdkit.tanimoto_threshold', %s, true)", (str(value),))


#  --- strategies -------------------------------------------------------------
#
#  Every strategy takes (conn, reaction, threshold, limit, exclude_sql, params)
#  and returns a list of Candidate. `exclude_sql` lets the benchmark hold out
#  campaigns and scaffolds without the strategies knowing anything about
#  benchmarking.


def _query(conn, sql: str, params: tuple) -> list[dict]:
    return conn.execute(sql, params).fetchall()


def _score(conn, reaction: NormalizedReaction, reaction_ids: list[str],
           centre: str = "") -> list[Candidate]:
    """Score a candidate set on EVERY component, whichever key selected it.

    The benchmark compares retrieval strategies, so the ranking must be held
    constant across them: a strategy that returned only its own component would
    be scored on a different formula from the others and the comparison would
    measure the harness rather than the chemistry. (It did, once: `by_product`
    returned only product similarity, the ranker combined transformation and
    substrate, every candidate scored 0 and the strategy looked 100% silent.)
    """
    if not reaction_ids:
        return []
    #  The query's own fingerprints go in a CTE so they are built ONCE. Inlined
    #  in the SELECT list they were rebuilt per candidate row - 1000 reaction
    #  parses per call - which made a benchmark pass take tens of minutes.
    rows = _query(
        conn,
        f"""WITH q AS MATERIALIZED (
              SELECT reaction_difference_fp(
                         reaction_from_smiles(%s::cstring),
                         {DIFFERENCE_FP_TYPE})                    AS tfp,
                     morganbv_fp(mol_from_smiles(%s::cstring))    AS sub,
                     morganbv_fp(mol_from_smiles(%s::cstring))    AS prod
            )
            SELECT i.reaction_id,
                   tanimoto_sml(i.transformation_sfp, q.tfp)  AS transformation_sim,
                   tanimoto_sml(i.substrate_bfp,      q.sub)  AS substrate_sim,
                   tanimoto_sml(i.product_bfp,        q.prod) AS product_sim,
                   (%s <> '' AND i.centre_hash = %s)          AS centre_match
            FROM ord_reaction_index i, q
            WHERE i.reaction_id = ANY(%s)""",
        (reaction.reaction_smiles, ".".join(reaction.reactants),
         reaction.products[0], centre, centre, reaction_ids),
    )
    return [
        Candidate(
            row["reaction_id"],
            transformation=float(row["transformation_sim"] or 0.0),
            substrate=float(row["substrate_sim"] or 0.0),
            product=float(row["product_sim"] or 0.0),
            centre_match=bool(row["centre_match"]),
        )
        for row in rows
    ]


def by_product(conn, reaction: NormalizedReaction,
               threshold: float = DEFAULT_THRESHOLDS["product"],
               limit: int = DEFAULT_LIMIT, extra_sql: str = "",
               extra_params: tuple = ()) -> list[Candidate]:
    """V2.1: select candidates whose major PRODUCT resembles the query's."""
    _set_threshold(conn, threshold)
    product = reaction.products[0]
    #  The query fingerprint is built once in a CTE. Left inline in the ORDER BY
    #  it is rebuilt for every candidate row, which dominated the whole query.
    rows = _query(
        conn,
        f"""WITH q AS MATERIALIZED (
              SELECT morganbv_fp(mol_from_smiles(%s::cstring)) AS fp
            )
            SELECT i.reaction_id
            FROM ord_reaction_index i, q
            WHERE i.product_bfp %% q.fp
              AND i.reaction_key <> %s {extra_sql}
            ORDER BY tanimoto_sml(i.product_bfp, q.fp) DESC
            LIMIT %s""",
        (product, reaction.reaction_key) + extra_params + (limit,),
    )
    return _score(conn, reaction, [r["reaction_id"] for r in rows])


def by_transformation(conn, reaction: NormalizedReaction,
                      threshold: float = DEFAULT_THRESHOLDS["transformation"],
                      limit: int = DEFAULT_LIMIT, extra_sql: str = "",
                      extra_params: tuple = ()) -> list[Candidate]:
    """V2.2: select candidates whose TRANSFORMATION resembles the query's.

    Selection is on `reaction_structural_bfp` - a bit vector, so a GiST index
    works on it. Scoring then uses `reaction_difference_fp`, which encodes what
    CHANGES between reactants and products and discriminates far better
    (esterification vs amidation: 0.10 on the difference fingerprint, 0.64 on
    the structural one).

    ⚠️ The difference fingerprint CANNOT be the index key. It is an `sfp`, and a
    GiST index over `sfp` silently returns ZERO rows for `%` at these thresholds
    - measured 0 against a sequential scan's 557, under both gist_sfp_ops and
    gist_sfp_low_ops. Indexing it would look like "transformation retrieval
    finds nothing" rather than like a broken index.
    """
    _set_threshold(conn, threshold)
    rows = _query(
        conn,
        f"""WITH q AS MATERIALIZED (
              SELECT reaction_structural_bfp(
                  reaction_from_smiles(%s::cstring), {STRUCTURAL_FP_TYPE}) AS fp
            )
            SELECT i.reaction_id
            FROM ord_reaction_index i, q
            WHERE i.transformation_bfp %% q.fp
              AND i.reaction_key <> %s {extra_sql}
            LIMIT %s""",
        (reaction.reaction_smiles, reaction.reaction_key) + extra_params + (limit,),
    )
    return _score(conn, reaction, [r["reaction_id"] for r in rows])


def by_substrate(conn, reaction: NormalizedReaction,
                 threshold: float = DEFAULT_THRESHOLDS["substrate"],
                 limit: int = DEFAULT_LIMIT, extra_sql: str = "",
                 extra_params: tuple = ()) -> list[Candidate]:
    """Select candidates whose REACTANTS resemble the query's reactants."""
    _set_threshold(conn, threshold)
    reactants = ".".join(reaction.reactants)
    rows = _query(
        conn,
        f"""WITH q AS MATERIALIZED (
              SELECT morganbv_fp(mol_from_smiles(%s::cstring)) AS fp
            )
            SELECT i.reaction_id
            FROM ord_reaction_index i, q
            WHERE i.substrate_bfp %% q.fp
              AND i.reaction_key <> %s {extra_sql}
            ORDER BY tanimoto_sml(i.substrate_bfp, q.fp) DESC
            LIMIT %s""",
        (reactants, reaction.reaction_key) + extra_params + (limit,),
    )
    return _score(conn, reaction, [r["reaction_id"] for r in rows])


def hybrid(conn, reaction: NormalizedReaction,
           threshold: float = DEFAULT_THRESHOLDS["transformation"],
           limit: int = DEFAULT_LIMIT, extra_sql: str = "",
           extra_params: tuple = (),
           substrate_threshold: float = DEFAULT_THRESHOLDS["substrate"]
           ) -> list[Candidate]:
    """Union of the transformation, substrate and reaction-centre keys.

    Neither key alone is sufficient: transformation similarity finds the right
    chemistry on unrelated substrates, substrate similarity finds the right
    molecules undergoing different chemistry, and an exact reaction-centre match
    finds the same bond changes whatever else differs. The union is scored on
    every component so the ranker can use whichever signal is informative.

    The branches are separate statements merged in Python rather than one SQL
    UNION, because the two similarity keys need different values of the same
    cartridge threshold GUC and a single statement can only hold one.
    """
    reactants = ".".join(reaction.reactants)
    centre, _ = reaction_centre(reaction)
    per_branch = max(1, limit)

    #  Branch 1 runs at the transformation threshold, branch 2 at the substrate
    #  threshold, so they are collected separately and merged in Python. One SQL
    #  statement cannot hold two values of the same GUC.
    found: dict[str, Candidate] = {}

    _set_threshold(conn, threshold)
    rows = _query(
        conn,
        f"""SELECT i.reaction_id
            FROM ord_reaction_index i
            WHERE i.transformation_bfp %% reaction_structural_bfp(
                      reaction_from_smiles(%s::cstring), {STRUCTURAL_FP_TYPE})
              AND i.reaction_key <> %s {extra_sql}
            LIMIT %s""",
        (reaction.reaction_smiles, reaction.reaction_key) + extra_params + (per_branch,),
    )
    for row in rows:
        found[row["reaction_id"]] = Candidate(row["reaction_id"])

    _set_threshold(conn, substrate_threshold)
    rows = _query(
        conn,
        f"""SELECT i.reaction_id
            FROM ord_reaction_index i
            WHERE i.substrate_bfp %% morganbv_fp(mol_from_smiles(%s::cstring))
              AND i.reaction_key <> %s {extra_sql}
            LIMIT %s""",
        (reactants, reaction.reaction_key) + extra_params + (per_branch,),
    )
    for row in rows:
        found.setdefault(row["reaction_id"], Candidate(row["reaction_id"]))

    if centre:
        rows = _query(
            conn,
            f"""SELECT i.reaction_id
                FROM ord_reaction_index i
                WHERE i.centre_hash = %s AND i.reaction_key <> %s {extra_sql}
                LIMIT %s""",
            (centre, reaction.reaction_key) + extra_params + (per_branch,),
        )
        for row in rows:
            found.setdefault(row["reaction_id"], Candidate(row["reaction_id"]))

    if not found:
        return []
    return _score(conn, reaction, list(found), centre)


#: Every strategy the benchmark evaluates, by name.
STRATEGIES: dict[str, Callable] = {
    "product": by_product,
    "transformation": by_transformation,
    "substrate": by_substrate,
    "hybrid": hybrid,
}
