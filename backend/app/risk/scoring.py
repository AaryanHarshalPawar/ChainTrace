"""Risk scoring, typology classification and investigative recommendations.

**Why rules and not a model.** The problem statement asks for ML-assisted risk
detection, and that is the right long-term answer -- but a supervised model
needs labelled outcomes, and no labelled corpus of Indian cyber-fraud wallets
exists to train on yet. A classifier trained on invented labels would produce
confident, unfalsifiable, unexplainable output, which is worse than useless in
an evidentiary setting: an investigating officer has to justify a freeze to a
magistrate, and "the model said 87" does not survive that.

So scoring is a transparent weighted-signal model. Every signal names itself,
carries its weight, and cites the evidence that fired it. The path to ML is
real and deliberately left open: once NCRP case outcomes provide labels, these
same signals become the feature vector, and the weights below become learned
rather than assigned. That is a data problem, not an architecture problem.

Scores are capped at 100 and the level bands are fixed, so two investigators
reading two reports mean the same thing by "high".
"""

from __future__ import annotations

from decimal import Decimal

from app.core.models import (
    Attribution,
    AttributionMethod,
    FraudTypology,
    InvestigativeAction,
    NodeRole,
    RiskAssessment,
    RiskLevel,
    RiskSignal,
    TraceResult,
    VaspCategory,
)

# Weights are contributions to a 0-100 score. Sanctions exposure alone is
# enough to reach CRITICAL (>= 75), because it changes the legal posture of
# the case regardless of anything else in the graph -- so this weight is tied
# to that band and must be raised with it if the bands ever move.
W_SANCTIONED = 75.0
W_MIXER = 45.0
W_RAPID_LAYERING = 20.0
W_DEEP_LAYERING = 15.0
W_MANY_VICTIMS = 25.0
W_COUNTERFEIT = 10.0
W_POISONING = 10.0
W_BURNER = 12.0
W_LARGE_VALUE = 15.0
W_REACHED_VASP = 10.0

LARGE_VALUE_USD = Decimal(100_000)
MANY_VICTIMS_THRESHOLD = 20


def _level_for(score: float) -> RiskLevel:
    if score >= 75:
        return RiskLevel.CRITICAL
    if score >= 50:
        return RiskLevel.HIGH
    if score >= 25:
        return RiskLevel.MEDIUM
    if score > 0:
        return RiskLevel.LOW
    return RiskLevel.INFO


def assess(result: TraceResult) -> RiskAssessment:
    signals: list[RiskSignal] = []
    subject = next((n for n in result.nodes if n.depth == 0), None)

    # -- sanctions ---------------------------------------------------------
    sanctioned = [a for a in result.attributions if a.category is VaspCategory.SANCTIONED]
    for hit in sanctioned[:3]:
        signals.append(
            RiskSignal(
                code="SANCTIONS_EXPOSURE",
                title=f"Sanctioned entity {hit.hops_from_subject} hop(s) away",
                level=RiskLevel.CRITICAL,
                weight=W_SANCTIONED,
                detail=(
                    f"Funds reach {hit.vasp_name}, an OFAC-designated entity, "
                    f"{hit.hops_from_subject} hop(s) from the reported address. "
                    f"This carries sanctions-compliance consequences for any "
                    f"VASP in the path and should be escalated immediately."
                ),
                evidence=hit.evidence_tx_hashes[:5] or [hit.matched_address],
            )
        )

    # -- mixers ------------------------------------------------------------
    mixers = [n for n in result.nodes if n.role is NodeRole.MIXER]
    if mixers:
        signals.append(
            RiskSignal(
                code="MIXER_USE",
                title="Funds routed through a mixing service",
                level=RiskLevel.CRITICAL,
                weight=W_MIXER,
                detail=(
                    f"{len(mixers)} mixer address(es) in the flow. Mixing is a "
                    f"deliberate obfuscation step and severely degrades onward "
                    f"traceability; act on pre-mixer attributions."
                ),
                evidence=[n.address for n in mixers[:5]],
            )
        )

    # -- layering ----------------------------------------------------------
    pass_through = [
        n
        for n in result.nodes
        if n.role is NodeRole.INTERMEDIARY and n.depth > 0
    ]
    if len(pass_through) >= 2:
        signals.append(
            RiskSignal(
                code="LAYERING_CHAIN",
                title=f"{len(pass_through)} sequential pass-through wallets",
                level=RiskLevel.HIGH,
                weight=W_RAPID_LAYERING,
                detail=(
                    f"Value moves through {len(pass_through)} wallets that retain "
                    f"almost nothing. Chained pass-through addresses are a "
                    f"layering structure, not incidental wallet use."
                ),
                evidence=[n.address for n in pass_through[:5]],
            )
        )

    if result.stats.max_depth_reached >= 4:
        signals.append(
            RiskSignal(
                code="DEEP_CHAIN",
                title=f"Trail extends at least {result.stats.max_depth_reached} hops",
                level=RiskLevel.MEDIUM,
                weight=W_DEEP_LAYERING,
                detail=(
                    "Depth of this order indicates a practised laundering "
                    "pipeline rather than an opportunistic transfer."
                ),
            )
        )

    # -- multiple victims --------------------------------------------------
    if subject and subject.profile and subject.profile.unique_senders >= MANY_VICTIMS_THRESHOLD:
        senders = subject.profile.unique_senders
        signals.append(
            RiskSignal(
                code="MULTI_VICTIM_COLLECTION",
                title=f"Collection wallet: {senders}+ distinct payers",
                level=RiskLevel.HIGH,
                weight=W_MANY_VICTIMS,
                detail=(
                    f"The reported address received from {senders} distinct "
                    f"addresses. This is a collection point serving many "
                    f"victims, so other complaints very likely reference the "
                    f"same wallet -- worth correlating across NCRP before "
                    f"treating this as a single-victim case."
                    + (
                        " The observation window was truncated, so the true "
                        "count is higher."
                        if subject.profile.is_truncated
                        else ""
                    )
                ),
            )
        )

    # -- deception ---------------------------------------------------------
    counterfeit_count = result.filtered_summary.get("counterfeit_token", 0)
    if counterfeit_count:
        signals.append(
            RiskSignal(
                code="COUNTERFEIT_TOKENS",
                title=f"{counterfeit_count} counterfeit token transfer(s)",
                level=RiskLevel.MEDIUM,
                weight=W_COUNTERFEIT,
                detail=(
                    "Tokens impersonating USDT or another major asset were sent "
                    "to addresses in this graph. These are excluded from value "
                    "totals, and their presence indicates an address operating "
                    "inside a scam ecosystem."
                ),
                evidence=result.deception_findings[:3],
            )
        )

    poisoning_count = result.filtered_summary.get("address_poisoning", 0)
    if poisoning_count:
        signals.append(
            RiskSignal(
                code="ADDRESS_POISONING",
                title=f"{poisoning_count} address-poisoning attempt(s)",
                level=RiskLevel.MEDIUM,
                weight=W_POISONING,
                detail=(
                    "Zero-value transfers from vanity lookalikes of genuine "
                    "counterparties. Someone is actively trying to induce a "
                    "misdirected payment from this address."
                ),
                evidence=result.deception_findings[:3],
            )
        )

    # -- burner ------------------------------------------------------------
    if subject and subject.profile and subject.profile.first_seen and subject.profile.last_seen:
        lifetime = subject.profile.last_seen - subject.profile.first_seen
        if lifetime.days <= 30 and subject.profile.transfer_count >= 3:
            signals.append(
                RiskSignal(
                    code="SHORT_LIVED_WALLET",
                    title=f"Wallet active for only {lifetime.days} day(s)",
                    level=RiskLevel.MEDIUM,
                    weight=W_BURNER,
                    detail=(
                        "A short, dense activity window is characteristic of a "
                        "burner wallet created for one campaign. Funds move out "
                        "quickly, so the window for a freeze is narrow."
                    ),
                )
            )

    # -- value -------------------------------------------------------------
    total_value = max(
        (a.value_usd for a in result.attributions), default=Decimal(0)
    )
    if total_value >= LARGE_VALUE_USD:
        signals.append(
            RiskSignal(
                code="HIGH_VALUE",
                title=f"Traced value ${total_value:,.0f}",
                level=RiskLevel.HIGH,
                weight=W_LARGE_VALUE,
                detail="Value at this scale warrants priority handling.",
            )
        )

    # -- reachable VASP (an opportunity, not a threat) ---------------------
    reachable = [
        a
        for a in result.attributions
        if a.category in (VaspCategory.EXCHANGE, VaspCategory.PAYMENT_PROCESSOR)
    ]
    if reachable:
        nearest = min(reachable, key=lambda a: a.hops_from_subject)
        signals.append(
            RiskSignal(
                code="VASP_REACHABLE",
                title=f"VASP identified {nearest.hops_from_subject} hop(s) away",
                level=RiskLevel.INFO,
                weight=W_REACHED_VASP,
                detail=(
                    f"Funds reach {nearest.vasp_name}. A preservation request "
                    f"is actionable now -- this is the recovery opportunity in "
                    f"this case."
                ),
                evidence=nearest.evidence_tx_hashes[:5],
            )
        )

    score = min(100.0, sum(s.weight for s in signals))
    typology, confidence = classify_typology(result, signals)

    return RiskAssessment(
        score=round(score, 1),
        level=_level_for(score),
        signals=sorted(signals, key=lambda s: -s.weight),
        typology=typology,
        typology_confidence=confidence,
        summary=_summarise(result, score, typology),
    )


def classify_typology(
    result: TraceResult, signals: list[RiskSignal]
) -> tuple[FraudTypology, float]:
    """Infer the fraud pattern from graph shape alone.

    On-chain structure constrains the typology but rarely determines it: a
    collection wallet looks much the same whether it served an investment scam
    or a task-based fraud. Confidence is kept deliberately low and the
    complaint narrative remains the authority -- this is a prompt for the
    investigator, never a conclusion.
    """
    codes = {s.code for s in signals}
    subject = next((n for n in result.nodes if n.depth == 0), None)
    profile = subject.profile if subject else None

    if "SANCTIONS_EXPOSURE" in codes:
        return FraudTypology.DARKNET, 0.4

    if profile and profile.unique_senders >= MANY_VICTIMS_THRESHOLD:
        # Many payers into one wallet, then a sweep out. Distinguishing an
        # investment scam from task-based fraud needs the payment sizes, which
        # the complaint has and the chain does not.
        if profile.total_received_usd >= LARGE_VALUE_USD:
            return FraudTypology.INVESTMENT_SCAM, 0.45
        return FraudTypology.TASK_BASED_FRAUD, 0.4

    if "LAYERING_CHAIN" in codes or "DEEP_CHAIN" in codes:
        return FraudTypology.LAYERING, 0.5

    return FraudTypology.UNDETERMINED, 0.0


def _summarise(result: TraceResult, score: float, typology: FraudTypology) -> str:
    primary = result.primary_attribution
    level = _level_for(score).value.upper()
    if primary is None:
        return (
            f"{level} risk (score {score:.0f}). No VASP could be attributed "
            f"within {result.stats.max_depth_reached} hop(s). Funds may still "
            f"be resting on-chain, or the trail extends beyond the search "
            f"budget -- consider re-running with a higher hop limit."
        )
    return (
        f"{level} risk (score {score:.0f}). Funds traced to {primary.vasp_name} "
        f"{primary.hops_from_subject} hop(s) from the reported address, "
        f"attributed by {primary.method.value.replace('_', ' ')} at "
        f"{primary.confidence:.0%} confidence. Indicative typology: "
        f"{typology.value.replace('_', ' ')}."
    )


def recommend(result: TraceResult, risk: RiskAssessment) -> list[InvestigativeAction]:
    """Turn the analysis into ordered next steps for the investigating officer.

    Ordered by how quickly the opportunity closes, not by severity. Crypto
    moves in minutes: a preservation request that goes out today can still
    land on funds, whereas one sent next week almost certainly cannot.
    """
    actions: list[InvestigativeAction] = []
    priority = 1

    for attribution in result.attributions[:3]:
        if attribution.category not in (
            VaspCategory.EXCHANGE,
            VaspCategory.PAYMENT_PROCESSOR,
        ):
            continue

        target = attribution.deposit_address or attribution.matched_address
        if attribution.method is AttributionMethod.BEHAVIOURAL_INFERENCE:
            actions.append(
                InvestigativeAction(
                    priority=priority,
                    action=(
                        f"Confirm the operator of {target} before serving any "
                        f"notice, then request preservation."
                    ),
                    rationale=(
                        "This address was identified as exchange-class from its "
                        "behaviour alone and carries no verified label. Serving "
                        "the wrong VASP wastes the freeze window."
                    ),
                    target=target,
                    deadline_hint="within 24 hours",
                )
            )
        else:
            registered = (
                " The VASP is FIU-IND registered, so an Indian LEA can serve "
                "notice directly."
                if attribution.fiu_ind_registered
                else " Confirm the VASP's FIU-IND registration status; if it is "
                "not registered, route via MLAT or the SAHYOG portal."
            )
            actions.append(
                InvestigativeAction(
                    priority=priority,
                    action=(
                        f"Serve a preservation and KYC request on "
                        f"{attribution.vasp_name} naming deposit address {target}."
                    ),
                    rationale=(
                        f"A deposit address maps to a single KYC'd account, "
                        f"unlike the shared omnibus wallet."
                        + registered
                    ),
                    target=target,
                    deadline_hint="immediately",
                )
            )
        priority += 1

    if any(s.code == "SANCTIONS_EXPOSURE" for s in risk.signals):
        actions.append(
            InvestigativeAction(
                priority=priority,
                action="Escalate to the sanctions/FIU-IND desk.",
                rationale=(
                    "The flow touches an OFAC-designated entity, which changes "
                    "the reporting obligations on this case."
                ),
                deadline_hint="immediately",
            )
        )
        priority += 1

    if any(s.code == "MULTI_VICTIM_COLLECTION" for s in risk.signals):
        actions.append(
            InvestigativeAction(
                priority=priority,
                action=(
                    "Search NCRP for other complaints naming this address and "
                    "consider consolidating them."
                ),
                rationale=(
                    "The wallet received from many distinct payers, so this is "
                    "very likely one of several complaints against a single "
                    "operation. Consolidated cases carry more weight with a VASP."
                ),
                target=result.subject_address,
                deadline_hint="within 48 hours",
            )
        )
        priority += 1

    holders = [n for n in result.nodes if n.role is NodeRole.TERMINAL and n.value_in_usd > 0]
    if holders:
        richest = max(holders, key=lambda n: n.value_in_usd)
        actions.append(
            InvestigativeAction(
                priority=priority,
                action=f"Monitor {richest.address} for onward movement.",
                rationale=(
                    f"Approximately ${richest.value_in_usd:,.0f} appears to be "
                    f"still resting here rather than cashed out, so recovery "
                    f"remains possible if movement is caught early."
                ),
                target=richest.address,
                deadline_hint="set an alert now",
            )
        )
        priority += 1

    if result.stats.truncated:
        actions.append(
            InvestigativeAction(
                priority=priority,
                action="Re-run the trace with a higher hop and node budget.",
                rationale=(
                    f"The search stopped early: {result.stats.truncation_reason}. "
                    f"Attributions beyond that point have not been evaluated."
                ),
            )
        )

    return actions
