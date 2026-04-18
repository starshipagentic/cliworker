"""Tests for gemini MCP strip/restore context manager."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from cliworker.fastflags import (
    CLAUDE_FAST_FLAGS,
    gemini_stripped_mcp,
)


def test_claude_fast_flags_shape():
    # Must include the five known-correct flags
    assert "--tools" in CLAUDE_FAST_FLAGS
    assert "--no-chrome" in CLAUDE_FAST_FLAGS
    assert "--strict-mcp-config" in CLAUDE_FAST_FLAGS
    assert "--mcp-config" in CLAUDE_FAST_FLAGS
    assert '{"mcpServers":{}}' in CLAUDE_FAST_FLAGS
    assert "--no-session-persistence" in CLAUDE_FAST_FLAGS


def test_gemini_strip_restore_roundtrip(tmp_path: Path, monkeypatch):
    # Redirect Path.home() to tmp
    monkeypatch.setenv("HOME", str(tmp_path))
    gemini_dir = tmp_path / ".gemini"
    gemini_dir.mkdir()
    settings = gemini_dir / "settings.json"
    original = {
        "theme": "dark",
        "mcpServers": {"foo": {"command": "foo-server"}},
    }
    settings.write_text(json.dumps(original))

    # Patch the module's Path.home() usage
    with patch("cliworker.fastflags.Path.home", return_value=tmp_path):
        # Inside the context, settings should have NO mcpServers
        with gemini_stripped_mcp():
            mid = json.loads(settings.read_text())
            assert "mcpServers" not in mid
            assert mid.get("theme") == "dark"

    # After exit, settings should be restored
    final = json.loads(settings.read_text())
    assert final == original

    # Backup file should be gone (cleaned up)
    backup = gemini_dir / "settings.json.cliworker-bak"
    assert not backup.exists()


def test_gemini_strip_restore_with_exception(tmp_path: Path, monkeypatch):
    """Restore must run even if the wrapped block raises."""
    gemini_dir = tmp_path / ".gemini"
    gemini_dir.mkdir()
    settings = gemini_dir / "settings.json"
    original = {"mcpServers": {"x": {"command": "x-server"}}}
    settings.write_text(json.dumps(original))

    with patch("cliworker.fastflags.Path.home", return_value=tmp_path):
        try:
            with gemini_stripped_mcp():
                raise RuntimeError("boom")
        except RuntimeError:
            pass

    assert json.loads(settings.read_text()) == original


def test_gemini_strip_noop_when_settings_missing(tmp_path: Path):
    """If ~/.gemini/settings.json doesn't exist, context manager is a no-op."""
    with patch("cliworker.fastflags.Path.home", return_value=tmp_path):
        with gemini_stripped_mcp():
            pass  # nothing to do
    # No file was created
    assert not (tmp_path / ".gemini" / "settings.json").exists()


def test_gemini_strip_noop_when_no_mcp_servers(tmp_path: Path):
    """If settings exists but has no mcpServers, leave it alone."""
    gemini_dir = tmp_path / ".gemini"
    gemini_dir.mkdir()
    settings = gemini_dir / "settings.json"
    original = {"theme": "light"}
    settings.write_text(json.dumps(original))

    with patch("cliworker.fastflags.Path.home", return_value=tmp_path):
        with gemini_stripped_mcp():
            # File unchanged mid-flight
            assert json.loads(settings.read_text()) == original

    # And still unchanged after
    assert json.loads(settings.read_text()) == original
