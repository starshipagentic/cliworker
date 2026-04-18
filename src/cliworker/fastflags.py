"""Speed-up flags and filesystem-level tricks to minimize CLI startup overhead.

Background: every LLM CLI spins up on invocation — reads config, loads MCP
servers, initializes tools, authenticates. On a loaded dev machine this is
easily 15+ seconds before the model emits its first token. For one-shot
subprocess calls (paircode, navcom, any orchestrator) we don't need any of
that machinery — we just want a single prompt → completion round-trip.

## Per-CLI strategies

### Claude Code (`claude -p`)
Native flags exist that bypass the heavy lifting:
  --tools ""                              disable all tools
  --no-chrome                             skip chrome-extension load
  --strict-mcp-config --mcp-config '...'  override MCP to EMPTY
  --no-session-persistence                skip session state I/O

### Gemini CLI (`gemini -p`)
No config-override flag exists. We TEMPORARILY remove `mcpServers` from
~/.gemini/settings.json, run the command, then restore the original file.
Must be wrapped in try/finally so restore always runs.

### Codex CLI (`codex exec`)
No known speed flags needed — codex exec is already light.

### Ollama (`ollama run`)
No known speed flags — local, no network overhead anyway.
"""
from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path


CLAUDE_FAST_FLAGS: list[str] = [
    "--tools", "",
    "--no-chrome",
    "--strict-mcp-config",
    "--mcp-config", '{"mcpServers":{}}',
    "--no-session-persistence",
]


def _gemini_settings_path() -> Path:
    return Path.home() / ".gemini" / "settings.json"


def _gemini_backup_path() -> Path:
    return Path.home() / ".gemini" / "settings.json.cliworker-bak"


@contextmanager
def gemini_stripped_mcp():
    """Context manager that temporarily removes `mcpServers` from
    ~/.gemini/settings.json and restores on exit.

    Usage:
        with gemini_stripped_mcp():
            subprocess.run(["gemini", "-p", prompt], ...)

    Safe to nest; a no-op if settings file or mcpServers key is missing.
    Restore is in a finally block so it runs even on exception.
    """
    settings = _gemini_settings_path()
    backup = _gemini_backup_path()
    restored_or_skipped = False

    if not settings.exists():
        yield
        return

    try:
        raw = settings.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        yield
        return

    if "mcpServers" not in data:
        yield
        return

    try:
        # Back up then write stripped version
        backup.write_text(raw, encoding="utf-8")
        stripped = dict(data)
        stripped.pop("mcpServers")
        settings.write_text(json.dumps(stripped, indent=2), encoding="utf-8")
        yield
    finally:
        # Always restore, even on exception
        try:
            if backup.exists():
                shutil.copy2(backup, settings)
                backup.unlink()
                restored_or_skipped = True
        except OSError:
            # Best-effort; if restore fails, user can manually copy back from .cliworker-bak
            pass
