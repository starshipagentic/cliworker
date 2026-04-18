"""End-to-end CLI smoke tests.

These invoke the cliworker CLI via click's CliRunner (same in-process path
users hit from the shell) and assert on real output. Catches regressions
that unit tests miss — like a missing default, a broken subcommand, or
click route that no longer registers.

Every new CLI subcommand should add a test here that at least runs --help
and confirms a non-crash.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cliworker.cli import main
from cliworker import get_spec


# ---------------------------------------------------------------------------
# Help-lint: every registered command must have a working --help.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cmd_argv",
    [
        ["--help"],
        ["-h"],
        ["--version"],
        ["doctor", "--help"],
        ["info", "--help"],
        ["setup", "--help"],
        ["skip-cache", "--help"],
    ],
)
def test_every_command_help_is_reachable(cmd_argv):
    """If click registration breaks, this catches it at the top level."""
    runner = CliRunner()
    result = runner.invoke(main, cmd_argv)
    assert result.exit_code == 0, f"{cmd_argv} exited {result.exit_code}:\n{result.output}"
    assert result.output.strip(), f"{cmd_argv} printed nothing"


def test_main_help_lists_every_public_subcommand():
    """Regression: main --help must show all four public subcommands."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in ("doctor", "info", "setup", "skip-cache"):
        assert cmd in result.output, f"Main --help missing subcommand: {cmd}"


def test_main_help_documents_bare_prompt_flags():
    """Regression: flags on the hidden _ask dispatcher must still be
    documented in the main help epilog. This was broken before v0.5.2."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for flag in ("--use", "--paid-ok", "--timeout", "--model"):
        assert flag in result.output, f"Main --help missing flag doc: {flag}"


# ---------------------------------------------------------------------------
# Default specs — catches forgetting to set a default like we did with ollama.
# ---------------------------------------------------------------------------

def test_ollama_default_model_is_gemma34b():
    """Regression: ollama default model is gemma3:4b (navcom's pick), not
    llama3.1. If this ever changes, update BOTH registry.py and state.py."""
    spec = get_spec("ollama")
    assert spec.model == "gemma3:4b", (
        f"ollama default model is {spec.model!r}, expected 'gemma3:4b'. "
        "Keep in sync with state.DEFAULT_OLLAMA_MODEL."
    )
    # Also verify the argv includes the model — without a default, ollama run
    # has no model and fails with cryptic 'invalid model name'.
    argv = spec.build_argv("PROMPT")
    assert "gemma3:4b" in argv, f"argv {argv} missing ollama default model"


def test_state_default_ollama_model_matches_registry():
    """The two sources of the ollama default must never drift apart."""
    from cliworker.state import DEFAULT_OLLAMA_MODEL

    spec_model = get_spec("ollama").model
    assert DEFAULT_OLLAMA_MODEL == spec_model, (
        f"DEFAULT_OLLAMA_MODEL={DEFAULT_OLLAMA_MODEL!r} but "
        f"registry ollama spec model={spec_model!r}"
    )


def test_claude_default_spec_has_fast_flags_on():
    """Regression: claude default spec must have fast=True so CLAUDE_FAST
    gets applied. Forgetting this regresses the 18s → 4s speedup."""
    spec = get_spec("claude")
    assert spec.fast is True, "claude default spec must have fast=True"
    argv = spec.build_argv("PROMPT")
    assert "--strict-mcp-config" in argv, "claude fast flags missing from argv"
    assert '{"mcpServers":{}}' in argv, "claude empty-mcp-config flag missing"


def test_every_cli_has_a_known_spec():
    """All four advertised CLIs have specs. Catches typos in KNOWN_CLIS."""
    from cliworker.registry import KNOWN_CLIS

    for name in ("claude", "codex", "gemini", "ollama"):
        assert name in KNOWN_CLIS, f"{name} missing from KNOWN_CLIS"
        spec = get_spec(name)
        assert spec.cli == name
        # Must be able to build argv without blowing up
        argv = spec.build_argv("PROMPT")
        assert argv[0] == name


# ---------------------------------------------------------------------------
# CLI dispatch — bare prompt + subcommands.
# ---------------------------------------------------------------------------

def test_bare_invocation_no_args_prints_help():
    runner = CliRunner()
    result = runner.invoke(main, [])
    assert result.exit_code == 0
    assert "cliworker" in result.output.lower()


def test_info_subcommand_shows_argv_recipe():
    runner = CliRunner()
    result = runner.invoke(main, ["info", "claude"])
    assert result.exit_code == 0
    assert "sample argv" in result.output
    assert "--strict-mcp-config" in result.output


def test_info_ollama_shows_gemma_default():
    """E2E: cliworker info ollama should mention gemma3:4b in sample argv."""
    runner = CliRunner()
    result = runner.invoke(main, ["info", "ollama"])
    assert result.exit_code == 0
    assert "gemma3:4b" in result.output, (
        "cliworker info ollama must show gemma3:4b in sample argv. "
        f"Got:\n{result.output}"
    )


def test_doctor_subcommand_runs_without_probe():
    """Fast-scan mode: doctor with no --probe must never invoke subprocess."""
    runner = CliRunner()
    # Even if subprocess.run were somehow called, we'd know from the timing
    # but a more direct check: the output should list the four known CLIs.
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
    for name in ("claude", "codex", "gemini", "ollama"):
        assert name in result.output


def test_doctor_probe_runs_subscription_path(monkeypatch):
    """E2E: doctor --probe must call run() with strip_keys=True so it tests
    the subscription path, not paid API. Regression from v0.5.2 → v0.5.3."""
    captured: list[dict] = []

    def fake_run(spec, prompt, **kwargs):
        from cliworker.core import CLIResult

        captured.append({
            "spec_cli": spec if isinstance(spec, str) else spec.cli,
            "prompt": prompt,
            "strip_keys": kwargs.get("strip_keys", False),
        })
        return CLIResult(
            spec=get_spec(spec if isinstance(spec, str) else spec.cli),
            ok=True, stdout="ok", stderr="", duration_s=0.1,
            returncode=0, argv=[], skipped_reason=None,
        )

    # Stub detection so each CLI appears installed.
    from cliworker.detect import CLIPresence

    def fake_detect():
        return {
            name: CLIPresence(
                name=name, binary=name, binary_path=Path(f"/fake/{name}"),
                config_dir=Path.home() / f".{name}", installed=True,
                install_hint="",
            )
            for name in ("claude", "codex", "gemini", "ollama")
        }

    monkeypatch.setattr("cliworker.cli.detect", fake_detect)
    monkeypatch.setattr("cliworker.cli.run", fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--probe", "--probe-timeout", "5"])
    assert result.exit_code == 0, f"doctor --probe failed:\n{result.output}"
    # All four should have been probed
    probed = {c["spec_cli"] for c in captured}
    assert probed == {"claude", "codex", "gemini", "ollama"}, (
        f"Expected all four probed, got: {probed}"
    )
    # Every probe must have strip_keys=True (subscription mode)
    assert all(c["strip_keys"] is True for c in captured), (
        f"doctor --probe must use strip_keys=True; got: {captured}"
    )


def test_skip_cache_show_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(main, ["skip-cache"])
    assert result.exit_code == 0
    assert "empty" in result.output.lower() or "Skip-cache" in result.output


def test_skip_cache_clear_all_cli_runs_cleanly():
    """The `skip-cache --clear ALL` CLI path must exit 0 and say what it did.
    (Filesystem-level clear behavior is covered in test_skipcache.py.)"""
    runner = CliRunner()
    result = runner.invoke(main, ["skip-cache", "--clear", "ALL"])
    assert result.exit_code == 0
    assert "cleared" in result.output.lower()


def test_skip_cache_clear_specific_cli_runs_cleanly():
    runner = CliRunner()
    result = runner.invoke(main, ["skip-cache", "--clear", "claude"])
    assert result.exit_code == 0
    assert "claude" in result.output.lower()


# ---------------------------------------------------------------------------
# Bare-prompt dispatch — cliworker "hi" goes through _ask.
# ---------------------------------------------------------------------------

def test_bare_prompt_with_explicit_use_uses_those_clis(tmp_path, monkeypatch):
    """E2E: `cliworker "hi" use claude` dispatches the prompt through _ask,
    passes use_csv correctly, and invokes use() with the right CLI names."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # Pre-populate state so first-run diagnostics don't fire in the test
    from cliworker import state

    state_dir = tmp_path / "cfg" / "cliworker"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(json.dumps({
        "first_run_at": "2026-01-01T00:00:00",
        "detected_clis": {"claude": True, "codex": True, "gemini": True, "ollama": True},
        "default_chain": ["claude"],
        "paid_ok": None,
    }))
    # Point state module at our fake XDG location
    monkeypatch.setattr(
        state, "state_path",
        lambda: tmp_path / "cfg" / "cliworker" / "state.json",
    )

    recorded: dict = {}

    def fake_use(specs, prompt, **kwargs):
        from cliworker.core import CLIResult

        recorded["cli_names"] = [s.cli if hasattr(s, "cli") else s for s in specs]
        recorded["prompt"] = prompt
        recorded["paid_ok"] = kwargs.get("paid_ok")
        return [CLIResult(
            spec=specs[0], ok=True, stdout="mocked answer", stderr="",
            duration_s=0.1, returncode=0, argv=[], skipped_reason=None,
        )]

    monkeypatch.setattr("cliworker.cli.use", fake_use)

    runner = CliRunner()
    result = runner.invoke(main, ["hi there", "use", "claude", "gemini"])
    assert result.exit_code == 0, f"bare prompt failed:\n{result.output}"
    assert recorded["cli_names"] == ["claude", "gemini"], (
        f"Expected ['claude','gemini'], got {recorded['cli_names']}"
    )
    assert "hi there" in (recorded["prompt"] or "")


def test_bare_prompt_default_has_paid_ok_none(tmp_path, monkeypatch):
    """Regression: default must never fall through to paid API."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from cliworker import state

    state_dir = tmp_path / "cfg" / "cliworker"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(json.dumps({
        "first_run_at": "2026-01-01T00:00:00",
        "detected_clis": {"claude": True},
        "default_chain": ["claude"],
        "paid_ok": None,
    }))
    monkeypatch.setattr(
        state, "state_path",
        lambda: tmp_path / "cfg" / "cliworker" / "state.json",
    )

    recorded: dict = {}

    def fake_use(specs, prompt, **kwargs):
        from cliworker.core import CLIResult

        recorded["paid_ok"] = kwargs.get("paid_ok")
        return [CLIResult(
            spec=specs[0], ok=True, stdout="ok", stderr="",
            duration_s=0.1, returncode=0, argv=[], skipped_reason=None,
        )]

    monkeypatch.setattr("cliworker.cli.use", fake_use)

    runner = CliRunner()
    result = runner.invoke(main, ["hello"])
    assert result.exit_code == 0
    assert recorded.get("paid_ok") in (None, False), (
        f"Default bare prompt must pass paid_ok=None/False, got {recorded.get('paid_ok')!r}"
    )


# ---------------------------------------------------------------------------
# Error rewrites — ollama "invalid model name" should get actionable hint.
# ---------------------------------------------------------------------------

def test_ollama_invalid_model_name_gets_rewritten(monkeypatch):
    """Regression: the cryptic `Error: invalid model name` from ollama must
    become `ollama model 'X' not pulled. Run: ollama pull X`."""
    from cliworker import run
    from cliworker.core import _run_impl

    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)

    def fake_subprocess_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args, returncode=1,
            stdout="", stderr="Error: invalid model name gemma3:4b",
        )

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_subprocess_run)

    result = run("ollama", "hi")
    assert result.ok is False
    assert "ollama pull" in result.stderr, (
        f"Expected actionable 'ollama pull' hint, got: {result.stderr!r}"
    )
    assert "gemma3:4b" in result.stderr, (
        f"Error should name the specific model, got: {result.stderr!r}"
    )
