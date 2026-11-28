"""Three concept rooms at the proposed logical resolution (480x270, 16 px grid, top-down 3/4 view).

Shared rules: one light direction (key light from the upper left: window / ceiling; shadows fall down-right),
selective outlines in dark hues, flat banded light pools, objects drawn back to front by their floor line.
Each room has the same functional zones so states map the same way in every concept:
Codex station, Claude station, verification station, status board, lounge, shared floor, link device.
"""

from __future__ import annotations

from characters import mascot
from pixel import Canvas, checker_icon, hexc, mix, shade, text, text_width

W, H = 480, 270
FLOOR_TOP = 84
FLOOR_BOTTOM = 258

GREEN, AMBER, RED, GREY, CYAN = hexc("#5fd38a"), hexc("#ffc24d"), hexc("#ff6b5e"), hexc("#8b93a3"), hexc("#7fe0f0")


# --- palettes -------------------------------------------------------------------------------

PALETTES = {
    "A": {  # Cozy Developer Studio: warm wood, sage, evening lamps
        "beam": "#4a3426", "wall": "#a99a80", "wall_lo": "#8c7056", "rail": "#6b513e", "base": "#4b3628",
        "floor": "#a98462", "floor2": "#a47f5d", "seam": "#8e6c4f", "lip": "#3a2a20",
        "wood_top": "#c99463", "wood_front": "#94623f", "wood_rim": "#4f3322",
        "glass": "#26355a", "frame": "#5b4130", "star": "#e8e6d0", "moon": "#f3e7b5", "curtain": "#b86a4e",
        "rug": "#7e9a82", "rug2": "#6c8a72", "rug_edge": "#55705c",
        "leaf": "#5e9a52", "leaf2": "#3f7040", "pot": "#b6643f",
        "cork": "#c99f6c", "cork_frame": "#6b4a33", "card": "#f4ead6",
        "metal": "#6c7a7e", "metal_front": "#4f5b5f", "metal_rim": "#2c3437",
        "screen": "#18233a", "monitor": "#383d4f",
        "fabric": "#a85a48", "fabric_front": "#7e3f33", "fabric_rim": "#4a2219",
        "chair": "#5f7593", "book1": "#8a4a3a", "book2": "#4f6f8a", "book3": "#c9a24a", "book4": "#6a8a5a",
        "warm": "#ffd08a", "cool": "#9fc6ff", "shadow": "#2a1a14", "ambient": None,
    },
    "B": {  # Modern Pixel Lab: bright neutral, modular, teal accents
        "beam": "#aab4bc", "wall": "#e7eaed", "wall_lo": "#d3d9de", "rail": "#b9c2c9", "base": "#8f9aa4",
        "floor": "#d7dce0", "floor2": "#cfd5da", "seam": "#bcc4ca", "lip": "#6d7882",
        "wood_top": "#f4f6f7", "wood_front": "#c6ced5", "wood_rim": "#6f7b86",
        "glass": "#a9dbe6", "frame": "#b4bec6", "star": "#ffffff", "moon": "#ffffff", "curtain": "#3fb5a8",
        "rug": "#c9e6e1", "rug2": "#bfe0da", "rug_edge": "#8cc9bf",
        "leaf": "#4f9a6e", "leaf2": "#3b7a55", "pot": "#f0f2f3",
        "cork": "#1f2a33", "cork_frame": "#8f9aa4", "card": "#2c3b47",
        "metal": "#dfe4e8", "metal_front": "#b5bec6", "metal_rim": "#66727d",
        "screen": "#1d2b36", "monitor": "#5b6772",
        "fabric": "#3fb5a8", "fabric_front": "#2e8b81", "fabric_rim": "#1d5b55",
        "chair": "#3fb5a8", "book1": "#3fb5a8", "book2": "#7a8a99", "book3": "#f2b84b", "book4": "#4f9a6e",
        "warm": "#fff3d6", "cool": "#d8f3ff", "shadow": "#5d6a74", "ambient": None,
    },
    "C": {  # Midnight Workshop: dark plum room, focused warm pools, one small neon
        "beam": "#1b1a29", "wall": "#33304a", "wall_lo": "#2a273d", "rail": "#3f3b58", "base": "#1d1b2b",
        "floor": "#4a3f52", "floor2": "#463b4e", "seam": "#382f41", "lip": "#141320",
        "wood_top": "#8a6248", "wood_front": "#5e4232", "wood_rim": "#2a1d17",
        "glass": "#151b36", "frame": "#22202f", "star": "#d9dcf0", "moon": "#f0e2b0", "curtain": "#4a3148",
        "rug": "#5a3a4a", "rug2": "#523545", "rug_edge": "#3e2836",
        "leaf": "#4f8a5a", "leaf2": "#355f40", "pot": "#8a4a3a",
        "cork": "#5c4b3d", "cork_frame": "#2e241d", "card": "#e9dcc4",
        "metal": "#5a6470", "metal_front": "#414a55", "metal_rim": "#1e232a",
        "screen": "#0f1c22", "monitor": "#2b303c",
        "fabric": "#7a4152", "fabric_front": "#58303c", "fabric_rim": "#2e1820",
        "chair": "#4a5570", "book1": "#7a3e3a", "book2": "#3e5a7a", "book3": "#b8913e", "book4": "#4e6e4a",
        "warm": "#ffbf6b", "cool": "#6fd8ff", "shadow": "#0d0a14", "ambient": "#1a1630",
    },
}


def pal(concept: str) -> dict:
    return {k: (hexc(v) if isinstance(v, str) else v) for k, v in PALETTES[concept].items()}


# --- kit ------------------------------------------------------------------------------------


def block(cv: Canvas, x, y, w, depth, height, top, front, rim, shadow=None):
    """A box seen from the upper front: top face, front face, coloured rim, cast shadow down-right."""

    if shadow is not None:
        cv.tint_rect(x + 3, y + depth + height, w, 3, shadow, 0.35)
        cv.tint_rect(x + w, y + depth + 2, 3, height, shadow, 0.3)
    cv.rect(x, y, w, depth, top)
    cv.hline(x, y, w, shade(top, 0.18))
    cv.rect(x, y + depth, w, height, front)
    cv.hline(x, y + depth, w, shade(front, 0.15))
    cv.hline(x, y + depth + height - 1, w, shade(front, -0.2))
    for xx in range(x - 1, x + w + 1):
        cv.set(xx, y - 1, rim)
        cv.set(xx, y + depth + height, rim)
    for yy in range(y, y + depth + height):
        cv.set(x - 1, yy, rim)
        cv.set(x + w, yy, rim)


def contact_shadow(cv: Canvas, cx, foot, w, P):
    cv.tint_ellipse(cx + 1, foot, w / 2, 2.6, P["shadow"], 0.38)


def plant(cv: Canvas, x, y, P, big=False):
    """x, y: the pot's top-left. Leaves are a few hand-placed clusters, not noise."""

    r = 7 if big else 5
    cv.tint_ellipse(x + 7, y + 12, 7, 2, P["shadow"], 0.3)
    leaves = [(x + 2, y - r + 1), (x + 8, y - r - 2), (x + 12, y - r + 2), (x + 5, y - r - 5), (x + 10, y - r - 6)]
    for i, (lx, ly) in enumerate(leaves):
        cv.ellipse(lx + 1, ly + 1, r * 0.75, r * 0.6, P["leaf2"])
        cv.ellipse(lx, ly, r * 0.7, r * 0.55, P["leaf"] if i % 2 else shade(P["leaf"], 0.12))
    block(cv, x + 2, y, 10, 3, 8, shade(P["pot"], 0.1), P["pot"], shade(P["pot"], -0.55))


def window(cv: Canvas, x, y, w, h, P, curtains=True, skyline=False):
    cv.rect(x - 3, y - 3, w + 6, h + 6, P["frame"])
    cv.rect(x, y, w, h, P["glass"])
    cv.rect(x, y, w, h // 3, shade(P["glass"], -0.15))
    for sx, sy in ((5, 4), (w - 9, 7), (w // 2, 3), (12, h // 2), (w - 18, h // 2 + 3)):
        if 0 < sx < w and 0 < sy < h:
            cv.set(x + sx, y + sy, P["star"])
    cv.ellipse(x + w - 9, y + 9, 4, 4, P["moon"])
    if skyline:
        heights = [10, 18, 13, 24, 15, 9, 20, 26, 12, 17, 22, 11, 16, 25, 14, 19]
        bx = x
        for i, bh in enumerate(heights * 4):
            bw = 8 + (i * 5) % 7
            if bx >= x + w:
                break
            ww = min(bw, x + w - bx)
            cv.rect(bx, y + h - bh, ww, bh, hexc("#0c0f20"))
            for wy in range(y + h - bh + 3, y + h - 2, 4):
                for wx in range(bx + 2, bx + ww - 1, 3):
                    if (wx * 7 + wy * 3 + i) % 5 == 0:
                        cv.set(wx, wy, hexc("#f2c46b"))
            bx += bw
    cv.vline(x + w // 2, y, h, P["frame"])
    cv.hline(x, y + h // 2, w, P["frame"])
    cv.hline(x - 3, y + h + 3, w + 6, shade(P["frame"], 0.25))  # sill
    if curtains:
        for cx in (x - 8, x + w + 2):
            cv.rect(cx, y - 5, 6, h + 10, P["curtain"])
            cv.vline(cx + 1, y - 5, h + 10, shade(P["curtain"], 0.15))
            cv.vline(cx + 4, y - 5, h + 10, shade(P["curtain"], -0.2))


def bookshelf(cv: Canvas, x, y, w, h, P):
    block(cv, x, y, w, 4, h, P["wood_top"], P["wood_front"], P["wood_rim"], P["shadow"])
    rows = (h - 4) // 13
    for r in range(rows):
        sy = y + 6 + r * 13
        cv.rect(x + 2, sy, w - 4, 10, shade(P["wood_front"], -0.35))
        bx = x + 3
        i = r * 3
        while bx < x + w - 4:
            bw = 2 + (i * 7) % 3
            bh = 7 + (i * 5) % 3
            c = P[("book1", "book2", "book3", "book4")[i % 4]]
            cv.rect(bx, sy + 10 - bh, bw, bh, c)
            cv.vline(bx, sy + 10 - bh, bh, shade(c, 0.2))
            bx += bw + 1
            i += 1
        cv.hline(x + 1, sy + 10, w - 2, P["wood_top"])


def desk(cv: Canvas, x, y, w, P, monitor=True, lamp=False, mug=True, glow=None):
    """Desk against the back wall: top face 14 px deep, front 12 px high. Returns the monitor screen rect."""

    block(cv, x, y, w, 14, 12, P["wood_top"], P["wood_front"], P["wood_rim"], P["shadow"])
    cv.rect(x + 3, y + 16, w - 6, 2, shade(P["wood_front"], -0.25))  # drawer gap
    screen = None
    if monitor:
        mx = x + w // 2 - 14
        block(cv, mx + 12, y + 4, 4, 2, 4, P["monitor"], shade(P["monitor"], -0.2), shade(P["monitor"], -0.6))
        cv.rect(mx, y - 18, 28, 20, P["monitor"])
        cv.rect(mx - 1, y - 19, 30, 1, shade(P["monitor"], -0.6))
        cv.rect(mx + 2, y - 16, 24, 15, P["screen"])
        screen = (mx + 2, y - 16, 24, 15)
        if glow is not None:
            cv.rect(mx + 2, y - 16, 24, 15, mix(P["screen"], glow, 0.18))
        cv.rect(mx + 4, y + 7, 16, 3, shade(P["monitor"], 0.25))  # keyboard
        cv.hline(mx + 5, y + 8, 14, shade(P["monitor"], 0.45))
    if lamp:
        lx = x + w - 12
        cv.rect(lx + 3, y + 2, 5, 2, shade(P["metal"], -0.2))
        cv.vline(lx + 5, y - 12, 14, P["metal_rim"])
        cv.rect(lx, y - 15, 9, 4, P["warm"])
        cv.hline(lx, y - 16, 9, shade(P["warm"], -0.45))
    if mug:
        cx = x + 6
        cv.rect(cx, y + 3, 4, 5, hexc("#efe6d6"))
        cv.set(cx + 4, y + 5, hexc("#efe6d6"))
        cv.hline(cx, y + 3, 4, hexc("#6b4a33"))
    return screen


def chair(cv: Canvas, x, y, P):
    cv.tint_ellipse(x + 7, y + 13, 8, 2, P["shadow"], 0.3)
    block(cv, x, y + 4, 14, 6, 3, P["chair"], shade(P["chair"], -0.25), shade(P["chair"], -0.6))
    cv.rect(x + 1, y - 6, 12, 8, shade(P["chair"], 0.08))
    cv.hline(x + 1, y - 7, 12, shade(P["chair"], -0.6))
    cv.vline(x + 6, y + 13, 2, P["metal_rim"])


def rug(cv: Canvas, x, y, w, h, P):
    cv.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, P["rug_edge"])
    cv.ellipse(x + w / 2, y + h / 2, w / 2 - 2, h / 2 - 2, P["rug"])
    cv.ellipse(x + w / 2, y + h / 2, w / 2 - 8, h / 2 - 5, P["rug2"])
    cv.ellipse(x + w / 2, y + h / 2, w / 2 - 12, h / 2 - 8, P["rug"])


def sofa(cv: Canvas, x, y, w, P):
    """A low sofa seen from the front, its back against nothing (the lounge faces the room)."""

    cv.tint_rect(x + 3, y + 22, w, 3, P["shadow"], 0.35)
    block(cv, x, y, w, 8, 6, shade(P["fabric"], 0.1), P["fabric_front"], P["fabric_rim"])  # backrest
    block(cv, x, y + 10, w, 8, 6, P["fabric"], P["fabric_front"], P["fabric_rim"])  # seat
    for ax in (x - 2, x + w - 5):
        block(cv, ax, y + 6, 7, 6, 11, shade(P["fabric"], 0.05), P["fabric_front"], P["fabric_rim"])
    cv.rect(x + 8, y + 7, 10, 5, shade(P["card"], -0.05))  # cushion
    cv.hline(x + 8, y + 7, 10, P["card"])


def status_board(cv: Canvas, x, y, w, h, P, state, lab=False):
    """Task cards pinned to a board. Each card: the task ID and a glyph; colour is never the only cue."""

    cv.rect(x - 2, y - 2, w + 4, h + 4, P["cork_frame"])
    cv.rect(x, y, w, h, P["cork"])
    if not lab:
        for i in range(0, w, 7):
            cv.set(x + i + (i // 7) % 3, y + 3 + (i * 3) % (h - 6), shade(P["cork"], -0.12))
    title_c = shade(P["card"], -0.1) if not lab else hexc("#9fb3c2")
    text(cv, x + 3, y + 3, "TASKS", title_c if lab else shade(P["cork_frame"], -0.1))
    tasks = state.get("tasks", [])
    glyph = {"verified": ("ok", GREEN), "failed": ("fail", RED), "ready": ("dot", AMBER),
             "waiting": ("wait", GREY), "recheck": ("attention", AMBER)}
    if not tasks:
        text(cv, x + 3, y + 14, "NO PLAN", GREY if lab else shade(P["cork_frame"], -0.1))
    for i, (tid, st) in enumerate(tasks):
        cx, cy = x + 3 + i * 25, y + 11
        cv.rect(cx + 1, cy + 1, 22, 17, shade(P["cork"], -0.3))
        cv.rect(cx, cy, 22, 17, P["card"])
        stale = state.get("stale_tasks") and tid in state["stale_tasks"]
        kind, c = glyph.get("recheck" if stale else st, ("unknown", AMBER))
        if lab:
            cv.hline(cx, cy, 22, c)
        else:
            cv.set(cx + 10, cy - 1, RED if kind == "fail" else shade(P["metal"], 0.2))  # pin
            cv.set(cx + 11, cy - 1, RED if kind == "fail" else shade(P["metal"], 0.2))
        text(cv, cx + 3, cy + 3, tid, hexc("#3a2f28") if not lab else hexc("#dfe8ef"))
        checker_icon(cv, cx + 9, cy + 10, kind, shade(c, -0.25) if not lab else c)
    flag = state.get("attention")
    if flag:
        fx = x + w - 12
        cv.rect(fx, y + 2, 9, 8, AMBER if flag != "end_unknown" else AMBER)
        checker_icon(cv, fx + 2, y + 3, "unknown" if flag == "end_unknown" else "attention", hexc("#3a2614"))
    if state.get("approved"):
        sx, sy = x + w - 26, y + h - 10
        cv.rect(sx, sy, 23, 8, GREEN)
        text(cv, sx + 2, sy + 2, "APPR", hexc("#123a22"))


def verification_station(cv: Canvas, x, y, P, state, style="A"):
    """The test rig: an environment object, never a mascot. Screen text + one lit lamp + glyph."""

    result = state.get("station", "idle")  # pass, partial, fail, unknown, idle
    lamp_c = {"pass": GREEN, "partial": GREEN, "fail": RED, "unknown": AMBER}.get(result)
    label = state.get("station_label") or {"pass": "PASS", "partial": "1/2", "fail": "FAIL", "unknown": "?",
                                           "idle": "--"}[result]
    icon = {"pass": "ok", "partial": "ok", "fail": "fail", "unknown": "unknown"}.get(result)
    if style == "B":  # glass chamber on a pedestal, ring light
        cv.tint_ellipse(x + 20, y + 44, 22, 4, P["shadow"], 0.3)
        block(cv, x, y + 26, 40, 8, 10, P["metal"], P["metal_front"], P["metal_rim"])
        cv.ellipse(x + 20, y + 14, 15, 15, mix(P["glass"], hexc("#ffffff"), 0.25))
        cv.ellipse(x + 20, y + 14, 13, 13, P["glass"])
        cv.ellipse(x + 15, y + 8, 3, 4, hexc("#ffffff"))
        ring = lamp_c or GREY
        cv.rect(x + 4, y + 28, 32, 3, ring)
        cv.rect(x + 12, y + 8, 16, 12, P["screen"])
        if icon:
            checker_icon(cv, x + 17, y + 11, icon, ring)
        else:
            text(cv, x + 16, y + 12, "--", GREY)
        text(cv, x + 20 - text_width(label) // 2, y + 37, label, P["metal_rim"])
        return
    if style == "C":  # oscilloscope cart
        cv.tint_ellipse(x + 20, y + 46, 22, 4, P["shadow"], 0.4)
        block(cv, x, y + 20, 40, 8, 16, P["metal"], P["metal_front"], P["metal_rim"])
        cv.vline(x + 3, y + 45, 2, P["metal_rim"])
        cv.vline(x + 36, y + 45, 2, P["metal_rim"])
        block(cv, x + 3, y, 34, 4, 16, shade(P["metal"], 0.1), P["metal_front"], P["metal_rim"])
        cv.rect(x + 6, y + 6, 20, 12, P["screen"])
        tc = lamp_c or GREY
        for i in range(20):
            yy = y + 12 + (0 if result in ("idle",) else int(3 * ((i * 3) % 7 - 3) / 3) if result == "fail"
                           else (1 if i % 6 < 3 else -1))
            cv.set(x + 6 + i, yy, tc)
        cv.rect(x + 29, y + 7, 5, 5, lamp_c or shade(P["metal_rim"], 0.2))
        if icon:
            checker_icon(cv, x + 29, y + 7, icon, hexc("#1b1b1b"))
        text(cv, x + 6, y + 30, label, lamp_c or GREY)
        return
    # A: wooden test cabinet with a lamp column and a little printer
    block(cv, x, y + 14, 44, 10, 22, P["wood_top"], P["wood_front"], P["wood_rim"], P["shadow"])
    cv.rect(x + 4, y + 27, 36, 2, shade(P["wood_front"], -0.25))
    block(cv, x + 6, y - 4, 24, 4, 16, P["metal"], P["metal_front"], P["metal_rim"])
    cv.rect(x + 8, y + 1, 20, 9, P["screen"])
    text(cv, x + 18 - text_width(label) // 2, y + 3, label, lamp_c or GREY)
    cv.rect(x + 33, y - 10, 7, 24, P["metal_rim"])  # lamp column: pass / attention / fail
    for i, (c, on) in enumerate(((GREEN, result in ("pass", "partial")), (AMBER, result == "unknown"),
                                 (RED, result == "fail"))):
        cv.rect(x + 34, y - 9 + i * 8, 5, 6, c if on else shade(c, -0.65))
    if icon:
        checker_icon(cv, x + 9, y + 31, icon, lamp_c)
    text(cv, x + 16, y + 32, "TEST", shade(P["wood_front"], -0.45))


def link_device(cv: Canvas, x, y, P, health):
    """Runtime connection only (not task results): LED + glyph + word."""

    ok = health == "connected"
    c = GREEN if ok else (RED if health in ("disconnected", "cli_missing") else AMBER)
    cv.rect(x, y, 22, 9, P["metal_rim"])
    cv.rect(x + 1, y + 1, 20, 7, shade(P["metal_front"], -0.1))
    cv.rect(x + 2, y + 2, 3, 3, c)
    text(cv, x + 7, y + 2, "ON" if ok else "OFF", c)
    cv.vline(x + 17, y - 6, 6, P["metal_rim"])
    cv.set(x + 17, y - 7, c)


def bubble(cv: Canvas, x, y, kind, P):
    """A small pixel thought mark above a mascot (presentation only)."""

    cv.rect(x, y, 9, 8, hexc("#fbf6ec"))
    cv.rect(x + 1, y - 1, 7, 1, hexc("#fbf6ec"))
    cv.rect(x + 1, y + 8, 7, 1, hexc("#fbf6ec"))
    cv.set(x + 2, y + 10, hexc("#fbf6ec"))
    checker_icon(cv, x + 2, y + 1, kind, hexc("#5a4632"))


# --- rooms ----------------------------------------------------------------------------------


def _floor_planks(cv: Canvas, P, plank_h=8, plank_w=48):
    for y in range(FLOOR_TOP, FLOOR_BOTTOM):
        row = (y - FLOOR_TOP) // plank_h
        c = P["floor"] if row % 2 == 0 else P["floor2"]
        cv.hline(0, y, W, c)
        if (y - FLOOR_TOP) % plank_h == plank_h - 1:
            cv.hline(0, y, W, P["seam"])
    for row in range((FLOOR_BOTTOM - FLOOR_TOP) // plank_h + 1):
        off = (row * 29) % plank_w
        for x in range(off, W, plank_w):
            cv.vline(x, FLOOR_TOP + row * plank_h, plank_h - 1, P["seam"])


def _floor_tiles(cv: Canvas, P, tw=32, th=16):
    for y in range(FLOOR_TOP, FLOOR_BOTTOM):
        r = (y - FLOOR_TOP) // th
        for x in range(W):
            c = P["floor"] if (x // tw + r) % 2 == 0 else P["floor2"]
            if (y - FLOOR_TOP) % th == 0 or x % tw == 0:
                c = P["seam"]
            cv.px[y][x] = c


def floor_depth(cv: Canvas, P):
    """Contact darkening where floor meets the back wall, and a darker front band: depth without gradients."""

    cv.tint_rect(0, FLOOR_TOP, W, 3, P["shadow"], 0.22)
    cv.tint_rect(0, FLOOR_TOP + 3, W, 2, P["shadow"], 0.10)
    cv.tint_rect(0, FLOOR_BOTTOM - 22, W, 12, P["shadow"], 0.05)
    cv.tint_rect(0, FLOOR_BOTTOM - 10, W, 10, P["shadow"], 0.10)


def _wall(cv: Canvas, P, panel=True):
    cv.rect(0, 0, W, 6, P["beam"])
    cv.rect(0, 6, W, FLOOR_TOP - 6, P["wall"])
    if panel:
        cv.rect(0, 56, W, FLOOR_TOP - 56, P["wall_lo"])
        cv.hline(0, 55, W, P["rail"])
        cv.hline(0, 54, W, shade(P["rail"], 0.25))
        for x in range(12, W, 32):
            cv.vline(x, 59, FLOOR_TOP - 64, shade(P["wall_lo"], -0.12))
    cv.rect(0, FLOOR_TOP - 5, W, 5, P["base"])
    cv.hline(0, FLOOR_TOP - 5, W, shade(P["base"], 0.3))


def _front(cv: Canvas, P):
    cv.rect(0, FLOOR_BOTTOM, W, H - FLOOR_BOTTOM, P["lip"])
    cv.hline(0, FLOOR_BOTTOM, W, shade(P["lip"], 0.3))
    for x in (0, W - 6):  # side wall edges frame the room
        cv.rect(x, 6, 6, FLOOR_BOTTOM - 6, P["lip"])


def default_poses(concept: str, state: dict) -> list[dict]:
    """Where the mascots are for a state. Positions are presentation; expressions react, never 'work'."""

    mood = state.get("mood", "idle")
    st = state.get("anchors", {})
    codex_home, claude_home, station, lounge, board = st["codex"], st["claude"], st["station"], st["lounge"], st["board"]
    rest = st.get("rest", lounge)
    if mood in ("failed",):
        return [dict(name="codex", at=(station[0] - 26, station[1]), expr="attention", flip=False),
                dict(name="claude", at=(station[0] + 30, station[1] + 6), expr="attention", flip=True)]
    if mood == "end_unknown" and state.get("unknown_at") == "board":  # recorded Worker run: look at the board
        return [dict(name="codex", at=(board[0], board[1]), expr="unknown", flip=False, mark="unknown"),
                dict(name="claude", at=(board[0] + 34, board[1] + 4), expr="unknown", flip=True)]
    if mood == "end_unknown":
        return [dict(name="codex", at=(station[0] - 26, station[1]), expr="unknown", flip=False, mark="unknown"),
                dict(name="claude", at=(station[0] + 30, station[1] + 6), expr="unknown", flip=True)]
    if mood == "review":
        return [dict(name="codex", at=(board[0], board[1]), expr="attention", flip=False, mark="attention"),
                dict(name="claude", at=(lounge[0], lounge[1]), expr="neutral", flip=False)]
    if mood == "approved":
        return [dict(name="codex", at=(station[0] - 26, station[1]), expr="happy", flip=False),
                dict(name="claude", at=(station[0] + 30, station[1] + 4), expr="happy", flip=True)]
    if mood == "idle":
        return [dict(name="codex", at=(rest[0], rest[1]), expr="rest", flip=False),
                dict(name="claude", at=(rest[0] + 34, rest[1] + 2), expr="rest", flip=True)]
    # ready / passed / disconnected and others: calm, at their own stations or resting
    return [dict(name="codex", at=codex_home, expr="neutral", flip=False),
            dict(name="claude", at=(lounge[0], lounge[1]), expr="blink", flip=False)]


def window_light(cv: Canvas, x, w, P, depth=40, t=0.10):
    """Hard-edged light patch thrown on the floor by a back-wall window (skews right: key light from the left)."""

    for i in range(depth):
        y = FLOOR_TOP + i
        off = int(i * 0.6) + 6
        for xx in range(x + off, x + w + off):
            if (xx - x - off) in (w // 2, w // 2 + 1):
                continue  # mullion shadow
            cv.tint(xx, y, P["cool"], t * (1.0 if i < depth * 0.6 else 0.6))


def round_table(cv: Canvas, cx, cy, P, items=True):
    cv.tint_ellipse(cx + 3, cy + 12, 26, 5, P["shadow"], 0.3)
    cv.rect(cx - 2, cy + 2, 5, 10, P["wood_front"])
    cv.ellipse(cx, cy + 1, 25, 8, P["wood_rim"])
    cv.ellipse(cx, cy + 2, 24, 7, P["wood_front"])
    cv.ellipse(cx, cy, 24, 7, P["wood_top"])
    cv.ellipse(cx - 6, cy - 2, 10, 3, shade(P["wood_top"], 0.12))
    if items:
        cv.rect(cx - 12, cy - 3, 9, 6, P["card"])  # papers
        cv.hline(cx - 11, cy - 1, 6, shade(P["card"], -0.25))
        cv.rect(cx + 6, cy - 5, 6, 5, hexc("#e8dcc8"))  # teapot
        cv.set(cx + 12, cy - 3, hexc("#e8dcc8"))
        cv.rect(cx + 7, cy - 6, 3, 1, P["wood_rim"])


def stool(cv: Canvas, x, y, P):
    cv.tint_ellipse(x + 5, y + 9, 6, 2, P["shadow"], 0.3)
    cv.vline(x + 2, y + 3, 6, P["wood_rim"])
    cv.vline(x + 8, y + 3, 6, P["wood_rim"])
    cv.ellipse(x + 5, y + 2, 6, 3, P["fabric_front"])
    cv.ellipse(x + 5, y + 1, 6, 3, P["fabric"])


def armchair(cv: Canvas, x, y, P):
    cv.tint_rect(x + 3, y + 22, 26, 3, P["shadow"], 0.35)
    block(cv, x + 3, y, 22, 8, 6, shade(P["fabric"], 0.1), P["fabric_front"], P["fabric_rim"])
    block(cv, x + 3, y + 10, 22, 6, 6, P["fabric"], P["fabric_front"], P["fabric_rim"])
    for ax in (x, x + 22):
        block(cv, ax, y + 6, 6, 5, 11, shade(P["fabric"], 0.05), P["fabric_front"], P["fabric_rim"])


def floor_lamp(cv: Canvas, x, y, P):
    cv.tint_ellipse(x + 2, y + 2, 5, 1.5, P["shadow"], 0.35)
    cv.rect(x - 3, y, 7, 2, P["metal_rim"])
    cv.vline(x, y - 30, 30, P["metal_rim"])
    cv.rect(x - 5, y - 37, 11, 7, P["warm"])
    cv.hline(x - 5, y - 38, 11, shade(P["warm"], -0.45))
    cv.hline(x - 5, y - 31, 11, shade(P["warm"], -0.2))


def crate(cv: Canvas, x, y, w, h, P):
    block(cv, x, y, w, 6, h, P["wood_top"], P["wood_front"], P["wood_rim"], P["shadow"])
    cv.hline(x, y + 6 + h // 2, w, shade(P["wood_front"], -0.25))


def render(concept: str, state: dict, *, poses=None, select: str | None = None) -> tuple[Canvas, dict]:
    P = pal(concept)
    cv = Canvas(W, H)
    items: list[tuple[int, callable]] = []
    rects: dict[str, tuple[int, int, int, int]] = {}
    lights: list[tuple] = []
    health = state.get("health", "connected")

    if concept == "A":
        _wall(cv, P)
        _floor_planks(cv, P)
        floor_depth(cv, P)
        window(cv, 22, 14, 58, 30, P)
        window(cv, 388, 14, 30, 30, P)
        window_light(cv, 22, 58, P)
        window_light(cv, 388, 30, P, depth=30)
        status_board(cv, 196, 10, 88, 36, P, state)
        rects["board"] = (194, 8, 92, 40)
        cv.rect(110, 18, 24, 16, shade(P["wall"], -0.25))  # Codex's poster: >_
        cv.rect(111, 19, 22, 14, P["screen"])
        text(cv, 114, 24, ">_", CYAN)
        cv.rect(332, 26, 34, 3, P["wood_front"])  # Claude's shelf: a jar and a little sun
        cv.rect(336, 17, 7, 9, P["fabric"])
        cv.rect(347, 19, 6, 7, hexc("#e9dcc6"))
        cv.ellipse(359, 21, 3, 3, P["book3"])
        link_device(cv, 150, 44, P, health)
        rects["link"] = (148, 36, 26, 18)
        anchors = dict(codex=(100, 142), claude=(356, 142), station=(240, 142), lounge=(372, 224),
                       board=(184, 132), rest=(352, 222))
        items.append((114, lambda: desk(cv, 52, 88, 84, P, glow=CYAN)))
        rects["codex_station"] = (50, 66, 88, 50)
        items.append((114, lambda: desk(cv, 316, 88, 84, P, monitor=False, lamp=True)))
        items.append((102, lambda: (cv.rect(334, 86, 8, 8, P["book2"]), cv.rect(342, 87, 7, 7, P["book3"]),
                                    cv.rect(350, 85, 6, 9, P["book1"]))))
        rects["claude_station"] = (314, 66, 88, 50)
        items.append((118, lambda: verification_station(cv, 218, 72, P, state)))
        rects["verification"] = (216, 58, 48, 62)
        items.append((112, lambda: bookshelf(cv, 428, 58, 42, 52, P)))
        items.append((130, lambda: chair(cv, 86, 118, P)))
        items.append((130, lambda: chair(cv, 350, 118, P)))
        items.append((110, lambda: plant(cv, 10, 96, P, big=True)))
        items.append((108, lambda: plant(cv, 404, 98, P)))
        # middle row: shared table, reading nook
        items.append((180, lambda: round_table(cv, 240, 172, P)))
        rects["shared"] = (214, 162, 54, 22)
        items.append((178, lambda: stool(cv, 202, 170, P)))
        items.append((186, lambda: stool(cv, 270, 174, P)))
        items.append((190, lambda: armchair(cv, 30, 168, P)))
        items.append((192, lambda: floor_lamp(cv, 20, 192, P)))
        lights.append(("warm", 22, 186, 26, 12, 0.22))
        items.append((200, lambda: block(cv, 64, 184, 16, 6, 8, P["wood_top"], P["wood_front"], P["wood_rim"],
                                         P["shadow"])))
        # front row: lounge
        items.append((196, lambda: rug(cv, 300, 200, 156, 46, P)))
        items.append((212, lambda: sofa(cv, 330, 190, 96, P)))
        items.append((236, lambda: plant(cv, 280, 222, P, big=True)))
        items.append((246, lambda: (block(cv, 110, 230, 40, 8, 8, P["wood_top"], P["wood_front"], P["wood_rim"],
                                          P["shadow"]),
                                    cv.ellipse(124, 233, 8, 3, hexc("#2b2b2b")), cv.set(124, 233, P["book3"]))))
        items.append((248, lambda: plant(cv, 456, 230, P)))
        lights += [("warm", 392, 104, 18, 10, 0.25), ("cool", 94, 92, 16, 8, 0.15)]
    elif concept == "B":
        _wall(cv, P, panel=False)
        _floor_tiles(cv, P)
        floor_depth(cv, P)
        cv.rect(0, 9, W, 2, hexc("#ffffff"))
        window(cv, 24, 18, 128, 24, P, curtains=False)
        window(cv, 328, 18, 128, 24, P, curtains=False)
        status_board(cv, 180, 12, 120, 40, P, state, lab=True)
        rects["board"] = (178, 10, 124, 44)
        link_device(cv, 444, 58, P, health)
        rects["link"] = (442, 50, 26, 18)
        anchors = dict(codex=(88, 144), claude=(392, 144), station=(240, 196), lounge=(404, 236),
                       board=(226, 132), rest=(380, 236))
        for px, who in ((36, "codex"), (340, "claude")):
            items.append((114, lambda px=px: desk(cv, px, 88, 104, P, glow=CYAN, mug=False)))
            items.append((96, lambda px=px, who=who: (cv.rect(px + 2, 82, 100, 2, hexc("#3fb5a8")),
                                                     text(cv, px + 4, 74, who.upper(), P["metal_rim"]))))
            rects[f"{who}_station"] = (px - 2, 66, 108, 50)
        for dx in (158, 262):  # empty docks: where a third or fourth agent pod would go
            items.append((100, lambda dx=dx: (cv.tint_rect(dx, 90, 60, 22, P["seam"], 0.7),
                                              text(cv, dx + 22, 98, "DOCK", P["base"]))))
        items.append((176, lambda: verification_station(cv, 220, 134, P, state, style="B")))
        rects["verification"] = (216, 126, 48, 52)
        items.append((196, lambda: block(cv, 40, 176, 110, 12, 8, P["wood_top"], P["wood_front"], P["wood_rim"],
                                         P["shadow"])))
        items.append((190, lambda: (cv.rect(52, 179, 14, 8, P["card"]), cv.rect(100, 178, 18, 9, P["card"]),
                                    text(cv, 104, 180, "MAP", hexc("#9fb3c2")))))
        rects["shared"] = (38, 174, 114, 24)
        for sx in (56, 92, 128):
            items.append((206, lambda sx=sx: stool(cv, sx, 200, P)))
        items.append((200, lambda: rug(cv, 340, 212, 128, 40, P)))
        items.append((226, lambda: block(cv, 352, 214, 104, 8, 8, P["fabric"], P["fabric_front"], P["fabric_rim"],
                                         P["shadow"])))
        items.append((110, lambda: plant(cv, 10, 96, P)))
        items.append((110, lambda: plant(cv, 456, 96, P)))
        items.append((246, lambda: plant(cv, 20, 230, P, big=True)))
        items.append((246, lambda: plant(cv, 300, 232, P)))
    else:  # C
        _wall(cv, P, panel=False)
        _floor_planks(cv, P, plank_h=10, plank_w=64)
        floor_depth(cv, P)
        window(cv, 120, 10, 240, 48, P, curtains=False, skyline=True)
        cv.rect(14, 12, 80, 52, shade(P["wall"], -0.15))  # pegboard with tools
        for yy in range(15, 62, 4):
            for xx in range(17, 92, 4):
                cv.set(xx, yy, shade(P["wall"], -0.45))
        cv.rect(24, 20, 3, 18, P["metal"])
        cv.rect(34, 18, 12, 3, P["metal"])
        cv.rect(56, 22, 4, 16, P["book3"])
        cv.ellipse(76, 30, 6, 6, P["metal_rim"])
        cv.ellipse(76, 30, 4, 4, P["metal"])
        status_board(cv, 392, 14, 72, 38, P, state)
        rects["board"] = (390, 12, 76, 42)
        cv.rect(370, 24, 16, 9, hexc("#2a1530"))  # one small neon sign
        text(cv, 373, 26, ">_", hexc("#ff6fa8"))
        lights.append(("neon", 378, 28, 14, 9, 0.25))
        link_device(cv, 100, 66, P, health)
        rects["link"] = (98, 58, 26, 18)
        anchors = dict(codex=(56, 146), claude=(166, 146), station=(320, 160), lounge=(396, 240),
                       board=(410, 132), rest=(372, 238))
        items.append((114, lambda: desk(cv, 14, 88, 200, P, monitor=False, mug=True)))
        items.append((100, lambda: (desk_monitor(cv, 46, 88, P), desk_monitor(cv, 150, 88, P))))
        rects["codex_station"] = (12, 66, 100, 50)
        rects["claude_station"] = (112, 66, 102, 50)
        for lx in (32, 136):
            items.append((98, lambda lx=lx: (cv.vline(lx, 68, 20, P["metal_rim"]), cv.rect(lx - 4, 65, 9, 4, P["warm"]))))
            lights.append(("warm", lx + 6, 100, 22, 9, 0.32))
        items.append((136, lambda: verification_station(cv, 300, 92, P, state, style="C")))
        rects["verification"] = (298, 88, 44, 50)
        items.append((180, lambda: block(cv, 186, 160, 70, 12, 12, P["wood_top"], P["wood_front"], P["wood_rim"],
                                         P["shadow"])))
        items.append((172, lambda: (block(cv, 196, 154, 18, 6, 6, RED, shade(RED, -0.3), shade(RED, -0.65)),
                                    cv.rect(226, 160, 16, 4, P["card"]))))
        rects["shared"] = (184, 150, 74, 36)
        items.append((198, lambda: stool(cv, 210, 190, P)))
        items.append((196, lambda: crate(cv, 26, 176, 26, 14, P)))
        items.append((208, lambda: crate(cv, 46, 190, 22, 12, P)))
        items.append((220, lambda: plant(cv, 16, 206, P, big=True)))
        items.append((206, lambda: rug(cv, 324, 208, 140, 46, P)))
        items.append((222, lambda: sofa(cv, 350, 198, 84, P)))
        items.append((238, lambda: floor_lamp(cv, 456, 238, P)))
        lights += [("warm", 452, 226, 30, 14, 0.25), ("cool", 64, 96, 14, 7, 0.15), ("cool", 168, 96, 14, 7, 0.15)]

    state = dict(state, anchors=anchors)
    poses = poses if poses is not None else default_poses(concept, state)
    for p in poses:
        def draw(p=p):
            x, foot = p["at"]
            sp = mascot(p["name"], 32, p.get("expr", "neutral"), view=p.get("view", "front"),
                        breath=p.get("breath", 0))
            contact_shadow(cv, x, foot, 20 if p["name"] == "codex" else 24, P)
            if select == p["name"]:
                for i in range(-13, 14, 2):  # dashed ground ring: quiet selection
                    yy = int(round(3.4 * (1 - (i / 13.5) ** 2) ** 0.5))
                    cv.set(x + i, foot + yy, hexc("#fff3c8"))
                    cv.set(x + i, foot - yy, hexc("#fff3c8"))
            cv.blit(sp, x - 16, foot - 31, flip=p.get("flip", False))
            if p.get("mark"):
                bubble(cv, x + 8, foot - 40, p["mark"], P)
            rects[p["name"]] = (x - 13, foot - 30, 26, 31)
        items.append((p["at"][1], draw))

    for _, fn in sorted(items, key=lambda it: it[0]):
        fn()
    _front(cv, P)
    if P["ambient"] is not None:  # night room: lift nothing to black, keep values readable
        for y in range(H):
            for x in range(W):
                c = cv.px[y][x]
                if c is not None:
                    cv.px[y][x] = mix(c, P["ambient"], 0.06)
    for kind, cx, cy, rx, ry, strength in lights:
        color = {"warm": P["warm"], "cool": P["cool"], "neon": hexc("#ff6fa8")}[kind]
        cv.glow(cx, cy, rx, ry, color, strength)
    if state.get("station") in ("fail", "unknown", "pass", "partial") and "verification" in rects:
        vx, vy, vw, vh = rects["verification"]
        c = {"fail": RED, "unknown": AMBER}.get(state["station"], GREEN)
        cv.glow(vx + vw // 2, vy + vh // 3, 14, 10, c, 0.10, bands=2)  # small: the station, not the room
    if state.get("health") not in (None, "connected"):
        cv.tint_rect(0, 0, W, H, hexc("#4a5060"), 0.18)  # the whole scene a little desaturated: observation lost
    return cv, rects


def desk_monitor(cv: Canvas, x, y, P):
    cv.rect(x, y - 18, 28, 20, P["monitor"])
    cv.rect(x - 1, y - 19, 30, 1, shade(P["monitor"], -0.6))
    cv.rect(x + 2, y - 16, 24, 15, mix(P["screen"], CYAN, 0.12))
    text(cv, x + 5, y - 11, ">_", mix(P["screen"], CYAN, 0.45))  # idle prompt only: no code being written
    cv.rect(x + 12, y + 2, 4, 3, P["monitor"])
