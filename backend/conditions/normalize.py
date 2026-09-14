"""Turn a route step into a stable, provider-independent reaction identity.

The identity strategy deliberately does NOT rest on the template hash alone.
One template applies to many substrates - template 40152 covers every aromatic
ester formation from an anhydride - so template identity alone would match
precedents for entirely different molecules.

Identity therefore combines the transformation with the actual substrates:

    reaction_key = sha256(canonical reactants >> canonical products)

Reactants are sorted after canonicalisation so that A+B and B+A collapse to one
key. Agents are excluded from the key: the same transformation run in THF or DMF
is the same reaction for matching purposes, and the conditions are what we are
looking up.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from rdkit import Chem, DataStructs, RDLogger
from rdkit.Chem import rdChemReactions

RDLogger.DisableLog("rdApp.*")


def canonical(smiles: str) -> Optional[str]:
    """Canonical SMILES, or None when RDKit cannot parse it.

    Not routed through backend.molrepr.service.canonicalize on purpose: that
    applies parent-compound stripping for the search library, which would
    discard salts and counter-ions that genuinely participate in a reaction.
    """
    if not smiles:
        return None
    mol = Chem.MolFromSmiles(smiles)
    return Chem.MolToSmiles(mol) if mol is not None else None


def split_components(smiles: str) -> list[str]:
    """Split a dot-separated SMILES into canonical components, sorted."""
    out: list[str] = []
    for part in (smiles or "").split("."):
        part = part.strip()
        if not part:
            continue
        canon = canonical(part)
        if canon:
            out.append(canon)
    return sorted(out)


@dataclass
class NormalizedReaction:
    """Provider-independent identity for one reaction step."""

    reactants: list[str]
    products: list[str]
    reaction_smiles: str
    reaction_key: str
    template_hash: Optional[str] = None
    template_code: Optional[int] = None
    reaction_class: Optional[str] = None
    mapped_reaction_smiles: Optional[str] = None
    #  Set lazily; not part of identity.
    _fingerprint: object = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "reactants": self.reactants,
            "products": self.products,
            "reaction_smiles": self.reaction_smiles,
            "reaction_key": self.reaction_key,
            "template_hash": self.template_hash,
            "template_code": self.template_code,
            "reaction_class": self.reaction_class,
        }


class NormalizationError(ValueError):
    """Reaction could not be normalised - unparseable or empty."""


def normalize(
    reactants: list[str],
    products: list[str],
    template_hash: str | None = None,
    template_code: int | None = None,
    reaction_class: str | None = None,
    mapped_reaction_smiles: str | None = None,
) -> NormalizedReaction:
    """Build a NormalizedReaction, raising if the input is not real chemistry."""
    reactant_parts: list[str] = []
    for smiles in reactants:
        reactant_parts.extend(split_components(smiles))
    product_parts: list[str] = []
    for smiles in products:
        product_parts.extend(split_components(smiles))

    if not reactant_parts:
        raise NormalizationError("no parseable reactants")
    if not product_parts:
        raise NormalizationError("no parseable products")

    reactant_parts.sort()
    product_parts.sort()
    reaction_smiles = f"{'.'.join(reactant_parts)}>>{'.'.join(product_parts)}"
    key = hashlib.sha256(reaction_smiles.encode("utf-8")).hexdigest()

    return NormalizedReaction(
        reactants=reactant_parts,
        products=product_parts,
        reaction_smiles=reaction_smiles,
        reaction_key=key,
        template_hash=template_hash,
        template_code=template_code,
        reaction_class=reaction_class,
        mapped_reaction_smiles=mapped_reaction_smiles,
    )


def from_route_step(reaction_node: dict) -> NormalizedReaction:
    """Normalise one reaction node from a /retrosynthesis/plan route tree.

    Takes the API's own dict shape rather than an AiZynthFinder object, so the
    evidence layer never reaches into the engine's internals.
    """
    reactants = [r.get("molecule_smiles", "") for r in reaction_node.get("reactants", [])]
    product = reaction_node.get("_product_smiles") or ""
    classification = reaction_node.get("classification")
    #  AiZynthFinder emits "0.0 Unrecognized" when no classifier is loaded; that
    #  is an absence, not a class.
    if classification and classification.strip().lower().endswith("unrecognized"):
        classification = None
    return normalize(
        reactants=reactants,
        products=[product],
        template_hash=reaction_node.get("template_hash"),
        template_code=reaction_node.get("template_used"),
        reaction_class=classification,
    )


#  --- similarity -----------------------------------------------------------

_RXNFP_PARAMS = rdChemReactions.ReactionFingerprintParams()


def reaction_fingerprint(reaction: NormalizedReaction):
    """Difference fingerprint over the reaction.

    RDKit's CreateDifferenceFingerprintForReaction encodes what CHANGES between
    reactants and products, so it keys on the transformation rather than on
    overall molecular resemblance. Two esterifications of different acids score
    as related; two unrelated reactions of similar-looking molecules do not.
    Available in the installed RDKit - no new dependency.
    """
    if reaction._fingerprint is None:
        rxn = rdChemReactions.ReactionFromSmarts(
            reaction.reaction_smiles, useSmiles=True
        )
        reaction._fingerprint = rdChemReactions.CreateDifferenceFingerprintForReaction(
            rxn, _RXNFP_PARAMS
        )
    return reaction._fingerprint


def reaction_centre(reaction: NormalizedReaction, radius: int = 1) -> tuple[str, int]:
    """A hash of the atom environments that CHANGE across the reaction.

    Returns (hash, size). Two reactions with the same hash make the same bond
    changes, whatever their substrates - which makes it a high-precision
    transformation key, and the complement of substrate similarity.

    ORD reaction SMILES reach us unmapped (ingestion stores sorted canonical
    reactants >> products), so a mapping-based reaction centre is not available.
    Instead the centre is the symmetric difference of the multisets of Morgan
    atom-environment invariants on each side: an environment present among the
    products but not the reactants was created, and vice versa. That is the same
    principle the difference fingerprint uses, reduced to an exact-match key.

    Approximate by construction - it cannot distinguish two different changes
    that happen to produce identical environments - so it is used to FIND
    candidates, never to assert that two reactions are the same reaction.
    """
    from collections import Counter

    from rdkit.Chem import rdFingerprintGenerator

    generator = rdFingerprintGenerator.GetMorganGenerator(radius=radius, fpSize=4096)

    def environments(smiles_list: list[str]) -> Counter:
        counts: Counter = Counter()
        for smiles in smiles_list:
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                continue
            counts.update(generator.GetSparseCountFingerprint(mol).GetNonzeroElements())
        return counts

    left = environments(reaction.reactants)
    right = environments(reaction.products)
    #  Symmetric difference of the two multisets: what appeared, and what left.
    changed = sorted(
        (bit, right.get(bit, 0) - left.get(bit, 0))
        for bit in set(left) | set(right)
        if right.get(bit, 0) != left.get(bit, 0)
    )
    if not changed:
        return "", 0
    digest = hashlib.sha256(
        ";".join(f"{bit}:{delta}" for bit, delta in changed).encode("utf-8")
    ).hexdigest()[:32]
    return digest, len(changed)


def substrate_fingerprint(reaction: NormalizedReaction):
    """One Morgan fingerprint over all reactants combined.

    The per-reactant form below is better for scoring (it matches each reactant
    to its best counterpart); this single combined vector is what goes into the
    database, because a GiST index needs one value per row.
    """
    from rdkit.Chem import rdFingerprintGenerator

    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    mol = Chem.MolFromSmiles(".".join(reaction.reactants))
    return generator.GetFingerprint(mol) if mol is not None else None


def substrate_fingerprints(reaction: NormalizedReaction):
    """Morgan fingerprints of the reactants, for substrate-level similarity."""
    from rdkit.Chem import rdFingerprintGenerator

    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    out = []
    for smiles in reaction.reactants:
        mol = Chem.MolFromSmiles(smiles)
        if mol is not None:
            out.append(generator.GetFingerprint(mol))
    return out


def transformation_similarity(a: NormalizedReaction, b: NormalizedReaction) -> float:
    """Tanimoto over reaction difference fingerprints (integer-count vectors,
    so Tanimoto is computed on the sparse int representation)."""
    try:
        fa, fb = reaction_fingerprint(a), reaction_fingerprint(b)
        return float(DataStructs.TanimotoSimilarity(fa, fb))
    except Exception:
        return 0.0


def substrate_similarity(a: NormalizedReaction, b: NormalizedReaction) -> float:
    """Best-match mean Tanimoto between the two reactant sets."""
    fa, fb = substrate_fingerprints(a), substrate_fingerprints(b)
    if not fa or not fb:
        return 0.0
    best = []
    for x in fa:
        best.append(max(DataStructs.TanimotoSimilarity(x, y) for y in fb))
    return float(sum(best) / len(best))
