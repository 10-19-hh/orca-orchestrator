"""Room composer and station allocator for the Living Workspace layout study (design tool, stdlib only).

This is the geometry the mockups are drawn from and the layout fixtures are
exported from. It is a reference for the Phase 3 TypeScript composer, not
product code. Units are floor tiles of a 2:1 isometric grid; every position
is a multiple of 1/8 tile so it lands on whole pixels at 32x16 px per tile.

Rules it demonstrates (ROOM_LAYOUT.md):

* shared zones (pantry, lounge, meeting, review, entrance) are anchored to the
  back-left corner and never move when the room grows,
* desks come in face-to-face cluster modules at fixed anchors; a room adds a
  module instead of rearranging one, and grows to the right, then to the front,
* a session gets the lowest free seat slot when it first appears (topology
  ``arrival`` order) and keeps it; seats of a built module that no session holds
  are spare (monitor off, chair tucked in),
* ambient pets reserve interaction points so two pets never use one seat.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
TOPOLOGY_GOLDEN = ROOT / "tests" / "fixtures" / "topology_v1"

# (most sessions it seats, width, depth, cluster modules built)
TIERS = ((0, 12, 10, 0), (4, 12, 10, 1), (8, 17, 10, 2), (12, 17, 14, 4))
CLUSTER_ANCHORS = {"A": (4.75, 5.0), "B": (10.25, 5.0), "C": (4.75, 9.5), "D": (10.25, 9.5)}
SEAT_ORDER = ("N-left", "S-left", "N-right", "S-right")  # 2 sessions sit face to face


@dataclass
class Module:
    module_id: str
    asset_id: str
    x: float
    y: float
    w: float
    d: float
    h: int
    zone: str
    blocks: bool = True
    facing: str | None = None
    state: str | None = None  # stations: assigned | spare


@dataclass
class Point:
    point_id: str
    kind: str        # seat | coffee | counter | sofa_sit | sofa_lie | meeting_seat | review_view | spawn | exit | wander
    x: float
    y: float
    facing: str      # N (toward -y), S (+y), E (+x), W (-x)
    zone: str
    pose_x: float | None = None  # where the pet is drawn while using it (on the sofa, ...); None: at x, y
    pose_y: float | None = None
    capacity: int = 1


@dataclass
class Station:
    slot: str        # A1..D4
    cluster: str
    seat: str        # N-left ...
    desk: str        # module id
    chair: str
    x: float         # where the pet sits
    y: float
    facing: str
    session_id: str | None = None
    provider: str | None = None
    state: str = "spare"


@dataclass
class Room:
    worktree: str
    tier: int
    width: int
    depth: int
    modules: list[Module] = field(default_factory=list)
    points: list[Point] = field(default_factory=list)
    stations: list[Station] = field(default_factory=list)


def tier_for(sessions: int) -> tuple[int, int, int, int]:
    for index, (most, w, d, clusters) in enumerate(TIERS):
        if sessions <= most:
            return index, w, d, clusters
    return len(TIERS) - 1, TIERS[-1][1], TIERS[-1][2], TIERS[-1][3]  # 13+: annex room (see ROOM_LAYOUT.md)


def shared_zones(room: Room) -> None:
    m, p = room.modules.append, room.points.append
    # Pantry: back wall, left. Counter with coffee machine, tall cabinet.
    m(Module("pantry-counter", "furniture.pantry_counter", 0.25, 0.125, 2.75, 0.875, 14, "pantry"))
    m(Module("coffee-machine", "furniture.coffee_machine", 0.5, 0.25, 0.75, 0.625, 10, "pantry", blocks=False))
    m(Module("pantry-cabinet", "furniture.cabinet_tall", 3.0, 0.125, 0.875, 0.875, 34, "pantry"))
    p(Point("coffee", "coffee", 0.875, 1.5, "N", "pantry"))
    p(Point("counter", "counter", 2.125, 1.5, "N", "pantry"))
    # Meeting: whiteboard on the back wall, table in front of it.
    m(Module("meeting-table", "furniture.meeting_table", 7.25, 1.5, 2.25, 1.0, 11, "meeting"))
    for i, (x, y, f) in enumerate(((7.75, 2.875, "N"), (9.0, 2.875, "N"), (6.875, 2.0, "E"), (9.875, 2.0, "W"))):
        m(Module(f"meeting-chair-{i + 1}", "furniture.stool", x - 0.25, y - 0.25, 0.5, 0.5, 7, "meeting"))
        p(Point(f"meeting-{i + 1}", "meeting_seat", x, y, f, "meeting"))
    # Review / verification: test rack in the back-right corner, status board above it.
    m(Module("verify-rack", "device.verification_rack", 10.25, 0.125, 1.25, 0.75, 30, "review"))
    p(Point("review-view", "review_view", 10.875, 1.375, "N", "review"))
    # Lounge: left wall, under the window.
    m(Module("lounge-rug", "furniture.rug", 0.25, 3.5, 3.0, 3.5, 0, "lounge", blocks=False))
    m(Module("sofa", "furniture.sofa", 0.125, 3.75, 0.875, 2.5, 16, "lounge"))
    m(Module("lounge-table", "furniture.coffee_table", 1.75, 4.375, 0.75, 1.25, 7, "lounge"))
    m(Module("lounge-plant", "furniture.plant_tall", 0.25, 6.375, 0.625, 0.625, 26, "lounge"))
    m(Module("floor-lamp", "furniture.floor_lamp", 2.75, 3.625, 0.375, 0.375, 32, "lounge"))
    p(Point("sofa-1", "sofa_sit", 1.25, 4.375, "E", "lounge", 0.625, 4.375))
    p(Point("sofa-2", "sofa_sit", 1.25, 5.75, "E", "lounge", 0.625, 5.75))
    p(Point("sofa-lie", "sofa_lie", 1.25, 5.0, "E", "lounge", 0.5, 5.0))
    p(Point("window-view", "wander", 3.25, 2.25, "N", "lounge"))
    # Entrance: door in the left wall near the front, mat on the floor.
    m(Module("doormat", "environment.doormat", 0.125, 8.0, 1.0, 1.25, 0, "entrance", blocks=False))
    p(Point("spawn", "spawn", 0.625, 8.625, "E", "entrance"))
    p(Point("exit", "exit", 0.625, 8.625, "W", "entrance"))
    if room.width >= 17:  # wider wall: storage and a plant, never in front of a cluster
        m(Module("bookshelf", "furniture.bookshelf", 12.5, 0.125, 1.5, 0.625, 36, "storage"))
        m(Module("plant-back", "furniture.plant_tall", 15.875, 0.25, 0.625, 0.625, 26, "storage"))
        p(Point("bookshelf-view", "wander", 13.25, 1.25, "N", "storage"))
    if room.depth >= 14:
        m(Module("plant-front", "furniture.plant_tall", 3.25, 12.75, 0.625, 0.625, 26, "storage"))


def cluster(room: Room, name: str, seats: int) -> None:
    """A face-to-face cluster: 2 seats (left desk pair) or 4 (left and right pairs)."""

    ax, ay = CLUSTER_ANCHORS[name]
    pairs = 1 if seats <= 2 else 2
    for side in range(pairs):
        dx = ax + side * 2.0
        label = ("left", "right")[side]
        for row, (dy, seat_y, facing) in enumerate(((ay, ay - 0.75, "S"), (ay + 1.0, ay + 2.75, "N"))):
            seat = f"{'NS'[row]}-{label}"
            desk = Module(f"desk-{name}-{seat}", "furniture.desk_modular", dx, dy, 2.0, 1.0, 12, f"cluster-{name}",
                          facing=facing)
            chair = Module(f"chair-{name}-{seat}", "furniture.office_chair", dx + 0.75, seat_y - 0.25, 0.5, 0.5, 16,
                           f"cluster-{name}", facing=facing)
            room.modules += [desk, chair]
            room.stations.append(Station("", name, seat, desk.module_id, chair.module_id, dx + 1.0, seat_y, facing))
        room.modules.append(Module(f"divider-{name}-{label}", "furniture.desk_divider", dx, ay + 0.9375, 2.0, 0.125,
                                   22, f"cluster-{name}"))
    order = {s: i for i, s in enumerate(SEAT_ORDER)}
    mine = sorted((s for s in room.stations if s.cluster == name), key=lambda s: order[s.seat])
    for i, st in enumerate(mine, 1):
        st.slot = f"{name}{i}"


def compose(worktree: str, sessions: int) -> Room:
    tier, w, d, most = tier_for(sessions)
    room = Room(worktree, tier, w, d)
    shared_zones(room)
    clusters = min(most, -(-sessions // 4))  # ceil(sessions / 4), never more than the tier holds
    for i, name in enumerate("ABCD"[:clusters]):
        cluster(room, name, sessions - 4 * i if i == clusters - 1 else 4)
    flex_decor(room, clusters)
    room.stations.sort(key=lambda s: s.slot)
    return room


def flex_decor(room: Room, clusters: int) -> None:
    """Decor on floor that a later cluster module will use. Removed (never moved) when the module arrives."""

    m = room.modules.append
    if clusters < 2 and room.width >= 12:  # where cluster B goes when the room widens
        m(Module("flex-printer", "furniture.printer_cart", 10.375, 5.25, 0.875, 0.75, 13, "flex"))
        m(Module("flex-plant", "furniture.plant_tall", 10.5, 7.375, 0.625, 0.625, 26, "flex"))
    if room.width >= 17:
        m(Module("credenza", "furniture.credenza", 15.375, 4.75, 0.875, 2.0, 11, "storage"))
        m(Module("plant-corner", "furniture.plant_tall", 15.5, 8.0, 0.625, 0.625, 26, "storage"))


# --- allocation from topology@1 ---------------------------------------------------------------


def seed(session_id: str) -> int:
    return int(hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8], 16)


# Idle pets live in the pantry and lounge first; the rest of the office takes the overflow.
AMBIENT_PRIMARY = ("coffee", "sofa-1", "sofa-lie", "counter", "sofa-2")
AMBIENT_SECONDARY = ("meeting-1", "window-view", "meeting-3", "review-view", "bookshelf-view", "meeting-2", "meeting-4")


def allocate(room: Room, sessions: list[dict]) -> dict[str, dict]:
    """Seats by arrival, ambient spots by seeded preference with reservation. Returns pet placements."""

    free = [s for s in room.stations]
    placements: dict[str, dict] = {}
    for s in sorted(sessions, key=lambda s: s["arrival"]):
        if s["lifecycle"] == "ended":
            continue
        station = free.pop(0) if free else None
        if station is not None:
            station.session_id, station.provider, station.state = s["session_id"], s["provider"], "assigned"
        placements[s["session_id"]] = {"provider": s["provider"], "intent": s["presentation"]["intent"],
                                       "station": station.slot if station else None}
    for st in room.stations:
        if st.state == "spare":
            for m in room.modules:
                if m.module_id in (st.desk, st.chair):
                    m.state = "spare"
        else:
            for m in room.modules:
                if m.module_id in (st.desk, st.chair):
                    m.state = "assigned"
    taken: set[str] = set()
    names = {p.point_id for p in room.points}
    for sid, pl in sorted(placements.items(), key=lambda kv: seed(kv[0])):
        if pl["intent"] not in ("ambient", "work_done"):
            pl["at"] = "station"
            continue
        primary = [p for p in AMBIENT_PRIMARY if p in names]
        start = seed(sid) % len(primary)
        order = primary[start:] + primary[:start] + [p for p in AMBIENT_SECONDARY if p in names]
        if "sofa-lie" in taken:  # one pet napping uses the whole sofa
            taken |= {"sofa-1", "sofa-2"}
        choice = next(p for p in order if p not in taken and not (p == "sofa-lie" and taken & {"sofa-1", "sofa-2"}))
        taken.add(choice)
        pl["at"] = choice
    return placements


# --- navigation ---------------------------------------------------------------------------------

CELL = 0.25  # nav grid resolution in tiles


def nav_grid(room: Room) -> list[str]:
    cols, rows = int(room.width / CELL), int(room.depth / CELL)
    grid = [["." for _ in range(cols)] for _ in range(rows)]
    for m in room.modules:
        if not m.blocks or m.asset_id == "furniture.office_chair" or m.asset_id == "furniture.stool":
            continue
        for r in range(int(m.y / CELL), min(rows, int((m.y + m.d) / CELL + 0.999))):
            for c in range(int(m.x / CELL), min(cols, int((m.x + m.w) / CELL + 0.999))):
                grid[r][c] = "#"
    return ["".join(row) for row in grid]


def reachable(room: Room, grid: list[str]) -> dict[str, bool]:
    def cell(x: float, y: float) -> tuple[int, int]:
        return int(y / CELL), int(x / CELL)

    start = cell(0.625, 8.625)
    seen = {start}
    queue = deque([start])
    while queue:
        r, c = queue.popleft()
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < len(grid) and 0 <= nc < len(grid[0]) and grid[nr][nc] == "." and (nr, nc) not in seen:
                seen.add((nr, nc))
                queue.append((nr, nc))
    out = {p.point_id: cell(p.x, p.y) in seen for p in room.points}
    out.update({f"station-{s.slot}": cell(s.x, s.y) in seen for s in room.stations})
    return out


# --- fixtures -----------------------------------------------------------------------------------


def load_scene(name: str, step: int = -1) -> dict:
    return json.loads((TOPOLOGY_GOLDEN / f"{name}.json").read_text(encoding="utf-8"))[step]


def room_for(doc: dict, worktree_id: str) -> tuple[Room, dict]:
    sessions = [s for s in doc["agent_sessions"] if s["worktree_id"] == worktree_id and s["lifecycle"] != "ended"]
    wt = next(w for w in doc["worktrees"] if w["worktree_id"] == worktree_id)
    room = compose(wt["display_name"] or worktree_id, len(sessions))
    return room, allocate(room, sessions)


def fixture(name: str, doc: dict, rooms: list[tuple[Room, dict]]) -> dict:
    out = {"schema": "orca-studio/layout-study@0", "note": "설계 시안 fixture. 합성 topology@1 DEMO 문서에서 계산함",
           "source_scene": doc["demo"]["scenario"], "tile_px": [32, 16], "projection": "isometric 2:1",
           "nav_cell_tiles": CELL, "rooms": []}
    for room, placements in rooms:
        grid = nav_grid(room)
        reach = reachable(room, grid)
        out["rooms"].append({
            "worktree": room.worktree, "tier": room.tier, "size_tiles": [room.width, room.depth],
            "modules": [asdict(m) for m in room.modules],
            "interaction_points": [asdict(p) for p in room.points],
            "stations": [asdict(s) for s in room.stations],
            "pets": placements,
            "nav_grid": grid,
            "reachable_from_entrance": reach,
            "all_reachable": all(reach.values()),
        })
    return out


def build_fixtures(out_dir: Path) -> dict[str, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    made = {}
    for name, scene in (("office-0", "empty-worktree"), ("office-2", "codex-and-claude"),
                        ("office-4", "four-sessions"), ("office-8", "eight-sessions")):
        doc = load_scene(scene)
        made[name] = fixture(name, doc, [room_for(doc, doc["worktrees"][0]["worktree_id"])])
    doc = load_scene("three-worktrees")
    made["studio-3-worktrees"] = fixture("studio-3-worktrees", doc,
                                         [room_for(doc, w["worktree_id"]) for w in doc["worktrees"]])
    for name, data in made.items():
        (out_dir / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                                              encoding="utf-8", newline="\n")
    return made


if __name__ == "__main__":
    made = build_fixtures(Path(__file__).resolve().parents[1] / "layout-fixtures")
    for name, data in made.items():
        for room in data["rooms"]:
            seats = sum(1 for s in room["stations"] if s["state"] == "assigned")
            print(f"{name:20} {room['worktree']:14} tier {room['tier']} {room['size_tiles']} "
                  f"seats {seats}/{len(room['stations'])} reachable {room['all_reachable']}")
