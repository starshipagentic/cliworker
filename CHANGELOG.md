# Changelog

## 0.6.0 — config/cache at ~/.cliworker/ (matches peer LLM CLIs) (breaking)

Default path changed. cliworker's state + cache now live at
`~/.cliworker/` — matching the dotfile-at-home convention of the tools
cliworker orchestrates (`~/.claude/`, `~/.codex/`, `~/.gemini/`,
`~/.ollama/`).

    ~/.cliworker/state.json        (was ~/.config/cliworker/state.json)
    ~/.cliworker/skip-cache.json   (was ~/.cache/cliworker/skip-cache.json)

XDG is still honored when the user opts in explicitly:

    if XDG_CONFIG_HOME is set → $XDG_CONFIG_HOME/cliworker/state.json
    if XDG_CACHE_HOME  is set → $XDG_CACHE_HOME/cliworker/skip-cache.json

This technically violates the XDG spec (which says unset XDG means
use `~/.config/`) but is the right call for a tool whose peers don't
respect XDG. Power users who set XDG explicitly get their preference;
everyone else gets peer-consistent `~/.cliworker/`.

Migration: no auto-migration. If you have state at the old path, move
it yourself — `mv ~/.config/cliworker ~/.cliworker` — or just re-run
`cliworker setup` to regenerate. No users known to exist yet.

5 new path-resolution tests in test_paths.py verify: default is
~/.cliworker/, XDG_CONFIG_HOME when set moves state, XDG_CACHE_HOME
when set moves cache, empty-string XDG falls back to dotfile-at-home,
and both paths co-located in one dir by default.

Total: 70 tests green.

## 0.5.5 — add CLI smoke tests that would have caught the 0.5.2 bugs

Honest gap: 0.5.2 shipped with 41 passing tests, but none of them
invoked `cliworker` end-to-end. All tests mocked `subprocess.run`.
That's why the broken ollama default (`llama3.1` instead of `gemma3:4b`)
slipped through, and why nothing noticed that `doctor --probe` wasn't
stripping API keys.

Added `tests/test_cli_smoke.py` with 24 end-to-end tests that invoke the
real CLI via `CliRunner`:

- Help-lint: every registered command (`--help`, `-h`, `--version`,
  and each subcommand + `--help`) is parametrized and must exit 0 with
  non-empty output.
- Main-help content: must list all four public subcommands AND all
  bare-prompt flags (`--use`, `--paid-ok`, `--timeout`, `--model`).
- Default specs: `ollama.model == 'gemma3:4b'`, `state.DEFAULT_OLLAMA_MODEL`
  matches the registry value (catches drift between the two sources),
  claude default has `fast=True`, every advertised CLI in `KNOWN_CLIS`.
- Dispatch: bare prompt → `_ask`, `use cli1 cli2` → those two in order,
  default passes `paid_ok=None` (free-only).
- `doctor --probe` E2E: monkeypatches `run()` + `detect()`, verifies all
  four CLIs probed AND every probe call has `strip_keys=True`.
- Error rewrite: ollama "invalid model name" must become actionable
  "Run: ollama pull gemma3:4b" hint.
- `skip-cache` subcommand exit codes + output.

Total now: 65 tests green. Each one a regression-canary for something
the prior suite let through.

## 0.5.3 — fix default ollama model + probe behavior

Three real bugs surfaced by actually running every command end-to-end:

- **ollama default model is now `gemma3:4b`** (was `llama3.1`). This is the
  model navcom uses (`navcom.py:27: SUMMARY_MODEL_DEFAULT = "gemma3:4b"`)
  and the one already on the dev machine. Picking an unpulled default
  was causing `doctor --probe` to fail with cryptic "invalid model name".
  Fixed in `registry.py` (`CLISpec(..., model="gemma3:4b", ...)`), in
  `state.py` (`DEFAULT_OLLAMA_MODEL`), and in `runner.py` default arg.
- **`cliworker doctor --probe` now strips API keys by default.** Probes
  test the subscription path (matching cliworker's free-first default)
  instead of trying paid API. Also auto-clears skip-cache at the start
  of a probe run so stale failures don't mask current state.
- **Better ollama error message** when a model isn't pulled. Previously
  just bubbled up ollama's cryptic `Error: invalid model name`. Now
  cliworker recognizes that specific error and rewrites it:
  `ollama model 'gemma3:4b' not pulled. Run: ollama pull gemma3:4b`.

Verified by hand: `cliworker doctor --probe --probe-timeout 60` now
shows all four CLIs succeeding (claude 4.1s, codex 7.2s, gemini 6.5s,
ollama 0.5s) on this machine.

41 tests still green.

## 0.5.0 — free-by-default, paid opt-in (breaking)

Flipped the default: cliworker now never uses paid API fallback unless
you explicitly allow it. Surprise-billing avoided.

### Python API (breaking)

`use()` kwargs `free_first` and `retry_paid` are gone. Single new kwarg:
`paid_ok`.

    use(clis, prompt)                       # free/subscription only (default)
    use(clis, prompt, paid_ok=True)         # paid OK for every CLI
    use(clis, prompt, paid_ok=["claude"])   # paid OK only for claude
    use(clis, prompt, paid_ok=False)        # explicit form of default

Pass 2 (paid API) runs only for the CLIs you authorized. CLIs NOT in
`paid_ok` never get their env key handed to them.

### CLI (breaking)

`--no-paid` is gone. New flag `--paid-ok`:

    cliworker "hi"                         # free only
    cliworker "hi" --paid-ok all           # paid OK for every CLI
    cliworker "hi" --paid-ok claude,codex  # paid OK only for those
    cliworker "hi" use claude --paid-ok claude   # fine-grained with `use`

The flag overrides the persistent state.json setting for one invocation.

### First-run

After the CLI scan, first-run now prompts:

    Paid API fallback
    When a CLI's subscription/free tier fails, cliworker can optionally fall
    back to paid API (using $ANTHROPIC_API_KEY, etc.). By default this is OFF.

      Allow paid API fallback for any CLIs now? [y/N]:
        y → asks "Which CLIs? (comma-separated, or 'all')"
        n → saved as paid_ok=None (stays free forever)

Saved to ~/.config/cliworker/state.json as `paid_ok: null | true | ["cli",...]`.
Edit that file any time to change it — or re-run `cliworker setup`.

### Tests

41 green. 4 new tests covering paid_ok default-off, paid_ok=True runs
both passes for all, paid_ok=list restricts pass 2 to the listed CLIs,
paid_ok=False matches default behavior.

## 0.4.0 — remove back-compat aliases (breaking)

Dropped: `fallback()`, `run_cli()`, `run_with_fallback()`. The public Python
API is now exactly `run()` and `use()`. Code using the old names must
rename to the new ones — no silent coexistence.

Every reference to "fallback" as a function scrubbed from source, tests,
and docs. The concept (try these in order, fall through on failure) is
still how `use()` works; we just stopped using the word as a function
name.

## 0.3.0 — natural-language CLI, one-word verb, first-run diagnostics

The CLI should feel like a tool, not a library's skin. 0.3.0 makes that real.

### Shell — no more `run`, no more `-p`

    cliworker "what is TCP?"                  default chain, just the prompt
    cliworker "what is TCP?" use claude       specific CLI
    cliworker "what is TCP?" use claude gemini  chain in stated order
    cliworker --use claude,gemini "hi"        flag form
    cliworker "hi" --no-paid                  only try subscription mode
    cliworker "hi" -m sonnet                  model override (optional)
    cliworker "hi" -v                         show winner + duration

Prompt is always positional. No verb needed. `use` is the only connector
word (flag form `--use` or `--llm` for scripts that prefer flags).

### Library — `fallback` → `use`

    from cliworker import run, use
    run("claude", "hi")
    use(["claude","codex"], "hi")   # was: fallback(...)

`fallback` kept as a back-compat alias.

### First-run experience

Typing `cliworker "..."` for the first time shows an ASCII banner, scans
PATH for installed CLIs, prints an actionable install command for each
missing one, checks if ollama has the default model pulled (and prints
`ollama pull llama3.1` if not), then saves state to
~/.config/cliworker/state.json so subsequent runs skip the check.

Added `cliworker setup` command to re-run diagnostics on demand (never
auto-installs — just prints what to run).

### Also

- State file at ~/.config/cliworker/state.json with detected CLIs + default chain.
- Custom Click group that treats any non-option first arg as a prompt
  unless it matches a known subcommand (doctor/info/skip-cache/setup).
- argv preprocessor converts `... use cli1 cli2` → `--use cli1,cli2`.
- `cliworker --help` shows all the natural-language examples up front.
- 37 tests green (10 new for argv preprocessor + first-run edge cases).

## 0.2.0 — clarity pass: API + CLI + docs

- **New primary API names**: `run()` and `fallback()`. Old names `run_cli()`
  and `run_with_fallback()` remain as back-compat aliases.
- `run()` now accepts `model=` and `fast=` kwargs directly — no CLISpec
  dataclass dance needed for simple overrides.
- `fallback()` uses clearer kwargs: `free_first`, `retry_paid` (replacing
  `strip_keys_first`, `retry_with_keys`).
- **CLI overhaul** with per-command `--help` that documents examples, exit
  codes, and when to use each command.
  - `run` / `fallback` replace the old `run` / `chain` (`chain` was unclear).
  - New `doctor` command: detects installed CLIs, optional `--probe` invokes
    each with a "hi" prompt to compare cold-start times.
  - `list` → `info` (takes optional CLI name to show just one).
  - `skip-cache` shows human-readable age + "clears in" countdown.
- **Much bigger README** with mental-model section, cookbook of real
  examples, FAQ, per-CLI recipe table, and technique explanations.
- `__init__.py` now has a module-level docstring explaining the pattern.
- `detect` module surfaces all installed CLIs with install hints.
- 27 tests still green (back-compat aliases covered).

## 0.1.0 — initial extract from navcom

Extracted the production-hardened CLI-calling patterns from navcom into a
reusable library.

- `CLISpec` + `KNOWN_CLIS` registry for claude / codex / gemini / ollama
- `run_cli(spec, prompt, ...)` — one-shot invocation with timeout handling
- `run_with_fallback(specs, prompt, ...)` — subscription-first fallback chain
- `CLAUDE_FAST_FLAGS` constants (tools/chrome/MCP/session-persistence bypasses)
- `gemini_stripped_mcp()` context manager (backup → strip → restore)
- `skipcache` module with 1h-TTL broken-engine cache
- `cliworker` CLI (list, run, chain, skip-cache subcommands)
- 26 tests green
