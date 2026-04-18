"""Tests for argv preprocessor + bare-prompt dispatch (v0.7.0+ `run` keyword)."""
from __future__ import annotations

from click.testing import CliRunner

from cliworker.cli import _rewrite_run_keyword, main


def test_rewrite_simple_run():
    argv = ['what is TCP?', 'run', 'claude']
    assert _rewrite_run_keyword(argv) == ['what is TCP?', '--run', 'claude']


def test_rewrite_multi_cli_run():
    argv = ['do stuff', 'run', 'claude', 'gemini', 'ollama']
    assert _rewrite_run_keyword(argv) == ['do stuff', '--run', 'claude,gemini,ollama']


def test_rewrite_run_with_following_flag():
    argv = ['hi', 'run', 'claude', 'gemini', '--fast']
    assert _rewrite_run_keyword(argv) == ['hi', '--run', 'claude,gemini', '--fast']


def test_rewrite_run_with_nothing_after_is_passthrough():
    """If 'run' appears but no CLI names follow, leave it — might be a prompt containing 'run'."""
    argv = ['how do I run grep']  # 'run' embedded in quoted prompt
    assert _rewrite_run_keyword(argv) == argv


def test_rewrite_run_with_only_flag_after():
    argv = ['hi', 'run', '--verbose']
    assert _rewrite_run_keyword(argv) == argv


def test_rewrite_no_run_keyword():
    argv = ['hi', '--model', 'sonnet']
    assert _rewrite_run_keyword(argv) == argv


def test_help_shows_simple_invocation_examples():
    runner = CliRunner()
    result = runner.invoke(main, ['--help'])
    assert result.exit_code == 0
    assert 'cliworker "what is TCP?"' in result.output
    assert 'run claude gemini' in result.output


def test_info_subcommand_still_works():
    runner = CliRunner()
    result = runner.invoke(main, ['info', 'claude'])
    assert result.exit_code == 0
    assert 'cli binary:' in result.output
    assert 'claude' in result.output


def test_doctor_subcommand_still_works():
    runner = CliRunner()
    result = runner.invoke(main, ['doctor'])
    assert result.exit_code == 0
    assert 'claude' in result.output


def test_bare_prompt_without_clis_installed_exits_gracefully(tmp_path, monkeypatch):
    """If no CLIs are installed, first-run should fail fast with install hints."""
    from pathlib import Path

    fake_config = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_config))

    from cliworker.detect import CLIPresence

    def fake_detect():
        return {
            name: CLIPresence(
                name=name, binary=name, binary_path=None,
                config_dir=Path.home() / f".{name}", installed=False, install_hint="hint",
            )
            for name in ("claude", "codex", "gemini", "ollama")
        }

    monkeypatch.setattr("cliworker.firstrun.detect", fake_detect)

    runner = CliRunner()
    result = runner.invoke(main, ['hello'])
    assert result.exit_code != 0, f"expected nonzero exit; got {result.exit_code}"
    haystack = result.output.lower()
    assert any(
        needle in haystack
        for needle in ("install", "npm i", "no llm", "cliworker", "claude")
    ), f"output was: {result.output!r}"
