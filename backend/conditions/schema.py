"""Domain model for reaction conditions and the evidence behind them.

The governing principle is traceability over completeness: every value carries
where it came from, and a missing value stays missing rather than being filled
with something plausible.

Deliberately independent of both AiZynthFinder and ORD. Nothing here imports
either, so providers can be added or swapped without touching this module, and
no ORD protobuf ever reaches the API surface.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class EvidenceLevel(str, Enum):
    """How a condition came to be known. These are never blurred."""

    #  A reported experiment on this exact transformation and substrates.
    EXPERIMENTAL = "experimental"
    #  Reported experiments on chemically similar reactions.
    SIMILAR_EXPERIMENTAL = "similar_experimental"
    #  A model's output. Never presented as having been run.
    PREDICTED = "predicted"
    #  Nothing found, and nothing invented.
    UNAVAILABLE = "unavailable"


class SourceType(str, Enum):
    PAPER = "paper"
    PATENT = "patent"
    DATASET = "dataset"
    INTERNAL_EXPERIMENT = "internal_experiment"
    PREDICTED = "predicted"
    SIMILAR_REACTION = "similar_reaction"


def _drop_none(value: Any) -> Any:
    """Strip empty fields recursively and unwrap enums to their values.

    A field that is absent must not appear as null in the API: the reader
    cannot tell a measured zero from a missing measurement if we emit both.
    Enums are unwrapped here because asdict() leaves them as members, which
    serialise as reprs once they reach a JSON column.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None and v != [] and v != {}}
    if isinstance(value, list):
        return [_drop_none(v) for v in value]
    return value


@dataclass
class Provenance:
    """Where a piece of evidence came from. Only known fields are emitted.

    No field is ever synthesised - in particular a DOI or URL is carried only
    when the source actually supplied one. Guessing a DOI from a title would
    produce a link that looks authoritative and resolves to the wrong paper, or
    to nothing.
    """

    source_type: SourceType
    source_id: str
    dataset_name: Optional[str] = None
    dataset_version: Optional[str] = None
    title: Optional[str] = None
    authors: Optional[list[str]] = None
    journal: Optional[str] = None
    year: Optional[int] = None
    #  The DOI of the publication that deposited/curated the dataset (e.g. USPTO patent dataset). 
    #  This is often NOT the publication where the specific experiment was performed!
    doi: Optional[str] = None
    patent_number: Optional[str] = None
    example_number: Optional[str] = None
    url: Optional[str] = None
    #  "source" when the record supplied the link; "derived_from_patent_number"
    #  when it was built from a verified patent number. Never derived from a DOI.
    url_origin: Optional[str] = None
    reaction_identifier: Optional[str] = None
    source_text_reference: Optional[str] = None
    retrieved_at: Optional[str] = None
    license: Optional[str] = None

    def to_dict(self) -> dict:
        return _drop_none(asdict(self))


@dataclass
class ConditionValue:
    """One condition, with its provenance attached.

    `confidence` is populated ONLY by model predictions. An experimentally
    reported temperature has no meaningful confidence score, and attaching one
    would invent a statistic; experimental strength is expressed through
    evidence_level and the precedent count instead.
    """

    value: Any
    unit: Optional[str] = None
    #  Canonical form for comparison (e.g. Kelvin normalised to Celsius) while
    #  `original_text` keeps exactly what the source said.
    normalized_value: Optional[Any] = None
    normalized_unit: Optional[str] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    source_type: Optional[SourceType] = None
    source_id: Optional[str] = None
    evidence_level: EvidenceLevel = EvidenceLevel.UNAVAILABLE
    confidence: Optional[float] = None
    original_text: Optional[str] = None
    #  How many precedents contributed, when aggregated.
    observation_count: Optional[int] = None

    def __post_init__(self) -> None:
        if (
            self.confidence is not None
            and self.evidence_level is not EvidenceLevel.PREDICTED
        ):
            raise ValueError(
                "confidence is only meaningful for predicted values; experimental "
                "evidence is described by evidence_level and observation_count"
            )

    def to_dict(self) -> dict:
        return _drop_none(asdict(self))


@dataclass
class ChemicalEntity:
    """A reagent, catalyst or solvent.

    Keeps the source's own wording alongside any structure we could resolve.
    Names are not guessed: an unresolvable name keeps `smiles = None`.
    """

    name: Optional[str] = None
    smiles: Optional[str] = None
    role: Optional[str] = None
    amount: Optional[str] = None
    equivalents: Optional[float] = None
    concentration: Optional[str] = None
    original_text: Optional[str] = None

    def to_dict(self) -> dict:
        return _drop_none(asdict(self))


@dataclass
class ReactionConditions:
    """The conditions for one reaction step, as far as they are known."""

    evidence_level: EvidenceLevel = EvidenceLevel.UNAVAILABLE
    reagents: list[ChemicalEntity] = field(default_factory=list)
    catalysts: list[ChemicalEntity] = field(default_factory=list)
    solvents: list[ChemicalEntity] = field(default_factory=list)
    temperature: Optional[ConditionValue] = None
    time: Optional[ConditionValue] = None
    pressure: Optional[ConditionValue] = None
    atmosphere: Optional[ConditionValue] = None
    yield_: Optional[ConditionValue] = None
    workup: list[str] = field(default_factory=list)
    purification: list[str] = field(default_factory=list)
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        out = {
            "evidence_level": self.evidence_level.value,
            "reagents": [r.to_dict() for r in self.reagents],
            "catalysts": [c.to_dict() for c in self.catalysts],
            "solvents": [s.to_dict() for s in self.solvents],
            "temperature": self.temperature.to_dict() if self.temperature else None,
            "time": self.time.to_dict() if self.time else None,
            "pressure": self.pressure.to_dict() if self.pressure else None,
            "atmosphere": self.atmosphere.to_dict() if self.atmosphere else None,
            "yield": self.yield_.to_dict() if self.yield_ else None,
            "workup": self.workup,
            "purification": self.purification,
            "notes": self.notes,
        }
        return _drop_none(out)

    @property
    def is_empty(self) -> bool:
        return not any(
            [
                self.reagents,
                self.catalysts,
                self.solvents,
                self.temperature,
                self.time,
                self.pressure,
                self.atmosphere,
                self.yield_,
                self.workup,
                self.purification,
            ]
        )


@dataclass
class Precedent:
    """One reported reaction offered as evidence.

    `similarity` is a chemical-similarity score, never a probability that the
    target reaction will work.
    """

    reaction_id: str
    reaction_smiles: Optional[str] = None
    conditions: Optional[ReactionConditions] = None
    provenance: Optional[Provenance] = None
    
    # Measured using the `reaction_difference_fp` (shows exact bond changes). Range: 0.0 - 1.0.
    transformation_similarity: Optional[float] = None
    # Measured using Morgan fingerprints of the reactants. Range: 0.0 - 1.0.
    substrate_similarity: Optional[float] = None
    # The weighted combined score used for ranking (currently 0.30 * trans + 0.70 * sub).
    combined_similarity: Optional[float] = None
    
    # Deprecated fallback similarity, retains value of combined_similarity for backwards compat.
    similarity: Optional[float] = None
    
    match_type: Optional[str] = None  # "exact" | "transformation" | "similar"

    def to_dict(self) -> dict:
        return _drop_none(
            {
                "reaction_id": self.reaction_id,
                "reaction_smiles": self.reaction_smiles,
                "conditions": self.conditions.to_dict() if self.conditions else None,
                "provenance": self.provenance.to_dict() if self.provenance else None,
                "similarity": self.similarity,
                "transformation_similarity": self.transformation_similarity,
                "substrate_similarity": self.substrate_similarity,
                "combined_similarity": self.combined_similarity,
                "match_type": self.match_type,
            }
        )


@dataclass
class ReactionEvidence:
    """Everything known about one reaction step's conditions and support."""

    evidence_level: EvidenceLevel = EvidenceLevel.UNAVAILABLE
    conditions: ReactionConditions = field(default_factory=ReactionConditions)
    direct_precedents: list[Precedent] = field(default_factory=list)
    similar_precedents: list[Precedent] = field(default_factory=list)
    provider: Optional[str] = None
    provider_version: Optional[str] = None
    dataset_version: Optional[str] = None
    retrieved_at: Optional[str] = None
    cached: bool = False
    #  Human-readable explanation when nothing was found, shown instead of a
    #  blank panel.
    reason: Optional[str] = None

    @property
    def precedent_count(self) -> int:
        return len(self.direct_precedents) + len(self.similar_precedents)

    def to_dict(self) -> dict:
        return _drop_none(
            {
                "evidence_level": self.evidence_level.value,
                "conditions": self.conditions.to_dict(),
                "direct_precedents": [p.to_dict() for p in self.direct_precedents],
                "similar_precedents": [p.to_dict() for p in self.similar_precedents],
                "precedent_count": self.precedent_count,
                "provider": self.provider,
                "provider_version": self.provider_version,
                "dataset_version": self.dataset_version,
                "retrieved_at": self.retrieved_at,
                "cached": self.cached,
                "reason": self.reason,
            }
        )


def unavailable(reason: str, provider: str | None = None) -> ReactionEvidence:
    """The honest empty result. Used wherever nothing could be found."""
    return ReactionEvidence(
        evidence_level=EvidenceLevel.UNAVAILABLE,
        conditions=ReactionConditions(evidence_level=EvidenceLevel.UNAVAILABLE),
        provider=provider,
        reason=reason,
    )
