"""Tests for skip-cache."""
from __future__ import annotations

import time

from cliworker.skipcache import clear, is_skipped, mark_broken


def test_mark_then_check(tmp_path):
    cache = tmp_path / "cache.json"
    mark_broken("claude", path=cache)
    assert is_skipped("claude", path=cache) is True
    assert is_skipped("codex", path=cache) is False


def test_ttl_expiry(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    mark_broken("claude", path=cache)
    # Simulate 2 hours later — capture real time.time BEFORE patching or we
    # get infinite recursion (the lambda would call itself).
    real_time = time.time
    monkeypatch.setattr("cliworker.skipcache.time.time", lambda: real_time() + 7200)
    assert is_skipped("claude", path=cache, ttl=3600) is False


def test_clear_one(tmp_path):
    cache = tmp_path / "cache.json"
    mark_broken("claude", path=cache)
    mark_broken("codex", path=cache)
    clear("claude", path=cache)
    assert is_skipped("claude", path=cache) is False
    assert is_skipped("codex", path=cache) is True


def test_clear_all(tmp_path):
    cache = tmp_path / "cache.json"
    mark_broken("claude", path=cache)
    mark_broken("codex", path=cache)
    clear(None, path=cache)
    assert not cache.exists()
