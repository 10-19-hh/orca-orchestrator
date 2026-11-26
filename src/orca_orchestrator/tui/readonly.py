"""Runner wrapper that only lets the status screen (and other read-only views) issue read commands."""

from __future__ import annotations

from typing import Sequence

from ..orca.runner import CommandResult, CommandRunner
from ..orca.workers import DISPATCH_ID_PATTERN

# Command tails (after the CLI path) the status screen may run.
ALLOWED_TAILS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("status", "--json"),
        ("orchestration", "run-current", "--json"),
    }
)


class ReadOnlyViolation(PermissionError):
    """Raised instead of running a command outside the read-only allowlist.

    The screen only asks for allowed commands, so this signals a programming error
    and is deliberately not turned into a display state.
    """


def _is_worker_show(tail: tuple[str, ...]) -> bool:
    return (len(tail) == 5 and tail[:3] == ("orchestration", "worker-show", "--dispatch")
            and DISPATCH_ID_PATTERN.match(tail[3]) is not None and tail[4] == "--json")


class ReadOnlyRunner:
    """``allow_worker_show`` also lets ``worker-show --dispatch <id> --json`` through (handoff --observe)."""

    def __init__(self, inner: CommandRunner, *, allow_worker_show: bool = False) -> None:
        self._inner = inner
        self._allow_worker_show = allow_worker_show

    def run(
        self, argv: Sequence[str], *, timeout: float, cwd: str | None = None
    ) -> CommandResult:
        tail = tuple(argv[1:])
        if tail not in ALLOWED_TAILS and not (self._allow_worker_show and _is_worker_show(tail)):
            raise ReadOnlyViolation(f"read-only screen refused: {' '.join(tail)}")
        return self._inner.run(argv, timeout=timeout, cwd=cwd)
