"""Pure molecular-representation helpers. No database, no I/O.

Python RDKit is the single source of truth for canonicalization, InChIKey and
fingerprints; the Postgres cartridge is used only for substructure matching.
Both are pinned to RDKit 2023.09 so they cannot silently disagree - see the
comment on the image tag in docker-compose.yml.
"""
from __future__ import annotations

import io

from rdkit import Chem, DataStructs, RDLogger
from rdkit.Chem import Descriptors, Draw, rdFingerprintGenerator
from rdkit.Chem.Draw import rdMolDraw2D
from rdkit.Chem.MolStandardize import rdMolStandardize
from rdkit.DataStructs.cDataStructs import ExplicitBitVect

# RDKit writes parse failures to stderr; we surface them as exceptions instead.
RDLogger.DisableLog("rdApp.*")

DEFAULT_RADIUS = 2
DEFAULT_N_BITS = 2048

# Elements whose presence as a counter-ion means the counter-ion may BE the drug.
METALS = frozenset({
    "Li", "Na", "K", "Rb", "Cs", "Be", "Mg", "Ca", "Sr", "Ba",
    "Al", "Ga", "In", "Tl", "Sn", "Pb", "Bi", "Sb", "As", "Si", "B", "Se",
    "Fe", "Co", "Ni", "Cu", "Zn", "Mn", "Cr", "Mo", "W", "V", "Ti", "Zr",
    "Ag", "Au", "Pt", "Pd", "Hg", "Cd", "Sc", "Y", "La", "Gd", "Sm", "Tc",
    "Ru", "Rh", "Os", "Ir",
})

# A metal counter-ion sitting on a parent no bigger than this means the metal is
# the point (lithium carbonate), not an inactive salt former (naproxen sodium,
# parent 17 heavy atoms). Sensitivity is mild: 4 -> 92 records flagged, 6 -> 95,
# 8 -> 103, 10 -> 110, so this is not a knife-edge.
MINERAL_PARENT_MAX_HEAVY_ATOMS = 6

# Two fingerprint flavours, deliberately:
#   include_chirality=False -> similarity search (scaffold-hopping; stereo-blind)
#   include_chirality=True  -> QSAR features (enantiomers can differ in activity)
# See the note in README.md before changing which one a caller uses.
_generators: dict[tuple[int, int, bool], object] = {}


class InvalidSmilesError(ValueError):
    """SMILES RDKit cannot parse -> HTTP 400."""


def _generator(radius: int, n_bits: int, include_chirality: bool = False):
    """Morgan generators are reusable and not cheap to build; keep one per shape."""
    key = (radius, n_bits, include_chirality)
    if key not in _generators:
        _generators[key] = rdFingerprintGenerator.GetMorganGenerator(
            radius=radius, fpSize=n_bits, includeChirality=include_chirality
        )
    return _generators[key]


def parse(smiles: str) -> Chem.Mol:
    """Parse SMILES to an RDKit Mol, or raise InvalidSmilesError."""
    if not isinstance(smiles, str) or not smiles.strip():
        raise InvalidSmilesError("smiles must be a non-empty string")
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise InvalidSmilesError(f"could not parse SMILES: {smiles!r}")
    return mol


def is_mineral_salt(mol: Chem.Mol) -> bool:
    """True when stripping would discard the pharmacologically active species.

    Lithium carbonate (bipolar disorder) and calcium carbonate (antacid) both
    reduce to carbonic acid, which would make them indistinguishable in search -
    silently wrong results rather than an error. Two rules:

      1. No carbon at all -> inorganic (NaCl, ZnSO4).
      2. A metal counter-ion on a parent of <= MINERAL_PARENT_MAX_HEAVY_ATOMS
         heavy atoms -> the metal is the drug.

    Rule 2 is what catches lithium carbonate. "No carbon in the parent" alone does
    NOT: carbonate contains a carbon, so that test returns False for the very case
    it needs to catch. Verified against ChEMBL, not assumed.
    """
    if not any(atom.GetSymbol() == "C" for atom in mol.GetAtoms()):
        return True
    fragments = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=False)
    if len(fragments) < 2:
        return False
    largest = max(fragments, key=lambda frag: frag.GetNumHeavyAtoms())
    if largest.GetNumHeavyAtoms() > MINERAL_PARENT_MAX_HEAVY_ATOMS:
        return False
    return any(
        atom.GetSymbol() in METALS
        for frag in fragments
        if frag is not largest
        for atom in frag.GetAtoms()
    )


def parent(mol: Chem.Mol) -> Chem.Mol:
    """Reduce to the parent compound: largest fragment, charges neutralised.

    Plain largest-fragment stripping is not enough. Aspirin sodium salt reduced to
    its largest fragment is still the carboxylate, which scores Tanimoto 0.69
    against free-acid aspirin; neutralising as well scores 1.00. Groups that
    cannot lose a proton (quaternary ammonium, e.g. neostigmine) stay charged.

    Mineral salts are returned untouched - see is_mineral_salt. Every caller goes
    through here, so ingestion and search cannot disagree about what got stripped.

    Swap to `rdMolStandardize.FragmentParent` here for largest-fragment-only.
    """
    if is_mineral_salt(mol):
        return mol
    return rdMolStandardize.ChargeParent(mol)


def is_mineral_salt_smiles(smiles: str) -> bool:
    return is_mineral_salt(parse(smiles))


def canonicalize(smiles: str, strip_to_parent: bool = True) -> str:
    """Canonical SMILES, by default reduced to the parent compound."""
    mol = parse(smiles)
    return Chem.MolToSmiles(parent(mol) if strip_to_parent else mol)


def to_inchikey(smiles: str, strip_to_parent: bool = True) -> str:
    mol = parse(smiles)
    return Chem.MolToInchiKey(parent(mol) if strip_to_parent else mol)


def molecular_weight(smiles: str, strip_to_parent: bool = True) -> float:
    mol = parse(smiles)
    return Descriptors.MolWt(parent(mol) if strip_to_parent else mol)


def morgan_fingerprint(
    smiles: str,
    radius: int = DEFAULT_RADIUS,
    n_bits: int = DEFAULT_N_BITS,
    strip_to_parent: bool = True,
    include_chirality: bool = False,
) -> DataStructs.ExplicitBitVect:
    """Morgan fingerprint. Stereo-blind by default - that is what similarity
    search wants. Pass include_chirality=True for QSAR features, where
    (S)- and (R)-ibuprofen are different molecules with different activities.
    """
    mol = parse(smiles)
    return _generator(radius, n_bits, include_chirality).GetFingerprint(
        parent(mol) if strip_to_parent else mol
    )


def fingerprint_to_bytes(fp: DataStructs.ExplicitBitVect) -> bytes:
    """RDKit's own round-trip format, so no bit-order assumptions of ours leak in.
    Compresses well - a 2048-bit Morgan fp is ~41 bytes."""
    return fp.ToBinary()


def fingerprint_from_bytes(raw: bytes) -> DataStructs.ExplicitBitVect:
    # NB: the ExplicitBitVect constructor, not DataStructs.CreateFromBinaryText,
    # which does not read ToBinary output and yields a length-mismatched vector.
    return ExplicitBitVect(bytes(raw))


def tanimoto(a: DataStructs.ExplicitBitVect, b: DataStructs.ExplicitBitVect) -> float:
    return DataStructs.TanimotoSimilarity(a, b)


def depict(smiles: str, width: int = 400, height: int = 300, strip_to_parent: bool = False) -> bytes:
    """2D structure as PNG bytes. Draws the molecule as given by default, so a
    depiction shows what the caller actually passed in."""
    mol = parse(smiles)
    if strip_to_parent:
        mol = parent(mol)
    Chem.rdDepictor.Compute2DCoords(mol)
    drawer = rdMolDraw2D.MolDraw2DCairo(width, height)
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()
