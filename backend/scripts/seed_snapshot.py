"""Build the offline demo snapshot.

Warms the cache against a list of addresses and then *pins* every entry, which
exempts it from TTL expiry. With ``OFFLINE_MODE=true`` the system then serves
a complete, live-looking trace with the network unplugged.

This is insurance for the demo. Venue wifi fails, TronGrid rate-limits under
load, and a cold-cache trace makes several sequential requests per hop -- none
of which is a good look with three minutes on the clock.

Run (online, ahead of time)::

    python scripts/seed_snapshot.py TR7NHq... TMuA6Y...
    python scripts/seed_snapshot.py --from-file demo_addresses.txt

Then set ``OFFLINE_MODE=true`` in ``.env`` and everything is served from the
pinned snapshot in milliseconds.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.service import TraceService  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("addresses", nargs="*", help="addresses to pre-trace")
    parser.add_argument("--from-file", help="file with one address per line")
    parser.add_argument("--max-hops", type=int, default=3)
    parser.add_argument("--max-nodes", type=int, default=40)
    args = parser.parse_args()

    addresses = list(args.addresses)
    if args.from_file:
        text = Path(args.from_file).read_text(encoding="utf-8")
        addresses += [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.startswith("#")
        ]

    if not addresses:
        parser.error("give at least one address, or --from-file")

    if settings.offline_mode:
        print(
            "OFFLINE_MODE is enabled -- the cache cannot be warmed from the "
            "network. Set OFFLINE_MODE=false, seed, then turn it back on."
        )
        return 1

    service = TraceService()
    await service.startup()

    succeeded = 0
    for address in addresses:
        print(f"\n=== warming {address} ===")
        try:
            result = await service.trace(
                address, max_hops=args.max_hops, max_nodes=args.max_nodes
            )
        except Exception as exc:  # noqa: BLE001 - one bad address must not abort
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            continue
        succeeded += 1
        print(
            f"  {result.stats.nodes_explored} nodes, "
            f"{result.stats.edges_discovered} edges, "
            f"{len(result.attributions)} attributions, "
            f"{result.stats.upstream_calls} upstream calls"
        )
        if result.warnings:
            for warning in result.warnings[:3]:
                print(f"  ! {warning[:100]}")

    pinned = service.http.cache.pin_all()
    summary = service.http.cache.summary()
    print(f"\npinned {pinned} cache entries ({summary['db_size_bytes']:,} bytes)")
    print(f"warmed {succeeded}/{len(addresses)} addresses")
    print(
        "\nSet OFFLINE_MODE=true in backend/.env to serve entirely from this "
        "snapshot. Commit backend/data/cache.sqlite3 to share it with the team."
    )

    await service.shutdown()
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
