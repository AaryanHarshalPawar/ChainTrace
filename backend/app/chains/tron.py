"""TRON adapter (TronGrid).

TRON is chain number one for this system: USDT-TRC20 is the dominant rail for
Indian investment-scam and task-fraud proceeds, and TronGrid serves a usable
free tier without a key.

Schema notes, verified against the live API rather than documentation:

* ``/v1/accounts/{a}/transactions/trc20`` returns ``from``/``to`` already in
  base58, ``value`` as a raw-unit *string*, and ``token_info.decimals``.
  ``type`` is ``Transfer`` or ``Approval`` -- an approval moves nothing and is
  excluded.
* ``/v1/accounts/{a}/transactions`` returns *all* contract types. A live probe
  of a busy wallet came back 70% resource delegation. Only
  ``TransferContract`` moves native TRX, and only when
  ``ret[0].contractRet == "SUCCESS"`` -- a reverted transaction that still
  appears in history would otherwise be traced as a real fund movement.
  Addresses here are 41-prefixed hex and need converting.
* ``/v1/accounts/{a}`` carries ``type: "Contract"`` for contracts; EOAs omit
  ``type`` and carry ``create_time``.

Known gap: TRC10 (``TransferAssetContract``) is not yet parsed. It is rare in
fraud casework and is recorded as a warning rather than silently ignored.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar

from app.chains.base import AccountInfo, ChainAdapter
from app.config import settings
from app.core.assets import check_counterfeit
from app.core.chains import Chain, tron_hex_to_base58
from app.core.models import Transfer

log = logging.getLogger(__name__)

SUN_PER_TRX = 6  # decimals
_PAGE_LIMIT = 200  # TronGrid hard cap per page


def _ms_to_dt(ms: int | None) -> datetime | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


class TronAdapter(ChainAdapter):
    chain: ClassVar[Chain] = Chain.TRON

    @property
    def _headers(self) -> dict[str, str]:
        # Keyless works; a key simply raises the rate ceiling.
        if settings.trongrid_api_key:
            return {"TRON-PRO-API-KEY": settings.trongrid_api_key}
        return {}

    # -- fetching ----------------------------------------------------------

    async def fetch_transfers(
        self, address: str, *, limit: int = 200
    ) -> list[Transfer]:
        trc20 = await self._fetch_trc20(address, limit=limit)
        native = await self._fetch_native(address, limit=limit)
        combined = trc20 + native
        combined.sort(key=lambda t: t.block_time, reverse=True)
        return combined[:limit]

    async def _fetch_trc20(self, address: str, *, limit: int) -> list[Transfer]:
        url = f"{settings.trongrid_base_url}/v1/accounts/{address}/transactions/trc20"
        transfers: list[Transfer] = []
        fingerprint: str | None = None
        # The page size must stay constant across a paginated run. A
        # fingerprint is only valid for the limit it was issued under, and
        # shrinking it to `limit - len(transfers)` made TronGrid reject the
        # follow-up page with 400 Bad Request. Over-fetch and trim instead.
        page_size = min(_PAGE_LIMIT, limit)

        while len(transfers) < limit:
            params: dict[str, Any] = {
                "limit": page_size,
                "order_by": "block_timestamp,desc",
            }
            if fingerprint:
                params["fingerprint"] = fingerprint

            body, _ = await self.http.get_json(
                url,
                params=params,
                headers=self._headers,
                chain=self.chain.value,
                kind="trc20_transfers",
            )
            records = (body or {}).get("data") or []
            for record in records:
                parsed = self._parse_trc20(record)
                if parsed is not None:
                    transfers.append(parsed)

            fingerprint = ((body or {}).get("meta") or {}).get("fingerprint")
            # No fingerprint, or a short page, means we reached the end.
            if not fingerprint or len(records) < page_size:
                break

        return transfers[:limit]

    def _parse_trc20(self, record: dict[str, Any]) -> Transfer | None:
        if record.get("type") != "Transfer":
            return None  # Approval etc. move no value

        token = record.get("token_info") or {}
        symbol = token.get("symbol") or "UNKNOWN"
        name = token.get("name")
        contract = token.get("address")
        try:
            # Counterfeits use absurd decimals (a probe found USDT Teller at
            # 16) so this must come from the token, never assumed.
            decimals = int(token.get("decimals", 6))
            amount = self._from_raw_units(record.get("value", "0"), decimals)
        except (ValueError, TypeError, ArithmeticError) as exc:
            log.debug("unparseable TRC20 value in %s: %s", record.get("transaction_id"), exc)
            return None

        block_time = _ms_to_dt(record.get("block_timestamp"))
        sender, recipient = record.get("from"), record.get("to")
        if not (block_time and sender and recipient):
            return None

        counterfeit = check_counterfeit(self.chain, symbol, contract, name)

        return Transfer(
            chain=self.chain,
            tx_hash=record.get("transaction_id", ""),
            block_time=block_time,
            from_address=sender,
            to_address=recipient,
            asset_symbol=symbol,
            asset_name=name,
            asset_contract=contract,
            amount=amount,
            amount_usd=self._value(contract, amount),
            is_native=False,
            is_counterfeit=counterfeit.is_counterfeit,
            impersonates=counterfeit.impersonates,
        )

    async def _fetch_native(self, address: str, *, limit: int) -> list[Transfer]:
        url = f"{settings.trongrid_base_url}/v1/accounts/{address}/transactions"
        body, _ = await self.http.get_json(
            url,
            params={
                "limit": min(_PAGE_LIMIT, limit),
                "order_by": "block_timestamp,desc",
            },
            headers=self._headers,
            chain=self.chain.value,
            kind="native_transactions",
        )

        transfers: list[Transfer] = []
        for record in (body or {}).get("data") or []:
            parsed = self._parse_native(record)
            if parsed is not None:
                transfers.append(parsed)
        return transfers

    def _parse_native(self, record: dict[str, Any]) -> Transfer | None:
        # A reverted transaction is still returned by the API. Tracing one as
        # a real movement would put a fictitious edge in an evidence graph.
        rets = record.get("ret") or []
        if rets and rets[0].get("contractRet") != "SUCCESS":
            return None

        contracts = (record.get("raw_data") or {}).get("contract") or []
        if not contracts or contracts[0].get("type") != "TransferContract":
            return None

        value = ((contracts[0].get("parameter") or {}).get("value")) or {}
        owner_hex, to_hex = value.get("owner_address"), value.get("to_address")
        block_time = _ms_to_dt(record.get("block_timestamp"))
        if not (owner_hex and to_hex and block_time):
            return None

        try:
            sender = tron_hex_to_base58(owner_hex)
            recipient = tron_hex_to_base58(to_hex)
        except (ValueError, TypeError) as exc:
            log.debug("bad TRON hex address in %s: %s", record.get("txID"), exc)
            return None

        amount = self._from_raw_units(value.get("amount", 0), SUN_PER_TRX)
        return Transfer(
            chain=self.chain,
            tx_hash=record.get("txID", ""),
            block_time=block_time,
            block_number=record.get("blockNumber"),
            from_address=sender,
            to_address=recipient,
            asset_symbol="TRX",
            asset_name="TRON",
            amount=amount,
            amount_usd=self._value(None, amount),
            is_native=True,
        )

    async def fetch_account(self, address: str) -> AccountInfo:
        url = f"{settings.trongrid_base_url}/v1/accounts/{address}"
        body, _ = await self.http.get_json(
            url,
            headers=self._headers,
            chain=self.chain.value,
            kind="account",
        )
        records = (body or {}).get("data") or []
        if not records:
            # Never activated: valid format, zero on-chain footprint.
            return AccountInfo(
                address=address, chain=self.chain, exists=False, balance_native=Decimal(0)
            )

        record = records[0]
        return AccountInfo(
            address=address,
            chain=self.chain,
            balance_native=self._from_raw_units(record.get("balance", 0), SUN_PER_TRX),
            created_at=_ms_to_dt(record.get("create_time")),
            is_contract=record.get("type") == "Contract",
            exists=True,
            raw={
                "trc20_balances": record.get("trc20", []),
                "account_name": record.get("account_name"),
            },
        )
