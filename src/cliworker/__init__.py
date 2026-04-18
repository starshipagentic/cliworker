"""cliworker — one sane way to call claude, codex, gemini, ollama as subprocesses.

Two verbs, one result object:

    from cliworker import run, use

    # Call ONE CLI:
    r = run("claude", "explain async/await")

    # Use a list of CLIs in order (first success wins):
    results = use(["claude", "codex", "gemini"], "summarize this")

Every call returns a CLIResult with .ok / .stdout / .stderr / .duration_s /
.spec / .argv / .returncode.

What cliworker does for you, automatically:
  * claude -p gets CLAUDE_FAST_FLAGS (no MCP/tools/chrome) → 18s → 4s cold start
  * gemini -p strips mcpServers from ~/.gemini/settings.json during call
  * use() tries subscription mode first (strips env API keys),
    then retries with keys for paid-API fallback
  * failed CLIs get cached for 1h so you don't re-spam a broken engine

From a shell, it's even simpler:

    cliworker "what is TCP?"
    cliworker "what is TCP?" use claude gemini
"""
from cliworker.core import (
    CLIResult,
    CLISpec,
    run,
    use,
    # Back-compat aliases:
    fallback,
    run_cli,
    run_with_fallback,
)
from cliworker.registry import KNOWN_CLIS, get_spec

__version__ = "0.3.0"

__all__ = [
    # Primary API
    "run",
    "use",
    "CLIResult",
    "CLISpec",
    "get_spec",
    "KNOWN_CLIS",
    # Back-compat
    "fallback",
    "run_cli",
    "run_with_fallback",
    "__version__",
]
