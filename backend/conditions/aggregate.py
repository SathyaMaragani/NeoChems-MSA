"""Combine conditions across several precedents.

When ten reactions were run in four different solvents, there is no single
correct answer - so this reports what was *observed*, with counts, and never
collapses it into a recommendation. Language matters here: "observed range",
not "optimal", and never a fabricated consensus value.
"""
from __future__ import annotations

import statistics
from collections import Counter
from typing import Optional

from backend.conditions.schema import (
    ChemicalEntity,
    ConditionValue,
    EvidenceLevel,
    Precedent,
    ReactionConditions,
    SourceType,
)


def _source_type_for(evidence_level: EvidenceLevel) -> SourceType:
    """Aggregates over exact precedents are still dataset-sourced; only
    aggregates over similar reactions are SIMILAR_REACTION."""
    return (
        SourceType.DATASET
        if evidence_level is EvidenceLevel.EXPERIMENTAL
        else SourceType.SIMILAR_REACTION
    )


def _numeric_summary(
    values: list[ConditionValue],
    evidence_level: EvidenceLevel,
    normalized_unit: str,
) -> Optional[ConditionValue]:
    """Median plus observed range across precedents.

    Median rather than mean: reaction datasets carry outliers (a 200 °C entry
    among room-temperature runs) and the median is not dragged by them. The full
    range is kept so the spread stays visible rather than hidden behind a point.
    """
    numbers = [
        float(v.normalized_value)
        for v in values
        if v is not None and v.normalized_value is not None
    ]
    if not numbers:
        return None
    return ConditionValue(
        value=round(statistics.median(numbers), 2),
        unit=normalized_unit,
        normalized_value=round(statistics.median(numbers), 2),
        normalized_unit=normalized_unit,
        minimum=round(min(numbers), 2),
        maximum=round(max(numbers), 2),
        source_type=_source_type_for(evidence_level),
        evidence_level=evidence_level,
        observation_count=len(numbers),
        original_text=(
            f"median of {len(numbers)} observations, "
            f"range {min(numbers):g}-{max(numbers):g} {normalized_unit}"
        ),
    )


def _entity_frequency(
    groups: list[list[ChemicalEntity]], limit: int = 6
) -> list[ChemicalEntity]:
    """Rank chemicals by how many precedents used them.

    Keyed on SMILES where known and name otherwise, so the same reagent written
    two ways still counts once when structures resolve.
    """
    counter: Counter[str] = Counter()
    exemplar: dict[str, ChemicalEntity] = {}
    for group in groups:
        #  Count once per precedent, not once per mention.
        seen: set[str] = set()
        for entity in group:
            key = (entity.smiles or entity.name or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            counter[key] += 1
            exemplar.setdefault(key, entity)

    out: list[ChemicalEntity] = []
    total = len(groups)
    for key, count in counter.most_common(limit):
        base = exemplar[key]
        out.append(
            ChemicalEntity(
                name=base.name,
                smiles=base.smiles,
                role=base.role,
                amount=base.amount,
                equivalents=base.equivalents,
                #  The count is the useful part: "7 of 10 precedents".
                original_text=f"{base.original_text or base.name or base.smiles}"
                f" ({count}/{total} precedents)",
            )
        )
    return out


def aggregate(
    precedents: list[Precedent], evidence_level: EvidenceLevel
) -> ReactionConditions:
    """Summarise conditions across precedents, preserving the observations.

    The individual precedents are returned alongside this by the service, so
    nothing here is the only record of what was reported.
    """
    conditions = [p.conditions for p in precedents if p.conditions is not None]
    if not conditions:
        return ReactionConditions(evidence_level=EvidenceLevel.UNAVAILABLE)

    combined = ReactionConditions(
        evidence_level=evidence_level,
        reagents=_entity_frequency([c.reagents for c in conditions]),
        catalysts=_entity_frequency([c.catalysts for c in conditions]),
        solvents=_entity_frequency([c.solvents for c in conditions]),
        temperature=_numeric_summary(
            [c.temperature for c in conditions if c.temperature], evidence_level, "C"
        ),
        time=_numeric_summary(
            [c.time for c in conditions if c.time], evidence_level, "h"
        ),
        pressure=_numeric_summary(
            [c.pressure for c in conditions if c.pressure], evidence_level, "bar"
        ),
        yield_=_numeric_summary(
            [c.yield_ for c in conditions if c.yield_], evidence_level, "%"
        ),
        workup=sorted({w for c in conditions for w in c.workup})[:6],
        purification=sorted({p for c in conditions for p in c.purification})[:6],
        notes=(
            f"Observed across {len(conditions)} experimental precedent"
            f"{'' if len(conditions) == 1 else 's'}. Frequencies and ranges describe "
            "what was reported, not a recommended procedure."
        ),
    )
    #  Precedents can exist while recording no conditions at all - ORD stores
    #  plenty of reactions with neither temperature, time, solvent nor reagent.
    #  Attaching "Observed across 6 precedents" to an otherwise empty block
    #  asserts observations that were never made, so drop the whole block.
    if combined.is_empty:
        return ReactionConditions(evidence_level=EvidenceLevel.UNAVAILABLE)
    return combined


def frequency_table(precedents: list[Precedent]) -> dict:
    """Raw counts for display, e.g. solvent THF 7 / DMF 2 / EtOH 1."""
    def counts(attr: str) -> list[dict]:
        counter: Counter[str] = Counter()
        for p in precedents:
            if not p.conditions:
                continue
            seen: set[str] = set()
            for entity in getattr(p.conditions, attr):
                label = entity.name or entity.smiles
                if not label or label in seen:
                    continue
                seen.add(label)
                counter[label] += 1
        return [{"label": k, "count": v} for k, v in counter.most_common(8)]

    return {
        "solvents": counts("solvents"),
        "reagents": counts("reagents"),
        "catalysts": counts("catalysts"),
        "precedents": len(precedents),
    }
