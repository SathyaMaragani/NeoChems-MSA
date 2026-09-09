"""Exact, similarity and substructure search over the molecules table.

Substructure matching runs in Postgres via the RDKit cartridge (GiST-indexed).
Exact and similarity use the Python-side canonical SMILES / fingerprints, which
are the source of truth - see backend/molrepr/service.py.
"""
from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from backend.molrepr import service

DSN = os.environ.get(
    "RAMCHEMS_DSN", "postgresql://ramchems:ramchems@127.0.0.1:5434/ramchems"
)

# Opened lazily; the API closes it on shutdown.
_pool: ConnectionPool | None = None

_COLUMNS = """id, canonical_smiles, original_smiles, inchikey, source::text,
              is_mineral_salt, molecular_weight, created_at"""


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DSN, min_size=1, max_size=4, kwargs={"row_factory": dict_row})
        _pool.wait(timeout=10)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def _serialise(row: dict) -> dict:
    row = dict(row)
    row["created_at"] = row["created_at"].isoformat()
    return row


def stats() -> dict:
    """Row counts for the molecules table.

    Exists because the UI needs a real total: substructure_search caps its count
    at top_n, so using it as a library size reports the cap, not the table.
    """
    with pool().connection() as conn:
        row = conn.execute(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE is_mineral_salt) AS mineral_salts,
                      count(DISTINCT source) AS sources
               FROM molecules"""
        ).fetchone()
    return dict(row)


def get_molecule(molecule_id: int) -> dict | None:
    with pool().connection() as conn:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM molecules WHERE id = %s", (molecule_id,)
        ).fetchone()
    return _serialise(row) if row else None


def exact_search(smiles: str) -> dict | None:
    """Match on parent canonical SMILES, falling back to InChIKey.

    Both sides are parent-stripped, so querying a free acid finds the salt record
    and vice versa.
    """
    canonical = service.canonicalize(smiles)
    inchikey = service.to_inchikey(smiles)
    with pool().connection() as conn:
        row = conn.execute(
            f"""SELECT {_COLUMNS} FROM molecules
                WHERE canonical_smiles = %s OR inchikey = %s
                ORDER BY (canonical_smiles = %s) DESC, id
                LIMIT 1""",
            (canonical, inchikey, canonical),
        ).fetchone()
    return _serialise(row) if row else None


def rank_by_similarity(
    query_fp,
    candidates: Iterable[tuple[Any, Any]],
    top_n: int = 10,
    min_similarity: float = 0.0,
) -> list[tuple[Any, float]]:
    """Score `candidates` - (payload, fingerprint) pairs - against `query_fp`.

    Fingerprints may be ExplicitBitVect or the raw bytes stored in the database.
    Split out from similarity_search so callers with their own fingerprint list
    (QSAR's applicability domain, which scores against a training set rather than
    the molecules table) reuse this scoring rather than reimplementing Tanimoto.

    ponytail: linear scan. Past ~100k rows, switch to an ANN index (FAISS, or the
    cartridge's own GiST-indexed `%` operator on bfp) rather than scoring every
    candidate per query.
    """
    scored: list[tuple[Any, float]] = []
    for payload, fingerprint in candidates:
        if isinstance(fingerprint, (bytes, bytearray, memoryview)):
            fingerprint = service.fingerprint_from_bytes(fingerprint)
        score = service.tanimoto(query_fp, fingerprint)
        if score >= min_similarity:
            scored.append((payload, score))
    scored.sort(key=lambda pair: -pair[1])
    return scored[:top_n]


def similarity_search(
    smiles: str, top_n: int = 10, min_similarity: float = 0.0
) -> list[tuple[dict, float]]:
    """Tanimoto over Morgan fingerprints against the molecules table, ranked
    descending. Stereo-blind, per the fingerprint-column table in README.md.

    Full scan in Python. At a few thousand rows this is a handful of milliseconds
    and keeps the Python fingerprints as the single source of truth.
    """
    query_fp = service.morgan_fingerprint(smiles)
    with pool().connection() as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS}, morgan_fingerprint FROM molecules "
            "WHERE morgan_fingerprint IS NOT NULL"
        ).fetchall()

    candidates = [(row, row.pop("morgan_fingerprint")) for row in rows]
    scored = rank_by_similarity(query_fp, candidates, top_n=len(candidates),
                                min_similarity=min_similarity)
    # Stable tie-break on id, which the API contract depends on.
    scored.sort(key=lambda pair: (-pair[1], pair[0]["id"]))
    return [(_serialise(row), score) for row, score in scored[:top_n]]


def substructure_search(smiles_pattern: str, top_n: int = 50) -> list[dict]:
    """Cartridge substructure match, GiST-indexed.

    The pattern is validated with Python RDKit first so a bad query returns a
    clear 400 rather than a Postgres error.
    """
    service.parse(smiles_pattern)
    with pool().connection() as conn:
        rows = conn.execute(
            f"""SELECT {_COLUMNS} FROM molecules
                WHERE mol @> %s::qmol
                ORDER BY id
                LIMIT %s""",
            (smiles_pattern, top_n),
        ).fetchall()
    return [_serialise(row) for row in rows]
