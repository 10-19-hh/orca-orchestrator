"""Interactive loop. Only touches this terminal; closing it never calls Orca."""

from __future__ import annotations

import os
import shutil
import sys
from contextlib import contextmanager
from typing import Callable, Iterator

from .render import render
from .snapshot import Snapshot

ENTER_SCREEN = "\x1b[?1049h\x1b[?25l"  # alternate screen, hide cursor
LEAVE_SCREEN = "\x1b[?25h\x1b[?1049l"  # show cursor, main screen
CLEAR = "\x1b[H\x1b[2J"

REFRESH_KEYS = frozenset({"r", "R", "ㄱ"})  # ㄱ: r with a Korean IME active
QUIT_KEYS = frozenset({"q", "Q", "ㅂ", "\x03", "\x04", ""})  # ㅂ: q with Korean IME; Ctrl+C/D; EOF


def run_loop(
    collect: Callable[[], Snapshot],
    read_key: Callable[[], str],
    write: Callable[[str], None],
    width: Callable[[], int] = lambda: 80,
) -> int:
    def draw() -> None:
        write(CLEAR + "\r\n".join(render(collect(), width())) + "\r\n")

    draw()
    while True:
        key = read_key()
        if key in QUIT_KEYS:
            return 0
        if key in REFRESH_KEYS:
            draw()


def _enable_windows_vt() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


@contextmanager
def _key_reader() -> Iterator[Callable[[], str]]:
    if os.name == "nt":
        import msvcrt

        def read() -> str:
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):  # arrow/function key prefix: drop the second code
                msvcrt.getwch()
                return "\x00"
            return ch

        yield read
        return

    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield lambda: sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def run_interactive(collect: Callable[[], Snapshot]) -> int:
    _enable_windows_vt()
    out = sys.stdout

    def write(text: str) -> None:
        out.write(text)
        out.flush()

    write(ENTER_SCREEN)
    try:
        with _key_reader() as read_key:
            try:
                return run_loop(
                    collect, read_key, write, lambda: shutil.get_terminal_size((80, 24)).columns
                )
            except KeyboardInterrupt:
                return 0
    finally:
        write(LEAVE_SCREEN)
