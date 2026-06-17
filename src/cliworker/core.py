"""Core subprocess invocation + chained runs.

Public API (two functions):

    run(prompt, *clis, fast=None, paid_ok=None, ...)       -> list[CLIResult]
    run_fast(prompt, *clis, **kwargs)                      -> list[CLIResult]

`run()` tries the given CLIs in order, returning the full list of attempts
(first success stops the chain). With no CLIs passed, falls back to the
default chain from ~/.cliworker/state.json.

`run_fast()` is a one-line wrapper that forces `fast=True`.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

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
    timeout_kind: Optional[str] = None  # "startup_idle" | "hard" | None (only set on timeout failures)

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


# ---------------------------------------------------------------------------
# Subprocess watchdog — catches silent-startup hangs that eat the full timeout
# ---------------------------------------------------------------------------

DEFAULT_STARTUP_IDLE_S = 30


class _StreamReader(threading.Thread):
    """Daemon thread reading a subprocess stream into a bytes buffer.

    Records `last_byte_at` (monotonic time) on every chunk so the watchdog
    loop can detect "process spawned, never wrote a byte" — i.e. a silent
    startup hang. Uses `read1()` to avoid buffering deadlocks.
    """

    def __init__(self, stream):
        super().__init__(daemon=True)
        self.stream = stream
        self.buf: list[bytes] = []
        self.last_byte_at: float = 0.0  # 0 means "nothing yet"

    def run(self) -> None:
        try:
            while True:
                chunk = self.stream.read1(4096)
                if not chunk:
                    return
                self.buf.append(chunk)
                self.last_byte_at = time.monotonic()
        except Exception:
            return


def _terminate_process_tree(proc: subprocess.Popen, grace_s: int = 2) -> None:
    """SIGTERM the process group, wait, SIGKILL if still alive. POSIX-only."""
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return
    try:
        proc.wait(timeout=grace_s)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        pass


_ORIG_SUBPROCESS_RUN = subprocess.run


def _run_with_watchdog(
    argv: list[str],
    *,
    env: Optional[dict[str, str]] = None,
    cwd: Optional[str] = None,
    stdin_content: Optional[str] = None,
    timeout_s: int = DEFAULT_TIMEOUT,
    startup_idle_s: int = DEFAULT_STARTUP_IDLE_S,
) -> tuple[Optional[int], str, str, float, Optional[str]]:
    """Spawn a subprocess with a startup-idle watchdog.

    Kills the process if:
      - elapsed > `timeout_s` (hard timeout), or
      - elapsed > `startup_idle_s` AND zero bytes of stdout+stderr have been
        seen (silent startup hang — the codex-exec-hang class).

    Uses `start_new_session=True` + `os.killpg` so child processes (sandbox
    helpers, git, etc.) are reaped cleanly.

    Returns: (returncode, stdout, stderr, duration_s, timeout_kind).
    `timeout_kind` is None on normal exit, "startup_idle" or "hard" on kill.
    """
    # Compat shim: if a test has monkeypatched subprocess.run, honor it — tests
    # predating the watchdog expect to intercept the subprocess.run path and
    # return a CompletedProcess. Adapt that CompletedProcess back to our tuple.
    if subprocess.run is not _ORIG_SUBPROCESS_RUN:
        start_compat = time.monotonic()
        kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": timeout_s,
            "env": env,
            "cwd": cwd,
            "check": False,
        }
        if stdin_content is not None:
            kwargs["input"] = stdin_content
        else:
            kwargs["stdin"] = subprocess.DEVNULL
        try:
            proc = subprocess.run(argv, **kwargs)
            duration = time.monotonic() - start_compat
            return (proc.returncode, proc.stdout or "", proc.stderr or "", duration, None)
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start_compat
            return (None, "", "", duration, "hard")

    start = time.monotonic()
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if stdin_content is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
        start_new_session=True,
    )

    out_reader = _StreamReader(proc.stdout)
    err_reader = _StreamReader(proc.stderr)
    out_reader.start()
    err_reader.start()

    if stdin_content is not None:
        def _write_stdin() -> None:
            try:
                proc.stdin.write(stdin_content.encode())
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        threading.Thread(target=_write_stdin, daemon=True).start()

    timeout_kind: Optional[str] = None
    while True:
        if proc.poll() is not None:
            break
        now = time.monotonic()
        elapsed = now - start
        if elapsed > timeout_s:
            timeout_kind = "hard"
            break
        if (
            startup_idle_s
            and elapsed > startup_idle_s
            and out_reader.last_byte_at == 0.0
            and err_reader.last_byte_at == 0.0
        ):
            timeout_kind = "startup_idle"
            break
        time.sleep(0.25)

    if timeout_kind is not None:
        _terminate_process_tree(proc, grace_s=2)

    out_reader.join(timeout=2)
    err_reader.join(timeout=2)

    stdout = b"".join(out_reader.buf).decode(errors="replace")
    stderr = b"".join(err_reader.buf).decode(errors="replace")
    duration = time.monotonic() - start
    return proc.returncode, stdout, stderr, duration, timeout_kind


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(
    prompt: str,
    *clis: str | CLISpec,
    fast: bool | None = None,
    paid_ok: bool | list[str] | None = None,
    timeout_s: int = DEFAULT_TIMEOUT,
    stdin_content: str | None = None,
    cwd: str | Path | None = None,
) -> list[CLIResult]:
    """Run a prompt against one or more CLIs, in order. Returns all attempts
    (short-circuits on the first success).

    Usage:
        run("hi")                                   # default chain from state.json
        run("hi", "claude")                         # one CLI
        run("hi", "claude", "codex")                # two CLIs in order
        run("hi", CLISpec(cli="claude", fast=True)) # custom spec
        run("hi", *saved_list)                      # spread a variable

    Kwargs:
        fast        None = respect each spec's own fast field (default)
                    True  = force fast mode on every spec (strip CLAUDE_FAST /
                            gemini MCP)
                    False = force full mode on every spec
        paid_ok     None (default) = free/subscription only, never paid API
                    True           = paid OK for every CLI in the chain
                    list[str]      = paid OK only for those CLI names
        timeout_s   Seconds per CLI before giving up.
        stdin_content  Optional bulk content piped via stdin. Keeps argv clean.
        cwd         Working directory for the subprocess.

    If `clis` is empty, uses the default chain from ~/.cliworker/state.json.
    Returns an empty list only if there's no state and no explicit CLIs.
    """
    # Default-chain fallback when no CLIs are passed
    if not clis:
        from cliworker.state import default_chain

        chain = default_chain()
        if not chain:
            return []
        clis = tuple(chain)

    # Resolve strings to specs
    specs_list = [get_spec(c) if isinstance(c, str) else c for c in clis]

    # Apply `fast` override if caller specified it
    if fast is not None:
        specs_list = [replace(s, fast=fast) for s in specs_list]

    results: list[CLIResult] = []

    # Pass 1: every spec with env API keys STRIPPED (subscription mode)
    for spec in specs_list:
        r = _run_impl(
            spec, prompt, stdin_content=stdin_content,
            strip_keys=True, timeout_s=timeout_s, cwd=cwd,
            skip_cache_check=False,
        )
        results.append(r)
        if r.ok:
            return results

    # Pass 2: paid-API retry — only for specs the caller authorized
    if paid_ok:
        paid_specs = [
            s for s in specs_list
            if paid_ok is True or (isinstance(paid_ok, list) and s.cli in paid_ok)
        ]
        for spec in paid_specs:
            r = _run_impl(
                spec, prompt, stdin_content=stdin_content,
                strip_keys=False, timeout_s=timeout_s, cwd=cwd,
                skip_cache_check=False,
            )
            results.append(r)
            if r.ok:
                return results

    return results


def run_fast(prompt: str, *clis: str | CLISpec, **kwargs) -> list[CLIResult]:
    """Shortcut for `run(..., fast=True)`.

    Forces every spec in the chain into fast mode (CLAUDE_FAST flags for
    claude, gemini MCP strip for gemini, no-op for codex/ollama).
    All other kwargs (`paid_ok`, `timeout_s`, `stdin_content`, `cwd`)
    behave identically to `run()`.

    Usage:
        run_fast("summarize:", "claude", stdin_content=big_text)
        run_fast("hi", "claude", "codex", paid_ok=["claude"])
    """
    return run(prompt, *clis, fast=True, **kwargs)


def invoke(
    cli: str,
    *args: str,
    timeout_s: int = DEFAULT_TIMEOUT,
    stdin_content: str | None = None,
    cwd: str | Path | None = None,
    check_skip_cache: bool = False,
) -> CLIResult:
    """Run an arbitrary CLI subprocess. No LLM semantics.

    Unlike `run()` / `run_fast()`, this primitive:
      * does NOT strip any env API keys (no subscription-mode forcing)
      * does NOT apply fast flags (no CLAUDE_FAST, no gemini MCP strip)
      * does NOT run two passes / paid_ok retry
      * does NOT require the CLI to be in KNOWN_CLIS

    It's for admin commands and one-off subprocess calls — `codex marketplace add`,
    `gemini extensions install`, `claude mcp list`, etc. — where you just want
    cliworker's subprocess plumbing (timeout, error-capture, CLIResult shape,
    FileNotFoundError handling) without the LLM-invocation baggage.

    Defaults that differ from `run()`:
      * stdin is closed (`DEVNULL`) when `stdin_content is None`. Admin commands
        that accidentally hit an interactive prompt fail fast instead of hanging.
      * skip-cache is OFF by default — admin commands are infrequent and
        shouldn't inherit peer-review-loop failure tracking.

    Usage:
        result = invoke("codex", "marketplace", "add", "owner/repo")
        if result.ok:
            ...
        else:
            print(f"Run yourself: {' '.join(result.argv)}")
            print(result.stderr)
    """
    # Build a minimal spec for the CLIResult — just for the .cli field.
    # Callers pass a CLI name string; we don't require it to be in KNOWN_CLIS.
    spec = CLISpec(cli=cli)

    if check_skip_cache and is_skipped(cli):
        return CLIResult(
            spec=spec, ok=False, stdout="",
            stderr=f"{cli} is in skip-cache (recent failure)",
            duration_s=0.0, returncode=None, argv=[],
            skipped_reason="skip_cache",
        )

    if _which(cli) is None:
        return CLIResult(
            spec=spec, ok=False, stdout="",
            stderr=f"{cli} not found on PATH",
            duration_s=0.0, returncode=None, argv=[],
            skipped_reason="not_on_path",
        )

    argv = [cli, *args]

    try:
        rc, stdout, stderr, duration, timeout_kind = _run_with_watchdog(
            argv,
            cwd=str(cwd) if cwd else None,
            stdin_content=stdin_content,
            timeout_s=timeout_s,
        )
    except FileNotFoundError:
        return CLIResult(
            spec=spec, ok=False, stdout="",
            stderr=f"{cli} binary disappeared mid-call",
            duration_s=0.0, returncode=None, argv=argv,
            skipped_reason="not_on_path",
        )

    if timeout_kind == "hard":
        return CLIResult(
            spec=spec, ok=False, stdout=stdout,
            stderr=stderr or f"timeout after {timeout_s}s",
            duration_s=duration, returncode=None, argv=argv,
            timeout_kind="hard",
        )
    if timeout_kind == "startup_idle":
        return CLIResult(
            spec=spec, ok=False, stdout=stdout,
            stderr=stderr or f"startup-idle hang: no output in first {DEFAULT_STARTUP_IDLE_S}s (process killed)",
            duration_s=duration, returncode=None, argv=argv,
            timeout_kind="startup_idle",
        )

    return CLIResult(
        spec=spec, ok=(rc == 0),
        stdout=stdout, stderr=stderr,
        duration_s=duration, returncode=rc, argv=argv,
    )


# ---------------------------------------------------------------------------
# Internal — single-CLI subprocess invocation
# ---------------------------------------------------------------------------

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
    """Invoke one CLI subprocess, return a CLIResult. Internal only — callers
    should use `run()` which handles chains + default chain + paid_ok."""
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

    def _invoke() -> CLIResult:
        try:
            rc, stdout, stderr, duration, timeout_kind = _run_with_watchdog(
                argv,
                env=env,
                cwd=str(cwd) if cwd else None,
                stdin_content=stdin_content,
                timeout_s=timeout_s,
                startup_idle_s=spec.startup_idle_s or DEFAULT_STARTUP_IDLE_S,
            )
        except FileNotFoundError:
            return CLIResult(
                spec=spec, ok=False, stdout="", stderr=f"{spec.cli} binary disappeared mid-call",
                duration_s=0.0, returncode=None, argv=argv,
                skipped_reason="not_on_path",
            )
        if timeout_kind == "hard":
            return CLIResult(
                spec=spec, ok=False, stdout=stdout, stderr=stderr or f"timeout after {timeout_s}s",
                duration_s=duration, returncode=None, argv=argv,
                timeout_kind="hard",
            )
        if timeout_kind == "startup_idle":
            return CLIResult(
                spec=spec, ok=False, stdout=stdout,
                stderr=stderr or f"startup-idle hang: no output in first {spec.startup_idle_s or DEFAULT_STARTUP_IDLE_S}s (process killed)",
                duration_s=duration, returncode=None, argv=argv,
                timeout_kind="startup_idle",
            )
        ok = rc == 0
        # Friendlier error for ollama's cryptic "invalid model name" — it
        # actually means the model isn't pulled. Point the user at the fix.
        if not ok and spec.cli == "ollama" and stderr and "invalid model" in stderr.lower():
            model_in_argv = argv[2] if len(argv) >= 3 else "gemma3:4b"
            stderr = (
                f"ollama model '{model_in_argv}' not pulled. "
                f"Run: ollama pull {model_in_argv}\n"
                f"(original ollama error: {stderr.strip()})"
            )
        return CLIResult(
            spec=spec, ok=ok, stdout=stdout, stderr=stderr,
            duration_s=duration, returncode=rc, argv=argv,
        )

    # Gemini needs MCP strip/restore at fs level when fast; wrap with it.
    # No-op for other CLIs.
    if spec.cli == "gemini" and spec.fast:
        with gemini_stripped_mcp():
            result = _invoke()
    else:
        result = _invoke()

    if not result.ok and result.skipped_reason is None:
        # Record as broken only for genuine run failures, not not-on-path edge case
        mark_broken(spec.cli)

    return result
