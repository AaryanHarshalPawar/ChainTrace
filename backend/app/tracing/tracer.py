"""Forward fund-flow tracing.

The victim *sent* money to the reported address, so the money is downstream:
tracing runs forward, following outbound value until it reaches something that
can be asked a question -- an exchange, a deposit address, a sanctioned entity
-- or until the search budget runs out.

**Value attribution uses the haircut method.** When an address receives
$10,000 and sends $6,000 to B and $4,000 to C, B is credited with 60% of the
traced value and C with 40%. The alternatives are worse: crediting each
downstream address with the full amount ("poison") multiplies one theft into
several and inflates the reported loss, while first-in-first-out demands
transaction ordering that is not reliably recoverable across chains. Haircut
is proportional, conservative, and additive -- traced value can never exceed
what actually entered the graph, which is the property that makes the number
safe to put in a report.

**The search is budgeted, and says so.** Fan-out grows fast, and free explorer
tiers rate-limit hard, so the tracer stops at a hop limit, a node limit or a
value floor. Whenever it stops early it records *why* on the node and in the
stats, because an investigator must never mistake "we stopped looking" for
"the trail ended".
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from app.attribution.engine import AttributionEngine, NodeAssessment, rank_attributions
from app.chains.base import ChainAdapter
from app.chains.http import UpstreamError
from app.chains.spam import FilterOutcome, partition
from app.config import settings
from app.core.chains import Chain
from app.core.models import (
    AddressProfile,
    Attribution,
    NodeRole,
    TraceEdge,
    TraceNode,
    TraceResult,
    TraceStats,
    Transfer,
)

log = logging.getLogger(__name__)

# How many downstream branches to follow from any one address. Fraud wallets
# fan out deliberately to exhaust an investigator's budget; following the
# highest-value branches first finds the bulk of the money without chasing
# hundreds of dust splits.
MAX_BRANCHES_PER_NODE = 8

# Concurrency across a BFS level. Kept low: the bottleneck is the explorer
# rate limit, and exceeding it produces 429s that slow the trace overall.
LEVEL_CONCURRENCY = 3


class Tracer:
    def __init__(
        self,
        adapters: dict[Chain, ChainAdapter],
        engine: AttributionEngine,
        *,
        max_hops: int | None = None,
        max_nodes: int | None = None,
        transfers_per_address: int | None = None,
    ) -> None:
        self.adapters = adapters
        self.engine = engine
        self.max_hops = max_hops if max_hops is not None else settings.max_trace_hops
        self.max_nodes = max_nodes or settings.max_nodes_per_trace
        self.transfers_per_address = (
            transfers_per_address or settings.max_transfers_per_address
        )

    async def trace(self, address: str, chain: Chain) -> TraceResult:
        started = time.monotonic()
        adapter = self.adapters.get(chain)
        if adapter is None:
            raise ValueError(f"no adapter registered for chain {chain}")

        nodes: dict[str, TraceNode] = {}
        edges: dict[tuple[str, str, str], TraceEdge] = {}
        attributions: list[Attribution] = []
        warnings: list[str] = []
        filtered_summary: dict[str, int] = defaultdict(int)
        deception_findings: list[str] = []
        stats = TraceStats()

        visited: set[str] = set()
        # (address, depth, traced_value_usd, inbound_tx_hashes)
        frontier: list[tuple[str, int, Decimal, list[str]]] = [
            (address, 0, Decimal(0), [])
        ]

        while frontier:
            if len(visited) >= self.max_nodes:
                stats.truncated = True
                stats.truncation_reason = (
                    f"node budget of {self.max_nodes} exhausted; the graph is "
                    f"wider than the search allowance"
                )
                warnings.append(stats.truncation_reason)
                break

            # Collapse duplicates *within* the level, not just against
            # `visited`. One address is commonly reached by several edges at
            # once -- a hop that forwards both USDT and TRX produces two edges
            # to the same target -- and without merging here it would be
            # fetched twice, double-counted in the stats, and would burn twice
            # the rate-limit budget. Traced value sums across those paths,
            # which is exactly what the haircut model intends.
            pending: dict[str, tuple[str, int, Decimal, list[str]]] = {}
            for node_address, depth, value, evidence in frontier:
                if node_address in visited:
                    continue
                previous = pending.get(node_address)
                if previous is None:
                    pending[node_address] = (node_address, depth, value, evidence)
                else:
                    pending[node_address] = (
                        node_address,
                        min(previous[1], depth),
                        previous[2] + value,
                        list(dict.fromkeys(previous[3] + evidence))[:10],
                    )
            level = list(pending.values())
            frontier = []
            if not level:
                break

            # Highest-value branches first, so a truncated trace still
            # captured the money that matters.
            level.sort(key=lambda item: item[2], reverse=True)
            level = level[: self.max_nodes - len(visited)]
            for item in level:
                visited.add(item[0])

            semaphore = asyncio.Semaphore(LEVEL_CONCURRENCY)

            async def explore(
                item: tuple[str, int, Decimal, list[str]],
            ) -> tuple[
                tuple[str, int, Decimal, list[str]],
                tuple[NodeAssessment, FilterOutcome, AddressProfile] | None,
            ]:
                async with semaphore:
                    return item, await self._visit(adapter, item, stats, warnings)

            results = await asyncio.gather(
                *(explore(item) for item in level), return_exceptions=False
            )

            for (node_address, depth, traced_value, evidence), visit in results:
                if visit is None:
                    # The address stays in the graph, explicitly marked as
                    # unexamined. Dropping it would render a rate-limit gap
                    # indistinguishable from the end of the money trail.
                    nodes[node_address] = TraceNode(
                        address=node_address,
                        chain=chain,
                        depth=depth,
                        role=NodeRole.SUBJECT if depth == 0 else NodeRole.TERMINAL,
                        value_in_usd=traced_value,
                        stop_reason=(
                            "could not be examined: upstream data unavailable. "
                            "This is a gap in the search, not a dead end -- "
                            "re-run to retry."
                        ),
                    )
                    stats.nodes_unreachable += 1
                    continue
                assessment, outcome, profile = visit

                node = self._build_node(
                    node_address, chain, depth, assessment, traced_value, profile
                )
                nodes[node_address] = node
                if assessment.attribution is not None:
                    attributions.append(assessment.attribution)

                stats.nodes_explored += 1
                stats.max_depth_reached = max(stats.max_depth_reached, depth)
                stats.transfers_examined += len(outcome.kept) + outcome.flagged_count

                for reason, count in outcome.reason_counts().items():
                    filtered_summary[reason] += count
                for transfer, verdict in outcome.counterfeits + outcome.poisoning_attempts:
                    deception_findings.append(
                        f"{node_address}: {verdict.detail} (tx {transfer.tx_hash})"
                    )

                if assessment.is_terminal:
                    node.stop_reason = assessment.terminal_reason
                    continue
                if depth >= self.max_hops:
                    node.stop_reason = (
                        f"hop limit of {self.max_hops} reached; funds may have "
                        f"moved further"
                    )
                    stats.truncated = True
                    continue

                for edge, next_value, edge_hashes in self._outgoing(
                    node_address, chain, outcome.kept, traced_value
                ):
                    key = (edge.source, edge.target, edge.asset_symbol)
                    if key in edges:
                        self._merge_edge(edges[key], edge)
                    else:
                        edges[key] = edge
                        stats.edges_discovered += 1
                    if edge.target not in visited:
                        frontier.append((edge.target, depth + 1, next_value, edge_hashes))

        stats.elapsed_seconds = round(time.monotonic() - started, 2)
        stats.upstream_calls = getattr(adapter.http, "upstream_calls", 0)
        stats.cache_hits = adapter.http.cache.stats.hits

        if adapter.http.stale_serves:
            warnings.append(
                f"{adapter.http.stale_serves} response(s) were served from stale "
                f"cache after upstream failures; the graph may not reflect the "
                f"current chain state"
            )

        return TraceResult(
            subject_address=address,
            chain=chain,
            generated_at=datetime.now(UTC),
            nodes=sorted(nodes.values(), key=lambda n: (n.depth, n.address)),
            edges=list(edges.values()),
            attributions=rank_attributions(attributions),
            stats=stats,
            warnings=warnings,
            filtered_summary=dict(filtered_summary),
            # Cap: a busy wallet attracts hundreds of identical spam tokens,
            # and a report needs the pattern, not every instance.
            deception_findings=list(dict.fromkeys(deception_findings))[:25],
        )

    # -- internals ---------------------------------------------------------

    async def _visit(
        self,
        adapter: ChainAdapter,
        item: tuple[str, int, Decimal, list[str]],
        stats: TraceStats,
        warnings: list[str],
    ) -> tuple[NodeAssessment, FilterOutcome, AddressProfile] | None:
        node_address, depth, traced_value, evidence = item
        try:
            transfers = await adapter.fetch_transfers(
                node_address, limit=self.transfers_per_address
            )
            account = await adapter.fetch_account(node_address)
        except UpstreamError as exc:
            warnings.append(f"could not fetch {node_address}: {exc}")
            log.warning("fetch failed for %s: %s", node_address, exc)
            return None

        outcome = partition(transfers)
        profile = adapter.build_profile(
            node_address,
            outcome.kept,
            account,
            requested_limit=self.transfers_per_address,
            raw_transfer_count=len(transfers),
        )
        assessment = self.engine.assess(
            address=node_address,
            chain=adapter.chain,
            depth=depth,
            profile=profile,
            transfers=outcome.kept,
            # Nothing has been traced into the subject, so use what it was
            # actually paid. Otherwise a hop-0 attribution reports $0 while
            # the node beside it shows the real figure.
            value_in_usd=(
                profile.total_received_usd if depth == 0 else traced_value
            ),
            evidence_tx_hashes=evidence,
        )
        return assessment, outcome, profile

    def _build_node(
        self,
        address: str,
        chain: Chain,
        depth: int,
        assessment: NodeAssessment,
        traced_value: Decimal,
        profile: AddressProfile,
    ) -> TraceNode:
        return TraceNode(
            address=address,
            chain=chain,
            depth=depth,
            role=NodeRole.SUBJECT if depth == 0 else assessment.role,
            label=assessment.label,
            category=assessment.category,
            # Nothing has been traced *into* the subject -- it is where the
            # trace begins -- so show what it actually received instead of a
            # meaningless zero.
            value_in_usd=(
                profile.total_received_usd if depth == 0 else traced_value
            ),
            profile=profile,
            risk_score=0.0,
        )

    def _outgoing(
        self,
        address: str,
        chain: Chain,
        transfers: list[Transfer],
        traced_value: Decimal,
    ) -> list[tuple[TraceEdge, Decimal, list[str]]]:
        """Aggregate outbound transfers into edges and haircut the value."""
        subject = address.lower()
        outbound = [t for t in transfers if t.from_address.lower() == subject]
        if not outbound:
            return []

        grouped: dict[tuple[str, str], list[Transfer]] = defaultdict(list)
        for transfer in outbound:
            grouped[(transfer.to_address, transfer.asset_symbol)].append(transfer)

        total_out = sum(
            (t.amount_usd or Decimal(0) for t in outbound), Decimal(0)
        )
        # At depth 0 nothing has been traced in yet, so the observed outbound
        # total *is* the traced value -- that is the money the victim's funds
        # became. Deeper nodes carry a haircut share down from their parent.
        basis = traced_value if traced_value > 0 else total_out

        results: list[tuple[TraceEdge, Decimal, list[str]]] = []
        for (target, symbol), group in grouped.items():
            amount = sum((t.amount for t in group), Decimal(0))
            usd = sum((t.amount_usd or Decimal(0) for t in group), Decimal(0))
            times = [t.block_time for t in group]
            hashes = [t.tx_hash for t in group if t.tx_hash]

            share = float(usd / total_out) if total_out > 0 else 1 / len(grouped)
            if share < settings.min_edge_value_ratio:
                continue  # immaterial branch

            edge = TraceEdge(
                source=address,
                target=target,
                chain=chain,
                asset_symbol=symbol,
                total_amount=amount,
                total_usd=usd,
                transfer_count=len(group),
                first_seen=min(times) if times else None,
                last_seen=max(times) if times else None,
                tx_hashes=hashes[:10],
            )
            results.append((edge, basis * Decimal(str(share)), hashes[:5]))

        results.sort(key=lambda item: item[1], reverse=True)
        return results[:MAX_BRANCHES_PER_NODE]

    @staticmethod
    def _merge_edge(existing: TraceEdge, new: TraceEdge) -> None:
        existing.total_amount += new.total_amount
        existing.total_usd += new.total_usd
        existing.transfer_count += new.transfer_count
        existing.tx_hashes = list(dict.fromkeys(existing.tx_hashes + new.tx_hashes))[:10]
        if new.first_seen and (not existing.first_seen or new.first_seen < existing.first_seen):
            existing.first_seen = new.first_seen
        if new.last_seen and (not existing.last_seen or new.last_seen > existing.last_seen):
            existing.last_seen = new.last_seen
