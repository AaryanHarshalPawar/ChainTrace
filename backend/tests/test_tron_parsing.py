"""TRON payload parsing, against records captured from the live API.

Every fixture here is a real TronGrid response body, trimmed but otherwise
unmodified. Parsing is tested without network access so the suite stays fast
and deterministic; the adapter's HTTP layer is exercised separately.

The two rejection cases matter most. TronGrid returns reverted transactions
and non-value contract types in the same feed as genuine transfers, and a
tracer that accepts them would put fictitious edges into an evidence graph.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.chains.http import CachedHttpClient
from app.chains.tron import TronAdapter
from app.core.chains import Chain
from app.core.pricing import PriceOracle

REAL_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


@pytest.fixture
def adapter(tmp_path):
    from app.cache.store import CacheStore

    http = CachedHttpClient(cache=CacheStore(tmp_path / "c.sqlite3"), offline=True)
    return TronAdapter(http, PriceOracle(http))


# --------------------------------------------------------------------------
# TRC20
# --------------------------------------------------------------------------

GENUINE_USDT_RECORD = {
    "transaction_id": "abc123",
    "token_info": {
        "symbol": "USDT",
        "address": REAL_USDT,
        "decimals": 6,
        "name": "Tether USD",
    },
    "block_timestamp": 1729425600000,
    "from": "TDDBTQF2ANuLYCbwUwvjyBK5V8Rk9Y6ppb",
    "to": "TMuA6YqfCeX8EhbfYEg5y7S4DqzSJireY9",
    "type": "Transfer",
    "value": "2000000",  # 6 decimals -> 2.0 USDT
}

COUNTERFEIT_RECORD = {
    "transaction_id": "def456",
    "token_info": {
        "symbol": "U S D T",
        "address": "TTwweAg8g611oG41LgGvERJVfo54K7au3t",
        "decimals": 6,
        "name": "T e t h e r",
    },
    "block_timestamp": 1729425600000,
    "from": "TScammer000000000000000000000000000",
    "to": "TMuA6YqfCeX8EhbfYEg5y7S4DqzSJireY9",
    "type": "Transfer",
    "value": "1000000000",
}

APPROVAL_RECORD = dict(GENUINE_USDT_RECORD, type="Approval", transaction_id="ghi789")


def test_genuine_usdt_parsed_and_valued(adapter):
    transfer = adapter._parse_trc20(GENUINE_USDT_RECORD)
    assert transfer is not None
    assert transfer.asset_symbol == "USDT"
    assert transfer.amount == Decimal("2")
    assert transfer.amount_usd == Decimal("2"), "registered stablecoin pegs 1:1"
    assert not transfer.is_counterfeit
    assert transfer.chain is Chain.TRON


def test_counterfeit_parsed_but_left_unvalued(adapter):
    """The critical safety property: a fake USDT must never be priced at $1."""
    transfer = adapter._parse_trc20(COUNTERFEIT_RECORD)
    assert transfer is not None
    assert transfer.is_counterfeit
    assert transfer.impersonates == "USDT"
    assert transfer.amount_usd is None, (
        "an unregistered contract must not receive the stablecoin peg, or a "
        "counterfeit could inflate a reported loss without limit"
    )


def test_approval_is_not_a_transfer(adapter):
    """An approval grants spending rights but moves nothing."""
    assert adapter._parse_trc20(APPROVAL_RECORD) is None


def test_token_decimals_are_honoured(adapter):
    """Counterfeits use odd decimals; assuming 6 would misstate the amount."""
    record = {
        **GENUINE_USDT_RECORD,
        "token_info": {**GENUINE_USDT_RECORD["token_info"], "decimals": 18},
        "value": "2" + "0" * 18,
    }
    transfer = adapter._parse_trc20(record)
    assert transfer is not None
    assert transfer.amount == Decimal("2")


def test_malformed_record_rejected_not_raised(adapter):
    assert adapter._parse_trc20({"type": "Transfer", "token_info": {}}) is None


# --------------------------------------------------------------------------
# Native TRX
# --------------------------------------------------------------------------

SUCCESSFUL_TRANSFER = {
    "txID": "8a6f5e4e521bff0741b13b4accc68bd9e421e683eae2c9ce6b10903373dfb49b",
    "blockNumber": 75657646,
    "block_timestamp": 1757632686000,
    "ret": [{"contractRet": "SUCCESS", "fee": 268000}],
    "raw_data": {
        "contract": [
            {
                "type": "TransferContract",
                "parameter": {
                    "value": {
                        "amount": 8333333,
                        "owner_address": "41238ce912167c221a781e04ea43f77fc8b8bc0f33",
                        "to_address": "4182dd6b9966724ae2fdc79b416c7588da67ff1b35",
                    }
                },
            }
        ]
    },
}

REVERTED_TRANSFER = {
    **SUCCESSFUL_TRANSFER,
    "txID": "reverted",
    "ret": [{"contractRet": "REVERT", "fee": 0}],
}

RESOURCE_DELEGATION = {
    "txID": "048598f1c5caba4e7808191865f565690ee3497caf419f43cf1e2e44d2a92c06",
    "blockNumber": 84954514,
    "block_timestamp": 1785534306000,
    "ret": [{"contractRet": "SUCCESS", "fee": 0}],
    "raw_data": {
        "contract": [
            {
                "type": "UnDelegateResourceContract",
                "parameter": {
                    "value": {
                        "balance": 6771359014,
                        "resource": "ENERGY",
                        "receiver_address": "4182dd6b9966724ae2fdc79b416c7588da67ff1b35",
                        "owner_address": "412c995748799f92862c5d3db379b768d6dbbcaf5c",
                    }
                },
            }
        ]
    },
}


def test_native_transfer_parsed_with_addresses_converted(adapter):
    transfer = adapter._parse_native(SUCCESSFUL_TRANSFER)
    assert transfer is not None
    assert transfer.asset_symbol == "TRX"
    assert transfer.is_native
    assert transfer.amount == Decimal("8.333333")  # SUN has 6 decimals
    # 41-prefixed hex must come back as base58.
    assert transfer.from_address.startswith("T")
    assert transfer.to_address.startswith("T")
    assert len(transfer.from_address) == 34


def test_reverted_transaction_rejected(adapter):
    """A reverted tx appears in history but moved no funds."""
    assert adapter._parse_native(REVERTED_TRANSFER) is None


def test_resource_delegation_is_not_a_value_transfer(adapter):
    """A live probe found 70% of one wallet's feed was resource delegation."""
    assert adapter._parse_native(RESOURCE_DELEGATION) is None


def test_native_trx_valued_from_oracle(adapter):
    """Native TRX has no contract address; it must still be priced."""
    adapter.oracle._quotes["TRX"] = Decimal("0.30")
    transfer = adapter._parse_native(SUCCESSFUL_TRANSFER)
    assert transfer is not None
    assert transfer.amount_usd == Decimal("8.333333") * Decimal("0.30")


# --------------------------------------------------------------------------
# Profile construction
# --------------------------------------------------------------------------


def test_truncation_reflects_raw_fetch_not_filtered_result(adapter):
    """Regression: filtering shrinks the list, but truncation is about the API.

    Computing truncation from the post-filter count made a heavily-truncated
    profile of a busy exchange wallet report itself as complete, which would
    let downstream fan-in heuristics treat a lower bound as a full picture.
    """
    kept = [adapter._parse_trc20(GENUINE_USDT_RECORD)]
    profile = adapter.build_profile(
        "TMuA6YqfCeX8EhbfYEg5y7S4DqzSJireY9",
        kept,
        None,
        requested_limit=60,
        raw_transfer_count=60,
    )
    assert profile.is_truncated

    profile_complete = adapter.build_profile(
        "TMuA6YqfCeX8EhbfYEg5y7S4DqzSJireY9",
        kept,
        None,
        requested_limit=60,
        raw_transfer_count=12,
    )
    assert not profile_complete.is_truncated
