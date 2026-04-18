"""Per-CLI configuration: how to build the argv, how to pass the prompt,
which env-var to strip for subscription-mode, whether to apply fast-flags.

`KNOWN_CLIS` is a dict of CLI-name → default CLISpec. Downstream callers
(paircode, navcom, your own code) either use these defaults or override.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from cliworker.fastflags import CLAUDE_FAST_FLAGS


PromptTransport = Literal["positional", "flag", "stdin"]


@dataclass(frozen=True)
class CLISpec:
    """Declarative recipe for one CLI invocation.

    cli             The binary name on PATH (claude, codex, gemini, ollama).
    subcommand      Subcommand between binary and prompt (e.g., "exec" for codex).
    model           Optional model string — passed with `model_flag` if set.
    model_flag      Flag for model (default `--model`; gemini uses `-m`).
    prompt_flag     How to pass the prompt:
                      "positional" → as bare final arg (claude -p "<prompt>")
                      "flag"       → under an explicit flag (gemini -p "<prompt>")
                      "stdin"      → via stdin only (no prompt arg)
                    Default "positional".
    prompt_flag_name  When prompt_flag="flag", the flag name (default "-p").
    fast            Whether to apply per-CLI fast-flag tricks. On by default.
    extra_args      Extra static args appended before the prompt (e.g., --output-format text).
    env_strip       Env vars to DELETE when invoking (subscription-mode forcing).
                    On claude: ANTHROPIC_API_KEY
                    On codex:  OPENAI_API_KEY
                    On gemini: GOOGLE_API_KEY, GEMINI_API_KEY
    """

    cli: str
    subcommand: Optional[str] = None
    model: Optional[str] = None
    model_flag: str = "--model"
    prompt_flag: PromptTransport = "positional"
    prompt_flag_name: str = "-p"
    fast: bool = True
    extra_args: list[str] = field(default_factory=list)
    env_strip: list[str] = field(default_factory=list)

    def build_argv(self, prompt: str | None) -> list[str]:
        """Assemble the full subprocess argv for this spec + prompt."""
        argv: list[str] = [self.cli]
        if self.subcommand:
            argv.append(self.subcommand)

        # Some CLIs put -p before the prompt (claude), some need -m first (gemini)
        if self.cli == "claude":
            argv.append("-p")

        if self.model:
            if self.model_flag:
                argv.extend([self.model_flag, self.model])
            else:
                # Empty model_flag means "model is a bare positional".
                # Used by ollama: `ollama run <model> <prompt>`.
                argv.append(self.model)

        # Fast flags applied per-CLI
        if self.fast:
            argv.extend(_fast_flags_for(self.cli))

        argv.extend(self.extra_args)

        if prompt is not None:
            if self.prompt_flag == "positional":
                argv.append(prompt)
            elif self.prompt_flag == "flag":
                argv.extend([self.prompt_flag_name, prompt])
            # "stdin" — no prompt arg; caller pipes via stdin

        return argv


def _fast_flags_for(cli: str) -> list[str]:
    if cli == "claude":
        return list(CLAUDE_FAST_FLAGS)
    # codex, ollama, gemini: no argv-level fast flags.
    # (gemini uses fs-level MCP strip handled in core.run_cli)
    return []


# Default specs — override via get_spec(name, model=...) or build your own CLISpec.
KNOWN_CLIS: dict[str, CLISpec] = {
    "claude": CLISpec(
        cli="claude",
        prompt_flag="positional",
        fast=True,
        env_strip=["ANTHROPIC_API_KEY"],
    ),
    "codex": CLISpec(
        cli="codex",
        subcommand="exec",
        prompt_flag="positional",
        fast=False,                  # codex exec already light
        extra_args=["--dangerously-bypass-approvals-and-sandbox"],
        env_strip=["OPENAI_API_KEY"],
    ),
    "gemini": CLISpec(
        cli="gemini",
        prompt_flag="flag",          # gemini uses -p <value>
        prompt_flag_name="-p",
        model_flag="-m",
        fast=True,                   # triggers gemini MCP strip-and-restore in core
        env_strip=["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    ),
    "ollama": CLISpec(
        cli="ollama",
        subcommand="run",
        prompt_flag="positional",    # ollama run <model> <prompt>
        model_flag="",               # model is a bare positional, not a flag
        fast=False,
        env_strip=[],                # local, no subscription concept
    ),
}


def get_spec(name: str, **overrides) -> CLISpec:
    """Return a CLISpec for `name`, optionally overriding any field."""
    base = KNOWN_CLIS.get(name)
    if base is None:
        # Unknown CLI — construct a minimal default
        base = CLISpec(cli=name)
    if not overrides:
        return base
    from dataclasses import replace

    return replace(base, **overrides)
