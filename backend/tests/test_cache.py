"""Cache behaviour -- TTL, pinning and the stale-serve path.

The stale-serve path is what keeps a demo alive on a failing network, so it is
tested explicitly rather than assumed.
"""

from __future__ import annotations

import time

from app.cache.store import CacheStore, make_key


def test_roundtrip(tmp_path):
    store = CacheStore(tmp_path / "c.sqlite3")
    key = make_key("https://example.test/a", {"x": 1})
    store.put(key, {"hello": "world"}, url="https://example.test/a",
              params={"x": 1}, ttl_seconds=60)

    result = store.get(key)
    assert result is not None
    body, is_stale = result
    assert body == {"hello": "world"}
    assert not is_stale


def test_key_is_order_independent():
    """Param ordering must not fragment one logical request into two entries."""
    assert make_key("u", {"a": 1, "b": 2}) == make_key("u", {"b": 2, "a": 1})


def test_key_distinguishes_different_params():
    assert make_key("u", {"a": 1}) != make_key("u", {"a": 2})


def test_miss_returns_none(tmp_path):
    store = CacheStore(tmp_path / "c.sqlite3")
    assert store.get(make_key("https://example.test/nothing")) is None


def test_expired_entry_is_a_miss_by_default(tmp_path):
    store = CacheStore(tmp_path / "c.sqlite3")
    key = make_key("https://example.test/b")
    store.put(key, {"v": 1}, url="https://example.test/b", ttl_seconds=0)
    time.sleep(1.1)
    assert store.get(key) is None


def test_expired_entry_served_when_stale_allowed(tmp_path):
    """Offline mode: an old answer beats no answer, but must be marked stale."""
    store = CacheStore(tmp_path / "c.sqlite3")
    key = make_key("https://example.test/c")
    store.put(key, {"v": 1}, url="https://example.test/c", ttl_seconds=0)
    time.sleep(1.1)

    result = store.get(key, allow_stale=True)
    assert result is not None
    body, is_stale = result
    assert body == {"v": 1}
    assert is_stale, "a stale serve must announce itself"
    assert store.stats.stale_serves == 1


def test_pinned_entry_never_expires(tmp_path):
    """Pinned snapshot entries back the offline demo and must outlive TTL."""
    store = CacheStore(tmp_path / "c.sqlite3")
    key = make_key("https://example.test/d")
    store.put(key, {"v": 1}, url="https://example.test/d", ttl_seconds=0, pinned=True)
    time.sleep(1.1)

    result = store.get(key)
    assert result is not None
    body, is_stale = result
    assert body == {"v": 1}
    assert not is_stale, "pinned entries are never stale"


def test_refresh_cannot_silently_unpin(tmp_path):
    """A routine re-fetch must not drop an address out of the demo snapshot."""
    store = CacheStore(tmp_path / "c.sqlite3")
    key = make_key("https://example.test/e")
    store.put(key, {"v": 1}, url="https://example.test/e", ttl_seconds=0, pinned=True)
    store.put(key, {"v": 2}, url="https://example.test/e", ttl_seconds=0, pinned=False)
    time.sleep(1.1)

    result = store.get(key)
    assert result is not None
    body, is_stale = result
    assert body == {"v": 2}, "content should update"
    assert not is_stale, "but the pin must survive"


def test_purge_expired_spares_pinned(tmp_path):
    store = CacheStore(tmp_path / "c.sqlite3")
    store.put(make_key("u1"), {}, url="u1", ttl_seconds=0, pinned=True)
    store.put(make_key("u2"), {}, url="u2", ttl_seconds=0, pinned=False)
    time.sleep(1.1)

    assert store.purge_expired() == 1
    assert store.summary()["entries"] == 1


def test_pin_all_by_chain(tmp_path):
    store = CacheStore(tmp_path / "c.sqlite3")
    store.put(make_key("u1"), {}, url="u1", ttl_seconds=1, chain="tron")
    store.put(make_key("u2"), {}, url="u2", ttl_seconds=1, chain="bitcoin")

    assert store.pin_all(chain="tron") == 1
    assert store.summary()["pinned_entries"] == 1


def test_summary_reports_hit_rate(tmp_path):
    store = CacheStore(tmp_path / "c.sqlite3")
    key = make_key("u")
    store.put(key, {}, url="u", ttl_seconds=60)
    store.get(key)
    store.get(make_key("absent"))

    session = store.summary()["session"]
    assert session["hits"] == 1
    assert session["misses"] == 1
    assert session["hit_rate"] == 0.5
