"""Tests for run() + run_fast() using monkeypatched subprocess.

Public API shape (v0.7.0+):
    run(prompt, *clis, fast=None, paid_ok=None, ...) -> list[CLIResult]
    run_fast(prompt, *clis, **kwargs)                -> list[CLIResult]
"""
from __future__ import annotations

import subprocess

from cliworker import CLIResult, run, run_fast
from cliworker.core import _run_impl
from cliworker.registry import get_spec


def _fake_completed(rc: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["fake"], returncode=rc, stdout=stdout, stderr=stderr
    )


# ---------------------------------------------------------------------------
# Single-CLI tests (one element in *clis)
# ---------------------------------------------------------------------------

def test_run_single_cli_success(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)
    monkeypatch.setattr(
        "cliworker.core.subprocess.run",
        lambda *a, **k: _fake_completed(0, stdout="hello from claude"),
    )
    results = run("hi", "claude")
    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].stdout == "hello from claude"
    assert results[0].spec.cli == "claude"


def test_run_single_cli_binary_missing(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: None)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)
    results = run("hi", "claude")
    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].skipped_reason == "not_on_path"


def test_run_single_cli_timeout(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="fake", timeout=5)

    monkeypatch.setattr("cliworker.core.subprocess.run", raise_timeout)
    results = run("hi", "claude", timeout_s=5)
    assert results[0].ok is False
    assert "timeout" in results[0].stderr.lower()


# ---------------------------------------------------------------------------
# Chained-CLI tests
# ---------------------------------------------------------------------------

def test_run_chain_first_success_short_circuits(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)
    call_count = {"n": 0}

    def fake_run(*a, **k):
        call_count["n"] += 1
        return _fake_completed(0, stdout="first")

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)
    results = run("hi", "claude", "codex", "gemini")
    assert call_count["n"] == 1
    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].stdout == "first"


def test_run_chain_falls_through_failures(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)
    calls = []

    def fake_run(argv=None, *args, **kwargs):
        real_argv = args[0] if args else argv
        binary = real_argv[0]
        calls.append(binary)
        if binary == "gemini":
            return _fake_completed(0, stdout="gemini-ok")
        return _fake_completed(1, stderr=f"{binary} failed")

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)
    results = run("hi", "claude", "codex", "gemini")
    assert len(results) == 3
    assert results[-1].ok is True
    assert results[-1].stdout == "gemini-ok"


# ---------------------------------------------------------------------------
# Default chain — empty *clis falls back to state.json
# ---------------------------------------------------------------------------

def test_run_empty_clis_uses_default_chain(monkeypatch, tmp_path):
    """run("hi") with no *clis must read default_chain() from state."""
    # Point state at a tmp file with a known default chain
    import cliworker.state as st

    state_dir = tmp_path / "cfg" / "cliworker"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(
        '{"default_chain": ["claude"], "detected_clis": {"claude": true}}'
    )
    monkeypatch.setattr(st, "state_path", lambda: state_dir / "state.json")

    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)
    monkeypatch.setattr(
        "cliworker.core.subprocess.run",
        lambda *a, **k: _fake_completed(0, stdout="from default chain"),
    )

    results = run("hi")  # no *clis
    assert len(results) == 1
    assert results[0].spec.cli == "claude"


def test_run_empty_clis_and_no_state_returns_empty_list(monkeypatch, tmp_path):
    """If state is missing/empty and no *clis given, run() returns []."""
    import cliworker.state as st

    monkeypatch.setattr(st, "state_path", lambda: tmp_path / "nonexistent.json")

    results = run("hi")
    assert results == []


# ---------------------------------------------------------------------------
# run_fast() — sugar for fast=True
# ---------------------------------------------------------------------------

def test_run_fast_forces_fast_on_specs(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)

    captured_specs = []

    def fake_run(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args")
        captured_specs.append(argv)
        return _fake_completed(0, stdout="ok")

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)
    run_fast("hi", "claude")

    # In fast mode, CLAUDE_FAST flags should appear in the argv
    assert any(isinstance(argv, list) and "--strict-mcp-config" in argv for argv in captured_specs), (
        f"run_fast should inject CLAUDE_FAST flags; got argvs: {captured_specs}"
    )


def test_run_default_fast_is_off(monkeypatch):
    """Default run() leaves fast as each spec's own default (claude default = False in v0.7+)."""
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)

    captured = []

    def fake_run(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args")
        captured.append(argv)
        return _fake_completed(0, stdout="ok")

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)
    run("hi", "claude")  # no fast kwarg

    # In full mode, CLAUDE_FAST flags should NOT appear
    assert all("--strict-mcp-config" not in argv for argv in captured), (
        f"default run() must NOT inject CLAUDE_FAST; got: {captured}"
    )


# ---------------------------------------------------------------------------
# Internal _run_impl — white-box tests for strip_keys + skip_cache
# ---------------------------------------------------------------------------

def test_run_impl_strip_keys_removes_env_var(monkeypatch):
    """_run_impl with strip_keys=True removes the API key env var for that call."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)
    captured = {}

    def fake_run(*args, **kwargs):
        captured["env"] = kwargs.get("env", {})
        return _fake_completed(0, stdout="ok")

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)
    _run_impl(
        get_spec("claude"), "hi",
        stdin_content=None, strip_keys=True,
        timeout_s=5, skip_cache_check=False, cwd=None,
    )
    assert "ANTHROPIC_API_KEY" not in captured["env"]


def test_run_impl_strip_keys_false_keeps_env_var(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)
    captured = {}

    def fake_run(*args, **kwargs):
        captured["env"] = kwargs.get("env", {})
        return _fake_completed(0, stdout="ok")

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)
    _run_impl(
        get_spec("claude"), "hi",
        stdin_content=None, strip_keys=False,
        timeout_s=5, skip_cache_check=False, cwd=None,
    )
    assert captured["env"].get("ANTHROPIC_API_KEY") == "sk-secret"


def test_run_impl_honors_skip_cache(monkeypatch):
    monkeypatch.setattr("cliworker.core.is_skipped", lambda name, **kw: True)
    result = _run_impl(
        get_spec("claude"), "hi",
        stdin_content=None, strip_keys=True,
        timeout_s=5, skip_cache_check=True, cwd=None,
    )
    assert result.ok is False
    assert result.skipped_reason == "skip_cache"
