"""Detect which LLM CLIs are installed on PATH, with config-dir hints."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CLIPresence:
    name: str
    binary: str
    binary_path: Path | None
    config_dir: Path
    installed: bool
    install_hint: str


_KNOWN: dict[str, tuple[str, Path, str]] = {
    "claude": (
        "claude",
        Path.home() / ".claude",
        "Install Claude Code: https://claude.com/product/claude-code",
    ),
    "codex": (
        "codex",
        Path.home() / ".codex",
        "Install Codex CLI: `npm i -g @openai/codex`",
    ),
    "gemini": (
        "gemini",
        Path.home() / ".gemini",
        "Install Gemini CLI: `npm i -g @google/gemini-cli`",
    ),
    "ollama": (
        "ollama",
        Path.home() / ".ollama",
        "Install Ollama: https://ollama.com/download",
    ),
}


def detect() -> dict[str, CLIPresence]:
    """Return a dict of cli-name → CLIPresence for every CLI cliworker knows."""
    out: dict[str, CLIPresence] = {}
    for name, (binary, config_dir, hint) in _KNOWN.items():
        found = shutil.which(binary)
        out[name] = CLIPresence(
            name=name,
            binary=binary,
            binary_path=Path(found) if found else None,
            config_dir=config_dir,
            installed=bool(found),
            install_hint=hint,
        )
    return out
