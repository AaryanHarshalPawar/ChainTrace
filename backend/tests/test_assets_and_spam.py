"""Counterfeit-token detection and spam filtering.

Every counterfeit below was observed on-chain during a live probe of a single
TRON exchange wallet -- these are recorded findings, not invented examples.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.chains.spam import SpamReason, classify, partition
from app.core.assets import check_counterfeit, normalize_symbol
from app.core.chains import Chain
from app.core.models import Transfer

REAL_USDT_TRON = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
FAKE_USDT_SPACED = "TTwweAg8g611oG41LgGvERJVfo54K7au3t"  # "U S D T" / "T e t h e r"
FAKE_USDT_TELLER = "TGXjZQCTdH6ioWL3acappQEzo6sEPhCsGy"  # "USDTT" / "USDT Teller"
TREX_CONTRACT = "TMd4qawCApLgt5czSwnNrd8DEan2aBWnmE"  # "TREX" / "T-REX", legitimate


def make_transfer(
    symbol: str,
    *,
    name: str | None = None,
    contract: str | None = None,
    amount: str = "100",
    amount_usd: str | None = "100",
    counterfeit: bool = False,
    impersonates: str | None = None,
    sender: str = "TSender0000000000000000000000000000",
    recipient: str = "TRecipient00000000000000000000000000",
) -> Transfer:
    return Transfer(
        chain=Chain.TRON,
        tx_hash="0xtest",
        block_time=datetime(2026, 1, 1, tzinfo=UTC),
        from_address=sender,
        to_address=recipient,
        asset_symbol=symbol,
        asset_name=name,
        asset_contract=contract,
        amount=Decimal(amount),
        amount_usd=Decimal(amount_usd) if amount_usd is not None else None,
        is_counterfeit=counterfeit,
        impersonates=impersonates,
    )


# --------------------------------------------------------------------------
# Symbol normalisation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("U S D T", "usdt"),  # spacing
        ("U$DT", "usdt"),  # symbol homoglyph
        ("U5DT", "usdt"),  # digit homoglyph
        ("U-S-D-T", "usdt"),  # punctuation
        ("usdt", "usdt"),
        ("USDT", "usdt"),
        ("USDС", "usdc"),  # trailing char is Cyrillic es, not Latin C
    ],
)
def test_normalize_symbol_folds_evasion(raw, expected):
    """Folding is aggressive on purpose: it exists to defeat evasion.

    Digits fold to their letter lookalikes, so this is not a general-purpose
    slugifier -- advert detection uses a separate dot-preserving fold, because
    a TLD is signal that this one would destroy.
    """
    assert normalize_symbol(raw) == expected


# --------------------------------------------------------------------------
# Counterfeit detection
# --------------------------------------------------------------------------


def test_genuine_usdt_is_not_counterfeit():
    verdict = check_counterfeit(Chain.TRON, "USDT", REAL_USDT_TRON, "Tether USD")
    assert not verdict.is_counterfeit


def test_spaced_usdt_impersonation_detected():
    """Observed on-chain: symbol "U S D T", name "T e t h e r"."""
    verdict = check_counterfeit(Chain.TRON, "U S D T", FAKE_USDT_SPACED, "T e t h e r")
    assert verdict.is_counterfeit
    assert verdict.impersonates == "USDT"


def test_near_miss_usdtt_impersonation_detected():
    """Observed on-chain: "USDTT" / "USDT Teller", 16 decimals."""
    verdict = check_counterfeit(Chain.TRON, "USDTT", FAKE_USDT_TELLER, "USDT Teller")
    assert verdict.is_counterfeit
    assert verdict.impersonates == "USDT"


def test_trex_is_not_flagged_as_trx_impersonation():
    """Regression: TREX is one deletion from TRX but is a real token.

    Three-letter tickers are too dense a space for edit-distance matching, so
    fuzzy comparison is restricted to protected symbols of four characters or
    more. Without this the counterfeit list fills with false positives and the
    genuine findings get buried.
    """
    verdict = check_counterfeit(Chain.TRON, "TREX", TREX_CONTRACT, "T-REX")
    assert not verdict.is_counterfeit


def test_exact_short_symbol_at_wrong_contract_still_flagged():
    """TRX itself at an unregistered contract is unambiguously a counterfeit."""
    verdict = check_counterfeit(Chain.TRON, "TRX", "TFakeContract00000000000000000000", "Tron")
    assert verdict.is_counterfeit
    assert verdict.impersonates == "TRX"


def test_unrelated_token_is_not_counterfeit():
    verdict = check_counterfeit(Chain.TRON, "SUNDOG", "TX3bAUs7nLaZxvLfnfh2Whh2Q8ibCQAndR", "Sundog")
    assert not verdict.is_counterfeit


# --------------------------------------------------------------------------
# Spam filtering
# --------------------------------------------------------------------------


def test_counterfeit_takes_priority_over_other_verdicts():
    """A counterfeit must not be filed as mere dust -- it is evidence."""
    transfer = make_transfer(
        "U S D T", name="T e t h e r", contract=FAKE_USDT_SPACED,
        amount="0.0001", amount_usd=None, counterfeit=True, impersonates="USDT",
    )
    verdict = classify(transfer)
    assert verdict.reason == SpamReason.COUNTERFEIT
    assert verdict.is_evidentiary


@pytest.mark.parametrize("symbol", ["2580k.C0M", "tre.pw", "claim-reward", "visit-t.me"])
def test_advert_tokens_flagged(symbol):
    verdict = classify(make_transfer(symbol, name=symbol))
    assert verdict.reason == SpamReason.ADVERT_TOKEN


def test_homoglyph_advert_caught():
    """"C0M" with a zero must still read as ".com"."""
    assert classify(make_transfer("2580k.C0M")).reason == SpamReason.ADVERT_TOKEN


def test_unvalued_token_flagged():
    verdict = classify(make_transfer("RANDOM", contract="TSomething", amount_usd=None))
    assert verdict.reason == SpamReason.UNVALUED_TOKEN


def test_dust_flagged_below_threshold():
    verdict = classify(make_transfer("USDT", contract=REAL_USDT_TRON, amount="0.5", amount_usd="0.5"))
    assert verdict.reason == SpamReason.DUST


def test_material_transfer_kept():
    verdict = classify(
        make_transfer("USDT", contract=REAL_USDT_TRON, amount="5000", amount_usd="5000")
    )
    assert not verdict.is_spam


def test_zero_value_flagged():
    assert classify(make_transfer("USDT", amount="0", amount_usd="0")).reason == SpamReason.ZERO_VALUE


def test_address_poisoning_detected():
    """Zero-value transfer from a prefix/suffix lookalike of a real counterparty."""
    genuine = "TXYZAaaaaaaaaaaaaaaaaaaaaaaaaWXYZ9"
    lookalike = "TXYZAbbbbbbbbbbbbbbbbbbbbbbbbWXYZ9"
    assert len(genuine) == len(lookalike)

    transfers = [
        # A real, material transfer establishing the genuine counterparty.
        make_transfer("USDT", contract=REAL_USDT_TRON, amount="5000",
                      amount_usd="5000", sender=genuine),
        # The poisoning attempt.
        make_transfer("USDT", contract=REAL_USDT_TRON, amount="0",
                      amount_usd="0", sender=lookalike),
    ]
    outcome = partition(transfers)
    assert len(outcome.poisoning_attempts) == 1
    assert outcome.poisoning_attempts[0][0].from_address == lookalike


def test_partition_keeps_and_flags_without_losing_anything():
    """Nothing may be silently dropped -- kept + flagged must equal input."""
    transfers = [
        make_transfer("USDT", contract=REAL_USDT_TRON, amount="1000", amount_usd="1000"),
        make_transfer("2580k.C0M"),
        make_transfer("RANDOM", amount_usd=None),
        make_transfer("USDT", contract=REAL_USDT_TRON, amount="0.1", amount_usd="0.1"),
    ]
    outcome = partition(transfers)
    assert len(outcome.kept) + outcome.flagged_count == len(transfers)
    assert len(outcome.kept) == 1
