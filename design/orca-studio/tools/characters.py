"""Concept sprites for the two mascots, drawn from shapes on a shared canvas.

Every size is rebuilt from the same shape plan (not up- or down-scaled pixels), lit from the top-left, with a
selective outline in a dark hue of the form. Canvas sizes: 16, 32 (production size), 48 (comparison).
Anchor: bottom-centre of the canvas = the point between the feet on the floor.
"""

from __future__ import annotations

from pixel import Canvas, Mask, box, disc, hexc, oval, paint_form, shade

CODEX = {
    "cloud": hexc("#a9d2f7"), "cloud_rim": hexc("#35548f"),
    "screen": hexc("#16213f"), "screen_rim": hexc("#0d1530"), "glyph": hexc("#7ff0ff"), "glyph_dim": hexc("#3c7f9a"),
    "body": hexc("#dfe7f1"), "body_rim": hexc("#4d5c7e"), "joint": hexc("#8fa3c4"), "lamp": hexc("#7ff0ff"),
    "warn": hexc("#ffcf5a"), "bad": hexc("#ff8a7a"),
}
CLAUDE = {
    "body": hexc("#e27a4c"), "rim": hexc("#82331f"), "leg": hexc("#c4603a"),
    "eye": hexc("#2a1712"), "spark": hexc("#fff4e6"), "blush": hexc("#f2a07c"),
}

EXPRESSIONS = ("neutral", "blink", "happy", "attention", "unknown", "fail", "rest")


def _s(v: float, s: float) -> int:
    return int(round(v * s))


def _glyph(cv: Canvas, x: int, y: int, rows: list[str], c, k: int = 1) -> None:
    for j, row in enumerate(rows):
        for i, ch in enumerate(row):
            if ch == "#":
                cv.rect(x + i * k, y + j * k, k, k, c)


CODEX_FACES = {
    "neutral": ["#.....", ".#....", "#..###"],          # >_
    "blink": ["......", "......", "##.###"],            # cursor only, eyes closed
    "happy": [".#..#.", "#.##.#", "......"],            # ^ ^
    "attention": ["..#...", "..#...", "..#..."],          # !
    "unknown": [".##...", "...#..", "..#..."],            # ?
    "fail": ["#.#.#.#", ".#...#.", "#.#.#.#"],           # x x
    "rest": ["......", "##.##.", "......"],             # - - (eyes closed, resting)
}

CLAUDE_EYES = {
    "neutral": ["##", "##"], "blink": ["..", "##"], "happy": [".#.", "#.#"], "attention": ["##", "##", "##"],
    "unknown": ["##", "##"], "fail": ["#.#", ".#.", "#.#"], "rest": ["...", "###"],
}


def _ground(cv: Canvas) -> Canvas:
    """Puts the lowest opaque pixel on row size-2, so the anchor (bottom centre, row size-1) is the floor contact."""

    rows = [y for y in range(cv.h) if any(c is not None for c in cv.px[y])]
    shift = (cv.h - 2) - max(rows) if rows else 0
    if shift > 0:
        cv.px = [[None] * cv.w for _ in range(shift)] + cv.px[:cv.h - shift]
    return cv


def codex(size: int = 32, expression: str = "neutral", view: str = "front", breath: int = 0,
          step: int = 0) -> Canvas:
    s = size / 32
    cv = Canvas(size, size)
    by = _s(breath, s) if size >= 32 else 0  # breathing: the head bobs 1 px, the feet stay
    # legs and feet first (behind the body)
    legs: Mask = set()
    if view == "front":
        for lx in (12.5, 18.5):
            legs |= box(_s(lx, s), _s(25.5, s), max(1, _s(2.5, s)), max(1, _s(3, s)))
            legs |= box(_s(lx - 1, s), _s(28, s), max(2, _s(4.5, s)), max(1, _s(2, s)), r=1 if size >= 32 else 0)
    else:
        a, b = (12.5, 17.5) if step == 0 else (14, 16)
        for lx in (a, b):
            legs |= box(_s(lx, s), _s(25.5, s), max(1, _s(2.5, s)), max(1, _s(3, s)))
            legs |= box(_s(lx, s), _s(28, s), max(2, _s(4.5, s)), max(1, _s(2, s)), r=1 if size >= 32 else 0)
    paint_form(cv, legs, CODEX["joint"], rim=CODEX["body_rim"], light=0.15, dark=0.3)
    # torso and arms
    if view == "front":
        torso = box(_s(10.5, s), _s(18.5, s) + by, _s(11, s), _s(8, s), r=max(1, _s(2.5, s)))
        arms = box(_s(7.5, s), _s(19.5, s) + by, max(1, _s(3, s)), max(1, _s(5, s)), r=1) | \
            box(_s(21.5, s), _s(19.5, s) + by, max(1, _s(3, s)), max(1, _s(5, s)), r=1)
    else:
        torso = box(_s(11, s), _s(18.5, s) + by, _s(10, s), _s(8, s), r=max(1, _s(2.5, s)))
        arms = box(_s(15, s), _s(19.5, s) + by, max(1, _s(3, s)), max(1, _s(5, s)), r=1)
    paint_form(cv, arms, CODEX["body"], rim=CODEX["body_rim"], light=0.2, dark=0.35)
    paint_form(cv, torso, CODEX["body"], rim=CODEX["body_rim"], light=0.25, dark=0.32)
    if size >= 32:
        lamp = CODEX["lamp"] if expression not in ("rest", "fail") else CODEX["glyph_dim"]
        cx = _s(16 if view == "front" else 17, s)
        cv.rect(cx - 1, _s(21.5, s) + by, max(1, _s(2, s)), max(1, _s(2, s)), lamp)
    # cloud head: overlapping puffs, flat-ish bottom
    if view == "front":
        puffs = disc(_s(10.5, s), _s(12.5, s) + by, 5.5 * s) | disc(_s(16, s), _s(9.5, s) + by, 6.8 * s) | \
            disc(_s(21.5, s), _s(12.5, s) + by, 5.5 * s) | disc(_s(16, s), _s(13.5, s) + by, 6.5 * s) | \
            box(_s(6, s), _s(12, s) + by, _s(20.5, s), _s(6.5, s), r=max(1, _s(3, s)))
    else:
        puffs = disc(_s(12, s), _s(11.5, s) + by, 6.0 * s) | disc(_s(18, s), _s(10, s) + by, 6.2 * s) | \
            disc(_s(15, s), _s(14, s) + by, 6.2 * s) | box(_s(7.5, s), _s(12, s) + by, _s(17.5, s), _s(6.5, s),
                                                            r=max(1, _s(3, s)))
    paint_form(cv, puffs, CODEX["cloud"], rim=CODEX["cloud_rim"], light=0.45, dark=0.28, ramp=0.25)
    # screen face
    if view == "front":
        sx, sy, sw, sh = _s(9.5, s), _s(10.5, s) + by, _s(13.5, s), _s(7, s)
    else:
        sx, sy, sw, sh = _s(15.5, s), _s(10.5, s) + by, _s(8.5, s), _s(7, s)
    screen = box(sx, sy, max(3, sw), max(2, sh), r=max(1, _s(2, s)))
    paint_form(cv, screen, CODEX["screen"], light=0.12, dark=0.25)
    glyph_c = {"fail": CODEX["bad"], "attention": CODEX["warn"], "unknown": CODEX["warn"],
               "rest": CODEX["glyph_dim"]}.get(expression, CODEX["glyph"])
    rows = CODEX_FACES[expression]
    if size < 24:  # 16 px: two pixels of face
        mid = sy + max(1, sh // 2)
        cv.set(sx + 1, mid, glyph_c)
        cv.set(sx + max(2, sw - 3), mid, glyph_c if expression != "blink" else CODEX["glyph_dim"])
    else:
        k = 2 if size >= 48 else 1
        gw = max(len(r) for r in rows) * k
        if view == "side":
            rows = [r[:3] for r in rows]
            gw = 3 * k
        gx = sx + (sw - gw) // 2 + (1 if view == "front" else 0)
        gy = sy + (sh - len(rows) * k) // 2
        _glyph(cv, gx, gy, rows, glyph_c, k)
        if size >= 32:  # screen sheen: one pixel line, top-left
            cv.hline(sx + 2, sy + 1, max(1, _s(3, s)), shade(CODEX["screen"], 0.25))
    return _ground(cv)


def claude(size: int = 32, expression: str = "neutral", view: str = "front", breath: int = 0,
           step: int = 0) -> Canvas:
    s = size / 32
    cv = Canvas(size, size)
    by = _s(breath, s) if size >= 32 else 0
    squash = 1 if breath and size >= 32 else 0
    legs: Mask = set()
    xs = (8, 13, 18, 23) if view == "front" else ((10, 15, 20) if step == 0 else (11, 15, 19))
    for lx in xs:
        legs |= box(_s(lx, s), _s(24.5, s), max(1, _s(2.5, s)), max(1, _s(4, s)))
    paint_form(cv, legs, CLAUDE["leg"], rim=CLAUDE["rim"], light=0.1, dark=0.3)
    if view == "front":
        body = box(_s(5.5, s), _s(10, s) + by + squash, _s(21, s), _s(15.5, s) - squash, r=max(1, _s(3.5, s)))
        arms = box(_s(1.5, s), _s(15, s) + by, max(1, _s(4.5, s)), max(1, _s(4, s)), r=max(0, _s(1.5, s))) | \
            box(_s(26, s), _s(15, s) + by, max(1, _s(4.5, s)), max(1, _s(4, s)), r=max(0, _s(1.5, s)))
        if expression == "happy":  # arms up
            arms = box(_s(2, s), _s(10, s) + by, max(1, _s(4, s)), max(1, _s(5, s)), r=1) | \
                box(_s(26, s), _s(10, s) + by, max(1, _s(4, s)), max(1, _s(5, s)), r=1)
    else:
        body = box(_s(7.5, s), _s(10, s) + by + squash, _s(18, s), _s(15.5, s) - squash, r=max(1, _s(3.5, s)))
        arms = box(_s(22, s), _s(15, s) + by, max(1, _s(5, s)), max(1, _s(4, s)), r=max(0, _s(1.5, s)))
    paint_form(cv, arms, CLAUDE["body"], rim=CLAUDE["rim"], light=0.25, dark=0.3)
    paint_form(cv, body, CLAUDE["body"], rim=CLAUDE["rim"], light=0.32, dark=0.3, ramp=0.22)
    # eyes
    eyes = CLAUDE_EYES[expression]
    k = 2 if size >= 48 else 1
    ey = _s(14.5, s) + by + squash
    positions = (_s(11.5, s), _s(20.5, s)) if view == "front" else (_s(20.5, s),)
    if size < 24:
        for ex in positions:
            cv.set(ex, ey, CLAUDE["eye"])
            if expression not in ("blink", "rest"):
                cv.set(ex, ey + 1, CLAUDE["eye"])
        return _ground(cv)
    for ex in positions:
        for j, row in enumerate(eyes):
            for i, ch in enumerate(row):
                if ch == "#":
                    cv.rect(ex + i * k - (k * (len(row) - 1)) // 2, ey + j * k, k, k, CLAUDE["eye"])
        if expression in ("neutral", "attention", "unknown"):
            cv.rect(ex - (k * (len(eyes[0]) - 1)) // 2, ey, k, k, CLAUDE["spark"])
    if size >= 32 and view == "front":
        cv.set(_s(8.5, s), _s(18.5, s) + by + squash, CLAUDE["blush"])
        cv.set(_s(23.5, s), _s(18.5, s) + by + squash, CLAUDE["blush"])
    return _ground(cv)


def mascot(name: str, *a, **k) -> Canvas:
    return codex(*a, **k) if name == "codex" else claude(*a, **k)
