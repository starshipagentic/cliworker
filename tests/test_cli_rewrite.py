"""Tests for argv preprocessor + bare-prompt dispatch."""
from __future__ import annotations

from click.testing import CliRunner

from cliworker.cli import _rewrite_use_keyword, main


def test_rewrite_simple_use():
    argv = ['what is TCP?', 'use', 'claude']
    assert _rewrite_use_keyword(argv) == ['what is TCP?', '--use', 'claude']


def test_rewrite_multi_cli_use():
    argv = ['do stuff', 'use', 'claude', 'gemini', 'ollama']
    assert _rewrite_use_keyword(argv) == ['do stuff', '--use', 'claude,gemini,ollama']


def test_rewrite_use_with_following_flag():
    argv = ['hi', 'use', 'claude', 'gemini', '--verbose']
    assert _rewrite_use_keyword(argv) == ['hi', '--use', 'claude,gemini', '--verbose']


def test_rewrite_use_with_nothing_after_is_passthrough():
    """If 'use' appears but no CLI names follow, leave it — probably part of the prompt."""
    argv = ['how do I use grep']  # 'use' embedded, not a bare arg
    assert _rewrite_use_keyword(argv) == argv


def test_rewrite_use_with_only_flag_after():
    argv = ['hi', 'use', '--verbose']
    assert _rewrite_use_keyword(argv) == argv


def test_rewrite_no_use_keyword():
    argv = ['hi', '--model', 'sonnet']
    assert _rewrite_use_keyword(argv) == argv


def test_help_shows_simple_invocation_examples():
    runner = CliRunner()
    result = runner.invoke(main, ['--help'])
    assert result.exit_code == 0
    assert 'cliworker "what is TCP?"' in result.output
    assert 'use claude gemini' in result.output


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
    # Should show ✓ or ✗ for each known CLI
    assert 'claude' in result.output


def test_bare_prompt_without_clis_installed_exits_gracefully(tmp_path, monkeypatch):
    """If no CLIs are installed, first-run should fail fast with install hints."""
    import cliworker.state as st

    fake_config = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_config))
    # Patch detect to return nothing installed
    import cliworker.firstrun as fr
    from cliworker.detect import CLIPresence
    from pathlib import Path

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
    # Should exit non-zero and print install hints or banner
    assert result.exit_code != 0, f"expected nonzero exit; got {result.exit_code}, output={result.output!r}"
    # Anything CLIWORKER-banner, install-hint, or install-instruction related
    haystack = result.output.lower()
    assert any(
        needle in haystack
        for needle in ("install", "npm i", "no llm", "cliworker", "claude")
    ), f"output was: {result.output!r}"
