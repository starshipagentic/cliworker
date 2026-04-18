# Changelog

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
