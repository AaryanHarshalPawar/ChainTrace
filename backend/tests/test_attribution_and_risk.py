"""Behavioural classification, attribution and risk scoring.

The deposit-address heuristic is the system's central claim, so it gets the
most attention here: it is the finding that turns a wallet trace into a named
person, and a false positive sends a preservation notice to the wrong company.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.attribution.behaviour import BehaviourClass, classify
from app.attribution.engine import AttributionEngine, rank_attributions
from app.attribution.labels import LabelStore
from app.core.chains import Chain
from app.core.models import (
    AddressProfile,
    Attribution,
    AttributionMethod,
    KycTier,
    NodeRole,
    RiskLevel,
    TraceNode,
    TraceResult,
    TraceStats,
    Transfer,
    VaspCategory,
    VaspRecord,
)
from app.risk.scoring import assess, recommend

HOT_WALLET = "TExchangeHotWallet00000000000000000"
DEPOSIT = "TDepositAddress0000000000000000000"
VICTIM_PAID = "TFraudCollector0000000000000000000"
USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


def profile(**kwargs) -> AddressProfile:
    base = dict(
        address=DEPOSIT,
        chain=Chain.TRON,
        transfer_count=10,
        inbound_count=5,
        outbound_count=5,
        unique_senders=3,
        unique_receivers=1,
        total_received_usd=Decimal(10_000),
        total_sent_usd=Decimal(10_000),
        first_seen=datetime(2026, 1, 1, tzinfo=UTC),
        last_seen=datetime(2026, 1, 5, tzinfo=UTC),
    )
    base.update(kwargs)
    return AddressProfile(**base)


def transfer(sender: str, recipient: str, usd: str) -> Transfer:
    return Transfer(
        chain=Chain.TRON,
        tx_hash=f"tx-{sender[:6]}-{recipient[:6]}-{usd}",
        block_time=datetime(2026, 1, 2, tzinfo=UTC),
        from_address=sender,
        to_address=recipient,
        asset_symbol="USDT",
        asset_contract=USDT,
        amount=Decimal(usd),
        amount_usd=Decimal(usd),
    )


@pytest.fixture
def labels() -> LabelStore:
    store = LabelStore()
    store._add(
        VaspRecord(
            address=HOT_WALLET,
            chain=Chain.TRON,
            name="Test Exchange",
            category=VaspCategory.EXCHANGE,
            source="unit test",
            confidence=0.9,
            jurisdiction="SG",
            kyc_tier=KycTier.FULL_KYC,
            fiu_ind_registered=True,
            compliance_contact="le@example.test",
        )
    )
    return store


# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------


def test_sweep_into_one_address_is_a_deposit_address():
    transfers = [
        transfer(VICTIM_PAID, DEPOSIT, "10000"),
        transfer(DEPOSIT, HOT_WALLET, "10000"),
    ]
    verdict = classify(profile(), transfers)
    assert verdict.behaviour is BehaviourClass.DEPOSIT_ADDRESS
    assert verdict.forwards_to == HOT_WALLET
    assert verdict.forward_ratio == pytest.approx(1.0)


def test_split_outflow_is_not_a_deposit_address():
    """Two comparable destinations is layering, not an exchange sweep."""
    transfers = [
        transfer(VICTIM_PAID, DEPOSIT, "10000"),
        transfer(DEPOSIT, HOT_WALLET, "5000"),
        transfer(DEPOSIT, "TOther00000000000000000000000000000", "5000"),
    ]
    verdict = classify(profile(unique_receivers=2), transfers)
    assert verdict.behaviour is not BehaviourClass.DEPOSIT_ADDRESS


def test_high_fan_in_is_exchange_like():
    verdict = classify(
        profile(unique_senders=250, transfer_count=200, unique_receivers=40,
                total_received_usd=Decimal(50_000_000),
                total_sent_usd=Decimal(20_000_000)),
        [],
    )
    assert verdict.behaviour is BehaviourClass.EXCHANGE_LIKE


def test_holding_funds_is_terminal_holder():
    verdict = classify(
        profile(total_received_usd=Decimal(10_000), total_sent_usd=Decimal(1_000),
                unique_receivers=1, outbound_count=1),
        [],
    )
    assert verdict.behaviour is BehaviourClass.TERMINAL_HOLDER


def test_too_little_history_is_not_over_classified():
    verdict = classify(profile(transfer_count=1, inbound_count=1, outbound_count=0), [])
    assert verdict.behaviour is BehaviourClass.LOW_ACTIVITY


def test_reasoning_is_always_provided():
    """An investigator must be able to justify the inference."""
    verdict = classify(profile(), [transfer(DEPOSIT, HOT_WALLET, "10000")])
    assert verdict.reasoning
    assert all(isinstance(line, str) and line for line in verdict.reasoning)


# --------------------------------------------------------------------------
# Attribution
# --------------------------------------------------------------------------


def test_deposit_address_attributed_to_the_exchange_behind_it(labels):
    """The core claim: a sweep into a known exchange names that exchange."""
    engine = AttributionEngine(labels)
    transfers = [
        transfer(VICTIM_PAID, DEPOSIT, "10000"),
        transfer(DEPOSIT, HOT_WALLET, "10000"),
    ]
    assessment = engine.assess(
        address=DEPOSIT,
        chain=Chain.TRON,
        depth=1,
        profile=profile(),
        transfers=transfers,
        value_in_usd=Decimal(10_000),
    )

    assert assessment.role is NodeRole.VASP_DEPOSIT
    attribution = assessment.attribution
    assert attribution is not None
    assert attribution.vasp_name == "Test Exchange"
    assert attribution.method is AttributionMethod.DEPOSIT_ADDRESS_HEURISTIC
    # The deposit address is what a notice must name.
    assert attribution.deposit_address == DEPOSIT
    assert attribution.fiu_ind_registered is True
    # Confidence compounds the label's own confidence.
    assert attribution.confidence < 0.9


def test_unknown_upstream_still_yields_a_finding(labels):
    """Sweeping into an unlabelled wallet is reported, not discarded."""
    engine = AttributionEngine(labels)
    unknown = "TUnknownWallet000000000000000000000"
    transfers = [
        transfer(VICTIM_PAID, DEPOSIT, "10000"),
        transfer(DEPOSIT, unknown, "10000"),
    ]
    assessment = engine.assess(
        address=DEPOSIT, chain=Chain.TRON, depth=1,
        profile=profile(), transfers=transfers,
    )
    assert assessment.attribution is not None
    assert assessment.attribution.vasp_name == "Unidentified VASP"
    assert assessment.attribution.deposit_address == DEPOSIT


def test_direct_label_on_sanctioned_address_is_terminal():
    store = LabelStore()
    store._add(
        VaspRecord(
            address=VICTIM_PAID, chain=Chain.TRON, name="SDN ENTITY",
            category=VaspCategory.SANCTIONED, source="OFAC SDN", confidence=1.0,
        )
    )
    assessment = AttributionEngine(store).assess(
        address=VICTIM_PAID, chain=Chain.TRON, depth=0,
        profile=profile(address=VICTIM_PAID), transfers=[],
    )
    assert assessment.is_terminal
    assert assessment.attribution.confidence == 1.0
    assert assessment.attribution.method is AttributionMethod.DIRECT_LABEL


def test_ranking_prefers_nearest_hop():
    far = Attribution(
        vasp_name="Far", category=VaspCategory.EXCHANGE, chain=Chain.TRON,
        matched_address="a", method=AttributionMethod.DIRECT_LABEL,
        confidence=0.99, hops_from_subject=4, value_usd=Decimal(1_000_000),
    )
    near = Attribution(
        vasp_name="Near", category=VaspCategory.EXCHANGE, chain=Chain.TRON,
        matched_address="b", method=AttributionMethod.DIRECT_LABEL,
        confidence=0.6, hops_from_subject=1, value_usd=Decimal(100),
    )
    assert rank_attributions([far, near])[0].vasp_name == "Near"


def test_ranking_merges_duplicate_vasp_and_sums_value():
    def hit(hops: int, usd: str) -> Attribution:
        return Attribution(
            vasp_name="Same", category=VaspCategory.EXCHANGE, chain=Chain.TRON,
            matched_address="x", method=AttributionMethod.DIRECT_LABEL,
            confidence=0.8, hops_from_subject=hops, value_usd=Decimal(usd),
        )

    ranked = rank_attributions([hit(2, "500"), hit(3, "700")])
    assert len(ranked) == 1
    assert ranked[0].hops_from_subject == 2, "keep the closest sighting"
    assert ranked[0].value_usd == Decimal(1200), "but sum value across paths"


# --------------------------------------------------------------------------
# Risk
# --------------------------------------------------------------------------


def make_result(**kwargs) -> TraceResult:
    base = dict(
        subject_address=VICTIM_PAID,
        chain=Chain.TRON,
        generated_at=datetime.now(UTC),
        nodes=[],
        edges=[],
        attributions=[],
        stats=TraceStats(),
    )
    base.update(kwargs)
    return TraceResult(**base)


def test_sanctions_exposure_scores_critical():
    result = make_result(
        attributions=[
            Attribution(
                vasp_name="SDN ENTITY", category=VaspCategory.SANCTIONED,
                chain=Chain.TRON, matched_address=VICTIM_PAID,
                method=AttributionMethod.DIRECT_LABEL, confidence=1.0,
                hops_from_subject=1,
            )
        ]
    )
    risk = assess(result)
    assert risk.level is RiskLevel.CRITICAL
    assert any(s.code == "SANCTIONS_EXPOSURE" for s in risk.signals)


def test_multi_victim_collection_detected():
    subject = TraceNode(
        address=VICTIM_PAID, chain=Chain.TRON, depth=0, role=NodeRole.SUBJECT,
        profile=profile(address=VICTIM_PAID, unique_senders=57),
    )
    risk = assess(make_result(nodes=[subject]))
    assert any(s.code == "MULTI_VICTIM_COLLECTION" for s in risk.signals)


def test_clean_trace_is_not_alarming():
    risk = assess(make_result())
    assert risk.level in (RiskLevel.INFO, RiskLevel.LOW)
    assert risk.score < 25


def test_score_is_capped_at_100():
    subject = TraceNode(
        address=VICTIM_PAID, chain=Chain.TRON, depth=0, role=NodeRole.SUBJECT,
        profile=profile(address=VICTIM_PAID, unique_senders=500,
                        first_seen=datetime.now(UTC) - timedelta(days=2),
                        last_seen=datetime.now(UTC)),
    )
    result = make_result(
        nodes=[subject, TraceNode(address="m", chain=Chain.TRON, depth=1,
                                  role=NodeRole.MIXER)],
        attributions=[
            Attribution(
                vasp_name="SDN", category=VaspCategory.SANCTIONED, chain=Chain.TRON,
                matched_address="s", method=AttributionMethod.DIRECT_LABEL,
                confidence=1.0, hops_from_subject=1, value_usd=Decimal(5_000_000),
            )
        ],
        filtered_summary={"counterfeit_token": 9, "address_poisoning": 3},
        stats=TraceStats(max_depth_reached=5),
    )
    risk = assess(result)
    assert risk.score == 100.0
    assert risk.level is RiskLevel.CRITICAL


def test_every_signal_explains_itself():
    """A bare score justifies nothing to a magistrate."""
    subject = TraceNode(
        address=VICTIM_PAID, chain=Chain.TRON, depth=0, role=NodeRole.SUBJECT,
        profile=profile(address=VICTIM_PAID, unique_senders=57),
    )
    for signal in assess(make_result(nodes=[subject])).signals:
        assert signal.detail and len(signal.detail) > 20
        assert signal.code and signal.title


def test_behavioural_inference_recommends_confirming_operator_first():
    """Never advise serving a notice on an unverified, inferred operator."""
    result = make_result(
        attributions=[
            Attribution(
                vasp_name="Unidentified exchange-class wallet",
                category=VaspCategory.EXCHANGE, chain=Chain.TRON,
                matched_address=HOT_WALLET,
                method=AttributionMethod.BEHAVIOURAL_INFERENCE,
                confidence=0.5, hops_from_subject=2,
            )
        ]
    )
    actions = recommend(result, assess(result))
    assert actions
    assert "Confirm the operator" in actions[0].action


def test_truncated_trace_recommends_rerun():
    result = make_result(
        stats=TraceStats(truncated=True, truncation_reason="node budget exhausted")
    )
    actions = recommend(result, assess(result))
    assert any("Re-run" in a.action for a in actions)


# --------------------------------------------------------------------------
# Taint ratio and deposit timing
# --------------------------------------------------------------------------


def test_taint_is_carried_onto_the_attribution(labels):
    """A path carrying 40% of the money must report 40%, not 100%."""
    engine = AttributionEngine(labels)
    transfers = [
        transfer(VICTIM_PAID, DEPOSIT, "4000"),
        transfer(DEPOSIT, HOT_WALLET, "4000"),
    ]
    assessment = engine.assess(
        address=DEPOSIT, chain=Chain.TRON, depth=2,
        profile=profile(), transfers=transfers,
        value_in_usd=Decimal(4000), taint_ratio=0.4,
    )
    assert assessment.attribution.taint_ratio == pytest.approx(0.4)


def test_taint_never_exceeds_one(labels):
    """Even a nonsensical input cannot report more than all of the money."""
    engine = AttributionEngine(labels)
    assessment = engine.assess(
        address=DEPOSIT, chain=Chain.TRON, depth=1,
        profile=profile(),
        transfers=[transfer(DEPOSIT, HOT_WALLET, "100")],
        taint_ratio=1.8,
    )
    assert assessment.attribution.taint_ratio == 1.0


def test_taint_sums_across_converging_paths():
    """Two routes to one exchange carrying 30% and 40% means 70% arrived."""
    def hit(hops: int, taint: float) -> Attribution:
        return Attribution(
            vasp_name="Same", category=VaspCategory.EXCHANGE, chain=Chain.TRON,
            matched_address="x", method=AttributionMethod.DIRECT_LABEL,
            confidence=0.8, hops_from_subject=hops, taint_ratio=taint,
        )

    ranked = rank_attributions([hit(2, 0.3), hit(3, 0.4)])
    assert len(ranked) == 1
    assert ranked[0].taint_ratio == pytest.approx(0.7)


def test_merged_taint_is_capped_at_one():
    def hit(taint: float) -> Attribution:
        return Attribution(
            vasp_name="Same", category=VaspCategory.EXCHANGE, chain=Chain.TRON,
            matched_address="x", method=AttributionMethod.DIRECT_LABEL,
            confidence=0.8, hops_from_subject=1, taint_ratio=taint,
        )

    assert rank_attributions([hit(0.7), hit(0.6)])[0].taint_ratio == 1.0


def test_deposit_timestamp_comes_from_the_sweep_not_the_arrival(labels):
    """The deposit event is when funds entered the exchange, not this wallet.

    An investigator asks "is the freeze window still open?", which is answered
    by when the money reached the VASP -- not by when it reached the deposit
    address one step earlier.
    """
    engine = AttributionEngine(labels)
    arrival = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    sweep = Transfer(
        chain=Chain.TRON, tx_hash="sweep", block_time=datetime(2026, 1, 3, 14, 32, tzinfo=UTC),
        from_address=DEPOSIT, to_address=HOT_WALLET, asset_symbol="USDT",
        asset_contract=USDT, amount=Decimal(5000), amount_usd=Decimal(5000),
    )
    assessment = engine.assess(
        address=DEPOSIT, chain=Chain.TRON, depth=1,
        profile=profile(),
        transfers=[transfer(VICTIM_PAID, DEPOSIT, "5000"), sweep],
        arrived_at=arrival,
    )
    attribution = assessment.attribution
    assert attribution.last_deposit_at == datetime(2026, 1, 3, 14, 32, tzinfo=UTC)
    assert attribution.last_deposit_at != arrival


def test_direct_label_uses_arrival_time(labels):
    """With no sweep to measure, the arrival at the labelled address is it."""
    engine = AttributionEngine(labels)
    arrival = datetime(2026, 2, 5, 11, 15, tzinfo=UTC)
    assessment = engine.assess(
        address=HOT_WALLET, chain=Chain.TRON, depth=1,
        profile=profile(address=HOT_WALLET), transfers=[], arrived_at=arrival,
    )
    assert assessment.attribution.first_deposit_at == arrival
    assert assessment.attribution.last_deposit_at == arrival


def test_merged_timestamps_span_first_to_last():
    def hit(when: datetime) -> Attribution:
        return Attribution(
            vasp_name="Same", category=VaspCategory.EXCHANGE, chain=Chain.TRON,
            matched_address="x", method=AttributionMethod.DIRECT_LABEL,
            confidence=0.9, hops_from_subject=1,
            first_deposit_at=when, last_deposit_at=when,
        )

    early = datetime(2026, 1, 1, tzinfo=UTC)
    late = datetime(2026, 3, 1, tzinfo=UTC)
    merged = rank_attributions([hit(late), hit(early)])[0]
    assert merged.first_deposit_at == early
    assert merged.last_deposit_at == late


def test_subject_as_labelled_entity_has_no_deposit_time(labels):
    """Nothing was deposited into the subject along a traced path."""
    store = LabelStore()
    store._add(
        VaspRecord(
            address=VICTIM_PAID, chain=Chain.TRON, name="SDN", 
            category=VaspCategory.SANCTIONED, source="OFAC SDN", confidence=1.0,
        )
    )
    assessment = AttributionEngine(store).assess(
        address=VICTIM_PAID, chain=Chain.TRON, depth=0,
        profile=profile(address=VICTIM_PAID), transfers=[],
    )
    assert assessment.attribution.first_deposit_at is None
    assert assessment.attribution.last_deposit_at is None
