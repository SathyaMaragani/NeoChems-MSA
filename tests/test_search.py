"""Search endpoints. Requires the DB up and ingested:
   docker compose up -d && python scripts/ingest_molecules.py
"""
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.molrepr import search

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
IBUPROFEN = "CC(C)Cc1ccc(C(C)C(=O)O)cc1"
PARACETAMOL = "CC(=O)Nc1ccc(O)cc1"

# Verified against the cartridge, not assumed: aspirin and ibuprofen carry a
# carboxylic acid; paracetamol is an anilide + phenol and carries none.
CARBOXYLIC_ACID = "C(=O)[OH]"
BENZENE = "c1ccccc1"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def _db(client):
    if search.get_molecule(1) is None:
        pytest.skip("molecules table empty - run scripts/ingest_molecules.py")


def test_exact_search_finds_aspirin(client, _db):
    body = client.post("/search/exact", json={"smiles": ASPIRIN}).json()
    assert body["canonical_smiles"] == ASPIRIN
    assert body["inchikey"] == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
    assert body["source"] == "public"


def test_exact_search_of_salt_finds_parent(client, _db):
    """A caller pasting a salt form should still find the parent record."""
    r = client.post("/search/exact", json={"smiles": "CC(=O)Oc1ccccc1C(=O)[O-].[Na+]"})
    assert r.status_code == 200
    assert r.json()["canonical_smiles"] == ASPIRIN


def test_exact_search_miss_returns_404(client, _db):
    # A real molecule that is not an approved drug.
    r = client.post("/search/exact", json={"smiles": "CCCCCCCCCCCCCCCCCCBr"})
    assert r.status_code == 404


def test_similarity_returns_self_first_with_score_one(client, _db):
    body = client.post(
        "/search/similarity", json={"smiles": ASPIRIN, "top_n": 5}
    ).json()
    top = body["results"][0]
    assert top["canonical_smiles"] == ASPIRIN
    assert top["tanimoto"] == 1.0
    assert len(body["results"]) == 5
    # ranked descending
    scores = [r["tanimoto"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)


def test_similarity_min_similarity_filters(client, _db):
    body = client.post(
        "/search/similarity",
        json={"smiles": ASPIRIN, "top_n": 50, "min_similarity": 0.5},
    ).json()
    assert all(r["tanimoto"] >= 0.5 for r in body["results"])


def test_substructure_carboxylic_acid_discriminates(client, _db):
    """aspirin + ibuprofen match; paracetamol has no carboxylic acid."""
    body = client.post(
        "/search/substructure",
        json={"smiles_pattern": CARBOXYLIC_ACID, "top_n": 500},
    ).json()
    hits = {r["canonical_smiles"] for r in body["results"]}
    assert ASPIRIN in hits
    assert IBUPROFEN in hits
    assert PARACETAMOL not in hits


def test_substructure_benzene_matches_all_three(client, _db):
    body = client.post(
        "/search/substructure", json={"smiles_pattern": BENZENE, "top_n": 500}
    ).json()
    hits = {r["canonical_smiles"] for r in body["results"]}
    assert {ASPIRIN, IBUPROFEN, PARACETAMOL} <= hits


def test_get_molecule_returns_record_and_image(client, _db):
    molecule_id = client.post("/search/exact", json={"smiles": ASPIRIN}).json()["id"]
    body = client.get(f"/molecules/{molecule_id}").json()
    assert body["canonical_smiles"] == ASPIRIN
    assert body["image_png_base64"].startswith("iVBOR")
    assert client.get(f"/molecules/{molecule_id}?include_image=false").json()[
        "image_png_base64"
    ] is None


def test_get_molecule_unknown_id_returns_404(client, _db):
    assert client.get("/molecules/99999999").status_code == 404


def test_represent_needs_no_database(client):
    body = client.post("/molecules/represent", json={"smiles": ASPIRIN}).json()
    assert body["canonical_smiles"] == ASPIRIN
    assert body["inchikey"] == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
    assert body["morgan_n_bits"] == 2048
    assert body["image_png_base64"].startswith("iVBOR")


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/search/exact", {"smiles": "not_a_molecule"}),
        ("/search/similarity", {"smiles": "C(C(C"}),
        ("/search/substructure", {"smiles_pattern": "not_a_molecule"}),
        ("/molecules/represent", {"smiles": ""}),
    ],
)
def test_invalid_smiles_returns_400(client, path, payload):
    r = client.post(path, json=payload)
    assert r.status_code == 400
    assert "detail" in r.json()


def test_mineral_salts_are_separate_records(client, _db):
    """Lithium carbonate (bipolar) and calcium carbonate (antacid) must not
    collapse into one row - a search for one would silently return the other."""
    lithium = client.post(
        "/search/exact", json={"smiles": "O=C([O-])[O-].[Li+].[Li+]"}
    ).json()
    calcium = client.post(
        "/search/exact", json={"smiles": "O=C([O-])[O-].[Ca+2]"}
    ).json()
    assert lithium["id"] != calcium["id"]
    assert lithium["is_mineral_salt"] is True
    assert calcium["is_mineral_salt"] is True
    assert "Li" in lithium["canonical_smiles"]
    assert "Ca" in calcium["canonical_smiles"]


def test_similarity_for_lithium_carbonate_ranks_itself_first(client, _db):
    body = client.post(
        "/search/similarity",
        json={"smiles": "O=C([O-])[O-].[Li+].[Li+]", "top_n": 3},
    ).json()
    assert body["results"][0]["canonical_smiles"] == "O=C([O-])[O-].[Li+].[Li+]"
    assert body["results"][0]["tanimoto"] == 1.0


def test_both_fingerprint_columns_are_populated(_db):
    with search.pool().connection() as conn:
        row = conn.execute(
            """SELECT count(*) AS n,
                      count(morgan_fingerprint) AS blind,
                      count(morgan_fingerprint_chiral) AS chiral
               FROM molecules"""
        ).fetchone()
    assert row["blind"] == row["n"] and row["chiral"] == row["n"]


def test_stored_chiral_column_separates_enantiomers(_db):
    """The blind column ties racemic and (S)-ibuprofen at 1.0; the chiral one
    must not. Guards the column QSAR will train on."""
    from backend.molrepr import service as svc

    with search.pool().connection() as conn:
        rows = conn.execute(
            """SELECT morgan_fingerprint, morgan_fingerprint_chiral
               FROM molecules
               WHERE canonical_smiles IN (%s, %s) ORDER BY id""",
            ("CC(C)Cc1ccc(C(C)C(=O)O)cc1", "CC(C)Cc1ccc([C@H](C)C(=O)O)cc1"),
        ).fetchall()
    assert len(rows) == 2
    a, b = rows
    blind = svc.tanimoto(
        svc.fingerprint_from_bytes(a["morgan_fingerprint"]),
        svc.fingerprint_from_bytes(b["morgan_fingerprint"]),
    )
    chiral = svc.tanimoto(
        svc.fingerprint_from_bytes(a["morgan_fingerprint_chiral"]),
        svc.fingerprint_from_bytes(b["morgan_fingerprint_chiral"]),
    )
    assert blind == 1.0
    assert chiral < blind


def test_similarity_search_stays_stereo_blind(_db, client):
    """Search behaviour must be unchanged by adding the chiral column."""
    body = client.post(
        "/search/similarity",
        json={"smiles": "CC(C)Cc1ccc([C@H](C)C(=O)O)cc1", "top_n": 2},
    ).json()
    assert {r["tanimoto"] for r in body["results"]} == {1.0}


def test_molecule_stats_reports_the_real_table_size(client, _db):
    """The UI needs a true library size. substructure_search caps its count at
    top_n, so using that as the total silently reports the cap instead."""
    body = client.get("/molecules/stats").json()
    assert body["total"] > 2000
    assert body["mineral_salts"] > 0
    assert body["mineral_salts"] < body["total"]

    capped = client.post(
        "/search/substructure", json={"smiles_pattern": "C", "top_n": 500}
    ).json()
    assert capped["count"] == 500, "substructure count is capped, as expected"
    assert body["total"] != capped["count"], "stats must not be the capped count"


def test_stats_route_does_not_shadow_molecule_by_id(client, _db):
    """/molecules/stats must not be parsed as /molecules/{id}."""
    assert client.get("/molecules/stats").status_code == 200
    assert client.get("/molecules/42").status_code == 200


# --- resolve: people type names, not SMILES ---------------------------------


def test_resolve_passes_a_smiles_through_without_a_network_call(client):
    """A structure must resolve locally - no PubChem round trip for SMILES."""
    body = client.post("/molecules/resolve", json={"query": ASPIRIN}).json()
    assert body["source"] == "smiles"
    assert body["canonical_smiles"] == ASPIRIN
    assert body["matched_name"] is None


def test_resolve_canonicalises_a_non_canonical_smiles(client):
    body = client.post(
        "/molecules/resolve", json={"query": "OC(=O)c1ccccc1OC(C)=O"}
    ).json()
    assert body["canonical_smiles"] == ASPIRIN
    assert body["source"] == "smiles"


@pytest.mark.parametrize("query", ["", "   "])
def test_resolve_rejects_empty_input(client, query):
    assert client.post("/molecules/resolve", json={"query": query}).status_code == 400


def test_resolve_looks_up_a_compound_name(client):
    """glucose -> a structure. This is the bug that made search look broken:
    a name-shaped query used to fail as an unparseable SMILES."""
    response = client.post("/molecules/resolve", json={"query": "glucose"})
    if response.status_code == 400 and "could not be reached" in response.json()["detail"]:
        pytest.skip("PubChem unreachable; name resolution needs network")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "pubchem"
    assert "glucose" in (body["matched_name"] or "").lower()
    # And the resolved structure must be usable by the rest of the API.
    assert client.post(
        "/search/similarity", json={"smiles": body["canonical_smiles"], "top_n": 3}
    ).status_code == 200


def test_resolve_reports_an_unknown_name_clearly(client):
    response = client.post(
        "/molecules/resolve", json={"query": "definitely_not_a_compound_zzq"}
    )
    if "could not be reached" in response.json().get("detail", ""):
        pytest.skip("PubChem unreachable; name resolution needs network")
    assert response.status_code == 400
    assert "not a valid SMILES" in response.json()["detail"]
