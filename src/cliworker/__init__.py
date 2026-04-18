"""cliworker — fast, reliable calls to claude / codex / gemini / ollama CLIs.

The techniques encapsulated here were reverse-engineered from navcom's
production loop. Key wins:

  - CLAUDE_FAST flags that skip MCP / tools / chrome / session-persistence
    startup overhead (~18s → ~4s).
  - Gemini MCP strip-and-restore for the one CLI with no config-override flag.
  - Subscription-first fallback (strip API keys on pass 1, use them on pass 2).
  - Skip-cache for broken engines (auth expired, subscription gone).
  - Prompt via stdin for bulk content, short instruction as CLI arg.
"""
from cliworker.core import (
    CLIResult,
    CLISpec,
    run_cli,
    run_with_fallback,
)
from cliworker.registry import KNOWN_CLIS, get_spec

__version__ = "0.1.0"

__all__ = [
    "CLIResult",
    "CLISpec",
    "run_cli",
    "run_with_fallback",
    "KNOWN_CLIS",
    "get_spec",
    "__version__",
]
