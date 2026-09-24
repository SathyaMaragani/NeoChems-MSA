"""Reaction-condition and experimental-evidence endpoints.

Separate router from retrosynthesis on purpose: evidence is its own subsystem
with its own providers, and a caller can ask about a reaction that never came
from a route search.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.conditions.normalize import NormalizationError, normalize
from backend.conditions.service import ConditionsService

router = APIRouter(prefix="/retrosynthesis", tags=["conditions"])

_service: Optional[ConditionsService] = None


def set_service(service: Optional[ConditionsService]) -> None:
    global _service
    _service = service


def get_service() -> ConditionsService:
    """Lazily construct so the router works even if startup wiring is skipped."""
    global _service
    if _service is None:
        _service = ConditionsService()
    return _service


class ConditionsRequest(BaseModel):
    reactants: list[str] = Field(..., min_length=1, description="Reactant SMILES")
    products: list[str] = Field(..., min_length=1, description="Product SMILES")
    template_hash: Optional[str] = Field(None, description="AiZynthFinder template hash")
    template_code: Optional[int] = Field(None, description="AiZynthFinder template code")
    reaction_class: Optional[str] = Field(None, description="Reaction class, if known")


@router.post("/conditions")
def conditions(request: ConditionsRequest) -> dict:
    """Experimental evidence and conditions for one reaction step.

    Always returns a body. When nothing is found the evidence level is
    "unavailable" with a reason - never invented conditions, and never a 500
    just because no precedent exists.
    """
    try:
        reaction = normalize(
            reactants=request.reactants,
            products=request.products,
            template_hash=request.template_hash,
            template_code=request.template_code,
            reaction_class=request.reaction_class,
        )
    except NormalizationError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    service = get_service()
    evidence = service.for_reaction(reaction)
    return {
        "status": "available"
        if evidence.get("evidence_level") != "unavailable"
        else "unavailable",
        "reaction": reaction.to_dict(),
        **evidence,
    }


@router.get("/evidence/status")
def evidence_status() -> dict:
    """What the evidence layer is currently backed by.

    Surfaces the licence with the data so a consumer of this API cannot use the
    evidence without seeing the terms attached to it.
    """
    service = get_service()
    provider = service.provider
    return {
        "provider": provider.name,
        "provider_display_name": provider.display_name,
        "provider_version": provider.version,
        "available": provider.available,
        #  The UI quotes this so a user reads "216,681 indexed reactions"
        #  rather than inferring that the whole literature was searched.
        "indexed_reactions": provider.record_count,
        "dataset_version": provider.dataset_version,
        "coverage_note": (
            "Evidence comes from a finite indexed corpus, not from a search of "
            "the scientific literature. A reaction absent from the index may "
            "still be well precedented elsewhere."
        ),
        "prediction_model": {
            "provider": service.predictor.name,
            "available": service.predictor.available,
            "note": (
                "No condition-prediction model is configured. Published models "
                "are trained on licensed reaction data under non-commercial "
                "terms; see docs/reaction-condition-intelligence.md."
            )
            if not service.predictor.available
            else None,
        },
        "data_license": "; ".join(f"{lic} ({label})" for lic, label in provider.licenses.items())
        if provider.name == "ord"
        else None,
        "evidence_levels": [
            "experimental",
            "similar_experimental",
            "predicted",
            "unavailable",
        ],
    }
