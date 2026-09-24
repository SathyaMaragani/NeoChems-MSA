"""Normalise condition values without destroying what the source actually said.

Every helper returns both a normalised number and the original text. A record
saying "353 K" becomes 80 °C for comparison and aggregation, but the response
still carries "353 K" so a chemist can see the reported value.

Deliberately conservative: anything that cannot be parsed confidently is left
as text rather than guessed into a number.
"""
from __future__ import annotations

import re
from typing import Optional

from backend.conditions.schema import (
    ChemicalEntity,
    ConditionValue,
    EvidenceLevel,
    SourceType,
)

#  --- temperature ----------------------------------------------------------

_TEMP_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(?:°|deg(?:rees)?)?\s*([CKF])\b", re.IGNORECASE
)


def to_celsius(value: float, unit: str) -> Optional[float]:
    """Convert to Celsius. Unknown units return None rather than a guess."""
    u = (unit or "").strip().upper().lstrip("°")
    if u.startswith("C"):
        return round(float(value), 2)
    if u.startswith("K"):
        return round(float(value) - 273.15, 2)
    if u.startswith("F"):
        return round((float(value) - 32.0) * 5.0 / 9.0, 2)
    return None


def temperature(
    value: float | None,
    unit: str | None,
    *,
    source_type: SourceType,
    source_id: str,
    evidence_level: EvidenceLevel,
    original_text: str | None = None,
) -> Optional[ConditionValue]:
    if value is None or unit is None:
        return None
    celsius = to_celsius(value, unit)
    if celsius is None:
        return None
    return ConditionValue(
        value=round(float(value), 2),
        unit=unit,
        normalized_value=celsius,
        normalized_unit="C",
        source_type=source_type,
        source_id=source_id,
        evidence_level=evidence_level,
        original_text=original_text or f"{value} {unit}",
    )


def parse_temperature_text(text: str) -> Optional[tuple[float, str]]:
    """Pull a temperature out of free text. Returns (value, unit) or None."""
    if not text:
        return None
    match = _TEMP_RE.search(text)
    if not match:
        return None
    return float(match.group(1)), match.group(2).upper()


#  --- time -----------------------------------------------------------------

_TIME_UNITS = {
    "s": 1 / 3600, "sec": 1 / 3600, "secs": 1 / 3600, "second": 1 / 3600,
    "seconds": 1 / 3600,
    "m": 1 / 60, "min": 1 / 60, "mins": 1 / 60, "minute": 1 / 60, "minutes": 1 / 60,
    "h": 1.0, "hr": 1.0, "hrs": 1.0, "hour": 1.0, "hours": 1.0,
    "d": 24.0, "day": 24.0, "days": 24.0,
}
_TIME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]+)")


def to_hours(value: float, unit: str) -> Optional[float]:
    factor = _TIME_UNITS.get((unit or "").strip().lower())
    if factor is None:
        return None
    return round(float(value) * factor, 4)


def reaction_time(
    value: float | None,
    unit: str | None,
    *,
    source_type: SourceType,
    source_id: str,
    evidence_level: EvidenceLevel,
    original_text: str | None = None,
) -> Optional[ConditionValue]:
    if value is None or unit is None:
        return None
    hours = to_hours(value, unit)
    if hours is None:
        return None
    return ConditionValue(
        value=round(float(value), 4),
        unit=unit,
        normalized_value=hours,
        normalized_unit="h",
        source_type=source_type,
        source_id=source_id,
        evidence_level=evidence_level,
        original_text=original_text or f"{value} {unit}",
    )


def parse_time_text(text: str) -> Optional[tuple[float, str]]:
    if not text:
        return None
    for match in _TIME_RE.finditer(text):
        unit = match.group(2).lower()
        if unit in _TIME_UNITS:
            return float(match.group(1)), unit
    return None


#  --- yield ----------------------------------------------------------------


def percent_yield(
    value: float | None,
    *,
    source_type: SourceType,
    source_id: str,
    evidence_level: EvidenceLevel,
    original_text: str | None = None,
) -> Optional[ConditionValue]:
    """A yield outside 0-100 is a data error, not a finding - drop it."""
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= numeric <= 100.0:
        return None
    return ConditionValue(
        value=round(numeric, 2),
        unit="%",
        normalized_value=round(numeric, 2),
        normalized_unit="%",
        source_type=source_type,
        source_id=source_id,
        evidence_level=evidence_level,
        original_text=original_text or f"{numeric}%",
    )


#  --- pressure -------------------------------------------------------------

_PRESSURE_TO_BAR = {
    "bar": 1.0, "atm": 1.01325, "psi": 0.0689476, "kpa": 0.01, "pa": 1e-5,
    "mbar": 1e-3, "torr": 0.00133322, "mmhg": 0.00133322,
}


def to_bar(value: float, unit: str) -> Optional[float]:
    factor = _PRESSURE_TO_BAR.get((unit or "").strip().lower())
    if factor is None:
        return None
    return round(float(value) * factor, 5)


def pressure(
    value: float | None,
    unit: str | None,
    *,
    source_type: SourceType,
    source_id: str,
    evidence_level: EvidenceLevel,
    original_text: str | None = None,
) -> Optional[ConditionValue]:
    if value is None or unit is None:
        return None
    bar = to_bar(value, unit)
    if bar is None:
        return None
    return ConditionValue(
        value=round(float(value), 4),
        unit=unit,
        normalized_value=bar,
        normalized_unit="bar",
        source_type=source_type,
        source_id=source_id,
        evidence_level=evidence_level,
        original_text=original_text or f"{value} {unit}",
    )


#  --- chemical entities ----------------------------------------------------


def chemical(
    name: str | None,
    smiles: str | None = None,
    role: str | None = None,
    amount: str | None = None,
    equivalents: float | None = None,
    original_text: str | None = None,
) -> Optional[ChemicalEntity]:
    """Build an entity, keeping the source's own wording.

    A name that cannot be resolved to a structure keeps smiles=None. Guessing a
    structure from an ambiguous name is how you turn "TEA" into triethanolamine
    when the paper meant triethylamine.
    """
    if not name and not smiles:
        return None
    canonical_smiles = None
    if smiles:
        from backend.conditions.normalize import canonical

        canonical_smiles = canonical(smiles)
    return ChemicalEntity(
        name=name or None,
        smiles=canonical_smiles or (smiles or None),
        role=role,
        amount=amount,
        equivalents=equivalents,
        original_text=original_text or name or smiles,
    )


#  --- patent links ---------------------------------------------------------

#  At most 8 digits, so an 11-digit application number can never match; the
#  optional trailing kind code (A, B1, B2) is accepted and dropped.
_US_GRANT = re.compile(r"^US0*(\d{4,8})(?:[A-Z]\d?)?$")
_US_APPLICATION = re.compile(r"^US\d{11}A\d$")


def patent_url(patent_number: str | None) -> Optional[str]:
    """A Google Patents link built from a US patent number, or None.

    This is the only URL the evidence layer constructs, and it is deterministic
    rather than guessed: a real patent number maps to exactly one record. Both
    forms were checked against the live site - "US3930836" resolves, while the
    zero-padded "US03930836" that USPTO extractions use returns 404, so the
    padding is stripped. The kind code (A, B1, B2) is dropped even when the
    source supplies one: the kind-free URL resolves for every grant checked,
    while a kept kind code can 404 ("US6168655A" does; "US6168655" resolves).

    Anything that is not a recognisable US grant or application number gets no
    link. A DOI never does.
    """
    number = (patent_number or "").strip().upper()
    grant = _US_GRANT.match(number)
    if grant:
        return f"https://patents.google.com/patent/US{int(grant.group(1))}"
    if _US_APPLICATION.match(number):
        return f"https://patents.google.com/patent/{number}"
    return None
