"""FastAPI application entrypoint.

Run::

    uvicorn app.main:app --reload --port 8000

Interactive API docs at http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routes import router as v1_router
from app.config import settings
from app.service import TraceService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # The label corpus and price quotes are loaded once and shared, so the
    # first investigator request is not slower than the rest.
    service = TraceService()
    await service.startup()
    app.state.service = service
    try:
        yield
    finally:
        await service.shutdown()


app = FastAPI(
    title="Chainalytics",
    version="0.1.0",
    description=(
        "Real-time attribution of fraud-linked cryptocurrency wallets to the "
        "exchanges and VASPs that receive them. Built for SIH problem "
        "statement 26183."
    ),
    lifespan=lifespan,
)

# The dashboard is served separately during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(v1_router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "api": "/api/v1",
    }
