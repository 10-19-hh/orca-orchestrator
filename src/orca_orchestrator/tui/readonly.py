"""Runner wrapper that only lets the status screen issue read commands."""

from __future__ import annotations

from typing import Sequence

from ..orca.runner import CommandResult, CommandRunner

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


class ReadOnlyRunner:
    def __init__(self, inner: CommandRunner) -> None:
        self._inner = inner

    def run(
        self, argv: Sequence[str], *, timeout: float, cwd: str | None = None
    ) -> CommandResult:
        tail = tuple(argv[1:])
        if tail not in ALLOWED_TAILS:
            raise ReadOnlyViolation(f"read-only screen refused: {' '.join(tail)}")
        return self._inner.run(argv, timeout=timeout, cwd=cwd)
