"""HTTP API for investigators and for LEA system integration.

Endpoint shapes are chosen so that NCRP or SAHYOG could post a complaint's
wallet field directly. Neither exposes a public integration API, so those
adapters cannot be built for real yet; what exists here is the contract they
would target, kept deliberately plain (a wallet string plus optional case
metadata) so it can be adapted without reshaping the service underneath.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.core.chains import CHAIN_SPECS, Chain, detect
from app.core.models import TraceResult
from app.service import TraceService, UnsupportedAddress

log = logging.getLogger(__name__)
router = APIRouter()


def _service(request: Request) -> TraceService:
    service: TraceService | None = getattr(request.app.state, "service", None)
    if service is None:  # pragma: no cover - startup guarantees this
        raise HTTPException(503, "service not initialised")
    return service


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TraceRequest(BaseModel):
    address: str = Field(
        ...,
        description="Victim-reported wallet address. Wrappers and stray "
        "punctuation from complaint free-text are tolerated.",
        examples=["TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"],
    )
    # Optional case metadata, mirroring what an NCRP complaint would carry.
    complaint_id: str | None = Field(None, description="NCRP acknowledgement number")
    reported_amount_inr: float | None = None
    incident_date: str | None = None
    max_hops: int | None = Field(None, ge=1, le=8)
    max_nodes: int | None = Field(None, ge=1, le=1000)


class ValidationResponse(BaseModel):
    input: str
    normalized: str
    is_valid: bool
    candidate_chains: list[str]
    is_ambiguous: bool
    supported: bool
    reason: str = ""


class TraceResponse(BaseModel):
    complaint_id: str | None = None
    result: TraceResult


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/health", summary="Liveness and dependency status")
async def health(request: Request) -> dict:
    service = _service(request)
    return {
        "status": "ok",
        "environment": settings.environment,
        "offline_mode": settings.offline_mode,
        "live_prices": service.oracle.has_live_quotes,
        "supported_chains": [c.value for c in service.supported_chains],
        "labels": service.labels.summary(),
        "cache": service.http.cache.summary(),
    }


@router.get("/chains", summary="Chains this deployment can trace")
async def chains(request: Request) -> dict:
    service = _service(request)
    supported = set(service.supported_chains)
    return {
        "chains": [
            {
                "id": spec.chain.value,
                "name": spec.display_name,
                "native_symbol": spec.native_symbol,
                "address_family": spec.family.value,
                "adapter_available": spec.chain in supported,
                "explorer": spec.explorer_address_url,
            }
            for spec in CHAIN_SPECS.values()
        ]
    }


@router.get("/validate", summary="Check an address without tracing it")
async def validate(
    request: Request,
    address: str = Query(..., description="Address to check"),
) -> ValidationResponse:
    """Cheap, offline pre-check for a complaint-intake form.

    Catches transcription errors at the point of entry, where the victim can
    still be asked to re-read the address, rather than after a trace returns
    nothing.
    """
    service = _service(request)
    detection = detect(address)
    supported = any(c in service.adapters for c in detection.candidates)
    return ValidationResponse(
        input=address,
        normalized=detection.normalized,
        is_valid=detection.is_valid,
        candidate_chains=[c.value for c in detection.candidates],
        is_ambiguous=detection.is_ambiguous,
        supported=supported,
        reason=detection.reason,
    )


@router.post("/trace", summary="Trace a wallet and attribute it to a VASP")
async def trace(request: Request, payload: TraceRequest) -> TraceResponse:
    service = _service(request)
    try:
        result = await service.trace(
            payload.address,
            max_hops=payload.max_hops,
            max_nodes=payload.max_nodes,
        )
    except UnsupportedAddress as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    return TraceResponse(complaint_id=payload.complaint_id, result=result)


@router.get("/screen", summary="Sanctions and label screening, no tracing")
async def screen(
    request: Request,
    address: str = Query(...),
    chain: Chain | None = Query(None),
) -> dict:
    """Instant corpus lookup.

    Separate from ``/trace`` because screening must stay fast enough to run
    inline on every complaint at intake, and it needs no network at all.
    """
    service = _service(request)
    detection = detect(address)
    if not detection.is_valid:
        raise HTTPException(422, detection.reason)

    candidates = [chain] if chain else list(detection.candidates)
    hits = []
    for candidate in candidates:
        record = service.labels.lookup(candidate, detection.normalized)
        if record is not None:
            hits.append(record.model_dump(mode="json"))

    return {
        "address": detection.normalized,
        "checked_chains": [c.value for c in candidates],
        "is_sanctioned": any(h["category"] == "sanctioned" for h in hits),
        "hits": hits,
    }
