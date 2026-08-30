"""Normalised domain models shared across chain adapters, tracer and API.

Design rule for this file: **every asserted conclusion carries its evidence.**
This is an evidentiary tool -- an investigator acting on an attribution may
freeze real money, so a claim with no transaction hash behind it is a bug, not
a convenience.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.core.chains import Chain


# ---------------------------------------------------------------------------
# Ledger primitives
# ---------------------------------------------------------------------------


class Transfer(BaseModel):
    """One value movement, normalised across UTXO and account-based chains.

    Bitcoin transactions move value from many inputs to many outputs at once;
    those are flattened into one Transfer per (input, output) pair so the
    tracer can treat every chain as a directed value graph.
    """

    model_config = ConfigDict(frozen=True)

    chain: Chain
    tx_hash: str
    block_time: datetime
    block_number: int | None = None
    from_address: str
    to_address: str
    asset_symbol: str
    # The on-chain token *name*. Kept separately from the symbol because
    # counterfeits hide in it -- a live probe found symbol "U S D T" carrying
    # the name "T e t h e r".
    asset_name: str | None = None
    asset_contract: str | None = None
    amount: Decimal
    # None means "could not be valued", never "worth nothing". Only assets in
    # the canonical registry are priced; see app.core.assets.
    amount_usd: Decimal | None = None
    is_native: bool = False
    # Set when the token imitates a protected asset from an unregistered
    # contract. Such a transfer is evidence of deception, not a value movement.
    is_counterfeit: bool = False
    impersonates: str | None = None
    # UTXO chains split one tx across many pairs; carries the split weight so
    # value attribution does not double-count.
    value_share: float = 1.0


class AddressProfile(BaseModel):
    """Aggregate behaviour of a single address. Drives behavioural inference."""

    address: str
    chain: Chain
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    transfer_count: int = 0
    inbound_count: int = 0
    outbound_count: int = 0
    unique_senders: int = 0
    unique_receivers: int = 0
    total_received_usd: Decimal = Decimal(0)
    total_sent_usd: Decimal = Decimal(0)
    balance_native: Decimal | None = None
    is_contract: bool = False
    # True when the adapter had to truncate at max_transfers_per_address, so
    # downstream code knows the profile is a lower bound, not a full picture.
    is_truncated: bool = False

    @property
    def fan_in(self) -> int:
        return self.unique_senders

    @property
    def fan_out(self) -> int:
        return self.unique_receivers

    @property
    def retained_ratio(self) -> float:
        """Share of inflow still held. Near 0 means a pure pass-through."""
        if self.total_received_usd <= 0:
            return 0.0
        retained = self.total_received_usd - self.total_sent_usd
        return max(0.0, min(1.0, float(retained / self.total_received_usd)))


# ---------------------------------------------------------------------------
# VASP attribution
# ---------------------------------------------------------------------------


class VaspCategory(StrEnum):
    EXCHANGE = "exchange"
    BRIDGE = "bridge"
    MIXER = "mixer"
    GAMBLING = "gambling"
    PAYMENT_PROCESSOR = "payment_processor"
    DEFI = "defi"
    SANCTIONED = "sanctioned"
    SCAM = "scam"
    UNKNOWN = "unknown"


class AttributionMethod(StrEnum):
    """How an address came to be attributed. Ordered by evidentiary strength."""

    DIRECT_LABEL = "direct_label"
    DEPOSIT_ADDRESS_HEURISTIC = "deposit_address_heuristic"
    CLUSTER_MATCH = "cluster_match"
    BEHAVIOURAL_INFERENCE = "behavioural_inference"


class KycTier(StrEnum):
    FULL_KYC = "full_kyc"
    PARTIAL_KYC = "partial_kyc"
    NO_KYC = "no_kyc"
    UNKNOWN = "unknown"


class VaspRecord(BaseModel):
    """An entry in the labelled VASP corpus.

    ``source`` and ``confidence`` are mandatory: an unsourced label is not
    admissible intelligence, and the UI greys out anything below 0.5.
    """

    address: str
    chain: Chain
    name: str
    category: VaspCategory
    source: str
    confidence: float = Field(ge=0.0, le=1.0)
    jurisdiction: str | None = None
    kyc_tier: KycTier = KycTier.UNKNOWN
    # India-specific: a VASP on the FIU-IND register can be served notice
    # directly by an Indian LEA, which changes the recommended next action.
    fiu_ind_registered: bool = False
    compliance_contact: str | None = None
    notes: str | None = None
    address_role: str | None = None  # "hot_wallet", "cold_wallet", "deposit"


class Attribution(BaseModel):
    """A ranked, evidenced claim that funds reached a named VASP."""

    vasp_name: str
    category: VaspCategory
    chain: Chain
    matched_address: str
    method: AttributionMethod
    confidence: float = Field(ge=0.0, le=1.0)
    hops_from_subject: int
    # Value that actually reached this VASP along the traced paths.
    value_usd: Decimal = Decimal(0)
    # Share of the value leaving the reported address that reached this VASP,
    # following the traced paths. 1.0 means everything; 0.68 means 68%.
    #
    # Defined precisely because an undefined "taint %" is indefensible under
    # challenge: it is the product of each hop's haircut share along the path,
    # summed where several paths converge on the same VASP. It therefore
    # answers exactly one question -- "how much of the victim-linked money
    # ended up here" -- and can never exceed 1.0.
    taint_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    # When the money actually landed at the VASP. `last_deposit_at` is the
    # decision-relevant one: it answers whether the freeze window is still
    # open. Both are None when the subject address is itself the labelled
    # entity, since nothing was deposited *into* it along a traced path.
    first_deposit_at: datetime | None = None
    last_deposit_at: datetime | None = None
    asset_breakdown: dict[str, Decimal] = Field(default_factory=dict)
    evidence_tx_hashes: list[str] = Field(default_factory=list)
    reasoning: list[str] = Field(default_factory=list)
    jurisdiction: str | None = None
    kyc_tier: KycTier = KycTier.UNKNOWN
    fiu_ind_registered: bool = False
    compliance_contact: str | None = None
    # The deposit address is what an LEA notice must actually name -- the hot
    # wallet is shared by millions of users and is useless on its own.
    deposit_address: str | None = None


# ---------------------------------------------------------------------------
# Trace graph
# ---------------------------------------------------------------------------


class NodeRole(StrEnum):
    SUBJECT = "subject"  # the victim-reported address
    INTERMEDIARY = "intermediary"  # layering hop
    VASP_DEPOSIT = "vasp_deposit"  # user-specific deposit address at a VASP
    VASP_HOT = "vasp_hot"  # exchange omnibus / hot wallet
    MIXER = "mixer"
    BRIDGE = "bridge"
    CONTRACT = "contract"
    TERMINAL = "terminal"  # funds still resting here


class TraceNode(BaseModel):
    address: str
    chain: Chain
    depth: int
    role: NodeRole = NodeRole.INTERMEDIARY
    label: str | None = None
    category: VaspCategory = VaspCategory.UNKNOWN
    value_in_usd: Decimal = Decimal(0)
    value_out_usd: Decimal = Decimal(0)
    # Share of the subject's outflow that reached this node. Lets the graph
    # view weight edges and nodes by how much of the victim's money they
    # actually carried, rather than by raw balance.
    taint_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    # When value most recently arrived here along a traced path.
    arrived_at: datetime | None = None
    profile: AddressProfile | None = None
    risk_score: float = 0.0
    # Set when the tracer stopped here for a reason other than "no outflow".
    stop_reason: str | None = None


class TraceEdge(BaseModel):
    source: str
    target: str
    chain: Chain
    asset_symbol: str
    total_amount: Decimal
    total_usd: Decimal = Decimal(0)
    transfer_count: int = 1
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    # Capped when an edge aggregates many transfers; full set stays in cache.
    tx_hashes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Risk and typology
# ---------------------------------------------------------------------------


class RiskLevel(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class RiskSignal(BaseModel):
    code: str
    title: str
    level: RiskLevel
    weight: float
    detail: str
    evidence: list[str] = Field(default_factory=list)


class FraudTypology(StrEnum):
    INVESTMENT_SCAM = "investment_scam"
    TASK_BASED_FRAUD = "task_based_fraud"
    SEXTORTION = "sextortion"
    RANSOMWARE = "ransomware"
    PHISHING = "phishing"
    DARKNET = "darknet"
    LAYERING = "layering"
    UNDETERMINED = "undetermined"


class RiskAssessment(BaseModel):
    score: float = Field(ge=0.0, le=100.0)
    level: RiskLevel
    signals: list[RiskSignal] = Field(default_factory=list)
    typology: FraudTypology = FraudTypology.UNDETERMINED
    typology_confidence: float = 0.0
    summary: str = ""


class InvestigativeAction(BaseModel):
    """A concrete next step for the investigating officer, ranked by urgency."""

    priority: int
    action: str
    rationale: str
    target: str | None = None
    deadline_hint: str | None = None


# ---------------------------------------------------------------------------
# Trace result
# ---------------------------------------------------------------------------


class TraceStats(BaseModel):
    nodes_explored: int = 0
    # Addresses that appear in the graph but could not be fetched. They stay
    # visible as unexamined nodes -- an investigator must never read a gap
    # caused by a rate limit as a dead end in the money trail.
    nodes_unreachable: int = 0
    edges_discovered: int = 0
    max_depth_reached: int = 0
    transfers_examined: int = 0
    upstream_calls: int = 0
    cache_hits: int = 0
    elapsed_seconds: float = 0.0
    truncated: bool = False
    truncation_reason: str | None = None


class TraceResult(BaseModel):
    subject_address: str
    chain: Chain
    generated_at: datetime
    nodes: list[TraceNode] = Field(default_factory=list)
    edges: list[TraceEdge] = Field(default_factory=list)
    attributions: list[Attribution] = Field(default_factory=list)
    risk: RiskAssessment | None = None
    recommended_actions: list[InvestigativeAction] = Field(default_factory=list)
    stats: TraceStats = Field(default_factory=TraceStats)
    warnings: list[str] = Field(default_factory=list)
    # Counts of transfers set aside by the spam filter, by reason, aggregated
    # across every node. Reported rather than hidden so the investigator can
    # see what was excluded from the value analysis and why.
    filtered_summary: dict[str, int] = Field(default_factory=dict)
    # Deception aimed at addresses in this graph: counterfeit tokens and
    # address-poisoning attempts. Evidence of targeting, not noise.
    deception_findings: list[str] = Field(default_factory=list)

    @property
    def primary_attribution(self) -> Attribution | None:
        """The nearest, highest-confidence VASP -- the answer the PS asks for."""
        return self.attributions[0] if self.attributions else None
