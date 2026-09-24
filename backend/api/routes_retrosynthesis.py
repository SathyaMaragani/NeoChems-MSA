"""Retrosynthesis HTTP endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.retrosynthesis.service import (
    DEFAULT_ITERATION_LIMIT,
    MAX_ITERATION_LIMIT,
    InvalidRequestError,
    RetrosynthesisService,
)

router = APIRouter(prefix="/retrosynthesis", tags=["retrosynthesis"])

# Set by main.py's lifespan once the model has finished loading.
_service: Optional[RetrosynthesisService] = None
_load_error: Optional[str] = None


def set_service(service: Optional[RetrosynthesisService], error: Optional[str] = None) -> None:
    global _service, _load_error
    _service, _load_error = service, error


class PlanRequest(BaseModel):
    smiles: str = Field(..., description="Target molecule as SMILES")
    top_n: int = Field(10, ge=1, le=25, description="Max solved routes to return")
    iteration_limit: int = Field(
        DEFAULT_ITERATION_LIMIT,
        ge=1,
        le=MAX_ITERATION_LIMIT,
        description=(
            f"MCTS iterations. Default {DEFAULT_ITERATION_LIMIT} keeps searches to "
            f"seconds; raise towards {MAX_ITERATION_LIMIT} for molecules that do not "
            "solve at the default (costs ~80s at 500)."
        ),
    )
    include_images: bool = Field(False, description="Embed a base64 PNG per route")
    include_conditions: bool = Field(
        False,
        description=(
            "Attach experimental condition evidence to every reaction step. "
            "Off by default so existing calls keep their current speed; "
            "enrichment failure never fails the plan."
        ),
    )
    include_validation: bool = Field(
        False,
        description=(
            "Attach forward-model validation to every reaction step to confirm "
            "the precursors actually produce the target product. Opt-in."
        ),
    )


@router.get("/health")
def health() -> dict:
    if _service is None:
        raise HTTPException(status_code=503, detail=_load_error or "model still loading")
    return {
        "status": "ready",
        "model_loaded": _service.ready,
        "load_time_seconds": _service.load_time_seconds,
        "config": str(_service.config_path),
        "default_iteration_limit": DEFAULT_ITERATION_LIMIT,
        "max_iteration_limit": MAX_ITERATION_LIMIT,
    }


@router.post("/plan")
def plan(request: PlanRequest) -> dict:
    if _service is None:
        raise HTTPException(status_code=503, detail=_load_error or "model still loading")
    try:
        return _service.plan_routes(
            smiles=request.smiles,
            top_n=request.top_n,
            iteration_limit=request.iteration_limit,
            include_images=request.include_images,
            include_conditions=request.include_conditions,
            include_validation=request.include_validation,
        )
    except InvalidRequestError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
