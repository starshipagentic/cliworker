"""Core subprocess invocation + chained use."""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from cliworker.fastflags import gemini_stripped_mcp
from cliworker.registry import CLISpec, get_spec
from cliworker.skipcache import is_skipped, mark_broken


DEFAULT_TIMEOUT = 600


@dataclass
class CLIResult:
    """Result of one CLI invocation."""
    spec: CLISpec
    ok: bool
    stdout: str
    stderr: str
    duration_s: float
    returncode: Optional[int]
    argv: list[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None  # "not_on_path", "skip_cache", etc.

    @property
    def text(self) -> str:
        """Convenience: stdout if ok else stderr."""
        return self.stdout if self.ok else self.stderr


def _build_env(spec: CLISpec, strip_keys: bool) -> dict[str, str]:
    env = dict(os.environ)
    if strip_keys:
        for var in spec.env_strip:
            env.pop(var, None)
    return env


def _which(binary: str) -> Optional[str]:
    """Lightweight shutil.which wrapper — easy to monkeypatch in tests."""
    import shutil

    return shutil.which(binary)


def run(
    spec: CLISpec | str,
    prompt: str | None = None,
    *,
    model: str | None = None,
    fast: bool | None = None,
    stdin_content: str | None = None,
    strip_keys: bool = False,
    timeout_s: int = DEFAULT_TIMEOUT,
    skip_cache_check: bool = True,
    cwd: str | Path | None = None,
) -> CLIResult:
    """Invoke ONE CLI subprocess, return a CLIResult.

    Friendly call-site shortcuts:
        run("claude", "hello")                        # simplest
        run("claude", "hello", model="sonnet")
        run("gemini", "hi", fast=False)               # disable speed flags
        run("claude", "summarize:", stdin_content=long_text)

    When `spec` is a string, it's looked up in KNOWN_CLIS. `model` and `fast`
    override the spec's defaults without requiring a dataclass-replace dance.
    For anything exotic, pass a pre-built CLISpec instead of a string.
    """
    # String-name → spec with optional overrides
    if isinstance(spec, str):
        overrides = {}
        if model is not None:
            overrides["model"] = model
        if fast is not None:
            overrides["fast"] = fast
        spec = get_spec(spec, **overrides)
    elif model is not None or fast is not None:
        # spec is already a CLISpec; apply overrides
        from dataclasses import replace

        overrides = {}
        if model is not None:
            overrides["model"] = model
        if fast is not None:
            overrides["fast"] = fast
        spec = replace(spec, **overrides)

    return _run_impl(
        spec, prompt,
        stdin_content=stdin_content, strip_keys=strip_keys,
        timeout_s=timeout_s, skip_cache_check=skip_cache_check, cwd=cwd,
    )


def _run_impl(
    spec: CLISpec,
    prompt: str | None,
    *,
    stdin_content: str | None,
    strip_keys: bool,
    timeout_s: int,
    skip_cache_check: bool,
    cwd: str | Path | None,
) -> CLIResult:
    """Invoke one CLI subprocess, return a CLIResult.

    Args:
      spec             CLISpec or string name (looked up in KNOWN_CLIS).
      prompt           Instruction string. Passed per spec.prompt_flag.
      stdin_content    Optional bulk content piped as stdin. Recommended for
                       long transcripts — keeps argv clean.
      strip_keys       Force subscription mode by stripping env keys from spec.env_strip.
      timeout_s        Subprocess timeout in seconds.
      skip_cache_check If True (default), bail early if cli is in skip-cache.
      cwd              Working directory for the subprocess.
    """
    if skip_cache_check and is_skipped(spec.cli):
        return CLIResult(
            spec=spec, ok=False, stdout="", stderr=f"{spec.cli} is in skip-cache (recent failure)",
            duration_s=0.0, returncode=None, argv=[],
            skipped_reason="skip_cache",
        )

    if _which(spec.cli) is None:
        return CLIResult(
            spec=spec, ok=False, stdout="", stderr=f"{spec.cli} not found on PATH",
            duration_s=0.0, returncode=None, argv=[],
            skipped_reason="not_on_path",
        )

    argv = spec.build_argv(prompt)
    env = _build_env(spec, strip_keys)

    # Gemini needs MCP strip/restore at fs level; wrap in context manager.
    # No-op for other CLIs.
    def _invoke() -> CLIResult:
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                input=stdin_content,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=env,
                cwd=str(cwd) if cwd else None,
                check=False,
            )
            duration = time.monotonic() - start
            ok = proc.returncode == 0
            return CLIResult(
                spec=spec, ok=ok, stdout=proc.stdout, stderr=proc.stderr,
                duration_s=duration, returncode=proc.returncode, argv=argv,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return CLIResult(
                spec=spec, ok=False, stdout="", stderr=f"timeout after {timeout_s}s",
                duration_s=duration, returncode=None, argv=argv,
            )
        except FileNotFoundError:
            duration = time.monotonic() - start
            return CLIResult(
                spec=spec, ok=False, stdout="", stderr=f"{spec.cli} binary disappeared mid-call",
                duration_s=duration, returncode=None, argv=argv,
                skipped_reason="not_on_path",
            )

    if spec.cli == "gemini" and spec.fast:
        with gemini_stripped_mcp():
            result = _invoke()
    else:
        result = _invoke()

    if not result.ok and result.skipped_reason is None:
        # Record as broken only for genuine run failures, not not-on-path edge case
        mark_broken(spec.cli)

    return result


def use(
    specs: Iterable[CLISpec | str],
    prompt: str | None = None,
    *,
    stdin_content: str | None = None,
    free_first: bool = True,
    retry_paid: bool = True,
    timeout_s: int = DEFAULT_TIMEOUT,
    cwd: str | Path | None = None,
) -> list[CLIResult]:
    """Use a list of CLIs in order — try the first, fall through to later
    ones only if earlier ones fail. Returns the full list of attempts; the
    first success stops the chain.

    Example:
        results = use(["claude", "codex", "gemini"], "summarize this")
        first_ok = next((r for r in results if r.ok), None)
        if first_ok:
            print(first_ok.stdout)

    Two passes, both optional:
      Pass 1 (free_first=True, default) — strip each spec's env_strip keys
        before invoking. For claude/codex/gemini this forces subscription
        mode rather than burning API credits. Claude CLI, for instance,
        prefers ANTHROPIC_API_KEY over your Claude.ai subscription when the
        var is set; stripping flips it back.
      Pass 2 (retry_paid=True, default) — retry each spec with env keys
        intact, in case subscription is unavailable and the paid API is
        the only path that works. Set retry_paid=False to stay free-only.

    Returns the full list of attempts in order, stopping at the first
    success. If every spec fails both passes, the list has one entry per
    spec per pass.
    """
    specs_list = [get_spec(s) if isinstance(s, str) else s for s in specs]
    results: list[CLIResult] = []

    # Force every spec to get a real attempt this call — a chained `use`
    # is explicit intent, not something skip-cache should short-circuit.
    if free_first:
        for spec in specs_list:
            r = _run_impl(
                spec, prompt, stdin_content=stdin_content,
                strip_keys=True, timeout_s=timeout_s, cwd=cwd,
                skip_cache_check=False,
            )
            results.append(r)
            if r.ok:
                return results

    if retry_paid:
        for spec in specs_list:
            r = _run_impl(
                spec, prompt, stdin_content=stdin_content,
                strip_keys=False, timeout_s=timeout_s, cwd=cwd,
                skip_cache_check=False,
            )
            results.append(r)
            if r.ok:
                return results

    return results


