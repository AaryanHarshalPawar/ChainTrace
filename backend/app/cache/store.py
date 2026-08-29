"""SQLite-backed response cache -- the mechanism behind the hybrid data model.

Three jobs:

1. **Rate-limit survival.** Free explorer tiers allow a handful of requests per
   second; a five-hop trace fans out to hundreds. Without a cache the tracer
   throttles itself to uselessness.
2. **Reproducibility.** An investigation must be re-runnable months later and
   produce the identical graph. Cached raw responses are the evidence record.
3. **Offline demo.** Entries can be *pinned*, which exempts them from TTL
   expiry. A pinned snapshot of real fraud-linked addresses ships with the
   repo, so ``OFFLINE_MODE=true`` gives a full live-looking demo with the
   network unplugged.

SQLite is deliberate: no Docker on the target machines, and a single file is
trivial to commit, ship and hand to a judge.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    key          TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    params_json  TEXT NOT NULL DEFAULT '{}',
    body_json    TEXT NOT NULL,
    chain        TEXT,
    kind         TEXT,
    fetched_at   INTEGER NOT NULL,
    ttl_seconds  INTEGER NOT NULL,
    pinned       INTEGER NOT NULL DEFAULT 0,
    hit_count    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cache_chain_kind ON http_cache(chain, kind);
CREATE INDEX IF NOT EXISTS idx_cache_pinned ON http_cache(pinned);
"""


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    writes: int = 0
    stale_serves: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


def make_key(url: str, params: dict[str, Any] | None = None) -> str:
    """Stable cache key. Params are sorted so ordering never splits an entry."""
    canonical = json.dumps(params or {}, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{url}|{canonical}".encode()).hexdigest()
    return digest[:40]


class CacheStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self.db_path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        # WAL lets the API read while a background trace writes.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self.stats = CacheStats()

    # -- reads -------------------------------------------------------------

    def get(
        self, key: str, *, allow_stale: bool = False
    ) -> tuple[Any, bool] | None:
        """Return ``(body, is_stale)`` or ``None`` on a miss.

        ``allow_stale`` is what makes offline mode work: an expired entry is
        still perfectly good evidence of what the chain looked like, and a
        stale answer beats no answer when the venue wifi has died.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT body_json, fetched_at, ttl_seconds, pinned"
                " FROM http_cache WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                self.stats.misses += 1
                return None

            age = time.time() - row["fetched_at"]
            is_stale = not row["pinned"] and age > row["ttl_seconds"]
            if is_stale and not allow_stale:
                self.stats.misses += 1
                return None

            self._conn.execute(
                "UPDATE http_cache SET hit_count = hit_count + 1 WHERE key = ?",
                (key,),
            )
            self.stats.hits += 1
            if is_stale:
                self.stats.stale_serves += 1
            return json.loads(row["body_json"]), is_stale

    # -- writes ------------------------------------------------------------

    def put(
        self,
        key: str,
        body: Any,
        *,
        url: str,
        params: dict[str, Any] | None = None,
        ttl_seconds: int,
        chain: str | None = None,
        kind: str | None = None,
        pinned: bool = False,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO http_cache"
                " (key, url, params_json, body_json, chain, kind,"
                "  fetched_at, ttl_seconds, pinned)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET"
                "   body_json = excluded.body_json,"
                "   fetched_at = excluded.fetched_at,"
                "   ttl_seconds = excluded.ttl_seconds,"
                # Never let a routine refresh silently un-pin the demo corpus.
                "   pinned = MAX(http_cache.pinned, excluded.pinned)",
                (
                    key,
                    url,
                    json.dumps(params or {}, sort_keys=True, separators=(",", ":")),
                    json.dumps(body, separators=(",", ":")),
                    chain,
                    kind,
                    int(time.time()),
                    ttl_seconds,
                    1 if pinned else 0,
                ),
            )
            self.stats.writes += 1

    # -- snapshot management ----------------------------------------------

    def pin_all(self, *, chain: str | None = None) -> int:
        """Freeze current entries into the offline demo snapshot."""
        with self._lock:
            if chain:
                cur = self._conn.execute(
                    "UPDATE http_cache SET pinned = 1 WHERE chain = ?", (chain,)
                )
            else:
                cur = self._conn.execute("UPDATE http_cache SET pinned = 1")
            return cur.rowcount

    def purge_expired(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM http_cache"
                " WHERE pinned = 0 AND (? - fetched_at) > ttl_seconds",
                (int(time.time()),),
            )
            return cur.rowcount

    def summary(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n,"
                " SUM(pinned) AS pinned,"
                " SUM(hit_count) AS hits FROM http_cache"
            ).fetchone()
            by_chain = self._conn.execute(
                "SELECT chain, COUNT(*) AS n FROM http_cache GROUP BY chain"
            ).fetchall()
        return {
            "entries": row["n"] or 0,
            "pinned_entries": row["pinned"] or 0,
            "lifetime_hits": row["hits"] or 0,
            "by_chain": {r["chain"] or "unknown": r["n"] for r in by_chain},
            "session": {
                "hits": self.stats.hits,
                "misses": self.stats.misses,
                "writes": self.stats.writes,
                "stale_serves": self.stats.stale_serves,
                "hit_rate": round(self.stats.hit_rate, 3),
            },
            "db_path": str(self.db_path),
            "db_size_bytes": (
                self.db_path.stat().st_size if self.db_path.exists() else 0
            ),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_store: CacheStore | None = None
_store_lock = threading.Lock()


def get_store(db_path: Path | None = None) -> CacheStore:
    """Process-wide singleton, created on first use."""
    global _store
    with _store_lock:
        if _store is None:
            from app.config import settings

            _store = CacheStore(db_path or settings.cache_db_path)
        return _store
