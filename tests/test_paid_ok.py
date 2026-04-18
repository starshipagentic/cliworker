"""Tests for paid_ok semantics with the new run() / run_fast() API.

Public API:
    run(prompt, *clis, paid_ok=None|True|list[str], ...) -> list[CLIResult]
"""
from __future__ import annotations

import subprocess

from cliworker import run


def _fake_completed(rc: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=["fake"], returncode=rc, stdout=stdout, stderr=stderr)


def _track_calls(tracker):
    """Build a fake subprocess.run that records (cli, has_key) tuples."""

    def fake_run(*args, **kwargs):
        argv = kwargs.get("args") or args[0]
        env = kwargs.get("env", {})
        cli_name = argv[0]
        key_var = {
            "claude": "ANTHROPIC_API_KEY",
            "codex": "OPENAI_API_KEY",
            "gemini": "GOOGLE_API_KEY",
        }.get(cli_name)
        has_key = key_var is not None and key_var in env
        tracker.append((cli_name, has_key))
        return _fake_completed(1, stderr=f"{cli_name} intentional fail")

    return fake_run


def test_paid_ok_none_only_runs_free_pass(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k1")
    monkeypatch.setenv("OPENAI_API_KEY", "k2")
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)

    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr("cliworker.core.subprocess.run", _track_calls(calls))

    run("hi", "claude", "codex", paid_ok=None)

    # Exactly 2 attempts (claude + codex, pass 1 only) — no paid pass.
    assert len(calls) == 2
    assert all(has_key is False for _, has_key in calls), calls


def test_paid_ok_true_runs_paid_pass_for_all(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k1")
    monkeypatch.setenv("OPENAI_API_KEY", "k2")
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)

    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr("cliworker.core.subprocess.run", _track_calls(calls))

    run("hi", "claude", "codex", paid_ok=True)

    # 4 attempts: claude free, codex free, claude paid, codex paid
    assert len(calls) == 4
    pass1 = [has_key for _, has_key in calls[:2]]
    pass2 = [has_key for _, has_key in calls[2:]]
    assert pass1 == [False, False]
    assert pass2 == [True, True]


def test_paid_ok_list_restricts_paid_pass(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k1")
    monkeypatch.setenv("OPENAI_API_KEY", "k2")
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)

    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr("cliworker.core.subprocess.run", _track_calls(calls))

    # Paid fallback allowed ONLY for claude, not codex.
    run("hi", "claude", "codex", paid_ok=["claude"])

    # 3 attempts: claude free, codex free, claude paid (no codex paid)
    assert len(calls) == 3
    assert calls[0] == ("claude", False)
    assert calls[1] == ("codex", False)
    assert calls[2] == ("claude", True)


def test_paid_ok_false_behaves_like_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k1")
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: False)

    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr("cliworker.core.subprocess.run", _track_calls(calls))

    run("hi", "claude", paid_ok=False)

    assert len(calls) == 1  # one free attempt, no paid
    assert calls[0][1] is False
