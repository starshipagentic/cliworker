"""cliworker CLI — debug / demo tool. Real use is via the Python API."""
from __future__ import annotations

import sys

import click

from cliworker import __version__, run_cli, run_with_fallback
from cliworker.registry import KNOWN_CLIS, get_spec


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="cliworker")
@click.pass_context
def main(ctx: click.Context) -> None:
    """cliworker — fast, reliable calls to LLM CLIs.

    Primary use is as a Python library. This CLI exists for debugging and
    one-off invocations.
    """
    if ctx.invoked_subcommand is None:
        click.echo(f"cliworker v{__version__}")
        click.echo("Run `cliworker --help` for subcommands, or use as a Python library:")
        click.echo("  from cliworker import run_cli")
        click.echo('  result = run_cli("claude", prompt="hi", fast=True)')


@main.command("list")
def list_cmd() -> None:
    """Show default specs for all known CLIs."""
    for name, spec in KNOWN_CLIS.items():
        click.echo(f"{name}")
        click.echo(f"  cli:              {spec.cli}")
        click.echo(f"  subcommand:       {spec.subcommand or '-'}")
        click.echo(f"  prompt_flag:      {spec.prompt_flag} ({spec.prompt_flag_name})")
        click.echo(f"  fast:             {spec.fast}")
        click.echo(f"  env_strip:        {spec.env_strip}")
        click.echo(f"  sample argv:      {spec.build_argv('<PROMPT>')}")
        click.echo()


@main.command("run")
@click.argument("cli_name")
@click.option("--prompt", "-p", required=True, help="Prompt to send.")
@click.option("--model", default=None, help="Model override.")
@click.option("--fast/--no-fast", default=True, help="Apply speed flags (default on).")
@click.option("--strip-keys", is_flag=True, help="Force subscription mode (strip env keys).")
@click.option("--timeout", default=120, show_default=True)
@click.option("--stdin", "stdin_flag", is_flag=True, help="Read bulk content from stdin (instruction goes as prompt arg).")
def run_cmd(cli_name: str, prompt: str, model: str | None, fast: bool, strip_keys: bool, timeout: int, stdin_flag: bool) -> None:
    """Invoke one CLI once. Example: cliworker run claude -p "hello"."""
    spec = get_spec(cli_name, model=model) if model else get_spec(cli_name)
    if not fast:
        from dataclasses import replace

        spec = replace(spec, fast=False)
    stdin_content = sys.stdin.read() if stdin_flag else None
    result = run_cli(
        spec, prompt,
        stdin_content=stdin_content, strip_keys=strip_keys, timeout_s=timeout,
    )
    click.echo(f"[{'ok' if result.ok else 'FAIL'}] {cli_name} in {result.duration_s:.2f}s (rc={result.returncode})")
    click.echo(f"argv: {result.argv}")
    if result.ok:
        click.echo(result.stdout)
    else:
        click.echo(f"stderr: {result.stderr}", err=True)
        sys.exit(1 if result.returncode in (None, 0) else result.returncode)


@main.command("chain")
@click.argument("cli_names", nargs=-1, required=True)
@click.option("--prompt", "-p", required=True)
@click.option("--timeout", default=120, show_default=True)
def chain_cmd(cli_names: tuple[str, ...], prompt: str, timeout: int) -> None:
    """Run CLIs in fallback order. Example: cliworker chain claude codex gemini -p "hi"."""
    results = run_with_fallback(list(cli_names), prompt, timeout_s=timeout)
    for r in results:
        status = "ok" if r.ok else "fail"
        click.echo(f"[{status}] {r.spec.cli} in {r.duration_s:.2f}s")
    first_ok = next((r for r in results if r.ok), None)
    if first_ok:
        click.echo()
        click.echo(first_ok.stdout)
    else:
        click.echo("All CLIs failed", err=True)
        sys.exit(1)


@main.command("skip-cache")
@click.option("--clear", "clear_name", default=None, help="CLI name to clear (or ALL).")
def skip_cache_cmd(clear_name: str | None) -> None:
    """Inspect or clear the skip-cache."""
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
        click.echo("Skip-cache is empty.")
        return
    import time

    for name, ts in sorted(data.items()):
        age = int(time.time() - ts)
        click.echo(f"  {name:12}  failed {age}s ago")


if __name__ == "__main__":
    main()
