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
Shell — the natural shape
  cliworker "what is TCP?"                     default chain, free only
  cliworker "summarize:" < file.txt            stdin as bulk content
  cliworker "hi" use claude                    one specific CLI
  cliworker "hi" use claude gemini             chain in stated order
  cliworker --use claude,gemini "hi"           flag form (for scripts)
  cliworker --llm claude,gemini "hi"           --llm aliases --use

\b
Options for any bare-prompt invocation
  --use, --llm TEXT    CLI names to try (comma-separated), in order.
  -m, --model TEXT     Model override (sonnet, gemini-2.5-flash, llama3.1).
  --paid-ok TEXT       Allow paid API fallback. 'all' or 'claude,codex,...'.
                       Default: never. cliworker stays free-only unless you
                       opt in here or persistently via state.json.
  --timeout INTEGER    Seconds per CLI before we give up. Default 120.
  -v, --verbose        Log winner CLI + duration to stderr.

\b
Paid API — opt-in examples
  cliworker "hi"                               free only (default)
  cliworker "hi" --paid-ok all                 paid OK for every CLI
  cliworker "hi" --paid-ok claude              paid OK for claude only
  cliworker "hi" use claude codex --paid-ok claude
                                               use both, but only claude may pay

\b
Diagnostic subcommands
  cliworker doctor                             which CLIs are installed?
  cliworker doctor --probe                     ping each with "say ok"
  cliworker info                               show default argv per CLI
  cliworker info claude                        show one CLI's recipe
  cliworker setup                              re-run first-run diagnostics
  cliworker skip-cache                         inspect broken-engine cache
  cliworker skip-cache --clear ALL             clear it all
  cliworker skip-cache --clear claude          clear one

\b
Python library
  from cliworker import run, use
  r = run("claude", "hi")                              # one call
  r = run("claude", "hi", model="sonnet")              # model override
  rs = use(["claude","codex"], "hi")                   # chain, free only
  rs = use(["claude","codex"], "hi", paid_ok=True)     # paid OK for all
  rs = use(["claude","codex"], "hi", paid_ok=["claude"])  # paid OK for one

\b
State
  First-run saves default chain + paid_ok preference to
  ~/.config/cliworker/state.json (respects XDG_CONFIG_HOME). Edit
  anytime or re-run `cliworker setup` to re-answer the prompts.

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
    """Show the default argv recipe for each CLI — the exact subprocess call
    cliworker would make. Useful for debugging "why is claude failing?" or
    "what flags does cliworker apply?"

    \b
    Fields shown per CLI:
      cli binary         what `shutil.which` looks up
      subcommand         e.g. 'exec' for codex
      model_flag         how to pass --model (or empty = positional)
      prompt transport   positional | flag | stdin
      fast flags         ON = applies per-CLI speed tricks
      env vars stripped  which API-key vars are removed for subscription mode
      sample argv        the full shell argv cliworker would build

    \b
    Examples:
      cliworker info                    show all four known CLIs
      cliworker info claude             show claude only
      cliworker info codex              show codex only
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
    """Scan PATH for installed LLM CLIs; optionally probe with a live call.

    \b
    Two modes:
      (default)  Fast PATH scan only. Never invokes anything. ~10ms.
      --probe    Actually runs each installed CLI with "say ok" and
                 reports duration. Uses your subscription (keys stripped).
                 Helpful for confirming CLAUDE_FAST is working, or
                 comparing cold-start times across CLIs.

    \b
    Examples:
      cliworker doctor                         fast scan
      cliworker doctor --probe                 + timing probe
      cliworker doctor --probe --probe-timeout 60
                                               generous timeout for cold ollama
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
    prints actionable commands for whatever's missing, then re-asks the
    paid-API-fallback preference.

    \b
    What it does:
      1. Shows the ASCII banner
      2. Scans PATH for claude / codex / gemini / ollama
      3. For each missing CLI: prints the exact install command
         (e.g. `npm i -g @openai/codex`, `brew install ollama`)
      4. Checks if ollama has the default model (llama3.1) pulled, prints
         `ollama pull llama3.1` if not
      5. Prompts: "Allow paid API fallback for any CLIs now? [y/N]"
         → no  = state.json saves paid_ok=null (free forever)
         → yes = asks "Which CLIs? (comma-separated or 'all')"
      6. Writes ~/.config/cliworker/state.json

    \b
    Examples:
      cliworker setup                run the interactive setup wizard
      rm ~/.config/cliworker/state.json && cliworker "hi"
                                     force re-setup next invocation

    Nothing is ever auto-installed. The wizard prints what to run; you run it.
    """
    firstrun.run_diagnostics()


# ---------------------------------------------------------------------------
# `skip-cache`
# ---------------------------------------------------------------------------

@main.command("skip-cache")
@click.option("--clear", "clear_name", default=None, help="CLI name to clear, or 'ALL' for everything.")
def skip_cache_cmd(clear_name: str | None) -> None:
    """Show or clear the broken-engine skip cache.

    \b
    What it is:
      When a CLI invocation fails (bad auth, quota hit, subscription
      lapsed), cliworker remembers that failure for 1 hour so subsequent
      calls don't keep retrying and eating seconds. The cache is a tiny
      JSON file at ~/.cache/cliworker/skip-cache.json (XDG-aware).

    \b
    Examples:
      cliworker skip-cache                  inspect — shows which CLIs are suppressed
      cliworker skip-cache --clear claude   un-suppress just claude
      cliworker skip-cache --clear ALL      reset everything
      rm ~/.cache/cliworker/skip-cache.json same effect as --clear ALL

    \b
    When to use:
      * Just fixed a subscription — `--clear <cli>` so cliworker retries it now
      * Debugging why a CLI isn't being tried — inspect to see if it's suppressed
      * Anytime you want a fresh slate
    """
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
