"""A tiny pixel canvas for design concepts (Python standard library only).

This is a design-review tool, not the GUI asset pipeline: it renders concept
images so directions can be compared at true pixel size. Final art is drawn
in a pixel editor and exported as PNG sheets (see the specification).
"""

from __future__ import annotations

import math
import struct
import zlib

RGB = tuple[int, int, int]


def hexc(value: str) -> RGB:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))  # type: ignore[return-value]


def shade(c: RGB, f: float) -> RGB:
    """f < 0 darkens toward a cool dark, f > 0 lightens toward a warm light (hue-shifted ramps)."""

    if f < 0:
        return mix(c, (18, 16, 40), -f)
    return mix(c, (255, 244, 220), f)


class Canvas:
    def __init__(self, w: int, h: int, fill: RGB | None = None) -> None:
        self.w, self.h = w, h
        self.px: list[list[RGB | None]] = [[fill] * w for _ in range(h)]

    # -- basic ---------------------------------------------------------------------------
    def inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def set(self, x: int, y: int, c: RGB | None) -> None:
        if self.inside(x, y):
            self.px[y][x] = c

    def get(self, x: int, y: int) -> RGB | None:
        return self.px[y][x] if self.inside(x, y) else None

    def rect(self, x: int, y: int, w: int, h: int, c: RGB) -> None:
        for yy in range(max(0, y), min(self.h, y + h)):
            row = self.px[yy]
            for xx in range(max(0, x), min(self.w, x + w)):
                row[xx] = c

    def hline(self, x: int, y: int, w: int, c: RGB) -> None:
        self.rect(x, y, w, 1, c)

    def vline(self, x: int, y: int, h: int, c: RGB) -> None:
        self.rect(x, y, 1, h, c)

    def ellipse(self, cx: float, cy: float, rx: float, ry: float, c: RGB) -> None:
        for y in range(int(cy - ry) - 1, int(cy + ry) + 2):
            for x in range(int(cx - rx) - 1, int(cx + rx) + 2):
                if ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2 <= 1.0:
                    self.set(x, y, c)

    def tint(self, x: int, y: int, toward: RGB, t: float) -> None:
        c = self.get(x, y)
        if c is not None:
            self.px[y][x] = mix(c, toward, t)

    def tint_rect(self, x: int, y: int, w: int, h: int, toward: RGB, t: float) -> None:
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.tint(xx, yy, toward, t)

    def tint_ellipse(self, cx, cy, rx, ry, toward: RGB, t: float) -> None:
        for y in range(int(cy - ry) - 1, int(cy + ry) + 2):
            for x in range(int(cx - rx) - 1, int(cx + rx) + 2):
                if ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2 <= 1.0:
                    self.tint(x, y, toward, t)

    def glow(self, cx, cy, rx, ry, color: RGB, strength: float, bands: int = 3) -> None:
        """Banded light pool: a few flat steps, never a smooth gradient (keeps the pixel look)."""

        for i in range(bands, 0, -1):
            f = i / bands
            self.tint_ellipse(cx, cy, rx * f, ry * f, color, strength / bands)

    def blit(self, src: "Canvas", x: int, y: int, flip: bool = False) -> None:
        for sy in range(src.h):
            for sx in range(src.w):
                c = src.px[sy][src.w - 1 - sx if flip else sx]
                if c is not None:
                    self.set(x + sx, y + sy, c)

    def copy(self) -> "Canvas":
        out = Canvas(self.w, self.h)
        out.px = [row[:] for row in self.px]
        return out

    # -- output ---------------------------------------------------------------------------
    def save(self, path, scale: int = 1, background: RGB | None = None) -> None:
        raw = bytearray()
        for y in range(self.h):
            line = bytearray()
            for x in range(self.w):
                c = self.px[y][x]
                if c is None:
                    line += bytes((*background, 255)) if background else b"\x00\x00\x00\x00"
                else:
                    line += bytes((*c, 255))
            # repeat each pixel `scale` times horizontally, then the row `scale` times
            wide = bytearray()
            for i in range(0, len(line), 4):
                wide += line[i:i + 4] * scale
            for _ in range(scale):
                raw += b"\x00" + wide
        w, h = self.w * scale, self.h * scale

        def chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
        with open(path, "wb") as f:
            f.write(png)


# --- masks: shapes for characters, shaded consistently ------------------------------------

Mask = set[tuple[int, int]]


def disc(cx: float, cy: float, r: float) -> Mask:
    out: Mask = set()
    for y in range(int(cy - r) - 1, int(cy + r) + 2):
        for x in range(int(cx - r) - 1, int(cx + r) + 2):
            if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r * r:
                out.add((x, y))
    return out


def oval(cx: float, cy: float, rx: float, ry: float) -> Mask:
    out: Mask = set()
    for y in range(int(cy - ry) - 1, int(cy + ry) + 2):
        for x in range(int(cx - rx) - 1, int(cx + rx) + 2):
            if ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2 <= 1.0:
                out.add((x, y))
    return out


def box(x: int, y: int, w: int, h: int, r: int = 0) -> Mask:
    out: Mask = set()
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            dx = max(x + r - xx, 0, xx - (x + w - 1 - r))
            dy = max(y + r - yy, 0, yy - (y + h - 1 - r))
            if r == 0 or dx * dx + dy * dy <= r * r + 0.5:
                out.add((xx, yy))
    return out


def outline_of(mask: Mask, diagonal: bool = False) -> Mask:
    steps = [(1, 0), (-1, 0), (0, 1), (0, -1)] + ([(1, 1), (-1, -1), (1, -1), (-1, 1)] if diagonal else [])
    return {(x + dx, y + dy) for x, y in mask for dx, dy in steps} - mask


def paint_form(cv: Canvas, mask: Mask, base: RGB, *, light: float = 0.28, dark: float = 0.30,
               rim: RGB | None = None, ox: int = 0, oy: int = 0, ramp: float = 0.0) -> None:
    """Fill a form lit from the top-left: highlight edge, body, shadow edge, then a coloured rim.

    The rim is a darker hue of the form itself (selective outline), never pure black.
    """

    if not mask:
        return
    ys = [y for _, y in mask]
    top, bottom = min(ys), max(ys)
    for (x, y) in mask:
        c = base
        if ramp and bottom > top:
            c = shade(c, -ramp * max(0.0, (y - top) / (bottom - top) - 0.55) * 2)
        up_left = (x - 1, y) not in mask or (x, y - 1) not in mask
        down_right = (x + 1, y) not in mask or (x, y + 1) not in mask
        down_right2 = (x + 2, y) not in mask or (x, y + 2) not in mask
        if up_left and not down_right:
            c = shade(base, light)
        elif down_right:
            c = shade(base, -dark)
        elif down_right2:
            c = shade(base, -dark * 0.5)
        cv.set(x + ox, y + oy, c)
    if rim is not None:
        for (x, y) in outline_of(mask):
            if cv.get(x + ox, y + oy) is None or True:
                cv.set(x + ox, y + oy, rim)


# --- a 3x5 pixel font for in-world labels only ----------------------------------------------

FONT = {
    "A": "010101111101101", "B": "110101110101110", "C": "011100100100011", "D": "110101101101110",
    "E": "111100110100111", "F": "111100110100100", "G": "011100101101011", "H": "101101111101101",
    "I": "111010010010111", "K": "101101110101101", "L": "100100100100111", "M": "101111111101101",
    "N": "110101101101101", "O": "010101101101010", "P": "110101110100100", "R": "110101110101101",
    "S": "011100010001110", "T": "111010010010010", "U": "101101101101111", "V": "101101101101010",
    "W": "101101111111101", "X": "101101010101101", "Y": "101101010010010", "Q": "010101101110011",
    "0": "111101101101111", "1": "010110010010111", "2": "110001010100111", "3": "110001010001110",
    "4": "101101111001001", "5": "111100110001110", "6": "011100111101111", "7": "111001010010010",
    "8": "111101111101111", "9": "111101111001110", "-": "000000111000000", "?": "110001010000010",
    "!": "010010010000010", ">": "100010001010100", "_": "000000000000111", ".": "000000000000010",
    ":": "000010000010000", "/": "001001010100100", " ": "000000000000000", "+": "000010111010000",
}


def text(cv: Canvas, x: int, y: int, s: str, c: RGB) -> int:
    for ch in s.upper():
        g = FONT.get(ch, FONT["?"])
        for i, bit in enumerate(g):
            if bit == "1":
                cv.set(x + i % 3, y + i // 3, c)
        x += 4
    return x


def text_width(s: str) -> int:
    return len(s) * 4 - 1


def checker_icon(cv: Canvas, x: int, y: int, kind: str, c: RGB) -> None:
    """5x5 state glyphs so states never depend on colour alone."""

    glyphs = {
        "ok": "0000000001000101010001000",
        "fail": "1000101010001000101010001",
        "unknown": "0111000001000100000000100",
        "attention": "0010000100001000000000100",
        "wait": "0111010101101110000101110",
        "dot": "0000001110011100111000000",
    }
    g = glyphs[kind].ljust(25, "0")
    for i, bit in enumerate(g):
        if bit == "1":
            cv.set(x + i % 5, y + i // 5, c)


def lerp_rows(cv: Canvas, x: int, y: int, w: int, h: int, top: RGB, bottom: RGB, steps: int = 4) -> None:
    """Vertical bands (flat steps) instead of a smooth gradient."""

    for i in range(h):
        t = math.floor(i / h * steps) / max(1, steps - 1)
        cv.hline(x, y + i, w, mix(top, bottom, min(1.0, t)))
