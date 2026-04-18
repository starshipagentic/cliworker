"""Cache broken engines so we don't retry them on every call within TTL.

Rationale: if claude's subscription lapsed, codex's auth expired, or gemini
hit its daily quota, those calls will fail with the same error every time
for some time. Caching the failure for a TTL (default 1h) prevents burning
seconds per call until the user fixes auth.

Cache file default: ~/.cliworker/skip-cache.json (JSON dict of
"cli-name": unix_timestamp_of_failure). If XDG_CACHE_HOME is set,
honors it at $XDG_CACHE_HOME/cliworker/skip-cache.json.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _default_cache_dir() -> Path:
    """cliworker cache lives at ~/.cliworker/ by default. If XDG_CACHE_HOME
    is explicitly set, we honor it at $XDG_CACHE_HOME/cliworker/."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "cliworker"
    return Path.home() / ".cliworker"


DEFAULT_CACHE_DIR = _default_cache_dir()
DEFAULT_CACHE_PATH = DEFAULT_CACHE_DIR / "skip-cache.json"
DEFAULT_TTL_SECONDS = 3600  # 1 hour


def _load(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(path: Path, data: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def is_skipped(cli_name: str, *, path: Path = DEFAULT_CACHE_PATH, ttl: int = DEFAULT_TTL_SECONDS) -> bool:
    """Return True if `cli_name` has a fresh (within TTL) failure record."""
    data = _load(path)
    ts = data.get(cli_name)
    if ts is None:
        return False
    if time.time() - ts > ttl:
        # Stale — remove opportunistically
        data.pop(cli_name, None)
        try:
            _save(path, data)
        except OSError:
            pass
        return False
    return True


def mark_broken(cli_name: str, *, path: Path = DEFAULT_CACHE_PATH) -> None:
    """Record `cli_name` as broken right now."""
    data = _load(path)
    data[cli_name] = time.time()
    try:
        _save(path, data)
    except OSError:
        pass


def clear(cli_name: str | None = None, *, path: Path = DEFAULT_CACHE_PATH) -> None:
    """Clear one CLI (or all if cli_name is None) from the skip cache."""
    if cli_name is None:
        if path.exists():
            path.unlink()
        return
    data = _load(path)
    data.pop(cli_name, None)
    try:
        _save(path, data)
    except OSError:
        pass
