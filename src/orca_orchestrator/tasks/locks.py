"""OS file locks shared by every process using the same results file.

The operating system releases a lock when its holder exits for any reason, so
a lock that can be taken means nobody holds it any more. Lock files are left
in place.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

POLL_SECONDS = 0.05


class LockTimeout(Exception):
    pass


if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def exclusive(lock_file: Path, timeout: float) -> Iterator[None]:
    """Waits up to ``timeout`` seconds for the lock, holds it for the block."""

    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise LockTimeout
            time.sleep(POLL_SECONDS)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


class HeldLock:
    """A lock kept for as long as a piece of work runs; released by release() or process exit."""

    def __init__(self, fd: int) -> None:
        self._fd: int | None = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            _unlock(self._fd)
        finally:
            os.close(self._fd)
            self._fd = None


def hold(lock_file: Path, wait: float = 0.5) -> HeldLock | None:
    """Takes the lock, retrying briefly; None when another holder keeps it."""

    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + wait
    while not _try_lock(fd):
        if time.monotonic() >= deadline:
            os.close(fd)
            return None
        time.sleep(POLL_SECONDS)
    return HeldLock(fd)


def is_held(lock_file: Path) -> bool:
    """Whether a live process holds the lock. Never creates the file."""

    try:
        fd = os.open(lock_file, os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        if _try_lock(fd):
            _unlock(fd)
            return False
        return True
    finally:
        os.close(fd)
