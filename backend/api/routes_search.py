"""Molecule representation + search endpoints."""
from __future__ import annotations

import base64

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.molrepr import resolve as resolver
from backend.molrepr import search, service

router = APIRouter(tags=["molecules"])


def _b64_depiction(smiles: str) -> str:
    return base64.b64encode(service.depict(smiles)).decode("ascii")


class SmilesRequest(BaseModel):
    smiles: str = Field(..., description="Query molecule as SMILES")


class SimilarityRequest(SmilesRequest):
    top_n: int = Field(10, ge=1, le=200)
    min_similarity: float = Field(0.0, ge=0.0, le=1.0)


class SubstructureRequest(BaseModel):
    smiles_pattern: str = Field(..., description="Substructure query as SMILES")
    top_n: int = Field(50, ge=1, le=500)


class ResolveRequest(BaseModel):
    query: str = Field(..., description="A SMILES or a compound name")


@router.post("/molecules/resolve")
def resolve_query(request: ResolveRequest) -> dict:
    """Turn what the user typed into a structure.

    Without this, a name-shaped query fails as an unparseable SMILES, which reads
    as the search being broken rather than the wrong input format.
    """
    try:
        return resolver.resolve(request.query).to_dict()
    except resolver.ResolutionError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/molecules/stats")
def molecule_stats() -> dict:
    """Library size. Declared before /molecules/{id} so "stats" is not parsed
    as an id."""
    return search.stats()


@router.get("/molecules/{molecule_id}")
def get_molecule(molecule_id: int, include_image: bool = Query(True)) -> dict:
    molecule = search.get_molecule(molecule_id)
    if molecule is None:
        raise HTTPException(status_code=404, detail=f"no molecule with id {molecule_id}")
    molecule["image_png_base64"] = (
        _b64_depiction(molecule["canonical_smiles"]) if include_image else None
    )
    return molecule


@router.post("/molecules/represent")
def represent(request: SmilesRequest) -> dict:
    """Stateless: canonicalize / fingerprint / depict without touching the DB."""
    try:
        fingerprint = service.morgan_fingerprint(request.smiles)
        return {
            "input_smiles": request.smiles,
            "canonical_smiles": service.canonicalize(request.smiles),
            "smiles_as_given_canonical": service.canonicalize(
                request.smiles, strip_to_parent=False
            ),
            "inchikey": service.to_inchikey(request.smiles),
            "molecular_weight": round(service.molecular_weight(request.smiles), 4),
            "morgan_fingerprint_on_bits": sorted(fingerprint.GetOnBits()),
            "morgan_radius": service.DEFAULT_RADIUS,
            "morgan_n_bits": service.DEFAULT_N_BITS,
            "image_png_base64": _b64_depiction(request.smiles),
        }
    except service.InvalidSmilesError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.post("/search/exact")
def exact(request: SmilesRequest) -> dict:
    try:
        match = search.exact_search(request.smiles)
    except service.InvalidSmilesError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    if match is None:
        raise HTTPException(
            status_code=404,
            detail=f"no molecule matching {service.canonicalize(request.smiles)}",
        )
    return match


@router.post("/search/similarity")
def similarity(request: SimilarityRequest) -> dict:
    try:
        hits = search.similarity_search(
            request.smiles, top_n=request.top_n, min_similarity=request.min_similarity
        )
    except service.InvalidSmilesError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return {
        "query_smiles": request.smiles,
        "query_canonical_smiles": service.canonicalize(request.smiles),
        "count": len(hits),
        "results": [{**molecule, "tanimoto": round(score, 6)} for molecule, score in hits],
    }


@router.post("/search/substructure")
def substructure(request: SubstructureRequest) -> dict:
    try:
        hits = search.substructure_search(request.smiles_pattern, top_n=request.top_n)
    except service.InvalidSmilesError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return {
        "query_pattern": request.smiles_pattern,
        "count": len(hits),
        "results": hits,
    }
