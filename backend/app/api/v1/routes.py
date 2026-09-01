"""HTTP API for investigators and for LEA system integration.

Endpoint shapes are chosen so that NCRP or SAHYOG could post a complaint's
wallet field directly. Neither exposes a public integration API, so those
adapters cannot be built for real yet; what exists here is the contract they
would target, kept deliberately plain (a wallet string plus optional case
metadata) so it can be adapted without reshaping the service underneath.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import cast

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.core.chains import CHAIN_SPECS, Chain, detect
from app.core.models import TraceResult
from app.service import TraceService, UnsupportedAddress

log = logging.getLogger(__name__)
router = APIRouter()


def _sse(payload: object) -> str:
    """One server-sent event frame. The blank line terminates the event."""
    return "data: " + json.dumps(payload, default=str) + "\n\n"


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


@router.post("/trace/stream", summary="Trace, streaming progress as it works")
async def trace_stream(request: Request, payload: TraceRequest) -> StreamingResponse:
    """Server-sent events: progress while the trace runs, then the result.

    A cold Bitcoin trace takes tens of seconds, and a spinner cannot tell an
    investigator whether the search is progressing or has hung. These events
    are emitted by the tracer itself as it works -- each ``hop`` marks a real
    BFS level and each ``node`` a real address examined -- so what is shown is
    the actual state of the search, not an animation timed to look busy.
    """
    service = _service(request)
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    async def on_progress(event: dict) -> None:
        await queue.put(("progress", event))

    async def run() -> None:
        try:
            result = await service.trace(
                payload.address,
                max_hops=payload.max_hops,
                max_nodes=payload.max_nodes,
                on_progress=on_progress,
            )
            await queue.put(("done", result))
        except UnsupportedAddress as exc:
            await queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001 - must reach the client as an event
            log.exception("streaming trace failed")
            await queue.put(("error", f"{type(exc).__name__}: {exc}"))

    async def events() -> AsyncIterator[str]:
        task = asyncio.create_task(run())
        try:
            while True:
                kind, payload_out = await queue.get()
                if kind == "progress":
                    yield _sse(payload_out)
                elif kind == "done":
                    result = cast(TraceResult, payload_out)
                    yield _sse(
                        {
                            "type": "done",
                            "complaint_id": payload.complaint_id,
                            "result": result.model_dump(mode="json"),
                        }
                    )
                    return
                else:
                    yield _sse({"type": "error", "message": payload_out})
                    return
        finally:
            # A client that navigates away mid-trace must not leave the search
            # running against a rate-limited upstream.
            if not task.done():
                task.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # stop proxies buffering the stream
        },
    )


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
