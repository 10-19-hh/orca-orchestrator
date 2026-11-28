"""Topology Preview: the pixel yard as a live office of observed agent sessions (Textual).

``python -m orca_orchestrator --topology-preview`` (real Orca, read-only) or
``--topology-preview --demo`` (synthetic timeline). Separate from the yard and
console: it neither reads nor changes Snapshot v1 state.

* Polls run in a daemon thread through ``topology.feed.TopologyFeed``: one
  tracker for the whole session, one poll at a time (a tick that finds one
  running is skipped), and a failed poll keeps the last document on screen
  marked as not current. Quitting closes the feed; a poll still running is
  discarded when it returns.
* ``OfficeView`` draws with the yard's parts: the room, pet, prop and desk
  sprites, half-block pixels, ``Pose``/``Motion``. One pet per
  ``agent_sessions[]`` entry of the chosen room; desks come from
  ``office.SeatBook``. The frame timer only moves pets and redraws.
* Nothing here sends input, starts or stops a session or Worker, or records
  anything.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Region
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..tui.render import fit
from . import pixels as px
from .motion import FRAME_SECONDS, Motion, Pose
from .office import DOOR_W, DOOR_X, SEATED, OfficeActor, OfficeGeometry, SeatBook, default_room, pet_kind, rooms
from .sprites import PETS, desk_frames, pet_frames, pet_layers, pet_size, prop_frames, room, shadow
from .yard_screen import PLAIN, _corners, colors_for

PROVIDER_TEXT = {"claude": "Claude", "codex": "Codex", "other": "기타", "unknown": "미상"}
INTENT_TEXT = {"work": "작업 중", "attention": "확인 필요", "unobservable": "관측 불가", "finishing": "Worker 정리 중",
               "work_done": "작업 끝남", "ambient": "대기", "exit": "퇴장 (종료 확인)"}
LIFECYCLE_TEXT = {"active": "목록에 있음", "missing": "목록에서 빠짐 (자리 유지)", "unknown": "확인 불가 (자리 유지)",
                  "ended": "종료 확인"}
DOOR_COLORS = {"frame": "#3a2d22", "inside": "#07080b", "step": "#1f1a16"}
NARROW = 110


def _hms(moment: datetime) -> str:
    return moment.astimezone().strftime("%H:%M:%S")


def _mix(color: str, toward: str, t: float) -> str:
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(toward[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def _gray(color: str) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    v = round(0.3 * r + 0.59 * g + 0.11 * b)
    return f"#{v:02x}{v:02x}{v:02x}"


def office_frames() -> dict[tuple[str, bool], dict[str, px.Sprite]]:
    """Pet frames per (kind, dimmed). ``unknown`` is the Codex shape in greys: a palette swap, no new art."""

    out: dict[tuple[str, bool], dict[str, px.Sprite]] = {}
    for kind, base in (("claude", "claude"), ("codex", "codex"), ("unknown", "codex")):
        palette = dict(PETS[base][1])
        if kind == "unknown":
            palette = {k: _gray(v) for k, v in palette.items()}
        out[(kind, False)] = pet_frames(base, palette)
        out[(kind, True)] = pet_frames(base, {k: _mix(v, "#5a5f68", 0.55) for k, v in palette.items()})
    return out


class PollDone(Message):
    def __init__(self, result) -> None:
        super().__init__()
        self.result = result


class OfficeView(Widget, can_focus=True):
    """The office of one room. ←/→ select a pet; Enter or a click shows it in the side panel."""

    DEFAULT_CSS = "OfficeView { width: 1fr; height: 100%; }"
    BINDINGS = [Binding("left", "move(-1)", show=False), Binding("right", "move(1)", show=False),
                Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False)]

    class Selected(Message):
        def __init__(self, session_id: str | None) -> None:
            super().__init__()
            self.session_id = session_id

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, motion: Motion = Motion.NORMAL,
                 colors: str = "full", salt: int = 0, id: str | None = None) -> None:
        super().__init__(id=id)
        self.clock = clock
        self.motion = motion
        self.color_mode = colors
        self.salt = salt
        self.geom = OfficeGeometry(0, 0)
        self.seats = SeatBook()
        self.actors: dict[str, OfficeActor] = {}
        self.slots: dict[str, int] = {}
        self.sessions: dict[str, dict] = {}
        self.room_id: str | None = None
        self._initialised: set[str] = set()
        self.selected: str | None = None
        self.paused = False
        self._frames = office_frames()
        self._flipped = {k: {f: art.mirrored() for f, art in frames.items()} for k, frames in self._frames.items()}
        self._props = prop_frames()
        self._desk = desk_frames()
        self._shadows = {k: shadow(pet_size(base)[0]) for k, base in (("claude", "claude"), ("codex", "codex"),
                                                                       ("unknown", "codex"))}
        self._background: px.Pixels | None = None
        self._strips: list[Strip] = []
        self._poses: dict[str, Pose] = {}
        self._styles: dict[tuple[str | None, str | None], Style] = {}
        self._timer = None

    # -- lifecycle ---------------------------------------------------------------------------

    def on_mount(self) -> None:
        self._timer = self.set_interval(FRAME_SECONDS, self.tick, pause=True)
        self._sync_timer()

    def _sync_timer(self) -> None:
        if self._timer is None:
            return
        if self.paused or self.effective_motion is Motion.OFF:
            self._timer.pause()
        else:
            self._timer.resume()

    @property
    def effective_motion(self) -> Motion:
        return Motion.OFF if self.color_mode == "text" else self.motion

    @property
    def running(self) -> bool:
        return self._timer is not None and not self.paused and self.effective_motion is not Motion.OFF

    def set_motion(self, motion: Motion) -> None:
        self.motion = motion
        for actor in self.actors.values():
            actor.motion = self.effective_motion
        self._sync_timer()
        self.redraw(force=True)

    def on_resize(self, event: events.Resize) -> None:
        self.geom = OfficeGeometry(event.size.width, event.size.height)
        self._background = None
        for sid, actor in self.actors.items():
            actor.set_geometry(self.geom, self._seat(sid))
            if actor.intent in SEATED:
                actor.set_intent(actor.intent, self._seat(sid), self.clock())
        self.redraw(force=True)

    # -- state in ----------------------------------------------------------------------------

    def _seat(self, sid: str) -> tuple[float, float] | None:
        slot = self.slots.get(sid)
        return None if slot is None else self.geom.seat(slot)

    def show(self, doc: dict, room_id: str | None) -> None:
        """Applies one document to the chosen room. Pets appear, change and leave only by the document."""

        now = self.clock()
        if room_id != self.room_id:
            self.actors.clear()
            self.room_id = room_id
            if self.selected is not None:
                self.selected = None
        first_time = room_id not in self._initialised
        mine = [s for s in doc["agent_sessions"] if s["worktree_id"] == room_id]
        self.sessions = {s["session_id"]: s for s in mine}
        self.slots = self.seats.sync(room_id, mine) if room_id is not None else {}
        for s in sorted(mine, key=lambda s: s["arrival"]):
            sid, intent = s["session_id"], s["presentation"]["intent"]
            actor = self.actors.get(sid)
            if actor is None:
                if s["lifecycle"] == "ended":
                    continue  # never seen here: nothing to walk out
                kind = pet_kind(s["provider"])
                actor = OfficeActor.create(sid, s["provider"], pet_size("claude" if kind == "claude" else "codex"),
                                           self.geom, self.effective_motion, now, at_door=not first_time,
                                           salt=self.salt)
                self.actors[sid] = actor
            actor.set_intent(intent, self._seat(sid), now)
        for sid in [sid for sid in self.actors if sid not in self.sessions]:
            del self.actors[sid]  # dropped after the end retention: it has already left
        if room_id is not None:
            self._initialised.add(room_id)
        if self.selected not in self.actors:
            self.selected = next(iter(self.actors), None)
        self.redraw(force=True)

    # -- frames -------------------------------------------------------------------------------

    def tick(self) -> None:
        self.redraw()

    def _advance(self) -> dict[str, Pose]:
        now = self.clock()
        poses = {}
        for sid, actor in list(self.actors.items()):
            pose = actor.advance(now)
            if pose is None:
                del self.actors[sid]
                if self.selected == sid:
                    self.selected = next(iter(self.actors), None)
                continue
            poses[sid] = pose
        self._poses = poses
        return poses

    def compose_pixels(self, poses: dict[str, Pose]) -> px.Pixels:
        geom = self.geom
        if self._background is None:
            self._background = room(geom.width, geom.px_height)
            _door(self._background, geom)
        layers: list[tuple[int, int, list[tuple[px.Sprite, int, int]]]] = []
        seated = {self.slots[sid]: (actor, poses.get(sid)) for sid, actor in self.actors.items()
                  if sid in self.slots and actor.state == "seated"}
        for slot in range(min(self.seats.desks(self.room_id) if self.room_id else 0, geom.capacity)):
            xy = geom.desk_xy(slot)
            holder = seated.get(slot)
            frame = "off"
            if holder is not None and holder[1] is not None:
                frame = "on1" if holder[1].phase % 2 == 0 else "on2"
            layers.append((geom.desk_foot(slot), 0, [(self._desk[frame], *xy)]))
        for order, (sid, pose) in enumerate(poses.items(), start=1):
            actor = self.actors[sid]
            kind = pet_kind(actor.provider)
            base = "claude" if kind == "claude" else "codex"
            body, props = pet_layers(base, pose.action, pose.phase)
            frames = (self._flipped if pose.flip else self._frames)[(kind, actor.dim)]
            art = frames[body]
            top = pose.foot - art.height + 1 - pose.dy
            items = [] if body == "error" else [(self._shadows[kind], pose.x + 1, pose.foot)]
            items.append((art, pose.x, top))
            items += [(self._props[prop], pose.x + dx, top + dy) for prop, dx, dy in props]
            layers.append((pose.foot, order, items))
        layers.sort(key=lambda d: (d[0], d[1]))
        canvas = px.compose(self._background, [item for _, _, items in layers for item in items])
        if self.has_focus and self.selected in poses:
            pose = poses[self.selected]
            art = self._frames[(pet_kind(self.actors[self.selected].provider), False)]["idle"]
            _corners(canvas, pose.x, pose.foot - art.height + 1, art.width, art.height)
        return canvas

    def _style(self, fg: str | None, bg: str | None) -> Style:
        key = (fg, bg)
        style = self._styles.get(key)
        if style is None:
            style = self._styles[key] = Style(color=fg, bgcolor=bg)
        return style

    def _rows(self) -> list[Strip]:
        width, height = self.size.width, self.size.height
        if width <= 0 or height <= 0:
            return []
        if self.color_mode == "text" or not self.geom.fits:
            self._advance()
            why = "색상 제한" if self.color_mode == "text" else "화면이 작음"
            note = fit(f"{why}: 사무실 그림 대신 오른쪽 목록으로 표시 · 펫 {len(self.actors)}", width)
            return [Strip([Segment(note, PLAIN)]).adjust_cell_length(width, PLAIN)] + \
                [Strip.blank(width, PLAIN)] * (height - 1)
        strips = []
        for row in px.to_cells(self.compose_pixels(self._advance()))[:height]:
            segments: list[Segment] = []
            run_style, run_text = None, []
            for cell in row:
                style = self._style(cell.fg, cell.bg)
                if style is not run_style and run_text:
                    segments.append(Segment("".join(run_text), run_style))
                    run_text = []
                run_style = style
                run_text.append(cell.char)
            if run_text:
                segments.append(Segment("".join(run_text), run_style))
            strips.append(Strip(segments, width))
        return strips + [Strip.blank(width, PLAIN)] * (height - len(strips))

    def redraw(self, force: bool = False) -> None:
        if not self.is_mounted:
            return
        before = self._strips
        after = self._rows()
        self._strips = after
        if force or len(before) != len(after):
            self.refresh()
            return
        for y, (old, new) in enumerate(zip(before, after)):
            if old != new:
                self.refresh(Region(0, y, self.size.width, 1))

    def render_line(self, y: int) -> Strip:
        if not self._strips and self.size.height:
            self._strips = self._rows()
        if 0 <= y < len(self._strips):
            return self._strips[y]
        return Strip.blank(self.size.width, PLAIN)

    # -- selection ----------------------------------------------------------------------------

    def target_at(self, x: int, y: int) -> str | None:
        for sid in sorted(self._poses, key=lambda s: self._poses[s].foot, reverse=True):
            pose = self._poses[sid]
            if self.geom.actor_rect(pose.x, pose.foot, self.actors[sid].size).contains(x, y):
                return sid
        return None

    def select(self, sid: str | None) -> None:
        if sid != self.selected:
            self.selected = sid
            self.redraw(force=True)
        self.post_message(self.Selected(sid))

    def action_move(self, step: int) -> None:
        order = [s for s in sorted(self.actors, key=lambda s: self.sessions.get(s, {}).get("arrival", 0))]
        if not order:
            return
        index = order.index(self.selected) if self.selected in order else -step
        self.select(order[(index + step) % len(order)])

    def on_click(self, event: events.Click) -> None:
        offset = event.get_content_offset(self)
        if offset is None:
            return
        sid = self.target_at(offset.x, offset.y)
        if sid is not None:
            event.stop()
            self.select(sid)

    def on_focus(self) -> None:
        self.redraw(force=True)

    def on_blur(self) -> None:
        self.redraw(force=True)


def _door(canvas: px.Pixels, geom: OfficeGeometry) -> None:
    """A doorway in the back wall, left side: where pets enter and leave (a flat placeholder, not final art)."""

    top, bottom = 2, geom.wall - 1
    for y in range(top, bottom + 1):
        for x in range(DOOR_X - 1, DOOR_X + DOOR_W + 7):
            if 0 <= y < len(canvas) and 0 <= x < geom.width:
                edge = y == top or x in (DOOR_X - 1, DOOR_X + DOOR_W + 6)
                canvas[y][x] = DOOR_COLORS["frame"] if edge else DOOR_COLORS["inside"]
    for x in range(DOOR_X - 2, DOOR_X + DOOR_W + 8):
        if 0 <= x < geom.width and geom.wall < len(canvas):
            canvas[geom.wall][x] = DOOR_COLORS["step"]


# --- the app ------------------------------------------------------------------------------------


class TopologyPreviewApp(App[int]):
    TITLE = "Orca Topology Preview"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; }
    #pv-header { height: auto; max-height: 2; padding: 0 1; background: $panel; text-wrap: nowrap;
                 text-overflow: ellipsis; }
    #pv-banner { height: 1; padding: 0 1; color: $warning; text-wrap: nowrap; text-overflow: ellipsis; }
    #pv-main { height: 1fr; }
    #office { width: 1fr; height: 100%; }
    #pv-side { width: 40; border-left: solid $primary-darken-2; }
    #rooms { height: auto; max-height: 9; }
    #pv-sessions { height: 1fr; padding: 0 1; }
    #pv-status { height: 1; padding: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }
    #pv-keys { height: auto; max-height: 2; padding: 0 1; color: $text-muted; }
    .narrow #pv-main { layout: vertical; }
    .narrow #office { width: 100%; height: 1fr; }
    .narrow #pv-side { width: 100%; height: 12; border-left: none; border-top: solid $primary-darken-2; }
    """
    BINDINGS = [
        Binding("q", "quit_preview", "종료"), Binding("ㅂ", "quit_preview", show=False),
        Binding("ctrl+c", "quit_preview", show=False, priority=True),
        Binding("r", "poll_now", "지금 조회"), Binding("ㄱ", "poll_now", show=False),
        Binding("n", "poll_now", show=False), Binding("ㅜ", "poll_now", show=False),
        Binding("left_square_bracket", "room(-1)", "이전 방"), Binding("right_square_bracket", "room(1)", "다음 방"),
        Binding("m", "motion", "움직임"), Binding("ㅡ", "motion", show=False),
    ]

    def __init__(self, feed, *, interval: float, motion: Motion = Motion.NORMAL,
                 clock: Callable[[], float] = time.monotonic, colors: str = "auto", salt: int = 0) -> None:
        super().__init__()
        self.feed = feed
        self.interval = interval
        self._motion, self._clock, self._colors, self._salt = motion, clock, colors, salt
        self.demo = feed.source.mode == "demo"
        self.doc: dict | None = None
        self.room_id: str | None = None
        self.shown_at: datetime | None = None
        self.failed: tuple[datetime, str] | None = None
        self.poll_requests = self.poll_started = self.poll_skipped = 0
        self.applied = 0

    def compose(self) -> ComposeResult:
        yield Static(id="pv-header")
        yield Static(id="pv-banner")
        with Horizontal(id="pv-main"):
            colors = self._colors
            if colors == "auto":
                colors = colors_for(self.console.color_system, getattr(self, "no_color", False))
            yield OfficeView(clock=self._clock, motion=self._motion, colors=colors, salt=self._salt, id="office")
            with Vertical(id="pv-side"):
                yield OptionList(id="rooms")
                with VerticalScroll(id="pv-sessions"):
                    yield Static(id="pv-list")
        yield Static(id="pv-status")
        yield Static(id="pv-keys")

    @property
    def office(self) -> OfficeView:
        return self.query_one("#office", OfficeView)

    def on_mount(self) -> None:
        self.query_one("#rooms", OptionList).border_title = "방 (Worktree)"
        self.set_class(self.size.width < NARROW, "narrow")
        self.set_interval(self.interval, self.request_poll)
        self.office.focus()
        self._paint()
        self.request_poll()

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width < NARROW, "narrow")

    # -- polling -----------------------------------------------------------------------------

    def request_poll(self) -> None:
        """Starts a poll in a daemon thread unless one is running (then this tick is skipped)."""

        self.poll_requests += 1
        if not self.feed.begin():
            self.poll_skipped += 1
            return
        self.poll_started += 1
        feed = self.feed

        def work() -> None:
            result = feed.poll()
            if result is not None and not feed.closed:
                self.post_message(PollDone(result))

        threading.Thread(target=work, name="topology-poll", daemon=True).start()
        self._paint_header()

    def on_poll_done(self, message: PollDone) -> None:
        if not self.is_running or self.return_code is not None or self.feed.closed:
            return
        result = message.result
        self.applied += 1
        if result.document is None:
            self.failed = (result.finished, result.error or "")
        else:
            self.doc, self.shown_at, self.failed = result.document, result.finished, None
            listed = {r.worktree_id for r in rooms(self.doc)}
            if self.room_id not in listed:
                self.room_id = default_room(self.doc)
            self.office.show(self.doc, self.room_id)
        self._paint()

    # -- painting ----------------------------------------------------------------------------

    def _paint_header(self) -> None:
        doc = self.doc
        parts = []
        if self.demo:
            meta = doc["demo"] if doc and doc.get("demo") else None
            step = f" · 단계 {meta['step']}/{meta['steps']} · {meta.get('step_note', '')}" if meta else ""
            parts.append(f"DEMO 합성 장면{step} · 실제 세션 아님")
        else:
            parts.append("LIVE 읽기 전용 · Orca 조회만 함 (입력·실행·전송 없음)")
        if doc is not None:
            rt = doc["runtime"]
            conn = "연결됨" if rt["connected"] else f"연결 안 됨 ({rt['health']})"
            parts.append(f"Runtime {conn}" + (f" · {rt['app_version']}" if rt["app_version"] else ""))
        if self.shown_at is not None:
            parts.append(f"관측 {_hms(self.shown_at)} · 조회 {self.feed.polls}회 · {self.interval:g}초마다")
        else:
            parts.append("첫 조회 중…")
        if self.feed.in_flight:
            parts.append("조회 중")
        self.query_one("#pv-header", Static).update(Text(" · ".join(parts), no_wrap=True, overflow="ellipsis"))

    def _paint(self) -> None:
        if not self.is_mounted:
            return
        self._paint_header()
        banner = self.query_one("#pv-banner", Static)
        notes = []
        if self.failed is not None:
            notes.append(f"⚠ 조회 실패 {_hms(self.failed[0])} ({fit(self.failed[1], 60)}) · "
                         "화면은 마지막 관측 결과입니다")
        if self.doc is not None:
            bad = [f"{d['source']} {d['availability']}" for d in self.doc["diagnostics"]]
            if bad:
                notes.append("관측 제한: " + ", ".join(bad) + " · 자리와 펫은 유지")
        banner.update(Text(" · ".join(notes) if notes else "관측 제한 없음", style="" if notes else "dim",
                           no_wrap=True, overflow="ellipsis"))
        self._paint_rooms()
        self._paint_sessions()
        status = Text(no_wrap=True, overflow="ellipsis")
        summary = next((r for r in rooms(self.doc) if r.worktree_id == self.room_id), None) if self.doc else None
        if summary is None:
            status.append("방 없음" if self.doc else "… 조회 전", style="dim")
        else:
            geom = self.office.geom
            desks = self.office.seats.desks(self.room_id)
            status.append(f"방 {summary.name} · 세션 {summary.sessions} · 작업 {summary.working} · "
                          f"확인 필요 {summary.attention} · 관측 불가 {summary.unobservable} · "
                          f"터미널 장치 {summary.devices} · 책상 {desks} (화면 {geom.capacity}자리)")
            if desks > geom.capacity:
                status.append(" · 화면이 좁아 일부 자리는 목록에만", style="yellow")
        self.query_one("#pv-status", Static).update(status)
        keys = ("←→ 펫 선택 · 클릭 상세 · [ ] 방 전환 · " + ("n 다음 단계" if self.demo else "r 지금 조회")
                + f" · m 움직임 {self.office.motion.label} · q 종료")
        self.query_one("#pv-keys", Static).update(Text(keys))

    def _paint_rooms(self) -> None:
        options = self.query_one("#rooms", OptionList)
        listed = rooms(self.doc) if self.doc else []
        items = []
        for r in listed:
            mark = "▶" if r.worktree_id == self.room_id else " "
            extra = f" (작업 {r.working}" + (f", 확인 {r.attention}" if r.attention else "") + ")" if r.sessions else ""
            state = "" if r.state == "open" else f" · {r.state}"
            items.append((r.worktree_id, f"{mark} {fit(r.name, 22)} · 세션 {r.sessions}{extra}{state}"))
        key = tuple(items)
        if getattr(self, "_room_key", None) == key:
            return
        self._room_key = key
        options.set_options([Option(text, id=oid) for oid, text in items])
        ids = [oid for oid, _ in items]
        if self.room_id in ids:
            options.highlighted = ids.index(self.room_id)

    def _paint_sessions(self) -> None:
        out = Text()
        if self.doc is None or self.room_id is None:
            self.query_one("#pv-list", Static).update(Text("세션 목록: 조회 전", style="dim"))
            return
        office = self.office
        sessions = sorted(office.sessions.values(), key=lambda s: s["arrival"])
        out.append(f"세션 {sum(1 for s in sessions if s['lifecycle'] != 'ended')}\n", style="bold")
        for s in sessions:
            slot = office.slots.get(s["session_id"])
            seat = f"#{slot + 1}" if slot is not None else "--"
            sid = s["session_id"]
            short = sid if not s["identity"]["confirmed"] else "…" + sid[-6:]
            mark = "▶ " if sid == office.selected else "  "
            style = "bold cyan" if sid == office.selected else ""
            out.append(f"{mark}{seat} {PROVIDER_TEXT[s['provider']]} {short} · "
                       f"{INTENT_TEXT[s['presentation']['intent']]}\n", style=style)
        selected = office.sessions.get(office.selected) if office.selected else None
        if selected is not None:
            s = selected
            out.append("\n선택한 펫\n", style="bold")
            lines = [
                f"Provider {PROVIDER_TEXT[s['provider']]} ({s['provider_basis']})",
                f"세션 {s['session_id']}",
                "동일성 " + ("Orca 세션 ID" if s["identity"]["confirmed"] else f"임시 ({s['identity']['basis']})"),
                f"Orca 상태 {s['activity']}" + (f" (마지막 {s['last_activity']})" if s["activity"] == "unknown"
                                                else ""),
                f"생애 주기 {LIFECYCLE_TEXT[s['lifecycle']]}",
                f"연결 {s['connection']} · 패널 {len(s['places'])}곳",
                f"마지막 관측 {s['freshness']['last_seen_at']}",
                "세부 작업 관측 불가 (Typing·Thinking·Building 없음)",
            ]
            flags = [t for t, on_ in (("다시 관측", s["reappeared"]), ("프로세스 교체", s["incarnation_changed"]),
                                      ("이전 세션과 동일성 미확인", bool(s["possible_predecessor"]))) if on_]
            if flags:
                lines.append(" · ".join(flags))
            if s["presentation"]["intent"] == "attention":
                lines.append("답은 그 에이전트의 터미널에서 (여기서는 보내지 않음)")
            for line in lines:
                out.append(line + "\n")
        self.query_one("#pv-list", Static).update(out)

    # -- input --------------------------------------------------------------------------------

    @on(OfficeView.Selected)
    def _selected(self, event: OfficeView.Selected) -> None:
        event.stop()
        self._paint_sessions()

    @on(OptionList.OptionSelected, "#rooms")
    def _room_chosen(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.choose_room(event.option.id)

    def choose_room(self, room_id: str | None) -> None:
        if room_id is None or self.doc is None or room_id == self.room_id:
            return
        self.room_id = room_id
        self.office.show(self.doc, room_id)
        self._paint()

    def action_room(self, step: int) -> None:
        listed = [r.worktree_id for r in rooms(self.doc)] if self.doc else []
        if not listed:
            return
        index = listed.index(self.room_id) if self.room_id in listed else 0
        self.choose_room(listed[(index + step) % len(listed)])

    def action_poll_now(self) -> None:
        self.request_poll()

    def action_motion(self) -> None:
        office = self.office
        office.set_motion(office.motion.next())
        self._paint()

    def action_quit_preview(self) -> None:
        self.feed.close()
        self.exit(0)

    def on_unmount(self) -> None:
        self.feed.close()


def run_topology_preview(feed, *, interval: float, motion: Motion = Motion.NORMAL) -> int:
    app = TopologyPreviewApp(feed, interval=interval, motion=motion)
    result = app.run()
    feed.close()
    return int(result or 0)


__all__ = ["OfficeView", "PollDone", "TopologyPreviewApp", "run_topology_preview", "office_frames"]
