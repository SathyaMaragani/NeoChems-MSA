"""Route benchmark helpers: step counting, the confidence interval, and TED matching."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from benchmark_routes import _found_at, _longest_linear, _steps, wilson  # noqa: E402


def mol(smiles, *reactions, in_stock=False):
    #  Leaves carry no "children" key at all, as in AiZynthFinder's output;
    #  rxnutils rejects an empty list.
    node = {"type": "mol", "smiles": smiles, "in_stock": in_stock}
    if reactions:
        node["children"] = list(reactions)
    return node


def rxn(product, *reactants):
    smiles = ".".join(r["smiles"] for r in reactants) + ">>" + product
    return {"type": "reaction", "smiles": smiles, "metadata": {"smiles": smiles},
            "children": list(reactants)}


#  Aspirin from salicylic acid, which is made from phenol: two steps, and
#  also two steps on the longest linear path.
PHENOL = mol("Oc1ccccc1", in_stock=True)
CO2 = mol("O=C=O", in_stock=True)
SALICYLIC = mol("O=C(O)c1ccccc1O", rxn("O=C(O)c1ccccc1O", PHENOL, CO2))
AC2O = mol("CC(=O)OC(C)=O", in_stock=True)
ASPIRIN = mol("CC(=O)Oc1ccccc1C(=O)O", rxn("CC(=O)Oc1ccccc1C(=O)O", SALICYLIC, AC2O))

#  A one-step route to the same target from a different acetyl source.
ACCL = mol("CC(=O)Cl", in_stock=True)
SALICYLIC_BOUGHT = mol("O=C(O)c1ccccc1O", in_stock=True)
ASPIRIN_ALT = mol("CC(=O)Oc1ccccc1C(=O)O",
                  rxn("CC(=O)Oc1ccccc1C(=O)O", SALICYLIC_BOUGHT, ACCL))


def test_step_counts():
    assert _steps(ASPIRIN) == 2
    assert _longest_linear(ASPIRIN) == 2
    assert _steps(ASPIRIN_ALT) == 1
    assert _steps(PHENOL) == 0


def test_the_reference_is_found_only_where_it_is_ranked():
    routes = [ASPIRIN_ALT, ASPIRIN]
    assert _found_at(routes, [0, 1], [1, 2], ASPIRIN) == 2
    assert _found_at(routes, [1, 0], [1, 2], ASPIRIN) == 1
    assert _found_at([ASPIRIN_ALT], [0], [1], ASPIRIN) is None


def test_wilson_interval():
    low, high = wilson(24, 100)
    assert low < 0.24 < high
    assert wilson(0, 0) == [0.0, 0.0]
    assert wilson(10, 10)[1] == 1.0
