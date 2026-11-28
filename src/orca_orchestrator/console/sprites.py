"""The yard's pixel art: two small pets, their props, a desk and a dark room. No UI framework here.

The pets are drawn for this project as plain string arrays, after reference
images the user supplied: a small orange critter (``claude``) and a blue
cloud-headed terminal robot (``codex``). They are low-resolution redrawings,
not the original artwork. They are mascots, not Workers: nothing in the yard
shows them doing Claude or Codex work (this build sends nothing to a Worker).

A pet has a few body frames (``idle``, ``blink``, ``walk1``, ``walk2``,
``happy``, ``error``) and props drawn over or beside it (a floating screen and
keyboard for typing, a hard hat and hammer for building, a thought bubble,
``?``/``!`` marks, a sparkle, a broom, ``z``). ``pet_layers`` turns an action
name and an animation phase into the body frame and prop placements.

``.`` is transparent; every other key is opaque, black eyes included. Rows
shorter than their sprite are padded with ``.`` on the right (``_rows``), so
widths stay consistent; ``Sprite`` rejects anything else. To restyle a pet
pass a palette to ``pet_frames``. There is no editor.
"""

from __future__ import annotations

import hashlib
from typing import Mapping

from .pixels import Color, Pixels, Sprite, blank, sprite


def _rows(width: int, rows: list[str]) -> list[str]:
    for r in rows:
        if len(r) > width:
            raise ValueError(f"row {r!r} is wider than {width}")
    return [r.ljust(width, ".") for r in rows]


FRAME_NAMES = ("idle", "blink", "walk1", "walk2", "happy", "error")

# --- Claude pet: flat orange critter, two tall eyes, side arms, four thin legs (14x7) --------------

CLAUDE_PALETTE: dict[str, Color] = {"o": "#d97757", "d": "#a9573c", "k": "#0b0b0d"}

_O = "o" * 10
_E = "oo" + "k" + "oooo" + "k" + "oo"
_LEG, _LEG_A, _LEG_B = ".d.d..d.d.", "d.d..d.d..", "..d.d..d.d"


def _claude(top_arms=False, eyes=(1, 2), legs=(_LEG, _LEG)) -> list[str]:
    def row(i: int, arms: bool) -> str:
        body = _E if i in eyes else _O
        return ("oo" if arms else "..") + body + ("oo" if arms else "..")

    if top_arms:
        rows = ["o." + (_E if 0 in eyes else _O) + ".o", row(1, True), row(2, False), row(3, False), row(4, False)]
    else:
        rows = [row(0, False), row(1, False), row(2, True), row(3, True), row(4, False)]
    return rows + [".." + legs[0] + "..", ".." + legs[1] + ".."]


_X = "o" + "k" + "o" + "k" + "oooo" + "k" + "o" + "k" + "o"  # 12: sideways x-eyes
CLAUDE_ROWS: dict[str, list[str]] = {
    "idle": _claude(),
    "blink": _claude(eyes=(2,)),
    "walk1": _claude(legs=(_LEG, _LEG_A)),
    "walk2": _claude(legs=(_LEG, _LEG_B)),
    "happy": _claude(top_arms=True, eyes=(1,), legs=(_LEG, _LEG_A)),
    "error": _rows(14, ["", "", "", ".." + _O, "." + _X, "d" + "o" * 12 + "d", ""]),
}

# --- Codex pet: cloud head with a terminal face, small body (12x12) ----------------------------------

CODEX_PALETTE: dict[str, Color] = {
    "B": "#5d78f2", "H": "#8fa3ff", "s": "#101531", "c": "#8fe3ff", "r": "#ff6b6b",
    "D": "#4a60d8", "L": "#33449e",
}


def _codex_head(face: tuple[str, str, str]) -> list[str]:
    return ["..BBB.BBB...", ".BHBBBBBBBB.", "BB" + "s" * 8 + "BB",
            *("BB" + f + "BB" for f in face), ".BBBBBBBBBB.", "..BBBBBBBB.."]


_FACE = ("sc" + "s" * 6, "ssc" + "s" * 5, "sc" + "ss" + "ccc" + "s")  # ">_"
_FACE_BLINK = ("s" * 8, "sccssccs", "s" * 8)
_FACE_HAPPY = ("s" * 8, "sscsscss", "scsccscs")  # "^ ^"
_FACE_ERROR = ("rsr" + "ss" + "rsr", "srs" + "ss" + "srs", "rsr" + "ss" + "rsr")  # "x x"
_BODY = ["...DDDDDD...", "..BDDcDDDB..", "...DDDDDD..."]
_BODY_UP = ["..B.DDDD.B..", "...DDcDDD...", "...DDDDDD..."]
_C_LEGS, _C_LEGS_A, _C_LEGS_B = ["...LL..LL..."], ["..LL....LL.."], ["....LL.LL..."]

CODEX_ROWS: dict[str, list[str]] = {
    "idle": _codex_head(_FACE) + _BODY + _C_LEGS,
    "blink": _codex_head(_FACE_BLINK) + _BODY + _C_LEGS,
    "walk1": _codex_head(_FACE) + _BODY + _C_LEGS_A,
    "walk2": _codex_head(_FACE) + _BODY + _C_LEGS_B,
    "happy": _codex_head(_FACE_HAPPY) + _BODY_UP + _C_LEGS_A,
    "error": _codex_head(_FACE_ERROR) + _BODY + _C_LEGS,
}

PETS: dict[str, tuple[dict[str, list[str]], dict[str, Color]]] = {
    "codex": (CODEX_ROWS, CODEX_PALETTE),
    "claude": (CLAUDE_ROWS, CLAUDE_PALETTE),
}
PET_NAMES = {"codex": "Codex 펫", "claude": "Claude 펫"}


def pet_frames(name: str, palette: Mapping[str, Color] | None = None) -> dict[str, Sprite]:
    rows, colors = PETS[name]
    colors = {**colors, **(palette or {})}
    return {frame: sprite(rows[frame], colors) for frame in FRAME_NAMES}


def pet_size(name: str) -> tuple[int, int]:
    rows = PETS[name][0]["idle"]
    return len(rows[0]), len(rows)


# --- props -------------------------------------------------------------------------------------

PROP_PALETTE: dict[str, Color] = {
    "w": "#f2f4f8", "b": "#3b82f6", "y": "#facc15", "Y": "#ca8a04", "g": "#9aa3b2", "G": "#4b5563",
    "m": "#0a0d14", "n": "#17213d", "l": "#4ade80", "L": "#60a5fa", "o": "#fb923c", "t": "#8b5a2b",
    "z": "#cbd5e1",
}

_PROP_ROWS: dict[str, list[str]] = {
    "bubble1": [".wwwww.", "wwwwwww", "wwwbwww", "wwwwwww", ".wwwww.", ".w....."],
    "bubble2": [".wwwww.", "wwwwwww", "wwbwbww", "wwwwwww", ".wwwww.", ".w....."],
    "bubble3": [".wwwww.", "wwwwwww", "wbwbwbw", "wwwwwww", ".wwwww.", ".w....."],
    "screen1": ["mmmmmmmmmm", "mnnnnnnnnm", "mnllllnnnm", "mnLLnnnnnm", "mnlllllnnm", "mmmmmmmmmm"],
    "screen2": ["mmmmmmmmmm", "mnnnnnnnnm", "mnlllnnnnm", "mnLLLLnnnm", "mnllnnnnnm", "mmmmmmmmmm"],
    "keyboard": ["gGgGgGgGgG", "GGGGGGGGGG"],
    "helmet": _rows(12, ["...yyyyyy", ".yyyyyyyyyy", "YYYYYYYYYYYY"]),
    "hammer": ["GGG", "GGG", ".t.", ".t.", ".t."],
    "sparkle1": [".y.", "yyy", ".y."],
    "sparkle2": ["...", ".y.", "..."],
    "exclaim": ["yy", "yy", "yy", "..", "yy"],
    "question": [".oo.", "o..o", "...o", "..o.", "....", "..o."],
    "broom": _rows(6, [".....t", "....t", "....t", "...t", "...t", "..t", "YYYY", "yYyY"]),
    "zz": ["zzz", ".z.", "zzz"],
}


def prop_frames() -> dict[str, Sprite]:
    return {name: sprite(rows, PROP_PALETTE) for name, rows in _PROP_ROWS.items()}


# Actions a pet can show; motion picks one, the view draws it.
ACTIONS = ("idle", "blink", "walk", "run", "type", "build", "think", "error", "question", "notify", "happy",
           "sweep", "sleep", "watch")
# Coding and building say "this agent is working". They are kept for a Worker that is observed for the pet's own
# agent; this build observes no Worker, so motion never picks them (a local check is watched, not worked on).
WORKER_ACTIONS = frozenset({"type", "build"})


def pet_layers(name: str, action: str, phase: int = 0) -> tuple[str, list[tuple[str, int, int]]]:
    """The body frame and the props, each at (dx, dy) from the pet's top-left pixel."""

    w, h = pet_size(name)
    odd = phase % 2
    if action in ("walk", "run"):
        return ("walk1" if odd == 0 else "walk2"), []
    if action == "type":
        return "idle", [(f"screen{odd + 1}", (w - 10) // 2, -7), ("keyboard", (w - 10) // 2, h - 3)]
    if action == "build":
        return "idle", [("helmet", (w - 12) // 2, -2), ("hammer", w - 1, h - 8 - (2 if odd else 0))]
    if action == "think":
        return "idle", [(f"bubble{phase % 3 + 1}", w - 3, -7)]
    if action == "error":
        return "error", []
    if action == "question":
        return "idle", [("question", (w - 4) // 2, -7 - odd)]
    if action == "notify":
        return "idle", [("exclaim", (w - 2) // 2, -6 - odd)]
    if action == "happy":
        return "happy", [(f"sparkle{odd + 1}", -3, -3), (f"sparkle{2 - odd}", w, -1)]
    if action == "sweep":
        return "idle", [("broom", -5 + 2 * odd, h - 8)]
    if action == "sleep":
        return "blink", [("zz", w - 1, -4 - phase % 3)]
    if action == "blink":
        return "blink", []
    if action == "watch":  # looks at the monitor, no tools: a local check runs, the pet only watches
        return ("blink" if phase % 12 == 11 else "idle"), []
    return "idle", []


# --- desk (2.5D: the top surface is seen from above) ----------------------------------------------

DESK_PALETTE: dict[str, Color] = {
    "m": "#090b11", "n": "#16203a", "l": "#4ade80", "L": "#60a5fa", "s": "#3b4252",
    "T": "#7a5a43", "t": "#5c4433", "F": "#3b2b21", "c": "#e5e7eb",
}
DESK_W = 24
_DESK_TOP = [
    "......mmmmmmmmmm",
    "......mnnnnnnnnm",
]
_DESK_SCREEN = {
    "off": ["......mnnnnnnnnm", "......mnnnnnnnnm", "......mnnnnnnnnm"],
    "on1": ["......mnllllnnnm", "......mnLLLnnnnm", "......mnllllllnm"],
    "on2": ["......mnlllnnnnm", "......mnLLLLLnnm", "......mnllnnnnnm"],
}
_DESK_BOTTOM = [
    "......mmmmmmmmmm",
    "..........ss.........cc",
    "..TTTTTTTTTTTTTTTTTTTTTT",
    ".TttttttttttttttttttttT",
    "TttttttttttttttttttttT",
    "FFFFFFFFFFFFFFFFFFFFFF",
    "FFFFFFFFFFFFFFFFFFFFFF",
    ".F..................F",
    ".F..................F",
]
DESK_ROWS = {name: _rows(DESK_W, _DESK_TOP + screen + _DESK_BOTTOM) for name, screen in _DESK_SCREEN.items()}
DESK_SIZE = (DESK_W, len(DESK_ROWS["off"]))


def desk_frames() -> dict[str, Sprite]:
    return {name: sprite(rows, DESK_PALETTE) for name, rows in DESK_ROWS.items()}


# --- the room ------------------------------------------------------------------------------------

SELECT_COLOR: Color = "#ffd84a"

ROOM = {
    "wall": "#141823", "wall_panel": "#171c29", "molding": "#1d2334", "base": "#0c0e14", "base_line": "#262d42",
    "tile_a": "#1a1e2b", "tile_b": "#1e2331", "grout": "#232939",
    "rug": "#251d3a", "rug_edge": "#3a2d57",
    "glass": "#0b1430", "frame": "#2a3146", "star": "#d6dbe6", "moon": "#f1e3a3",
    "leaf": "#2f6b45", "leaf_dark": "#24533a", "pot": "#6b3f2a",
    "shadow": "#07080c",
}


def shadow(width: int) -> Sprite:
    """A soft oval under a pet's feet, on the floor."""

    w = max(4, width - 2)
    return sprite([".." + "S" * (w - 4) + "..", "." + "S" * (w - 2) + "."], {"S": ROOM["shadow"]})


def _noise(x: int, y: int, salt: str) -> int:
    """A fixed pattern (no random state): the same room every time for the same size."""

    return hashlib.blake2b(f"{salt}:{x}:{y}".encode(), digest_size=2).digest()[0]


def wall_height(px_height: int) -> int:
    return max(6, px_height // 4)


def room(width: int, height: int) -> Pixels:
    """A dark room in pixels: a back wall with a night window and a plant, and a tiled floor seen at an angle.

    The floor tiles are diamonds (the 2.5D look); a rug lies in the middle of the floor.
    """

    pixels = blank(width, height, ROOM["wall"])
    wall = wall_height(height)
    for y in range(height):
        row = pixels[y]
        for x in range(width):
            if y < wall:
                if y == 0:
                    row[x] = ROOM["molding"]
                elif y >= wall - 2:
                    row[x] = ROOM["base"] if y == wall - 1 else ROOM["base_line"]
                elif x % 14 == 0:
                    row[x] = ROOM["wall_panel"]
                continue
            fy = y - wall
            u, v = x + 2 * fy, x - 2 * fy
            if u % 16 == 0 or v % 16 == 0:
                row[x] = ROOM["grout"]
            else:
                row[x] = ROOM["tile_a"] if (u // 16 + v // 16) % 2 == 0 else ROOM["tile_b"]
    # rug: an ellipse in the middle of the floor
    floor_h = height - wall
    cx, cy = width * 0.45, wall + floor_h * 0.6
    rx, ry = max(6.0, width * 0.18), max(2.0, floor_h * 0.25)
    for y in range(wall, height):
        for x in range(width):
            d = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
            if d <= 1.0:
                pixels[y][x] = ROOM["rug_edge"] if d > 0.78 else ROOM["rug"]
    # window with stars and a moon
    if width >= 40 and wall >= 8:
        wx, wy, ww, wh = 6, 2, 18, wall - 5
        for y in range(wy, wy + wh):
            for x in range(wx, wx + ww):
                edge = y in (wy, wy + wh - 1) or x in (wx, wx + ww - 1, wx + ww // 2)
                if edge:
                    pixels[y][x] = ROOM["frame"]
                else:
                    pixels[y][x] = ROOM["star"] if _noise(x, y, "s") < 10 else ROOM["glass"]
        mx, my = wx + ww - 5, wy + 2
        for dy, line in enumerate((".mm.", "mm..", "mm..", ".mm.")):
            for dx, ch in enumerate(line):
                if ch == "m" and my + dy < wy + wh - 1:
                    pixels[my + dy][mx + dx] = ROOM["moon"]
    # a plant against the wall, on the left
    if width >= 40:
        px0, base = 2, wall + 1
        plant = ["..l.l..", ".lLlLl.", "lLlLlLl", ".lLlLl.", "..ppp..", "..ppp.."]
        colors = {"l": ROOM["leaf"], "L": ROOM["leaf_dark"], "p": ROOM["pot"]}
        for dy, line in enumerate(plant):
            y = base - len(plant) + 1 + dy
            for dx, ch in enumerate(line):
                if ch != "." and 0 <= y < height and px0 + dx < width:
                    pixels[y][px0 + dx] = colors[ch]
    return pixels
