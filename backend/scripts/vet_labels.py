"""Verify curated VASP labels against real on-chain behaviour.

Run before committing any entry to ``data/labels/vasp_seed.json``::

    python scripts/vet_labels.py
    python scripts/vet_labels.py --address TKHuVq1oKVruCGLvqVexFs6dawKv6fQgFs --role hot_wallet

This exists because unverified labels do not survive contact with the chain.
During development four commonly-cited "exchange hot wallets" were profiled:
one had 0 senders and 198 receivers (a payout wallet, not a deposit target),
two showed ordinary retail activity, and only one behaved like an exchange.
Committing those three would have sent preservation notices to the wrong
companies.

The script does not decide whether a label is *correct* -- only whether the
address behaves consistently with the claimed role. A wallet can behave
exactly like an exchange and still belong to a different exchange than the one
named, so operator attribution still needs an independent source.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.attribution.behaviour import BehaviourClass, classify  # noqa: E402
from app.chains.http import CachedHttpClient  # noqa: E402
from app.chains.spam import partition  # noqa: E402
from app.chains.tron import TronAdapter  # noqa: E402
from app.config import settings  # noqa: E402
from app.core.chains import Chain  # noqa: E402
from app.core.pricing import PriceOracle  # noqa: E402

# Behaviour classes consistent with each claimed role.
ROLE_EXPECTATIONS: dict[str, set[BehaviourClass]] = {
    "hot_wallet": {BehaviourClass.EXCHANGE_LIKE, BehaviourClass.CONSOLIDATOR},
    "cold_wallet": {BehaviourClass.TERMINAL_HOLDER, BehaviourClass.CONSOLIDATOR},
    "deposit": {BehaviourClass.DEPOSIT_ADDRESS, BehaviourClass.PASS_THROUGH},
}

SAMPLE_SIZE = 200


async def vet_one(adapter, address: str, claimed_role: str | None) -> dict:
    account = await adapter.fetch_account(address)
    raw = await adapter.fetch_transfers(address, limit=SAMPLE_SIZE)
    outcome = partition(raw)
    profile = adapter.build_profile(
        address,
        outcome.kept,
        account,
        requested_limit=SAMPLE_SIZE,
        raw_transfer_count=len(raw),
    )
    verdict = classify(profile, outcome.kept)

    expected = ROLE_EXPECTATIONS.get(claimed_role or "", set())
    if not expected:
        consistent = None  # no claim to check against
    else:
        consistent = verdict.behaviour in expected

    return {
        "address": address,
        "claimed_role": claimed_role,
        "is_contract": account.is_contract,
        "observed_behaviour": verdict.behaviour.value,
        "behaviour_confidence": verdict.confidence,
        "consistent_with_claim": consistent,
        "transfers_sampled": len(raw),
        "material_transfers": len(outcome.kept),
        "unique_senders": profile.unique_senders,
        "unique_receivers": profile.unique_receivers,
        "received_usd": float(profile.total_received_usd),
        "retained_ratio": round(profile.retained_ratio, 4),
        "truncated": profile.is_truncated,
        "first_seen": profile.first_seen.isoformat() if profile.first_seen else None,
        "reasoning": verdict.reasoning,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", help="vet a single address instead of the corpus")
    parser.add_argument("--role", help="claimed role for --address", default=None)
    args = parser.parse_args()

    http = CachedHttpClient()
    oracle = PriceOracle(http)
    await oracle.warm()
    adapters = {Chain.TRON: TronAdapter(http, oracle)}

    if args.address:
        targets = [{"address": args.address, "chain": "tron", "address_role": args.role}]
    else:
        seed_path = settings.labels_dir / "vasp_seed.json"
        if not seed_path.exists():
            print(f"no curated corpus at {seed_path}")
            return 1
        targets = json.loads(seed_path.read_text(encoding="utf-8")).get("entries", [])

    if not targets:
        print("nothing to vet")
        return 0

    failures = 0
    for entry in targets:
        chain = Chain(entry.get("chain", "tron"))
        adapter = adapters.get(chain)
        if adapter is None:
            print(f"SKIP  {entry['address']}: no adapter for {chain.value}")
            continue

        try:
            report = await vet_one(adapter, entry["address"], entry.get("address_role"))
        except Exception as exc:  # noqa: BLE001 - report, do not abort the run
            print(f"ERROR {entry['address']}: {type(exc).__name__}: {exc}")
            failures += 1
            continue

        status = {True: "OK  ", False: "FAIL", None: "N/A "}[
            report["consistent_with_claim"]
        ]
        if report["consistent_with_claim"] is False:
            failures += 1

        print(f"\n{status} {report['address']}  ({entry.get('name', '?')})")
        print(f"     claimed role   : {report['claimed_role']}")
        print(f"     observed       : {report['observed_behaviour']} "
              f"(confidence {report['behaviour_confidence']})")
        print(f"     is_contract    : {report['is_contract']}")
        print(f"     senders/recvrs : {report['unique_senders']} / "
              f"{report['unique_receivers']}")
        print(f"     received       : ${report['received_usd']:,.2f}")
        print(f"     retained       : {report['retained_ratio']:.2%}")
        print(f"     sampled        : {report['material_transfers']} material of "
              f"{report['transfers_sampled']} (truncated={report['truncated']})")
        for line in report["reasoning"]:
            print(f"       - {line}")

    print(
        f"\n{len(targets) - failures}/{len(targets)} entries consistent with their "
        f"claimed role."
    )
    if failures:
        print(
            "Entries marked FAIL do not behave as claimed. Correct the role, "
            "lower the confidence, or remove the entry -- do not commit it as is."
        )
    print(
        "\nReminder: consistent behaviour does NOT confirm the named operator. "
        "Naming a company still requires an independent, citable source."
    )

    await http.aclose()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
