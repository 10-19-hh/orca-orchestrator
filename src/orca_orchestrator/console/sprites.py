"""The yard's pixel art: one neutral character, one workbench and the garden. No UI framework here.

All art here was made for this project as plain string arrays; it does not
copy any existing character. A frame is a list of 16-pixel rows (16 cells by
8 terminal rows once drawn with half blocks). ``.`` is transparent; ``k`` is
the near-black outline and eyes, which stay opaque like every other colour.

To restyle the character, pass a different palette to ``character_frames``
(the keys stay the same); to redraw it, replace the row lists below. There is
no editor.
"""

from __future__ import annotations

import hashlib
from typing import Mapping

from .pixels import Color, Pixels, Sprite, blank, sprite

CHARACTER_PALETTE: dict[str, Color] = {
    "k": "#1a1a24",  # outline, eyes, mouth
    "t": "#4f9fa3",  # hood
    "b": "#3a7c86",  # body
    "f": "#f3d3b0",  # face, hands
    "p": "#e98b8b",  # cheeks
    "l": "#2d3c4f",  # legs
    "w": "#9fdcff",  # sweat drop
}

# The head: rows 0-9. Eyes at x=5 and x=10.
_HEAD = [
    "................",
    ".....kkkkkk.....",
    "...kkttttttkk...",
    "..kttttttttttk..",
    ".kttffffffffttk.",
    ".ktfffffffffftk.",
    ".ktffkffffkfftk.",
    ".ktfpffffffpftk.",
    "..kttffkkffttk..",
    "...kkttttttkk...",
]
_EYES_CLOSED = ".ktfkkffffkkftk."
_MOUTH_WORRIED = "..kttfkffkfttk.."
_SWEAT_ROW3 = "..kttttttttttk.w"

_BODY = [
    "..kbbbbbbbbbbk..",
    ".kbbkbbbbbbkbbk.",
    ".kffkbbbbbbkffk.",
    "..kkbbbbbbbbkk..",
]
_LEGS_STAND = ["...kllk..kllk...", "...kkkk..kkkk..."]
_LEGS_APART = ["..kllk....kllk..", "..kkkk....kkkk.."]
_LEGS_TOGETHER = ["....kllkkllk....", "....kkkkkkkk...."]
_LEGS_STRIDE = [".kllk......kllk.", ".kkk........kkk."]


def _frame(head=None, body=None, legs=None) -> list[str]:
    return list(head or _HEAD) + list(body or _BODY) + list(legs or _LEGS_STAND)


def _head(*, eyes: str | None = None, mouth: str | None = None, row3: str | None = None,
          arms_up: bool = False) -> list[str]:
    rows = list(_HEAD)
    if eyes:
        rows[6] = eyes
    if mouth:
        rows[8] = mouth
    if row3:
        rows[3] = row3
    if arms_up:
        rows[8] = "f" + rows[8][1:-1] + "f"
        rows[9] = "fk" + rows[9][2:-2] + "kf"
    return rows


_BODY_ARMS_OUT = [_BODY[0], ".kbbbbbbbbbbbbk.", "kffkbbbbbbbbkffk", _BODY[3]]
_BODY_TYPE_HIGH = [_BODY[0], ".kbbbbbbbbbbkffk", ".kbbbbbbbbbbk...", _BODY[3]]
_BODY_TYPE_LOW = [_BODY[0], ".kbbbbbbbbbbk...", ".kbbbbbbbbbbkffk", _BODY[3]]
_BODY_NO_ARMS = [_BODY[0], "..kbbbbbbbbbbk..", "..kbbbbbbbbbbk..", _BODY[3]]

FRAME_ROWS: dict[str, list[str]] = {
    "idle": _frame(),
    "blink": _frame(head=_head(eyes=_EYES_CLOSED)),
    "walk1": _frame(legs=_LEGS_APART),
    "walk2": _frame(legs=_LEGS_TOGETHER),
    "run1": _frame(head=_head(row3=_SWEAT_ROW3), body=_BODY_ARMS_OUT, legs=_LEGS_STRIDE),
    "run2": _frame(head=_head(row3=_SWEAT_ROW3), legs=_LEGS_TOGETHER),
    "work1": _frame(body=_BODY_TYPE_HIGH),
    "work2": _frame(body=_BODY_TYPE_LOW),
    "happy": _frame(head=_head(arms_up=True), body=_BODY_NO_ARMS, legs=_LEGS_APART),
    "worried": _frame(head=_head(mouth=_MOUTH_WORRIED)),
}

CHARACTER_SIZE = (16, 16)


def character_frames(palette: Mapping[str, Color] | None = None) -> dict[str, Sprite]:
    colors = {**CHARACTER_PALETTE, **(palette or {})}
    return {name: sprite(rows, colors) for name, rows in FRAME_ROWS.items()}


BENCH_PALETTE: dict[str, Color] = {
    "k": "#1a1a24",
    "m": "#24405e",  # screen
    "g": "#7fe0a0",  # text on the screen
    "s": "#8a8f99",  # stand
    "w": "#b98552",  # bench top
    "W": "#8a5c33",  # bench side and legs
    "o": "#d9a441",  # tool handle
}

_BENCH_TOP = [
    "....kkkkkkkkkkkk....",
    "....kmmmmmmmmmmk....",
]
_BENCH_SCREEN_IDLE = [
    "....kmmmmmmmmmmk....",
    "....kmmmmmmmmmmk....",
    "....kmmmmmmmmmmk....",
]
_BENCH_SCREEN_A = [
    "....kmggmgggmmmk....",
    "....kmgggmmggmmk....",
    "....kmggmmmmmmmk....",
]
_BENCH_SCREEN_B = [
    "....kmgggggmmmmk....",
    "....kmggmmgggmmk....",
    "....kmgmmmmmmmmk....",
]
_BENCH_BOTTOM = [
    "....kkkkkkkkkkkk....",
    "........kssk.....ko.",
    "kkkkkkkkkkkkkkkkkkkk",
    "kwwwwwwwwwwwwwwwwwwk",
    "kWWWWWWWWWWWWWWWWWWk",
    ".kWk............kWk.",
    ".kWk............kWk.",
    ".kkk............kkk.",
]

BENCH_ROWS: dict[str, list[str]] = {
    "off": _BENCH_TOP + _BENCH_SCREEN_IDLE + _BENCH_BOTTOM,
    "on1": _BENCH_TOP + _BENCH_SCREEN_A + _BENCH_BOTTOM,
    "on2": _BENCH_TOP + _BENCH_SCREEN_B + _BENCH_BOTTOM,
}
BENCH_SIZE = (20, 13)


def bench_frames(palette: Mapping[str, Color] | None = None) -> dict[str, Sprite]:
    colors = {**BENCH_PALETTE, **(palette or {})}
    return {name: sprite(rows, colors) for name, rows in BENCH_ROWS.items()}


SELECT_COLOR: Color = "#ffd84a"

GARDEN = {
    "hedge": "#3f7a3a",
    "hedge_dark": "#33652f",
    "grass": "#79b851",
    "grass_dark": "#68a446",
    "floor": "#c7a06c",
    "floor_line": "#a9834f",
    "flower_a": "#f4d55a",
    "flower_b": "#e9789a",
}


def _noise(x: int, y: int, salt: str) -> int:
    """A fixed pattern (no random state): the same yard every time for the same size."""

    return hashlib.blake2b(f"{salt}:{x}:{y}".encode(), digest_size=2).digest()[0]


def garden(width: int, height: int, workshop_x: int | None) -> Pixels:
    """The yard background in pixels: a hedge at the top, grass, and a plank floor from ``workshop_x``."""

    pixels = blank(width, height, GARDEN["grass"])
    hedge = max(2, height // 6)
    for y in range(height):
        for x in range(width):
            if y < hedge:
                pixels[y][x] = GARDEN["hedge_dark"] if _noise(x, y, "h") < 70 else GARDEN["hedge"]
            elif workshop_x is not None and x >= workshop_x:
                pixels[y][x] = GARDEN["floor_line"] if (y - hedge) % 4 == 3 or x == workshop_x else GARDEN["floor"]
            else:
                n = _noise(x, y, "g")
                if n < 24:
                    pixels[y][x] = GARDEN["grass_dark"]
                elif n == 200 and y > hedge + 1:
                    pixels[y][x] = GARDEN["flower_a"]
                elif n == 201 and y > hedge + 1:
                    pixels[y][x] = GARDEN["flower_b"]
    return pixels
