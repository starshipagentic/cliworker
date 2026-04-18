"""cliworker — one sane way to call claude, codex, gemini, ollama as subprocesses.

Why this library exists
=======================
Every LLM CLI has its own flags, its own quirks, its own startup overhead. You
end up writing the same wrapper code in every project: build the argv, run
subprocess, catch timeouts, parse output. Worse: on a loaded dev machine,
`claude -p` can take 18 seconds to say "hi" because it loads MCP servers,
tools, and chrome extensions at startup — every time.

cliworker fixes that once.

Two verbs, one result
=====================

    from cliworker import run, fallback

    # Verb 1 — call ONE CLI:
    r = run("claude", "explain async/await")
    print(r.ok, r.duration_s, r.stdout)

    # Verb 2 — try a FALLBACK CHAIN (stop on first success):
    results = fallback(["claude", "codex", "gemini"], "summarize this")
    first_ok = next((r for r in results if r.ok), None)

Every call returns a CLIResult with .ok / .stdout / .stderr / .duration_s /
.spec / .argv / .returncode. That's the entire mental model.

Sensible defaults baked in
==========================

* `run("claude", ...)` automatically applies CLAUDE_FAST_FLAGS
  (--tools "" --no-chrome --strict-mcp-config --mcp-config {} --no-session-persistence)
  which reduces a cold `claude -p` from ~18s to ~4s on loaded machines.
* `run("gemini", ...)` automatically strips mcpServers from ~/.gemini/settings.json
  during the call and restores afterwards (gemini has no config-override flag).
* `fallback(..., free_first=True)` (default) strips API key env vars on pass 1
  to force subscription-mode use, then retries with keys on pass 2.
* Failed CLIs are cached for 1 hour in ~/.cache/cliworker/skip-cache.json so
  repeated calls don't keep trying an engine whose auth just expired.

All defaults are overridable via CLISpec.
"""
from cliworker.core import (
    CLIResult,
    CLISpec,
    fallback,
    run,
    # Back-compat aliases (original names):
    run_cli,
    run_with_fallback,
)
from cliworker.registry import KNOWN_CLIS, get_spec

__version__ = "0.2.1"

__all__ = [
    # Primary API
    "run",
    "fallback",
    "CLIResult",
    "CLISpec",
    "get_spec",
    "KNOWN_CLIS",
    # Back-compat
    "run_cli",
    "run_with_fallback",
    "__version__",
]
