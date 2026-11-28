"""2:1 isometric drawing for the layout study: floor, walls, light, furniture modules and small mascots.

Design-review tool (stdlib only). It shows proportions, zoning, light direction
and pet placement. It is not the production art pipeline: final sprites are
drawn by an artist and replace every shape here (ASSET_MANIFEST.md).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from pixel import FONT, Canvas, hexc, mix, shade  # noqa: E402

TW, TH = 32, 16  # px per floor tile
WALL_H = 60      # px
WALL_T = 0.25    # tiles

P = {k: hexc(v) for k, v in {
    "canvas": "#223344", "wall": "#D2C2AF", "wall_shadow": "#A99C90", "wall_cap": "#E6DCCD",
    "floor": "#C5A17A", "floor_shadow": "#967456", "seam": "#B08B63", "slab": "#5C4334", "slab_dark": "#47342A",
    "walnut": "#795743", "walnut_dark": "#5E4334", "oak": "#C79B6B", "oak_light": "#D6AE80",
    "gray": "#B4B5B0", "gray_dark": "#8E8F8A", "monitor": "#303A43", "screen": "#9FC6CF", "screen_dim": "#3B4752",
    "plant": "#78896B", "plant_dark": "#5D6B53", "plant_light": "#93A383", "pot": "#A6765A",
    "sun": "#EAD5A3", "glass": "#B7CBD3", "glass_dark": "#93AEBB", "frame": "#EEE6D9",
    "fabric": "#8E7F72", "fabric_light": "#A39484", "rug": "#A8917A", "rug_edge": "#8C7660",
    "board": "#EFEBE3", "board_frame": "#9C9B95", "ink_blue": "#6E7F95", "ink_warm": "#B07A5A", "note": "#E3D18E",
    "rack": "#3E464E", "rack_light": "#56606A", "led": "#8BC28C", "led_warn": "#E2B65B",
    "stone": "#DCD3C6", "metal": "#4A4F55", "chrome": "#A3A8AD", "white": "#F2EEE7", "lamp": "#F1DDA8",
    "chair": "#4B5058", "chair_light": "#5D636B", "chair_dark": "#383C42", "door": "#6E4E3B",
    "ink": "#2A2522", "bubble": "#F3EEE6", "warn": "#C98A2E", "bad": "#B5544A", "calm": "#6E8296",
    "book1": "#8C5A47", "book2": "#6F7F8C", "book3": "#A08560", "book4": "#7D8466",
}.items()}

CLAUDE = {k: hexc(v) for k, v in {"body": "#D7774E", "light": "#E8946A", "shade": "#B45E3B", "rim": "#6E3220",
                                  "eye": "#2A1712"}.items()}
CODEX = {k: hexc(v) for k, v in {"cloud": "#A9D2F7", "cloud_shade": "#86B0DC", "rim": "#35548F", "screen": "#1E2A44",
                                 "glyph": "#8FE3F0", "body": "#DFE7F1", "body_shade": "#B7C3D3",
                                 "joint": "#8FA3C4"}.items()}

GLYPHS = dict(FONT, Z="111001010100111", J="001001001101010", **{",": "000000000010100", "(": "010100100100010",
                                                                 ")": "010001001001010", "=": "000111000111000"})


class Iso:
    def __init__(self, cv: Canvas, ox: int, oy: int) -> None:
        self.cv, self.ox, self.oy = cv, ox, oy

    def pt(self, x: float, y: float, z: float = 0.0) -> tuple[float, float]:
        return self.ox + (x - y) * TW / 2, self.oy + (x + y) * TH / 2 - z

    # --- raster -------------------------------------------------------------------------
    def _spans(self, pts):
        ys = [p[1] for p in pts]
        for y in range(int(math.floor(min(ys))), int(math.ceil(max(ys))) + 1):
            yc = y + 0.5
            xs = []
            for i in range(len(pts)):
                (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % len(pts)]
                if (y0 <= yc < y1) or (y1 <= yc < y0):
                    xs.append(x0 + (yc - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for j in range(0, len(xs) - 1, 2):
                yield y, math.ceil(xs[j] - 0.5), math.floor(xs[j + 1] - 0.5)

    def poly(self, pts, color) -> None:
        for y, a, b in self._spans(pts):
            for x in range(a, b + 1):
                self.cv.set(x, y, color)

    def tint(self, pts, toward, t: float) -> None:
        for y, a, b in self._spans(pts):
            for x in range(a, b + 1):
                self.cv.tint(x, y, toward, t)

    def line(self, p0, p1, color) -> None:
        (x0, y0), (x1, y1) = p0, p1
        n = int(max(abs(x1 - x0), abs(y1 - y0)))
        for i in range(n + 1):
            t = i / n if n else 0
            self.cv.set(int(math.floor(x0 + (x1 - x0) * t)), int(math.floor(y0 + (y1 - y0) * t)), color)

    # --- shapes -------------------------------------------------------------------------
    def quad(self, x, y, w, d, z=0.0):
        return [self.pt(x, y, z), self.pt(x + w, y, z), self.pt(x + w, y + d, z), self.pt(x, y + d, z)]

    def prism(self, x, y, w, d, z, h, top, left=None, right=None, edge=True) -> None:
        """Box on the floor grid: top, the +y face (lower left) and the +x face (lower right)."""

        left = left or shade(top, -0.16)
        right = right or shade(top, -0.3)
        self.poly([self.pt(x, y + d, z), self.pt(x + w, y + d, z), self.pt(x + w, y + d, z + h),
                   self.pt(x, y + d, z + h)], left)
        self.poly([self.pt(x + w, y, z), self.pt(x + w, y + d, z), self.pt(x + w, y + d, z + h),
                   self.pt(x + w, y, z + h)], right)
        self.poly(self.quad(x, y, w, d, z + h), top)
        if edge and h > 0:
            hi = shade(top, 0.18)
            self.line(self.pt(x, y + d, z + h), self.pt(x + w, y + d, z + h), hi)
            self.line(self.pt(x + w, y + d, z + h), self.pt(x + w, y, z + h), hi)

    def face_y(self, x0, x1, y, z0, z1, color) -> None:
        """A rectangle on a plane y = const (back wall, or a +y face)."""

        self.poly([self.pt(x0, y, z0), self.pt(x1, y, z0), self.pt(x1, y, z1), self.pt(x0, y, z1)], color)

    def face_x(self, y0, y1, x, z0, z1, color) -> None:
        self.poly([self.pt(x, y0, z0), self.pt(x, y1, z0), self.pt(x, y1, z1), self.pt(x, y0, z1)], color)

    def shadow(self, x, y, w, d, t=0.22) -> None:
        self.tint(self.quad(x + 0.12, y + 0.1, w, d), P["floor_shadow"], t)


def text(cv: Canvas, x: int, y: int, s: str, c) -> int:
    for ch in s.upper():
        g = GLYPHS.get(ch, GLYPHS["?"])
        for i, bit in enumerate(g):
            if bit == "1":
                cv.set(x + i % 3, y + i // 3, c)
        x += 4
    return x


def text_w(s: str) -> int:
    return len(s) * 4 - 1


# --- room shell ---------------------------------------------------------------------------


def floor(iso: Iso, w: int, d: int) -> None:
    iso.prism(0, 0, w, d, -8, 8, P["floor"], P["slab"], P["slab_dark"], edge=False)
    rows = d * 4
    for j in range(rows):
        y = j / 4
        offset = (j * 1.375) % 3.0
        x = -offset
        k = 0
        while x < w:
            x0, x1 = max(0.0, x), min(float(w), x + 3.0)
            tone = (0.04, -0.03, 0.0, 0.06, -0.05)[(j * 7 + k * 3) % 5]
            iso.poly(iso.quad(x0, y, x1 - x0, 0.25), shade(P["floor"], tone))
            if x1 < w:
                iso.line(iso.pt(x1, y), iso.pt(x1, y + 0.25), P["seam"])
            x += 3.0
            k += 1
        iso.line(iso.pt(0, y), iso.pt(w, y), P["seam"])
    # ambient occlusion along the walls
    for i, t in enumerate((0.30, 0.18, 0.08)):
        iso.tint(iso.quad(0, i * 0.125, w, 0.125), P["floor_shadow"], t)
        iso.tint(iso.quad(i * 0.125, 0, 0.125, d), P["floor_shadow"], t)


def walls(iso: Iso, w: int, d: int) -> None:
    left = mix(P["wall"], P["wall_shadow"], 0.38)
    iso.prism(-WALL_T, -WALL_T, w + WALL_T, WALL_T, -8, WALL_H + 8, P["wall_cap"], P["wall"], P["wall_shadow"],
              edge=False)
    iso.prism(-WALL_T, 0, WALL_T, d, -8, WALL_H + 8, P["wall_cap"], P["wall_shadow"], left, edge=False)
    # soft vertical light falloff toward the floor, baseboards
    for i, t in enumerate((0.10, 0.06, 0.03)):
        iso.tint([iso.pt(0, 0, i * 6), iso.pt(w, 0, i * 6), iso.pt(w, 0, i * 6 + 6), iso.pt(0, 0, i * 6 + 6)],
                 P["wall_shadow"], t)
    iso.face_y(0, w, 0, 0, 3, P["walnut"])
    iso.face_x(0, d, 0, 0, 3, P["walnut_dark"])
    iso.line(iso.pt(0, 0, 0), iso.pt(0, 0, WALL_H), shade(P["wall_shadow"], -0.1))


def window_back(iso: Iso, x0: float, x1: float, z0=20, z1=46) -> None:
    iso.face_y(x0 - 0.125, x1 + 0.125, 0, z0 - 2, z1 + 2, P["frame"])
    iso.face_y(x0, x1, 0, z0, z1, P["glass"])
    iso.face_y(x0, x1, 0, z0, z0 + 8, P["glass_dark"])
    mid = (x0 + x1) / 2
    iso.face_y(mid - 0.0625, mid + 0.0625, 0, z0, z1, P["frame"])
    iso.face_y(x0, x1, 0, (z0 + z1) / 2 - 1, (z0 + z1) / 2 + 1, P["frame"])
    iso.prism(x0 - 0.125, 0, x1 - x0 + 0.25, 0.1875, z0 - 3, 2, P["frame"])


def window_left(iso: Iso, y0: float, y1: float, z0=20, z1=46) -> None:
    iso.face_x(y0 - 0.125, y1 + 0.125, 0, z0 - 2, z1 + 2, shade(P["frame"], -0.08))
    iso.face_x(y0, y1, 0, z0, z1, shade(P["glass"], -0.06))
    iso.face_x(y0, y1, 0, z0, z0 + 8, shade(P["glass_dark"], -0.06))
    mid = (y0 + y1) / 2
    iso.face_x(mid - 0.0625, mid + 0.0625, 0, z0, z1, shade(P["frame"], -0.08))
    iso.face_x(y0, y1, 0, (z0 + z1) / 2 - 1, (z0 + z1) / 2 + 1, shade(P["frame"], -0.08))
    iso.prism(0, y0 - 0.125, 0.1875, y1 - y0 + 0.25, z0 - 3, 2, P["frame"])


def sunlight_from_left(iso: Iso, y0: float, y1: float) -> None:
    patch = [iso.pt(0.5, y0 + 0.5), iso.pt(0.5, y1 + 0.5), iso.pt(3.25, y1 + 1.4), iso.pt(3.25, y0 + 1.4)]
    iso.tint(patch, P["sun"], 0.34)
    inner = [iso.pt(0.75, y0 + 0.7), iso.pt(0.75, y1 + 0.3), iso.pt(2.75, y1 + 1.0), iso.pt(2.75, y0 + 1.4)]
    iso.tint(inner, P["sun"], 0.18)


def sunlight_from_back(iso: Iso, x0: float, x1: float) -> None:
    patch = [iso.pt(x0 + 0.5, 0.5), iso.pt(x1 + 0.5, 0.5), iso.pt(x1 + 1.4, 3.25), iso.pt(x0 + 1.4, 3.25)]
    iso.tint(patch, P["sun"], 0.30)


def air_shaft_back(iso: Iso, x0: float, x1: float) -> None:
    iso.tint([iso.pt(x0, 0, 20), iso.pt(x1, 0, 20), iso.pt(x1 + 1.4, 3.25, 0), iso.pt(x0 + 1.4, 3.25, 0)],
             P["sun"], 0.07)


def door_left(iso: Iso, y0: float, y1: float) -> None:
    iso.face_x(y0 - 0.125, y1 + 0.125, 0, 0, 40, P["walnut_dark"])
    iso.face_x(y0, y1, 0, 0, 37, P["door"])
    iso.face_x(y0 + 0.125, y1 - 0.125, 0, 22, 33, shade(P["door"], 0.08))
    iso.face_x(y0 + 0.125, y1 - 0.125, 0, 6, 18, shade(P["door"], 0.08))
    kx, ky = iso.pt(0, y1 - 0.25, 18)
    iso.cv.rect(int(kx), int(ky), 2, 2, hexc("#D9C07A"))


def frames_left(iso: Iso, y0: float) -> None:
    for dy, z, h, c in ((0.0, 28, 9, P["sun"]), (0.625, 30, 7, P["plant_light"])):
        iso.face_x(y0 + dy, y0 + dy + 0.5, 0, z, z + h, P["walnut"])
        iso.face_x(y0 + dy + 0.0625, y0 + dy + 0.4375, 0, z + 1, z + h - 1, c)


def whiteboard(iso: Iso, x0: float, x1: float) -> None:
    iso.face_y(x0, x1, 0, 18, 42, P["board_frame"])
    iso.face_y(x0 + 0.0625, x1 - 0.0625, 0, 19, 41, P["board"])
    for i, (a, b, z, c) in enumerate(((0.25, 1.1, 36, P["ink_blue"]), (0.25, 0.8, 33, P["ink_blue"]),
                                      (1.3, 2.1, 35, P["ink_warm"]), (0.4, 1.6, 27, P["ink_blue"]))):
        iso.line(iso.pt(x0 + a, 0, z), iso.pt(x0 + b, 0, z), c)
    for k, c in enumerate((P["note"], hexc("#A9BCD0"), P["note"])):
        iso.face_y(x0 + 1.55 + k * 0.3, x0 + 1.8 + k * 0.3, 0, 22, 27, c)
    iso.prism(x0 + 0.25, 0, x1 - x0 - 0.5, 0.1875, 17, 1, P["board_frame"], edge=False)


def status_board(iso: Iso, x0: float, x1: float, attention: bool = False) -> None:
    iso.face_y(x0, x1, 0, 38, 52, P["rack"])
    iso.face_y(x0 + 0.0625, x1 - 0.0625, 0, 39, 51, P["screen_dim"])
    for i in range(4):
        c = P["led_warn"] if attention and i == 3 else P["led"]
        px, py = iso.pt(x0 + 0.2 + i * 0.25, 0, 46)
        iso.cv.rect(int(px), int(py), 2, 2, c)
    px, py = iso.pt(x0 + 0.25, 0, 43)
    for i, (dx, dy) in enumerate(((0, 1), (1, 2), (2, 1), (3, 0), (4, -1))):
        iso.cv.set(int(px) + dx, int(py) + dy, P["led"])


def wall_shelf_back(iso: Iso, x0: float, x1: float, z: int) -> None:
    iso.prism(x0, 0, x1 - x0, 0.25, z, 2, P["walnut"])
    for i, c in enumerate((P["white"], P["pot"], P["white"], P["book3"], P["white"])):
        iso.prism(x0 + 0.15 + i * 0.45, 0.04, 0.2, 0.16, z + 2, 4 if i % 2 else 3, c)


def clock_back(iso: Iso, x: float, z: int) -> None:
    cx, cy = iso.pt(x, 0, z)
    for yy in range(-3, 4):
        for xx in range(-3, 4):
            if xx * xx + yy * yy <= 10:
                iso.cv.set(int(cx) + xx, int(cy) + yy + xx // 2, P["white"] if xx * xx + yy * yy <= 5 else P["walnut"])
    iso.cv.set(int(cx), int(cy) - 1, P["ink"])
    iso.cv.set(int(cx) + 1, int(cy), P["ink"])


# --- furniture modules --------------------------------------------------------------------


def pantry_counter(iso, m) -> None:
    iso.prism(m.x, m.y, m.w, m.d, 0, 12, P["walnut"], P["walnut"], P["walnut_dark"])
    for i in range(1, 4):
        xx = m.x + m.w * i / 4
        iso.line(iso.pt(xx, m.y + m.d, 1), iso.pt(xx, m.y + m.d, 11), P["walnut_dark"])
    iso.prism(m.x - 0.0625, m.y, m.w + 0.125, m.d + 0.0625, 12, 2, P["stone"])
    for i, dx in enumerate((1.6, 1.85, 2.3)):
        iso.prism(m.x + dx, m.y + 0.35, 0.15, 0.15, 14, 3, P["white"] if i != 1 else P["pot"])


def coffee_machine(iso, m) -> None:
    iso.prism(m.x, m.y, m.w, m.d, 14, 11, P["metal"])
    iso.prism(m.x + 0.1, m.y + 0.1, m.w - 0.2, m.d - 0.3, 25, 1, P["chrome"])
    px, py = iso.pt(m.x + 0.45, m.y + m.d, 22)
    iso.cv.set(int(px), int(py), hexc("#D06A4A"))
    iso.prism(m.x + 0.3, m.y + m.d - 0.05, 0.15, 0.15, 14, 3, P["white"])


def cabinet_tall(iso, m) -> None:
    iso.prism(m.x, m.y, m.w, m.d, 0, m.h, hexc("#DAD6CE"))
    iso.line(iso.pt(m.x + 0.12, m.y + m.d, 10), iso.pt(m.x + 0.12, m.y + m.d, 20), P["gray_dark"])
    iso.line(iso.pt(m.x, m.y + m.d, 22), iso.pt(m.x + m.w, m.y + m.d, 22), P["gray"])


def meeting_table(iso, m) -> None:
    for lx, ly in ((m.x + 0.1, m.y + 0.1), (m.x + m.w - 0.25, m.y + 0.1), (m.x + 0.1, m.y + m.d - 0.25),
                   (m.x + m.w - 0.25, m.y + m.d - 0.25)):
        iso.prism(lx, ly, 0.15, 0.15, 0, 9, P["walnut_dark"])
    iso.prism(m.x, m.y, m.w, m.d, 9, 2, P["oak"], P["walnut"], P["walnut_dark"])
    iso.prism(m.x + 0.4, m.y + 0.3, 0.45, 0.3, 11, 0, P["white"], edge=False)
    iso.prism(m.x + 1.5, m.y + 0.5, 0.15, 0.15, 11, 3, P["white"])


def stool(iso, m) -> None:
    iso.prism(m.x + 0.2, m.y + 0.2, 0.1, 0.1, 0, 5, P["walnut_dark"])
    iso.prism(m.x + 0.05, m.y + 0.05, 0.4, 0.4, 5, 2, P["oak"], P["walnut"], P["walnut_dark"])


def verification_rack(iso, m, attention: bool = False) -> None:
    iso.prism(m.x, m.y, m.w, m.d, 0, m.h, P["rack_light"], P["rack"], shade(P["rack"], -0.2))
    for row in range(4):
        z = 6 + row * 6
        iso.line(iso.pt(m.x + 0.1, m.y + m.d, z), iso.pt(m.x + m.w - 0.1, m.y + m.d, z), shade(P["rack"], -0.25))
        for k in range(3):
            c = P["led_warn"] if attention and row == 3 and k == 0 else P["led"]
            px, py = iso.pt(m.x + 0.2 + k * 0.15, m.y + m.d, z + 3)
            iso.cv.set(int(px), int(py), c)


def rug(iso, m) -> None:
    iso.poly(iso.quad(m.x, m.y, m.w, m.d), P["rug_edge"])
    iso.poly(iso.quad(m.x + 0.125, m.y + 0.125, m.w - 0.25, m.d - 0.25), P["rug"])
    for k in range(1, 4):
        yy = m.y + m.d * k / 4
        iso.line(iso.pt(m.x + 0.25, yy), iso.pt(m.x + m.w - 0.25, yy), shade(P["rug"], -0.06))


def doormat(iso, m) -> None:
    iso.poly(iso.quad(m.x, m.y, m.w, m.d), shade(P["rug_edge"], -0.15))
    iso.poly(iso.quad(m.x + 0.125, m.y + 0.125, m.w - 0.25, m.d - 0.25), shade(P["rug"], -0.12))


def sofa(iso, m) -> None:
    iso.prism(m.x, m.y, m.w, m.d, 0, 8, P["fabric"])
    iso.prism(m.x, m.y, 0.3, m.d, 8, 9, P["fabric_light"], P["fabric"], shade(P["fabric"], -0.25))
    for ay in (m.y, m.y + m.d - 0.25):
        iso.prism(m.x + 0.3, ay, m.w - 0.3, 0.25, 8, 4, P["fabric_light"])
    for k in range(1, 3):
        yy = m.y + m.d * k / 3
        iso.line(iso.pt(m.x + 0.35, yy, 8), iso.pt(m.x + m.w, yy, 8), shade(P["fabric"], -0.15))
    iso.prism(m.x + 0.35, m.y + 1.45, 0.3, 0.45, 8, 3, hexc("#B9A27E"))


def coffee_table(iso, m) -> None:
    iso.prism(m.x + 0.08, m.y + 0.08, m.w - 0.16, m.d - 0.16, 0, 5, P["walnut_dark"])
    iso.prism(m.x, m.y, m.w, m.d, 5, 2, P["walnut"])
    iso.prism(m.x + 0.2, m.y + 0.3, 0.3, 0.4, 7, 0, P["white"], edge=False)
    iso.prism(m.x + 0.3, m.y + 0.85, 0.15, 0.15, 7, 3, P["white"])


def plant_tall(iso, m) -> None:
    iso.prism(m.x + 0.08, m.y + 0.08, m.w - 0.16, m.d - 0.16, 0, 9, P["pot"])
    cx, cy = iso.pt(m.x + m.w / 2, m.y + m.d / 2, 9)
    leaves = ((0, -10, 5), (-4, -6, 4), (4, -7, 4), (-2, -15, 4), (3, -14, 3), (0, -19, 3), (-5, -11, 3))
    for dx, dy, r in leaves:
        for yy in range(-r, r + 1):
            for xx in range(-r - 1, r + 2):
                if (xx / (r + 1)) ** 2 + (yy / r) ** 2 <= 1:
                    c = P["plant"] if yy > -r // 2 else P["plant_light"]
                    if xx > r // 2 or yy > r // 2:
                        c = P["plant_dark"]
                    iso.cv.set(int(cx) + dx + xx, int(cy) + dy + yy, c)


def floor_lamp(iso, m) -> None:
    cx, cy = iso.pt(m.x + m.w / 2, m.y + m.d / 2)
    iso.tint(iso.quad(m.x - 0.6, m.y - 0.6, 1.6, 1.6), P["lamp"], 0.10)
    iso.cv.rect(int(cx) - 1, int(cy) - 1, 3, 1, P["walnut_dark"])
    iso.cv.rect(int(cx), int(cy) - 26, 1, 26, P["walnut_dark"])
    for i, wdt in enumerate((5, 7, 7, 9)):
        iso.cv.rect(int(cx) - wdt // 2, int(cy) - 31 + i, wdt, 1, P["lamp"] if i < 3 else shade(P["lamp"], -0.1))


def bookshelf(iso, m) -> None:
    iso.prism(m.x, m.y, m.w, m.d, 0, m.h, P["walnut"], P["walnut"], P["walnut_dark"])
    books = (P["book1"], P["book2"], P["book3"], P["book4"], P["white"], P["book2"])
    for row in range(3):
        z = 3 + row * 11
        iso.face_y(m.x + 0.06, m.x + m.w - 0.06, m.y + m.d, z, z + 9, P["walnut_dark"])
        x = m.x + 0.1
        k = row
        while x < m.x + m.w - 0.2:
            wdt = 0.1 + 0.05 * ((k * 5) % 3)
            iso.face_y(x, x + wdt, m.y + m.d, z, z + 6 + (k % 3), books[k % len(books)])
            x += wdt + 0.03
            k += 1


def desk(iso, m, lit: bool) -> None:
    for lx in (m.x, m.x + m.w - 0.125):
        iso.prism(lx, m.y + 0.1, 0.125, m.d - 0.2, 0, 10, P["walnut_dark"], P["walnut"], P["walnut_dark"])
    iso.prism(m.x, m.y, m.w, m.d, 10, 2, P["oak"], P["walnut"], P["walnut_dark"])
    screen_toward_viewer = m.facing == "N"  # the pet faces -y, so its screen faces +y (us)
    mx, my = m.x + 0.6, (m.y + 0.2 if screen_toward_viewer else m.y + m.d - 0.35)
    iso.prism(mx + 0.3, my + 0.05, 0.15, 0.1, 12, 3, P["monitor"])
    back, front = P["monitor"], (P["screen"] if lit else P["screen_dim"])
    iso.prism(mx, my, 0.8, 0.125, 15, 11, shade(back, 0.1), front if screen_toward_viewer else back,
              shade(back, -0.2))
    if lit and screen_toward_viewer:
        iso.face_y(mx + 0.1, mx + 0.4, my + 0.125, 22, 23, shade(P["screen"], -0.25))
        iso.face_y(mx + 0.1, mx + 0.6, my + 0.125, 19, 20, shade(P["screen"], -0.25))
    ky = m.y + m.d - 0.4 if screen_toward_viewer else m.y + 0.2
    if lit:
        iso.prism(mx + 0.1, ky, 0.6, 0.2, 12, 1, P["gray"])
        iso.prism(m.x + 1.6, ky + 0.02, 0.15, 0.15, 12, 3, P["white"])


def printer_cart(iso, m) -> None:
    iso.prism(m.x, m.y, m.w, m.d, 0, 8, P["walnut"], P["walnut"], P["walnut_dark"])
    iso.prism(m.x + 0.06, m.y + 0.06, m.w - 0.12, m.d - 0.12, 8, 5, P["gray"], P["gray_dark"],
              shade(P["gray_dark"], -0.15))
    iso.prism(m.x + 0.2, m.y + 0.15, m.w - 0.4, 0.3, 13, 0, P["white"], edge=False)


def credenza(iso, m) -> None:
    iso.prism(m.x, m.y, m.w, m.d, 0, m.h, P["walnut"], P["walnut"], P["walnut_dark"])
    for k in (1, 2):
        yy = m.y + m.d * k / 3
        iso.line(iso.pt(m.x + m.w, yy, 2), iso.pt(m.x + m.w, yy, m.h - 2), P["walnut_dark"])
    for i, c in enumerate((P["book1"], P["book2"], P["book3"])):
        iso.prism(m.x + 0.2, m.y + 0.25 + i * 0.15, 0.5, 0.12, m.h, 5 - i, c)
    iso.prism(m.x + 0.25, m.y + 1.3, 0.35, 0.35, m.h, 4, P["pot"])
    cx, cy = iso.pt(m.x + 0.43, m.y + 1.48, m.h + 4)
    for dx, dy in ((0, -2), (-2, -1), (2, -1), (-1, -3), (1, -3), (0, -4), (-3, -2), (3, -2)):
        iso.cv.set(int(cx) + dx, int(cy) + dy, P["plant"] if dy > -3 else P["plant_light"])


def divider(iso, m) -> None:
    iso.prism(m.x, m.y, m.w, m.d, 12, 10, shade(P["gray"], -0.05), P["gray"], P["gray_dark"])


def chair_seat(iso, m, tucked: bool) -> None:
    dy = (0.25 if m.facing == "S" else -0.25) if tucked else 0.0
    cx, cy = m.x + 0.15, m.y + 0.15 + dy
    iso.prism(cx + 0.05, cy + 0.05, 0.1, 0.1, 1, 4, P["chair_dark"])
    iso.prism(cx - 0.1, cy - 0.1, 0.4, 0.4, 0, 1, P["chair_dark"], edge=False)
    iso.prism(m.x, m.y + dy, 0.5, 0.5, 5, 2, P["chair_light"], P["chair"], P["chair_dark"])


def chair_back(iso, m, tucked: bool) -> None:
    dy = (0.25 if m.facing == "S" else -0.25) if tucked else 0.0
    by = m.y + dy if m.facing == "S" else m.y + 0.375 + dy
    iso.prism(m.x + 0.05, by, 0.4, 0.125, 7, 9, P["chair_light"], P["chair"], P["chair_dark"])


# --- mascots (layout-study sprites, 1x) -----------------------------------------------------

CLAUDE_BODY = (2, 0, 12, 7)
CLAUDE_NUBS = ((0, 3, 2, 2), (14, 3, 2, 2))
CLAUDE_LEGS = (2, 5, 9, 12)


def _rect_mask(x, y, w, h):
    return {(xx, yy) for yy in range(y, y + h) for xx in range(x, x + w)}


def claude_sprite(view: str = "front", pose: str = "stand", frame: int = 0) -> dict:
    """{(x, y): colour}; anchor (8, 10) is the floor point between the feet."""

    px: dict = {}
    body = _rect_mask(*CLAUDE_BODY) | _rect_mask(*CLAUDE_NUBS[0]) | _rect_mask(*CLAUDE_NUBS[1])
    if pose == "lie":
        body = _rect_mask(1, 3, 14, 5) | _rect_mask(0, 5, 1, 2) | _rect_mask(15, 5, 1, 2)
    for (x, y) in body:
        c = CLAUDE["body"]
        if (x, y - 1) not in body:
            c = CLAUDE["light"]
        if (x + 1, y) not in body or (x, y + 1) not in body:
            c = CLAUDE["shade"]
        px[(x, y)] = c
    if pose in ("stand", "walk"):
        for i, lx in enumerate(CLAUDE_LEGS):
            lift = 1 if pose == "walk" and (i + frame) % 2 else 0
            for yy in range(7, 10 - lift):
                for xx in (lx, lx + 1):
                    px[(xx, yy)] = CLAUDE["shade"]
    elif pose == "sit":
        for lx in CLAUDE_LEGS:
            px[(lx, 7)] = px[(lx + 1, 7)] = CLAUDE["shade"]
    if view == "front":
        closed = pose == "lie"
        ey = 4 if pose == "lie" else 2
        for ex in (4, 10):
            if closed:
                px[(ex, ey + 1)] = px[(ex + 1, ey + 1)] = CLAUDE["eye"]
            else:
                for dx in (0, 1):
                    for dy in (0, 1):
                        px[(ex + dx, ey + dy)] = CLAUDE["eye"]
    rim = {}
    for (x, y) in px:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (x + dx, y + dy) not in px:
                rim[(x + dx, y + dy)] = CLAUDE["rim"]
    rim.update(px)
    return {"px": rim, "anchor": (8, 10 if pose in ("stand", "walk") else (8 if pose == "sit" else 8))}


def codex_sprite(view: str = "front", pose: str = "stand", frame: int = 0) -> dict:
    px: dict = {}
    head = set()
    for cx, cy, r in ((3.5, 4.0, 3.2), (6.5, 3.2, 3.6), (9.5, 4.0, 3.2), (6.5, 5.5, 3.6)):
        for y in range(0, 10):
            for x in range(0, 13):
                if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r * r:
                    head.add((x, y))
    head = {(x, y) for (x, y) in head if y <= 8}
    if pose == "lie":
        head = {(x + 1, y + 3) for (x, y) in head if y <= 6}
    body = _rect_mask(4, 9, 5, 3) if pose != "lie" else set()
    arms = (_rect_mask(2, 9, 2, 2) | _rect_mask(9, 9, 2, 2)) if pose != "lie" else set()
    for (x, y) in arms | body:
        px[(x, y)] = CODEX["body"] if (x + 1, y) in arms | body else CODEX["body_shade"]
    for (x, y) in head:
        c = CODEX["cloud"]
        if (x + 1, y) not in head or (x, y + 1) not in head:
            c = CODEX["cloud_shade"]
        px[(x, y)] = c
    if pose in ("stand", "walk"):
        for i, lx in enumerate((4, 7)):
            lift = 1 if pose == "walk" and (i + frame) % 2 else 0
            for yy in range(12, 14 - lift):
                px[(lx, yy)] = px[(lx + 1, yy)] = CODEX["joint"]
    if view == "front":
        oy = 3 if pose == "lie" else 0
        for yy in range(3 + oy, 8 + oy):
            for xx in range(3, 10 + (1 if pose == "lie" else 0)):
                px[(xx, yy)] = CODEX["screen"]
        if pose == "lie":
            for xx in (5, 6, 8, 9):
                px[(xx, 6)] = CODEX["glyph"]
        else:
            for (gx, gy) in ((4, 4), (5, 5), (4, 6), (7, 6), (8, 6)):
                px[(gx, gy)] = CODEX["glyph"]
    rim = {}
    for (x, y) in px:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (x + dx, y + dy) not in px:
                rim[(x + dx, y + dy)] = CODEX["rim"]
    rim.update(px)
    return {"px": rim, "anchor": (6, 13 if pose in ("stand", "walk") else 11)}


def sprite(provider: str, *a, **k) -> dict:
    return claude_sprite(*a, **k) if provider == "claude" else codex_sprite(*a, **k)


def blit_sprite(cv: Canvas, spr: dict, x: int, y: int, dim: bool = False, flip: bool = False) -> None:
    ax, ay = spr["anchor"]
    xs = [p[0] for p in spr["px"]]
    w = max(xs) + min(xs)
    for (px, py), c in spr["px"].items():
        if dim:
            c = mix(c, (150, 150, 150), 0.55)
        sx = (w - px) if flip else px
        cv.set(x + sx - ax, y + py - ay, c)


def foot_shadow(cv: Canvas, x: int, y: int, w: int = 7) -> None:
    for xx in range(-w, w + 1):
        for yy in (-1, 0, 1):
            if (xx / (w + 0.5)) ** 2 + (yy / 1.6) ** 2 <= 1:
                cv.tint(x + xx, y + yy, P["floor_shadow"], 0.35)


BUBBLE_GLYPH = {"attention": ("!", "warn"), "unobservable": ("?", "calm"), "error": ("X", "bad"),
                "finishing": ("...", "calm"), "sleep": ("Z", "calm"), "done": ("+", "led"), "new": ("+", "led")}


def bubble(cv: Canvas, x: int, y: int, kind: str) -> None:
    glyph, color = BUBBLE_GLYPH[kind]
    w = 9 if glyph != "..." else 11
    cv.rect(x - w // 2, y - 9, w, 8, P["bubble"])
    for xx in range(x - w // 2, x - w // 2 + w):
        cv.set(xx, y - 10, P["ink"])
        cv.set(xx, y - 1, P["ink"])
    for yy in range(y - 9, y - 1):
        cv.set(x - w // 2 - 1, yy, P["ink"])
        cv.set(x - w // 2 + w, yy, P["ink"])
    cv.set(x, y, P["ink"])
    cv.set(x - 1, y - 1, P["bubble"])
    if glyph == "...":
        for k in (-3, 0, 3):
            cv.set(x + k, y - 4, P[color])
    else:
        text(cv, x - 1, y - 8, glyph, P[color])


def mug(cv: Canvas, x: int, y: int) -> None:
    cv.rect(x, y, 2, 2, P["white"])
    cv.set(x + 2, y, P["white"])
    cv.set(x, y - 2, shade(P["white"], -0.2))
    cv.set(x + 1, y - 3, shade(P["white"], -0.2))


def broom(cv: Canvas, x: int, y: int) -> None:
    for i in range(10):
        cv.set(x + i // 3, y - 10 + i, P["walnut"])
    cv.rect(x + 2, y - 1, 4, 2, hexc("#C9AE6E"))
