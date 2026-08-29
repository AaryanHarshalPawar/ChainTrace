"""Chain adapter contract.

Each supported ledger implements :class:`ChainAdapter`, which hides the wild
differences between account-based chains (TRON, EVM) and UTXO chains (Bitcoin)
behind one normalised interface. Everything above this layer -- tracer,
attribution, risk -- is chain-agnostic and never sees a raw explorer payload.

Profile construction lives here, not in the subclasses, so behavioural
inference (fan-in, pass-through ratio) is computed identically on every chain.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from app.chains.http import CachedHttpClient
from app.core.chains import Chain
from app.core.models import AddressProfile, Transfer
from app.core.pricing import PriceOracle

log = logging.getLogger(__name__)


@dataclass
class AccountInfo:
    """Chain-level facts about an address, independent of its transfers."""

    address: str
    chain: Chain
    balance_native: Decimal | None = None
    created_at: datetime | None = None
    is_contract: bool = False
    exists: bool = True
    raw: dict = field(default_factory=dict)


class ChainAdapter(ABC):
    chain: ClassVar[Chain]

    def __init__(self, http: CachedHttpClient, oracle: PriceOracle) -> None:
        self.http = http
        self.oracle = oracle

    # -- subclass responsibilities ----------------------------------------

    @abstractmethod
    async def fetch_transfers(
        self, address: str, *, limit: int = 200
    ) -> list[Transfer]:
        """Return value transfers touching ``address``, newest first."""

    @abstractmethod
    async def fetch_account(self, address: str) -> AccountInfo:
        """Return chain-level facts about ``address``."""

    # -- shared behaviour --------------------------------------------------

    async def has_activity(self, address: str) -> bool:
        """Cheap probe used to disambiguate an EVM address across chains."""
        try:
            transfers = await self.fetch_transfers(address, limit=1)
            if transfers:
                return True
            account = await self.fetch_account(address)
            return account.exists and bool(account.balance_native)
        except Exception as exc:  # noqa: BLE001 - probing must never be fatal
            log.debug("activity probe failed for %s on %s: %s", address, self.chain, exc)
            return False

    def build_profile(
        self,
        address: str,
        transfers: list[Transfer],
        account: AccountInfo | None = None,
        *,
        requested_limit: int = 200,
        raw_transfer_count: int | None = None,
    ) -> AddressProfile:
        """Aggregate transfers into the behavioural profile.

        Comparison is case-insensitive because EVM addresses arrive in mixed
        EIP-55 casing from different endpoints and would otherwise be counted
        as distinct counterparties.

        ``raw_transfer_count`` is the size of the *unfiltered* fetch. Pass it
        whenever ``transfers`` has been through spam filtering: truncation is a
        property of what the API returned, not of what survived filtering, and
        getting this wrong makes a lower-bound profile look complete.
        """
        subject = address.lower()
        inbound = [t for t in transfers if t.to_address.lower() == subject]
        outbound = [t for t in transfers if t.from_address.lower() == subject]

        senders = {t.from_address.lower() for t in inbound}
        receivers = {t.to_address.lower() for t in outbound}

        received_usd = sum((t.amount_usd or Decimal(0) for t in inbound), Decimal(0))
        sent_usd = sum((t.amount_usd or Decimal(0) for t in outbound), Decimal(0))

        times = [t.block_time for t in transfers if t.block_time]
        first_seen = min(times) if times else None
        if account and account.created_at:
            first_seen = (
                min(first_seen, account.created_at) if first_seen else account.created_at
            )

        return AddressProfile(
            address=address,
            chain=self.chain,
            first_seen=first_seen,
            last_seen=max(times) if times else None,
            transfer_count=len(transfers),
            inbound_count=len(inbound),
            outbound_count=len(outbound),
            unique_senders=len(senders),
            unique_receivers=len(receivers),
            total_received_usd=received_usd,
            total_sent_usd=sent_usd,
            balance_native=account.balance_native if account else None,
            is_contract=account.is_contract if account else False,
            # A full page means the history was cut off, so every aggregate
            # above is a lower bound. Downstream heuristics must know this.
            is_truncated=(
                raw_transfer_count if raw_transfer_count is not None else len(transfers)
            )
            >= requested_limit,
        )

    async def profile(
        self, address: str, *, limit: int = 200
    ) -> tuple[AddressProfile, list[Transfer]]:
        transfers = await self.fetch_transfers(address, limit=limit)
        account = await self.fetch_account(address)
        return (
            self.build_profile(address, transfers, account, requested_limit=limit),
            transfers,
        )

    # -- helpers for subclasses -------------------------------------------

    def _value(self, contract: str | None, amount: Decimal) -> Decimal | None:
        """Value an amount. ``contract=None`` means the chain's native asset.

        Keyed on contract, never symbol -- see :mod:`app.core.assets`.
        """
        return self.oracle.value(self.chain, contract, amount).amount_usd

    @staticmethod
    def _from_raw_units(raw: str | int, decimals: int) -> Decimal:
        """Convert integer base units to a human amount without float error."""
        return Decimal(str(raw)) / (Decimal(10) ** decimals)
