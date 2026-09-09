"""Pure representation functions - no DB, no API."""
import pytest

from backend.molrepr import service

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
IBUPROFEN = "CC(C)Cc1ccc(C(C)C(=O)O)cc1"
PARACETAMOL = "CC(=O)Nc1ccc(O)cc1"

# InChIKeys from PubChem CID 2244 / 3672 / 1983.
EXPECTED = {
    ASPIRIN: "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
    IBUPROFEN: "HEFNNWSXXWATRW-UHFFFAOYSA-N",
    PARACETAMOL: "RZVAJINKPMORJF-UHFFFAOYSA-N",
}


@pytest.mark.parametrize("smiles,inchikey", EXPECTED.items())
def test_canonicalize_is_idempotent(smiles, inchikey):
    canonical = service.canonicalize(smiles)
    assert service.canonicalize(canonical) == canonical
    assert service.to_inchikey(smiles) == inchikey


def test_non_canonical_input_canonicalizes_to_same_form():
    """Kekulized / differently-written aspirin must land on one canonical form."""
    assert service.canonicalize("OC(=O)c1ccccc1OC(C)=O") == service.canonicalize(ASPIRIN)


def test_salt_strips_to_parent():
    """Sodium acetylsalicylate and free-acid aspirin are the same parent."""
    salt = "CC(=O)Oc1ccccc1C(=O)[O-].[Na+]"
    assert service.canonicalize(salt) == service.canonicalize(ASPIRIN)
    assert service.to_inchikey(salt) == EXPECTED[ASPIRIN]
    assert service.tanimoto(
        service.morgan_fingerprint(salt), service.morgan_fingerprint(ASPIRIN)
    ) == 1.0


def test_permanently_charged_group_is_not_neutralised():
    """Quaternary ammonium has no proton to lose - neostigmine stays cationic."""
    parent = service.canonicalize("CN(C)C(=O)Oc1cccc([N+](C)(C)C)c1.[Br-]")
    assert "[N+]" in parent and "Br" not in parent


def test_fingerprint_shape_and_roundtrip():
    fp = service.morgan_fingerprint(ASPIRIN)
    assert fp.GetNumBits() == 2048
    assert 0 < fp.GetNumOnBits() < 2048
    restored = service.fingerprint_from_bytes(service.fingerprint_to_bytes(fp))
    assert service.tanimoto(restored, fp) == 1.0


def test_distinct_molecules_score_below_one():
    assert service.tanimoto(
        service.morgan_fingerprint(ASPIRIN), service.morgan_fingerprint(IBUPROFEN)
    ) < 1.0


def test_depict_returns_png():
    png = service.depict(ASPIRIN)
    assert png[:4] == b"\x89PNG" and len(png) > 1000


@pytest.mark.parametrize("bad", ["not_a_molecule", "C(C(C", "", "   "])
def test_invalid_smiles_raises(bad):
    with pytest.raises(service.InvalidSmilesError):
        service.canonicalize(bad)


# --- mineral salts: the counter-ion is the drug, so stripping is skipped ---

LITHIUM_CARBONATE = "O=C([O-])[O-].[Li+].[Li+]"
CALCIUM_CARBONATE = "O=C([O-])[O-].[Ca+2]"


def test_mineral_salts_are_detected():
    assert service.is_mineral_salt_smiles(LITHIUM_CARBONATE)
    assert service.is_mineral_salt_smiles(CALCIUM_CARBONATE)
    assert service.is_mineral_salt_smiles("O=S(=O)([O-])[O-].[Zn+2]")
    assert service.is_mineral_salt_smiles("[Na+].[Cl-]")  # no carbon at all


def test_lithium_and_calcium_carbonate_stay_distinct():
    """Two different drugs. Collapsing both to carbonic acid would make a
    similarity search for one silently return the other."""
    assert service.canonicalize(LITHIUM_CARBONATE) != service.canonicalize(
        CALCIUM_CARBONATE
    )
    assert "Li" in service.canonicalize(LITHIUM_CARBONATE)
    assert "Ca" in service.canonicalize(CALCIUM_CARBONATE)
    assert service.to_inchikey(LITHIUM_CARBONATE) != service.to_inchikey(
        CALCIUM_CARBONATE
    )


def test_no_carbon_in_parent_alone_would_not_catch_lithium_carbonate():
    """Guards the reason for the heuristic: carbonate contains a carbon, so a
    'parent has no carbon' rule returns False for the case it must catch."""
    from rdkit import Chem

    carbonate = Chem.MolFromSmiles("O=C(O)O")
    assert any(atom.GetSymbol() == "C" for atom in carbonate.GetAtoms())


def test_organic_salts_are_not_flagged_as_mineral():
    """Naproxen sodium's parent is 17 heavy atoms - an inactive salt former."""
    naproxen_sodium = "COc1ccc2cc([C@H](C)C(=O)[O-])ccc2c1.[Na+]"
    assert not service.is_mineral_salt_smiles(naproxen_sodium)
    assert "Na" not in service.canonicalize(naproxen_sodium)
    assert not service.is_mineral_salt_smiles(ASPIRIN)


# --- two fingerprint flavours: stereo-blind for search, chiral for QSAR ---

S_IBUPROFEN = "CC(C)Cc1ccc([C@H](C)C(=O)O)cc1"
R_IBUPROFEN = "CC(C)Cc1ccc([C@@H](C)C(=O)O)cc1"


def test_default_fingerprint_is_stereo_blind():
    """Correct for scaffold-hopping: enantiomers are the same shape."""
    assert service.tanimoto(
        service.morgan_fingerprint(S_IBUPROFEN),
        service.morgan_fingerprint(R_IBUPROFEN),
    ) == 1.0


def test_chiral_fingerprint_separates_enantiomers():
    """Required for QSAR: (S)- and (R)-ibuprofen differ in activity, so they
    must not share a feature vector."""
    score = service.tanimoto(
        service.morgan_fingerprint(S_IBUPROFEN, include_chirality=True),
        service.morgan_fingerprint(R_IBUPROFEN, include_chirality=True),
    )
    assert score < 1.0


def test_chiral_fingerprint_agrees_on_achiral_molecules():
    """Nothing to distinguish - aspirin has no stereocentre."""
    assert service.tanimoto(
        service.morgan_fingerprint(ASPIRIN),
        service.morgan_fingerprint(ASPIRIN, include_chirality=True),
    ) == 1.0


def test_both_fingerprint_flavours_roundtrip_through_bytes():
    for chiral in (False, True):
        fp = service.morgan_fingerprint(S_IBUPROFEN, include_chirality=chiral)
        restored = service.fingerprint_from_bytes(service.fingerprint_to_bytes(fp))
        assert service.tanimoto(restored, fp) == 1.0
