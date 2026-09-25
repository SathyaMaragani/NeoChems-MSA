"""ORD-backed evidence provider.

Reads only the flattened index in Postgres - never a protobuf, never
ord-schema. Ingestion lives in scripts/ingest_ord.py under a separate conda env,
so the serving environment keeps AiZynthFinder's pins untouched.

Retrieval uses V2.2 transformation-keyed indexing (hybrid strategy):
  1. PREFILTER in Postgres using the transformation difference, substrate
     Morgan fingerprints, and exact reaction centre matches.
  2. RANK candidates by combining transformation and substrate similarity.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from backend.conditions import extract as ex
from backend.conditions.normalize import NormalizedReaction
from backend.conditions.providers import LiteratureProvider
from backend.conditions.schema import (
    ChemicalEntity,
    EvidenceLevel,
    Precedent,
    Provenance,
    ReactionConditions,
    SourceType,
)

#: Weighting for the combined ranking score, set from the retrieval benchmark
#: (scripts/benchmark_retrieval.py, 700 queries graded against ORD's own
#: REACTION_TYPE labels) rather than from intuition.
#:
#: The original 0.65/0.35 guess was on the wrong side. Measured P@1 across two
#: seeds: 0.2-0.4 transformation is the stable optimum (0.536-0.551), 0.5 is
#: slightly worse (0.514-0.537), and pure transformation similarity is worst
#: (0.459-0.480). Substrate similarity is the stronger signal here.
#:
#: CAVEAT, because this matters more than the number: the benchmark's labelled
#: chemistry is industrial HTE, where each reaction-type campaign uses
#: characteristic substrate classes. Substrate similarity may therefore be
#: partly proxying "which campaign is this from", which would not generalise
#: outside HTE data. 0.30 sat at the measured optimum; anything in 0.2-0.4 was
#: within noise, and a higher transformation share may well be safer on
#: non-HTE chemistry. Re-run the benchmark before trusting this elsewhere.
#:
#: 0.40 since USPTO (non-HTE) joined the index, and the caveat above came true.
#: At 0.30, "same substrates, different chemistry" outranked the right
#: chemistry: a C-H borylation's top precedent was a Suzuki of the same
#: chloropyridine, because pinacol boronate bits sit in both reactant sets.
#: Measured on distinct reactions (--distinct), held-out test split:
#: rule-graded P@1 0.660 -> 0.720 with no type worse, human-graded 0.434 ->
#: 0.443. 0.6 scores higher on rules but costs 3 points on human labels.
TRANSFORMATION_WEIGHT = 0.40
SUBSTRATE_WEIGHT = 0.60

#: Cartridge similarity floor for the prefilter, loosened from 0.5 on benchmark
#: evidence. At 0.5 only 52.6% of queries were offered a single relevant
#: reaction, which caps everything downstream.
#:
#: Measured trade-off (700 queries, fine labels):
#:
#:     threshold  P@1    silent  offered-any-relevant  candidates  latency/step
#:     0.3        0.536  12.9%   60.1%                 514         ~1.6 s
#:     0.4        0.516  27.6%   56.0%                 180         ~0.6 s
#:     0.5        -      -       52.6%                 123         ~0.6 s
#:
#: 0.3 retrieves better. 0.4 is shipped anyway because stage 2 re-normalises and
#: re-fingerprints every candidate on every request, so cost scales directly
#: with candidate count - and 1.6 s per step is too much for an interactive
#: plan. That is a fixable implementation problem, not a retrieval one:
#: precompute candidate fingerprints into Postgres and 0.3 (or lower) becomes
#: affordable. Revisit this constant when that lands.
#:
#: The deeper limit is structural and tuning cannot reach it: product similarity
#: is a poor proxy for "same transformation", so stage 1 is keyed on the wrong
#: thing. See docs/retrieval-benchmark.md.
PREFILTER_THRESHOLD = 0.4
PREFILTER_LIMIT = 1000

logger = logging.getLogger("ramchems.conditions.ord")


class OrdProvider(LiteratureProvider):
    name = "ord"
    #: v2: "not found" wording now names the indexed corpus and its size.
    #: v3: similar-precedent retrieval was dead (the cartridge threshold GUC
    #: does not exist until an rdkit function is touched, and the exception was
    #: swallowed), so every cached v2 "unavailable" may be wrong.
    #: v6: migration to V2.2 transformation-keyed hybrid retrieval.
    #: v7: kind-coded patent numbers (US07842696B2, 72% of USPTO rows) now get
    #: links; evidence cached under v6 carries those precedents without one.
    #: v8: ranking weight 0.30 -> 0.40 transformation; similar precedents change.
    #: v9: similarity floor 0.20 -> 0.40 (service.MIN_SIMILARITY); cached steps
    #: may carry precedents, and a SIMILAR badge, the new floor would drop.
    version = "9"
    display_name = "Open Reaction Database + USPTO patent reactions (indexed)"

    def __init__(self, pool_factory=None) -> None:
        #  Injected so tests can run against a stub without a database.
        if pool_factory is None:
            from backend.molrepr.search import pool as pool_factory
        self._pool = pool_factory
        self._dataset_version: Optional[str] = None
        self._available: Optional[bool] = None
        self._record_count: Optional[int] = None
        self._checked_at: float = 0.0

    #  --- availability -----------------------------------------------------

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._pool().connection() as conn:
            return conn.execute(sql, params).fetchall()

    @property
    def dataset_version(self) -> Optional[str]:
        """Combined version across ingested datasets.

        Any dataset changing changes this string, which changes the cache key,
        which retires stale evidence rather than letting it look current.
        """
        if self._dataset_version is None:
            try:
                rows = self._query(
                    "SELECT string_agg(dataset_version, '+' ORDER BY dataset_id) AS v "
                    "FROM ord_ingest_log"
                )
                self._dataset_version = (rows[0]["v"] if rows else None) or None
            except Exception:
                self._dataset_version = None
        return self._dataset_version

    #: How long a negative availability probe is trusted before re-checking.
    UNAVAILABLE_TTL_SECONDS = 30.0

    @property
    def available(self) -> bool:
        """Whether the index can be queried right now.

        A positive result is cached for the life of the process - the data does
        not un-ingest itself. A NEGATIVE result is cached only briefly: one
        failed probe (the database still starting, a connection blip, a pool
        closed by a test teardown) would otherwise mark the evidence layer dead
        until the process restarts, and nothing would say why. Observed exactly
        that during the V2.1 audit, as two tests skipping for no visible reason.
        """
        if self._available:
            return True
        now = time.monotonic()
        if self._available is False and now - self._checked_at < self.UNAVAILABLE_TTL_SECONDS:
            return False
        try:
            rows = self._query("SELECT count(*) AS n FROM ord_reactions")
            self._record_count = int(rows[0]["n"]) if rows else 0
            self._available = self._record_count > 0
        except Exception:
            #  No table, no database, no ingest - all mean "no evidence",
            #  never an error that breaks a retrosynthesis. Logged, because a
            #  silent one of these is how the similar path stayed dead.
            logger.warning("ORD availability probe failed", exc_info=False)
            self._record_count = 0
            self._available = False
        self._checked_at = now
        return self._available

    @property
    def licenses(self) -> dict[str, str]:
        """Licence per ingested dataset name, so the API reports every licence
        the served evidence actually carries."""
        try:
            rows = self._query(
                "SELECT DISTINCT license, dataset_id FROM ord_ingest_log ORDER BY license")
        except Exception:
            return {}
        return {r["license"]: ("USPTO patent grants (Lowe)"
                               if r["dataset_id"].startswith("uspto")
                               else "Open Reaction Database")
                for r in rows}

    @property
    def record_count(self) -> Optional[int]:
        """Rows in the index. Quoted in every "not found" message so the reader
        sees what was actually searched, rather than inferring "the literature"."""
        if self._record_count is None:
            self.available  # populates the count as a side effect
        return self._record_count

    #  --- row -> domain ----------------------------------------------------

    @staticmethod
    def _entities(raw: list[dict] | None, role: str) -> list[ChemicalEntity]:
        out: list[ChemicalEntity] = []
        for item in raw or []:
            entity = ex.chemical(
                name=item.get("name"),
                smiles=item.get("smiles"),
                role=item.get("role") or role,
            )
            if entity is not None:
                out.append(entity)
        return out

    def _conditions(
        self, row: dict, evidence_level: EvidenceLevel
    ) -> ReactionConditions:
        raw: dict[str, Any] = row.get("conditions") or {}
        source_id = row["reaction_id"]

        temperature = None
        if raw.get("temperature"):
            temperature = ex.temperature(
                raw["temperature"].get("value"),
                raw["temperature"].get("unit"),
                source_type=SourceType.DATASET,
                source_id=source_id,
                evidence_level=evidence_level,
            )
        time_value = None
        if raw.get("time"):
            time_value = ex.reaction_time(
                raw["time"].get("value"),
                raw["time"].get("unit"),
                source_type=SourceType.DATASET,
                source_id=source_id,
                evidence_level=evidence_level,
            )
        yield_value = None
        if raw.get("yield"):
            yield_value = ex.percent_yield(
                raw["yield"].get("value"),
                source_type=SourceType.DATASET,
                source_id=source_id,
                evidence_level=evidence_level,
            )

        return ReactionConditions(
            evidence_level=evidence_level,
            reagents=self._entities(raw.get("reagents"), "REAGENT"),
            catalysts=self._entities(raw.get("catalysts"), "CATALYST"),
            solvents=self._entities(raw.get("solvents"), "SOLVENT"),
            temperature=temperature,
            time=time_value,
            yield_=yield_value,
            workup=list(raw.get("workup") or []),
            notes=raw.get("procedure_excerpt"),
        )

    @staticmethod
    def _provenance(row: dict) -> Provenance:
        raw: dict[str, Any] = row.get("provenance") or {}
        source_type = raw.get("source_type") or "dataset"
        try:
            typed = SourceType(source_type)
        except ValueError:
            typed = SourceType.DATASET
        return Provenance(
            source_type=typed,
            source_id=raw.get("source_id") or row["reaction_id"],
            dataset_name=raw.get("dataset_name"),
            dataset_version=row.get("dataset_version"),
            title=raw.get("title"),
            year=raw.get("year"),
            doi=raw.get("doi"),
            patent_number=raw.get("patent_number"),
            #  Carried only when the record supplied one. A DOI is never turned
            #  into a URL here: that would manufacture a link we never verified.
            url=raw.get("url"),
            #  A record-supplied URL has no origin tag; only a link built from a
            #  patent number carries one, so the UI can say so.
            url_origin=raw.get("url_origin") or ("source" if raw.get("url") else None),
            reaction_identifier=raw.get("reaction_identifier"),
            license=raw.get("license"),
            retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def _precedent(
        self,
        row: dict,
        evidence_level: EvidenceLevel,
        match_type: str,
        similarity: float | None = None,
        transformation_similarity: float | None = None,
        substrate_similarity: float | None = None,
    ) -> Precedent:
        return Precedent(
            reaction_id=row["reaction_id"],
            reaction_smiles=row.get("reaction_smiles"),
            conditions=self._conditions(row, evidence_level),
            provenance=self._provenance(row),
            similarity=None if similarity is None else round(similarity, 4),
            transformation_similarity=None if transformation_similarity is None else round(transformation_similarity, 4),
            substrate_similarity=None if substrate_similarity is None else round(substrate_similarity, 4),
            combined_similarity=None if similarity is None else round(similarity, 4),
            match_type=match_type,
        )

    #  --- retrieval --------------------------------------------------------

    def find_exact_precedents(
        self, reaction: NormalizedReaction, limit: int = 10
    ) -> list[Precedent]:
        """Reactions whose normalised identity is the same as this step's."""
        if not self.available:
            return []
        rows = self._query(
            """SELECT reaction_id, reaction_smiles, conditions, provenance,
                      dataset_version
               FROM ord_reactions
               WHERE reaction_key = %s
               ORDER BY has_temperature DESC, has_time DESC, has_yield DESC
               LIMIT %s""",
            (reaction.reaction_key, limit),
        )
        return [
            self._precedent(r, EvidenceLevel.EXPERIMENTAL, "exact", 1.0) for r in rows
        ]

    def find_similar_precedents(
        self, reaction: NormalizedReaction, limit: int = 10, min_similarity: float = 0.3
    ) -> list[Precedent]:
        if not self.available or not reaction.products:
            return []

        try:
            from backend.conditions.retrieval import hybrid, DEFAULT_THRESHOLDS
            with self._pool().connection() as conn:
                candidates = hybrid(
                    conn, reaction,
                    threshold=DEFAULT_THRESHOLDS["transformation"],
                    limit=PREFILTER_LIMIT,
                    substrate_threshold=DEFAULT_THRESHOLDS["substrate"]
                )
        except Exception:
            #  A retrieval failure must never break a retrosynthesis - but it
            #  must not be silent either.
            logger.warning("similar-precedent prefilter failed", exc_info=True)
            return []

        scored = []
        for candidate in candidates:
            score = candidate.combined(TRANSFORMATION_WEIGHT)
            if score >= min_similarity:
                scored.append((score, candidate))

        scored.sort(key=lambda pair: -pair[0])
        top_candidates = [cand for score, cand in scored[:limit]]
        top_ids = [cand.reaction_id for cand in top_candidates]

        if not top_ids:
            return []

        rows = self._query(
            "SELECT reaction_id, reaction_smiles, conditions, provenance, dataset_version "
            "FROM ord_reactions WHERE reaction_id = ANY(%s)",
            (top_ids,)
        )
        row_by_id = {row["reaction_id"]: row for row in rows}

        precedents = []
        for score, cand in scored[:limit]:
            if cand.reaction_id in row_by_id:
                precedents.append(
                    self._precedent(
                        row_by_id[cand.reaction_id], 
                        EvidenceLevel.SIMILAR_EXPERIMENTAL, 
                        "similar", 
                        similarity=score,
                        transformation_similarity=cand.transformation,
                        substrate_similarity=cand.substrate
                    )
                )
        return precedents

    def get_details(self, source_id: str) -> Optional[Precedent]:
        if not self.available:
            return None
        rows = self._query(
            """SELECT reaction_id, reaction_smiles, conditions, provenance,
                      dataset_version
               FROM ord_reactions WHERE reaction_id = %s""",
            (source_id,),
        )
        if not rows:
            return None
        return self._precedent(rows[0], EvidenceLevel.EXPERIMENTAL, "exact", 1.0)
