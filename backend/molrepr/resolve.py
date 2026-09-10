"""Resolve a user's text into a structure.

People type "glucose", not "OC[C@H]1OC(O)[C@H](O)[C@@H](O)[C@@H]1O". Without this
every name-shaped query fails as an unparseable SMILES, which reads as the search
being broken rather than as the wrong input format.

SMILES is tried first, locally. Only genuine names hit the network.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

from backend.molrepr import service

PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{}/property/SMILES,Title/JSON"
TIMEOUT_SECONDS = 12

# Names are stable, so one lookup per name per process is plenty.
_cache: dict[str, "Resolution"] = {}


class ResolutionError(ValueError):
    """Neither a parseable structure nor a resolvable name -> HTTP 400/404."""


@dataclass
class Resolution:
    query: str
    canonical_smiles: str
    #  "smiles"  - the input already was a structure
    #  "pubchem" - looked up by name
    source: str
    matched_name: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _lookup_pubchem(name: str) -> Resolution:
    url = PUBCHEM.format(urllib.parse.quote(name, safe=""))
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            raise ResolutionError(
                f"{name!r} is not a valid SMILES and PubChem has no compound by that name"
            ) from err
        raise ResolutionError(f"PubChem lookup failed for {name!r}: HTTP {err.code}") from err
    except Exception as err:  # DNS, TLS, timeout - offline is a normal local state
        raise ResolutionError(
            f"{name!r} is not a valid SMILES, and the PubChem name lookup could not "
            f"be reached ({type(err).__name__}). Paste a SMILES instead."
        ) from err

    properties = payload.get("PropertyTable", {}).get("Properties", [])
    if not properties or not properties[0].get("SMILES"):
        raise ResolutionError(f"PubChem returned no structure for {name!r}")

    entry = properties[0]
    # Canonicalize through our own service so a resolved name and a pasted SMILES
    # land on exactly the same string.
    return Resolution(
        query=name,
        canonical_smiles=service.canonicalize(entry["SMILES"]),
        source="pubchem",
        matched_name=entry.get("Title") or name,
    )


def resolve(query: str) -> Resolution:
    """Text -> canonical SMILES. Tries to parse as a structure before any lookup."""
    text = (query or "").strip()
    if not text:
        raise ResolutionError("query must be a non-empty string")

    try:
        return Resolution(
            query=text, canonical_smiles=service.canonicalize(text), source="smiles"
        )
    except service.InvalidSmilesError:
        pass

    cached = _cache.get(text.lower())
    if cached is not None:
        return cached

    resolution = _lookup_pubchem(text)
    _cache[text.lower()] = resolution
    return resolution
