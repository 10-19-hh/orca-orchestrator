"""The live preview's office: seats, desk grid and one pet per observed agent session. No UI framework, no I/O.

It turns ``topology@1`` documents into presentation state for ``office_screen``.
Every decision it reads comes from the document: a pet exists only for an
``agent_sessions[]`` entry, and what it does follows ``presentation.intent``.
Nothing here judges a session again, and nothing picks the coding or building
actions (``sprites.WORKER_ACTIONS``) or a thought bubble: the document has no
evidence of typing, thinking or building.

* ``SeatBook`` gives each session of a room the lowest free seat slot in
  arrival order and keeps it while the session is listed, missing or unknown.
  An ended session's seat becomes free; nobody already seated moves.
* ``OfficeGeometry`` places the shared space on the left (entrance door on the
  back wall) and a grid of desks on the right, from the terminal size alone.
  Slot ``k`` always maps to the same grid cell for a given size; desks are
  only added, never moved, as sessions arrive.
* ``OfficeActor`` is one pet: it walks to its desk for ``work``/``attention``/
  ``finishing``, rests and wanders in the shared space for ``ambient``, stands
  up for ``work_done``, stops where it is with a "?" for ``unobservable`` and
  walks out of the door for ``exit``. Time and randomness are injected, so
  every frame is reproducible.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from typing import Iterable

from .motion import CELEBRATE_SECONDS, FPS, REST_SECONDS, RUN_SPEED, WALK_SPEED, Layout, Motion, Pose, Rect
from .pixels import cell_rows
from .sprites import DESK_SIZE

PET_W, PET_H = 14, 12  # the larger of the two pets: room reserved per pet
SEAT_DX, SEAT_DY = 5, 4  # a seated pet sits in front of its desk, under the monitor
SLOT_W = DESK_SIZE[0] + 2
ROW_PITCH = DESK_SIZE[1] + SEAT_DY + 3
LOUNGE_MIN = 30  # pixels of shared space on the left
MIN_WIDTH, MIN_HEIGHT = 60, 12  # cells; smaller than this the list stands in for the room
DOOR_W, DOOR_X = 8, 26
SEATED = frozenset({"work", "attention", "finishing"})
AMBIENT_FLAVORS = ("idle", "idle", "sweep", "sleep")  # never "think": no evidence of thinking


def seed_of(session_id: str) -> int:
    return int(hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8], 16)


def pet_kind(provider: str) -> str:
    return provider if provider in ("claude", "codex") else "unknown"


# --- seats -----------------------------------------------------------------------------------


class SeatBook:
    """Stable seat slots per room (worktree). Positions come from ``OfficeGeometry.seat``."""

    def __init__(self) -> None:
        self.held: dict[str, dict[str, int]] = {}
        self.built: dict[str, int] = {}

    def sync(self, worktree_id: str, sessions: Iterable[dict]) -> dict[str, int]:
        held = self.held.setdefault(worktree_id, {})
        alive = [s for s in sessions if s["worktree_id"] == worktree_id and s["lifecycle"] != "ended"]
        present = {s["session_id"] for s in alive}
        for sid in [sid for sid in held if sid not in present]:
            del held[sid]
        for s in sorted(alive, key=lambda s: (s["arrival"], s["session_id"])):
            if s["session_id"] not in held:
                taken = set(held.values())
                held[s["session_id"]] = next(i for i in range(len(taken) + 1) if i not in taken)
        top = max(held.values()) + 1 if held else 0
        self.built[worktree_id] = max(self.built.get(worktree_id, 0), top)
        return dict(held)

    def desks(self, worktree_id: str) -> int:
        """Desks built in this room so far: they stay when a session leaves (a spare desk)."""

        return self.built.get(worktree_id, 0)


# --- geometry --------------------------------------------------------------------------------


@dataclass(frozen=True)
class OfficeGeometry:
    width: int   # cells (= pixels across)
    height: int  # rows (= pixels down / 2)

    @property
    def layout(self) -> Layout:
        return Layout(self.width, self.height)

    @property
    def px_height(self) -> int:
        return self.height * 2

    @property
    def wall(self) -> int:
        return self.layout.wall

    @property
    def fits(self) -> bool:
        return self.width >= MIN_WIDTH and self.height >= MIN_HEIGHT

    @property
    def lounge_right(self) -> int:
        return max(LOUNGE_MIN, int(self.width * 0.25))

    @property
    def desk_x0(self) -> int:
        return self.lounge_right + 2

    @property
    def cols(self) -> int:
        return max(1, (self.width - self.desk_x0 - 1) // SLOT_W)

    @property
    def rows(self) -> int:
        top = self.wall + 2
        room_for_first = DESK_SIZE[1] - 1 + SEAT_DY  # desk top to the seated pet's feet
        free = self.px_height - 2 - top - room_for_first
        return max(1, free // ROW_PITCH + 1) if free >= 0 else 1

    @property
    def capacity(self) -> int:
        return self.cols * self.rows if self.fits else 0

    def desk_xy(self, slot: int) -> tuple[int, int] | None:
        if slot >= self.capacity:
            return None
        row, col = divmod(slot, self.cols)
        x = self.desk_x0 + col * SLOT_W
        y = self.wall + 2 + row * ROW_PITCH
        return x, y

    def desk_foot(self, slot: int) -> int | None:
        xy = self.desk_xy(slot)
        return None if xy is None else xy[1] + DESK_SIZE[1] - 1

    def seat(self, slot: int) -> tuple[float, float] | None:
        """Where the pet of ``slot`` works: in front of its desk, under the monitor (drawn over the desk)."""

        xy = self.desk_xy(slot)
        if xy is None:
            return None
        return float(xy[0] + SEAT_DX), float(self.desk_foot(slot) + SEAT_DY)

    def desk_rect(self, slot: int) -> Rect | None:
        xy = self.desk_xy(slot)
        if xy is None:
            return None
        rows = cell_rows(xy[1], DESK_SIZE[1])
        return Rect(xy[0], rows.start, DESK_SIZE[0], len(rows))

    @property
    def door(self) -> tuple[float, float]:
        """The entrance: a doorway in the back wall; pets appear and leave standing in it."""

        return float(DOOR_X), float(self.wall + 1 + PET_H // 3)

    def feet(self, h: int) -> tuple[int, int]:
        return self.layout.feet(h)

    def lounge_spot(self, rng: random.Random, size: tuple[int, int]) -> tuple[float, float]:
        w, h = size
        lo, hi = 1, max(1, self.lounge_right - w)
        flo, fhi = self.feet(h)
        low = min(max(flo, self.wall + 6), fhi)  # a tiny or not yet sized room still gives a valid spot
        return float(rng.randint(lo, hi)), float(rng.randint(low, fhi))

    def clamp(self, x: float, foot: float, size: tuple[int, int]) -> tuple[float, float]:
        return self.layout.clamp(x, foot, size)

    def actor_rect(self, x: float, foot: float, size: tuple[int, int]) -> Rect:
        return self.layout.actor_rect(x, foot, size)


# --- one pet ---------------------------------------------------------------------------------


@dataclass
class OfficeActor:
    session_id: str
    provider: str
    size: tuple[int, int]
    geom: OfficeGeometry
    motion: Motion = Motion.NORMAL
    rng: random.Random = field(default_factory=lambda: random.Random(0))
    x: float = 0.0
    foot: float = 0.0
    facing: int = 1
    intent: str = "ambient"
    state: str = "rest"  # walk, seated, rest, pose, gone
    target: tuple[float, float] | None = None
    seat: tuple[float, float] | None = None
    until: float = 0.0
    celebrate_until: float = 0.0
    flavor: str = "idle"
    dim: bool = False
    last: float | None = None

    @classmethod
    def create(cls, session_id: str, provider: str, size: tuple[int, int], geom: OfficeGeometry, motion: Motion,
               now: float, *, at_door: bool, salt: int = 0) -> "OfficeActor":
        actor = cls(session_id, provider, size, geom, motion, random.Random(seed_of(session_id) ^ salt))
        actor.x, actor.foot = geom.door if at_door else geom.lounge_spot(actor.rng, size)
        actor.until = now + actor.rng.uniform(*REST_SECONDS)
        actor.flavor = actor.rng.choice(AMBIENT_FLAVORS)
        return actor

    # -- inputs --------------------------------------------------------------------------------

    def set_geometry(self, geom: OfficeGeometry, seat: tuple[float, float] | None) -> None:
        self.geom = geom
        self.seat = seat
        self.x, self.foot = geom.clamp(self.x, self.foot, self.size)
        if self.state == "seated" and seat is not None:
            self.x, self.foot = seat
        if self.intent in SEATED and self.target is not None and seat is not None:
            self.target = seat

    def set_intent(self, intent: str, seat: tuple[float, float] | None, now: float) -> None:
        """Follows ``presentation.intent``. Re-applying the same intent changes nothing."""

        self.seat = seat
        if intent == self.intent and self.state != "gone":
            if intent in SEATED and self.state != "seated" and seat is not None:
                self._go(seat, now)
            return
        previous, self.intent = self.intent, intent
        self.dim = intent == "unobservable"
        if intent in SEATED:
            if seat is None:  # no visible seat (room too small): stays in the shared space
                self.state, self.target = "rest", None
            else:
                self._go(seat, now)
        elif intent == "unobservable":
            self.state, self.target = "pose", None  # stops where it is; not sent to rest, not shown working
        elif intent == "work_done":
            self.state, self.target, self.celebrate_until = "pose", None, now + CELEBRATE_SECONDS
        elif intent == "exit":
            self._go(self.geom.door, now)
        else:  # ambient
            if previous in SEATED or self.state == "seated":
                self.state, self.target, self.until = "rest", None, now + 0.5
            elif self.state not in ("walk", "rest"):
                self.state, self.until = "rest", now + self.rng.uniform(*REST_SECONDS)
            self.flavor = self.rng.choice(AMBIENT_FLAVORS)

    def _go(self, spot: tuple[float, float], now: float) -> None:
        if self.motion is Motion.NORMAL:
            self.state, self.target = "walk", spot
            self.facing = 1 if spot[0] >= self.x else -1
        else:  # reduced or off: placed, no walking
            self.x, self.foot = spot
            self.target = None
            self._arrive(now)

    def _arrive(self, now: float) -> None:
        if self.intent == "exit":
            self.state = "gone"
        elif self.intent in SEATED and self.seat is not None:
            self.state = "seated"
        else:
            self.state, self.until = "rest", now + self.rng.uniform(*REST_SECONDS)

    # -- time ----------------------------------------------------------------------------------

    @property
    def gone(self) -> bool:
        return self.state == "gone"

    def advance(self, now: float) -> Pose | None:
        dt = 0.0 if self.last is None else min(max(now - self.last, 0.0), 0.5)
        self.last = now
        if self.state == "gone":
            return None
        if self.motion is Motion.OFF:
            return self._still()
        slow = self.motion is not Motion.NORMAL
        if self.state == "walk":
            speed = RUN_SPEED if self.intent in SEATED or self.intent == "exit" else WALK_SPEED
            if self._move(speed * dt):
                self._arrive(now)
                if self.state == "gone":
                    return None
            step = int(now * FPS) % 2
            return self._pose("walk", step, dy=step)
        if self.state == "seated":
            if self.intent == "attention":
                return self._pose("notify", int(now * (1 if slow else 2)))
            return self._pose("watch", int(now * (1 if slow else 4)))
        if self.state == "pose":
            if self.intent == "unobservable":
                return self._pose("question", int(now * 2))
            if now < self.celebrate_until:
                return self._pose("happy", int(now * 4), dy=int(now * 4) % 2 if not slow else 0)
            self.state, self.until, self.flavor = "rest", now + 0.5, self.rng.choice(AMBIENT_FLAVORS)
        # rest in the shared space
        if not slow and now >= self.until and self.geom.fits:
            self.target = self.geom.lounge_spot(self.rng, self.size)
            self.facing = 1 if self.target[0] >= self.x else -1
            self.state = "walk"
            return self._pose("walk", 0)
        if self.flavor == "idle":
            return self._pose("blink" if int(now * 2) % 9 == 0 else "idle", 0)
        return self._pose(self.flavor, int(now * (1 if slow else 3)))

    def _still(self) -> Pose:
        if self.state == "walk" and self.target is not None:
            self.x, self.foot = self.target
            self.target = None
            self._arrive(self.last or 0.0)
            if self.state == "gone":
                return self._pose("idle", 0)
        if self.state == "seated":
            return self._pose("notify" if self.intent == "attention" else "watch", 0)
        if self.intent == "unobservable":
            return self._pose("question", 0)
        return self._pose("idle", 0)

    def _move(self, step: float) -> bool:
        if self.target is None:
            return True
        tx, ty = self.target
        dx, dy = tx - self.x, ty - self.foot
        dist = math.hypot(dx, dy)
        if dist <= step or dist == 0:
            self.x, self.foot = tx, ty
            self.target = None
            return True
        self.x += dx / dist * step
        self.foot += dy / dist * step
        if abs(dx) > 0.01:
            self.facing = 1 if dx > 0 else -1
        return False

    def _pose(self, action: str, phase: int, dy: int = 0) -> Pose:
        x, foot = self.geom.clamp(self.x, self.foot, self.size)
        return Pose(action, phase, int(round(x)), int(round(foot)), self.facing < 0 and action == "walk", dy)

    def rect(self) -> Rect:
        return self.geom.actor_rect(self.x, self.foot, self.size)


# --- the room as the screen sees it ----------------------------------------------------------------


@dataclass(frozen=True)
class RoomSummary:
    worktree_id: str
    name: str
    state: str
    sessions: int
    working: int
    attention: int
    unobservable: int
    devices: int


def rooms(doc: dict) -> list[RoomSummary]:
    out = []
    for w in doc["worktrees"]:
        if w["lifecycle"] == "removed":
            continue
        mine = [s for s in doc["agent_sessions"] if s["worktree_id"] == w["worktree_id"] and s["lifecycle"] != "ended"]
        count = lambda *i: sum(1 for s in mine if s["presentation"]["intent"] in i)  # noqa: E731
        devices = sum(1 for t in doc["terminals"] if t["worktree_id"] == w["worktree_id"]
                      and t["role"] == "terminal_device" and t["listed"] and not t["exit_reported"])
        out.append(RoomSummary(w["worktree_id"], w["display_name"] or w["worktree_id"], w["room"]["state"],
                               len(mine), count("work", "finishing"), count("attention"), count("unobservable"),
                               devices))
    return out


def default_room(doc: dict) -> str | None:
    """The first room (arrival order) with a session, else the first room."""

    listed = rooms(doc)
    for r in listed:
        if r.sessions:
            return r.worktree_id
    return listed[0].worktree_id if listed else None
