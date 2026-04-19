"""Tests for cliworker.invoke() — the no-LLM-semantics subprocess primitive."""
from __future__ import annotations

import subprocess
from unittest.mock import patch

from cliworker import CLIResult, invoke


def _fake_completed(rc: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["fake"], returncode=rc, stdout=stdout, stderr=stderr
    )


def test_invoke_success_returns_cli_result(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["stdin"] = kwargs.get("stdin")
        captured["input"] = kwargs.get("input")
        return _fake_completed(0, stdout="added")

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)

    result = invoke("codex", "marketplace", "add", "owner/repo")

    assert isinstance(result, CLIResult)
    assert result.ok is True
    assert result.stdout == "added"
    assert result.argv == ["codex", "marketplace", "add", "owner/repo"]
    assert result.spec.cli == "codex"
    assert result.skipped_reason is None


def test_invoke_closes_stdin_by_default(monkeypatch):
    """Default stdin=DEVNULL so accidental interactive prompts fail fast
    instead of hanging. Critical for admin commands like `gemini extensions
    install` which has a fallback 'install via git clone?' prompt when the
    GitHub release is missing."""
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    captured = {}

    def fake_run(argv, **kwargs):
        captured["stdin"] = kwargs.get("stdin")
        captured["input"] = kwargs.get("input")
        return _fake_completed(0)

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)

    invoke("gemini", "extensions", "install", "https://github.com/x/y", "--consent")

    assert captured["stdin"] == subprocess.DEVNULL, (
        f"invoke() must close stdin by default; got {captured.get('stdin')!r}"
    )
    assert captured.get("input") is None


def test_invoke_with_stdin_content_uses_input(monkeypatch):
    """When the caller explicitly passes stdin_content, use it instead of DEVNULL."""
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    captured = {}

    def fake_run(argv, **kwargs):
        captured["stdin"] = kwargs.get("stdin")
        captured["input"] = kwargs.get("input")
        return _fake_completed(0)

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)

    invoke("some-cli", "read-stdin", stdin_content="hello from stdin")

    assert captured["input"] == "hello from stdin"
    # When `input` is passed, subprocess.run manages stdin itself — we don't set it.
    assert "stdin" not in captured or captured.get("stdin") is None


def test_invoke_missing_binary_returns_not_on_path(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: None)
    result = invoke("definitely-not-installed", "some", "args")
    assert result.ok is False
    assert result.skipped_reason == "not_on_path"
    assert "not found on PATH" in result.stderr


def test_invoke_timeout_returns_clean_failure(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 5))

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)

    result = invoke("slow-cli", "do-thing", timeout_s=5)
    assert result.ok is False
    assert "timeout" in result.stderr.lower()
    assert result.returncode is None


def test_invoke_does_not_strip_env_keys(monkeypatch):
    """Unlike run(), invoke() must NOT strip env API keys — admin commands
    may legitimately need them (e.g., codex auth status checks)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-preserve-me")
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    captured = {}

    def fake_run(argv, **kwargs):
        # invoke() should NOT pass env= kwarg at all (inherit os.environ)
        captured["env"] = kwargs.get("env")
        return _fake_completed(0)

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)

    invoke("claude", "some-admin-thing")
    # Either env not passed (inherits default) or if passed, key is present
    if captured.get("env") is not None:
        assert "ANTHROPIC_API_KEY" in captured["env"]


def test_invoke_skip_cache_check_off_by_default(monkeypatch):
    """Admin commands shouldn't inherit skip-cache from LLM peer-review loops."""
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: True)

    def fake_run(argv, **kwargs):
        return _fake_completed(0, stdout="ran anyway")

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)

    # Default: check_skip_cache=False — runs even when is_skipped returns True
    result = invoke("some-cli", "admin-cmd")
    assert result.ok is True
    assert result.stdout == "ran anyway"


def test_invoke_respects_skip_cache_when_opted_in(monkeypatch):
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)
    monkeypatch.setattr("cliworker.core.is_skipped", lambda n, **kw: True)

    result = invoke("some-cli", "admin-cmd", check_skip_cache=True)
    assert result.ok is False
    assert result.skipped_reason == "skip_cache"


def test_invoke_accepts_any_cli_name_not_just_known(monkeypatch):
    """invoke() should work for CLIs not in KNOWN_CLIS — e.g., `gh`, `brew`."""
    monkeypatch.setattr("cliworker.core._which", lambda b: "/fake/" + b)

    def fake_run(argv, **kwargs):
        return _fake_completed(0, stdout=f"ran {argv[0]}")

    monkeypatch.setattr("cliworker.core.subprocess.run", fake_run)

    result = invoke("gh", "release", "create", "v1.0.0", "--notes", "initial")
    assert result.ok is True
    assert "gh" in result.stdout
    assert result.argv[0] == "gh"
    assert result.spec.cli == "gh"


def test_invoke_real_binary_works(tmp_path):
    """End-to-end with a real system binary (echo)."""
    # Use /bin/echo which is universal on Unix
    result = invoke("echo", "hello", "world")
    assert result.ok is True, f"expected echo to work; got stderr={result.stderr!r}"
    assert "hello world" in result.stdout
    assert result.returncode == 0
