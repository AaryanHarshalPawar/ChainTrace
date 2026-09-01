"""Application wiring: raw complaint string in, TraceResult out.

Holds the objects that are expensive to build (HTTP client with its cache,
price oracle, label corpus) for the process lifetime, and resolves the one
ambiguity the chain adapters cannot: an ``0x`` address is valid on every EVM
chain, so the service probes for on-chain activity to decide which one the
victim actually meant.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.attribution.engine import AttributionEngine
from app.attribution.labels import LabelStore, get_label_store
from app.chains.base import ChainAdapter
from app.chains.bitcoin import BitcoinAdapter
from app.chains.http import CachedHttpClient
from app.chains.tron import TronAdapter
from app.core.chains import Chain, DetectionResult, detect
from app.core.models import TraceResult
from app.core.pricing import PriceOracle
from app.risk.scoring import assess, recommend
from app.tracing.tracer import Tracer

log = logging.getLogger(__name__)


class UnsupportedAddress(ValueError):
    """The address is malformed, or on a chain with no adapter yet."""


@dataclass
class ResolvedAddress:
    address: str
    chain: Chain
    detection: DetectionResult
    probed: bool = False
    probe_note: str | None = None


class TraceService:
    def __init__(self) -> None:
        self.http = CachedHttpClient()
        self.oracle = PriceOracle(self.http)
        self.labels: LabelStore = get_label_store()
        self.engine = AttributionEngine(self.labels)
        self.adapters: dict[Chain, ChainAdapter] = {
            Chain.TRON: TronAdapter(self.http, self.oracle),
            Chain.BITCOIN: BitcoinAdapter(self.http, self.oracle),
        }

    async def startup(self) -> None:
        await self.oracle.warm()
        log.info(
            "service ready: %d labels, %d adapters, live_prices=%s",
            len(self.labels),
            len(self.adapters),
            self.oracle.has_live_quotes,
        )

    async def shutdown(self) -> None:
        await self.http.aclose()

    @property
    def supported_chains(self) -> list[Chain]:
        return list(self.adapters)

    async def resolve(self, raw_address: str) -> ResolvedAddress:
        """Decide which chain a victim-reported address belongs to."""
        detection = detect(raw_address)
        if not detection.is_valid:
            raise UnsupportedAddress(detection.reason)

        supported = [c for c in detection.candidates if c in self.adapters]
        if not supported:
            names = ", ".join(c.value for c in detection.candidates)
            raise UnsupportedAddress(
                f"address is on {names}, which has no adapter yet. "
                f"Supported: {', '.join(c.value for c in self.adapters)}"
            )

        if len(supported) == 1 and not detection.is_ambiguous:
            return ResolvedAddress(detection.normalized, supported[0], detection)

        # Ambiguous EVM address: ask each chain whether it has seen activity.
        for candidate in supported:
            adapter = self.adapters[candidate]
            if await adapter.has_activity(detection.normalized):
                return ResolvedAddress(
                    detection.normalized,
                    candidate,
                    detection,
                    probed=True,
                    probe_note=f"resolved to {candidate.value} by activity probe",
                )

        return ResolvedAddress(
            detection.normalized,
            supported[0],
            detection,
            probed=True,
            probe_note=(
                f"no activity found on any supported EVM chain; defaulted to "
                f"{supported[0].value}. The address may be unused, or on a "
                f"chain not yet indexed."
            ),
        )

    async def trace(
        self,
        raw_address: str,
        *,
        max_hops: int | None = None,
        max_nodes: int | None = None,
        on_progress: Callable[[dict], Awaitable[None]] | None = None,
    ) -> TraceResult:
        async def emit(event: dict) -> None:
            if on_progress is not None:
                await on_progress(event)

        await emit({"type": "stage", "stage": "resolve",
                    "message": "Checking the address and identifying its blockchain"})
        resolved = await self.resolve(raw_address)
        await emit({"type": "stage", "stage": "resolved",
                    "message": f"Recognised as {resolved.chain.value}",
                    "chain": resolved.chain.value})

        tracer = Tracer(
            self.adapters,
            self.engine,
            max_hops=max_hops,
            max_nodes=max_nodes,
            on_progress=on_progress,
        )
        result = await tracer.trace(resolved.address, resolved.chain)

        await emit({"type": "stage", "stage": "scoring",
                    "message": "Attributing wallets and scoring risk"})
        if resolved.probe_note:
            result.warnings.append(resolved.probe_note)

        result.risk = assess(result)
        result.recommended_actions = recommend(result, result.risk)
        # Propagate the case-level score onto the subject so a graph view can
        # colour it without re-deriving anything.
        for node in result.nodes:
            if node.depth == 0:
                node.risk_score = result.risk.score
        return result
