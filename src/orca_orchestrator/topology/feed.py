"""Repeated read-only observation for the live preview.

``TopologyFeed`` keeps one :class:`~.tracker.TopologyTracker` for its whole
life, so missing sessions, reappearances, process changes and ``work_done`` are
judged against the previous poll. It runs at most one poll at a time
(``begin`` refuses while one is in flight), never blocks its caller beyond the
poll itself, and keeps the last good document when a poll fails. After
``close`` a running poll's result is discarded and no new poll starts.

Sources:

* ``LiveSource``: ``topology.discovery`` (the read commands only),
* ``TimelineSource``: a synthetic DEMO scene stepped one observation per poll.

Nothing here starts, stops or sends anything.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping, Protocol

from .document import topology_document
from .model import WorkerLink
from .parse import RawObservation, parse_observation
from .scenarios import START, STEP, Scene, raw
from .tracker import TopologyTracker


class Source(Protocol):
    mode: str

    def collect(self) -> tuple[RawObservation, Mapping[str, WorkerLink] | None]: ...


class LiveSource:
    mode = "live"

    def __init__(self, discovery=None, *, git: bool = True) -> None:
        if discovery is None:
            from .discovery import TopologyDiscovery

            discovery = TopologyDiscovery(git=git)
        self._discovery = discovery

    def collect(self) -> tuple[RawObservation, None]:
        return self._discovery.collect(), None


class TimelineSource:
    """Steps through a synthetic scene: poll k shows step min(k, last); time advances 30 s per poll."""

    mode = "demo"

    def __init__(self, scene: Scene, notes: tuple[str, ...] = ()) -> None:
        self.scene = scene
        self.notes = notes
        self.polls = 0
        self.step = 0  # index of the step the last poll showed

    @property
    def total(self) -> int:
        return len(self.scene.steps)

    def collect(self) -> tuple[RawObservation, Mapping[str, WorkerLink] | None]:
        self.step = min(self.polls, self.total - 1)
        step = self.scene.steps[self.step]
        at = START + STEP * self.polls
        self.polls += 1
        return raw(step, at), step.links

    def demo_meta(self) -> dict:
        note = self.notes[self.step] if self.step < len(self.notes) else ""
        return {"scenario": self.scene.name, "label": self.scene.title, "step": self.step + 1, "steps": self.total,
                "evidence": self.scene.evidence, "note": "합성 DEMO · 실제 세션 관측 아님", "step_note": note}


@dataclass(frozen=True)
class PollResult:
    document: dict | None  # None when the poll failed
    error: str | None
    finished: datetime
    sequence: int


class TopologyFeed:
    def __init__(self, source: Source, clock: Callable[[], datetime] | None = None) -> None:
        self.source = source
        self.tracker = TopologyTracker(source.mode, None)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._in_flight = False
        self._closed = False
        self.last_document: dict | None = None
        self.last_error: str | None = None
        self.polls = 0

    @property
    def in_flight(self) -> bool:
        return self._in_flight

    @property
    def closed(self) -> bool:
        return self._closed

    def begin(self) -> bool:
        """Claims the single poll slot. False while a poll runs or after ``close``."""

        with self._lock:
            if self._closed or self._in_flight:
                return False
            self._in_flight = True
            return True

    def poll(self) -> PollResult | None:
        """One observation (call only after ``begin`` returned True). None if the feed closed meanwhile."""

        try:
            try:
                raw_obs, links = self.source.collect()
                if self._closed:
                    return None
                if isinstance(self.source, TimelineSource):
                    self.tracker.demo = self.source.demo_meta()
                doc = topology_document(self.tracker.observe(parse_observation(raw_obs), links))
                error = None
            except Exception as exc:  # the screen keeps the last document and says the poll failed
                doc, error = None, f"{type(exc).__name__}: {exc}"
            if self._closed:
                return None
            self.polls += 1
            if doc is not None:
                self.last_document, self.last_error = doc, None
            else:
                self.last_error = error
            return PollResult(doc, error, self._clock(), self.polls)
        finally:
            with self._lock:
                self._in_flight = False

    def poll_now(self) -> PollResult | None:
        """``begin`` + ``poll`` in the caller's thread; None when a poll already runs or the feed is closed."""

        if not self.begin():
            return None
        return self.poll()

    def close(self) -> None:
        with self._lock:
            self._closed = True
