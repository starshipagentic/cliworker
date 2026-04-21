"""Tests for the startup-idle subprocess watchdog.

These run real subprocesses (`sh -c '...'`) to verify the watchdog actually
kills silent-startup hangs, lets noisy processes run to completion, and
still honors the hard timeout for chatty-but-slow processes.
"""
from __future__ import annotations

import sys
import time

import pytest

from cliworker import invoke
from cliworker.core import _run_with_watchdog


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="watchdog uses POSIX start_new_session + killpg",
)


def test_startup_idle_kills_silent_process():
    """A process that sleeps silently for longer than startup_idle_s should
    be killed by the watchdog, not run to its natural end."""
    start = time.monotonic()
    result = invoke("sh", "-c", "sleep 60", timeout_s=600)
    elapsed = time.monotonic() - start
    # With default startup_idle_s=30 it should kill at ~30s, way before 60s
    # natural exit and way before the 600s hard timeout.
    assert result.ok is False
    assert result.timeout_kind == "startup_idle"
    assert elapsed < 40, f"watchdog took {elapsed}s — should be ~30s"


def test_noisy_process_survives_startup_idle():
    """A process that prints something within startup_idle_s should run to
    completion — and stay alive even if it goes quiet after emitting output."""
    rc, stdout, stderr, duration, timeout_kind = _run_with_watchdog(
        ["sh", "-c", "echo hello; sleep 3; echo goodbye"],
        timeout_s=30,
        startup_idle_s=1,
    )
    assert timeout_kind is None
    assert rc == 0
    assert "hello" in stdout
    assert "goodbye" in stdout
    # 3s sleep + grace ≥ 3s, but well under 30s hard timeout
    assert 2.5 < duration < 10


def test_hard_timeout_fires_for_chatty_slow_process():
    """A process that keeps printing slowly still hits hard timeout."""
    rc, stdout, stderr, duration, timeout_kind = _run_with_watchdog(
        ["sh", "-c", "for i in 1 2 3 4 5 6 7 8 9 10; do echo $i; sleep 1; done"],
        timeout_s=2,
        startup_idle_s=30,
    )
    assert timeout_kind == "hard"
    assert duration < 6, f"hard timeout took {duration}s — should be ~2s + grace"


def test_successful_fast_exit_returns_cleanly():
    """A process that finishes quickly should just work, no timeout."""
    rc, stdout, stderr, duration, timeout_kind = _run_with_watchdog(
        ["sh", "-c", "echo 'ack'"],
        timeout_s=30,
        startup_idle_s=30,
    )
    assert timeout_kind is None
    assert rc == 0
    assert "ack" in stdout
    assert duration < 2


def test_stdin_passthrough_still_works():
    """`stdin_content` must land on the subprocess's stdin without blocking
    the watchdog loop."""
    rc, stdout, stderr, duration, timeout_kind = _run_with_watchdog(
        ["cat"],
        timeout_s=10,
        startup_idle_s=5,
        stdin_content="hello from stdin\n",
    )
    assert timeout_kind is None
    assert rc == 0
    assert "hello from stdin" in stdout


def test_startup_idle_does_not_fire_if_process_exits_quickly():
    """If a silent process exits before startup_idle_s, the watchdog must
    NOT mistakenly mark it as a startup_idle kill."""
    result = invoke("sh", "-c", "exit 0", timeout_s=10)
    assert result.ok is True
    assert result.timeout_kind is None
    assert result.returncode == 0
