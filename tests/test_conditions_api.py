"""API-level tests for condition evidence, including the ORD-backed path.

The ORD tests skip rather than fail when the evidence index has not been
ingested: a fresh checkout has no 390 MB of reaction data, and the rest of the
suite must still be runnable there.
"""
import pathlib

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
IBUPROFEN = "CC(C)Cc1ccc(C(C)C(=O)O)cc1"
PARACETAMOL = "CC(=O)Nc1ccc(O)cc1"

#  A reaction verified present in the ingested ORD index: N-methylation of a
#  bromo-fluoro-indazole with dimethyl sulfate, from an HTE dataset that records
#  temperature, time, yield and a DOI.
ORD_REACTANTS = ["COS(=O)(=O)OC", "Fc1cc(Br)cc2cn[nH]c12"]
ORD_PRODUCT = "Cn1ncc2cc(Br)cc(F)c21"

#  Not a real reaction: ethane to a fluorinated indazole. Nothing can match it,
#  which is the point.
NONSENSE_REACTANTS = ["CC"]
NONSENSE_PRODUCT = "FC(F)(F)c1ccc2[nH]ncc2c1C1CCCCC1"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def ord_available(client) -> bool:
    body = client.get("/retrosynthesis/evidence/status").json()
    return body["provider"] == "ord" and body["available"]


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


def test_status_names_the_provider_and_its_licence(client):
    body = client.get("/retrosynthesis/evidence/status").json()
    assert body["provider"] in {"ord", "null"}
    assert set(body["evidence_levels"]) == {
        "experimental", "similar_experimental", "predicted", "unavailable",
    }
    if body["provider"] == "ord":
        #  The licence travels with the data. A consumer cannot use the evidence
        #  without being told the terms - ORD is ShareAlike, which matters.
        assert "CC-BY-SA-4.0 (Open Reaction Database)" in body["data_license"]


def test_status_admits_that_no_prediction_model_is_configured(client):
    model = client.get("/retrosynthesis/evidence/status").json()["prediction_model"]
    assert model["available"] is False
    assert "non-commercial" in model["note"]


# --------------------------------------------------------------------------
# /retrosynthesis/conditions
# --------------------------------------------------------------------------


def test_invalid_smiles_is_a_400_not_a_500(client):
    r = client.post(
        "/retrosynthesis/conditions",
        json={"reactants": ["not a molecule ((("], "products": [ASPIRIN]},
    )
    assert r.status_code == 400
    assert "parseable" in r.json()["detail"]


def test_missing_reactants_is_rejected_by_validation(client):
    r = client.post("/retrosynthesis/conditions",
                    json={"reactants": [], "products": [ASPIRIN]})
    #  The app maps validation errors to 400 rather than FastAPI's default 422.
    assert r.status_code == 400
    assert r.json()["detail"][0]["type"] == "too_short"


def test_a_reaction_with_no_precedent_returns_a_body_not_an_error(client):
    r = client.post(
        "/retrosynthesis/conditions",
        json={"reactants": NONSENSE_REACTANTS, "products": [NONSENSE_PRODUCT]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unavailable"
    assert body["evidence_level"] == "unavailable"
    assert body["reason"]
    #  Nothing invented to fill the gap.
    assert body["conditions"] == {"evidence_level": "unavailable"}
    assert body["precedent_count"] == 0


def test_the_normalised_reaction_is_echoed_back(client):
    body = client.post(
        "/retrosynthesis/conditions",
        json={"reactants": ["OC(=O)c1ccccc1O", "CC(=O)OC(C)=O"], "products": [ASPIRIN]},
    ).json()
    reaction = body["reaction"]
    #  Canonicalised and sorted, so the caller can see what was actually queried.
    assert reaction["products"] == [ASPIRIN]
    assert len(reaction["reaction_key"]) == 64
    assert ">>" in reaction["reaction_smiles"]


def test_exact_ord_match_is_experimental_with_real_provenance(client, ord_available):
    if not ord_available:
        pytest.skip("ORD index not ingested")
    body = client.post(
        "/retrosynthesis/conditions",
        json={"reactants": ORD_REACTANTS, "products": [ORD_PRODUCT]},
    ).json()

    assert body["status"] == "available"
    assert body["evidence_level"] == "experimental"
    assert body["precedent_count"] >= 1
    assert body["provider"] == "ord"
    assert body["dataset_version"]

    precedent = body["direct_precedents"][0]
    assert precedent["match_type"] == "exact"
    assert precedent["reaction_id"].startswith("ord-")
    prov = precedent["provenance"]
    assert prov["license"] == "CC-BY-SA-4.0"
    assert prov["source_id"] == precedent["reaction_id"]
    #  A link is present only if ORD recorded one; it is never derived from the
    #  DOI, so a DOI-only record must have no url at all.
    if "url" not in prov:
        assert "doi" in prov or "patent_number" in prov


def test_experimental_conditions_carry_no_model_confidence(client, ord_available):
    if not ord_available:
        pytest.skip("ORD index not ingested")
    conditions = client.post(
        "/retrosynthesis/conditions",
        json={"reactants": ORD_REACTANTS, "products": [ORD_PRODUCT]},
    ).json()["conditions"]
    for key in ("temperature", "time", "yield", "pressure"):
        if key in conditions:
            assert "confidence" not in conditions[key]
            assert conditions[key]["evidence_level"] in {
                "experimental", "similar_experimental",
            }


def test_repeated_lookup_is_served_from_cache(client, ord_available):
    if not ord_available:
        pytest.skip("ORD index not ingested")
    payload = {"reactants": ORD_REACTANTS, "products": [ORD_PRODUCT]}
    first = client.post("/retrosynthesis/conditions", json=payload).json()
    second = client.post("/retrosynthesis/conditions", json=payload).json()
    assert second["cached"] is True
    #  Identical content on both paths, not a hollowed-out cached copy.
    assert second["evidence_level"] == first["evidence_level"]
    assert second["conditions"] == first["conditions"]
    assert second["precedent_count"] == first["precedent_count"]


# --------------------------------------------------------------------------
# plan integration
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def aspirin_plan_plain(client):
    return client.post(
        "/retrosynthesis/plan", json={"smiles": ASPIRIN, "top_n": 2}
    ).json()


def test_plan_is_unchanged_when_conditions_are_not_requested(aspirin_plan_plain):
    """Default-off must be byte-identical to the pre-evidence response shape."""
    assert aspirin_plan_plain["is_solved"] is True
    for route in aspirin_plan_plain["routes"]:
        assert "evidence_summary" not in route

        def walk(node):
            for reaction in node["reactions"]:
                assert "evidence" not in reaction
                for child in reaction["reactants"]:
                    walk(child)

        walk(route["tree"])


def test_plan_surfaces_template_identity_without_mislabelling_it(aspirin_plan_plain):
    reaction = aspirin_plan_plain["routes"][0]["tree"]["reactions"][0]
    assert reaction["template_hash"]
    #  A library count, and named as one. Not a success count, yield or odds.
    assert isinstance(reaction["template_occurrence"], int)
    assert reaction["template_occurrence"] >= 1


def test_plan_with_conditions_labels_every_step(client):
    body = client.post(
        "/retrosynthesis/plan",
        json={"smiles": ASPIRIN, "top_n": 2, "include_conditions": True},
    ).json()
    assert body["is_solved"] is True

    for route in body["routes"]:
        summary = route["evidence_summary"]
        assert summary["steps"] == route["number_of_reactions"]
        assert 0.0 <= summary["evidence_coverage"] <= 1.0
        counted = (
            summary["steps_with_experimental_evidence"]
            + summary["steps_with_similar_evidence"]
            + summary["steps_predicted"]
            + summary["steps_without_evidence"]
        )
        assert counted == summary["steps"]

        def walk(node):
            for reaction in node["reactions"]:
                evidence = reaction["evidence"]
                assert evidence["evidence_level"] in {
                    "experimental", "similar_experimental", "predicted", "unavailable",
                }
                if evidence["evidence_level"] == "unavailable":
                    assert evidence["reason"]
                    assert evidence.get("conditions", {}) == {
                        "evidence_level": "unavailable"
                    }
                for child in reaction["reactants"]:
                    walk(child)

        walk(route["tree"])


def test_conditions_do_not_change_whether_a_route_is_solved(client):
    """Evidence is decoration on a verdict the search already reached."""
    plain = client.post(
        "/retrosynthesis/plan", json={"smiles": PARACETAMOL, "top_n": 3}
    ).json()
    enriched = client.post(
        "/retrosynthesis/plan",
        json={"smiles": PARACETAMOL, "top_n": 3, "include_conditions": True},
    ).json()

    assert plain["is_solved"] is enriched["is_solved"] is True
    assert plain["solved_routes_found"] == enriched["solved_routes_found"]
    assert len(plain["routes"]) == len(enriched["routes"])
    assert [r["number_of_reactions"] for r in plain["routes"]] == [
        r["number_of_reactions"] for r in enriched["routes"]
    ]


def test_ibuprofen_stays_unsolved_when_conditions_are_requested(client):
    """THE safeguard.

    Ibuprofen does not solve at the default 100 iterations with the USPTO
    template set - every candidate route bottoms out in something not in stock.
    Condition evidence must not change that: a step can have five reported
    precedents and the route is still unsolved, because "solved" is a statement
    about purchasable starting material, not about literature support.
    """
    plain = client.post(
        "/retrosynthesis/plan", json={"smiles": IBUPROFEN, "top_n": 3}
    ).json()
    enriched = client.post(
        "/retrosynthesis/plan",
        json={"smiles": IBUPROFEN, "top_n": 3, "include_conditions": True},
    ).json()

    assert plain["is_solved"] is False, "fixture assumption: ibuprofen is unsolved here"
    assert enriched["is_solved"] is False
    assert enriched["routes"] == []
    assert enriched["solved_routes_found"] == 0


def test_evidence_failure_does_not_fail_the_plan(client, monkeypatch):
    """A dead evidence database must cost conditions, never the route."""
    from backend.retrosynthesis import service as retro_service

    class ExplodingService:
        def for_step(self, *_args, **_kwargs):
            raise RuntimeError("evidence backend is on fire")

    monkeypatch.setattr(
        retro_service.RetrosynthesisService,
        "_conditions_service",
        lambda self: ExplodingService(),
    )

    body = client.post(
        "/retrosynthesis/plan",
        json={"smiles": PARACETAMOL, "top_n": 2, "include_conditions": True},
    ).json()

    assert body["is_solved"] is True
    assert body["routes"]
    route = body["routes"][0]
    assert route["evidence_summary"]["steps_without_evidence"] >= 1
    assert route["evidence_summary"]["evidence_coverage"] == 0.0

    def walk(node):
        for reaction in node["reactions"]:
            assert reaction["evidence"]["evidence_level"] == "unavailable"
            assert "failed" in reaction["evidence"]["reason"].lower()
            for child in reaction["reactants"]:
                walk(child)

    walk(route["tree"])


def test_requesting_conditions_does_not_slow_the_search_itself(client):
    """Enrichment happens after the search, so search time should not move."""
    plain = client.post(
        "/retrosynthesis/plan", json={"smiles": PARACETAMOL, "top_n": 1}
    ).json()
    enriched = client.post(
        "/retrosynthesis/plan",
        json={"smiles": PARACETAMOL, "top_n": 1, "include_conditions": True},
    ).json()
    assert enriched["search_time_seconds"] < plain["search_time_seconds"] * 3 + 5


# --------------------------------------------------------------------------
# privacy
# --------------------------------------------------------------------------


def test_the_evidence_layer_makes_no_outbound_network_calls():
    """User structures must not leave the machine.

    Ingestion downloads from Hugging Face - that is a deliberate, offline,
    operator-run step. Nothing in the serving path may open a socket.
    """
    served = pathlib.Path("backend/conditions")
    offenders = []
    for path in served.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in ("import requests", "urllib.request", "httpx", "aiohttp",
                      "socket.create_connection"):
            if token in text:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, f"serving path must stay offline: {offenders}"


def test_proprietary_structures_are_not_written_to_logs():
    """No logger call in the evidence path interpolates a SMILES."""
    served = pathlib.Path("backend/conditions")
    offenders = []
    for path in served.glob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith("logger."):
                continue
            if "smiles" in stripped.lower() or "reaction_key" in stripped.lower():
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"log lines may leak structures: {offenders}"


# --------------------------------------------------------------------------
# terminology audit: what the product is allowed to claim
# --------------------------------------------------------------------------

FRONTEND = pathlib.Path("frontend/src")


def test_status_states_the_indexed_scope_not_the_literature(client):
    body = client.get("/retrosynthesis/evidence/status").json()
    assert body["provider_display_name"]
    assert "not from a search of the scientific literature" in body["coverage_note"]
    if body["available"]:
        assert isinstance(body["indexed_reactions"], int)
        assert body["indexed_reactions"] > 0


def test_not_found_names_the_corpus_it_searched(client, ord_available):
    if not ord_available:
        pytest.skip("ORD index not ingested")
    reason = client.post(
        "/retrosynthesis/conditions",
        json={"reactants": NONSENSE_REACTANTS, "products": [NONSENSE_PRODUCT]},
    ).json()["reason"]
    assert "Open Reaction Database" in reason
    assert "indexed reactions" in reason
    assert "absent from the indexed data" in reason
    for forbidden in ("no literature", "does not exist", "never been done",
                      "no published", "no precedent exists"):
        assert forbidden not in reason.lower(), reason


def test_route_summary_does_not_call_records_literature_sources(client):
    body = client.post(
        "/retrosynthesis/plan",
        json={"smiles": ASPIRIN, "top_n": 1, "include_conditions": True},
    ).json()
    summary = body["routes"][0]["evidence_summary"]
    #  These are distinct source RECORDS in one indexed corpus.
    assert "distinct_sources" in summary
    assert "literature_sources" not in summary


def test_ui_never_claims_the_route_came_from_the_matched_source():
    """The most dangerous possible misreading, guarded at the source level.

    A matched record is evidence found AFTER the route was proposed. The UI must
    say so wherever records are displayed.
    """
    results = (FRONTEND / "workspaces/results.tsx").read_text(encoding="utf-8")
    assert "did not take this step from these records" in results
    assert "matched against it afterwards" in results


def test_ui_distinguishes_direct_from_similar_in_the_label_itself():
    results = (FRONTEND / "workspaces/results.tsx").read_text(encoding="utf-8")
    assert "DIRECT EXPERIMENTAL PRECEDENT" in results
    assert "SIMILAR EXPERIMENTAL PRECEDENT" in results
    assert "AI-PREDICTED CONDITIONS" in results
    assert "NO VERIFIED CONDITION EVIDENCE" in results
    #  Not distinguished by colour alone, and not only in a tooltip.
    assert "different substrates. Not a precedent for this reaction." in results


def test_ui_never_says_no_literature_exists():
    """"Not in the index" is ours to say. "No literature exists" is not."""
    banned = [
        "no literature", "not in the literature", "no published",
        "does not exist in the literature", "never been reported",
        "no precedent exists", "unprecedented",
    ]
    offenders = []
    for path in list(FRONTEND.rglob("*.tsx")) + list(FRONTEND.rglob("*.ts")):
        text = path.read_text(encoding="utf-8").lower()
        for phrase in banned:
            if phrase in text:
                offenders.append(f"{path.name}: {phrase!r}")
    assert not offenders, offenders


def test_ui_never_calls_a_similarity_or_policy_score_a_probability_of_success():
    banned = [
        "success probability", "probability of success", "chance of success",
        "likely to work", "will work", "guaranteed", "recommended conditions",
        "optimal conditions", "verified experimentally",
    ]
    offenders = []
    for path in list(FRONTEND.rglob("*.tsx")) + list(FRONTEND.rglob("*.ts")):
        text = path.read_text(encoding="utf-8").lower()
        for phrase in banned:
            start = 0
            while (at := text.find(phrase, start)) != -1:
                start = at + 1
                #  A banned phrase under a negation ("NOT a probability the
                #  reaction will work") is the guarantee being stated, not a
                #  claim being made. Only unnegated uses are offences.
                if "not " in text[max(0, at - 60):at]:
                    continue
                offenders.append(f"{path.name}: {phrase!r} at {at}")
    assert not offenders, offenders


# --------------------------------------------------------------------------
# regression: the similar-precedent path must actually run
# --------------------------------------------------------------------------

#  One atom away from ORD_PRODUCT (Br -> Cl). The exact reaction_key cannot
#  match, but the transformation is the same and the product is close, so the
#  similar path MUST return something. It returned nothing for the life of the
#  first implementation, because two stacked bugs in the prefilter were
#  swallowed by a bare `except`.
NEAR_MISS_REACTANTS = ["COS(=O)(=O)OC", "Fc1cc(Cl)cc2cn[nH]c12"]
NEAR_MISS_PRODUCT = "Cn1ncc2cc(Cl)cc(F)c21"


def test_similar_precedent_retrieval_is_not_silently_dead(client, ord_available):
    """Regression for the defect this audit found.

    1. The cartridge's tanimoto_threshold GUC does not exist until an rdkit
       function has been called in the session, so setting it raised.
    2. Plain SET takes no bind parameter, so the statement was a syntax error
       even once the GUC existed.

    Both were caught by `except Exception: return []`, so every similar lookup
    returned an empty list and every such step reported "no evidence".
    """
    if not ord_available:
        pytest.skip("ORD index not ingested")
    body = client.post(
        "/retrosynthesis/conditions",
        json={"reactants": NEAR_MISS_REACTANTS, "products": [NEAR_MISS_PRODUCT]},
    ).json()

    assert body["evidence_level"] == "similar_experimental", body.get("reason")
    assert body["precedent_count"] >= 1
    assert "direct_precedents" not in body
    top = body["similar_precedents"][0]
    assert top["match_type"] == "similar"
    #  Bounded by the production floor rather than a hardcoded number, so
    #  retuning the retriever cannot silently invalidate this test.
    from backend.conditions.service import MIN_SIMILARITY
    assert MIN_SIMILARITY <= top["similarity"] < 1.0


def test_a_near_miss_is_never_promoted_to_a_direct_precedent(client, ord_available):
    if not ord_available:
        pytest.skip("ORD index not ingested")
    near = client.post(
        "/retrosynthesis/conditions",
        json={"reactants": NEAR_MISS_REACTANTS, "products": [NEAR_MISS_PRODUCT]},
    ).json()
    exact = client.post(
        "/retrosynthesis/conditions",
        json={"reactants": ORD_REACTANTS, "products": [ORD_PRODUCT]},
    ).json()

    assert near["evidence_level"] == "similar_experimental"
    assert exact["evidence_level"] == "experimental"
    #  The two must not share a reaction_key, or the "near miss" is not one.
    assert near["reaction"]["reaction_key"] != exact["reaction"]["reaction_key"]
    #  Every value in the similar result is tagged as coming from a similar
    #  reaction, so no single field can be read as this reaction's own.
    for key in ("temperature", "time", "yield"):
        if key in near["conditions"]:
            assert near["conditions"][key]["source_type"] == "similar_reaction"
            assert near["conditions"][key]["evidence_level"] == "similar_experimental"


# --------------------------------------------------------------------------
# V2.2 transformation-keyed retrieval
# --------------------------------------------------------------------------


def test_the_sfp_difference_fingerprint_is_never_used_as_an_index_key():
    """Regression for a silent-zero that would have faked a negative result.

    A GiST index over `sfp` returns ZERO rows for the `%` operator at the
    thresholds this system uses - measured at 0 against a sequential scan's 557,
    under both gist_sfp_ops and gist_sfp_low_ops. Keying the transformation
    prefilter on it therefore looks exactly like "transformation retrieval finds
    nothing", which is the wrong conclusion to draw from a broken index.

    The prefilter must use `transformation_bfp` (a bit vector, which indexes
    correctly); the difference fingerprint is for SCORING the survivors.
    """
    source = pathlib.Path("backend/conditions/retrieval.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "transformation_sfp %%" not in code, (
        "transformation_sfp is used with the % operator - a GiST index over sfp "
        "silently returns nothing at these thresholds"
    )
    assert "transformation_bfp %%" in code, (
        "the transformation prefilter must key on the indexable bit vector"
    )


@pytest.fixture(scope="session")
def index_available() -> bool:
    from backend.molrepr.search import pool
    try:
        with pool().connection() as conn:
            rows = conn.execute(
                "SELECT count(*) AS n FROM ord_reaction_index "
                "WHERE transformation_bfp IS NOT NULL").fetchall()
        return bool(rows and rows[0]["n"] > 0)
    except Exception:
        return False


def test_every_retrieval_strategy_returns_candidates(index_available):
    """Each strategy must actually retrieve something for a reaction that is in
    the index - the failure this catches is silent, not loud."""
    if not index_available:
        pytest.skip("reaction index not built")
    from backend.conditions import retrieval
    from backend.conditions.normalize import normalize
    from backend.molrepr.search import pool

    reaction = normalize(ORD_REACTANTS, [ORD_PRODUCT])
    empty = []
    for name, strategy in retrieval.STRATEGIES.items():
        with pool().connection() as conn:
            candidates = strategy(conn, reaction, limit=200)
        if not candidates:
            empty.append(name)
    assert not empty, f"strategies returned nothing at all: {empty}"


def test_transformation_retrieval_finds_the_same_chemistry_on_other_substrates(
        index_available):
    """The point of keying on the transformation: a query should retrieve its
    own reaction type even when the products look different."""
    if not index_available:
        pytest.skip("reaction index not built")
    from backend.conditions import retrieval
    from backend.conditions.normalize import normalize
    from backend.molrepr.search import pool

    reaction = normalize(ORD_REACTANTS, [ORD_PRODUCT])
    with pool().connection() as conn:
        candidates = retrieval.by_transformation(conn, reaction, limit=50)
    assert candidates
    #  Strategies select and score; ORDERING is the service's job, so assert on
    #  content rather than sequence.
    assert max(c.transformation for c in candidates) > 0.0
    #  Every candidate is scored on every component, whichever key selected it -
    #  otherwise the benchmark would compare strategies under different ranking
    #  formulas and measure the harness instead of the chemistry.
    assert max(c.substrate for c in candidates) > 0.0
    assert max(c.product for c in candidates) > 0.0
