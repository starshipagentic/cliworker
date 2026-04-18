"""Persistent state: first-run flag, detected CLIs, default chain.

Lives at $XDG_CONFIG_HOME/cliworker/state.json (default ~/.config/cliworker/state.json).
One file, small schema, human-readable JSON. Users can edit it freely.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


DEFAULT_OLLAMA_MODEL = "llama3.1"
BUILT_IN_ORDER = ("claude", "codex", "gemini", "ollama")


def _state_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "cliworker"


def state_path() -> Path:
    return _state_dir() / "state.json"


def exists() -> bool:
    return state_path().exists()


def load() -> dict:
    p = state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save(data: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def mark_first_run_complete(
    detected_clis: dict[str, bool],
    ollama_has_model: bool | None = None,
    paid_ok: bool | list[str] | None = None,
) -> dict:
    """Record first-run completion + detected CLIs. Returns the saved state.

    paid_ok controls whether paid API fallback is allowed by default:
      None  — never (safe default; cliworker stays free-tier only)
      True  — allowed for every detected CLI
      list  — allowed only for the named CLIs
    """
    data = {
        "first_run_at": datetime.now().isoformat(timespec="seconds"),
        "detected_clis": detected_clis,
        "default_chain": [n for n in BUILT_IN_ORDER if detected_clis.get(n)],
        "default_ollama_model": DEFAULT_OLLAMA_MODEL,
        "ollama_has_any_model": ollama_has_model,
        "paid_ok": paid_ok,
    }
    save(data)
    return data


def default_chain() -> list[str]:
    """Return the cached default chain from state, or empty list if first-run not done."""
    data = load()
    return list(data.get("default_chain") or [])
