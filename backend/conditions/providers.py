"""Evidence provider interface.

Everything downstream talks to this, never to a concrete source. Adding Lowe
USPTO, patents, an ELN or a commercial database means writing one class here -
no change to normalisation, aggregation, the API or the frontend.
"""
from __future__ import annotations

import abc
from typing import Optional

from backend.conditions.normalize import NormalizedReaction
from backend.conditions.schema import Precedent


class LiteratureProvider(abc.ABC):
    """A source of experimentally reported reactions.

    "Literature" here names the *kind* of source, not a claim of coverage. No
    provider searches the scientific literature; each searches a finite indexed
    corpus, and `record_count` is how the UI states that scope honestly.
    """

    #: Short stable name recorded in evidence provenance and the cache key.
    name: str = "abstract"
    #: Bumped when retrieval logic changes in a way that invalidates cached rows.
    version: str = "0"
    #: Human-readable name of the corpus, for UI text that must not imply more
    #: coverage than exists.
    display_name: str = "unconfigured evidence source"

    @property
    def dataset_version(self) -> Optional[str]:
        """Version of the underlying data, so cached evidence cannot silently
        outlive the dataset it was derived from."""
        return None

    @property
    def record_count(self) -> Optional[int]:
        """How many reactions are actually indexed.

        Absence of a match means absence from THIS many records - never absence
        from the chemical literature. Every "not found" message quotes it.
        """
        return None

    @property
    def available(self) -> bool:
        """False when the provider is configured but its data is absent."""
        return True

    @abc.abstractmethod
    def find_exact_precedents(
        self, reaction: NormalizedReaction, limit: int = 10
    ) -> list[Precedent]:
        """Reactions whose normalised identity matches this one."""

    @abc.abstractmethod
    def find_similar_precedents(
        self, reaction: NormalizedReaction, limit: int = 10, min_similarity: float = 0.3
    ) -> list[Precedent]:
        """Chemically similar reported reactions, ranked most similar first."""

    @abc.abstractmethod
    def get_details(self, source_id: str) -> Optional[Precedent]:
        """Full record for one precedent."""


class NullProvider(LiteratureProvider):
    """The default when no evidence source is configured.

    Returns nothing rather than anything invented. The rest of the application
    continues to work; steps simply report evidence_level "unavailable".
    """

    name = "null"
    version = "1"
    display_name = "no evidence source configured"

    @property
    def available(self) -> bool:
        return False

    @property
    def record_count(self) -> Optional[int]:
        return 0

    def find_exact_precedents(self, reaction, limit: int = 10) -> list[Precedent]:
        return []

    def find_similar_precedents(
        self, reaction, limit: int = 10, min_similarity: float = 0.3
    ) -> list[Precedent]:
        return []

    def get_details(self, source_id: str) -> Optional[Precedent]:
        return None


class ConditionPredictionProvider(abc.ABC):
    """A model that predicts conditions when no precedent exists.

    Kept separate from LiteratureProvider so a prediction can never be mistaken
    for a retrieval: predictions are the only thing allowed to carry a
    confidence, and they are labelled PREDICTED at every layer.
    """

    name: str = "abstract"
    version: str = "0"

    @property
    @abc.abstractmethod
    def available(self) -> bool:
        ...

    @abc.abstractmethod
    def predict(self, reaction: NormalizedReaction):
        """Return ReactionConditions with evidence_level PREDICTED, or None."""


class UnavailablePredictionProvider(ConditionPredictionProvider):
    """No condition-prediction model is wired in.

    This is a deliberate state, not an oversight. The strong published models
    (e.g. the Gao et al. condition recommender as shipped in ASKCOS) are trained
    on licensed reaction data and distributed under non-commercial terms, which
    does not fit a product intended for commercial use. Rather than ship a
    licence problem or fabricate numbers, this returns nothing and the UI says
    no prediction is available.

    See docs/reaction-condition-intelligence.md for the evaluation criteria a
    replacement must meet.
    """

    name = "unavailable"
    version = "1"

    @property
    def available(self) -> bool:
        return False

    def predict(self, reaction: NormalizedReaction):
        return None
