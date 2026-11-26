"""Command execution seam for the Orca CLI.

The real runner passes an argv list to ``subprocess.run`` with ``shell=False``
(the form verified in Step 0 Experiment 1). Tests substitute a fake runner.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


class CommandTimeout(Exception):
    """The process did not finish in time; its effect is unknown."""


class CommandLaunchError(Exception):
    """The executable could not be started."""


class CommandRunner(Protocol):
    def run(
        self, argv: Sequence[str], *, timeout: float, cwd: str | None = None
    ) -> CommandResult: ...


class SubprocessRunner:
    """Runs a command without a shell and inherits the caller's environment.

    The environment is inherited unchanged so that a Coordinator process keeps
    the terminal binding Orca assigned to it. Nothing is added or removed.
    Callers that need a different environment (local verification) pass ``env``.
    """

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = dict(env) if env is not None else None

    def run(
        self, argv: Sequence[str], *, timeout: float, cwd: str | None = None
    ) -> CommandResult:
        args = [str(a) for a in argv]
        try:
            completed = subprocess.run(
                args,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                env=self._env,
            )
        except subprocess.TimeoutExpired:
            raise CommandTimeout(f"timed out after {timeout}s") from None
        except OSError as exc:
            raise CommandLaunchError(f"{type(exc).__name__}: {exc.strerror or exc}") from None
        return CommandResult(
            argv=tuple(args),
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


class TrackedSubprocessRunner(SubprocessRunner):
    """Like SubprocessRunner, but reports the child's process ID as soon as it starts.

    Used for local verification so an execution record can name the process it
    started. On timeout the child is killed; whether processes it started
    themselves also ended is not known, so the caller treats the end as unknown.
    """

    reports_start = True

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: str | None = None,
        on_start: Callable[[int], None] | None = None,
    ) -> CommandResult:
        args = [str(a) for a in argv]
        try:
            proc = subprocess.Popen(
                args,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
                env=self._env,
            )
        except OSError as exc:
            raise CommandLaunchError(f"{type(exc).__name__}: {exc.strerror or exc}") from None
        try:
            if on_start is not None:
                on_start(proc.pid)
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            raise CommandTimeout(f"timed out after {timeout}s") from None
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        return CommandResult(tuple(args), proc.returncode, stdout or "", stderr or "")
