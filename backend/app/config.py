"""Runtime configuration, loaded from environment / .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Chainalytics"
    environment: str = "development"

    # --- Upstream blockchain data providers -------------------------------
    # All three have keyless free tiers; keys simply raise the rate limits.
    trongrid_api_key: str | None = None
    trongrid_base_url: str = "https://api.trongrid.io"

    etherscan_api_key: str | None = None
    # Etherscan V2 is multi-chain: one key, one host, `chainid` selects network.
    etherscan_base_url: str = "https://api.etherscan.io/v2/api"

    mempool_base_url: str = "https://mempool.space/api"

    # --- Hybrid data strategy ---------------------------------------------
    # offline_mode forces every adapter to serve from the local cache only, so
    # a demo keeps working when the venue network does not. Requests that miss
    # the cache raise UpstreamUnavailable rather than hitting the network.
    offline_mode: bool = False
    cache_ttl_seconds: int = 24 * 3600
    cache_db_path: Path = DATA_DIR / "cache.sqlite3"

    http_timeout_seconds: float = 20.0
    # Keyless explorer tiers rate-limit aggressively; too few retries
    # produced traces with zero nodes rather than a slow trace.
    http_max_retries: int = 5

    # --- Tracing limits ----------------------------------------------------
    # Guard rails: a fraud wallet two hops from a Binance hot wallet fans out
    # fast, so an unbounded BFS will melt the rate limiter.
    max_trace_hops: int = 5
    max_nodes_per_trace: int = 400
    max_transfers_per_address: int = 200
    # Below this share of an address's inflow, an edge is not worth following.
    min_edge_value_ratio: float = 0.01

    # --- Label corpus ------------------------------------------------------
    labels_dir: Path = DATA_DIR / "labels"

    @property
    def snapshots_dir(self) -> Path:
        return DATA_DIR / "snapshots"


settings = Settings()
