"""Attribution: deciding which known entity an address belongs to.

The problem statement asks for "the nearest exchange or VASP receiving direct
deposits". Three mechanisms answer that, in descending evidentiary strength:

1. **Direct label** -- the address is in the corpus. Strongest, rarest.
2. **Deposit-address heuristic** -- the address sweeps essentially everything
   it receives into a single known exchange wallet. That makes it a *customer
   deposit address*, and this is the finding that actually matters: an
   exchange's omnibus hot wallet is shared by millions of users and names
   nobody, whereas a deposit address maps to one KYC'd account. A notice
   naming the hot wallet is close to useless; one naming the deposit address
   identifies a person.
3. **Behavioural inference** -- the address has the shape of an exchange
   wallet but carries no label. Yields an unnamed but real VASP hit, which
   still tells an investigator that funds left the open chain.

Ranking puts the nearest hop first, then confidence, then value: an
investigator has hours, and the closest attributable VASP is where a
preservation request has the best chance of landing on funds still in place.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.attribution.behaviour import BehaviourClass, BehaviourVerdict, classify
from app.attribution.labels import LabelStore
from app.core.chains import Chain
from app.core.models import (
    AddressProfile,
    Attribution,
    AttributionMethod,
    KycTier,
    NodeRole,
    Transfer,
    VaspCategory,
    VaspRecord,
)

log = logging.getLogger(__name__)

# Categories that terminate a trace: funds have left the open chain into an
# entity that must be asked, not followed.
TERMINAL_CATEGORIES = frozenset(
    {
        VaspCategory.EXCHANGE,
        VaspCategory.PAYMENT_PROCESSOR,
        VaspCategory.SANCTIONED,
        VaspCategory.MIXER,
    }
)


@dataclass(frozen=True)
class _DepositTiming:
    """When value reached a VASP along the traced path."""

    first: datetime | None = None
    last: datetime | None = None


def _sweep_timing(
    address: str, destination: str | None, transfers: list[Transfer]
) -> _DepositTiming | None:
    """Timestamps of the sweep from a deposit address into its exchange.

    This is the deposit event an investigator cares about: the instant the
    money stopped being on the open chain and became a balance inside a
    regulated company. ``last`` is the one that decides whether the freeze
    window is still open.
    """
    if not destination:
        return None
    subject = address.lower()
    target = destination.lower()
    times = [
        transfer.block_time
        for transfer in transfers
        if transfer.from_address.lower() == subject
        and transfer.to_address.lower() == target
        and transfer.block_time
    ]
    if not times:
        return None
    return _DepositTiming(first=min(times), last=max(times))


@dataclass
class NodeAssessment:
    """Everything concluded about one address in the graph."""

    address: str
    chain: Chain
    depth: int
    behaviour: BehaviourVerdict
    record: VaspRecord | None = None
    role: NodeRole = NodeRole.INTERMEDIARY
    category: VaspCategory = VaspCategory.UNKNOWN
    label: str | None = None
    attribution: Attribution | None = None
    # True when tracing should not continue past this address.
    is_terminal: bool = False
    terminal_reason: str | None = None


class AttributionEngine:
    def __init__(self, labels: LabelStore) -> None:
        self.labels = labels

    def assess(
        self,
        *,
        address: str,
        chain: Chain,
        depth: int,
        profile: AddressProfile,
        transfers: list[Transfer],
        value_in_usd: Decimal = Decimal(0),
        evidence_tx_hashes: list[str] | None = None,
        taint_ratio: float = 1.0,
        arrived_at: datetime | None = None,
    ) -> NodeAssessment:
        behaviour = classify(profile, transfers)
        record = self.labels.lookup(chain, address)
        assessment = NodeAssessment(
            address=address,
            chain=chain,
            depth=depth,
            behaviour=behaviour,
            record=record,
        )
        timing = _DepositTiming(first=arrived_at, last=arrived_at)

        if record is not None:
            self._apply_direct_label(
                assessment, record, value_in_usd, evidence_tx_hashes, taint_ratio, timing
            )
        elif behaviour.behaviour is BehaviourClass.DEPOSIT_ADDRESS:
            # For a sweep, the moment that matters is when the funds left this
            # address *into the exchange*, not when they arrived here.
            self._apply_deposit_heuristic(
                assessment,
                behaviour,
                chain,
                value_in_usd,
                evidence_tx_hashes,
                taint_ratio,
                _sweep_timing(address, behaviour.forwards_to, transfers) or timing,
            )
        elif behaviour.behaviour is BehaviourClass.EXCHANGE_LIKE:
            self._apply_behavioural_inference(
                assessment, behaviour, value_in_usd, evidence_tx_hashes, taint_ratio, timing
            )
        else:
            assessment.role = self._role_for_behaviour(behaviour.behaviour)

        if profile.is_contract and assessment.role is NodeRole.INTERMEDIARY:
            assessment.role = NodeRole.CONTRACT

        return assessment

    # -- mechanisms --------------------------------------------------------

    def _apply_direct_label(
        self,
        assessment: NodeAssessment,
        record: VaspRecord,
        value_in_usd: Decimal,
        evidence: list[str] | None,
        taint_ratio: float,
        timing: _DepositTiming,
    ) -> None:
        assessment.category = record.category
        assessment.label = record.name
        assessment.role = (
            NodeRole.MIXER
            if record.category is VaspCategory.MIXER
            else NodeRole.BRIDGE
            if record.category is VaspCategory.BRIDGE
            else NodeRole.VASP_HOT
        )
        assessment.is_terminal = record.category in TERMINAL_CATEGORIES
        if assessment.is_terminal:
            assessment.terminal_reason = (
                f"funds reached {record.name} ({record.category.value}); "
                f"further movement is off-chain and must be obtained from the entity"
            )

        assessment.attribution = Attribution(
            vasp_name=record.name,
            category=record.category,
            chain=record.chain,
            matched_address=record.address,
            method=AttributionMethod.DIRECT_LABEL,
            confidence=record.confidence,
            hops_from_subject=assessment.depth,
            value_usd=value_in_usd,
            taint_ratio=round(min(1.0, taint_ratio), 6),
            first_deposit_at=timing.first,
            last_deposit_at=timing.last,
            evidence_tx_hashes=list(evidence or []),
            reasoning=[
                f"address is a known {record.category.value} address of "
                f"{record.name}",
                f"corpus source: {record.source}",
            ]
            + ([record.notes] if record.notes else []),
            jurisdiction=record.jurisdiction,
            kyc_tier=record.kyc_tier,
            fiu_ind_registered=record.fiu_ind_registered,
            compliance_contact=record.compliance_contact,
        )

    def _apply_deposit_heuristic(
        self,
        assessment: NodeAssessment,
        behaviour: BehaviourVerdict,
        chain: Chain,
        value_in_usd: Decimal,
        evidence: list[str] | None,
        taint_ratio: float,
        timing: _DepositTiming,
    ) -> None:
        """A sweep into a known VASP makes this address a customer deposit."""
        destination = behaviour.forwards_to
        upstream = self.labels.lookup(chain, destination) if destination else None

        if upstream is None:
            # Sweeps into something we cannot name. Still a strong structural
            # finding -- record it as an unnamed VASP rather than discard it.
            assessment.role = NodeRole.VASP_DEPOSIT
            assessment.category = VaspCategory.EXCHANGE
            assessment.label = "Unidentified VASP (deposit address)"
            assessment.attribution = Attribution(
                vasp_name="Unidentified VASP",
                category=VaspCategory.EXCHANGE,
                chain=chain,
                matched_address=destination or assessment.address,
                method=AttributionMethod.DEPOSIT_ADDRESS_HEURISTIC,
                confidence=round(behaviour.confidence * 0.7, 3),
                hops_from_subject=assessment.depth,
                value_usd=value_in_usd,
                taint_ratio=round(min(1.0, taint_ratio), 6),
                first_deposit_at=timing.first,
                last_deposit_at=timing.last,
                evidence_tx_hashes=list(evidence or []),
                reasoning=behaviour.reasoning
                + [
                    "the receiving wallet is not in the label corpus, so the "
                    "operator cannot be named from available data",
                ],
                deposit_address=assessment.address,
            )
            return

        assessment.role = NodeRole.VASP_DEPOSIT
        assessment.category = upstream.category
        assessment.label = f"{upstream.name} (deposit address)"
        assessment.is_terminal = upstream.category in TERMINAL_CATEGORIES
        assessment.terminal_reason = (
            f"deposit address at {upstream.name}; the account holder is "
            f"identifiable from that VASP's KYC records"
        )

        assessment.attribution = Attribution(
            vasp_name=upstream.name,
            category=upstream.category,
            chain=chain,
            matched_address=upstream.address,
            method=AttributionMethod.DEPOSIT_ADDRESS_HEURISTIC,
            # Compounded: the label's own confidence limits the conclusion.
            confidence=round(behaviour.confidence * upstream.confidence, 3),
            hops_from_subject=assessment.depth,
            value_usd=value_in_usd,
            taint_ratio=round(min(1.0, taint_ratio), 6),
            first_deposit_at=timing.first,
            last_deposit_at=timing.last,
            evidence_tx_hashes=list(evidence or []),
            reasoning=behaviour.reasoning
            + [
                f"the receiving wallet {upstream.address} is a known "
                f"{upstream.category.value} wallet of {upstream.name}",
                "an address that sweeps its entire balance into an exchange "
                "wallet is that exchange's customer deposit address, which "
                "maps to one KYC'd account",
            ],
            jurisdiction=upstream.jurisdiction,
            kyc_tier=upstream.kyc_tier,
            fiu_ind_registered=upstream.fiu_ind_registered,
            compliance_contact=upstream.compliance_contact,
            deposit_address=assessment.address,
        )

    def _apply_behavioural_inference(
        self,
        assessment: NodeAssessment,
        behaviour: BehaviourVerdict,
        value_in_usd: Decimal,
        evidence: list[str] | None,
        taint_ratio: float,
        timing: _DepositTiming,
    ) -> None:
        assessment.role = NodeRole.VASP_HOT
        assessment.category = VaspCategory.EXCHANGE
        assessment.label = "Unidentified exchange-class wallet"
        assessment.is_terminal = True
        assessment.terminal_reason = (
            "address behaves as an exchange omnibus wallet; onward movement is "
            "internal to that service and not visible on-chain"
        )
        assessment.attribution = Attribution(
            vasp_name="Unidentified exchange-class wallet",
            category=VaspCategory.EXCHANGE,
            chain=assessment.chain,
            matched_address=assessment.address,
            method=AttributionMethod.BEHAVIOURAL_INFERENCE,
            confidence=round(behaviour.confidence * 0.8, 3),
            hops_from_subject=assessment.depth,
            value_usd=value_in_usd,
            taint_ratio=round(min(1.0, taint_ratio), 6),
            first_deposit_at=timing.first,
            last_deposit_at=timing.last,
            evidence_tx_hashes=list(evidence or []),
            reasoning=behaviour.reasoning
            + [
                "no label matched, so the operator is inferred from behaviour "
                "alone and must be confirmed before any notice is served",
            ],
            kyc_tier=KycTier.UNKNOWN,
        )

    @staticmethod
    def _role_for_behaviour(behaviour: BehaviourClass) -> NodeRole:
        return {
            BehaviourClass.PASS_THROUGH: NodeRole.INTERMEDIARY,
            BehaviourClass.CONSOLIDATOR: NodeRole.INTERMEDIARY,
            BehaviourClass.DISTRIBUTOR: NodeRole.INTERMEDIARY,
            BehaviourClass.TERMINAL_HOLDER: NodeRole.TERMINAL,
            BehaviourClass.LOW_ACTIVITY: NodeRole.TERMINAL,
        }.get(behaviour, NodeRole.INTERMEDIARY)


def _earliest(a: datetime | None, b: datetime | None) -> datetime | None:
    return min(x for x in (a, b) if x is not None) if (a or b) else None


def _latest(a: datetime | None, b: datetime | None) -> datetime | None:
    return max(x for x in (a, b) if x is not None) if (a or b) else None


def rank_attributions(attributions: list[Attribution]) -> list[Attribution]:
    """Merge duplicates per VASP and order by investigative usefulness.

    Nearest hop first: every additional hop is another layering step and
    another chance the funds have already moved on. Confidence and value break
    ties, in that order -- a certain small hit beats a speculative large one,
    because a notice served on the wrong exchange costs the whole window.
    """
    merged: dict[tuple[str, str], Attribution] = {}
    grouped_evidence: dict[tuple[str, str], list[str]] = defaultdict(list)

    for attribution in attributions:
        key = (attribution.vasp_name, attribution.deposit_address or attribution.matched_address)
        grouped_evidence[key].extend(attribution.evidence_tx_hashes)
        existing = merged.get(key)
        if existing is None:
            merged[key] = attribution.model_copy(deep=True)
            continue
        # Keep the closest sighting, but accumulate the value across paths.
        best = (
            attribution
            if (attribution.hops_from_subject, -attribution.confidence)
            < (existing.hops_from_subject, -existing.confidence)
            else existing
        )
        combined = best.model_copy(deep=True)
        combined.value_usd = existing.value_usd + attribution.value_usd
        # Taint sums across converging paths -- two routes carrying 30% and
        # 40% of the subject's outflow to one exchange means 70% reached it.
        # Capped at 1.0, since no more than all of the money can arrive.
        combined.taint_ratio = round(
            min(1.0, existing.taint_ratio + attribution.taint_ratio), 6
        )
        combined.first_deposit_at = _earliest(
            existing.first_deposit_at, attribution.first_deposit_at
        )
        combined.last_deposit_at = _latest(
            existing.last_deposit_at, attribution.last_deposit_at
        )
        merged[key] = combined

    for key, attribution in merged.items():
        # Cap evidence: a report needs enough hashes to verify, not thousands.
        attribution.evidence_tx_hashes = list(dict.fromkeys(grouped_evidence[key]))[:20]

    return sorted(
        merged.values(),
        key=lambda a: (a.hops_from_subject, -a.confidence, -float(a.value_usd)),
    )
