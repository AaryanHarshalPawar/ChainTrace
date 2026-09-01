"""Bitcoin adapter (mempool.space).

Bitcoin is the second chain for this system: ransomware, sextortion and
darknet cases -- three of the fraud types named in the problem statement --
settle in BTC, and 536 of the 944 OFAC-sanctioned addresses in the corpus are
Bitcoin. mempool.space serves a generous keyless API.

**Bitcoin is not an account ledger, and that changes everything above it.**
There are no balances being debited and credited. A transaction consumes whole
previous outputs (inputs) and creates new ones (outputs), often many-to-many.
So a single transaction is not one edge in a value graph -- it is a bundle of
them, and turning it into edges requires choices that are stated here rather
than buried:

* **Value is split proportionally.** When an address supplies 30% of a
  transaction's input value, it is credited with 30% of each payment out. The
  alternative -- attributing every output to every input in full -- would
  multiply the traced total far beyond the money that actually moved.
* **Change outputs are excluded.** Bitcoin returns the unspent remainder of an
  input to its owner, and that output is indistinguishable from a payment
  unless you look for it. Following change as though it were a payment invents
  a hop that never happened. Any output paying an address that also appears
  among the inputs is treated as change.
* **Coinbase inputs are skipped.** Newly minted coins have no sender, so there
  is nothing upstream to trace to.
* **Wide transactions are capped.** A single probe found a transaction with
  127 outputs; expanding those in full would swamp the graph with dust. The
  largest inputs and outputs by value are kept and the rest recorded as
  truncated.

Schema notes verified against the live API:

* ``/address/{a}`` returns ``chain_stats`` in satoshis; balance is
  ``funded_txo_sum - spent_txo_sum``.
* ``/address/{a}/txs`` returns 50 transactions per page, newest first.
* An output can have **no address at all** (``op_return`` and other
  non-standard scripts). Those carry no recipient and are skipped.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar

from app.chains.base import AccountInfo, ChainAdapter
from app.config import settings
from app.core.chains import Chain
from app.core.models import Transfer

log = logging.getLogger(__name__)

SATOSHIS = 8  # decimals
_PAGE = 50  # mempool.space page size

# Per transaction, how many inputs/outputs to expand. Beyond this the long
# tail is dust that would bury the material flow.
MAX_INPUTS = 25
MAX_OUTPUTS = 25


def _dt(seconds: int | None) -> datetime | None:
    if not seconds:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC)


class BitcoinAdapter(ChainAdapter):
    chain: ClassVar[Chain] = Chain.BITCOIN

    # -- fetching ----------------------------------------------------------

    async def fetch_transfers(
        self, address: str, *, limit: int = 200
    ) -> list[Transfer]:
        transfers: list[Transfer] = []
        last_txid: str | None = None

        while len(transfers) < limit:
            url = f"{settings.mempool_base_url}/address/{address}/txs"
            if last_txid:
                url = f"{url}/chain/{last_txid}"

            body, _ = await self.http.get_json(
                url, chain=self.chain.value, kind="btc_txs"
            )
            txs = body or []
            if not isinstance(txs, list) or not txs:
                break

            for tx in txs:
                transfers.extend(self._flatten(tx, address))

            last_txid = txs[-1].get("txid")
            if len(txs) < _PAGE or not last_txid:
                break

        transfers.sort(key=lambda t: t.block_time, reverse=True)
        return transfers[:limit]

    async def fetch_account(self, address: str) -> AccountInfo:
        body, _ = await self.http.get_json(
            f"{settings.mempool_base_url}/address/{address}",
            chain=self.chain.value,
            kind="btc_address",
        )
        stats = (body or {}).get("chain_stats") or {}
        funded = int(stats.get("funded_txo_sum", 0))
        spent = int(stats.get("spent_txo_sum", 0))
        tx_count = int(stats.get("tx_count", 0))

        return AccountInfo(
            address=address,
            chain=self.chain,
            balance_native=self._from_raw_units(funded - spent, SATOSHIS),
            # Bitcoin has no accounts to create, so "first seen" is only
            # knowable from transaction history, which the profile supplies.
            created_at=None,
            is_contract=False,  # no contract concept on Bitcoin
            exists=tx_count > 0,
            raw={"tx_count": tx_count, "funded_sat": funded, "spent_sat": spent},
        )

    # -- UTXO flattening ---------------------------------------------------

    def _flatten(self, tx: dict[str, Any], subject: str) -> list[Transfer]:
        """Turn one transaction into the value edges that touch ``subject``."""
        status = tx.get("status") or {}
        block_time = _dt(status.get("block_time"))
        if not status.get("confirmed") or not block_time:
            # Unconfirmed transactions can still be replaced or dropped; an
            # evidence graph should not contain one.
            return []

        txid = tx.get("txid", "")
        block_height = status.get("block_height")

        # -- inputs, keyed by the address that supplied them ---------------
        inputs: dict[str, int] = {}
        for vin in tx.get("vin") or []:
            if vin.get("is_coinbase"):
                continue  # freshly minted; no sender exists to trace back to
            prevout = vin.get("prevout") or {}
            addr = prevout.get("scriptpubkey_address")
            if not addr:
                continue  # non-standard input script, no attributable owner
            inputs[addr] = inputs.get(addr, 0) + int(prevout.get("value", 0))

        total_in = sum(inputs.values())
        if total_in <= 0:
            return []

        # -- outputs, minus change and unspendable scripts -----------------
        outputs: list[tuple[str, int]] = []
        for vout in tx.get("vout") or []:
            addr = vout.get("scriptpubkey_address")
            value = int(vout.get("value", 0))
            # OP_RETURN and other non-standard scripts have no recipient.
            if not addr or value <= 0:
                continue
            outputs.append((addr, value))

        # Anything paying back to an input owner is change, not a payment.
        payments = [(a, v) for a, v in outputs if a not in inputs]
        received_by_subject = sum(v for a, v in outputs if a == subject)

        transfers: list[Transfer] = []

        # -- subject is spending ------------------------------------------
        if subject in inputs:
            share = inputs[subject] / total_in
            top = sorted(payments, key=lambda p: -p[1])[:MAX_OUTPUTS]
            for addr, value in top:
                amount = self._from_raw_units(int(value * share), SATOSHIS)
                if amount <= 0:
                    continue
                transfers.append(
                    self._transfer(txid, block_time, block_height, subject, addr, amount, share)
                )

        # -- subject is receiving -----------------------------------------
        # Only when it is not also an input: if it is, the output back to it is
        # change, already excluded above, and counting it would fabricate a
        # self-payment.
        elif received_by_subject > 0:
            top = sorted(inputs.items(), key=lambda kv: -kv[1])[:MAX_INPUTS]
            for addr, value in top:
                share = value / total_in
                amount = self._from_raw_units(
                    int(received_by_subject * share), SATOSHIS
                )
                if amount <= 0:
                    continue
                transfers.append(
                    self._transfer(txid, block_time, block_height, addr, subject, amount, share)
                )

        return transfers

    def _transfer(
        self,
        txid: str,
        block_time: datetime,
        block_height: int | None,
        sender: str,
        recipient: str,
        amount: Decimal,
        share: float,
    ) -> Transfer:
        return Transfer(
            chain=self.chain,
            tx_hash=txid,
            block_time=block_time,
            block_number=block_height,
            from_address=sender,
            to_address=recipient,
            asset_symbol="BTC",
            asset_name="Bitcoin",
            asset_contract=None,  # native asset; priced via the registry
            amount=amount,
            amount_usd=self._value(None, amount),
            is_native=True,
            value_share=share,
        )
