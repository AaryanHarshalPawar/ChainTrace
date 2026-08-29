"""Ingest OFAC sanctioned cryptocurrency addresses into the label corpus.

The US Treasury publishes its Specially Designated Nationals list as XML with
no key and no rate limit, and it carries digital-currency addresses tagged by
asset. That makes it the one piece of the corpus that is unimpeachable ground
truth: an address here is sanctioned as a matter of published record, not
inference.

Run::

    python scripts/ingest_ofac.py

Writes ``data/labels/ofac_sanctions.json``.

The chain of each address is decided by *our own format detector*, not by the
OFAC asset tag, and the two are cross-checked. The tag is a poor chain
identifier -- "USDT" appears on both TRON and Ethereum -- and a disagreement
between tag and format is worth reporting rather than silently resolving.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.core.chains import Chain, detect  # noqa: E402

SDN_URL = (
    "https://sanctionslistservice.ofac.treas.gov"
    "/api/PublicationPreview/exports/SDN.XML"
)
ID_TYPE_PREFIX = "Digital Currency Address - "

# OFAC asset tag -> the chain we expect the address format to confirm.
EXPECTED_CHAIN = {
    "XBT": Chain.BITCOIN,
    "BTC": Chain.BITCOIN,
    "ETH": Chain.ETHEREUM,
    "TRX": Chain.TRON,
    "BSC": Chain.BSC,
    "ARB": Chain.ARBITRUM,
}


def _namespace_of(root: ElementTree.Element) -> dict[str, str]:
    """Read the default namespace off the document rather than hardcoding it.

    OFAC has already moved this once -- from ``tempuri.org/sdnList.xsd`` to a
    service URL -- and a stale constant fails silently, parsing zero entries
    while reporting success. Deriving it means a future move cannot break the
    ingest without also changing the document structure.
    """
    tag = root.tag
    if tag.startswith("{"):
        return {"s": tag[1 : tag.index("}")]}
    return {"s": ""}


def _text(node: ElementTree.Element, tag: str, ns: dict[str, str]) -> str | None:
    found = node.find(f"s:{tag}", ns)
    return found.text.strip() if found is not None and found.text else None


def _entity_name(entry: ElementTree.Element, ns: dict[str, str]) -> str:
    last = _text(entry, "lastName", ns) or ""
    first = _text(entry, "firstName", ns) or ""
    return " ".join(p for p in (first, last) if p) or "UNNAMED SDN ENTRY"


def download(url: str = SDN_URL) -> bytes:
    print(f"downloading {url} ...")
    with httpx.Client(timeout=180, follow_redirects=True) as client:
        response = client.get(url, headers={"User-Agent": "Chainalytics/0.1"})
        response.raise_for_status()
    print(f"  {len(response.content):,} bytes")
    return response.content


def parse(xml_bytes: bytes) -> dict:
    root = ElementTree.fromstring(xml_bytes)
    ns = _namespace_of(root)

    publish = root.find("s:publshInformation", ns)
    publish_date = _text(publish, "Publish_Date", ns) if publish is not None else None

    entries: list[dict] = []
    skipped_unsupported: dict[str, int] = {}
    mismatches: list[dict] = []
    seen: set[tuple[str, str]] = set()

    sdn_entries = root.findall("s:sdnEntry", ns)
    if not sdn_entries:
        raise RuntimeError(
            f"parsed 0 sdnEntry elements under namespace {ns['s']!r} -- the SDN "
            f"schema has changed and this ingester needs updating"
        )

    for sdn_entry in sdn_entries:
        id_list = sdn_entry.find("s:idList", ns)
        if id_list is None:
            continue

        crypto_ids = [
            identifier
            for identifier in id_list.findall("s:id", ns)
            if (_text(identifier, "idType", ns) or "").startswith(ID_TYPE_PREFIX)
        ]
        if not crypto_ids:
            continue

        name = _entity_name(sdn_entry, ns)
        programs = [
            program.text.strip()
            for program in sdn_entry.findall("s:programList/s:program", ns)
            if program.text
        ]
        remarks = _text(sdn_entry, "remarks", ns)

        for identifier in crypto_ids:
            asset = (_text(identifier, "idType", ns) or "")[len(ID_TYPE_PREFIX) :].strip()
            address = _text(identifier, "idNumber", ns)
            if not address:
                continue

            detection = detect(address)
            if not detection.is_valid:
                # Monero, Zcash, Litecoin and friends: real sanctions data, but
                # on chains this system does not index. Counted, not dropped.
                skipped_unsupported[asset] = skipped_unsupported.get(asset, 0) + 1
                continue

            # An EVM address is valid on every EVM chain, so trust the OFAC tag
            # to pick among them; otherwise take the unambiguous format result.
            expected = EXPECTED_CHAIN.get(asset)
            if detection.is_ambiguous:
                chain = expected if expected in detection.candidates else Chain.ETHEREUM
            else:
                chain = detection.candidates[0]
                if expected is not None and expected is not chain:
                    mismatches.append(
                        {"address": address, "ofac_asset": asset, "detected": chain.value}
                    )

            key = (detection.normalized, chain.value)
            if key in seen:
                continue  # the same address is often listed per-asset
            seen.add(key)

            entries.append(
                {
                    "address": detection.normalized,
                    "chain": chain.value,
                    "name": name,
                    "ofac_uid": _text(sdn_entry, "uid", ns),
                    "asset_code": asset,
                    "programs": programs,
                    "remarks": (remarks or "")[:300] or None,
                }
            )

    return {
        "source": "OFAC Specially Designated Nationals List",
        "source_url": SDN_URL,
        "publish_date": publish_date,
        "generated_at": datetime.now(UTC).isoformat(),
        "entry_count": len(entries),
        "unsupported_chain_counts": skipped_unsupported,
        "tag_format_mismatches": mismatches,
        "entries": entries,
    }


def main() -> int:
    payload = parse(download())

    by_chain: dict[str, int] = {}
    for entry in payload["entries"]:
        by_chain[entry["chain"]] = by_chain.get(entry["chain"], 0) + 1

    out_path = settings.labels_dir / "ofac_sanctions.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"\nOFAC publish date : {payload['publish_date']}")
    print(f"supported addresses: {payload['entry_count']:,}")
    for chain, count in sorted(by_chain.items(), key=lambda kv: -kv[1]):
        print(f"  {chain:<10} {count:>6,}")
    print("\nskipped (chains not indexed):")
    for asset, count in sorted(
        payload["unsupported_chain_counts"].items(), key=lambda kv: -kv[1]
    ):
        print(f"  {asset:<10} {count:>6,}")
    if payload["tag_format_mismatches"]:
        print(f"\ntag/format mismatches: {len(payload['tag_format_mismatches'])}")
        for mismatch in payload["tag_format_mismatches"][:5]:
            print(f"  {mismatch}")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
