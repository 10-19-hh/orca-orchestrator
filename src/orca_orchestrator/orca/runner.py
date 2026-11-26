"""Command execution seam for the Orca CLI.

The real runner passes an argv list to ``subprocess.run`` with ``shell=False``
(the form verified in Step 0 Experiment 1). Tests substitute a fake runner.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol, Sequence


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
    """

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
