"""Where things are in the yard and how the character moves. No UI framework, no I/O.

Positions, frames and walking paths live only in this screen's memory. Time
comes from the caller (a monotonic clock in the app, a fake one in tests) and
randomness from an injected ``random.Random``, so every frame is reproducible.

``Layout`` is the single coordinate source: the renderer draws the bench and
character at its positions and the click test asks the same object which
cells they cover, so a click always lands on what is drawn, also after a
resize.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from .pixels import cell_rows
from .sprites import BENCH_SIZE, CHARACTER_SIZE
from .yard import AT_BENCH, Mood

FPS = 8
FRAME_SECONDS = 1.0 / FPS
WALK_SPEED = 6.0  # pixels per second
RUN_SPEED = 32.0
CELEBRATE_SECONDS = 2.5
BLINK_SECONDS = 0.15

WORRIED = frozenset({Mood.FAILED, Mood.END_UNKNOWN, Mood.PROBLEM})
DONE = frozenset({Mood.PASSED, Mood.REVIEW, Mood.APPROVED})
MIN_WIDTH, MIN_HEIGHT = 24, 9  # cells; smaller than this there is no room for the 16x16 character
BENCH_MIN_WIDTH = 56  # cells; narrower yards have no bench (the task list stands in for it)


class Motion(str, Enum):
    NORMAL = "normal"  # walk, run, blink, work
    REDUCED = "reduced"  # no walking or running: the character is placed; slow blink and work frames
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
    def has_bench(self) -> bool:
        return self.fits and self.width >= BENCH_MIN_WIDTH

    @property
    def bench_xy(self) -> tuple[int, int] | None:
        if not self.has_bench:
            return None
        bw, bh = BENCH_SIZE
        return self.width - bw - 2, self.px_height - bh - 1

    @property
    def workshop_x(self) -> int | None:
        xy = self.bench_xy
        return None if xy is None else xy[0] - 4

    @property
    def actor_y(self) -> int:
        return max(0, self.px_height - CHARACTER_SIZE[1] - 1)

    @property
    def work_x(self) -> int:
        xy = self.bench_xy
        if xy is None:
            return self.wander[1]
        return xy[0] - CHARACTER_SIZE[0] + 3  # just in front of the bench's left edge

    @property
    def wander(self) -> tuple[int, int]:
        right = (self.workshop_x if self.workshop_x is not None else self.width) - CHARACTER_SIZE[0] - 2
        return 1, max(1, right)

    def clamp_x(self, x: float) -> float:
        lo = 0
        hi = max(0, self.width - CHARACTER_SIZE[0])
        return min(max(x, lo), hi)

    def actor_rect(self, x: float) -> Rect:
        rows = cell_rows(self.actor_y, CHARACTER_SIZE[1])
        return Rect(int(round(self.clamp_x(x))), rows.start, CHARACTER_SIZE[0], len(rows))

    def bench_rect(self) -> Rect | None:
        xy = self.bench_xy
        if xy is None:
            return None
        rows = cell_rows(xy[1], BENCH_SIZE[1])
        return Rect(xy[0], rows.start, BENCH_SIZE[0], len(rows))

    def bubble_xy(self, x: float, bubble_w: int, bubble_h: int) -> tuple[int, int]:
        """Top-left cell of a bubble over the character, kept inside the yard."""

        actor = self.actor_rect(x)
        if actor.y >= bubble_h:  # room above the head
            bx = actor.x + actor.w // 2 - bubble_w // 2
            return min(max(0, bx), max(0, self.width - bubble_w)), actor.y - bubble_h
        # No room above (a short yard): beside the character, so it never covers the face.
        right, left = actor.x + actor.w + 1, actor.x - bubble_w - 1
        if right + bubble_w <= self.width:
            bx = right
        elif left >= 0:
            bx = left
        else:
            bx = min(max(0, actor.x + actor.w // 2 - bubble_w // 2), max(0, self.width - bubble_w))
        return bx, 0


@dataclass(frozen=True)
class Pose:
    frame: str  # a key of sprites.FRAME_ROWS
    x: int  # pixels
    flip: bool  # facing left
    bench: str  # a key of sprites.BENCH_ROWS


class Actor:
    """The character's presentation state. Changing it never changes a task, record or approval."""

    def __init__(self, layout: Layout, rng: random.Random | None = None, motion: Motion = Motion.NORMAL,
                 now: float = 0.0) -> None:
        self.rng = rng or random.Random(0)
        self.layout = layout
        self.motion = motion
        self.mood = Mood.NO_DATA
        self.x = float(layout.wander[0] + (layout.wander[1] - layout.wander[0]) // 3)
        self.facing = 1
        self.activity = "rest"  # rest, walk, run, work, pose
        self.target: float | None = None
        self.until = now + 1.0
        self.celebrate_until = 0.0
        self.blink_at = now + 2.0
        self.last: float | None = None

    # -- inputs ---------------------------------------------------------------------------

    def set_layout(self, layout: Layout) -> None:
        self.layout = layout
        self.x = layout.clamp_x(self.x)
        if self.target is not None:
            self.target = layout.work_x if self.mood in AT_BENCH else layout.clamp_x(self.target)

    def set_motion(self, motion: Motion, now: float) -> None:
        self.motion = motion
        if motion is not Motion.NORMAL:
            if self.activity == "run":
                self.x, self.activity, self.target = float(self.layout.work_x), "work", None
            elif self.activity == "walk":
                self.activity, self.target = "rest", None

    def set_mood(self, mood: Mood, now: float) -> None:
        """Switches at once: a run that ends before the character reaches the bench shows the new state now."""

        if mood is self.mood:
            return
        previous, self.mood = self.mood, mood
        if mood in AT_BENCH:
            if self.motion is Motion.NORMAL:
                self.activity, self.target = "run", float(self.layout.work_x)
                self.facing = 1 if self.layout.work_x >= self.x else -1
            else:
                self.x, self.activity, self.target = float(self.layout.work_x), "work", None
            return
        self.target = None
        if mood in DONE and previous in AT_BENCH:
            self.activity, self.celebrate_until = "pose", now + CELEBRATE_SECONDS
        elif mood in WORRIED:
            self.activity = "pose"
        else:
            self.activity, self.until = "rest", now + 1.5

    # -- time ---------------------------------------------------------------------------

    def advance(self, now: float) -> Pose:
        dt = 0.0 if self.last is None else min(max(now - self.last, 0.0), 0.5)
        self.last = now
        if self.motion is Motion.OFF:
            return self._still()
        if self.activity == "run":
            if self._move(RUN_SPEED * dt):
                self.activity = "work"
            return self._pose("run1" if int(now * FPS) % 2 == 0 else "run2")
        if self.activity == "work":
            rate = 4 if self.motion is Motion.NORMAL else 1
            frame = "work1" if int(now * rate) % 2 == 0 else "work2"
            return self._pose(frame, bench="on1" if frame == "work1" else "on2")
        if self.activity == "pose":
            if self.mood in WORRIED:
                return self._pose("worried")
            if now < self.celebrate_until:
                return self._pose("happy")
            self.activity, self.until = "rest", now + 1.0
        if self.activity == "walk":
            if self._move(WALK_SPEED * dt):
                self.activity, self.until = "rest", now + self.rng.uniform(1.5, 4.0)
            return self._pose("walk1" if int(now * 4) % 2 == 0 else "walk2")
        # rest
        if self.motion is Motion.NORMAL and now >= self.until and self.layout.fits:
            lo, hi = self.layout.wander
            self.target = float(self.rng.randint(lo, hi))
            self.facing = 1 if self.target >= self.x else -1
            self.activity = "walk"
        return self._pose(self._resting_frame(now))

    def _resting_frame(self, now: float) -> str:
        if now >= self.blink_at:
            if now < self.blink_at + BLINK_SECONDS:
                return "blink"
            self.blink_at = now + self.rng.uniform(2.0, 5.0) * (1 if self.motion is Motion.NORMAL else 2)
        return "idle"

    def _move(self, step: float) -> bool:
        """Moves toward the target; True when it is reached."""

        if self.target is None:
            return True
        delta = self.target - self.x
        if abs(delta) <= step:
            self.x, self.target = self.target, None
            return True
        self.x += step if delta > 0 else -step
        self.facing = 1 if delta > 0 else -1
        return False

    def _pose(self, frame: str, bench: str = "off") -> Pose:
        x = int(round(self.layout.clamp_x(self.x)))
        flip = self.facing < 0 and frame in ("walk1", "walk2", "run1", "run2")
        return Pose(frame, x, flip, bench)

    def _still(self) -> Pose:
        if self.mood in AT_BENCH:
            self.x = float(self.layout.work_x)
            return self._pose("work1", bench="on1")
        if self.mood in WORRIED:
            return self._pose("worried")
        return self._pose("idle")

    @property
    def animating(self) -> bool:
        """Whether frames would change without a new input (False: the timer can rest)."""

        return self.motion is not Motion.OFF
