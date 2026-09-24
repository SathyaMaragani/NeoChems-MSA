"""Benchmark grading rules, on real ORD records - including the two that misfired."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from reaction_rules import classify  # noqa: E402

CASES = [
    ("SUZUKI", "Brc1ccc2ncccc2c1.Cn1nc(N)cc1B1OC(C)(C)C(C)(C)O1",
     "Cn1nc(N)cc1-c1ccc2ncccc2c1"),
    ("ARYL C-N/C-O", "CC(C)(C)OC(=O)N1CCCNCC1.CCc1ccn2ccnc2c1Br",
     "CCc1ccn2ccnc2c1N1CCCN(C(=O)OC(C)(C)C)CC1"),
    ("ARYL C-N/C-O", "C=CCO.Cc1cc(Br)nn1C", "C=CCOc1cc(C)n(C)n1"),
    #  An aryl triflate's own C-O must not hide the new C-N bond.
    ("ARYL C-N/C-O",
     "CCNC1CCOCC1.Cc1cc(C)c(CN2CCc3ccc(OS(=O)(=O)C(F)(F)F)c(Cl)c3C2=O)c(OCc2ccccc2)n1",
     "CCN(c1ccc2c(c1Cl)C(=O)N(Cc1c(C)cc(C)nc1OCc1ccccc1)CC2)C1CCOCC1"),
    ("BORYLATION", "CC1(C)OB(B2OC(C)(C)C(C)(C)O2)OC1(C)C.COC(=O)c1cc(Cl)ccc1Cl",
     "COC(=O)c1cc(Cl)cc(B2OC(C)(C)C(C)(C)O2)c1Cl"),
    ("STILLE", "Cc1cc(C)c(CN2CCc3cc[c]([Sn]([CH3])([CH3])[CH3])c(Cl)c3C2=O)c(OCc2ccccc2)n1."
     "Cc1ncn(C)c1Br", "Cc1cc(C)c(CN2CCc3ccc(-c4c(C)ncn4C)c(Cl)c3C2=O)c(OCc2ccccc2)n1"),
    ("HECK", "CC(C)(C)OC(=O)N1CC=CC1=O.COc1ncccc1Br",
     "COc1ncccc1C1=CC(=O)N(C(=O)OC(C)(C)C)C1"),
    #  Organozinc partner present: still a carbonylation, not a Negishi.
    ("CARBONYLATION", "CC(C)(C)c1ccc(Br)cn1.[Br][Zn][CH2]Cc1ccccc1.[C-]#[N+]C(C)(C)C",
     "CC(C)(C)c1ccc(C(=O)CCc2ccccc2)cn1"),
    ("CYANATION", "CS(=O)(=O)O[C@@H]1CN(C(=O)OCc2ccccc2)CC[C@@H]1F.N#[C][Zn][C]#N",
     "N#C[C@H]1CN(C(=O)OCc2ccccc2)CC[C@@H]1F"),
    ("FLUORINATION", "Cc1ccc(-c2cnccn2)cc1.F[N+]12CC[N+](CCl)(CC1)CC2",
     "FCc1ccc(-c2cnccn2)cc1"),
    ("HALOGENATION", "Cc1nc(Cl)ccc1Br.O=C1CCC(=O)N1Br", "Clc1ccc(Br)c(CBr)n1"),
    ("HYDROGENATION", "Cc1nc2c(C(=O)O)cccc2[nH]1", "Cc1nc2c([nH]1)CCCC2C(=O)O"),
    ("OXIDATION", "COc1ccc(CN[C@@H]2CCO[C@H](C)C2)c(OC)c1",
     "COc1ccc(CN[C@@H]2CCO[C@H](CO)C2)c(OC)c1"),
    #  The chloroaryl partner is missing from the record: not a chlorination.
    (None, "CCOC(=O)c1c[nH]nc1O", "CCOC(=O)c1cn(-c2ccc(Cl)cc2)nc1O"),
]


@pytest.mark.parametrize("expected, reactants, product", CASES)
def test_rule_labels(expected, reactants, product):
    assert classify(reactants.split("."), [product]) == expected


def test_an_internal_standard_in_the_products_cannot_fire_a_rule():
    #  C-N COUPLING records list caffeine among the products.
    caffeine = "Cn1c(=O)c2c(ncn2C)n(C)c1=O"
    assert classify(["C1COCCN1", "FC(F)(F)c1ccc(Br)cc1"],
                    [caffeine, "FC(F)(F)c1ccc(N2CCOCC2)cc1"]) == "ARYL C-N/C-O"
    assert classify(["CCO"], [caffeine]) is None
