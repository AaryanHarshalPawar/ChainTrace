"""Behavioural classification of an address from its transaction profile.

Labels run out fast. Commercial attribution datasets are expensive, community
lists are stale, and a burner wallet created this morning appears in no corpus
at all. But an address's *shape* gives it away regardless: an exchange omnibus
wallet, a user deposit address and a layering hop each have a signature that
holds across chains and survives the absence of any label.

This is what lets the system answer usefully on an address nobody has ever
seen before -- "funds reached an exchange-class wallet" is actionable even
when the exchange cannot yet be named.

Every classification returns its reasoning as plain sentences, because an
investigator has to be able to justify the inference, and a bare confidence
score justifies nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.core.models import AddressProfile, Transfer


class BehaviourClass(StrEnum):
    EXCHANGE_LIKE = "exchange_like"
    DEPOSIT_ADDRESS = "deposit_address"
    PASS_THROUGH = "pass_through"
    CONSOLIDATOR = "consolidator"
    DISTRIBUTOR = "distributor"
    TERMINAL_HOLDER = "terminal_holder"
    LOW_ACTIVITY = "low_activity"


@dataclass
class BehaviourVerdict:
    behaviour: BehaviourClass
    confidence: float
    reasoning: list[str] = field(default_factory=list)
    # Set when the address forwards nearly everything to one destination --
    # the candidate VASP hot wallet behind a deposit address.
    forwards_to: str | None = None
    forward_ratio: float = 0.0


# Thresholds are evaluated inside a bounded observation window (by default the
# most recent 200 transfers), so they describe density, not lifetime totals.
EXCHANGE_MIN_SENDERS = 40
EXCHANGE_MIN_TRANSFERS = 80
PASS_THROUGH_MAX_RETAINED = 0.05
DEPOSIT_MIN_FORWARD_RATIO = 0.90
CONSOLIDATOR_MIN_SENDERS = 10
DISTRIBUTOR_MIN_RECEIVERS = 20


def _dominant_destination(
    address: str, transfers: list[Transfer]
) -> tuple[str | None, float, Decimal]:
    """Find the destination taking the largest share of outbound value.

    Returns ``(destination, share_of_outbound, total_outbound_usd)``. Falls
    back to counting transfers when nothing could be valued, so an unvalued
    but structurally obvious forwarder is still recognised.
    """
    subject = address.lower()
    outbound = [t for t in transfers if t.from_address.lower() == subject]
    if not outbound:
        return None, 0.0, Decimal(0)

    totals: dict[str, Decimal] = {}
    total = Decimal(0)
    for transfer in outbound:
        value = transfer.amount_usd or Decimal(0)
        totals[transfer.to_address] = totals.get(transfer.to_address, Decimal(0)) + value
        total += value

    if total > 0:
        destination, value = max(totals.items(), key=lambda kv: kv[1])
        return destination, float(value / total), total

    counts: dict[str, int] = {}
    for transfer in outbound:
        counts[transfer.to_address] = counts.get(transfer.to_address, 0) + 1
    destination, count = max(counts.items(), key=lambda kv: kv[1])
    return destination, count / len(outbound), Decimal(0)


def classify(
    profile: AddressProfile, transfers: list[Transfer]
) -> BehaviourVerdict:
    reasoning: list[str] = []
    destination, forward_ratio, outbound_usd = _dominant_destination(
        profile.address, transfers
    )

    if profile.transfer_count < 3:
        return BehaviourVerdict(
            BehaviourClass.LOW_ACTIVITY,
            0.4,
            [
                f"only {profile.transfer_count} material transfers observed; "
                f"too little history to classify"
            ],
        )

    # -- exchange omnibus --------------------------------------------------
    # Hundreds of unrelated senders paying into one address, with the address
    # also paying out widely, is the shape of a hot wallet. No individual
    # fraudster's wallet looks like this.
    if (
        profile.unique_senders >= EXCHANGE_MIN_SENDERS
        and profile.transfer_count >= EXCHANGE_MIN_TRANSFERS
    ):
        reasoning.append(
            f"{profile.unique_senders} distinct senders across "
            f"{profile.transfer_count} transfers in the observed window"
        )
        if profile.is_truncated:
            reasoning.append(
                "history was truncated by the observation limit, so the true "
                "counterparty count is higher than measured"
            )
        if profile.unique_receivers >= 10:
            reasoning.append(
                f"also pays out to {profile.unique_receivers} distinct "
                f"addresses, consistent with customer withdrawals"
            )
        confidence = 0.75 if profile.is_truncated else 0.65
        if profile.total_received_usd > Decimal(10_000_000):
            reasoning.append(
                f"inbound value of ${profile.total_received_usd:,.0f} in the "
                f"window far exceeds any plausible individual"
            )
            confidence = min(0.9, confidence + 0.15)
        return BehaviourVerdict(BehaviourClass.EXCHANGE_LIKE, confidence, reasoning)

    # -- deposit address ---------------------------------------------------
    # Receives, then sweeps essentially everything to exactly one place and
    # keeps nothing. This is how an exchange sweeps a customer's deposit
    # address into its omnibus wallet.
    if (
        destination
        and forward_ratio >= DEPOSIT_MIN_FORWARD_RATIO
        and profile.unique_receivers <= 2
        and profile.retained_ratio <= PASS_THROUGH_MAX_RETAINED
        and profile.inbound_count >= 1
    ):
        reasoning.append(
            f"forwards {forward_ratio:.0%} of outbound value to a single "
            f"address ({destination})"
        )
        reasoning.append(
            f"retains {profile.retained_ratio:.1%} of what it receives -- a "
            f"sweep, not a wallet in use"
        )
        return BehaviourVerdict(
            BehaviourClass.DEPOSIT_ADDRESS,
            0.7,
            reasoning,
            forwards_to=destination,
            forward_ratio=forward_ratio,
        )

    # -- layering hop ------------------------------------------------------
    if (
        profile.retained_ratio <= PASS_THROUGH_MAX_RETAINED
        and profile.outbound_count >= 1
        and profile.inbound_count >= 1
    ):
        reasoning.append(
            f"passes through {profile.retained_ratio:.1%} retained: receives "
            f"{profile.inbound_count} and forwards {profile.outbound_count}"
        )
        if profile.first_seen and profile.last_seen:
            lifetime = profile.last_seen - profile.first_seen
            if lifetime.total_seconds() < 86400 * 7:
                reasoning.append(
                    f"active for only {lifetime.days}d "
                    f"{lifetime.seconds // 3600}h -- consistent with a burner "
                    f"wallet used for a single layering step"
                )
        return BehaviourVerdict(
            BehaviourClass.PASS_THROUGH,
            0.6,
            reasoning,
            forwards_to=destination,
            forward_ratio=forward_ratio,
        )

    # -- collector ---------------------------------------------------------
    if profile.unique_senders >= CONSOLIDATOR_MIN_SENDERS and profile.retained_ratio > 0.5:
        reasoning.append(
            f"collects from {profile.unique_senders} senders and retains "
            f"{profile.retained_ratio:.0%} -- a consolidation point"
        )
        return BehaviourVerdict(BehaviourClass.CONSOLIDATOR, 0.6, reasoning)

    # -- payout ------------------------------------------------------------
    if profile.unique_receivers >= DISTRIBUTOR_MIN_RECEIVERS:
        reasoning.append(
            f"pays out to {profile.unique_receivers} distinct addresses "
            f"against {profile.unique_senders} senders"
        )
        return BehaviourVerdict(BehaviourClass.DISTRIBUTOR, 0.55, reasoning)

    # -- still holding -----------------------------------------------------
    if profile.retained_ratio > 0.5:
        reasoning.append(
            f"retains {profile.retained_ratio:.0%} of ${profile.total_received_usd:,.2f} "
            f"received -- funds may still be recoverable here"
        )
        return BehaviourVerdict(BehaviourClass.TERMINAL_HOLDER, 0.6, reasoning)

    reasoning.append(
        f"{profile.transfer_count} transfers, {profile.unique_senders} senders, "
        f"{profile.unique_receivers} receivers -- no distinctive pattern"
    )
    return BehaviourVerdict(BehaviourClass.LOW_ACTIVITY, 0.3, reasoning)
