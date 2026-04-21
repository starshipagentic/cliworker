"""cliworker — one sane way to call claude, codex, gemini, ollama as subprocesses.

Python API — two functions:

    from cliworker import run, run_fast

    run("hi")                            # default chain (from state.json), full mode
    run("hi", "claude")                  # one CLI, full mode
    run("hi", "claude", "codex")         # chain, full mode
    run_fast("hi", "claude")             # one CLI, fast mode (sugar for fast=True)
    run("hi", "claude", fast=True)       # same thing, explicit
    run("hi", "claude", paid_ok=True)    # allow paid API fallback

Every call returns a `list[CLIResult]`. Short-circuits at first success.

Shell API — even simpler:

    cliworker "hi"                       # default chain, full mode
    cliworker "hi" --fast                # default chain, fast mode
    cliworker "hi" run claude            # one CLI, full mode
    cliworker "hi" run claude --fast     # one CLI, fast mode
    cliworker "hi" run claude codex      # chain

What "fast" does, automatically:
    * claude -p gets CLAUDE_FAST_FLAGS (no MCP/tools/chrome startup) → ~18s → ~4s
    * gemini -p strips mcpServers from ~/.gemini/settings.json during the call
    * codex/ollama: no-op (already lightweight)

By default cliworker stays free-only: API key env vars are stripped before every
invocation. To allow paid-API fallback, pass paid_ok=True / paid_ok=["claude"]
in Python, or `--paid-ok all` / `--paid-ok claude,codex` on the shell.

State + cache live at ~/.cliworker/ (honors XDG_CONFIG_HOME / XDG_CACHE_HOME
if explicitly set).
"""
from cliworker.core import (
    CLIResult,
    CLISpec,
    invoke,
    run,
    run_fast,
)
from cliworker.registry import KNOWN_CLIS, get_spec
from cliworker.state import default_chain

__version__ = "0.8.2"

__all__ = [
    "run",
    "run_fast",
    "invoke",
    "CLIResult",
    "CLISpec",
    "get_spec",
    "KNOWN_CLIS",
    "default_chain",
    "__version__",
]
