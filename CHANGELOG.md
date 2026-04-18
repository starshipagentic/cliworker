# Changelog

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
