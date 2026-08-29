"""Asset valuation, keyed on contract address rather than symbol.

Symbols are attacker-controlled. A live probe of a TRON exchange wallet found
tokens presenting as ``U S D T`` and ``USDTT`` alongside genuine Tether, so
pricing anything by its symbol would let a counterfeit inflate a reported loss
without limit. Valuation therefore accepts only ``(chain, contract)`` pairs
present in :mod:`app.core.assets`.

Three confidence tiers, each surfaced in the report:

* **Pegged** -- a registered stablecoin, valued 1:1. Exact, and it covers most
  Indian cyber-fraud proceeds, which move as USDT-TRC20.
* **Live spot** -- registered native assets, quoted from CoinGecko, cached
  hourly.
* **Static estimate** -- offline fallback. Order-of-magnitude only, and
  labelled as such wherever it appears.

Anything unregistered is returned unvalued. These are *spot* prices, so valuing
a two-year-old ransomware payment at today's BTC price is wrong; the report
states that caveat rather than hiding it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.chains.http import CachedHttpClient, UpstreamError
from app.core.assets import AssetClass, lookup
from app.core.chains import Chain

log = logging.getLogger(__name__)

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

_COINGECKO_IDS = {
    "TRX": "tron",
    "ETH": "ethereum",
    "BTC": "bitcoin",
    "BNB": "binancecoin",
    "POL": "polygon-ecosystem-token",
}

# Used only when CoinGecko is unreachable and no cached quote exists. Flagged
# as an estimate everywhere it surfaces; not accurate spot prices.
_STATIC_FALLBACK: dict[str, Decimal] = {
    "TRX": Decimal("0.30"),
    "ETH": Decimal("3000"),
    "BTC": Decimal("90000"),
    "BNB": Decimal("600"),
    "POL": Decimal("0.40"),
}


class PriceSource(StrEnum):
    STABLECOIN_PEG = "stablecoin_peg"
    LIVE_SPOT = "live_spot"
    STATIC_ESTIMATE = "static_estimate"
    UNREGISTERED = "unregistered_asset"


@dataclass(frozen=True)
class Valuation:
    amount_usd: Decimal | None
    source: PriceSource
    unit_price_usd: Decimal | None = None

    @property
    def is_exact(self) -> bool:
        return self.source is PriceSource.STABLECOIN_PEG

    @property
    def is_estimate(self) -> bool:
        return self.source is PriceSource.STATIC_ESTIMATE


class PriceOracle:
    def __init__(self, http: CachedHttpClient) -> None:
        self._http = http
        self._quotes: dict[str, Decimal] = {}
        self._loaded = False

    async def warm(self) -> None:
        """Best-effort load of live spot prices. Never raises."""
        if self._loaded:
            return
        self._loaded = True
        try:
            body, _ = await self._http.get_json(
                COINGECKO_URL,
                params={
                    "ids": ",".join(sorted(_COINGECKO_IDS.values())),
                    "vs_currencies": "usd",
                },
                ttl=3600,
                kind="price",
            )
        except (UpstreamError, OSError, ValueError) as exc:
            log.warning("price oracle unavailable, using static estimates: %s", exc)
            return

        for symbol, cg_id in _COINGECKO_IDS.items():
            usd = ((body or {}).get(cg_id) or {}).get("usd")
            if usd is not None:
                self._quotes[symbol] = Decimal(str(usd))
        log.info("price oracle loaded %d live quotes", len(self._quotes))

    def value(
        self, chain: Chain, contract: str | None, amount: Decimal
    ) -> Valuation:
        """Value ``amount`` of the asset at ``(chain, contract)``.

        ``contract=None`` means the chain's native asset.
        """
        asset = lookup(chain, contract)
        if asset is None:
            # Unregistered contract: no valuation. The tracer still follows the
            # edge, it simply cannot rank it by value.
            return Valuation(None, PriceSource.UNREGISTERED)

        if asset.asset_class is AssetClass.STABLECOIN:
            return Valuation(amount, PriceSource.STABLECOIN_PEG, Decimal(1))

        symbol = asset.symbol.upper()
        if symbol in self._quotes:
            price = self._quotes[symbol]
            return Valuation(amount * price, PriceSource.LIVE_SPOT, price)

        if symbol in _STATIC_FALLBACK:
            price = _STATIC_FALLBACK[symbol]
            return Valuation(amount * price, PriceSource.STATIC_ESTIMATE, price)

        return Valuation(None, PriceSource.UNREGISTERED)

    @property
    def has_live_quotes(self) -> bool:
        return bool(self._quotes)

    @property
    def quotes(self) -> dict[str, Decimal]:
        return dict(self._quotes)
