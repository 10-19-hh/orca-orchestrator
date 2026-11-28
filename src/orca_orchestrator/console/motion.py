"""Where things are in the room and how the pets move. No UI framework, no I/O.

The room is seen at an angle (2.5D): a back wall, and a floor the pets walk
on in two directions, left-right (``x``) and toward or away from the viewer
(``foot``, the pixel row of their feet). Things are drawn back to front by
their feet, so a pet in front of the desk covers it and one behind is covered.

Positions, actions and walking paths live only in this screen's memory. Time
comes from the caller (a monotonic clock in the app, a fake one in tests) and
randomness from an injected ``random.Random``, so every frame is reproducible.

``Layout`` is the single coordinate source: the renderer draws the desk and
pets at its positions and the click test asks the same object which cells
they cover, so a click always lands on what is drawn, also after a resize.

What the pets do must not claim more than is known. A local check started in
this console sends them to the desk to *watch* the monitor; they never type or
build (``sprites.WORKER_ACTIONS``), because no Worker works on it. Another
session's run keeps them where they are, watching, since nothing says which
agent runs it. The coding actions are kept for a Worker observed for a pet's
own agent; this build observes none, so nothing here picks them.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from enum import Enum

from .pixels import cell_rows
from .sprites import DESK_SIZE, wall_height
from .yard import AT_BENCH, WAITING_ON_OTHERS, Mood

FPS = 8
FRAME_SECONDS = 1.0 / FPS
WALK_SPEED = 16.0  # pixels per second: the pets trot around the room
RUN_SPEED = 40.0  # to the desk, in a hurry
REST_SECONDS = (0.6, 2.4)
CELEBRATE_SECONDS = 2.5
BLINK_SECONDS = 0.15
SIZE = (16, 16)  # default sprite size in pixels

ERROR_MOODS = frozenset({Mood.FAILED, Mood.PROBLEM})
DONE = frozenset({Mood.PASSED, Mood.REVIEW, Mood.APPROVED})
WORRIED = ERROR_MOODS | {Mood.END_UNKNOWN}  # stop where they are; never back to a normal rest
MIN_WIDTH, MIN_HEIGHT = 24, 9  # cells; smaller than this there is no room for the pets
DESK_MIN_WIDTH = 60  # cells; narrower rooms have no desk (the task list stands in for it)
WATCH = "watch"  # at the desk during a local check: the pets watch the monitor (never type or build)


class Motion(str, Enum):
    NORMAL = "normal"  # walk, run, blink, work
    REDUCED = "reduced"  # no walking or running: the pets are placed; slow animation
    OFF = "off"  # a still picture; no timer

    @property
    def label(self) -> str:
        return {"normal": "보통", "reduced": "줄이기", "off": "정지"}[self.value]

    def next(self) -> "Motion":
        order = list(Motion)
        return order[(order.index(self) + 1) % len(order)]


@dataclass(frozen=True)
class Rect:
    """A rectangle in terminal cells: x, y (row), width, height."""

    x: int
    y: int
    w: int
    h: int

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h


@dataclass(frozen=True)
class Layout:
    width: int  # cells (= pixels across)
    height: int  # rows (= pixels down / 2)

    @property
    def px_width(self) -> int:
        return self.width

    @property
    def px_height(self) -> int:
        return self.height * 2

    @property
    def fits(self) -> bool:
        return self.width >= MIN_WIDTH and self.height >= MIN_HEIGHT

    @property
    def wall(self) -> int:
        """Pixel rows of the back wall; the floor starts below."""

        return wall_height(self.px_height)

    @property
    def has_desk(self) -> bool:
        return self.fits and self.width >= DESK_MIN_WIDTH

    @property
    def desk_xy(self) -> tuple[int, int] | None:
        """Top-left pixel of the desk sprite (it stands toward the back right of the floor)."""

        if not self.has_desk:
            return None
        dw, dh = DESK_SIZE
        foot = self.wall + max(dh // 2, int((self.px_height - self.wall) * 0.45))
        foot = min(foot, self.px_height - 2)
        return self.width - dw - 22, foot - dh + 1

    @property
    def desk_foot(self) -> int | None:
        xy = self.desk_xy
        return None if xy is None else xy[1] + DESK_SIZE[1] - 1

    def feet(self, h: int) -> tuple[int, int]:
        """Rows a pet ``h`` pixels tall may stand on: on the floor, head not above the wall top."""

        lo = max(self.wall + 1, h + 1)
        return lo, max(lo, self.px_height - 2)

    def xs(self, w: int) -> tuple[int, int]:
        return 1, max(1, self.width - w - 1)

    def clamp(self, x: float, foot: float, size: tuple[int, int] = SIZE) -> tuple[float, float]:
        lo, hi = self.xs(size[0])
        flo, fhi = self.feet(size[1])
        return min(max(x, 0), hi + 1), min(max(foot, flo), fhi)

    def blocked(self, x: float, foot: float, w: int) -> bool:
        """Whether a pet standing here would stand inside the desk's footprint."""

        xy = self.desk_xy
        if xy is None:
            return False
        dx, foot_desk = xy[0], self.desk_foot
        return x + w > dx and x < dx + DESK_SIZE[0] and foot_desk - 5 <= foot <= foot_desk + 1

    def work_spot(self, slot: int, size: tuple[int, int] = SIZE) -> tuple[int, int]:
        """Where a pet works: slot 0 left of the desk, slot 1 right of it, both level with its feet."""

        w, h = size
        xy = self.desk_xy
        if xy is None:
            lo, hi = self.xs(w)
            flo, fhi = self.feet(h)
            return (max(lo, hi - slot * (w + 2)), fhi - 1)
        foot = self.desk_foot
        x = xy[0] - w - 1 if slot == 0 else xy[0] + DESK_SIZE[0] + 1
        return x, max(self.feet(h)[0], foot)

    def actor_rect(self, x: float, foot: float, size: tuple[int, int] = SIZE) -> Rect:
        w, h = size
        cx, cfoot = self.clamp(x, foot, size)
        rows = cell_rows(int(round(cfoot)) - h + 1, h)
        return Rect(int(round(cx)), rows.start, w, len(rows))

    def desk_rect(self) -> Rect | None:
        xy = self.desk_xy
        if xy is None:
            return None
        rows = cell_rows(xy[1], DESK_SIZE[1])
        return Rect(xy[0], rows.start, DESK_SIZE[0], len(rows))

    def bubble_xy(self, actor: Rect, bubble_w: int, bubble_h: int) -> tuple[int, int]:
        """Top-left cell of a bubble over a pet, kept inside the room and never over the pet's face."""

        gap = 3  # leave room for the pet's own props (a screen, a "?")
        if actor.y >= bubble_h + gap:
            bx = actor.x + actor.w // 2 - bubble_w // 2
            return min(max(0, bx), max(0, self.width - bubble_w)), actor.y - bubble_h - gap
        right, left = actor.x + actor.w + 2, actor.x - bubble_w - 2
        if right + bubble_w <= self.width:
            bx = right
        elif left >= 0:
            bx = left
        else:
            bx = min(max(0, actor.x + actor.w // 2 - bubble_w // 2), max(0, self.width - bubble_w))
        return bx, max(0, actor.y - bubble_h + 2)


@dataclass(frozen=True)
class Pose:
    action: str  # one of sprites.ACTIONS
    phase: int  # animation step of that action
    x: int  # left pixel
    foot: int  # pixel row of the feet
    flip: bool  # facing left
    dy: int = 0  # a hop (pixels up)


class Actor:
    """A pet's presentation state. Changing it never changes a task, record or approval."""

    def __init__(self, layout: Layout, rng: random.Random | None = None, motion: Motion = Motion.NORMAL,
                 now: float = 0.0, *, size: tuple[int, int] = SIZE, slot: int = 0,
                 start: tuple[float, float] = (0.3, 0.6)) -> None:
        self.rng = rng or random.Random(0)
        self.layout = layout
        self.motion = motion
        self.size = size
        self.slot = slot
        self.mood = Mood.NO_DATA
        self.start = start
        self.placed = False
        self.x = 0.0
        self.foot = 0.0
        self.facing = 1
        self.activity = "rest"  # rest, walk, run, work, pose
        self.flavor = "think"  # what a resting pet does: idle, sweep, sleep, think, notify
        self.target: tuple[float, float] | None = None
        self.until = now + 1.0
        self.celebrate_until = 0.0
        self.blink_at = now + 2.0
        self.last: float | None = None
        self._place(layout)

    # -- inputs ---------------------------------------------------------------------------

    def _place(self, layout: Layout) -> None:
        """The starting spot, once the room has a real size (each pet gets its own part of the floor)."""

        if not self.placed and layout.fits:
            lo, hi = layout.xs(self.size[0])
            flo, fhi = layout.feet(self.size[1])
            self.x = float(lo + int((hi - lo) * self.start[0]))
            self.foot = float(flo + int((fhi - flo) * self.start[1]))
            self.placed = True

    def set_layout(self, layout: Layout) -> None:
        self.layout = layout
        self._place(layout)
        self.x, self.foot = layout.clamp(self.x, self.foot, self.size)
        if self.target is not None:
            self.target = self.work_spot if self.mood in AT_BENCH else layout.clamp(*self.target, self.size)
        if self.activity == "work":
            self.x, self.foot = self.work_spot

    @property
    def work_spot(self) -> tuple[float, float]:
        x, foot = self.layout.work_spot(self.slot, self.size)
        return float(x), float(foot)

    def rect(self) -> Rect:
        return self.layout.actor_rect(self.x, self.foot, self.size)

    def set_motion(self, motion: Motion, now: float) -> None:
        self.motion = motion
        if motion is not Motion.NORMAL:
            if self.activity == "run":
                (self.x, self.foot), self.activity, self.target = self.work_spot, "work", None
            elif self.activity == "walk":
                self.activity, self.target = "rest", None

    def _rest_flavor(self) -> str:
        if self.mood is Mood.NO_DATA:
            return "think"
        if self.mood is Mood.WAITING_RESET:
            return "sleep"
        if self.mood is Mood.REVIEW and self.slot == 0:
            return "notify"
        if self.mood is Mood.APPROVED:
            return self.rng.choice(("idle", "idle", "sleep"))
        return self.rng.choice(("idle", "idle", "idle", "sweep", "think"))

    def set_mood(self, mood: Mood, now: float) -> None:
        """Switches at once: a run that ends before a pet reaches the desk shows the new state now."""

        if mood is self.mood:
            return
        previous, self.mood = self.mood, mood
        if mood in AT_BENCH:
            if self.motion is Motion.NORMAL:
                self.activity, self.target = "run", self.work_spot
                self.facing = 1 if self.target[0] >= self.x else -1
            else:
                (self.x, self.foot), self.activity, self.target = self.work_spot, "work", None
            return
        self.target = None
        if mood in DONE and previous in AT_BENCH:
            self.activity, self.celebrate_until = "pose", now + CELEBRATE_SECONDS
        elif mood in WORRIED or mood in WAITING_ON_OTHERS:
            self.activity = "pose"
        else:
            self.activity, self.until, self.flavor = "rest", now + 1.0, self._rest_flavor()

    def cheer(self, now: float) -> None:
        if self.mood not in AT_BENCH and self.activity in ("rest", "walk"):
            self.activity, self.target, self.celebrate_until = "pose", None, now + 2.0

    # -- time ---------------------------------------------------------------------------

    def advance(self, now: float) -> Pose:
        dt = 0.0 if self.last is None else min(max(now - self.last, 0.0), 0.5)
        self.last = now
        if self.motion is Motion.OFF:
            return self._still()
        slow = self.motion is not Motion.NORMAL
        if self.activity == "run":
            if self._move(RUN_SPEED * dt):
                self.activity = "work"
            step = int(now * FPS) % 2
            return self._pose("run", step, dy=step)
        if self.activity == "work":
            return self._pose(WATCH, int(now * (1 if slow else 4)))
        if self.activity == "pose":
            if self.mood in WAITING_ON_OTHERS:  # stays put and watches; another session's run is not theirs
                return self._pose(WATCH, int(now * (1 if slow else 4)))
            if self.mood in ERROR_MOODS:
                return self._pose("error", 0)
            if self.mood is Mood.END_UNKNOWN:  # fidgets in place under a "?"
                jitter = 1 if not slow and int(now * 2) % 2 else 0
                return self._pose("question", int(now * 2), dx=jitter)
            if now < self.celebrate_until:
                return self._pose("happy", int(now * 4), dy=int(now * 4) % 2 if not slow else 0)
            self.activity, self.until, self.flavor = "rest", now + 1.0, self._rest_flavor()
        if self.activity == "walk":
            if self._move(WALK_SPEED * dt):
                self.activity, self.until = "rest", now + self.rng.uniform(*REST_SECONDS)
                self.flavor = self._rest_flavor()
            step = int(now * FPS) % 2
            return self._pose("walk", step, dy=step)
        # rest
        if not slow and now >= self.until and self.layout.fits:
            self.target = self._pick_spot()
            self.facing = 1 if self.target[0] >= self.x else -1
            self.activity = "walk"
            return self._pose("walk", 0)
        if self.flavor == "idle":
            return self._pose(self._blink(now), 0)
        rate = 1 if slow else 3
        return self._pose(self.flavor, int(now * rate))

    def _pick_spot(self) -> tuple[float, float]:
        lo, hi = self.layout.xs(self.size[0])
        flo, fhi = self.layout.feet(self.size[1])
        spot = (float(lo), float(fhi))
        for _ in range(12):
            spot = (float(self.rng.randint(lo, hi)), float(self.rng.randint(flo, fhi)))
            if not self.layout.blocked(spot[0], spot[1], self.size[0]):
                break
        return spot

    def _blink(self, now: float) -> str:
        if now >= self.blink_at:
            if now < self.blink_at + BLINK_SECONDS:
                return "blink"
            self.blink_at = now + self.rng.uniform(2.0, 5.0) * (1 if self.motion is Motion.NORMAL else 2)
        return "idle"

    def _move(self, step: float) -> bool:
        """Moves toward the target; True when it is reached."""

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

    def _pose(self, action: str, phase: int, dx: int = 0, dy: int = 0) -> Pose:
        x, foot = self.layout.clamp(self.x + dx, self.foot, self.size)
        flip = self.facing < 0 and action in ("walk", "run")
        return Pose(action, phase, int(round(x)), int(round(foot)), flip, dy)

    def _still(self) -> Pose:
        if self.mood in AT_BENCH:
            self.x, self.foot = self.work_spot
            return self._pose(WATCH, 0)
        if self.mood in WAITING_ON_OTHERS:
            return self._pose(WATCH, 0)
        if self.mood in ERROR_MOODS:
            return self._pose("error", 0)
        if self.mood is Mood.END_UNKNOWN:
            return self._pose("question", 0)
        if self.mood in DONE:
            return self._pose("notify" if self.mood is Mood.REVIEW and self.slot == 0 else "idle", 0)
        return self._pose("think" if self.mood is Mood.NO_DATA else "idle", 0)

    @property
    def animating(self) -> bool:
        """Whether frames would change without a new input (False: the timer can rest)."""

        return self.motion is not Motion.OFF
