# Changelog

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
