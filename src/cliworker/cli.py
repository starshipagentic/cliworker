"""cliworker CLI — natural-language-ish invocation.

Primary shape:
    cliworker "what is TCP?"                       default chain, full mode
    cliworker "what is TCP?" --fast                default chain, fast mode
    cliworker "what is TCP?" run claude            one CLI, full mode
    cliworker "what is TCP?" run claude gemini     chain, full mode
    cliworker "what is TCP?" run claude --fast     one CLI, fast mode
    cliworker --run claude,gemini "what is TCP?"   flag form (scripts)

Subcommands (diagnostic, no prompt):
    cliworker doctor
    cliworker info [cli]
    cliworker skip-cache [--clear ...]
    cliworker setup

Library equivalents:
    from cliworker import run, run_fast
    run("hi")                                   # default chain
    run("hi", "claude", "codex")                # explicit chain
    run_fast("hi", "claude")                    # fast mode
"""
from __future__ import annotations

import sys

import click

from cliworker import __version__, run
from cliworker.detect import detect
from cliworker.registry import KNOWN_CLIS, get_spec
from cliworker import firstrun, state


# ---------------------------------------------------------------------------
# argv preprocessing: turn `... run cli1 cli2 [...]` into `... --run cli1,cli2 ...`
# ---------------------------------------------------------------------------

def _rewrite_run_keyword(argv: list[str]) -> list[str]:
    """If 'run' appears as a bare arg (and NOT as a registered subcommand),
    convert the following cli-name tokens into a single `--run cli1,cli2` option.

    Rules:
      * `run` must appear as its own argv token (not embedded in a longer word).
      * `run` must NOT be the first positional arg (that's how you'd run
        `cliworker run ...` if we ever had such a subcommand — we don't).
      * Everything after `run` that does NOT start with `-` is a CLI name,
        until the next `-flag` or end of argv.
      * If no CLI names follow `run`, leave argv unchanged (probably a
        prompt containing the literal word 'run').
    """
    if "run" not in argv:
        return argv
    idx = argv.index("run")
    # If 'run' is the very first positional, it might be misread — but we
    # don't have a `run` subcommand, so treat it as the connector keyword.
    clis: list[str] = []
    i = idx + 1
    while i < len(argv) and not argv[i].startswith("-"):
        clis.append(argv[i])
        i += 1
    if not clis:
        return argv
    rest = argv[i:]
    return argv[:idx] + ["--run", ",".join(clis)] + rest


# ---------------------------------------------------------------------------
# Main group
# ---------------------------------------------------------------------------

HELP_EPILOG = """\
\b
Shell — the natural shape
  cliworker "what is TCP?"                     default chain, full mode
  cliworker "what is TCP?" --fast              default chain, fast mode
  cliworker "hi" run claude                    one CLI, full mode
  cliworker "hi" run claude gemini             chain in stated order
  cliworker "hi" run claude --fast             one CLI, fast mode
  cliworker --run claude,gemini "hi"           flag form (scripts)
  cliworker "summarize:" < file.txt --fast     stdin content, fast mode

\b
Options on any bare-prompt invocation
  --run TEXT           CLI names to try (comma-separated), in order.
  --fast               Strip MCP / tools / chrome startup — ~18s → ~4s on claude.
                       Default is full mode (everything loaded).
  -m, --model TEXT     Model override (sonnet, gemini-2.5-flash, gemma3:4b).
  --paid-ok TEXT       Allow paid API fallback. 'all' or 'claude,codex,...'.
                       Default: never. Stays free-only unless you opt in.
  --timeout INTEGER    Seconds per CLI before we give up. Default 120.
  -v, --verbose        Log winner CLI + duration on stderr.

\b
Fast mode (--fast) — when to use
  --fast is for pure text-in, text-out tasks: summarization, translation,
  quick answers. It strips:
    * claude: --tools "" --no-chrome --strict-mcp-config --no-session-persistence
    * gemini: removes mcpServers from ~/.gemini/settings.json during the call
    * codex/ollama: no-op (already lightweight)
  Leave it off when you want the full tool/MCP environment (agent workflows,
  code reading/writing, MCP-powered research).

\b
Paid API — opt-in examples
  cliworker "hi"                               free only (default)
  cliworker "hi" --paid-ok all                 paid OK for every CLI
  cliworker "hi" --paid-ok claude              paid OK for claude only
  cliworker "hi" run claude codex --paid-ok claude
                                               run both, only claude may pay

\b
Diagnostic subcommands
  cliworker doctor                             which CLIs are installed?
  cliworker doctor --probe                     ping each with "say ok"
  cliworker info                               show default argv per CLI
  cliworker info claude                        show one CLI's recipe
  cliworker setup                              re-run first-run diagnostics
  cliworker skip-cache                         inspect broken-engine cache
  cliworker skip-cache --clear ALL             clear it all

\b
Python library
  from cliworker import run, run_fast
  r = run("hi")                                # default chain, full mode
  r = run("hi", "claude")                      # one CLI
  r = run("hi", "claude", "codex")             # chain
  r = run_fast("hi", "claude")                 # fast mode (sugar for fast=True)
  r = run("hi", "claude", paid_ok=["claude"])  # paid OK for claude only

\b
State
  First-run saves default chain + paid_ok preference to
  ~/.cliworker/state.json (or $XDG_CONFIG_HOME/cliworker/state.json
  if that env var is explicitly set). Edit anytime or re-run
  `cliworker setup`.

\b
Full docs: https://github.com/starshipagentic/cliworker
"""


class PromptOrSubcommandGroup(click.Group):
    """Group that treats the first non-option arg as a prompt if it's not a
    registered subcommand. Also preprocesses the `run` keyword into --run."""

    def parse_args(self, ctx, args):
        args = _rewrite_run_keyword(list(args))
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
      cliworker "what is TCP?"                     default chain, full
      cliworker "hi" run claude gemini             specific CLIs in order
      cliworker "summarize:" --fast                fast mode for quick tasks

    \b
    Full mode (default) loads your full MCP/tool environment — like running
    `claude -p` yourself. Fast mode (--fast) strips it down for quick
    text-only tasks; see --help for what it strips.

    \b
    Default is FREE-ONLY: cliworker strips API-key env vars before invoking
    each CLI, forcing subscription mode. Paid API is opt-in via --paid-ok.

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
@click.option("--run", "run_csv", default=None, help="Comma-separated CLI names to try in order.")
@click.option("--fast", is_flag=True, default=False, help="Strip MCP/tools/chrome for speed.")
@click.option("--model", "-m", default=None, help="Model override (sonnet, gemini-2.5-flash, gemma3:4b).")
@click.option("--timeout", default=120, show_default=True, help="Seconds before we give up on a single CLI.")
@click.option(
    "--paid-ok",
    "paid_ok_raw",
    default=None,
    help=(
        "Allow paid API fallback. Pass 'all' for every CLI, or a "
        "comma-separated list like 'claude,codex'. Default: never."
    ),
)
@click.option("--verbose", "-v", is_flag=True, help="Show which CLI answered + duration on stderr.")
def _ask(
    prompt_parts: tuple[str, ...],
    run_csv: str | None,
    fast: bool,
    model: str | None,
    timeout: int,
    paid_ok_raw: str | None,
    verbose: bool,
) -> None:
    """Default invocation: ask installed LLM CLIs a prompt."""
    # First-run diagnostics — only if state file doesn't exist yet
    if not state.exists():
        firstrun.run_diagnostics()
        if not state.default_chain():
            sys.exit(1)

    prompt = " ".join(prompt_parts).strip() if prompt_parts else ""
    stdin_content: str | None = None
    if not sys.stdin.isatty():
        stdin_content = sys.stdin.read()

    if not prompt and not stdin_content:
        click.echo("Usage: cliworker \"your prompt here\"", err=True)
        click.echo("Run `cliworker --help` for full examples.", err=True)
        sys.exit(2)

    if not prompt and stdin_content:
        prompt = stdin_content
        stdin_content = None

    # Determine which CLIs to use
    if run_csv:
        cli_names = [c.strip() for c in run_csv.split(",") if c.strip()]
    else:
        cli_names = state.default_chain()

    if not cli_names:
        click.echo("No LLM CLIs available. Install one and re-run:", err=True)
        for name in ("claude", "codex", "gemini", "ollama"):
            for line in firstrun.INSTALL_HINTS[name]:
                click.echo(f"  {line}", err=True)
        sys.exit(1)

    # Build specs with optional model override
    specs = [get_spec(name, model=model) if model else get_spec(name) for name in cli_names]

    # Resolve paid_ok from flag or state
    if paid_ok_raw is None:
        saved = state.load().get("paid_ok")
        paid_ok = saved if isinstance(saved, (list, bool)) or saved is None else None
    elif paid_ok_raw.lower() == "all":
        paid_ok = True
    else:
        paid_ok = [c.strip() for c in paid_ok_raw.split(",") if c.strip()]

    # Fire the chain
    results = run(
        prompt, *specs,
        fast=True if fast else None,  # None = respect spec default (full)
        paid_ok=paid_ok,
        timeout_s=timeout,
        stdin_content=stdin_content,
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
    """
    names = [cli_name] if cli_name else list(KNOWN_CLIS.keys())
    for name in names:
        spec = get_spec(name)
        click.echo(click.style(f"{name}", bold=True))
        click.echo(f"  cli binary:        {spec.cli}")
        click.echo(f"  subcommand:        {spec.subcommand or '(none)'}")
        click.echo(f"  prompt transport:  {spec.prompt_flag} ({spec.prompt_flag_name})")
        click.echo(f"  fast flags:        {'ON' if spec.fast else 'off (full mode default)'}")
        click.echo(f"  env vars stripped: {spec.env_strip or '(none)'}")
        click.echo(f"  sample argv:       {spec.build_argv('<PROMPT>')}")
        click.echo()


# ---------------------------------------------------------------------------
# `doctor`
# ---------------------------------------------------------------------------

@main.command()
@click.option("--probe/--no-probe", default=False, help="Invoke each installed CLI with a tiny prompt to measure cold start.")
@click.option("--probe-timeout", default=60, show_default=True)
@click.option("--fast", "probe_fast", is_flag=True, default=False, help="Probe with fast mode (skip MCP/tools).")
def doctor(probe: bool, probe_timeout: int, probe_fast: bool) -> None:
    """Scan PATH for installed LLM CLIs; optionally probe with a live call.

    \b
    Two modes:
      (default)  Fast PATH scan only. Never invokes anything. ~10ms.
      --probe    Actually runs each installed CLI with "say ok" and
                 reports duration. Uses subscription (keys stripped).

    \b
    Probing uses the per-spec default (full mode). Add --fast to probe
    the speed-flagged path instead.

    \b
    Examples:
      cliworker doctor                          fast scan, no network
      cliworker doctor --probe                  probe in full mode (slow)
      cliworker doctor --probe --fast           probe in fast mode (quick)
      cliworker doctor --probe --probe-timeout 120
                                                generous timeout for cold ollama
    """
    presences = detect()
    for name, p in presences.items():
        status = click.style("✓", fg="green") if p.installed else click.style("✗", fg="red")
        right = str(p.binary_path) if p.installed else click.style(p.install_hint, dim=True)
        click.echo(f"  {status}  {name:8}  {right}")

    if probe:
        click.echo()
        mode_label = "fast mode" if probe_fast else "full mode"
        click.echo(click.style(
            f"Probing installed CLIs (one-shot 'say ok', subscription, {mode_label})...",
            bold=True,
        ))
        from cliworker.skipcache import DEFAULT_CACHE_PATH, clear

        clear(None, path=DEFAULT_CACHE_PATH)
        for name, p in presences.items():
            if not p.installed:
                continue
            results = run(
                "Say exactly: ok",
                name,
                fast=True if probe_fast else None,
                timeout_s=probe_timeout,
            )
            r = results[0] if results else None
            if r and r.ok:
                click.echo(f"  {name:8}  {r.duration_s:5.2f}s  {click.style('ok', fg='green')}")
            elif r:
                err = r.stderr.strip()[:80] or "(no stderr)"
                click.echo(f"  {name:8}  {r.duration_s:5.2f}s  {click.style('fail', fg='red')}  {err}")
            else:
                click.echo(f"  {name:8}  —      {click.style('no result', fg='red')}")


# ---------------------------------------------------------------------------
# `setup`
# ---------------------------------------------------------------------------

@main.command()
def setup() -> None:
    """Re-run the first-run diagnostics. Never auto-installs anything.

    \b
    What it does:
      1. Shows the ASCII banner
      2. Scans PATH for claude / codex / gemini / ollama
      3. For missing CLIs: prints exact install command (e.g. `npm i -g @openai/codex`)
      4. For ollama: checks if default model (gemma3:4b) is pulled;
         prints `ollama pull gemma3:4b` if not
      5. Prompts: "Allow paid API fallback for any CLIs now? [y/N]"
      6. Writes ~/.cliworker/state.json

    \b
    Examples:
      cliworker setup
      rm ~/.cliworker/state.json && cliworker "hi"
                                             force re-setup on next call

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
      JSON file at ~/.cliworker/skip-cache.json.

    \b
    Examples:
      cliworker skip-cache                 inspect — shows which CLIs are suppressed
      cliworker skip-cache --clear claude  un-suppress just claude
      cliworker skip-cache --clear ALL     reset everything
      rm ~/.cliworker/skip-cache.json      same effect as --clear ALL

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
