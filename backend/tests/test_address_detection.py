"""Address format detection.

Positive cases use real mainnet addresses and the official BIP-173 / BIP-350
test vectors. Negative cases are those same addresses with a single character
corrupted -- exactly the failure mode of an address transcribed by hand from a
complaint form, and the one case a checksum exists to catch.
"""

from __future__ import annotations

import pytest

from app.core.chains import Chain, detect, tron_hex_to_base58

VALID = [
    ("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", Chain.TRON, "USDT-TRC20 contract"),
    ("TMuA6YqfCeX8EhbfYEg5y7S4DqzSJireY9", Chain.TRON, "TRON exchange wallet"),
    ("0xdAC17F958D2ee523a2206206994597C13D831ec7", Chain.ETHEREUM, "USDT ERC20"),
    ("0x28C6c06298d514Db089934071355E5743bf21d60", Chain.ETHEREUM, "ETH exchange wallet"),
    ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", Chain.BITCOIN, "genesis coinbase"),
    ("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy", Chain.BITCOIN, "P2SH"),
    ("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", Chain.BITCOIN, "BIP-173 P2WPKH"),
    (
        "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3",
        Chain.BITCOIN,
        "BIP-173 P2WSH",
    ),
]

INVALID = [
    ("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6X", "TRON, corrupted last char"),
    ("0xdAC17F958D2ee523a2206206994597C13D831e", "EVM, one char short"),
    ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb", "BTC base58, corrupted"),
    ("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5", "bech32, corrupted"),
    ("bc1Qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", "bech32, mixed case"),
    ("not an address", "free text"),
    ("", "empty"),
    ("   ", "whitespace only"),
]


@pytest.mark.parametrize("address,expected_chain,label", VALID)
def test_valid_addresses_detected(address, expected_chain, label):
    result = detect(address)
    assert result.is_valid, f"{label} should be valid: {result.reason}"
    assert expected_chain in result.candidates, label


@pytest.mark.parametrize("address,label", INVALID)
def test_invalid_addresses_rejected(address, label):
    result = detect(address)
    assert not result.is_valid, f"{label} should be rejected"
    assert result.reason, "a rejection must explain itself to the investigator"


def test_evm_is_ambiguous_across_chains():
    """An 0x address exists on every EVM chain; detection must not guess."""
    result = detect("0xdAC17F958D2ee523a2206206994597C13D831ec7")
    assert result.is_ambiguous
    assert Chain.ETHEREUM in result.candidates
    assert Chain.BSC in result.candidates


def test_tron_and_bitcoin_are_unambiguous():
    assert not detect("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t").is_ambiguous
    assert not detect("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa").is_ambiguous


@pytest.mark.parametrize(
    "raw",
    [
        "  TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t  ",
        "tron:TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
        '"TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"',
        "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t,",
        "<TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t>",
    ],
)
def test_wrappers_stripped_from_complaint_text(raw):
    """Addresses arrive from complaint free-text with junk attached."""
    result = detect(raw)
    assert result.is_valid
    assert result.normalized == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


def test_evm_normalised_to_lowercase():
    """Mixed EIP-55 casing must not split one address into two graph nodes."""
    upper = detect("0xDAC17F958D2EE523A2206206994597C13D831EC7")
    mixed = detect("0xdAC17F958D2ee523a2206206994597C13D831ec7")
    assert upper.normalized == mixed.normalized


def test_tron_hex_to_base58_roundtrip():
    """TRON's native API returns 41-prefixed hex; conversion must be exact."""
    assert (
        tron_hex_to_base58("41a614f803b6fd780986a42c78ec9c7f77e6ded13c")
        == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    )


def test_tron_hex_without_prefix_is_handled():
    """Some endpoints omit the 0x41 network byte."""
    assert (
        tron_hex_to_base58("a614f803b6fd780986a42c78ec9c7f77e6ded13c")
        == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    )
