"""Canonical asset registry and counterfeit-token detection.

Motivation, straight from a live probe of a TRON exchange wallet. Among the
tokens sent to it were:

===================  ==================  ====================================
symbol               name                contract
===================  ==================  ====================================
``USDT``             Tether USD          ``TR7NHq...Lj6t``  (genuine)
``U S D T``          T e t h e r         ``TTwweA...au3t``  (counterfeit)
``USDTT``            USDT Teller         ``TGXjZQ...CsGy``  (counterfeit)
``2580k.C0M``        2580k               ``TZ19jz...vFdi``  (advertisement)
===================  ==================  ====================================

Two consequences:

1. **Never value a token by its symbol.** A counterfeit that calls itself USDT
   would otherwise be priced at $1 and inflate a reported loss by orders of
   magnitude. Valuation is keyed on ``(chain, contract address)`` only. An
   unrecognised contract is left *unvalued* rather than guessed -- in an
   evidentiary tool the safe failure is "unknown", never "wrong".
2. **A counterfeit is itself a finding.** A token engineered to read as USDT
   is deliberate deception, so it is surfaced as a risk signal rather than
   quietly dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.chains import Chain


class AssetClass(StrEnum):
    NATIVE = "native"
    STABLECOIN = "stablecoin"
    MAJOR_TOKEN = "major_token"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CanonicalAsset:
    symbol: str
    name: str
    chain: Chain
    contract: str | None  # None for a chain's native asset
    decimals: int
    asset_class: AssetClass


def _k(chain: Chain, contract: str | None) -> str:
    return f"{chain.value}:{(contract or 'native').lower()}"


# Canonical contracts. Entries here are the *only* ones eligible for the
# stablecoin peg. `scripts/verify_assets.py` re-checks each against its chain.
_CANONICAL: list[CanonicalAsset] = [
    # -- TRON -------------------------------------------------------------
    CanonicalAsset("USDT", "Tether USD", Chain.TRON,
                   "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", 6, AssetClass.STABLECOIN),
    CanonicalAsset("USDC", "USD Coin", Chain.TRON,
                   "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8", 6, AssetClass.STABLECOIN),
    CanonicalAsset("TRX", "TRON", Chain.TRON, None, 6, AssetClass.NATIVE),
    # -- Ethereum ---------------------------------------------------------
    CanonicalAsset("USDT", "Tether USD", Chain.ETHEREUM,
                   "0xdac17f958d2ee523a2206206994597c13d831ec7", 6, AssetClass.STABLECOIN),
    CanonicalAsset("USDC", "USD Coin", Chain.ETHEREUM,
                   "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 6, AssetClass.STABLECOIN),
    CanonicalAsset("DAI", "Dai Stablecoin", Chain.ETHEREUM,
                   "0x6b175474e89094c44da98b954eedeac495271d0f", 18, AssetClass.STABLECOIN),
    CanonicalAsset("ETH", "Ether", Chain.ETHEREUM, None, 18, AssetClass.NATIVE),
    # -- BNB Smart Chain --------------------------------------------------
    CanonicalAsset("USDT", "Binance-Peg USD", Chain.BSC,
                   "0x55d398326f99059ff775485246999027b3197955", 18, AssetClass.STABLECOIN),
    CanonicalAsset("USDC", "Binance-Peg USDC", Chain.BSC,
                   "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d", 18, AssetClass.STABLECOIN),
    CanonicalAsset("BNB", "BNB", Chain.BSC, None, 18, AssetClass.NATIVE),
    # -- Polygon ----------------------------------------------------------
    CanonicalAsset("USDT", "Tether USD", Chain.POLYGON,
                   "0xc2132d05d31c914a87c6611c10748aeb04b58e8f", 6, AssetClass.STABLECOIN),
    CanonicalAsset("USDC", "USD Coin", Chain.POLYGON,
                   "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359", 6, AssetClass.STABLECOIN),
    CanonicalAsset("USDC.e", "USD Coin (bridged)", Chain.POLYGON,
                   "0x2791bca1f2de4661ed88a30c99a7a9449aa84174", 6, AssetClass.STABLECOIN),
    CanonicalAsset("POL", "Polygon Ecosystem Token", Chain.POLYGON, None, 18,
                   AssetClass.NATIVE),
    # -- Arbitrum ---------------------------------------------------------
    CanonicalAsset("USDT", "Tether USD", Chain.ARBITRUM,
                   "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9", 6, AssetClass.STABLECOIN),
    CanonicalAsset("USDC", "USD Coin", Chain.ARBITRUM,
                   "0xaf88d065e77c8cc2239327c5edb3a432268e5831", 6, AssetClass.STABLECOIN),
    CanonicalAsset("ETH", "Ether", Chain.ARBITRUM, None, 18, AssetClass.NATIVE),
    # -- Bitcoin ----------------------------------------------------------
    CanonicalAsset("BTC", "Bitcoin", Chain.BITCOIN, None, 8, AssetClass.NATIVE),
]

REGISTRY: dict[str, CanonicalAsset] = {_k(a.chain, a.contract): a for a in _CANONICAL}

# Symbols worth impersonating. A token that resolves to one of these but sits
# at an unregistered contract is a counterfeit.
PROTECTED_SYMBOLS = frozenset({"usdt", "usdc", "dai", "busd", "weth", "wbtc", "trx", "eth", "btc", "bnb"})


def lookup(chain: Chain, contract: str | None) -> CanonicalAsset | None:
    return REGISTRY.get(_k(chain, contract))


def is_canonical(chain: Chain, contract: str | None) -> bool:
    return _k(chain, contract) in REGISTRY


# ---------------------------------------------------------------------------
# Counterfeit detection
# ---------------------------------------------------------------------------

# Characters substituted to defeat naive string matching.
_HOMOGLYPHS = str.maketrans(
    {
        "0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b",
        "$": "s", "·": ".", "•": ".", "|": "l", "!": "i", "@": "a",
        "а": "a", "е": "e", "о": "o", "р": "p",  # Cyrillic
        "с": "c", "х": "x", "і": "i",
    }
)


def normalize_symbol(raw: str) -> str:
    """Fold a token symbol to its deceptive intent.

    ``"U S D T"`` and ``"U$DT"`` both fold to ``"usdt"``.
    """
    folded = (raw or "").strip().lower().translate(_HOMOGLYPHS)
    return "".join(ch for ch in folded if ch.isalnum())


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return len(a) or len(b)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
    return previous[-1]


@dataclass(frozen=True)
class CounterfeitVerdict:
    is_counterfeit: bool
    impersonates: str | None = None
    detail: str = ""


def check_counterfeit(
    chain: Chain, symbol: str, contract: str | None, name: str | None = None
) -> CounterfeitVerdict:
    """Decide whether a token is masquerading as a protected asset."""
    if contract is not None and is_canonical(chain, contract):
        return CounterfeitVerdict(False)  # registered: genuine by definition

    for candidate in (symbol, name):
        normalized = normalize_symbol(candidate or "")
        if not normalized:
            continue

        if normalized in PROTECTED_SYMBOLS:
            return CounterfeitVerdict(
                True,
                normalized.upper(),
                f"token presents as {normalized.upper()} but contract "
                f"{contract} is not the canonical {chain.value} contract",
            )

        # Near-misses such as USDTT -> USDT.
        #
        # Fuzzy matching is restricted to protected symbols of 4+ characters.
        # Three-letter tickers sit in far too dense a space for an edit
        # distance of 1 to mean anything: T-REX is one deletion from TRX, and
        # flagging every such coincidence would bury the real counterfeits in
        # noise. Short symbols must therefore match exactly (after homoglyph
        # folding), which still catches "T R X" and "TRX" at a fake contract.
        if len(normalized) >= 4:
            for protected in PROTECTED_SYMBOLS:
                if len(protected) >= 4 and _levenshtein(normalized, protected) == 1:
                    return CounterfeitVerdict(
                        True,
                        protected.upper(),
                        f"symbol {symbol!r} is one character from "
                        f"{protected.upper()} and sits at unregistered "
                        f"contract {contract}",
                    )

    if normalize_symbol(name or "") == "tether":
        return CounterfeitVerdict(
            True, "USDT", f"token name {name!r} imitates Tether at {contract}"
        )

    return CounterfeitVerdict(False)
