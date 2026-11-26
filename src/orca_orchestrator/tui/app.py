"""Interactive loop. Only touches this terminal; closing it never calls Orca."""

from __future__ import annotations

import os
import shutil
import sys
from contextlib import contextmanager
from typing import Callable, Iterator

from .render import Frame, layout
from .snapshot import Snapshot

ENTER_SCREEN = "\x1b[?1049h\x1b[?25l"  # alternate screen, hide cursor
LEAVE_SCREEN = "\x1b[?25h\x1b[?1049l"  # show cursor, main screen
CLEAR = "\x1b[H\x1b[2J"

REFRESH_KEYS = frozenset({"r", "R", "ㄱ"})  # ㄱ: r with a Korean IME active
QUIT_KEYS = frozenset({"q", "Q", "ㅂ", "\x03", "\x04", ""})  # ㅂ: q with Korean IME; Ctrl+C/D; EOF

# Named keys produced by the key readers below.
UP, DOWN, PAGE_UP, PAGE_DOWN, HOME, END = "UP", "DOWN", "PAGE_UP", "PAGE_DOWN", "HOME", "END"
UNKNOWN_KEY = "\x00"
SCROLL_KEYS: dict[str, str] = {
    UP: UP, "k": UP, "ㅏ": UP,  # ㅏ/ㅓ: k/j with a Korean IME active
    DOWN: DOWN, "j": DOWN, "ㅓ": DOWN,
    PAGE_UP: PAGE_UP, "b": PAGE_UP, "ㅠ": PAGE_UP,
    PAGE_DOWN: PAGE_DOWN, " ": PAGE_DOWN,
    HOME: HOME, "g": HOME, "ㅎ": HOME,
    END: END, "G": END,
}

# Windows: msvcrt.getwch() returns "\x00" or "\xe0", then one of these codes.
WINDOWS_SPECIAL = {"H": UP, "P": DOWN, "I": PAGE_UP, "Q": PAGE_DOWN, "G": HOME, "O": END}
# POSIX: what follows ESC.
ESCAPE_SEQUENCES = {
    "[A": UP, "OA": UP, "[B": DOWN, "OB": DOWN,
    "[5~": PAGE_UP, "[6~": PAGE_DOWN,
    "[H": HOME, "OH": HOME, "[1~": HOME, "[7~": HOME,
    "[F": END, "OF": END, "[4~": END, "[8~": END,
}

Size = Callable[[], "tuple[int, int]"]  # (columns, lines)


def _scroll(key: str, offset: int, frame: Frame) -> int:
    page = max(1, frame.body_rows - 1)
    step = {UP: -1, DOWN: 1, PAGE_UP: -page, PAGE_DOWN: page}.get(key)
    if step is not None:
        return offset + step  # layout() clamps it
    return 0 if key == HOME else frame.max_offset


def run_loop(
    collect: Callable[[], Snapshot],
    read_key: Callable[[], str],
    write: Callable[[str], None],
    size: Size = lambda: (80, 24),
) -> int:
    """Draws, then reacts to keys. Only refresh calls ``collect``; scrolling reuses the snapshot."""

    snap = collect()
    offset = 0

    def draw() -> Frame:
        nonlocal offset
        columns, lines = size()
        # One spare column so a full-width line never wraps, and no newline after
        # the last line so a full-height frame never scrolls the title away.
        frame = layout(snap, columns - 1, lines, offset)
        offset = frame.offset
        write(CLEAR + "\r\n".join(frame.lines))
        return frame

    frame = draw()
    while True:
        key = read_key()
        if key in QUIT_KEYS:
            return 0
        if key in REFRESH_KEYS:
            snap = collect()
            frame = draw()
        elif key in SCROLL_KEYS:
            offset = _scroll(SCROLL_KEYS[key], offset, frame)
            frame = draw()


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


def decode_windows(first: str, read_next: Callable[[], str]) -> str:
    if first in ("\x00", "\xe0"):
        return WINDOWS_SPECIAL.get(read_next(), UNKNOWN_KEY)
    return first


def decode_escape(sequence: str) -> str:
    """``sequence`` is what followed ESC; empty for a lone ESC."""

    return ESCAPE_SEQUENCES.get(sequence, UNKNOWN_KEY)


@contextmanager
def _key_reader() -> Iterator[Callable[[], str]]:
    if os.name == "nt":
        import msvcrt

        yield lambda: decode_windows(msvcrt.getwch(), msvcrt.getwch)
        return

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)

    def read() -> str:
        ch = sys.stdin.read(1)
        if ch != "\x1b":
            return ch
        sequence = ""
        while len(sequence) < 4 and select.select([fd], [], [], 0.05)[0]:
            sequence += sys.stdin.read(1)
            if sequence[-1].isalpha() or sequence[-1] == "~":
                break
        return decode_escape(sequence)

    try:
        tty.setcbreak(fd)
        yield read
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size((80, 24))
    return size.columns, size.lines


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
                return run_loop(collect, read_key, write, _terminal_size)
            except KeyboardInterrupt:
                return 0
    finally:
        write(LEAVE_SCREEN)
