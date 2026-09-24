"""Top-level FastAPI app.

Run:  uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.api import (
    routes_conditions,
    routes_qsar,
    routes_retrosynthesis,
    routes_search,
)
from backend.molrepr import search as molsearch
from backend.qsar.service import QsarService
from backend.retrosynthesis.service import RetrosynthesisService

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ~8s: the ONNX expansion model plus the ZINC stock. Done once, at startup.
    logger.info("loading AiZynthFinder...")
    try:
        service = RetrosynthesisService()
        routes_retrosynthesis.set_service(service)
        logger.info("AiZynthFinder ready in %ss", service.load_time_seconds)
    except Exception as err:  # startup must not die silently; /health reports it
        routes_retrosynthesis.set_service(None, f"model failed to load: {err}")
        logger.exception("AiZynthFinder failed to load")

    # Small and fast (~1s) next to AiZynthFinder, but still loaded once, not per
    # request: the applicability index fingerprints the whole training set.
    try:
        qsar = QsarService()
        routes_qsar.set_service(qsar)
        logger.info("QSAR models ready in %ss", qsar.load_time_seconds)
    except Exception as err:
        routes_qsar.set_service(None, f"QSAR models failed to load: {err}")
        logger.exception("QSAR models failed to load")

    yield
    molsearch.close_pool()


app = FastAPI(title="RamChems", version="0.1.0", lifespan=lifespan)
# LOCAL DEV ONLY. This allows the Vite dev server to call the API from a
# different origin. Before ANY non-local deployment: replace the wildcard method
# and header lists with the specific ones used, drop any origin that is not the
# real frontend, and put the whole thing behind an env var so production never
# gets a localhost origin allow-listed. Credentials are off, so a stolen origin
# cannot ride the user's cookies - keep it that way unless auth is added.
app.add_middleware(
    CORSMiddleware,
    # Any private-LAN host on the Vite port, so a second laptop on the same
    # Wi-Fi works without re-listing a DHCP address every time it changes.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+):5173",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_retrosynthesis.router)
app.include_router(routes_search.router)
app.include_router(routes_qsar.router)
app.include_router(routes_conditions.router)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc: RequestValidationError):
    """Malformed body / out-of-range iteration_limit -> 400, not 422 + stack trace."""
    return JSONResponse(status_code=400, content={"detail": jsonable_encoder(exc.errors())})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
