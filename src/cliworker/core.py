"""Core subprocess invocation + fallback chain."""
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


def run_cli(
    spec: CLISpec | str,
    prompt: str | None = None,
    *,
    stdin_content: str | None = None,
    strip_keys: bool = False,
    timeout_s: int = DEFAULT_TIMEOUT,
    skip_cache_check: bool = True,
    cwd: str | Path | None = None,
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
    if isinstance(spec, str):
        spec = get_spec(spec)

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


def run_with_fallback(
    specs: Iterable[CLISpec | str],
    prompt: str | None = None,
    *,
    stdin_content: str | None = None,
    strip_keys_first: bool = True,
    retry_with_keys: bool = True,
    timeout_s: int = DEFAULT_TIMEOUT,
    cwd: str | Path | None = None,
) -> list[CLIResult]:
    """Run CLIs in order, returning after the first success.

    Pass 1 (if strip_keys_first=True): try each spec with env keys stripped.
      This forces subscription-mode on CLIs that prefer paid API when keys present.
    Pass 2 (if retry_with_keys=True): try each spec with env keys present.
      This catches the case where the key IS required (no subscription available).

    Returns the full list of attempts (one CLIResult per try). The caller
    picks out the first .ok or handles multi-failure.
    """
    specs_list = [get_spec(s) if isinstance(s, str) else s for s in specs]
    results: list[CLIResult] = []

    # Fallback chain deliberately tries multiple engines and passes. Don't
    # let skip-cache short-circuit that — each individual run_cli still
    # updates skip-cache on failure, but we force every spec to get a real
    # attempt this call.
    if strip_keys_first:
        for spec in specs_list:
            r = run_cli(
                spec, prompt, stdin_content=stdin_content,
                strip_keys=True, timeout_s=timeout_s, cwd=cwd,
                skip_cache_check=False,
            )
            results.append(r)
            if r.ok:
                return results

    if retry_with_keys:
        for spec in specs_list:
            r = run_cli(
                spec, prompt, stdin_content=stdin_content,
                strip_keys=False, timeout_s=timeout_s, cwd=cwd,
                skip_cache_check=False,
            )
            results.append(r)
            if r.ok:
                return results

    return results
