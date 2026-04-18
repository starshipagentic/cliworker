"""Tests for CLISpec.build_argv + registry defaults."""
from __future__ import annotations

from cliworker.registry import KNOWN_CLIS, get_spec


def test_claude_argv_includes_fast_flags_and_positional_prompt():
    spec = get_spec("claude")
    argv = spec.build_argv("hello world")
    assert argv[0] == "claude"
    assert argv[1] == "-p"
    # Fast flags present
    assert "--strict-mcp-config" in argv
    assert '{"mcpServers":{}}' in argv
    assert "--no-chrome" in argv
    assert "--no-session-persistence" in argv
    assert "--tools" in argv
    # Prompt is last positional
    assert argv[-1] == "hello world"


def test_claude_argv_with_model_passes_model_flag():
    spec = get_spec("claude", model="sonnet")
    argv = spec.build_argv("hi")
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "sonnet"


def test_claude_argv_fast_off_drops_flags():
    spec = get_spec("claude", fast=False)
    argv = spec.build_argv("hi")
    assert "--strict-mcp-config" not in argv
    assert "--no-chrome" not in argv


def test_codex_argv_has_exec_and_dangerously_bypass():
    spec = get_spec("codex")
    argv = spec.build_argv("summarize this")
    assert argv[:2] == ["codex", "exec"]
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert argv[-1] == "summarize this"


def test_gemini_argv_uses_flag_for_prompt():
    spec = get_spec("gemini")
    argv = spec.build_argv("hi")
    # gemini uses -p <value>
    assert "-p" in argv
    # -p should be immediately followed by the prompt value
    assert argv[argv.index("-p") + 1] == "hi"


def test_gemini_argv_with_model_uses_m_flag():
    spec = get_spec("gemini", model="gemini-2.5-flash")
    argv = spec.build_argv("hi")
    assert "-m" in argv
    assert argv[argv.index("-m") + 1] == "gemini-2.5-flash"


def test_ollama_argv():
    """ollama expects `ollama run <model> <prompt>` — model is positional, no flag."""
    spec = get_spec("ollama", model="llama3.1")
    argv = spec.build_argv("hi")
    assert argv == ["ollama", "run", "llama3.1", "hi"]


def test_unknown_cli_gets_minimal_default():
    spec = get_spec("mystery-cli")
    argv = spec.build_argv("hi")
    assert argv[0] == "mystery-cli"
    # Positional prompt by default
    assert argv[-1] == "hi"


def test_known_clis_dict_has_expected_keys():
    assert set(KNOWN_CLIS.keys()) == {"claude", "codex", "gemini", "ollama"}


def test_env_strip_defaults():
    assert "ANTHROPIC_API_KEY" in KNOWN_CLIS["claude"].env_strip
    assert "OPENAI_API_KEY" in KNOWN_CLIS["codex"].env_strip
    assert "GOOGLE_API_KEY" in KNOWN_CLIS["gemini"].env_strip
