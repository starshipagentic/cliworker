"""cliworker — CLI entry point.

The Python library is the main interface. This CLI exists for three reasons:
  1. Debugging — `cliworker info` shows what argv would be built.
  2. Health check — `cliworker doctor` pings every installed LLM CLI.
  3. Quick one-offs — `cliworker run claude -p "hi"` from your shell.
"""
from __future__ import annotations

import sys
import time

import click

from cliworker import __version__, fallback, run
from cliworker.detect import detect
from cliworker.registry import KNOWN_CLIS, get_spec


HELP_EPILOG = """\
\b
Quick reference:
  cliworker run claude -p "hi"                  run one CLI
  cliworker run claude -p "hi" --model sonnet   choose a model
  cliworker run gemini -p "hi" --no-fast        disable speed flags
  cliworker run claude -p "summarize:" --stdin < transcript.txt

\b
  cliworker fallback claude codex gemini -p "summarize"
                                                try CLIs in order, stop on first success

\b
  cliworker info                                show default spec for every CLI
  cliworker info claude                         show spec for one CLI
  cliworker doctor                              detect installed CLIs + timing probe
  cliworker skip-cache                          show which CLIs are currently suppressed
  cliworker skip-cache --clear ALL              reset all suppressions

\b
Python usage:
  from cliworker import run, fallback
  r = run("claude", "hi")                # one call
  rs = fallback(["claude","codex"], ...)  # fallback chain

\b
See https://github.com/starshipagentic/cliworker for full docs.
"""


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=HELP_EPILOG,
)
@click.version_option(__version__, prog_name="cliworker")
@click.pass_context
def main(ctx: click.Context) -> None:
    """cliworker — one sane way to call claude, codex, gemini, ollama.

    \b
    Two verbs, one result object.
      run <cli>       invoke one CLI, get its output
      fallback a b c  try CLIs in order, stop on first success

    \b
    Sensible defaults applied automatically:
      * claude -p gets CLAUDE_FAST_FLAGS (no MCP/tools/chrome) — 18s → 4s
      * gemini -p strips mcpServers from ~/.gemini/settings.json during call
      * fallback tries subscription mode first (pass 1 strips API keys),
        then paid API (pass 2 keeps keys) — maximizes free-tier usage
      * failed CLIs get cached for 1h so you don't re-spam a broken engine
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ---------------------------------------------------------------------------
# `run`
# ---------------------------------------------------------------------------

@main.command()
@click.argument("cli_name")
@click.option("--prompt", "-p", required=True, help="The prompt to send to the CLI.")
@click.option("--model", "-m", default=None, help="Model override (e.g., sonnet for claude, gemini-2.5-flash for gemini).")
@click.option(
    "--fast/--no-fast",
    default=True,
    show_default=True,
    help="Apply per-CLI speed flags. Default ON. Turn off for debugging or if flags cause errors.",
)
@click.option(
    "--strip-keys/--keep-keys",
    default=False,
    show_default=True,
    help="Strip the CLI's env API key var to force subscription mode.",
)
@click.option("--timeout", default=120, show_default=True, help="Subprocess timeout in seconds.")
@click.option(
    "--stdin",
    "stdin_flag",
    is_flag=True,
    help="Read bulk content from stdin; --prompt becomes the instruction.",
)
@click.option("--verbose", "-v", is_flag=True, help="Print argv + timing info to stderr.")
def run_cmd(
    cli_name: str,
    prompt: str,
    model: str | None,
    fast: bool,
    strip_keys: bool,
    timeout: int,
    stdin_flag: bool,
    verbose: bool,
) -> None:
    """Invoke ONE CLI once, print its stdout.

    \b
    Examples:
      cliworker run claude -p "explain async/await"
      cliworker run claude -p "hi" --model sonnet
      cliworker run gemini -p "summarize:" --stdin < transcript.txt
      cliworker run codex -p "refactor this function" --timeout 300
      cliworker run claude -p "hi" --strip-keys              # force subscription

    \b
    Exit codes:
      0   success
      1   CLI failed (stderr printed to stderr)
      2   CLI binary not found on PATH
      3   timeout

    \b
    Available CLIs (defaults):
      claude   — Claude Code CLI, fast flags ON
      codex    — OpenAI Codex CLI, fast flags OFF (already lightweight)
      gemini   — Google Gemini CLI, MCP strip-and-restore ON
      ollama   — Local Ollama, fast flags OFF (no network startup)
    """
    stdin_content = sys.stdin.read() if stdin_flag else None
    result = run(
        cli_name,
        prompt,
        model=model,
        fast=fast,
        stdin_content=stdin_content,
        strip_keys=strip_keys,
        timeout_s=timeout,
    )

    if verbose:
        click.echo(
            f"[cliworker] {cli_name} {'ok' if result.ok else 'FAIL'} "
            f"in {result.duration_s:.2f}s (rc={result.returncode})",
            err=True,
        )
        click.echo(f"[cliworker] argv: {result.argv}", err=True)

    if result.ok:
        click.echo(result.stdout, nl=False)
        sys.exit(0)

    click.echo(f"[cliworker] {cli_name} failed: {result.stderr[:200]}", err=True)
    if result.skipped_reason == "not_on_path":
        sys.exit(2)
    if "timeout" in result.stderr.lower():
        sys.exit(3)
    sys.exit(1)


# ---------------------------------------------------------------------------
# `fallback`
# ---------------------------------------------------------------------------

@main.command()
@click.argument("cli_names", nargs=-1, required=True)
@click.option("--prompt", "-p", required=True)
@click.option(
    "--free-first/--paid-first",
    default=True,
    show_default=True,
    help="Pass 1 strategy: strip env API keys (free_first) or keep them (paid_first).",
)
@click.option(
    "--no-retry",
    is_flag=True,
    help="Disable the 2nd pass (keys flipped). Only one attempt per CLI.",
)
@click.option("--timeout", default=120, show_default=True)
@click.option("--verbose", "-v", is_flag=True)
def fallback_cmd(
    cli_names: tuple[str, ...],
    prompt: str,
    free_first: bool,
    no_retry: bool,
    timeout: int,
    verbose: bool,
) -> None:
    """Try CLIs in order; stop at first success.

    \b
    This is for reliability — when you'd rather get any answer than no answer,
    from whichever engine is alive and subscribed right now.

    \b
    Examples:
      cliworker fallback claude codex gemini -p "summarize this"
      cliworker fallback gemini claude -p "hi" --paid-first
      cliworker fallback ollama claude -p "hi" --no-retry

    \b
    Two-pass default behavior:
      Pass 1: each CLI with its env API key STRIPPED → forces free / subscription mode
      Pass 2: each CLI with env API key PRESENT → falls back to paid API

    \b
    --paid-first inverts the order (useful if you want determinism and have credits).
    --no-retry disables pass 2 entirely.
    """
    results = fallback(
        list(cli_names), prompt,
        free_first=free_first, retry_paid=not no_retry,
        timeout_s=timeout,
    )

    if verbose:
        for r in results:
            status = "ok" if r.ok else "FAIL"
            click.echo(
                f"[cliworker] {r.spec.cli:8} {status} in {r.duration_s:.2f}s",
                err=True,
            )

    first_ok = next((r for r in results if r.ok), None)
    if first_ok:
        click.echo(first_ok.stdout, nl=False)
        sys.exit(0)

    click.echo("[cliworker] All CLIs failed", err=True)
    for r in results:
        click.echo(f"  {r.spec.cli}: {r.stderr[:100]}", err=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# `info`
# ---------------------------------------------------------------------------

@main.command()
@click.argument("cli_name", required=False)
def info(cli_name: str | None) -> None:
    """Show the default spec (what argv gets built) for each CLI.

    \b
    With no arg, shows all four. With a name, shows just that one.

    \b
    Examples:
      cliworker info
      cliworker info claude
    """
    names = [cli_name] if cli_name else list(KNOWN_CLIS.keys())
    for name in names:
        spec = get_spec(name)
        click.echo(click.style(f"{name}", bold=True))
        click.echo(f"  cli binary:        {spec.cli}")
        click.echo(f"  subcommand:        {spec.subcommand or '(none)'}")
        click.echo(f"  model_flag:        {spec.model_flag or '(positional)'}")
        click.echo(f"  prompt transport:  {spec.prompt_flag} ({spec.prompt_flag_name})")
        click.echo(f"  fast flags:        {'ON' if spec.fast else 'off'}")
        click.echo(f"  env vars stripped: {spec.env_strip or '(none)'}")
        click.echo(f"  sample argv:       {spec.build_argv('<PROMPT>')}")
        click.echo()


# ---------------------------------------------------------------------------
# `doctor`
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "--probe/--no-probe",
    default=False,
    help="Actually invoke each installed CLI with a tiny prompt to measure cold start.",
)
@click.option("--probe-timeout", default=30, show_default=True)
def doctor(probe: bool, probe_timeout: int) -> None:
    """Report which LLM CLIs are installed on PATH + optional cold-start probe.

    \b
    Without --probe: just scans PATH. Fast, never invokes anything.
    With    --probe: runs `cliworker run <cli> -p "hi"` for every installed CLI
                     and reports duration. Useful for comparing fast-flag impact.

    \b
    Example:
      cliworker doctor
      cliworker doctor --probe --probe-timeout 60
    """
    presences = detect()
    for name, p in presences.items():
        status = click.style("✓", fg="green") if p.installed else click.style("✗", fg="red")
        right = str(p.binary_path) if p.installed else click.style(p.install_hint, dim=True)
        click.echo(f"  {status}  {name:8}  {right}")

    if not probe:
        return

    click.echo()
    click.echo(click.style("Probing (cold-start ping, one prompt each)...", bold=True))
    for name, p in presences.items():
        if not p.installed:
            continue
        start = time.monotonic()
        try:
            result = run(name, "Say exactly: ok", timeout_s=probe_timeout)
            dur = time.monotonic() - start
            if result.ok:
                click.echo(f"  {name:8}  {dur:5.2f}s  {click.style('ok', fg='green')}")
            else:
                click.echo(f"  {name:8}  {dur:5.2f}s  {click.style('fail', fg='red')}  {result.stderr[:60]}")
        except Exception as exc:
            dur = time.monotonic() - start
            click.echo(f"  {name:8}  {dur:5.2f}s  {click.style('err', fg='red')}  {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# `skip-cache`
# ---------------------------------------------------------------------------

@main.command("skip-cache")
@click.option("--clear", "clear_name", default=None, help="CLI name to clear, or 'ALL' for everything.")
def skip_cache_cmd(clear_name: str | None) -> None:
    """Show or clear the broken-engine skip cache.

    \b
    When a CLI fails, cliworker remembers it for 1 hour so subsequent calls
    don't keep hitting the same broken engine. Use this to inspect what's
    currently suppressed, or to manually clear entries.

    \b
    Examples:
      cliworker skip-cache                   # show what's suppressed
      cliworker skip-cache --clear claude    # un-suppress just claude
      cliworker skip-cache --clear ALL       # reset everything

    \b
    Cache lives at ~/.cache/cliworker/skip-cache.json (XDG_CACHE_HOME aware).
    """
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
        click.echo(f"(cache lives at {DEFAULT_CACHE_PATH})")
        return

    click.echo(f"Skip-cache at {DEFAULT_CACHE_PATH}:")
    for name, ts in sorted(data.items()):
        age = int(time.time() - ts)
        human = _human_duration(age)
        suppressed_for = 3600 - age
        remain = _human_duration(max(0, suppressed_for))
        status = "suppressed" if suppressed_for > 0 else "stale (will clear on next call)"
        click.echo(f"  {name:8}  failed {human} ago  {status}  (clears in ~{remain})")


def _human_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60}m"


# Register the two renamed commands under their canonical names.
# Click decorators above assigned them to `run_cmd` / `fallback_cmd` functions
# but we want `cliworker run` / `cliworker fallback` in the help output.
# The @main.command() decorator already handles that via the function name —
# but `run` shadows the imported `run` from cliworker. Fix: explicit name=.
#
# Already handled by the decorators being applied to functions named `run_cmd`
# and `fallback_cmd`. Click infers the command name from the function name,
# stripping `_cmd`. That gives us `run` and `fallback` as CLI commands.
# (Click converts _cmd suffix automatically.)


if __name__ == "__main__":
    main()
