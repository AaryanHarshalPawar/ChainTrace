"""Chain registry and address-format detection.

A victim reports a bare string. Before anything can be traced we must decide
which ledger it belongs to. Format alone is decisive for TRON and Bitcoin, but
an ``0x``-prefixed EVM address is valid on Ethereum, BSC, Polygon and every
other EVM chain simultaneously -- so detection returns *candidates* and the
resolver probes for on-chain activity to pick the live one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

import base58


class Chain(StrEnum):
    TRON = "tron"
    ETHEREUM = "ethereum"
    BSC = "bsc"
    POLYGON = "polygon"
    ARBITRUM = "arbitrum"
    BITCOIN = "bitcoin"


class AddressFamily(StrEnum):
    """Address encodings. Several chains can share one family."""

    TRON_BASE58 = "tron_base58"
    EVM_HEX = "evm_hex"
    BITCOIN = "bitcoin"


@dataclass(frozen=True)
class ChainSpec:
    chain: Chain
    family: AddressFamily
    native_symbol: str
    decimals: int
    display_name: str
    # Etherscan V2 chain id; None for non-EVM chains.
    evm_chain_id: int | None = None
    explorer_tx_url: str = ""
    explorer_address_url: str = ""


CHAIN_SPECS: dict[Chain, ChainSpec] = {
    Chain.TRON: ChainSpec(
        chain=Chain.TRON,
        family=AddressFamily.TRON_BASE58,
        native_symbol="TRX",
        decimals=6,
        display_name="TRON",
        explorer_tx_url="https://tronscan.org/#/transaction/{}",
        explorer_address_url="https://tronscan.org/#/address/{}",
    ),
    Chain.ETHEREUM: ChainSpec(
        chain=Chain.ETHEREUM,
        family=AddressFamily.EVM_HEX,
        native_symbol="ETH",
        decimals=18,
        display_name="Ethereum",
        evm_chain_id=1,
        explorer_tx_url="https://etherscan.io/tx/{}",
        explorer_address_url="https://etherscan.io/address/{}",
    ),
    Chain.BSC: ChainSpec(
        chain=Chain.BSC,
        family=AddressFamily.EVM_HEX,
        native_symbol="BNB",
        decimals=18,
        display_name="BNB Smart Chain",
        evm_chain_id=56,
        explorer_tx_url="https://bscscan.com/tx/{}",
        explorer_address_url="https://bscscan.com/address/{}",
    ),
    Chain.POLYGON: ChainSpec(
        chain=Chain.POLYGON,
        family=AddressFamily.EVM_HEX,
        native_symbol="POL",
        decimals=18,
        display_name="Polygon",
        evm_chain_id=137,
        explorer_tx_url="https://polygonscan.com/tx/{}",
        explorer_address_url="https://polygonscan.com/address/{}",
    ),
    Chain.ARBITRUM: ChainSpec(
        chain=Chain.ARBITRUM,
        family=AddressFamily.EVM_HEX,
        native_symbol="ETH",
        decimals=18,
        display_name="Arbitrum One",
        evm_chain_id=42161,
        explorer_tx_url="https://arbiscan.io/tx/{}",
        explorer_address_url="https://arbiscan.io/address/{}",
    ),
    Chain.BITCOIN: ChainSpec(
        chain=Chain.BITCOIN,
        family=AddressFamily.BITCOIN,
        native_symbol="BTC",
        decimals=8,
        display_name="Bitcoin",
        explorer_tx_url="https://mempool.space/tx/{}",
        explorer_address_url="https://mempool.space/address/{}",
    ),
}

# EVM detection is inherently ambiguous; probe these in order of how often
# Indian cyber-fraud proceeds actually land on them.
EVM_PROBE_ORDER: tuple[Chain, ...] = (
    Chain.ETHEREUM,
    Chain.BSC,
    Chain.POLYGON,
    Chain.ARBITRUM,
)


# --------------------------------------------------------------------------
# Format validation
# --------------------------------------------------------------------------

_TRON_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BTC_BASE58_RE = re.compile(r"^[13][1-9A-HJ-NP-Za-km-z]{25,34}$")
_BTC_BECH32_RE = re.compile(r"^bc1[02-9ac-hj-np-z]{11,71}$")

_TRON_ADDRESS_PREFIX = 0x41
_BTC_VERSION_BYTES = {0x00, 0x05}  # P2PKH ("1..."), P2SH ("3...")

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3


def _bech32_polymod(values: list[int]) -> int:
    generator = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i in range(5):
            chk ^= generator[i] if ((top >> i) & 1) else 0
    return chk


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def is_valid_bech32_btc(address: str) -> bool:
    """Verify a BIP-173 (segwit v0) or BIP-350 (bech32m, taproot) address."""
    if address.lower() != address and address.upper() != address:
        return False  # mixed case is forbidden by BIP-173
    address = address.lower()
    pos = address.rfind("1")
    if pos < 1 or pos + 7 > len(address) or len(address) > 90:
        return False
    hrp, data_part = address[:pos], address[pos + 1 :]
    if hrp != "bc":
        return False
    try:
        data = [_BECH32_CHARSET.index(c) for c in data_part]
    except ValueError:
        return False
    checksum = _bech32_polymod(_bech32_hrp_expand(hrp) + data)
    witness_version = data[0]
    # v0 must use bech32, v1+ (taproot) must use bech32m.
    expected = _BECH32_CONST if witness_version == 0 else _BECH32M_CONST
    return checksum == expected


def is_valid_tron(address: str) -> bool:
    if not _TRON_RE.match(address):
        return False
    try:
        raw = base58.b58decode_check(address)
    except ValueError:
        return False
    return len(raw) == 21 and raw[0] == _TRON_ADDRESS_PREFIX


def is_valid_evm(address: str) -> bool:
    # Deliberately no EIP-55 checksum enforcement: victims copy addresses out
    # of chat screenshots and case files, where casing is routinely mangled.
    return bool(_EVM_RE.match(address))


def is_valid_bitcoin(address: str) -> bool:
    if _BTC_BASE58_RE.match(address):
        try:
            raw = base58.b58decode_check(address)
        except ValueError:
            return False
        return len(raw) == 21 and raw[0] in _BTC_VERSION_BYTES
    if _BTC_BECH32_RE.match(address.lower()):
        return is_valid_bech32_btc(address)
    return False


@dataclass(frozen=True)
class DetectionResult:
    """Outcome of format detection on a raw, victim-supplied string."""

    input_address: str
    normalized: str
    family: AddressFamily | None
    candidates: tuple[Chain, ...]
    is_valid: bool
    reason: str = ""

    @property
    def is_ambiguous(self) -> bool:
        return len(self.candidates) > 1


# Wrappers that routinely arrive glued to addresses in complaint free-text.
_URI_PREFIXES = ("tron:", "ethereum:", "bitcoin:", "ether:", "bnb:", "tether:")
_SURROUNDING_JUNK = "\"'<>()[]{},;: \t\r\n"


def detect(raw: str) -> DetectionResult:
    """Classify a raw address string into its chain candidates."""
    address = (raw or "").strip()
    for prefix in _URI_PREFIXES:
        if address.lower().startswith(prefix):
            address = address[len(prefix) :]
            break
    address = address.strip(_SURROUNDING_JUNK)

    if not address:
        return DetectionResult(raw, "", None, (), False, "empty input")

    if is_valid_tron(address):
        return DetectionResult(
            raw, address, AddressFamily.TRON_BASE58, (Chain.TRON,), True
        )

    if is_valid_evm(address):
        return DetectionResult(
            raw,
            address.lower(),
            AddressFamily.EVM_HEX,
            EVM_PROBE_ORDER,
            True,
            "EVM address is valid on every EVM chain; probing for activity",
        )

    if is_valid_bitcoin(address):
        normalized = address.lower() if address.lower().startswith("bc1") else address
        return DetectionResult(
            raw, normalized, AddressFamily.BITCOIN, (Chain.BITCOIN,), True
        )

    # Give the investigator a usable reason rather than a bare rejection.
    if _TRON_RE.match(address):
        reason = (
            "looks like a TRON address but the base58 checksum fails "
            "(likely a transcription error in the complaint)"
        )
    elif address.startswith("0x"):
        reason = f"looks like an EVM address but is {len(address)} chars, expected 42"
    elif _BTC_BASE58_RE.match(address) or address.lower().startswith("bc1"):
        reason = (
            "looks like a Bitcoin address but the checksum fails "
            "(likely a transcription error in the complaint)"
        )
    else:
        reason = "does not match any supported address format (TRON, EVM, Bitcoin)"
    return DetectionResult(raw, address, None, (), False, reason)


def spec(chain: Chain) -> ChainSpec:
    return CHAIN_SPECS[chain]


def tron_hex_to_base58(hex_address: str) -> str:
    """Convert TRON's internal 41-prefixed hex form to the T... base58 form."""
    cleaned = hex_address.lower().removeprefix("0x")
    if len(cleaned) == 40:  # EVM-style, missing the 0x41 network prefix
        cleaned = "41" + cleaned
    return base58.b58encode_check(bytes.fromhex(cleaned)).decode()
