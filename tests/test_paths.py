"""Path-resolution tests — config/cache go to ~/.cliworker/ by default,
honor XDG_CONFIG_HOME / XDG_CACHE_HOME when explicitly set.
"""
from __future__ import annotations

from pathlib import Path


def test_state_path_defaults_to_dotcliworker(monkeypatch, tmp_path):
    """Unset XDG_CONFIG_HOME → state at ~/.cliworker/state.json"""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    from cliworker import state

    assert state.state_path() == tmp_path / ".cliworker" / "state.json"


def test_state_path_honors_xdg_config_home(monkeypatch, tmp_path):
    """XDG_CONFIG_HOME set → state at $XDG_CONFIG_HOME/cliworker/state.json"""
    xdg = tmp_path / "xdg-cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    from cliworker import state

    assert state.state_path() == xdg / "cliworker" / "state.json"


def test_cache_path_defaults_to_dotcliworker(monkeypatch, tmp_path):
    """Unset XDG_CACHE_HOME → cache at ~/.cliworker/skip-cache.json"""
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # Re-import to pick up fresh DEFAULT_CACHE_PATH (module-level at import)
    import importlib

    import cliworker.skipcache as sc

    importlib.reload(sc)
    assert sc.DEFAULT_CACHE_PATH == tmp_path / ".cliworker" / "skip-cache.json"


def test_cache_path_honors_xdg_cache_home(monkeypatch, tmp_path):
    """XDG_CACHE_HOME set → cache at $XDG_CACHE_HOME/cliworker/skip-cache.json"""
    xdg = tmp_path / "xdg-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))

    import importlib

    import cliworker.skipcache as sc

    importlib.reload(sc)
    assert sc.DEFAULT_CACHE_PATH == xdg / "cliworker" / "skip-cache.json"


def test_empty_xdg_var_falls_back_to_dotcliworker(monkeypatch, tmp_path):
    """XDG_CONFIG_HOME set to empty string → treat as unset → ~/.cliworker/"""
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    from cliworker import state

    assert state.state_path() == tmp_path / ".cliworker" / "state.json"
