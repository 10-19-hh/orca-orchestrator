"""Low-resolution pixel art drawn with half blocks. No UI framework here.

A sprite is a list of equal-length strings plus a palette. Each character is
one pixel; ``TRANSPARENT`` (``.``) is the only see-through value. Colours are
never treated as transparent, so a black eye or outline (``#000000``) stays
on top of whatever is under it: there is no brightness-threshold background
removal anywhere.

A frame is composed in pixel space, back to front (background, then the
workbench, then the character, then selection marks), into a fresh copy of
the background every time. A character that moved therefore leaves no trace:
the cells it covered before are the background again.

The composed pixels become terminal cells two rows at a time: the upper pixel
is the foreground of ``▀`` and the lower one its background, ``█`` when both
are the same colour, ``▄`` when only the lower one is set and a space when
neither is. An odd pixel height leaves the lower half of the last cell row
empty (``None``), never an invented colour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

TRANSPARENT = "."
UPPER, LOWER, FULL, EMPTY = "▀", "▄", "█", " "

Color = str  # "#rrggbb"
Pixels = list[list[Color | None]]  # [y][x], None = nothing drawn


@dataclass(frozen=True)
class Cell:
    char: str
    fg: Color | None
    bg: Color | None


@dataclass(frozen=True)
class Sprite:
    """Rows of palette keys; ``.`` is transparent. Every other key must be in the palette."""

    rows: tuple[str, ...]
    palette: Mapping[str, Color]

    def __post_init__(self) -> None:
        if not self.rows:
            raise ValueError("sprite has no rows")
        width = len(self.rows[0])
        for i, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(f"sprite row {i} is {len(row)} pixels wide, expected {width}")
            for ch in row:
                if ch != TRANSPARENT and ch not in self.palette:
                    raise ValueError(f"sprite row {i} uses {ch!r}, which is not in the palette")

    @property
    def width(self) -> int:
        return len(self.rows[0])

    @property
    def height(self) -> int:
        return len(self.rows)

    def pixel(self, x: int, y: int) -> Color | None:
        key = self.rows[y][x]
        return None if key == TRANSPARENT else self.palette[key]

    def mirrored(self) -> "Sprite":
        return Sprite(tuple(row[::-1] for row in self.rows), self.palette)

    def recolored(self, palette: Mapping[str, Color]) -> "Sprite":
        """The same frame with some palette keys replaced (a palette swap, not an editor)."""

        return Sprite(self.rows, {**self.palette, **palette})


def sprite(rows: Sequence[str], palette: Mapping[str, Color]) -> Sprite:
    return Sprite(tuple(rows), dict(palette))


def blank(width: int, height: int, color: Color | None = None) -> Pixels:
    return [[color] * width for _ in range(height)]


def copy(pixels: Pixels) -> Pixels:
    return [row[:] for row in pixels]


def stamp(canvas: Pixels, art: Sprite, x: int, y: int) -> None:
    """Draws ``art`` with its top-left at (x, y); transparent pixels and pixels off the canvas are skipped."""

    height = len(canvas)
    width = len(canvas[0]) if height else 0
    for sy, row in enumerate(art.rows):
        cy = y + sy
        if cy < 0 or cy >= height:
            continue
        line = canvas[cy]
        for sx, key in enumerate(row):
            if key == TRANSPARENT:
                continue
            cx = x + sx
            if 0 <= cx < width:
                line[cx] = art.palette[key]


def compose(background: Pixels, layers: Iterable[tuple[Sprite, int, int]]) -> Pixels:
    """A new canvas: the background, then each (sprite, x, y) in order. The background is not changed."""

    canvas = copy(background)
    for art, x, y in layers:
        stamp(canvas, art, x, y)
    return canvas


def cell_for(top: Color | None, bottom: Color | None) -> Cell:
    if top is None and bottom is None:
        return Cell(EMPTY, None, None)
    if top is None:
        return Cell(LOWER, bottom, None)
    if bottom is None:
        return Cell(UPPER, top, None)
    if top == bottom:
        return Cell(FULL, top, top)
    return Cell(UPPER, top, bottom)


def to_cells(pixels: Pixels) -> list[list[Cell]]:
    """Two pixel rows per terminal row. An odd height leaves the last lower half empty."""

    out: list[list[Cell]] = []
    for y in range(0, len(pixels), 2):
        top = pixels[y]
        bottom = pixels[y + 1] if y + 1 < len(pixels) else [None] * len(top)
        out.append([cell_for(t, b) for t, b in zip(top, bottom)])
    return out


def cell_rows(pixel_y: int, pixel_height: int) -> range:
    """Terminal rows covered by pixels ``pixel_y .. pixel_y + pixel_height - 1`` (the same rule as ``to_cells``)."""

    if pixel_height <= 0:
        return range(0)
    first = max(0, pixel_y) // 2
    last = (pixel_y + pixel_height - 1) // 2
    return range(first, last + 1)
