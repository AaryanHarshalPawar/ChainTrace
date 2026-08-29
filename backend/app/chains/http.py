"""Cached, rate-limited, offline-capable HTTP client for explorer APIs.

Every upstream call in the system goes through :class:`CachedHttpClient`, which
implements the hybrid data policy in one place:

* cache hit within TTL      -> serve from SQLite, no network
* offline mode              -> serve from SQLite at any age, else fail loudly
* live call fails / 429s    -> fall back to a *stale* cache entry if one exists
* live call succeeds        -> persist the raw body as the evidence record

The stale-on-failure fallback is what keeps a demo alive when the venue network
degrades mid-trace: the graph finishes building, and every node sourced that
way is flagged so the investigator knows the data is not fresh.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

import httpx

from app.cache.store import CacheStore, get_store, make_key
from app.config import settings

log = logging.getLogger(__name__)


class UpstreamError(RuntimeError):
    """Base class for upstream data-provider failures."""


class UpstreamUnavailable(UpstreamError):
    """No live data and no cached fallback -- the caller cannot proceed."""


class RateLimited(UpstreamError):
    """Provider returned 429 and retries were exhausted."""


class HostRateLimiter:
    """Minimum-interval throttle, keyed by host.

    Free explorer tiers are per-host, so one limiter per host is the right
    granularity: throttling TronGrid must not slow down mempool.space.
    """

    # Measured, not documented. Keyless TronGrid returned 429 continuously at
    # ~3 rps during a multi-address trace and starved the graph of every node,
    # so the keyless baseline is set an order of magnitude below the published
    # ceiling. A free TronGrid key lifts this substantially, which is why the
    # setup instructions push for one.
    KEYED_INTERVALS = {
        "api.trongrid.io": 0.25,
        "api.etherscan.io": 0.25,
        "mempool.space": 0.12,
    }
    KEYLESS_INTERVALS = {
        "api.trongrid.io": 1.0,
        "api.etherscan.io": 0.35,
        "mempool.space": 0.12,
    }
    MAX_INTERVAL = 5.0

    def __init__(
        self, default_interval: float = 0.25, *, has_trongrid_key: bool = False
    ) -> None:
        self._default = default_interval
        self._intervals: dict[str, float] = dict(
            self.KEYED_INTERVALS if has_trongrid_key else self.KEYLESS_INTERVALS
        )
        self._last: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def interval_for(self, host: str) -> float:
        return self._intervals.get(host, self._default)

    def penalise(self, host: str) -> float:
        """Widen a host's interval after a 429.

        Published rate limits and enforced ones differ, and the enforced one
        varies with load. Backing off permanently for the rest of the session
        -- rather than only for the current retry -- stops a deep trace from
        rediscovering the same limit on every branch.
        """
        widened = min(self.interval_for(host) * 1.6, self.MAX_INTERVAL)
        self._intervals[host] = widened
        return widened

    async def acquire(self, host: str) -> None:
        async with self._locks[host]:
            wait = self.interval_for(host) - (time.monotonic() - self._last[host])
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[host] = time.monotonic()


class CachedHttpClient:
    def __init__(
        self,
        *,
        cache: CacheStore | None = None,
        offline: bool | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.cache = cache or get_store()
        self.offline = settings.offline_mode if offline is None else offline
        self.timeout = timeout or settings.http_timeout_seconds
        self.max_retries = max_retries or settings.http_max_retries
        self.limiter = HostRateLimiter(
            has_trongrid_key=bool(settings.trongrid_api_key)
        )
        self._client: httpx.AsyncClient | None = None
        self.upstream_calls = 0
        self.stale_serves = 0

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": "ChainTrace/0.1 (LEA blockchain analytics)"},
                limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
            )
        return self._client

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        ttl: int | None = None,
        chain: str | None = None,
        kind: str | None = None,
        force_refresh: bool = False,
        pin: bool = False,
    ) -> tuple[Any, bool]:
        """Fetch JSON. Returns ``(body, from_cache)``.

        Raises :class:`UpstreamUnavailable` when neither the network nor the
        cache can satisfy the request.
        """
        key = make_key(url, params)
        ttl = ttl if ttl is not None else settings.cache_ttl_seconds

        if not force_refresh:
            cached = self.cache.get(key, allow_stale=self.offline)
            if cached is not None:
                body, is_stale = cached
                if is_stale:
                    self.stale_serves += 1
                return body, True

        if self.offline:
            cached = self.cache.get(key, allow_stale=True)
            if cached is not None:
                self.stale_serves += 1
                return cached[0], True
            raise UpstreamUnavailable(
                f"offline mode: no cached response for {url} "
                f"(params={params}). Warm the cache online, or run the "
                f"snapshot seeder."
            )

        host = urlparse(url).netloc
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                await self.limiter.acquire(host)
                client = await self._ensure_client()
                self.upstream_calls += 1
                response = await client.get(url, params=params, headers=headers)

                if response.status_code == 429:
                    # Honour Retry-After when present, else exponential backoff
                    # with jitter so parallel branches do not resynchronise.
                    retry_after = response.headers.get("Retry-After")
                    delay = (
                        float(retry_after)
                        if retry_after and retry_after.isdigit()
                        else (2**attempt) + random.random()
                    )
                    widened = self.limiter.penalise(host)
                    log.warning(
                        "429 from %s: backing off %.1fs, host interval now %.2fs",
                        host,
                        delay,
                        widened,
                    )
                    last_error = RateLimited(f"{host} rate limited")
                    await asyncio.sleep(min(delay, 10.0))
                    continue

                response.raise_for_status()
                body = response.json()
                self.cache.put(
                    key,
                    body,
                    url=url,
                    params=params,
                    ttl_seconds=ttl,
                    chain=chain,
                    kind=kind,
                    pinned=pin,
                )
                return body, False

            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                log.warning(
                    "upstream %s attempt %d/%d failed: %s",
                    host,
                    attempt + 1,
                    self.max_retries,
                    exc,
                )
                await asyncio.sleep(min((2**attempt) * 0.5 + random.random(), 8.0))

        # Network exhausted -- a stale cached body is better than no answer.
        cached = self.cache.get(key, allow_stale=True)
        if cached is not None:
            log.warning("serving STALE cache for %s after upstream failure", url)
            self.stale_serves += 1
            return cached[0], True

        raise UpstreamUnavailable(
            f"{url} unreachable after {self.max_retries} attempts "
            f"and no cached fallback exists"
        ) from last_error

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> CachedHttpClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
