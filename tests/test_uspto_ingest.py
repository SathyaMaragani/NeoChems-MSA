"""The USPTO CML parser, against a real reaction from US 3,930,836 (1976)."""
import pathlib
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from ingest_uspto import extract, split_reaction_smiles  # noqa: E402

SAMPLE = """<reaction xmlns="http://www.xml-cml.org/schema" xmlns:dl="http://bitbucket.org/dan2097">
  <dl:source>
    <dl:documentId>US03930836</dl:documentId>
    <dl:headingText>2-Bromoethyl ethanesulfonate</dl:headingText>
    <dl:paragraphText>A mixture of 2-bromoethanol ... to yield 17.6 grams of a yellow oil.</dl:paragraphText>
  </dl:source>
  <dl:reactionSmiles>[Br:1][CH2:2][CH2:3][OH:4].[CH2:5]([S:7](Cl)(=[O:9])=[O:8])[CH3:6].CCOCC&gt;C(N(CC)CC)C&gt;[CH2:5]([S:7]([O:4][CH2:3][CH2:2][Br:1])(=[O:9])=[O:8])[CH3:6]</dl:reactionSmiles>
  <productList><product><molecule id="m0"><name>2-Bromoethyl ethanesulfonate</name></molecule>
    <amount dl:propertyType="CALCULATEDPERCENTYIELD" dl:normalizedValue="100.5">100.5</amount>
  </product></productList>
  <reactantList>
    <reactant role="reactant"><molecule id="m1"><name>2-bromoethanol</name></molecule>
      <identifier dictRef="cml:smiles" value="BrCCO"/></reactant>
    <reactant role="reactant"><molecule id="m3"><name>ether</name></molecule>
      <identifier dictRef="cml:smiles" value="CCOCC"/></reactant>
  </reactantList>
  <spectatorList>
    <spectator role="solvent"><molecule id="m4"><name>triethylamine</name></molecule>
      <identifier dictRef="cml:smiles" value="C(C)N(CC)CC"/></spectator>
  </spectatorList>
  <dl:reactionActionList>
    <dl:reactionAction action="Add">
      <dl:parameter propertyType="Time" normalizedValue="3300">55 minute</dl:parameter>
      <dl:parameter propertyType="Temperature" normalizedValue="5 to 10">5-10 C</dl:parameter>
    </dl:reactionAction>
    <dl:reactionAction action="Stir">
      <dl:parameter propertyType="Time" normalizedValue="3600">1 hour</dl:parameter>
    </dl:reactionAction>
    <dl:reactionAction action="Dry"/>
  </dl:reactionActionList>
</reaction>"""


def record():
    return extract(ET.fromstring(SAMPLE), 1976)


def test_unmapped_species_are_conditions_not_reactants():
    reactants, agents, products = split_reaction_smiles(
        "[Br:1][CH2:2][OH:4].CCOCC>C(N(CC)CC)C>[Br:1][CH2:2][O:4]C")
    assert reactants == ["[Br][CH2][OH]"]
    assert agents == ["CCOCC", "C(N(CC)CC)C"]
    assert products == ["[Br][CH2][O]C"]


def test_reaction_identity_uses_only_mapped_reactants():
    r = record()
    assert "CCOCC" not in r["reactants"]
    assert len(r["reactants"]) == 2
    assert r["reaction_id"].startswith("uspto-")


def test_provenance_carries_patent_number_heading_and_a_derived_link():
    p = record()["provenance"]
    assert p["patent_number"] == "US03930836"
    assert p["title"] == "2-Bromoethyl ethanesulfonate"
    assert p["url"] == "https://patents.google.com/patent/US3930836"
    assert p["url_origin"] == "derived_from_patent_number"
    assert p["license"] == "CC0-1.0"
    assert p["source_type"] == "patent"


def test_a_temperature_range_is_not_turned_into_a_number():
    assert record()["conditions"]["temperature"] is None


def test_calculated_yield_is_not_reported_as_the_patents_yield():
    assert record()["conditions"]["yield"] is None


def test_roles_come_from_the_source():
    c = record()["conditions"]
    assert [s["name"] for s in c["solvents"]] == ["triethylamine"]
    assert "ether" in [r["name"] for r in c["reagents"]]
    assert c["time"] == {"value": 1.0, "unit": "h"}
    assert c["workup"] == ["Dry"]


def test_the_same_reaction_in_a_different_patent_is_a_different_row():
    other = SAMPLE.replace("US03930836", "US04000001")
    assert extract(ET.fromstring(other), 1977)["reaction_id"] != record()["reaction_id"]
    assert extract(ET.fromstring(other), 1977)["reaction_key"] == record()["reaction_key"]
