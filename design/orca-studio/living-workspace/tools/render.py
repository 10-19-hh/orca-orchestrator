"""Renders the Living Workspace layout-study mockups from the layout fixtures (design tool, stdlib only).

python design/orca-studio/living-workspace/tools/render.py

Every image says LAYOUT MOCKUP - NOT FINAL ART. Pets are placed from synthetic
topology@1 DEMO documents through ``layout.allocate``; nothing here reads a live
Orca workspace.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

import iso as I
import layout as L
from iso import P, Canvas, Iso, hexc, shade

OUT = Path(__file__).resolve().parents[1] / "mockups"
MARGIN = 18
CAPTION_H = 14

DRAW = {
    "furniture.pantry_counter": I.pantry_counter, "furniture.coffee_machine": I.coffee_machine,
    "furniture.cabinet_tall": I.cabinet_tall, "furniture.meeting_table": I.meeting_table, "furniture.stool": I.stool,
    "furniture.sofa": I.sofa, "furniture.coffee_table": I.coffee_table, "furniture.plant_tall": I.plant_tall,
    "furniture.floor_lamp": I.floor_lamp, "furniture.bookshelf": I.bookshelf, "furniture.desk_divider": I.divider,
    "furniture.printer_cart": I.printer_cart, "furniture.credenza": I.credenza,
}
DECALS = {"furniture.rug": I.rug, "environment.doormat": I.doormat}
SEATED = {"work", "attention", "finishing", "unobservable"}
LIT = {"work", "attention", "finishing", "work_done"}


def canvas_for(room: L.Room) -> tuple[Canvas, Iso]:
    w, d = room.width, room.depth
    width = int((w + d + 2 * L.CELL) * 16) + 2 * MARGIN
    height = (w + d) * 8 + I.WALL_H + 8 + 2 * MARGIN + CAPTION_H + 8
    cv = Canvas(width, height, P["canvas"])
    return cv, Iso(cv, int(d * 16 + MARGIN + 6), I.WALL_H + MARGIN + 6)


def pets_from(room: L.Room, placements: dict[str, dict]) -> list[dict]:
    """Where and how each pet is drawn, from its intent and its seat or interaction point."""

    stations = {s.slot: s for s in room.stations}
    points = {p.point_id: p for p in room.points}
    pets = []
    for sid, pl in placements.items():
        intent, prov = pl["intent"], pl["provider"] if pl["provider"] in ("claude", "codex") else "codex"
        st = stations.get(pl["station"]) if pl.get("station") else None
        pet = {"session": sid, "provider": prov, "intent": intent, "dim": intent == "unobservable", "z": 0,
               "pose": "stand", "view": "front", "bubble": None, "prop": None}
        if intent in SEATED and st is not None:
            pet.update(x=st.x, y=st.y, z=7, pose="sit", view="front" if st.facing == "S" else "back",
                       bubble={"attention": "attention", "finishing": "finishing", "unobservable": "unobservable"}
                       .get(intent))
        elif intent == "work_done" and st is not None:
            pet.update(x=st.x + 0.5, y=st.y + (-0.5 if st.facing == "S" else 0.5), bubble="done")
        elif intent == "exit":
            pet.update(x=1.0, y=8.625, pose="walk", view="back")
        else:
            p = points[pl["at"]]
            pet.update(x=p.pose_x if p.pose_x is not None else p.x, y=p.pose_y if p.pose_y is not None else p.y)
            if p.kind in ("coffee", "counter"):
                pet.update(view="back", prop="mug")
            elif p.kind == "sofa_sit":
                pet.update(z=8, pose="sit")
            elif p.kind == "sofa_lie":
                pet.update(z=8, pose="lie", bubble="sleep")
            elif p.kind == "meeting_seat":
                pet.update(z=7, pose="sit", view="back" if p.facing == "N" else "front")
            else:
                pet.update(view="back")
        pets.append(pet)
    return pets


def draw_room(room: L.Room, pets: list[dict], caption: str, *, reserved: bool = False,
              selected: str | None = None, cv_iso: tuple[Canvas, Iso] | None = None,
              chrome: bool = True) -> Canvas:
    cv, iso = cv_iso or canvas_for(room)
    w, d = room.width, room.depth
    I.floor(iso, w, d)
    for m in room.modules:
        if m.asset_id in DECALS:
            DECALS[m.asset_id](iso, m)
    I.sunlight_from_left(iso, 3.25, 5.25)
    I.sunlight_from_back(iso, 4.5, 6.5)
    if w >= 17:
        I.sunlight_from_back(iso, 14.25, 15.75)
    for m in room.modules:
        if m.blocks and m.h > 0 and m.asset_id not in ("furniture.office_chair",):
            iso.shadow(m.x, m.y, m.w, m.d, 0.16 if m.h < 12 else 0.22)
    if reserved:
        ax, ay = L.CLUSTER_ANCHORS["A"]
        q = iso.quad(ax - 0.25, ay - 1.25, 4.5, 4.5)
        for i in range(4):
            a, b = q[i], q[(i + 1) % 4]
            for k in range(0, 24, 2):
                t0, t1 = k / 24, (k + 1) / 24
                iso.line((a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0),
                         (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1), shade(P["floor"], -0.25))
    I.walls(iso, w, d)
    I.window_back(iso, 4.5, 6.5)
    I.window_left(iso, 3.25, 5.25)
    I.wall_shelf_back(iso, 0.5, 2.75, 31)
    I.clock_back(iso, 3.45, 46)
    I.whiteboard(iso, 7.0, 9.75)
    attention = any(p["intent"] == "attention" for p in pets)
    I.status_board(iso, 10.25, 11.5, attention)
    I.door_left(iso, 8.0, 9.25)
    I.frames_left(iso, 6.6)
    if w >= 17:
        I.window_back(iso, 14.25, 15.75)
    I.air_shaft_back(iso, 4.5, 6.5)

    stations = {s.desk: s for s in room.stations}
    chairs = {s.chair: s for s in room.stations}
    lit_desks = {s.desk for s in room.stations for p in pets
                 if s.session_id == p["session"] and p["intent"] in LIT}
    items: list[tuple[float, object]] = []
    for m in room.modules:
        if m.asset_id in DECALS:
            continue
        key = m.x + m.w / 2 + m.y + m.d / 2
        if m.asset_id == "furniture.sofa":
            key = m.x + m.y
        if m.asset_id == "furniture.desk_modular":
            items.append((key, lambda m=m: I.desk(iso, m, m.module_id in lit_desks)))
        elif m.asset_id == "furniture.office_chair":
            tucked = chairs[m.module_id].state == "spare"
            items.append((key - 0.02, lambda m=m, t=tucked: I.chair_seat(iso, m, t)))
            back_key = (m.x + 0.25 + m.y) if m.facing == "S" else (m.x + 0.25 + m.y + 0.56)
            items.append((back_key, lambda m=m, t=tucked: I.chair_back(iso, m, t)))
        elif m.asset_id == "device.verification_rack":
            items.append((key, lambda m=m: I.verification_rack(iso, m, attention)))
        elif m.asset_id in DRAW:
            items.append((key, lambda m=m: DRAW[m.asset_id](iso, m)))
    bubbles = []
    for pet in pets:
        def draw_pet(pet=pet):
            sx, sy = iso.pt(pet["x"], pet["y"], pet["z"])
            if pet["z"] == 0:
                I.foot_shadow(cv, int(sx), int(sy))
            if selected == pet["session"]:
                for k in range(-9, 10, 2):
                    cv.set(int(sx) + k, int(sy) + 2, P["bubble"])
                    cv.set(int(sx) + k + 1, int(sy) - 2 + (2 if abs(k) > 6 else 0), P["bubble"])
            spr = I.sprite(pet["provider"], pet["view"], pet["pose"])
            I.blit_sprite(cv, spr, int(sx), int(sy), dim=pet["dim"])
            if pet["prop"] == "mug":
                I.mug(cv, int(sx) + 7, int(sy) - 6)
            elif pet["prop"] == "broom":
                I.broom(cv, int(sx) + 8, int(sy))
            if pet["bubble"]:
                top = 14 if pet["provider"] == "codex" else 11
                bubbles.append((int(sx) + 4, int(sy) - top, pet["bubble"]))
        items.append((pet["x"] + pet["y"] + 0.05, draw_pet))
    for _, fn in sorted(items, key=lambda kv: kv[0]):
        fn()  # type: ignore[operator]
    for bx, by, kind in bubbles:
        I.bubble(cv, bx, by, kind)
    if chrome:
        caption_bar(cv, caption)
    return cv


def caption_bar(cv: Canvas, caption: str) -> None:
    y = cv.h - CAPTION_H
    cv.rect(0, y, cv.w, CAPTION_H, shade(P["canvas"], -0.25))
    x = I.text(cv, 6, y + 5, "LAYOUT MOCKUP - NOT FINAL ART", hexc("#E3B866"))
    I.text(cv, x + 8, y + 5, caption, hexc("#C9D1D9"))


def room_from_scene(scene: str, overrides: dict[str, str] | None = None, worktree: int = 0):
    doc = L.load_scene(scene)
    if overrides:
        for s in doc["agent_sessions"]:
            if s["session_id"] in overrides:
                s["presentation"]["intent"] = overrides[s["session_id"]]
    return L.room_for(doc, doc["worktrees"][worktree]["worktree_id"])


def save(cv: Canvas, name: str, scale: int = 3) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cv.save(OUT / f"{name}.png")
    cv.save(OUT / f"{name}@{scale}x.png", scale=scale)


# --- extra sheets ---------------------------------------------------------------------------


def crop(cv: Canvas, x: int, y: int, w: int, h: int) -> Canvas:
    out = Canvas(w, h, P["canvas"])
    for yy in range(h):
        for xx in range(w):
            c = cv.get(x + xx, y + yy)
            if c is not None:
                out.set(xx, yy, c)
    return out


def paste(dst: Canvas, src: Canvas, x: int, y: int) -> None:
    for yy in range(src.h):
        for xx in range(src.w):
            c = src.get(xx, yy)
            if c is not None:
                dst.set(x + xx, y + yy, c)


def path_cells(room: L.Room, start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[float, float]]:
    grid = L.nav_grid(room)
    cell = lambda x, y: (int(y / L.CELL), int(x / L.CELL))  # noqa: E731
    s, g = cell(*start), cell(*goal)
    prev = {s: None}
    queue = deque([s])
    while queue:
        cur = queue.popleft()
        if cur == g:
            break
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (cur[0] + dr, cur[1] + dc)
            if 0 <= n[0] < len(grid) and 0 <= n[1] < len(grid[0]) and grid[n[0]][n[1]] == "." and n not in prev:
                prev[n] = cur
                queue.append(n)
    out, cur = [], g
    while cur is not None:
        out.append(((cur[1] + 0.5) * L.CELL, (cur[0] + 0.5) * L.CELL))
        cur = prev.get(cur)
    return out[::-1]


def sequence_sheet() -> Canvas:
    """One Codex pet: ambient -> walk to desk -> sit/work -> done, stand -> back; and a confirmed exit."""

    room, placements = room_from_scene("four-sessions")
    sid = "example-session-1-2"
    st = next(s for s in room.stations if s.session_id == sid)
    others = {k: v for k, v in placements.items() if k != sid}
    sofa = next(p for p in room.points if p.point_id == "sofa-1")
    route = path_cells(room, (sofa.x, sofa.y), (st.x, st.y))
    back = path_cells(room, (st.x, st.y + 0.5), (sofa.x, sofa.y))
    exit_route = path_cells(room, (st.x, st.y + 0.5), (0.625, 8.625))
    base = {"session": sid, "provider": "codex", "dim": False, "z": 0, "prop": None}
    frames = [
        ("1 AMBIENT: SOFA", dict(base, intent="ambient", x=sofa.pose_x, y=sofa.pose_y, z=8, pose="sit",
                                 view="front", bubble=None)),
        ("2 WORK SEEN: WALK", dict(base, intent="ambient", x=route[len(route) // 2][0], y=route[len(route) // 2][1],
                                    pose="walk", view="front", bubble=None)),
        ("3 SIT + WORK", dict(base, intent="work", x=st.x, y=st.y, z=7, pose="sit", view="back", bubble=None)),
        ("4 DONE: STAND UP", dict(base, intent="work_done", x=st.x + 0.5, y=st.y + 0.5, pose="stand",
                                  view="front", bubble="done")),
        ("5 BACK TO LOUNGE", dict(base, intent="ambient", x=back[len(back) // 2][0], y=back[len(back) // 2][1],
                                  pose="walk", view="back", bubble=None)),
        ("6 END SEEN: EXIT", dict(base, intent="exit", x=exit_route[-3][0], y=exit_route[-3][1], pose="walk",
                                  view="back", bubble=None)),
    ]
    panel_w, panel_h = 232, 150
    sheet = Canvas(panel_w * 3 + 16, panel_h * 2 + 12 + CAPTION_H + 12, P["canvas"])
    for i, (label, pet) in enumerate(frames):
        lit_room, lit_pl = room_from_scene("four-sessions")
        pets = pets_from(lit_room, {k: v for k, v in lit_pl.items() if k != sid}) + [pet]
        cv = draw_room(lit_room, pets, "", chrome=False)
        _, iso = canvas_for(lit_room)
        cx, cy = iso.pt(4.0, 6.0)
        part = crop(cv, int(cx) - panel_w // 2 - 20, int(cy) - panel_h // 2 - 22, panel_w - 4, panel_h - 4)
        col, row = i % 3, i // 3
        px, py = 4 + col * (panel_w + 4), 4 + row * (panel_h + 4)
        paste(sheet, part, px, py)
        sheet.rect(px, py, 6 + I.text_w(label), 9, shade(P["canvas"], -0.3))
        I.text(sheet, px + 3, py + 2, label, hexc("#E6DCCD"))
    caption_bar(sheet, "BEHAVIOUR SEQUENCE - ONE CODEX PET (SYNTHETIC)")
    return sheet


def character_sheet() -> Canvas:
    poses = [("IDLE", "front", "stand", None, None, False), ("WALK A", "front", "walk", None, None, False),
             ("WALK B", "back", "walk", None, None, False), ("SIT", "front", "sit", None, None, False),
             ("WORK", "back", "sit", None, None, False), ("COFFEE", "front", "stand", None, "mug", False),
             ("SLEEP", "front", "lie", "sleep", None, False), ("SWEEP", "front", "stand", None, "broom", False),
             ("NEEDS YOU", "front", "sit", "attention", None, False), ("FINISHING", "back", "sit", "finishing", None,
                                                                         False),
             ("UNKNOWN", "front", "sit", "unobservable", None, True), ("ERROR", "front", "stand", "error", None, False),
             ("DONE", "front", "stand", "done", None, False)]
    cell = 40
    cv = Canvas(cell * len(poses) + 8, 2 * 46 + 22 + CAPTION_H, P["canvas"])
    for row, prov in enumerate(("claude", "codex")):
        for i, (label, view, pose, bub, prop, dim) in enumerate(poses):
            x, y = 4 + i * cell + cell // 2, 8 + row * 46 + 30
            cv.rect(4 + i * cell + 2, 8 + row * 46 + 2, cell - 4, 40, shade(P["floor"], -0.05))
            I.foot_shadow(cv, x, y, 6)
            I.blit_sprite(cv, I.sprite(prov, view, pose), x, y, dim=dim)
            if prop == "mug":
                I.mug(cv, x + 7, y - 6)
            if prop == "broom":
                I.broom(cv, x + 8, y)
            if bub:
                I.bubble(cv, x + 4, y - (14 if prov == "codex" else 11), bub)
            if row == 1:
                I.text(cv, 4 + i * cell + 3, 8 + 2 * 46 + 1, label[:9], hexc("#C9D1D9"))
    caption_bar(cv, "CHARACTER POSE STUDY - CLAUDE / CODEX 1X")
    return cv


def ui_overlay(cv: Canvas, inspector: bool) -> None:
    bar = shade(P["canvas"], -0.3)
    cv.rect(0, 0, cv.w, 13, bar)
    x = I.text(cv, 6, 4, "ORCA STUDIO", hexc("#E6DCCD"))
    x = I.text(cv, x + 8, 4, "EXAMPLE-APP / MAIN", hexc("#C9D1D9"))
    x = I.text(cv, x + 10, 4, "LINK OK", P["led"])
    I.text(cv, x + 10, 4, "SESSIONS 4  WORKING 2  NEEDS YOU 1  UNKNOWN 0", hexc("#C9D1D9"))
    if not inspector:
        return
    pw = 132
    px = cv.w - pw - 6
    cv.rect(px, 18, pw, 116, shade(P["canvas"], -0.15))
    cv.rect(px, 18, pw, 11, shade(P["canvas"], -0.35))
    I.text(cv, px + 5, 21, "AGENT INSPECTOR", hexc("#E6DCCD"))
    I.text(cv, px + pw - 12, 21, "X", hexc("#C9D1D9"))
    rows = [("AGENT", "CODEX"), ("SESSION", "EXAMPLE-SESSION-1-4"), ("STATE", "WAITING FOR INPUT"),
            ("SOURCE", "ORCA AGENT LIST"), ("DESK", "A4 - FRONT ROW"), ("SEEN", "03:00:00Z  FRESH"),
            ("DETAIL", "NOT OBSERVABLE"), ("WORKER", "NO LINK")]
    for i, (k, v) in enumerate(rows):
        I.text(cv, px + 5, 34 + i * 11, k, hexc("#8FA0B0"))
        I.text(cv, px + 40, 34 + i * 11, v[:23], hexc("#E6DCCD"))
    cv.rect(px + 5, 34 + 8 * 11 + 2, pw - 10, 1, shade(P["canvas"], 0.2))
    I.text(cv, px + 5, 34 + 8 * 11 + 6, "ANSWER IN ITS OWN TERMINAL", P["led_warn"])


def studio_overview() -> Canvas:
    doc = L.load_scene("three-worktrees")
    rendered = []
    for w in doc["worktrees"]:
        room, placements = L.room_for(doc, w["worktree_id"])
        cv = draw_room(room, pets_from(room, placements), "", chrome=False)
        rendered.append((w["display_name"], len(placements), crop(cv, 0, 0, cv.w, cv.h - CAPTION_H - 8)))
    gap = 6
    width = sum(r[2].w for r in rendered) + gap * (len(rendered) + 1)
    height = max(r[2].h for r in rendered) + 16 + CAPTION_H
    sheet = Canvas(width, height, shade(P["canvas"], -0.08))
    x = gap
    for name, count, part in rendered:
        paste(sheet, part, x, 14)
        I.text(sheet, x + 8, 5, f"ROOM {name.upper()}  -  {count} SESSIONS", hexc("#E6DCCD"))
        x += part.w + gap
    caption_bar(sheet, "STUDIO OVERVIEW - ONE ROOM PER WORKTREE (SYNTHETIC)")
    return sheet


def main() -> None:
    made = []

    def emit(name, cv, scale=3):
        save(cv, name, scale)
        made.append(f"{name}.png {cv.w}x{cv.h}")

    room, pl = room_from_scene("empty-worktree")
    emit("office-0-empty", draw_room(room, pets_from(room, pl), "EMPTY OFFICE - 0 SESSIONS", reserved=True))
    room, pl = room_from_scene("codex-and-claude")
    emit("office-2", draw_room(room, pets_from(room, pl), "2 SESSIONS - CODEX WORKING, CLAUDE IDLE"))
    room, pl = room_from_scene("four-sessions")
    emit("office-4", draw_room(room, pets_from(room, pl), "4 SESSIONS - 2 WORKING, 1 IDLE, 1 NEEDS YOU"))
    room, pl = room_from_scene("eight-sessions")
    emit("office-8", draw_room(room, pets_from(room, pl), "8 SESSIONS - TWO DESK CLUSTERS"))
    ids = [f"example-session-1-{i}" for i in range(1, 5)]
    room, pl = room_from_scene("four-sessions", {i: "ambient" for i in ids})
    emit("office-4-idle-pantry-lounge", draw_room(room, pets_from(room, pl), "4 SESSIONS ALL IDLE - PANTRY AND LOUNGE"))
    room, pl = room_from_scene("four-sessions", {i: "work" for i in ids})
    emit("office-4-active-seated", draw_room(room, pets_from(room, pl), "4 SESSIONS ALL WORKING - SEATED"))
    for opened in (False, True):
        room, pl = room_from_scene("four-sessions")
        cv = draw_room(room, pets_from(room, pl), "INSPECTOR " + ("OPEN ON PET CLICK" if opened else "CLOSED"),
                       selected="example-session-1-4" if opened else None)
        ui_overlay(cv, opened)
        emit(f"ux-inspector-{'open' if opened else 'closed'}", cv)
    emit("studio-3-worktrees", studio_overview(), 2)
    emit("behaviour-sequence", sequence_sheet(), 3)
    emit("character-poses", character_sheet(), 4)
    for line in made:
        print(line)


if __name__ == "__main__":
    main()
