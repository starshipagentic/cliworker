"""Tests for run_cli + run_with_fallback using monkeypatched subprocess."""
from __future__ import annotations

from unittest.mock import patch

import subprocess

from cliworker import CLIResult, run_cli, run_with_fallback
from cliworker.registry import get_spec


def _fake_completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_run_cli_success(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr(
        "cliworker.core.subprocess.run",
        lambda *a, **k: _fake_completed(0, stdout="hello from claude", stderr=""),
    )
    result = run_cli("claude", prompt="hi", skip_cache_check=False)
    assert result.ok is True
    assert result.stdout == "hello from claude"
    assert result.returncode == 0
    assert result.spec.cli == "claude"


def test_run_cli_binary_missing(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: None)
    result = run_cli("claude", prompt="hi", skip_cache_check=False)
    assert result.ok is False
    assert result.skipped_reason == "not_on_path"


def test_run_cli_timeout(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="fake", timeout=5)

    monkeypatch.setattr("cliworker.core.subprocess.run", raise_timeout)
    result = run_cli("claude", prompt="hi", skip_cache_check=False, timeout_s=5)
    assert result.ok is False
    assert "timeout" in result.stderr.lower()


def test_run_cli_strip_keys_removes_env_var(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    captured = {}

    def fake_run(*args, **kwargs):
        captured["env"] = kwargs.get("env", {})
        return _fake_completed(0, stdout="ok")

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)
    run_cli("claude", prompt="hi", skip_cache_check=False, strip_keys=True)
    assert "ANTHROPIC_API_KEY" not in captured["env"]


def test_run_cli_strip_keys_false_keeps_env_var(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    captured = {}

    def fake_run(*args, **kwargs):
        captured["env"] = kwargs.get("env", {})
        return _fake_completed(0, stdout="ok")

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)
    run_cli("claude", prompt="hi", skip_cache_check=False, strip_keys=False)
    assert captured["env"].get("ANTHROPIC_API_KEY") == "sk-secret"


def test_run_with_fallback_first_success_short_circuits(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    # Isolate from any skip-cache state on the dev machine
    monkeypatch.setattr("cliworker.core.is_skipped", lambda name, **kw: False)
    call_count = {"n": 0}

    def fake_run(*a, **k):
        call_count["n"] += 1
        # First call succeeds
        return _fake_completed(0, stdout="first")

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)
    results = run_with_fallback(
        ["claude", "codex", "gemini"], prompt="hi",
    )
    # Only one subprocess should have been spawned despite 3 specs
    assert call_count["n"] == 1
    assert results[0].ok is True
    assert results[0].stdout == "first"
    # results only has one entry (we stopped at first success)
    assert len(results) == 1


def test_run_with_fallback_chains_through_failures(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda name, **kw: False)
    calls = []

    def fake_run(argv=None, *args, **kwargs):
        cmd = kwargs.get("args", argv) or (argv if argv else args[0] if args else None)
        # Infer which CLI from argv (first arg in args list is argv)
        real_argv = args[0] if args else argv
        binary = real_argv[0]
        calls.append(binary)
        # Fail on claude+codex, succeed on gemini
        if binary == "gemini":
            return _fake_completed(0, stdout="gemini-ok")
        return _fake_completed(1, stderr=f"{binary} failed")

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)
    results = run_with_fallback(
        ["claude", "codex", "gemini"], prompt="hi",
        strip_keys_first=True, retry_with_keys=False,  # only one pass
    )
    # Should have tried all three
    assert len(results) == 3
    assert results[-1].ok is True
    assert results[-1].stdout == "gemini-ok"


def test_run_cli_uses_skip_cache(monkeypatch, tmp_path):
    # Seed cache with claude as broken
    from cliworker import skipcache

    monkeypatch.setattr(skipcache, "DEFAULT_CACHE_PATH", tmp_path / "cache.json")
    # Patch the import location used inside core
    monkeypatch.setattr("cliworker.core.is_skipped", lambda name, **kw: name == "claude")

    result = run_cli("claude", prompt="hi")
    assert result.ok is False
    assert result.skipped_reason == "skip_cache"
