"""Dust, spam, counterfeit and address-poisoning detection.

TRON and the cheaper EVM chains are saturated with worthless token transfers
pushed at high-profile addresses. A single live probe of one TRON exchange
wallet turned up every category below, which is why this filter exists at all:

* **Advertisement tokens** -- the "symbol" is a URL. Seen: ``2580k.C0M``,
  ``tre.pw``. Note the zero in ``C0M``: matching must fold homoglyphs.
* **Counterfeits** -- ``U S D T`` (name ``T e t h e r``) and ``USDTT``
  (``USDT Teller``, 16 decimals) sitting beside genuine Tether.
* **Unvalued tokens** -- anything outside the canonical registry. Immaterial
  for value tracing, since fraud proceeds move as USDT, TRX, ETH or BTC.
* **Address poisoning** -- a zero-value transfer from a vanity lookalike of a
  real counterparty, hoping the victim copies the wrong address next time.

Nothing here deletes data. Transfers are *flagged*; the tracer declines to
traverse flagged edges but still counts them, and the report states how many
were set aside and why. Counterfeits and poisoning attempts are promoted into
risk signals -- they are deliberate deception, and therefore evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.core.models import Transfer

# Applied to homoglyph-folded text, so "C0M" matches "com".
_ADVERT_PATTERN = re.compile(
    r"(https?|www\.|\.com|\.io|\.xyz|\.net|\.org|\.vip|\.top|\.pw|\.cc"
    r"|\.info|\.site|\.club|\.link|\.app"
    r"|claim|airdrop|visit|reward|bonus|voucher|giveaway|freemint"
    r"|telegram|tme|whatsapp)",
    re.IGNORECASE,
)

DEFAULT_DUST_USD = Decimal("1.00")


class SpamReason:
    ZERO_VALUE = "zero_value"
    ADVERT_TOKEN = "advert_token_name"
    COUNTERFEIT = "counterfeit_token"
    UNVALUED_TOKEN = "unvalued_token"
    DUST = "dust_below_threshold"
    POISONING = "address_poisoning"


# Reasons that are themselves evidence of targeting, not mere noise.
EVIDENTIARY_REASONS = frozenset({SpamReason.COUNTERFEIT, SpamReason.POISONING})


@dataclass(frozen=True)
class SpamVerdict:
    is_spam: bool
    reason: str | None = None
    detail: str = ""

    @property
    def is_evidentiary(self) -> bool:
        return self.reason in EVIDENTIARY_REASONS


# Folds homoglyphs while preserving dots, which carry the TLD signal that
# distinguishes "2580k.C0M" from an ordinary ticker.
_HOMOGLYPH_KEEP_DOTS = str.maketrans(
    {"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s", "·": ".", "•": "."}
)


def _advert_hit(text: str | None) -> bool:
    if not text:
        return False
    return bool(_ADVERT_PATTERN.search(text.lower().translate(_HOMOGLYPH_KEEP_DOTS)))


def _looks_like_poisoning(transfer: Transfer, known_counterparties: set[str]) -> bool:
    """A near-lookalike of an address the subject genuinely transacts with.

    Poisoning addresses are ground out until they share a prefix and suffix
    with the target. Matching 5 leading and 5 trailing characters on an
    otherwise different address is far beyond coincidence.
    """
    candidate = transfer.from_address
    if candidate in known_counterparties:
        return False
    for known in known_counterparties:
        if known == candidate or len(known) != len(candidate):
            continue
        if candidate[:5] == known[:5] and candidate[-5:] == known[-5:]:
            return True
    return False


def classify(
    transfer: Transfer,
    *,
    dust_threshold_usd: Decimal = DEFAULT_DUST_USD,
    known_counterparties: set[str] | None = None,
) -> SpamVerdict:
    # Counterfeits first: the most serious finding, and it must not be
    # shadowed by a dust or unvalued verdict.
    if transfer.is_counterfeit:
        return SpamVerdict(
            True,
            SpamReason.COUNTERFEIT,
            f"{transfer.asset_symbol!r} ({transfer.asset_name!r}) impersonates "
            f"{transfer.impersonates} from unregistered contract "
            f"{transfer.asset_contract}",
        )

    if transfer.amount == 0:
        if known_counterparties and _looks_like_poisoning(
            transfer, known_counterparties
        ):
            return SpamVerdict(
                True,
                SpamReason.POISONING,
                f"zero-value transfer from {transfer.from_address}, a lookalike "
                f"of a genuine counterparty -- address-poisoning attempt",
            )
        return SpamVerdict(True, SpamReason.ZERO_VALUE, "zero-value transfer")

    if _advert_hit(transfer.asset_symbol) or _advert_hit(transfer.asset_name):
        return SpamVerdict(
            True,
            SpamReason.ADVERT_TOKEN,
            f"token {transfer.asset_symbol!r} / {transfer.asset_name!r} "
            f"is an advertisement",
        )

    if transfer.amount_usd is None:
        return SpamVerdict(
            True,
            SpamReason.UNVALUED_TOKEN,
            f"{transfer.asset_symbol!r} at {transfer.asset_contract} is outside "
            f"the canonical asset registry and cannot be valued",
        )

    if transfer.amount_usd < dust_threshold_usd:
        return SpamVerdict(
            True,
            SpamReason.DUST,
            f"${transfer.amount_usd:.4f} is below the ${dust_threshold_usd} "
            f"materiality threshold",
        )

    return SpamVerdict(False)


@dataclass
class FilterOutcome:
    kept: list[Transfer]
    flagged: list[tuple[Transfer, SpamVerdict]]

    @property
    def flagged_count(self) -> int:
        return len(self.flagged)

    def reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _, verdict in self.flagged:
            if verdict.reason:
                counts[verdict.reason] = counts.get(verdict.reason, 0) + 1
        return counts

    def by_reason(self, reason: str) -> list[tuple[Transfer, SpamVerdict]]:
        return [(t, v) for t, v in self.flagged if v.reason == reason]

    @property
    def counterfeits(self) -> list[tuple[Transfer, SpamVerdict]]:
        return self.by_reason(SpamReason.COUNTERFEIT)

    @property
    def poisoning_attempts(self) -> list[tuple[Transfer, SpamVerdict]]:
        return self.by_reason(SpamReason.POISONING)


def partition(
    transfers: list[Transfer],
    *,
    dust_threshold_usd: Decimal = DEFAULT_DUST_USD,
) -> FilterOutcome:
    """Split transfers into material and flagged sets."""
    # Counterparties from genuinely valued transfers form the baseline against
    # which poisoning lookalikes are judged.
    material = [
        t
        for t in transfers
        if t.amount > 0
        and not t.is_counterfeit
        and (t.amount_usd or Decimal(0)) >= dust_threshold_usd
    ]
    genuine = {t.from_address for t in material} | {t.to_address for t in material}

    kept: list[Transfer] = []
    flagged: list[tuple[Transfer, SpamVerdict]] = []
    for transfer in transfers:
        verdict = classify(
            transfer,
            dust_threshold_usd=dust_threshold_usd,
            known_counterparties=genuine,
        )
        if verdict.is_spam:
            flagged.append((transfer, verdict))
        else:
            kept.append(transfer)
    return FilterOutcome(kept=kept, flagged=flagged)
