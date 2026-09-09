"""QSAR property-prediction endpoints.

Routing is registry-driven: adding logP or a toxicity endpoint means adding a
PROPERTY_REGISTRY entry and a trained artifact in backend/qsar/service.py, not a
new route here.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.molrepr.service import InvalidSmilesError
from backend.qsar.service import (
    QsarService,
    UncalibratedAlphaError,
    UnknownPropertyError,
)

router = APIRouter(prefix="/predict", tags=["qsar"])

_service: Optional[QsarService] = None
_load_error: Optional[str] = None


def set_service(service: Optional[QsarService], error: Optional[str] = None) -> None:
    global _service, _load_error
    _service, _load_error = service, error


class PredictRequest(BaseModel):
    smiles: str = Field(..., description="Molecule as SMILES")
    property: str = Field("solubility", description="Property to predict")
    model: Optional[str] = Field(
        None, description="Model name; omit to use the property's default"
    )
    alpha: float = Field(
        0.1,
        gt=0.0,
        lt=1.0,
        description=(
            "Conformal miscoverage level. Only calibrated values are accepted - "
            "see calibrated_alphas from GET /predict/properties. Uncalibrated "
            "values are rejected rather than interpolated."
        ),
    )


def _require_service() -> QsarService:
    if _service is None:
        raise HTTPException(
            status_code=503, detail=_load_error or "QSAR models still loading"
        )
    return _service


@router.get("/properties")
def properties() -> dict:
    """What can be predicted, and how well. Lets the frontend build a dropdown
    without hardcoding property or model names."""
    service = _require_service()
    return {"properties": service.properties()}


@router.post("/property")
def predict(request: PredictRequest) -> dict:
    service = _require_service()
    try:
        return service.predict(
            smiles=request.smiles,
            property_name=request.property,
            model=request.model,
            alpha=request.alpha,
        )
    except InvalidSmilesError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except (UnknownPropertyError, UncalibratedAlphaError) as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
