# cliworker

**One sane way to call `claude`, `codex`, `gemini`, `ollama` as subprocesses — fast, uniform, and reliable.**

From your shell:

```bash
cliworker "what is TCP?"                       # use default chain
cliworker "what is TCP?" use claude gemini     # specific CLIs, in order
```

From Python:

```python
from cliworker import run, use

result = run("claude", "explain async/await in 3 sentences")
print(result.ok, result.duration_s, result.stdout)

results = use(["claude", "codex", "gemini"], "summarize this")
first_ok = next((r for r in results if r.ok), None)
```

---

## The problem it solves

Every LLM CLI has its own flags, its own startup quirks, its own auth behavior. You end up rewriting the same subprocess wrapper in every project. Worse, on a dev machine with a typical MCP setup, **`claude -p "hi"` can take 18+ seconds** to say hi — because it loads every configured MCP server, tool, and chrome extension at startup, every single call.

cliworker encapsulates a year of tricks for calling these CLIs efficiently:

| Problem | cliworker's fix |
|---|---|
| `claude -p` boots every MCP server → 18s cold start | `CLAUDE_FAST` flags skip MCP / tools / chrome / session-persistence → ~4s |
| `gemini` has no config-override flag | Temporarily strips `mcpServers` from `~/.gemini/settings.json` and restores after |
| CLIs prefer paid API keys over subscriptions when both exist | `strip_keys=True` removes API-key env vars at call time to force subscription use |
| Broken CLIs (expired auth, quota hit) waste seconds every call | 1-hour skip-cache at `~/.cache/cliworker/skip-cache.json` |
| Every CLI uses different prompt-transport conventions | Unified `run()` API; per-CLI recipes in `KNOWN_CLIS` |
| Long transcripts bloat argv | `stdin_content=` pipes bulk content via stdin, keeps the instruction on argv |

---

## Install

```bash
pip install cliworker          # from pypi
# or
pipx install cliworker         # isolated, bin on PATH
```

Requires Python ≥ 3.10. The actual LLM CLIs (`claude`, `codex`, `gemini`, `ollama`) are not dependencies — cliworker just invokes them if present.

---

## Shell usage — the natural shape

```bash
cliworker "what is TCP?"                    # bare prompt, default chain
cliworker "what is TCP?" use claude         # one specific CLI
cliworker "what is TCP?" use claude gemini  # chain in the order you listed
cliworker --use claude,gemini "hi"          # flag form
cliworker --llm claude,gemini "hi"          # --llm is an alias for --use

cliworker "summarize:" < transcript.txt     # pipe bulk content via stdin
cliworker "hi" -m sonnet                    # model override
cliworker "hi" --no-paid                    # stay free-tier, don't retry with API keys
cliworker "hi" -v                           # show winner CLI + duration on stderr
```

No verbs to remember, no `-p` flag to type, no boilerplate. The prompt is
the prompt; `use` tells cliworker which CLIs. That's it.

For diagnostics:

```bash
cliworker doctor                             # which LLM CLIs are installed?
cliworker doctor --probe                     # also ping each with a "say ok"
cliworker info                               # show argv recipe for each CLI
cliworker info claude                        # just one
cliworker setup                              # re-run first-run diagnostics
cliworker skip-cache                         # inspect broken-engine cache
cliworker skip-cache --clear ALL             # reset it
```

### First run

The first time you type `cliworker "..."`, cliworker shows an ASCII banner,
scans PATH for installed CLIs, tells you exactly what to `npm i -g` /
`brew install` / `ollama pull` for anything missing, and saves its config
to `~/.config/cliworker/state.json`. Subsequent runs skip all that.

## Python library — the mental model

There are exactly **two verbs** and **one result object.**

### `run(cli, prompt, **kwargs)` → `CLIResult`

Call ONE CLI, get a `CLIResult`:

```python
from cliworker import run

r = run("claude", "hello")                    # simplest — defaults applied
r = run("claude", "hi", model="sonnet")       # pick a model
r = run("gemini", "hi", fast=False)           # disable speed tricks
r = run("claude", "hi", timeout_s=60)         # custom timeout
r = run("claude", "summarize:",               # big content via stdin
        stdin_content=open("transcript.txt").read())
r = run("claude", "hi", strip_keys=True)      # force subscription mode
```

### `use(clis, prompt, **kwargs)` → `list[CLIResult]`

Use a list of CLIs in order, stop at first success:

```python
from cliworker import use

results = use(["claude", "codex", "gemini"], "summarize this")
first_ok = next((r for r in results if r.ok), None)

# Default behavior: two passes
#   Pass 1: all CLIs with env API keys STRIPPED (free/subscription mode)
#   Pass 2: all CLIs with env API keys PRESENT (paid API mode)
# First .ok returns; list contains every attempt in order.

results = use(
    ["claude", "codex"],
    "hi",
    free_first=False,       # flip the default: paid API first
    retry_paid=False,       # don't try a 2nd pass at all
    timeout_s=30,
)
```

The old name `fallback()` still works as a back-compat alias.

### `CLIResult` — what comes back

```python
@dataclass
class CLIResult:
    spec: CLISpec              # which CLI + config was invoked
    ok: bool                   # True iff subprocess returncode == 0
    stdout: str                # full stdout
    stderr: str                # full stderr
    duration_s: float          # wall-clock seconds
    returncode: int | None     # None on timeout / binary-missing
    argv: list[str]            # the actual argv passed to subprocess
    skipped_reason: str | None # "not_on_path" / "skip_cache" / None

    @property
    def text(self) -> str:     # stdout if ok else stderr — convenience
        ...
```

That's it. Check `r.ok`, use `r.stdout`, read `r.duration_s` if you care about timing. The dataclass makes everything introspectable: `r.argv` shows you the exact subprocess call, `r.spec` shows which config was applied.

---

## Cookbook

### One-shot prompt

```python
from cliworker import run

r = run("claude", "what's the time complexity of quicksort?")
if r.ok:
    print(r.stdout)
```

### Long content via stdin + short instruction on argv

```python
transcript = open("meeting.txt").read()
r = run(
    "claude",
    "Summarize this meeting transcript in 5 bullet points:",
    stdin_content=transcript,
)
```

### Fallback chain with budget awareness

```python
# Prefer free tier on all, only burn paid credits as last resort.
results = fallback(
    ["gemini", "ollama", "claude", "codex"],   # order = preference
    "brief summary of the last commit",
    free_first=True,                            # pass 1: no API keys
    retry_paid=True,                            # pass 2: retry w/ keys
    timeout_s=90,
)
```

### Model override without building a CLISpec

```python
r = run("claude", "hi", model="sonnet")
r = run("gemini", "hi", model="gemini-2.5-flash")
r = run("ollama", "hi", model="kimi-k2.5")
```

### Custom spec for an exotic invocation

```python
from cliworker import CLISpec, run

spec = CLISpec(
    cli="claude",
    model="opus",
    fast=False,                    # disable CLAUDE_FAST (e.g., needs MCP tools)
    extra_args=["--allowedTools", "Bash,Read"],
    env_strip=[],                  # keep API key env vars intact
)
r = run(spec, "hi", timeout_s=300)
```

### Inspect what argv WOULD be sent, without running

```python
from cliworker import get_spec
spec = get_spec("claude", model="sonnet")
print(spec.build_argv("hello"))
# ['claude', '-p', '--model', 'sonnet', '--tools', '', '--no-chrome',
#  '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
#  '--no-session-persistence', 'hello']
```

### Clear skip-cache programmatically

```python
from cliworker.skipcache import clear
clear("claude")      # unsuppress claude
clear(None)          # nuke entire cache
```

---

## CLI reference

See the "Shell usage" section above for the natural-language invocation.
Every subcommand has `--help` with full examples:

```bash
cliworker --help                     # full overview
cliworker doctor --help
cliworker info --help
cliworker skip-cache --help
cliworker setup --help
```

---

## The techniques, explained

### `CLAUDE_FAST` flags

Five flags that disable everything claude-code loads on cold start:

```python
CLAUDE_FAST_FLAGS = [
    "--tools", "",                        # disable all tools
    "--no-chrome",                        # skip chrome-extension load
    "--strict-mcp-config",                # enforce the following MCP config
    "--mcp-config", '{"mcpServers":{}}',  # override MCP config to EMPTY
    "--no-session-persistence",           # skip session state I/O
]
```

The MCP override (`--strict-mcp-config` + empty `--mcp-config`) is the big one. If your `~/.claude/` has 4 MCP servers configured (starforge, atlassian, prodboost, etc.), each spins up on every call. Stripping them for one-shot subprocess calls saves 10+ seconds and doesn't lose anything — your main Claude Code session still has all of them.

**When to turn off (`fast=False`)**: if your prompt genuinely needs a specific MCP tool or session continuity. Rare in one-shot orchestrator calls.

### Gemini MCP strip-and-restore

Gemini CLI has no `--mcp-config` flag. cliworker monkey-patches at the filesystem level:

1. Back up `~/.gemini/settings.json` → `~/.gemini/settings.json.cliworker-bak`
2. Remove `mcpServers` key from the live file
3. Invoke `gemini -p ...`
4. Restore the backup — even if the subprocess raised

The context manager `gemini_stripped_mcp()` handles this with `try/finally`. If cliworker crashes mid-flight, the backup file is still on disk and can be manually restored.

### Subscription-mode-via-key-strip

Counter-intuitive discovery from navcom: **many LLM CLIs prefer your paid API key over your subscription when both are available.** Claude Code with `ANTHROPIC_API_KEY` set burns API credits instead of using your Claude.ai subscription.

The fix: strip the env var at call time:

```python
r = run("claude", "hi", strip_keys=True)
# Internally: env.pop("ANTHROPIC_API_KEY") before subprocess.run(..., env=env)
```

The stripped env vars are defined per-spec:

| CLI | Env vars stripped |
|---|---|
| claude | `ANTHROPIC_API_KEY` |
| codex | `OPENAI_API_KEY` |
| gemini | `GOOGLE_API_KEY`, `GEMINI_API_KEY` |
| ollama | (none — local, no subscription concept) |

`fallback()` uses this in its pass 1 by default (`free_first=True`), then retries with keys intact on pass 2 (`retry_paid=True`). Maximizes free-tier usage without losing reliability.

### Skip-cache

When a CLI fails (auth expired, subscription lapsed, quota hit), cliworker records it at `~/.cache/cliworker/skip-cache.json` with a timestamp. Next `run()` bails early with `skipped_reason="skip_cache"` if the entry is less than 1h old. Stale entries auto-clear.

You can inspect and clear the cache via `cliworker skip-cache [--clear <name>|ALL]` or programmatically via `cliworker.skipcache.{is_skipped, mark_broken, clear}`.

Respects `XDG_CACHE_HOME` if set.

### Prompt via stdin, instruction via argv

Best for long content. Keeps shell logs clean, avoids argv length limits:

```python
r = run(
    "claude",
    "Summarize this in 5 bullets. Ignore XML/tool noise.",   # short, goes to argv
    stdin_content=big_transcript,                             # long, goes to stdin
)
```

The CLI mode equivalent: `cliworker run claude -p "instruction" --stdin < file.txt`.

---

## Per-CLI recipes (what's baked into `KNOWN_CLIS`)

| CLI | argv template | fast flags | env strip | prompt transport |
|---|---|---|---|---|
| claude | `claude -p [--model M] [FAST_FLAGS] <prompt>` | ON | `ANTHROPIC_API_KEY` | positional |
| codex | `codex exec --dangerously-bypass-approvals-and-sandbox <prompt>` | off | `OPENAI_API_KEY` | positional |
| gemini | `gemini [-m M] -p <prompt>` + fs-level MCP strip | ON (filesystem hack) | `GOOGLE_API_KEY`, `GEMINI_API_KEY` | flag `-p` |
| ollama | `ollama run <model> <prompt>` | off | (none) | positional after model |

Run `cliworker info` to see the exact argv each one would build.

---

## Python API surface

```python
from cliworker import (
    run,            # call one CLI
    use,            # list of CLIs in order, first success wins
    CLIResult,      # dataclass: ok/stdout/stderr/duration_s/spec/argv
    CLISpec,        # dataclass: cli/model/fast/env_strip/...
    get_spec,       # look up spec by CLI name + optional overrides
    KNOWN_CLIS,     # dict of built-in specs
)

# Back-compat aliases (older names, same signatures):
from cliworker import fallback           # old name for `use`
from cliworker import run_cli            # old name for `run`
from cliworker import run_with_fallback  # old name for `use`
```

Sub-modules worth knowing about:

- `cliworker.fastflags` — `CLAUDE_FAST_FLAGS`, `gemini_stripped_mcp()` context manager.
- `cliworker.skipcache` — `is_skipped()`, `mark_broken()`, `clear()`.
- `cliworker.detect` — `detect()` returns presence info for every known CLI.
- `cliworker.registry` — `CLISpec`, `KNOWN_CLIS`, `get_spec()`.

---

## FAQ

**Q: Why not just use the LLM SDKs (anthropic, openai, google-generativeai)?**
A: SDKs bypass the user's subscription entirely and always burn API credits. cliworker deliberately uses the user's installed CLI (`claude -p`, `codex exec`, `gemini -p`) so paid subscriptions get used when available.

**Q: Why not use MCP / AiExecutors / some agent framework?**
A: Those are for building agents. cliworker is for orchestrating subprocess calls. Lower-level, smaller blast radius, zero lock-in. Use both if you want.

**Q: Doesn't stripping env vars in a subprocess leak somehow?**
A: No. `env.pop()` operates on a copy passed to `subprocess.run(env=...)` — your real shell env is untouched. Verified in `tests/test_core.py::test_run_cli_strip_keys_removes_env_var`.

**Q: What if I want to send text to a prompt and ALSO pipe content?**
A: cliworker uses stdin for `stdin_content`. If you need both, concatenate into one argument or feed via a file flag in `extra_args`. Most CLIs don't support both gracefully.

**Q: Can I use cliworker asynchronously?**
A: Not in 0.x. Spawn threads yourself if you need parallel calls — `concurrent.futures.ThreadPoolExecutor` works fine. A real async API is on the roadmap.

**Q: What about aider / continue / other CLIs?**
A: Easy to add — build your own `CLISpec` and call `run(spec, prompt)`. PRs welcome to add them to `KNOWN_CLIS`.

---

## Roadmap

- [ ] async API (`arun`, `afallback`)
- [ ] `cliworker doctor --probe` comparison table showing fast-flag impact per CLI
- [ ] streaming mode (subprocess `stdout` line-by-line) for long responses
- [ ] more CLIs in `KNOWN_CLIS`: aider, continue, sgpt
- [ ] retry-with-backoff for transient failures (different from skip-cache)

---

## Provenance

The techniques here were reverse-engineered from [navcom](https://pypi.org/project/navcom/) after a 31-iteration peer-review loop in a sibling project kept spending minutes waiting for cold starts. [paircode](https://pypi.org/project/paircode/) now depends on cliworker for all CLI invocations.

---

## License

MIT. See [LICENSE](LICENSE).
