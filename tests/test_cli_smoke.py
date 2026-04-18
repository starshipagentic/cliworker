"""End-to-end CLI smoke tests (v0.7.0+ API shape).

Invoke the cliworker CLI via click's CliRunner and assert on real output.
Catches regressions unit tests miss — missing defaults, broken subcommands,
click routes that stopped registering.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in ("doctor", "info", "setup", "skip-cache"):
        assert cmd in result.output, f"Main --help missing subcommand: {cmd}"


def test_main_help_documents_bare_prompt_flags():
    """Flags on the hidden _ask dispatcher must still be documented in the
    main help epilog."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for flag in ("--run", "--fast", "--paid-ok", "--timeout", "--model"):
        assert flag in result.output, f"Main --help missing flag doc: {flag}"


# ---------------------------------------------------------------------------
# Default specs
# ---------------------------------------------------------------------------

def test_ollama_default_model_is_gemma34b():
    """Regression: ollama default model is gemma3:4b (navcom's pick)."""
    spec = get_spec("ollama")
    assert spec.model == "gemma3:4b", (
        f"ollama default model is {spec.model!r}, expected 'gemma3:4b'. "
        "Keep in sync with state.DEFAULT_OLLAMA_MODEL."
    )
    argv = spec.build_argv("PROMPT")
    assert "gemma3:4b" in argv


def test_state_default_ollama_model_matches_registry():
    """The two sources of the ollama default must not drift apart."""
    from cliworker.state import DEFAULT_OLLAMA_MODEL

    spec_model = get_spec("ollama").model
    assert DEFAULT_OLLAMA_MODEL == spec_model, (
        f"DEFAULT_OLLAMA_MODEL={DEFAULT_OLLAMA_MODEL!r} but "
        f"registry ollama spec model={spec_model!r}"
    )


def test_claude_default_spec_is_full_mode():
    """Regression: as of v0.7.0, claude default spec has fast=False
    (full mode). Fast is opt-in via run_fast() or --fast."""
    spec = get_spec("claude")
    assert spec.fast is False, (
        "claude default spec must have fast=False as of v0.7.0. "
        "Fast mode is opt-in."
    )
    argv = spec.build_argv("PROMPT")
    assert "--strict-mcp-config" not in argv, (
        "Default argv must NOT include CLAUDE_FAST flags — fast is opt-in."
    )


def test_claude_fast_mode_applies_claude_fast_flags():
    """When fast=True is set (via run_fast or explicit spec), CLAUDE_FAST
    flags land in argv."""
    from dataclasses import replace

    spec = replace(get_spec("claude"), fast=True)
    argv = spec.build_argv("PROMPT")
    assert "--strict-mcp-config" in argv
    assert '{"mcpServers":{}}' in argv
    assert "--no-chrome" in argv


def test_every_cli_has_a_known_spec():
    from cliworker.registry import KNOWN_CLIS

    for name in ("claude", "codex", "gemini", "ollama"):
        assert name in KNOWN_CLIS
        spec = get_spec(name)
        assert spec.cli == name
        argv = spec.build_argv("PROMPT")
        assert argv[0] == name


# ---------------------------------------------------------------------------
# CLI dispatch — subcommands
# ---------------------------------------------------------------------------

def test_bare_invocation_no_args_prints_help():
    runner = CliRunner()
    result = runner.invoke(main, [])
    assert result.exit_code == 0
    assert "cliworker" in result.output.lower()


def test_info_subcommand_shows_argv_recipe_full_mode():
    """Default claude info shows full-mode argv (no CLAUDE_FAST flags)."""
    runner = CliRunner()
    result = runner.invoke(main, ["info", "claude"])
    assert result.exit_code == 0
    assert "sample argv" in result.output
    # In full mode (new default), CLAUDE_FAST flags should NOT appear
    assert "--strict-mcp-config" not in result.output


def test_info_ollama_shows_gemma_default():
    runner = CliRunner()
    result = runner.invoke(main, ["info", "ollama"])
    assert result.exit_code == 0
    assert "gemma3:4b" in result.output


def test_doctor_subcommand_runs_without_probe():
    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
    for name in ("claude", "codex", "gemini", "ollama"):
        assert name in result.output


def test_doctor_probe_uses_subscription_mode(monkeypatch):
    """E2E: doctor --probe must go through run() which always strips keys
    in pass 1. We intercept run() and verify every CLI was probed."""
    captured: list[dict] = []

    def fake_run(prompt, *clis, **kwargs):
        from cliworker.core import CLIResult

        # There should be exactly one CLI per probe call
        spec = clis[0] if clis else None
        cli_name = spec if isinstance(spec, str) else (spec.cli if spec else "?")
        captured.append({
            "prompt": prompt,
            "cli_name": cli_name,
            "fast": kwargs.get("fast"),
            "timeout_s": kwargs.get("timeout_s"),
        })
        return [CLIResult(
            spec=get_spec(cli_name) if isinstance(cli_name, str) else spec,
            ok=True, stdout="ok", stderr="", duration_s=0.1,
            returncode=0, argv=[], skipped_reason=None,
        )]

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
    probed = {c["cli_name"] for c in captured}
    assert probed == {"claude", "codex", "gemini", "ollama"}, (
        f"Expected all four probed, got: {probed}"
    )


def test_doctor_probe_with_fast_flag_sets_fast_true(monkeypatch):
    """`doctor --probe --fast` should pass fast=True into run()."""
    captured: list[dict] = []

    def fake_run(prompt, *clis, **kwargs):
        from cliworker.core import CLIResult

        spec = clis[0]
        cli_name = spec if isinstance(spec, str) else spec.cli
        captured.append({"fast": kwargs.get("fast")})
        return [CLIResult(
            spec=get_spec(cli_name),
            ok=True, stdout="ok", stderr="", duration_s=0.1,
            returncode=0, argv=[], skipped_reason=None,
        )]

    from cliworker.detect import CLIPresence

    def fake_detect():
        return {
            "claude": CLIPresence(
                name="claude", binary="claude", binary_path=Path("/fake/claude"),
                config_dir=Path.home() / ".claude", installed=True, install_hint="",
            ),
        }

    monkeypatch.setattr("cliworker.cli.detect", fake_detect)
    monkeypatch.setattr("cliworker.cli.run", fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--probe", "--fast"])
    assert result.exit_code == 0
    assert all(c["fast"] is True for c in captured), (
        f"doctor --probe --fast must pass fast=True; got: {captured}"
    )


def test_skip_cache_show_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(main, ["skip-cache"])
    assert result.exit_code == 0
    assert "empty" in result.output.lower() or "Skip-cache" in result.output


def test_skip_cache_clear_all_cli_runs_cleanly():
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
# Bare-prompt dispatch — `cliworker "hi" run claude` goes through _ask.
# ---------------------------------------------------------------------------

def _seed_state(tmp_path, monkeypatch, chain=("claude",), paid_ok=None):
    """Set up a tmp state.json with the given default chain."""
    import cliworker.state as st

    state_dir = tmp_path / "cfg" / "cliworker"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(json.dumps({
        "first_run_at": "2026-01-01T00:00:00",
        "detected_clis": {name: True for name in chain},
        "default_chain": list(chain),
        "paid_ok": paid_ok,
    }))
    monkeypatch.setattr(st, "state_path", lambda: state_dir / "state.json")


def test_bare_prompt_with_explicit_run_uses_those_clis(tmp_path, monkeypatch):
    """E2E: `cliworker "hi" run claude gemini` dispatches to _ask,
    preprocessor rewrites `run` → `--run`, calls run() with those CLIs."""
    _seed_state(tmp_path, monkeypatch, chain=("claude", "codex", "gemini", "ollama"))

    recorded: dict = {}

    def fake_run(prompt, *specs, **kwargs):
        from cliworker.core import CLIResult

        recorded["cli_names"] = [s.cli if hasattr(s, "cli") else s for s in specs]
        recorded["prompt"] = prompt
        recorded["paid_ok"] = kwargs.get("paid_ok")
        recorded["fast"] = kwargs.get("fast")
        return [CLIResult(
            spec=specs[0], ok=True, stdout="mocked", stderr="",
            duration_s=0.1, returncode=0, argv=[], skipped_reason=None,
        )]

    monkeypatch.setattr("cliworker.cli.run", fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["hi there", "run", "claude", "gemini"])
    assert result.exit_code == 0, f"bare prompt failed:\n{result.output}"
    assert recorded["cli_names"] == ["claude", "gemini"]
    assert "hi there" in (recorded["prompt"] or "")


def test_bare_prompt_default_is_full_mode_and_not_paid(tmp_path, monkeypatch):
    """Defaults: full mode (fast=None, respects spec default which is False),
    paid_ok=None (free only)."""
    _seed_state(tmp_path, monkeypatch, chain=("claude",))

    recorded: dict = {}

    def fake_run(prompt, *specs, **kwargs):
        from cliworker.core import CLIResult

        recorded["paid_ok"] = kwargs.get("paid_ok")
        recorded["fast"] = kwargs.get("fast")
        return [CLIResult(
            spec=specs[0], ok=True, stdout="ok", stderr="",
            duration_s=0.1, returncode=0, argv=[], skipped_reason=None,
        )]

    monkeypatch.setattr("cliworker.cli.run", fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["hello"])
    assert result.exit_code == 0
    # Default: paid_ok must be None or False (never True, never a list)
    assert recorded.get("paid_ok") in (None, False)
    # Default: fast must be None (respect spec default) — NOT True
    assert recorded.get("fast") in (None, False), (
        f"Default bare prompt must NOT force fast=True; got {recorded.get('fast')!r}"
    )


def test_bare_prompt_fast_flag_sets_fast_true(tmp_path, monkeypatch):
    """`cliworker "hi" --fast` must pass fast=True to run()."""
    _seed_state(tmp_path, monkeypatch, chain=("claude",))

    recorded: dict = {}

    def fake_run(prompt, *specs, **kwargs):
        from cliworker.core import CLIResult

        recorded["fast"] = kwargs.get("fast")
        return [CLIResult(
            spec=specs[0], ok=True, stdout="ok", stderr="",
            duration_s=0.1, returncode=0, argv=[], skipped_reason=None,
        )]

    monkeypatch.setattr("cliworker.cli.run", fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["hello", "--fast"])
    assert result.exit_code == 0
    assert recorded.get("fast") is True, (
        f"--fast flag must set fast=True; got {recorded.get('fast')!r}"
    )


# ---------------------------------------------------------------------------
# Ollama error rewrite
# ---------------------------------------------------------------------------

def test_ollama_invalid_model_name_gets_rewritten(monkeypatch):
    """The cryptic `Error: invalid model name` must become an actionable
    `ollama pull <model>` hint."""
    from cliworker import run

    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)

    def fake_subprocess_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args, returncode=1,
            stdout="", stderr="Error: invalid model name gemma3:4b",
        )

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_subprocess_run)

    results = run("hi", "ollama")
    assert len(results) == 1
    r = results[0]
    assert r.ok is False
    assert "ollama pull" in r.stderr, f"expected 'ollama pull' hint; got {r.stderr!r}"
    assert "gemma3:4b" in r.stderr
