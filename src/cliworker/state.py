"""Persistent state: first-run flag, detected CLIs, default chain.

Lives at ~/.cliworker/state.json by default, matching the convention of
the LLM CLIs cliworker orchestrates (~/.claude/, ~/.codex/, ~/.gemini/,
~/.ollama/). If XDG_CONFIG_HOME is explicitly set, honors that at
$XDG_CONFIG_HOME/cliworker/state.json instead.

One file, small schema, human-readable JSON. Users can edit it freely.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


DEFAULT_OLLAMA_MODEL = "gemma3:4b"
BUILT_IN_ORDER = ("claude", "codex", "gemini", "ollama")


def _state_dir() -> Path:
    """cliworker state lives at ~/.cliworker/ by default — matching the
    convention of the LLM CLIs we orchestrate (~/.claude/, ~/.codex/,
    ~/.gemini/, ~/.ollama/). If the user explicitly set XDG_CONFIG_HOME,
    they opted into XDG and we honor it at $XDG_CONFIG_HOME/cliworker/."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "cliworker"
    return Path.home() / ".cliworker"


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
