"""Orchestrates evidence retrieval for one reaction step.

Order of attempts, stopping at the first that yields anything:

  1. exact precedent            -> EXPERIMENTAL
  2. similar precedents         -> SIMILAR_EXPERIMENTAL (aggregated)
  3. condition prediction model -> PREDICTED
  4. nothing                    -> UNAVAILABLE, with a reason

Every failure path degrades to UNAVAILABLE. Nothing in here can raise into the
retrosynthesis request: a route that solved stays solved even if the database is
down, the provider is missing, or retrieval times out.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from backend.conditions.aggregate import aggregate
from backend.conditions.normalize import (
    NormalizationError,
    NormalizedReaction,
    normalize,
)
from backend.conditions.providers import (
    ConditionPredictionProvider,
    LiteratureProvider,
    NullProvider,
    UnavailablePredictionProvider,
)
from backend.conditions.schema import (
    EvidenceLevel,
    ReactionConditions,
    ReactionEvidence,
    unavailable,
)

logger = logging.getLogger("ramchems.conditions")

MAX_DIRECT = 5
MAX_SIMILAR = 8

#: Minimum combined score for a similar precedent to be shown at all.
#:
#: Lowered from 0.35 on benchmark evidence: 0.35 was the single worst setting
#: measured. It made the system silent on 43% of queries and, when it did
#: answer, put a correct-transformation reaction first 87% of the time; with no
#: floor at all those figures were 29% and 95%. The floor was discarding good
#: results, not filtering bad ones.
#:
#: 0.20 rather than 0.00 is a product judgement, not a measurement: 0.00 scored
#: marginally better on every metric, but it would also let a reaction scoring
#: 0.05 be shown under the heading "SIMILAR EXPERIMENTAL PRECEDENT". A floor
#: keeps that from happening; 0.20 captures most of the measured gain.
MIN_SIMILARITY = 0.20


def _no_conditions_reason(count: int, kind: str) -> str:
    """A precedent that records no conditions is still evidence the reaction was
    run - but it is not evidence of any conditions, and must not be shown as if
    it were."""
    plural = "" if count == 1 else "s"
    return (
        f"{count} {kind} reaction{plural} found, but none of those records "
        "include reaction conditions. The transformation is attested; the "
        "conditions are not."
    )


class ConditionsService:
    """Evidence lookup with a Postgres-backed cache."""

    def __init__(
        self,
        provider: Optional[LiteratureProvider] = None,
        predictor: Optional[ConditionPredictionProvider] = None,
        pool_factory=None,
        use_cache: bool = True,
    ) -> None:
        if provider is None:
            provider = self._default_provider()
        self.provider = provider
        self.predictor = predictor or UnavailablePredictionProvider()
        self.use_cache = use_cache
        if pool_factory is None:
            try:
                from backend.molrepr.search import pool as pool_factory
            except Exception:
                pool_factory = None
        self._pool = pool_factory

    @staticmethod
    def _default_provider() -> LiteratureProvider:
        """ORD when it has been ingested, otherwise the honest empty provider."""
        if os.environ.get("RAMCHEMS_EVIDENCE_PROVIDER", "ord").lower() == "null":
            return NullProvider()
        try:
            from backend.conditions.ord_provider import OrdProvider

            provider = OrdProvider()
            return provider if provider.available else NullProvider()
        except Exception:
            return NullProvider()

    #  --- cache ------------------------------------------------------------

    def _cache_get(self, reaction_key: str) -> Optional[dict]:
        if not (self.use_cache and self._pool):
            return None
        try:
            with self._pool().connection() as conn:
                rows = conn.execute(
                    """SELECT evidence FROM reaction_evidence_cache
                       WHERE reaction_key = %s AND provider = %s
                         AND provider_version = %s AND dataset_version = %s""",
                    (
                        reaction_key,
                        self.provider.name,
                        self.provider.version,
                        self.provider.dataset_version or "none",
                    ),
                ).fetchall()
            return rows[0]["evidence"] if rows else None
        except Exception:
            return None

    def _cache_put(self, reaction_key: str, evidence: dict) -> None:
        if not (self.use_cache and self._pool):
            return
        try:
            with self._pool().connection() as conn:
                conn.execute(
                    """INSERT INTO reaction_evidence_cache
                           (reaction_key, provider, provider_version,
                            dataset_version, evidence)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT (reaction_key, provider, provider_version,
                                    dataset_version)
                       DO UPDATE SET evidence = EXCLUDED.evidence,
                                     retrieved_at = now()""",
                    (
                        reaction_key,
                        self.provider.name,
                        self.provider.version,
                        self.provider.dataset_version or "none",
                        json.dumps(evidence),
                    ),
                )
                conn.commit()
        except Exception:
            #  A cache that cannot write is a slow cache, not a failure.
            pass

    #  --- lookup -----------------------------------------------------------

    def for_reaction(self, reaction: NormalizedReaction) -> dict:
        """Evidence for one normalised reaction, in API-transport shape.

        Returns a dict rather than ReactionEvidence so a cache hit and a fresh
        lookup are the same type. Rebuilding the nested dataclasses from cached
        JSON only to flatten them again would risk the two paths drifting - and
        an earlier draft of this did silently drop conditions on a cache hit.
        """
        cached = self._cache_get(reaction.reaction_key)
        if cached is not None:
            return {**cached, "cached": True}

        payload = self._retrieve(reaction).to_dict()
        self._cache_put(reaction.reaction_key, payload)
        return {**payload, "cached": False}

    def _retrieve(self, reaction: NormalizedReaction) -> ReactionEvidence:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        stamp = {
            "provider": self.provider.name,
            "provider_version": self.provider.version,
            "dataset_version": self.provider.dataset_version,
            "retrieved_at": now,
        }

        if not self.provider.available:
            result = unavailable(
                "No experimental evidence source is configured, so nothing was "
                "searched. This is not a statement about whether precedent "
                "exists. See docs/reaction-condition-intelligence.md to enable "
                "a source.",
                provider=self.provider.name,
            )
            result.retrieved_at = now
            return result

        try:
            direct = self.provider.find_exact_precedents(reaction, limit=MAX_DIRECT)
        except Exception:
            logger.warning("exact precedent lookup failed", exc_info=False)
            direct = []

        if direct:
            #  A single exact precedent is reported as-is; several are aggregated
            #  so one arbitrary record does not stand in for the set.
            conditions = (
                direct[0].conditions
                if len(direct) == 1
                else aggregate(direct, EvidenceLevel.EXPERIMENTAL)
            ) or ReactionConditions()
            return ReactionEvidence(
                evidence_level=EvidenceLevel.EXPERIMENTAL,
                conditions=conditions,
                direct_precedents=direct,
                reason=_no_conditions_reason(len(direct), "matching")
                if conditions.is_empty
                else None,
                **stamp,
            )

        try:
            similar = self.provider.find_similar_precedents(
                reaction, limit=MAX_SIMILAR, min_similarity=MIN_SIMILARITY
            )
        except Exception:
            logger.warning("similar precedent lookup failed", exc_info=False)
            similar = []

        if similar:
            conditions = aggregate(similar, EvidenceLevel.SIMILAR_EXPERIMENTAL)
            return ReactionEvidence(
                evidence_level=EvidenceLevel.SIMILAR_EXPERIMENTAL,
                conditions=conditions,
                similar_precedents=similar,
                reason=_no_conditions_reason(len(similar), "similar")
                if conditions.is_empty
                else None,
                **stamp,
            )

        if self.predictor.available:
            try:
                predicted = self.predictor.predict(reaction)
            except Exception:
                predicted = None
            if predicted is not None:
                return ReactionEvidence(
                    evidence_level=EvidenceLevel.PREDICTED,
                    conditions=predicted,
                    **stamp,
                )

        result = unavailable(
            self._not_found_reason(),
            provider=self.provider.name,
        )
        result.provider_version = self.provider.version
        result.dataset_version = self.provider.dataset_version
        result.retrieved_at = now
        return result

    def _not_found_reason(self) -> str:
        """Say what was searched, not what exists.

        "No literature exists" and "not in the corpus we indexed" are completely
        different claims, and only the second one is ours to make. The message
        therefore names the corpus and its size.
        """
        scope = self.provider.display_name
        count = self.provider.record_count
        searched = f"{scope} ({count:,} indexed reactions)" if count else scope
        return (
            f"No matching precedent found in {searched}. This means the reaction "
            "is absent from the indexed data - not that no experimental "
            "precedent exists. No condition-prediction model is configured, so "
            "no predicted conditions are offered either."
        )

    def for_step(self, reaction_node: dict, product_smiles: str) -> dict:
        """Evidence for one reaction node from a plan route tree."""
        try:
            reaction = normalize(
                reactants=[
                    r.get("molecule_smiles", "")
                    for r in reaction_node.get("reactants", [])
                ],
                products=[product_smiles],
                template_code=reaction_node.get("template_used"),
            )
        except NormalizationError as err:
            return unavailable(f"Reaction could not be normalised: {err}").to_dict()
        return self.for_reaction(reaction)
