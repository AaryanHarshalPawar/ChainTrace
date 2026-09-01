"""Bitcoin UTXO flattening.

Turning a many-to-many transaction into directed value edges involves choices
that are easy to get subtly wrong and hard to notice afterwards, because the
output still *looks* like a plausible graph. Each case below locks down one of
those choices.

Payload shapes are taken from live mempool.space responses.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.cache.store import CacheStore
from app.chains.bitcoin import BitcoinAdapter
from app.chains.http import CachedHttpClient
from app.core.pricing import PriceOracle

ALICE = "1AliceAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "1BobBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
CAROL = "1CarolCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
BTC = 100_000_000  # satoshis


@pytest.fixture
def adapter(tmp_path):
    http = CachedHttpClient(cache=CacheStore(tmp_path / "c.sqlite3"), offline=True)
    oracle = PriceOracle(http)
    oracle._quotes["BTC"] = Decimal("100000")  # fixed price for exact assertions
    return BitcoinAdapter(http, oracle)


def tx(vin, vout, *, confirmed=True, txid="tx1", time=1700000000):
    return {
        "txid": txid,
        "status": {"confirmed": confirmed, "block_height": 800000, "block_time": time},
        "vin": vin,
        "vout": vout,
        "fee": 1000,
    }


def vin(address: str | None, value: int, coinbase: bool = False):
    if coinbase:
        return {"is_coinbase": True, "prevout": None}
    return {
        "is_coinbase": False,
        "prevout": {"scriptpubkey_address": address, "value": value},
    }


def vout(address: str | None, value: int):
    out = {"value": value}
    if address:
        out["scriptpubkey_address"] = address
    return out


# --------------------------------------------------------------------------
# The core split
# --------------------------------------------------------------------------


def test_simple_payment(adapter):
    """One input, one output: the whole amount moves."""
    transfers = adapter._flatten(tx([vin(ALICE, BTC)], [vout(BOB, BTC)]), ALICE)
    assert len(transfers) == 1
    t = transfers[0]
    assert t.from_address == ALICE
    assert t.to_address == BOB
    assert t.amount == Decimal(1)
    assert t.amount_usd == Decimal(100_000)


def test_change_output_is_not_a_payment(adapter):
    """The remainder returning to the sender must not become a hop.

    Bitcoin returns unspent input value to its owner as an ordinary-looking
    output. Treating it as a payment invents a transfer that never happened and
    doubles the apparent flow.
    """
    transfers = adapter._flatten(
        tx([vin(ALICE, BTC)], [vout(BOB, BTC // 4), vout(ALICE, BTC * 3 // 4)]),
        ALICE,
    )
    assert len(transfers) == 1, "only the payment to Bob is a real transfer"
    assert transfers[0].to_address == BOB
    assert transfers[0].amount == Decimal("0.25")


def test_value_split_proportionally_across_inputs(adapter):
    """Supplying 30% of the input value credits 30% of each payment out."""
    transfers = adapter._flatten(
        tx([vin(ALICE, 30 * BTC), vin(CAROL, 70 * BTC)], [vout(BOB, 100 * BTC)]),
        ALICE,
    )
    assert len(transfers) == 1
    assert transfers[0].amount == Decimal(30)


def test_receiving_side_splits_across_senders(adapter):
    """An address receiving from a joint spend is credited per contributor."""
    transfers = adapter._flatten(
        tx([vin(ALICE, 25 * BTC), vin(CAROL, 75 * BTC)], [vout(BOB, 100 * BTC)]),
        BOB,
    )
    assert len(transfers) == 2
    by_sender = {t.from_address: t.amount for t in transfers}
    assert by_sender[ALICE] == Decimal(25)
    assert by_sender[CAROL] == Decimal(75)


def test_traced_value_never_exceeds_the_transaction(adapter):
    """The property that makes the number safe to put in a report."""
    transfers = adapter._flatten(
        tx(
            [vin(ALICE, 40 * BTC), vin(CAROL, 60 * BTC)],
            [vout(BOB, 50 * BTC), vout("1Dave", 50 * BTC)],
        ),
        ALICE,
    )
    assert sum(t.amount for t in transfers) <= Decimal(40)


# --------------------------------------------------------------------------
# Payload hazards found in live data
# --------------------------------------------------------------------------


def test_op_return_output_is_skipped(adapter):
    """A live probe returned an output with no address and zero value."""
    transfers = adapter._flatten(
        tx([vin(ALICE, BTC)], [vout(None, 0), vout(BOB, BTC)]), ALICE
    )
    assert len(transfers) == 1
    assert transfers[0].to_address == BOB


def test_coinbase_input_has_no_sender_to_trace(adapter):
    """Newly minted coins come from nobody; there is nothing upstream."""
    assert adapter._flatten(tx([vin(None, 0, coinbase=True)], [vout(BOB, BTC)]), BOB) == []


def test_unconfirmed_transaction_is_excluded(adapter):
    """An unconfirmed tx can still be replaced or dropped from the chain."""
    assert adapter._flatten(
        tx([vin(ALICE, BTC)], [vout(BOB, BTC)], confirmed=False), ALICE
    ) == []


def test_wide_transaction_is_capped(adapter):
    """One probed transaction had 127 outputs; the long tail is dust."""
    outs = [vout(f"1Recipient{i:04d}", BTC // 200) for i in range(120)]
    transfers = adapter._flatten(tx([vin(ALICE, BTC)], outs), ALICE)
    assert 0 < len(transfers) <= 25


def test_address_uninvolved_in_the_transaction_gets_nothing(adapter):
    assert adapter._flatten(tx([vin(ALICE, BTC)], [vout(BOB, BTC)]), CAROL) == []


def test_dust_below_one_satoshi_is_dropped(adapter):
    """A rounded-to-zero share must not become a zero-value edge."""
    transfers = adapter._flatten(
        tx([vin(ALICE, 1), vin(CAROL, 10 * BTC)], [vout(BOB, 10 * BTC)]), ALICE
    )
    assert all(t.amount > 0 for t in transfers)


def test_native_btc_is_priced_from_the_registry(adapter):
    """BTC has no contract address but must still be valued."""
    t = adapter._flatten(tx([vin(ALICE, BTC // 2)], [vout(BOB, BTC // 2)]), ALICE)[0]
    assert t.asset_symbol == "BTC"
    assert t.is_native
    assert t.asset_contract is None
    assert t.amount_usd == Decimal(50_000)
