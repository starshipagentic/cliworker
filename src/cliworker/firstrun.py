"""First-run diagnostics: ASCII banner, CLI detection, actionable install hints."""
from __future__ import annotations

import shutil
import subprocess

import click

from cliworker import state
from cliworker.detect import detect
from cliworker.state import DEFAULT_OLLAMA_MODEL


BANNER = r"""
 ██████╗██╗     ██╗██╗    ██╗  ██████╗  ██████╗ ██╗  ██╗███████╗██████╗
██╔════╝██║     ██║██║    ██║ ██╔═══██╗ ██╔══██╗██║ ██╔╝██╔════╝██╔══██╗
██║     ██║     ██║██║ █╗ ██║ ██║   ██║ ██████╔╝█████╔╝ █████╗  ██████╔╝
██║     ██║     ██║██║███╗██║ ██║   ██║ ██╔══██╗██╔═██╗ ██╔══╝  ██╔══██╗
╚██████╗███████╗██║╚███╔███╔╝ ╚██████╔╝ ██║  ██║██║  ██╗███████╗██║  ██║
 ╚═════╝╚══════╝╚═╝ ╚══╝╚══╝   ╚═════╝  ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
          one sane way to call claude, codex, gemini, ollama
"""


INSTALL_HINTS: dict[str, list[str]] = {
    # Ordered by how cliworker recommends them. Each line is an actionable
    # shell command or URL.
    "claude": [
        "# Install Claude Code:",
        "https://claude.com/product/claude-code",
    ],
    "codex": [
        "# Install Codex CLI:",
        "npm i -g @openai/codex",
    ],
    "gemini": [
        "# Install Gemini CLI:",
        "npm i -g @google/gemini-cli",
    ],
    "ollama": [
        "# Install Ollama:",
        "brew install ollama        # macOS (or https://ollama.com/download)",
        f"ollama pull {DEFAULT_OLLAMA_MODEL}      # cliworker's default ollama model",
    ],
}


def _ollama_has_any_model() -> bool | None:
    """Return True if `ollama list` shows at least one pulled model, False if empty,
    None if ollama binary not on PATH or `ollama list` errors out."""
    if not shutil.which("ollama"):
        return None
    try:
        proc = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    # `ollama list` output has a header row + one row per model. Empty if
    # only the header is present.
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return len(lines) > 1


def _ollama_has_default_model() -> bool | None:
    """Return True if the DEFAULT_OLLAMA_MODEL is pulled, False if not, None on error."""
    if not shutil.which("ollama"):
        return None
    try:
        proc = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return any(
        ln.split()[0].split(":")[0] == DEFAULT_OLLAMA_MODEL
        for ln in proc.stdout.splitlines()[1:]
        if ln.strip()
    )


def run_diagnostics(echo=click.echo, color: bool = True) -> dict:
    """Scan PATH for known CLIs, print status + install hints, save state.

    Returns the saved state dict.
    """
    def _c(s: str, fg: str | None = None, bold: bool = False) -> str:
        if not color:
            return s
        return click.style(s, fg=fg, bold=bold)

    echo(_c(BANNER, "cyan"))
    echo("First run — scanning for installed LLM CLIs...")
    echo("")

    presences = detect()
    installed_names: list[str] = []
    missing_names: list[str] = []

    for name, p in presences.items():
        if p.installed:
            installed_names.append(name)
            marker = _c("✓", "green")
            right = str(p.binary_path)
            echo(f"  {marker}  {name:8}  {right}")
        else:
            missing_names.append(name)
            marker = _c("✗", "red")
            echo(f"  {marker}  {name:8}  {_c('not installed', 'yellow')}")

    # Extra check: if ollama IS installed, is any model pulled?
    ollama_model_ok = None
    if presences["ollama"].installed:
        ollama_model_ok = _ollama_has_default_model()
        if ollama_model_ok is True:
            marker = _c("✓", "green")
            echo(f"     {marker}  ollama has `{DEFAULT_OLLAMA_MODEL}` pulled")
        elif ollama_model_ok is False:
            marker = _c("!", "yellow")
            echo(f"     {marker}  ollama installed but `{DEFAULT_OLLAMA_MODEL}` not pulled")

    # Print install hints for anything missing or incomplete
    any_hints = False
    for name in missing_names:
        if not any_hints:
            echo("")
            echo(_c("To add missing CLIs, run:", bold=True))
            any_hints = True
        echo("")
        for line in INSTALL_HINTS[name]:
            if line.startswith("#"):
                echo(_c(f"  {line}", bold=True))
            else:
                echo(f"    {line}")

    if presences["ollama"].installed and ollama_model_ok is False:
        if not any_hints:
            echo("")
            echo(_c("To finish ollama setup:", bold=True))
            any_hints = True
        else:
            echo("")
            echo(_c("  # Pull cliworker's default ollama model:", bold=True))
        echo(f"    ollama pull {DEFAULT_OLLAMA_MODEL}")

    echo("")

    if not installed_names:
        echo(_c("No LLM CLIs detected.", "red") + " Install at least one of the above, then re-run cliworker.")
        # Don't save state — force re-run diagnostics next time.
        return {}

    default_chain = [n for n in ("claude", "codex", "gemini", "ollama") if n in installed_names]
    echo(f"Default chain: {_c(' → '.join(default_chain), 'cyan')}")
    saved = state.mark_first_run_complete(
        detected_clis={name: (name in installed_names) for name in presences},
        ollama_has_model=ollama_model_ok,
    )
    echo(click.style(f"Saved config to {state.state_path()}", dim=True) if color else f"Saved: {state.state_path()}")
    echo("")
    return saved
