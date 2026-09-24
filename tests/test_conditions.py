"""Unit tests for the reaction-condition evidence subsystem.

No database and no network: everything here runs against in-memory stubs so the
guarantees hold even on a machine that has never ingested ORD. The guarantees
being tested are mostly negative ones — what the system must NOT do — because
the failure mode that matters is fabricated chemistry, not a missing answer.
"""
import pathlib

import pytest

from backend.conditions.aggregate import aggregate, frequency_table
from backend.conditions.extract import (
    chemical,
    percent_yield,
    pressure,
    reaction_time,
    temperature,
    to_bar,
    to_celsius,
    to_hours,
)
from backend.conditions.normalize import (
    NormalizationError,
    normalize,
    substrate_similarity,
    transformation_similarity,
)
from backend.conditions.ord_provider import OrdProvider
from backend.conditions.providers import (
    NullProvider,
    UnavailablePredictionProvider,
)
from backend.conditions.schema import (
    ConditionValue,
    EvidenceLevel,
    Precedent,
    ReactionConditions,
    SourceType,
    unavailable,
)
from backend.conditions.service import ConditionsService

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
SALICYLIC = "O=C(O)c1ccccc1O"
ACETIC_ANHYDRIDE = "CC(=O)OC(C)=O"
PARACETAMOL = "CC(=O)Nc1ccc(O)cc1"
PARA_AMINOPHENOL = "Nc1ccc(O)cc1"
IBUPROFEN = "CC(C)Cc1ccc(C(C)C(=O)O)cc1"


# --------------------------------------------------------------------------
# ORD record parsing
# --------------------------------------------------------------------------


def ord_row(**overrides) -> dict:
    """One row in exactly the shape scripts/ingest_ord.py writes."""
    row = {
        "reaction_id": "ord-testrecord0000000000000000001",
        "reaction_smiles": f"{ACETIC_ANHYDRIDE}.{SALICYLIC}>>{ASPIRIN}",
        "reactants": [ACETIC_ANHYDRIDE, SALICYLIC],
        "products": [ASPIRIN],
        "dataset_version": "abc123def456",
        "conditions": {
            "reagents": [{"name": "Pyridine", "smiles": "c1ccncc1", "role": "REAGENT"}],
            "catalysts": [{"name": "Sulfuric acid", "smiles": "OS(=O)(=O)O",
                           "role": "CATALYST"}],
            "solvents": [{"name": "Toluene", "smiles": "Cc1ccccc1", "role": "SOLVENT"}],
            "temperature": {"value": 353.15, "unit": "K"},
            "time": {"value": 90.0, "unit": "minute"},
            "yield": {"value": 87.5},
            "workup": ["Filtration", "Wash"],
            "procedure_excerpt": "Stirred at reflux.",
        },
        "provenance": {
            "source_type": "paper",
            "source_id": "ord-testrecord0000000000000000001",
            "dataset_name": "Test dataset",
            "doi": "10.1000/example",
            "patent_number": None,
            "url": "https://example.org/articles/1",
            "license": "CC-BY-SA-4.0",
            "reaction_identifier": "ord-testrecord0000000000000000001",
        },
    }
    row.update(overrides)
    return row


def provider() -> OrdProvider:
    """OrdProvider with the database injection point stubbed out.

    Only the pure row -> domain conversion is exercised; anything that touches
    the pool would raise, which is what we want if a test strays.
    """
    return OrdProvider(pool_factory=lambda: (_ for _ in ()).throw(AssertionError("db")))


def test_ord_record_parses_into_domain_conditions():
    conditions = provider()._conditions(ord_row(), EvidenceLevel.EXPERIMENTAL)
    assert conditions.evidence_level is EvidenceLevel.EXPERIMENTAL
    assert [r.name for r in conditions.reagents] == ["Pyridine"]
    assert [c.name for c in conditions.catalysts] == ["Sulfuric acid"]
    assert [s.name for s in conditions.solvents] == ["Toluene"]
    assert conditions.workup == ["Filtration", "Wash"]


def test_ord_record_normalises_units_but_keeps_the_reported_value():
    conditions = provider()._conditions(ord_row(), EvidenceLevel.EXPERIMENTAL)
    #  353.15 K is 80 C; both must survive, so nobody has to trust our maths.
    assert conditions.temperature.normalized_value == 80.0
    assert conditions.temperature.normalized_unit == "C"
    assert conditions.temperature.value == 353.15
    assert conditions.temperature.unit == "K"
    assert "353.15 K" in conditions.temperature.original_text

    assert conditions.time.normalized_value == 1.5
    assert conditions.time.normalized_unit == "h"
    assert conditions.time.value == 90.0
    assert conditions.time.unit == "minute"


def test_provenance_is_preserved_exactly():
    prov = provider()._provenance(ord_row())
    assert prov.doi == "10.1000/example"
    assert prov.url == "https://example.org/articles/1"
    assert prov.dataset_name == "Test dataset"
    assert prov.license == "CC-BY-SA-4.0"
    assert prov.source_id == "ord-testrecord0000000000000000001"


def test_missing_doi_leaves_the_field_absent_rather_than_invented():
    row = ord_row()
    row["provenance"] = {**row["provenance"], "doi": None, "url": None}
    prov = provider()._provenance(row)
    assert prov.doi is None
    assert prov.url is None
    assert "doi" not in prov.to_dict()
    assert "url" not in prov.to_dict()


def test_url_is_never_constructed_from_a_doi():
    """A DOI without a URL must stay link-less.

    Building https://doi.org/<doi> ourselves would look authoritative while
    resolving to something we never checked.
    """
    row = ord_row()
    row["provenance"] = {**row["provenance"], "url": None}
    prov = provider()._provenance(row)
    assert prov.doi == "10.1000/example"
    assert prov.url is None
    assert "10.1000/example" not in str(prov.to_dict().get("url", ""))


def test_missing_temperature_is_omitted_not_defaulted():
    row = ord_row()
    row["conditions"] = {**row["conditions"], "temperature": None}
    conditions = provider()._conditions(row, EvidenceLevel.EXPERIMENTAL)
    assert conditions.temperature is None
    assert "temperature" not in conditions.to_dict()
    #  The rest of the record is still useful evidence.
    assert conditions.solvents


def test_missing_yield_is_omitted_not_zero():
    row = ord_row()
    row["conditions"] = {**row["conditions"], "yield": None}
    conditions = provider()._conditions(row, EvidenceLevel.EXPERIMENTAL)
    assert conditions.yield_ is None
    assert "yield" not in conditions.to_dict()


def test_out_of_range_yield_is_dropped_as_a_data_error():
    row = ord_row()
    row["conditions"] = {**row["conditions"], "yield": {"value": 940.0}}
    conditions = provider()._conditions(row, EvidenceLevel.EXPERIMENTAL)
    assert conditions.yield_ is None


def test_record_with_no_conditions_at_all_yields_an_empty_block():
    row = ord_row()
    row["conditions"] = {}
    conditions = provider()._conditions(row, EvidenceLevel.EXPERIMENTAL)
    assert conditions.is_empty
    assert conditions.to_dict()["evidence_level"] == "experimental"


# --------------------------------------------------------------------------
# unit conversion
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,unit,expected",
    [
        (25.0, "C", 25.0),
        (25.0, "°C", 25.0),
        (298.15, "K", 25.0),
        (77.0, "F", 25.0),
        (-40.0, "F", -40.0),
    ],
)
def test_temperature_conversion(value, unit, expected):
    assert to_celsius(value, unit) == pytest.approx(expected, abs=0.01)


def test_unknown_temperature_unit_returns_none_not_a_guess():
    assert to_celsius(25.0, "rankine") is None
    assert temperature(
        25.0, "rankine",
        source_type=SourceType.DATASET, source_id="x",
        evidence_level=EvidenceLevel.EXPERIMENTAL,
    ) is None


@pytest.mark.parametrize(
    "value,unit,expected",
    [
        (90.0, "minute", 1.5), (30.0, "s", 1 / 120), (2.0, "h", 2.0),
        (1.5, "days", 36.0), (45.0, "min", 0.75),
    ],
)
def test_time_conversion(value, unit, expected):
    assert to_hours(value, unit) == pytest.approx(expected, abs=1e-4)


def test_unknown_time_unit_returns_none():
    assert to_hours(5.0, "fortnights") is None
    assert reaction_time(
        5.0, "fortnights",
        source_type=SourceType.DATASET, source_id="x",
        evidence_level=EvidenceLevel.EXPERIMENTAL,
    ) is None


@pytest.mark.parametrize(
    "value,unit,expected",
    [(1.0, "bar", 1.0), (1.0, "atm", 1.01325), (14.5038, "psi", 1.0),
     (760.0, "torr", 1.01325)],
)
def test_pressure_conversion(value, unit, expected):
    assert to_bar(value, unit) == pytest.approx(expected, abs=0.001)


def test_pressure_keeps_the_reported_unit():
    value = pressure(
        30.0, "psi",
        source_type=SourceType.DATASET, source_id="x",
        evidence_level=EvidenceLevel.EXPERIMENTAL,
    )
    assert value.unit == "psi" and value.normalized_unit == "bar"
    assert value.normalized_value == pytest.approx(2.068, abs=0.001)


def test_chemical_without_a_name_or_smiles_is_dropped():
    assert chemical(None, None) is None


def test_chemical_name_is_never_resolved_to_a_guessed_structure():
    entity = chemical("TEA")
    assert entity.name == "TEA"
    assert entity.smiles is None


# --------------------------------------------------------------------------
# schema guarantees
# --------------------------------------------------------------------------


def test_confidence_is_rejected_on_experimental_values():
    """An experimentally reported number has no model confidence to report."""
    with pytest.raises(ValueError, match="only meaningful for predicted"):
        ConditionValue(
            value=80.0, unit="C",
            evidence_level=EvidenceLevel.EXPERIMENTAL, confidence=0.9,
        )


def test_confidence_is_allowed_only_on_predictions():
    value = ConditionValue(
        value=80.0, unit="C",
        evidence_level=EvidenceLevel.PREDICTED, confidence=0.9,
    )
    assert value.to_dict()["confidence"] == 0.9
    assert value.to_dict()["evidence_level"] == "predicted"


def test_serialisation_unwraps_enums_and_drops_empty_fields():
    payload = ConditionValue(
        value=80.0, unit="C", source_type=SourceType.DATASET,
        evidence_level=EvidenceLevel.EXPERIMENTAL,
    ).to_dict()
    assert payload["source_type"] == "dataset"
    assert payload["evidence_level"] == "experimental"
    assert "minimum" not in payload and "confidence" not in payload
    assert "SourceType." not in str(payload)


def test_unavailable_carries_a_reason_and_no_conditions():
    evidence = unavailable("nothing indexed", provider="ord").to_dict()
    assert evidence["evidence_level"] == "unavailable"
    assert evidence["reason"] == "nothing indexed"
    assert evidence["conditions"] == {"evidence_level": "unavailable"}
    assert evidence["precedent_count"] == 0


# --------------------------------------------------------------------------
# normalisation and similarity
# --------------------------------------------------------------------------


def test_reaction_key_is_order_independent():
    a = normalize([ACETIC_ANHYDRIDE, SALICYLIC], [ASPIRIN])
    b = normalize([SALICYLIC, ACETIC_ANHYDRIDE], [ASPIRIN])
    assert a.reaction_key == b.reaction_key


def test_reaction_key_ignores_smiles_spelling():
    a = normalize(["OC(=O)c1ccccc1O"], [ASPIRIN])
    b = normalize([SALICYLIC], [ASPIRIN])
    assert a.reaction_key == b.reaction_key


def test_reaction_key_separates_different_substrates():
    """Identity is substrate-specific, not template-specific.

    One template covers many substrates, so a template-only key would return
    precedents for entirely different molecules.
    """
    a = normalize([ACETIC_ANHYDRIDE, SALICYLIC], [ASPIRIN], template_hash="same")
    b = normalize([ACETIC_ANHYDRIDE, PARA_AMINOPHENOL], [PARACETAMOL],
                  template_hash="same")
    assert a.reaction_key != b.reaction_key


def test_unparseable_reaction_is_rejected_not_silently_accepted():
    with pytest.raises(NormalizationError):
        normalize(["not a molecule at all ((("], [ASPIRIN])
    with pytest.raises(NormalizationError):
        normalize([SALICYLIC], [])


def test_transformation_similarity_separates_related_from_unrelated():
    esterification = normalize([ACETIC_ANHYDRIDE, SALICYLIC], [ASPIRIN])
    amidation = normalize([ACETIC_ANHYDRIDE, PARA_AMINOPHENOL], [PARACETAMOL])
    unrelated = normalize(["CCBr", "[Na]C#N"], ["CCC#N"])
    assert transformation_similarity(esterification, esterification) == 1.0
    assert transformation_similarity(esterification, unrelated) < 0.2
    assert transformation_similarity(esterification, amidation) > transformation_similarity(
        esterification, unrelated
    )


def test_substrate_similarity_is_symmetric_enough_to_rank():
    a = normalize([ACETIC_ANHYDRIDE, SALICYLIC], [ASPIRIN])
    b = normalize([ACETIC_ANHYDRIDE, PARA_AMINOPHENOL], [PARACETAMOL])
    assert substrate_similarity(a, a) == pytest.approx(1.0)
    assert 0.0 < substrate_similarity(a, b) < 1.0


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------


def make_precedent(temp_c, time_h, yield_pct, solvent, reaction_id):
    return Precedent(
        reaction_id=reaction_id,
        conditions=ReactionConditions(
            evidence_level=EvidenceLevel.EXPERIMENTAL,
            solvents=[chemical(solvent)],
            temperature=temperature(
                temp_c, "C",
                source_type=SourceType.DATASET, source_id=reaction_id,
                evidence_level=EvidenceLevel.EXPERIMENTAL,
            ),
            time=reaction_time(
                time_h, "h",
                source_type=SourceType.DATASET, source_id=reaction_id,
                evidence_level=EvidenceLevel.EXPERIMENTAL,
            ),
            yield_=percent_yield(
                yield_pct,
                source_type=SourceType.DATASET, source_id=reaction_id,
                evidence_level=EvidenceLevel.EXPERIMENTAL,
            ),
        ),
    )


PRECEDENTS = [
    make_precedent(20.0, 2.0, 70.0, "THF", "r1"),
    make_precedent(80.0, 4.0, 75.0, "THF", "r2"),
    make_precedent(85.0, 6.0, 80.0, "DMF", "r3"),
    make_precedent(200.0, 8.0, 90.0, "THF", "r4"),
]


def test_aggregate_reports_median_and_the_observed_range():
    combined = aggregate(PRECEDENTS, EvidenceLevel.EXPERIMENTAL)
    #  Median (82.5), not mean (96.25) — the 200 C outlier must not drag it.
    assert combined.temperature.normalized_value == pytest.approx(82.5)
    assert combined.temperature.minimum == 20.0
    assert combined.temperature.maximum == 200.0
    assert combined.temperature.observation_count == 4
    assert "median of 4 observations" in combined.temperature.original_text


def test_aggregate_never_labels_a_range_as_optimal():
    combined = aggregate(PRECEDENTS, EvidenceLevel.EXPERIMENTAL)
    text = combined.to_dict()
    assert "optimal" not in str(text).lower()
    assert "recommend" in combined.notes  # "not a recommended procedure"
    assert "not a recommended procedure" in combined.notes


def test_aggregate_counts_entity_frequency_once_per_precedent():
    combined = aggregate(PRECEDENTS, EvidenceLevel.EXPERIMENTAL)
    top = combined.solvents[0]
    assert top.name == "THF"
    assert "(3/4 precedents)" in top.original_text


def test_aggregate_of_exact_matches_is_not_labelled_similar():
    combined = aggregate(PRECEDENTS, EvidenceLevel.EXPERIMENTAL)
    assert combined.temperature.source_type is SourceType.DATASET
    combined_similar = aggregate(PRECEDENTS, EvidenceLevel.SIMILAR_EXPERIMENTAL)
    assert combined_similar.temperature.source_type is SourceType.SIMILAR_REACTION


def test_aggregate_of_nothing_is_unavailable():
    combined = aggregate([], EvidenceLevel.EXPERIMENTAL)
    assert combined.evidence_level is EvidenceLevel.UNAVAILABLE
    assert combined.is_empty


def test_aggregate_skips_precedents_missing_a_value():
    partial = PRECEDENTS + [Precedent(
        reaction_id="r5",
        conditions=ReactionConditions(evidence_level=EvidenceLevel.EXPERIMENTAL,
                                      solvents=[chemical("THF")]),
    )]
    combined = aggregate(partial, EvidenceLevel.EXPERIMENTAL)
    assert combined.temperature.observation_count == 4  # not 5
    assert "(4/5 precedents)" in combined.solvents[0].original_text


def test_frequency_table_counts_solvents():
    table = frequency_table(PRECEDENTS)
    assert table["precedents"] == 4
    assert table["solvents"][0] == {"label": "THF", "count": 3}


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------


def test_null_provider_returns_nothing_and_says_so():
    null = NullProvider()
    assert null.available is False
    reaction = normalize([SALICYLIC, ACETIC_ANHYDRIDE], [ASPIRIN])
    assert null.find_exact_precedents(reaction) == []
    assert null.find_similar_precedents(reaction) == []
    assert null.get_details("anything") is None


def test_service_with_null_provider_reports_unavailable_with_a_reason():
    service = ConditionsService(provider=NullProvider(), use_cache=False)
    evidence = service.for_reaction(normalize([SALICYLIC], [ASPIRIN]))
    assert evidence["evidence_level"] == "unavailable"
    assert "no experimental evidence source" in evidence["reason"].lower()
    assert evidence["precedent_count"] == 0
    assert evidence["conditions"] == {"evidence_level": "unavailable"}


def test_prediction_provider_is_absent_by_design_and_returns_nothing():
    predictor = UnavailablePredictionProvider()
    assert predictor.available is False
    assert predictor.predict(normalize([SALICYLIC], [ASPIRIN])) is None
    #  The licensing reason is documented on the class, not buried in a commit.
    assert "non-commercial" in UnavailablePredictionProvider.__doc__


# --------------------------------------------------------------------------
# service orchestration, against stub providers
# --------------------------------------------------------------------------


class StubProvider(NullProvider):
    name = "stub"
    version = "1"

    def __init__(self, exact=(), similar=(), dataset_version="v1", fail=False):
        self._exact = list(exact)
        self._similar = list(similar)
        self._dataset_version = dataset_version
        self._fail = fail

    @property
    def available(self):
        return True

    @property
    def dataset_version(self):
        return self._dataset_version

    def find_exact_precedents(self, reaction, limit=10):
        if self._fail:
            raise RuntimeError("database is down")
        return self._exact[:limit]

    def find_similar_precedents(self, reaction, limit=10, min_similarity=0.3):
        if self._fail:
            raise RuntimeError("database is down")
        return self._similar[:limit]


REACTION = normalize([ACETIC_ANHYDRIDE, SALICYLIC], [ASPIRIN])


def test_exact_match_is_labelled_experimental():
    service = ConditionsService(
        provider=StubProvider(exact=PRECEDENTS), use_cache=False
    )
    evidence = service.for_reaction(REACTION)
    assert evidence["evidence_level"] == "experimental"
    assert evidence["precedent_count"] == 4
    assert evidence["conditions"]["evidence_level"] == "experimental"


def test_single_exact_match_is_reported_verbatim_not_aggregated():
    one = [make_precedent(80.0, 4.0, 75.0, "THF", "r1")]
    service = ConditionsService(provider=StubProvider(exact=one), use_cache=False)
    conditions = service.for_reaction(REACTION)["conditions"]
    assert conditions["temperature"]["value"] == 80.0
    #  No aggregate wording, because there is nothing to aggregate.
    assert "minimum" not in conditions["temperature"]


def test_similar_match_is_labelled_similar_not_experimental():
    service = ConditionsService(
        provider=StubProvider(similar=PRECEDENTS), use_cache=False
    )
    evidence = service.for_reaction(REACTION)
    assert evidence["evidence_level"] == "similar_experimental"
    assert evidence["conditions"]["evidence_level"] == "similar_experimental"
    assert "direct_precedents" not in evidence
    assert len(evidence["similar_precedents"]) == 4


def test_exact_match_wins_over_similar():
    exact = [make_precedent(30.0, 1.0, 60.0, "MeCN", "exact1")]
    service = ConditionsService(
        provider=StubProvider(exact=exact, similar=PRECEDENTS), use_cache=False
    )
    evidence = service.for_reaction(REACTION)
    assert evidence["evidence_level"] == "experimental"
    assert "similar_precedents" not in evidence


def test_no_match_is_unavailable_and_invents_nothing():
    service = ConditionsService(provider=StubProvider(), use_cache=False)
    evidence = service.for_reaction(REACTION)
    assert evidence["evidence_level"] == "unavailable"
    assert evidence["precedent_count"] == 0
    assert "no matching precedent found" in evidence["reason"].lower()
    assert evidence["conditions"] == {"evidence_level": "unavailable"}


def test_provider_failure_degrades_to_unavailable_rather_than_raising():
    service = ConditionsService(provider=StubProvider(fail=True), use_cache=False)
    evidence = service.for_reaction(REACTION)
    assert evidence["evidence_level"] == "unavailable"


def test_evidence_carries_provider_and_dataset_version():
    service = ConditionsService(
        provider=StubProvider(exact=PRECEDENTS, dataset_version="v7"), use_cache=False
    )
    evidence = service.for_reaction(REACTION)
    assert evidence["provider"] == "stub"
    assert evidence["dataset_version"] == "v7"
    assert evidence["retrieved_at"]


def test_unnormalisable_step_returns_unavailable_not_an_exception():
    service = ConditionsService(provider=StubProvider(), use_cache=False)
    evidence = service.for_step({"reactants": [{"molecule_smiles": "@@@"}]}, ASPIRIN)
    assert evidence["evidence_level"] == "unavailable"
    assert "could not be normalised" in evidence["reason"]


# --------------------------------------------------------------------------
# caching, with an in-memory pool stand-in
# --------------------------------------------------------------------------


class FakeConnection:
    def __init__(self, store):
        self.store = store

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("SELECT"):
            self.result = [{"evidence": self.store[params]}] if params in self.store else []
        else:
            key, provider_name, provider_version, dataset_version, payload = params
            import json as _json

            self.store[(key, provider_name, provider_version, dataset_version)] = (
                _json.loads(payload)
            )
            self.result = []
        return self

    def fetchall(self):
        return self.result

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakePool:
    def __init__(self):
        self.store = {}

    def connection(self):
        return FakeConnection(self.store)


def test_second_lookup_is_served_from_cache():
    pool = FakePool()
    provider_a = StubProvider(exact=PRECEDENTS)
    service = ConditionsService(provider=provider_a, pool_factory=lambda: pool)

    first = service.for_reaction(REACTION)
    assert first["cached"] is False
    second = service.for_reaction(REACTION)
    assert second["cached"] is True
    #  A cache hit must carry the same evidence, not a hollowed-out copy. An
    #  earlier draft dropped conditions on the cached path.
    assert second["evidence_level"] == first["evidence_level"]
    assert second["conditions"] == first["conditions"]
    assert second["direct_precedents"] == first["direct_precedents"]


def test_a_new_dataset_version_invalidates_cached_evidence():
    pool = FakePool()
    old = ConditionsService(
        provider=StubProvider(exact=PRECEDENTS, dataset_version="v1"),
        pool_factory=lambda: pool,
    )
    old.for_reaction(REACTION)
    assert old.for_reaction(REACTION)["cached"] is True

    refreshed = ConditionsService(
        provider=StubProvider(exact=PRECEDENTS, dataset_version="v2"),
        pool_factory=lambda: pool,
    )
    assert refreshed.for_reaction(REACTION)["cached"] is False


def test_a_new_provider_version_invalidates_cached_evidence():
    pool = FakePool()
    first = StubProvider(exact=PRECEDENTS)
    ConditionsService(provider=first, pool_factory=lambda: pool).for_reaction(REACTION)

    second = StubProvider(exact=PRECEDENTS)
    second.version = "2"
    assert ConditionsService(
        provider=second, pool_factory=lambda: pool
    ).for_reaction(REACTION)["cached"] is False


def test_a_broken_cache_is_a_slow_cache_not_a_failure():
    class BrokenPool:
        def connection(self):
            raise RuntimeError("no database")

    service = ConditionsService(
        provider=StubProvider(exact=PRECEDENTS), pool_factory=lambda: BrokenPool()
    )
    evidence = service.for_reaction(REACTION)
    assert evidence["evidence_level"] == "experimental"
    assert evidence["cached"] is False


# --------------------------------------------------------------------------
# language guarantees
# --------------------------------------------------------------------------


def test_nothing_in_the_payload_calls_a_score_a_yield_or_a_probability():
    service = ConditionsService(
        provider=StubProvider(similar=PRECEDENTS), use_cache=False
    )
    payload = str(service.for_reaction(REACTION))
    for phrase in ("success probability", "probability of success", "will work",
                   "guaranteed", "verified experimentally"):
        assert phrase not in payload.lower()


def test_similar_evidence_never_claims_the_exact_reaction_was_run():
    service = ConditionsService(
        provider=StubProvider(similar=PRECEDENTS), use_cache=False
    )
    evidence = service.for_reaction(REACTION)
    assert evidence["evidence_level"] != "experimental"
    assert evidence["conditions"]["notes"].startswith("Observed across")


# --------------------------------------------------------------------------
# terminology: what the system is allowed to claim
# --------------------------------------------------------------------------


def test_not_found_names_the_corpus_and_its_size_not_the_literature():
    """"Not in our index" and "no literature exists" are different claims.

    Only the first is ours to make, so the message must name the corpus that was
    actually searched and how big it is.
    """
    class SizedStub(StubProvider):
        name = "sized"
        display_name = "Test Corpus"

        @property
        def record_count(self):
            return 216681

    reason = ConditionsService(
        provider=SizedStub(), use_cache=False
    ).for_reaction(REACTION)["reason"]

    assert "Test Corpus" in reason
    assert "216,681 indexed reactions" in reason
    assert "absent from the indexed data" in reason
    for forbidden in ("no literature", "no precedent exists", "does not exist",
                      "never been done", "no published"):
        assert forbidden not in reason.lower(), reason


def test_unconfigured_source_says_nothing_was_searched():
    reason = ConditionsService(
        provider=NullProvider(), use_cache=False
    ).for_reaction(REACTION)["reason"]
    assert "nothing was searched" in reason
    assert "not a statement about whether precedent exists" in reason.lower()


def test_similar_evidence_never_carries_the_direct_label():
    """A similar-reaction match must never surface as an exact one."""
    service = ConditionsService(
        provider=StubProvider(similar=PRECEDENTS), use_cache=False
    )
    evidence = service.for_reaction(REACTION)
    assert evidence["evidence_level"] == "similar_experimental"
    assert evidence["conditions"]["evidence_level"] == "similar_experimental"
    #  Every aggregated value is tagged as coming from a similar reaction, so a
    #  consumer reading one field in isolation still cannot mistake it.
    for key in ("temperature", "time", "yield"):
        assert evidence["conditions"][key]["source_type"] == "similar_reaction"
        assert evidence["conditions"][key]["evidence_level"] == "similar_experimental"


def test_direct_evidence_is_not_tagged_as_a_similar_reaction():
    """Regression: aggregates over EXACT matches once claimed source_type
    'similar_reaction', which understated evidence the wrong way round."""
    service = ConditionsService(
        provider=StubProvider(exact=PRECEDENTS), use_cache=False
    )
    conditions = service.for_reaction(REACTION)["conditions"]
    for key in ("temperature", "time", "yield"):
        assert conditions[key]["source_type"] == "dataset"
        assert conditions[key]["evidence_level"] == "experimental"


def test_no_enum_repr_ever_reaches_the_payload():
    """Regression: asdict() left Enum members in place, which serialised as
    "<SourceType.DATASET: 'dataset'>" once they hit a JSON column."""
    service = ConditionsService(
        provider=StubProvider(exact=PRECEDENTS), use_cache=False
    )
    payload = str(service.for_reaction(REACTION))
    assert "SourceType." not in payload
    assert "EvidenceLevel." not in payload
    assert "<" not in payload


def test_empty_aggregate_does_not_claim_observations():
    """Regression: precedents that record NO conditions produced a conditions
    block whose only content was "Observed across 6 experimental precedents" —
    asserting observations that were never made."""
    bare = [
        Precedent(reaction_id=f"r{i}",
                  conditions=ReactionConditions(
                      evidence_level=EvidenceLevel.SIMILAR_EXPERIMENTAL))
        for i in range(6)
    ]
    combined = aggregate(bare, EvidenceLevel.SIMILAR_EXPERIMENTAL)
    assert combined.evidence_level is EvidenceLevel.UNAVAILABLE
    assert combined.is_empty
    assert combined.notes is None
    assert combined.to_dict() == {"evidence_level": "unavailable"}


def test_precedents_without_conditions_are_reported_as_such():
    """The transformation is attested; the conditions are not. Say both."""
    bare = [
        Precedent(reaction_id=f"r{i}",
                  conditions=ReactionConditions(
                      evidence_level=EvidenceLevel.SIMILAR_EXPERIMENTAL))
        for i in range(3)
    ]
    service = ConditionsService(provider=StubProvider(similar=bare), use_cache=False)
    evidence = service.for_reaction(REACTION)
    assert evidence["evidence_level"] == "similar_experimental"
    assert evidence["precedent_count"] == 3
    assert evidence["conditions"] == {"evidence_level": "unavailable"}
    assert "none of those records include reaction conditions" in evidence["reason"]
    assert "transformation is attested" in evidence["reason"]


def test_a_failed_availability_probe_is_not_cached_forever():
    """One blip must not black out the evidence layer until restart.

    Observed during the V2.1 audit: a transient database hiccup left
    `available` permanently False, and two tests skipped with no visible cause.
    """
    from backend.conditions.ord_provider import OrdProvider

    calls = {"n": 0}

    class Flaky:
        def connection(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("database still starting")
            return self

        def execute(self, *_a, **_k):
            return self

        def fetchall(self):
            return [{"n": 216681}]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    provider = OrdProvider(pool_factory=Flaky)
    assert provider.available is False          # first probe fails
    provider.UNAVAILABLE_TTL_SECONDS = 0.0      # let it re-check immediately
    assert provider.available is True           # recovers on its own
    assert provider.record_count == 216681


# --------------------------------------------------------------------------
# V2.2 retrieval: the benchmark's ground truth must never reach the retriever
# --------------------------------------------------------------------------


def test_retrieval_never_selects_or_ranks_on_reaction_class():
    """`reaction_class` is the label the retrieval benchmark grades against.

    It is stored in the index so the API can say "this precedent is a Suzuki".
    If any retrieval query ever selected or ordered on it, the benchmark would
    be measuring the retriever reading the answer key, and every number in
    docs/retrieval-benchmark.md would be meaningless.
    """
    source = pathlib.Path("backend/conditions/retrieval.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "reaction_class" not in code, (
        "retrieval.py references reaction_class outside a comment - that is the "
        "benchmark's ground truth leaking into the thing being benchmarked"
    )


def test_every_retrieval_strategy_excludes_the_query_itself():
    """A strategy that returns the query reaction scores a free, false win."""
    source = pathlib.Path("backend/conditions/retrieval.py").read_text(encoding="utf-8")
    for name in ("by_product", "by_transformation", "by_substrate", "hybrid"):
        start = source.index(f"def {name}(")
        end = source.find("\ndef ", start + 1)
        body = source[start:end if end != -1 else len(source)]
        assert "reaction_key <> %s" in body, f"{name} does not exclude the query"


def test_reaction_centre_is_substrate_independent_but_transformation_specific():
    """The centre must collide for one transformation across substrates, and
    separate different transformations - otherwise it is useless as a key."""
    from backend.conditions.normalize import reaction_centre

    ester_a = normalize([ACETIC_ANHYDRIDE, SALICYLIC], [ASPIRIN])
    ester_b = normalize([ACETIC_ANHYDRIDE, "O=C(O)c1ccc(C)cc1O"],
                        ["CC(=O)Oc1cc(C)ccc1C(=O)O"])
    amide = normalize([ACETIC_ANHYDRIDE, PARA_AMINOPHENOL], [PARACETAMOL])

    centre_a, size_a = reaction_centre(ester_a)
    centre_b, _ = reaction_centre(ester_b)
    centre_amide, _ = reaction_centre(amide)

    assert centre_a and size_a > 0
    assert centre_a == centre_b, "same transformation, different acid, must collide"
    assert centre_a != centre_amide, "esterification and amidation must differ"


def test_reaction_centre_of_a_non_reaction_is_empty_not_invented():
    from backend.conditions.normalize import reaction_centre

    nothing = normalize([ASPIRIN], [ASPIRIN])
    assert reaction_centre(nothing) == ("", 0)



# --------------------------------------------------------------------------
# patent links
# --------------------------------------------------------------------------


def test_patent_url_strips_zero_padding_without_guessing_a_kind_code():
    from backend.conditions.extract import patent_url

    #  "US03930836" is how the USPTO extraction writes it; that form 404s.
    assert patent_url("US03930836") == "https://patents.google.com/patent/US3930836"
    assert patent_url("US06000000") == "https://patents.google.com/patent/US6000000"
    #  Grants from 2001 on carry a kind code; it is dropped, because the
    #  kind-coded "US6168655A" 404s while the kind-free URL resolves.
    assert patent_url("US07842696B2") == "https://patents.google.com/patent/US7842696"
    assert patent_url("US06168655A") == "https://patents.google.com/patent/US6168655"
    assert patent_url("US20010000011A1") == (
        "https://patents.google.com/patent/US20010000011A1")


def test_only_recognisable_patent_numbers_get_a_link():
    from backend.conditions.extract import patent_url

    for value in (None, "", "EP1234567", "US12", "10.1038/s41557",
                  "https://doi.org/10.1038/s41557", "WO2010000001"):
        assert patent_url(value) is None, value


def test_a_derived_patent_link_is_labelled_as_derived():
    row = ord_row()
    row["provenance"] = {
        "source_type": "patent", "source_id": "US03930836",
        "patent_number": "US03930836", "title": "2-Bromoethyl ethanesulfonate",
        "year": 1976, "url": "https://patents.google.com/patent/US3930836",
        "url_origin": "derived_from_patent_number", "license": "CC0-1.0",
    }
    prov = provider()._provenance(row).to_dict()
    assert prov["url_origin"] == "derived_from_patent_number"
    assert prov["patent_number"] == "US03930836"
    assert prov["title"] == "2-Bromoethyl ethanesulfonate"
    assert prov["year"] == 1976
    assert "doi" not in prov


def test_a_record_supplied_url_is_labelled_as_from_the_source():
    prov = provider()._provenance(ord_row()).to_dict()
    assert prov["url_origin"] == "source"
