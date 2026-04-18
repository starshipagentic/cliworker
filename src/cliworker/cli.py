"""cliworker CLI — natural-language-ish invocation.

Primary shape:
    cliworker "what is TCP?"                    # default chain
    cliworker "what is TCP?" use claude         # one CLI
    cliworker "what is TCP?" use claude gemini  # chain in stated order
    cliworker --use claude,gemini "what is TCP?"  # flag form

Subcommands (diagnostic, no prompt):
    cliworker doctor
    cliworker info [cli]
    cliworker skip-cache [--clear ...]
    cliworker setup      (planned — walk through missing CLI installs)

Library equivalents for Python programs:
    run("claude", "hi")
    use(["claude", "codex"], "hi")
"""
from __future__ import annotations

import sys

import click

from cliworker import __version__, run, use
from cliworker.detect import detect
from cliworker.registry import KNOWN_CLIS, get_spec
from cliworker import firstrun, state


SUBCOMMAND_NAMES = {"doctor", "info", "skip-cache", "setup", "help"}


# ---------------------------------------------------------------------------
# argv preprocessing: turn `... use cli1 cli2 [...]` into `... --use cli1,cli2 ...`
# ---------------------------------------------------------------------------

def _rewrite_use_keyword(argv: list[str]) -> list[str]:
    """If 'use' appears as a bare arg, convert the following cli-name tokens
    into a single `--use cli1,cli2` option.

    Rules:
      * `use` must appear as its own argv token (not embedded in a longer word).
      * Everything after `use` that does NOT start with `-` is a CLI name,
        until the next `-flag` or end of argv.
      * If no CLI names follow `use`, leave argv unchanged (probably a
        prompt containing the literal word 'use').
    """
    if "use" not in argv:
        return argv
    idx = argv.index("use")
    clis: list[str] = []
    i = idx + 1
    while i < len(argv) and not argv[i].startswith("-"):
        clis.append(argv[i])
        i += 1
    if not clis:
        return argv
    rest = argv[i:]
    return argv[:idx] + ["--use", ",".join(clis)] + rest


# ---------------------------------------------------------------------------
# Default command — handles bare prompt invocation
# ---------------------------------------------------------------------------

HELP_EPILOG = """\
\b
Shell examples:
  cliworker "what is TCP?"                     default chain
  cliworker "summarize:" < file.txt            stdin as bulk content
  cliworker "hi" use claude gemini             chain in order
  cliworker --use claude,gemini "hi"           flag form
  cliworker "hi" use claude -m sonnet          model override
  cliworker "hi" --no-paid                     only try subscription mode
  cliworker doctor                             health check
  cliworker doctor --probe                     also ping each CLI

\b
Python library:
  from cliworker import run, use
  run("claude", "hi")
  use(["claude","codex"], "hi")

\b
Full docs: https://github.com/starshipagentic/cliworker
"""


class PromptOrSubcommandGroup(click.Group):
    """Group that treats the first non-option arg as a prompt if it's not a
    registered subcommand. Also preprocesses the `use` keyword into --use."""

    def parse_args(self, ctx, args):
        args = _rewrite_use_keyword(list(args))
        # If the first positional arg isn't a registered subcommand, treat the
        # whole thing as a default "ask" invocation by prepending "_ask".
        first_positional = next((a for a in args if not a.startswith("-")), None)
        if first_positional and first_positional not in self.commands:
            args = ["_ask"] + args
        return super().parse_args(ctx, args)


@click.group(
    cls=PromptOrSubcommandGroup,
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=HELP_EPILOG,
)
@click.version_option(__version__, prog_name="cliworker")
@click.pass_context
def main(ctx: click.Context) -> None:
    """cliworker — ask any installed LLM CLI a question, fast and reliably.

    \b
    Simplest:
      cliworker "what is TCP?"                     default chain
      cliworker "what is TCP?" use claude gemini   specific CLIs, in order

    \b
    Under the hood cliworker applies per-CLI speed flags so that, e.g.,
    `claude -p` doesn't spend 15 seconds booting MCP servers.

    \b
    Default is FREE-ONLY: cliworker strips API-key env vars before invoking
    each CLI, forcing subscription mode. Paid API is opt-in via --paid-ok
    (or persistently via first-run / state.json). You never pay by accident.

    \b
    First run scans PATH and suggests installs for any missing CLI.
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ---------------------------------------------------------------------------
# `_ask` — internal command that handles bare prompt invocation
# ---------------------------------------------------------------------------

@main.command("_ask", hidden=True)
@click.argument("prompt_parts", nargs=-1)
@click.option("--use", "--llm", "use_csv", default=None, help="Comma-separated CLI names to try in order.")
@click.option("--model", "-m", default=None, help="Model override (e.g., sonnet, gemini-2.5-flash, llama3.1).")
@click.option("--timeout", default=120, show_default=True, help="Seconds before we give up on a single CLI.")
@click.option(
    "--paid-ok",
    "paid_ok_raw",
    default=None,
    help=(
        "Allow paid API fallback when subscription fails. "
        "Pass 'all' for every CLI, or a comma-separated list like 'claude,codex'. "
        "Default: never use paid API (free/subscription only)."
    ),
)
@click.option("--verbose", "-v", is_flag=True, help="Show which CLI answered + duration on stderr.")
def _ask(prompt_parts: tuple[str, ...], use_csv: str | None, model: str | None, timeout: int, paid_ok_raw: str | None, verbose: bool) -> None:
    """Default invocation: ask installed LLM CLIs a prompt."""
    # First-run diagnostics — only if state file doesn't exist yet
    if not state.exists():
        firstrun.run_diagnostics()
        if not state.default_chain():
            # No CLIs installed — can't proceed
            sys.exit(1)

    # Reconstruct prompt. If nothing piped in and no prompt given, show help.
    prompt = " ".join(prompt_parts).strip() if prompt_parts else ""
    stdin_content: str | None = None
    if not sys.stdin.isatty():
        stdin_content = sys.stdin.read()

    if not prompt and not stdin_content:
        click.echo("Usage: cliworker \"your prompt here\"", err=True)
        click.echo("Run `cliworker --help` for full examples.", err=True)
        sys.exit(2)

    # If prompt empty but stdin has content, treat stdin as the prompt.
    if not prompt and stdin_content:
        prompt = stdin_content
        stdin_content = None

    # Determine which CLIs to use
    if use_csv:
        clis = [c.strip() for c in use_csv.split(",") if c.strip()]
    else:
        clis = state.default_chain()

    if not clis:
        click.echo("No LLM CLIs available. Install one and re-run:", err=True)
        for name in ("claude", "codex", "gemini", "ollama"):
            for line in firstrun.INSTALL_HINTS[name]:
                click.echo(f"  {line}", err=True)
        sys.exit(1)

    # Optional per-CLI model override via get_spec()
    from cliworker.registry import get_spec as _get_spec

    specs = [_get_spec(name, model=model) if model else _get_spec(name) for name in clis]

    # Resolve paid_ok:
    #   --paid-ok flag not given  → use saved state preference (default: None)
    #   --paid-ok all             → True (all CLIs)
    #   --paid-ok claude,codex    → list of names
    if paid_ok_raw is None:
        # Inherit from state, if configured
        saved = state.load().get("paid_ok")
        if saved is True or saved is False or saved is None:
            paid_ok = saved
        elif isinstance(saved, list):
            paid_ok = saved
        else:
            paid_ok = None
    elif paid_ok_raw.lower() == "all":
        paid_ok = True
    else:
        paid_ok = [c.strip() for c in paid_ok_raw.split(",") if c.strip()]

    results = use(
        specs, prompt,
        stdin_content=stdin_content,
        paid_ok=paid_ok,
        timeout_s=timeout,
    )

    if verbose:
        for r in results:
            status = click.style("ok", fg="green") if r.ok else click.style("FAIL", fg="red")
            click.echo(f"[cliworker] {r.spec.cli:8} {status} in {r.duration_s:.2f}s", err=True)

    first_ok = next((r for r in results if r.ok), None)
    if first_ok:
        if verbose:
            click.echo(f"[cliworker] winner: {first_ok.spec.cli} ({first_ok.duration_s:.2f}s)", err=True)
        click.echo(first_ok.stdout, nl=False)
        sys.exit(0)

    # All failed
    click.echo("All CLIs failed:", err=True)
    for r in results:
        click.echo(f"  {r.spec.cli}: {r.stderr[:120]}", err=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# `info`
# ---------------------------------------------------------------------------

@main.command()
@click.argument("cli_name", required=False)
def info(cli_name: str | None) -> None:
    """Show the default argv recipe for each CLI.

    \b
    Examples:
      cliworker info                    show all
      cliworker info claude             show one
    """
    names = [cli_name] if cli_name else list(KNOWN_CLIS.keys())
    for name in names:
        spec = get_spec(name)
        click.echo(click.style(f"{name}", bold=True))
        click.echo(f"  cli binary:        {spec.cli}")
        click.echo(f"  subcommand:        {spec.subcommand or '(none)'}")
        click.echo(f"  prompt transport:  {spec.prompt_flag} ({spec.prompt_flag_name})")
        click.echo(f"  fast flags:        {'ON' if spec.fast else 'off'}")
        click.echo(f"  env vars stripped: {spec.env_strip or '(none)'}")
        click.echo(f"  sample argv:       {spec.build_argv('<PROMPT>')}")
        click.echo()


# ---------------------------------------------------------------------------
# `doctor`
# ---------------------------------------------------------------------------

@main.command()
@click.option("--probe/--no-probe", default=False, help="Invoke each installed CLI with a tiny prompt to measure cold start.")
@click.option("--probe-timeout", default=30, show_default=True)
def doctor(probe: bool, probe_timeout: int) -> None:
    """Scan PATH for installed LLM CLIs, optionally probe them.

    \b
    Without --probe: fast scan, never invokes anything.
    With    --probe: runs each CLI with "say ok" and reports duration.

    \b
    Examples:
      cliworker doctor
      cliworker doctor --probe --probe-timeout 60
    """
    presences = detect()
    for name, p in presences.items():
        status = click.style("✓", fg="green") if p.installed else click.style("✗", fg="red")
        right = str(p.binary_path) if p.installed else click.style(p.install_hint, dim=True)
        click.echo(f"  {status}  {name:8}  {right}")

    if probe:
        click.echo()
        click.echo(click.style("Probing installed CLIs (one-shot 'say ok')...", bold=True))
        for name, p in presences.items():
            if not p.installed:
                continue
            result = run(name, "Say exactly: ok", timeout_s=probe_timeout)
            if result.ok:
                click.echo(f"  {name:8}  {result.duration_s:5.2f}s  {click.style('ok', fg='green')}")
            else:
                click.echo(f"  {name:8}  {result.duration_s:5.2f}s  {click.style('fail', fg='red')}  {result.stderr[:60]}")


# ---------------------------------------------------------------------------
# `setup`
# ---------------------------------------------------------------------------

@main.command()
def setup() -> None:
    """Re-run the first-run diagnostics. Never auto-installs anything — just
    prints actionable commands for whatever's missing.

    \b
    Example:
      cliworker setup
    """
    firstrun.run_diagnostics()


# ---------------------------------------------------------------------------
# `skip-cache`
# ---------------------------------------------------------------------------

@main.command("skip-cache")
@click.option("--clear", "clear_name", default=None, help="CLI name to clear, or 'ALL' for everything.")
def skip_cache_cmd(clear_name: str | None) -> None:
    """Show or clear the broken-engine skip cache (1h TTL auto-expiry)."""
    import time

    from cliworker.skipcache import DEFAULT_CACHE_PATH, clear, _load

    if clear_name is not None:
        if clear_name.upper() == "ALL":
            clear(None)
            click.echo("Cleared entire skip-cache")
        else:
            clear(clear_name)
            click.echo(f"Cleared {clear_name} from skip-cache")
        return
    data = _load(DEFAULT_CACHE_PATH)
    if not data:
        click.echo("Skip-cache is empty. Nothing suppressed.")
        click.echo(f"(cache at {DEFAULT_CACHE_PATH})")
        return
    click.echo(f"Skip-cache at {DEFAULT_CACHE_PATH}:")
    for name, ts in sorted(data.items()):
        age = int(time.time() - ts)
        click.echo(f"  {name:8}  failed {_human(age)} ago  (suppresses for 1h)")


def _human(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60}m"


if __name__ == "__main__":
    main()
