# cliworker

**Call `claude`, `codex`, `gemini`, `ollama` CLIs as subprocesses — fast and reliably.** Encapsulates the speed-and-correctness tricks that a year of orchestrating multi-LLM workflows has taught:

- **Skip MCP / tools / chrome startup overhead** (`claude -p` goes from ~18s to ~4s on a loaded machine)
- **Gemini MCP strip-and-restore** (the one CLI with no config-override flag)
- **Subscription-first fallback chain** (strip API keys to force subscription use before falling back to paid)
- **Skip-cache for broken engines** (don't retry a CLI whose auth just expired — 1h TTL)
- **Prompt-via-stdin** for long content; short instruction stays on argv

## Install

```bash
pip install cliworker
# or: pipx install cliworker
```

## Python API

```python
from cliworker import run_cli, run_with_fallback

# Simple
result = run_cli("claude", prompt="explain async/await in 3 sentences")
print(result.ok, result.duration_s, result.stdout)

# With overrides
from cliworker import get_spec
spec = get_spec("claude", model="sonnet", fast=True)
result = run_cli(spec, prompt="hi")

# Long content via stdin, short instruction via argv
result = run_cli(
    "claude",
    prompt="Summarize this in 2 paragraphs:",
    stdin_content=open("big-transcript.txt").read(),
)

# Fallback chain: subscription-first, then paid API
results = run_with_fallback(
    ["claude", "codex", "gemini"],
    prompt="summarize this",
    strip_keys_first=True,   # pass 1: strip env keys (subscription)
    retry_with_keys=True,    # pass 2: keys present (paid API)
)
first_ok = next((r for r in results if r.ok), None)
```

## CLI

```bash
cliworker --version
cliworker list                               # show default specs per CLI
cliworker run claude -p "hi"                 # invoke one CLI
cliworker run claude --no-fast -p "hi"       # disable fast-flags (debug)
cliworker chain claude codex gemini -p "hi"  # fallback chain
cliworker skip-cache                         # show broken engines
cliworker skip-cache --clear claude          # un-skip one CLI
cliworker skip-cache --clear ALL             # nuke the whole cache
```

## Per-CLI speed tactics

### Claude Code (`claude -p`)

Applies these flags by default when `fast=True`:

```
--tools ""                              # disable all tools
--no-chrome                             # skip chrome-extension load
--strict-mcp-config --mcp-config {...}  # override MCP to EMPTY
--no-session-persistence                # skip session-state I/O
```

### Gemini CLI (`gemini -p`)

Gemini has no config-override flag, so `cliworker` monkey-patches `~/.gemini/settings.json` for the duration of the call:

1. Back up the real file to `~/.gemini/settings.json.cliworker-bak`
2. Remove `mcpServers` key from the live file
3. Run the command
4. Restore the original (always, even on exception)

### Codex (`codex exec`)

Already lightweight. No speed flags applied; default includes `--dangerously-bypass-approvals-and-sandbox` so the CLI runs non-interactively.

### Ollama (`ollama run`)

Local, no network. No tricks needed.

## Subscription-mode-via-key-strip

Many LLM CLIs prefer a paid API key over your subscription if the env var is set. To force subscription use:

```python
run_cli("claude", prompt="hi", strip_keys=True)
# Internally: env.pop("ANTHROPIC_API_KEY") before subprocess.run(...)
```

Why? Claude Code, Codex, and Gemini all have this bias. `strip_keys=True` on pass 1 of the fallback chain maximizes free-tier usage; pass 2 retries with keys if pass 1 fails.

## Why this exists

Originally extracted from [navcom](https://pypi.org/project/navcom/) after watching a paircode peer-review loop spend 3 minutes waiting for 8 CLI invocations to start, most of which was MCP-server startup. Moved here so every orchestrator (paircode, navcom, your project) can share the same hardened code instead of reinventing the wheel per tool.

## License

MIT. See [LICENSE](LICENSE).
