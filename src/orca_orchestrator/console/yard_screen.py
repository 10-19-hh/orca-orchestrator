"""The yard: the console's default screen (Textual). Shows state; never acts on it.

``YardView`` draws the pixel yard through Textual's line API: each frame is
composed in pixel space (``pixels``), turned into half-block cells and kept
as one ``Strip`` per row; only rows that changed are refreshed. Nothing is
printed to the terminal directly.

The animation timer only advances the character (``motion.Actor``) and
redraws. It never calls the collector, git, Orca, a check or the flow: state
comes in through ``YardScreen.show`` from the console's existing refresh and
run events. The timer stops while the yard is hidden, the app is in the
background, motion is off, or the screen is too small or has too few colours
for pixels.

Clicking the character, the bench or the bubble only opens a detail screen;
nothing is run, approved or retried from here.
"""

from __future__ import annotations

import random
import time
from typing import Callable

from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.geometry import Region
from textual.message import Message
from textual.screen import Screen
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..tui.render import display_width, fit
from . import pixels as px
from .motion import FRAME_SECONDS, Actor, Layout, Motion, Pose, Rect
from .sprites import SELECT_COLOR, bench_frames, character_frames, garden
from .yard import AT_BENCH, Mood, Request, RequestBook, YardModel

ACTOR, BENCH, BUBBLE = "actor", "bench", "bubble"
TARGET_NAMES = {ACTOR: "캐릭터", BENCH: "작업대", BUBBLE: "말풍선"}
NARROW_WIDTH, SHORT_HEIGHT = 70, 22
BUBBLE_MAX = 44

# Colour support (YARD-06): "full" draws pixels in truecolor (Rich lowers them for 256-colour terminals),
# "text" replaces the pixels with a character-only scene.
PIXEL_COLOR_SYSTEMS = ("truecolor", "256")
PLAIN = Style()  # every segment carries a style (Textual's NO_COLOR filter expects one)


def colors_for(color_system: str | None, no_color: bool) -> str:
    if no_color or color_system not in PIXEL_COLOR_SYSTEMS:
        return "text"
    return "full"


class TextLayout:
    """The character-only scene used when colours are limited: same targets, plain text."""

    ACTOR_W, BENCH_W, H = 10, 10, 3

    def __init__(self, width: int, height: int, at_bench: bool) -> None:
        self.width, self.height = width, height
        self.has_bench = width >= 34 and height >= self.H
        bx = width - self.BENCH_W - 1
        self.bench = Rect(bx, 0, self.BENCH_W, self.H) if self.has_bench else None
        self.actor = Rect(bx - self.ACTOR_W - 1 if (at_bench and self.has_bench) else 1, 0, self.ACTOR_W, self.H)

    def lines(self, frame: str, selected: str | None) -> list[str]:
        # ASCII and Hangul only: their cell widths are the same in every terminal and in Rich.
        face = {"worried": " ( ;_; ) ", "happy": "\\( ^o^ )/", "blink": " ( -_- ) ",
                "work1": " ( o_o )>", "work2": " ( o_o )>"}.get(frame, " ( o_o ) ")
        actor = [face, "  /|  |\\ ", "   /  \\  "]
        bench = ["+--------+", "| 작업대 |", "+--------+"]
        rows = []
        for i in range(self.H):
            line = [" "] * self.width
            parts = [(self.actor, actor[i])] + ([(self.bench, bench[i])] if self.bench else [])
            for rect, text in parts:
                col = rect.x
                for ch in text:
                    w = cell_len(ch)
                    if col + w > self.width:
                        break
                    line[col] = ch
                    for extra in range(1, w):
                        line[col + extra] = ""
                    col += w
            rows.append("".join(line))
        marks = {ACTOR: self.actor, BENCH: self.bench}
        if selected in marks and marks[selected] is not None and self.height > self.H:
            rect = marks[selected]
            rows.append(" " * rect.x + fit("▲ 선택됨", rect.w))
        return rows


class YardView(Widget, can_focus=True):
    """The pixel yard. Arrow keys move the selection between targets; Enter or a click opens one."""

    DEFAULT_CSS = "YardView { width: 100%; height: 100%; }"
    BINDINGS = [
        Binding("left", "move(-1)", "이전 대상", show=False), Binding("up", "move(-1)", show=False),
        Binding("right", "move(1)", "다음 대상", show=False), Binding("down", "move(1)", show=False),
        Binding("enter", "open", "상세 열기", show=False),
    ]

    class Open(Message):
        """The person asked to open the detail behind a target (actor, bench or bubble)."""

        def __init__(self, target: str) -> None:
            super().__init__()
            self.target = target

    class Selected(Message):
        def __init__(self, target: str) -> None:
            super().__init__()
            self.target = target

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, rng: random.Random | None = None,
                 motion: Motion = Motion.NORMAL, colors: str = "full", id: str | None = None) -> None:
        super().__init__(id=id)
        self.clock = clock
        self.geom = Layout(0, 0)
        self.actor = Actor(self.geom, rng or random.Random(), motion, clock())
        self.motion = motion  # what the person chose
        self.color_mode = colors
        self.still = False  # forced still (narrow screen)
        self.paused = False
        self.selected = ACTOR
        self.has_bubble = False
        self.ticks = 0
        self._frames = character_frames()
        self._flipped = {name: art.mirrored() for name, art in self._frames.items()}
        self._bench = bench_frames()
        self._background: px.Pixels | None = None
        self._strips: list[Strip] = []
        self._pose: Pose | None = None
        self._timer = None
        self._styles: dict[tuple[str | None, str | None], Style] = {}

    # -- lifecycle ----------------------------------------------------------------------

    def on_mount(self) -> None:
        self._timer = self.set_interval(FRAME_SECONDS, self.tick, pause=True)
        self._sync_timer()

    def on_resize(self, event: events.Resize) -> None:
        self.geom = Layout(event.size.width, event.size.height)
        self.actor.set_layout(self.geom)
        self._background = None
        self.redraw(force=True)

    @property
    def effective_motion(self) -> Motion:
        if self.still or self.color_mode == "text":
            return Motion.OFF
        return self.motion

    @property
    def running(self) -> bool:
        """Whether the frame timer is running now."""

        return self._timer is not None and not self.paused and self.effective_motion is not Motion.OFF

    def _sync_timer(self) -> None:
        self.actor.set_motion(self.effective_motion, self.clock())
        if self._timer is None:
            return
        if self.paused or self.effective_motion is Motion.OFF:
            self._timer.pause()
        else:
            self._timer.resume()

    def pause(self) -> None:
        self.paused = True
        self._sync_timer()

    def resume(self) -> None:
        self.paused = False
        self._sync_timer()
        self.redraw()

    def set_motion(self, motion: Motion) -> None:
        self.motion = motion
        self._sync_timer()
        self.redraw(force=True)

    def set_still(self, still: bool) -> None:
        if still != self.still:
            self.still = still
            self._sync_timer()
            self.redraw(force=True)

    def set_mood(self, mood: Mood) -> None:
        self.actor.set_mood(mood, self.clock())
        self.redraw()

    def cheer(self) -> None:
        """A short happy reaction to a passed check (presentation only)."""

        if self.actor.mood not in AT_BENCH and self.actor.activity in ("rest", "walk"):
            self.actor.activity, self.actor.target = "pose", None
            self.actor.celebrate_until = self.clock() + 2.0
            self.redraw()

    # -- frames -----------------------------------------------------------------------------

    def tick(self) -> None:
        """One animation step: move the character and redraw. Reads no state and runs nothing."""

        self.ticks += 1
        self.redraw()

    def targets(self) -> list[str]:
        out = [ACTOR]
        if self._bench_rect() is not None:
            out.append(BENCH)
        if self.has_bubble:
            out.append(BUBBLE)
        return out

    def _bench_rect(self) -> Rect | None:
        if self.color_mode == "text":
            return TextLayout(self.size.width, self.size.height, False).bench
        return self.geom.bench_rect()

    def target_rects(self) -> dict[str, Rect]:
        """Cells each target covers now, from the same layout the frame was drawn with."""

        if self.color_mode == "text":
            text = TextLayout(self.size.width, self.size.height, self.actor.mood in AT_BENCH)
            rects = {ACTOR: text.actor}
            if text.bench is not None:
                rects[BENCH] = text.bench
            return rects
        x = self._pose.x if self._pose is not None else self.actor.x
        rects = {ACTOR: self.geom.actor_rect(x)}
        bench = self.geom.bench_rect()
        if bench is not None:
            rects[BENCH] = bench
        return rects

    def target_at(self, x: int, y: int) -> str | None:
        rects = self.target_rects()
        for name in (ACTOR, BENCH):  # the character is drawn over the bench
            rect = rects.get(name)
            if rect is not None and rect.contains(x, y):
                return name
        return None

    def bubble_anchor(self, w: int, h: int) -> tuple[int, int]:
        if self.color_mode == "text":
            rect = self.target_rects()[ACTOR]
            return min(max(0, rect.x), max(0, self.size.width - w)), rect.y + rect.h
        x = self._pose.x if self._pose is not None else self.actor.x
        return self.geom.bubble_xy(x, w, h)

    def _style(self, fg: str | None, bg: str | None) -> Style:
        key = (fg, bg)
        style = self._styles.get(key)
        if style is None:
            style = self._styles[key] = Style(color=fg, bgcolor=bg)
        return style

    def compose_pixels(self, pose: Pose) -> px.Pixels:
        layout = self.geom
        if self._background is None:
            self._background = garden(layout.px_width, layout.px_height, layout.workshop_x)
        layers: list[tuple[px.Sprite, int, int]] = []
        bench_xy = layout.bench_xy
        if bench_xy is not None:
            layers.append((self._bench[pose.bench], *bench_xy))
        art = (self._flipped if pose.flip else self._frames)[pose.frame]
        layers.append((art, pose.x, layout.actor_y))
        canvas = px.compose(self._background, layers)
        rects = {ACTOR: (pose.x, layout.actor_y, art.width, art.height)}
        if bench_xy is not None:
            rects[BENCH] = (*bench_xy, self._bench[pose.bench].width, self._bench[pose.bench].height)
        if self.has_focus and self.selected in rects:
            _corners(canvas, *rects[self.selected])
        return canvas

    def _rows(self) -> list[Strip]:
        width, height = self.size.width, self.size.height
        if width <= 0 or height <= 0:
            return []
        if self.color_mode == "text":
            pose = self.actor.advance(self.clock())
            self._pose = pose
            text = TextLayout(width, height, self.actor.mood in AT_BENCH)
            lines = text.lines(pose.frame, self.selected if self.has_focus else None)
            strips = [Strip([Segment(line, PLAIN)]).adjust_cell_length(width, PLAIN) for line in lines]
            return strips + [Strip.blank(width, PLAIN)] * (height - len(strips))
        if not self.geom.fits:
            note = fit("마당을 그릴 공간이 부족함 · 요청함과 상세 화면은 그대로 사용", width)
            return [Strip([Segment(note, PLAIN)]).adjust_cell_length(width, PLAIN)] + \
                [Strip.blank(width, PLAIN)] * (height - 1)
        pose = self.actor.advance(self.clock())
        self._pose = pose
        strips = []
        for row in px.to_cells(self.compose_pixels(pose))[:height]:
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
        """Recomposes the frame and refreshes only the rows that differ from what is on screen."""

        if not self.is_mounted:
            return
        before = self._strips
        after = self._rows()
        self._strips = after
        if force or len(before) != len(after):
            self.refresh()
            return
        width = self.size.width
        for y, (old, new) in enumerate(zip(before, after)):
            if old != new:
                self.refresh(Region(0, y, width, 1))
        screen = self.screen
        if isinstance(screen, YardScreen):
            screen.place_bubble()

    def render_line(self, y: int) -> Strip:
        if not self._strips and self.size.height:
            self._strips = self._rows()
        if 0 <= y < len(self._strips):
            return self._strips[y]
        return Strip.blank(self.size.width, PLAIN)

    # -- selection and input ------------------------------------------------------------------

    def select(self, target: str) -> None:
        if target != self.selected:
            self.selected = target
            self.post_message(self.Selected(target))
            self.redraw(force=True)

    def action_move(self, step: int) -> None:
        targets = self.targets()
        index = targets.index(self.selected) if self.selected in targets else 0
        self.select(targets[(index + step) % len(targets)])

    def action_open(self) -> None:
        if self.selected not in self.targets():
            self.selected = ACTOR
        self.post_message(self.Open(self.selected))

    def on_focus(self) -> None:
        self.redraw(force=True)
        self.post_message(self.Selected(self.selected))

    def on_blur(self) -> None:
        self.redraw(force=True)

    def on_click(self, event: events.Click) -> None:
        offset = event.get_content_offset(self)
        if offset is None:
            return
        target = self.target_at(offset.x, offset.y)
        if target is None:
            return
        event.stop()
        self.select(target)
        self.post_message(self.Open(target))


def _corners(canvas: px.Pixels, x: int, y: int, w: int, h: int) -> None:
    """Selection marks: four L-shaped corners just outside the target, in pixels."""

    height = len(canvas)
    width = len(canvas[0]) if height else 0
    left, top, right, bottom = x - 1, y - 1, x + w, y + h
    for cx, cy, dx, dy in ((left, top, 1, 1), (right, top, -1, 1), (left, bottom, 1, -1), (right, bottom, -1, -1)):
        for i in range(3):
            for px_, py_ in ((cx + dx * i, cy), (cx, cy + dy * i)):
                if 0 <= px_ < width and 0 <= py_ < height:
                    canvas[py_][px_] = SELECT_COLOR


class Bubble(Static):
    """A speech bubble over the character: one request, short. Click or Enter (on the yard) opens it."""

    DEFAULT_CSS = """
    Bubble { position: absolute; height: auto; border: round $warning; background: $surface;
             padding: 0 1; display: none; }
    Bubble.selected { border: heavy $accent; }
    """

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self.request: Request | None = None
        self.box = (0, 0)

    def show_request(self, request: Request | None, stage_width: int) -> None:
        self.request = request
        if request is None:
            self.display = False
            return
        inner = max(8, min(BUBBLE_MAX, stage_width - 4))
        title = fit(f"{'?' if request.kind.value == 'end_unknown' else '!'} {request.label}", inner)
        summary = fit(request.summary, inner)
        hint = fit("Enter·클릭: 상세 · d: 닫기(읽음)", inner)
        width = max(display_width(title), display_width(summary), display_width(hint)) + 4
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append(title, style="bold")
        text.append("\n" + summary)
        text.append("\n" + hint, style="dim")
        self.update(text)
        self.styles.width = width
        self.box = (width, 5)
        self.display = True

    def on_click(self, event: events.Click) -> None:
        if self.request is not None:
            event.stop()
            self.post_message(YardView.Open(BUBBLE))


class YardScreen(Screen):
    """Header, the yard, a state line, the request inbox and the keys. Details open in the console."""

    DEFAULT_CSS = """
    YardScreen { layout: vertical; }
    #yard-header { height: auto; max-height: 2; padding: 0 1; background: $panel; text-wrap: nowrap;
                   text-overflow: ellipsis; }
    #stage { height: 1fr; min-height: 6; max-height: 20; }
    #yard-status { height: 1; padding: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }
    #yard-banner { height: auto; padding: 0 1; color: $error; display: none; }
    #inbox { height: auto; max-height: 8; border-top: solid $primary-darken-2; }
    #inbox:focus { border-top: heavy $accent; }
    #yard-keys { height: auto; max-height: 2; padding: 0 1; color: $text-muted; }
    YardScreen.narrow #stage { height: 9; min-height: 9; }
    YardScreen.narrow #inbox { height: 1fr; max-height: 100%; }
    """

    BINDINGS = [
        Binding("c", "console", "상세 콘솔"), Binding("ㅊ", "console", show=False),
        Binding("m", "motion", "움직임"), Binding("ㅡ", "motion", show=False),
        Binding("d", "dismiss_bubble", "말풍선 닫기"), Binding("ㅇ", "dismiss_bubble", show=False),
        Binding("n", "next_scene", "모의 장면"), Binding("ㅜ", "next_scene", show=False),
        Binding("escape", "noop", show=False),
    ]

    class OpenRequest(Message):
        """Open the detail behind a request (``request``) or a task (``task_id``) or the task list."""

        def __init__(self, tab: str, task_id: str | None, rid: str | None = None) -> None:
            super().__init__()
            self.tab = tab
            self.task_id = task_id
            self.rid = rid

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, rng: random.Random | None = None,
                 motion: Motion = Motion.NORMAL, colors: str = "auto", demo: bool = False) -> None:
        super().__init__(name="yard")
        self._clock, self._rng, self._motion, self._colors = clock, rng, motion, colors
        self.demo = demo
        self.book = RequestBook()
        self.model: YardModel | None = None
        self.tasks: tuple[tuple[str, str], ...] = ()  # (task_id, one-line state) for the narrow list
        self.scene: int | None = None  # demo scene index, or None for the snapshot
        self._header = ""
        self._options: tuple = ()
        self._last_focus: str | None = "yard"

    def compose(self) -> ComposeResult:
        yield Static(id="yard-header")
        with Container(id="stage"):
            colors = self._colors
            if colors == "auto":
                colors = colors_for(self.app.console.color_system, getattr(self.app, "no_color", False))
            yield YardView(clock=self._clock, rng=self._rng, motion=self._motion, colors=colors, id="yard")
            yield Bubble(id="bubble")
        yield Static(id="yard-status")
        yield Static(id="yard-banner")
        yield OptionList(id="inbox")
        yield Static(id="yard-keys")

    @property
    def view(self) -> YardView:
        return self.query_one("#yard", YardView)

    def on_mount(self) -> None:
        self.query_one("#inbox", OptionList).border_title = "요청함"
        self._apply_size(self.app.size.width, self.app.size.height)
        self.view.focus()
        self._paint()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_size(event.size.width, event.size.height)

    def _apply_size(self, width: int, height: int) -> None:
        narrow = width < NARROW_WIDTH or height < SHORT_HEIGHT
        self.set_class(narrow, "narrow")
        self.view.set_still(narrow)
        self._paint()

    @property
    def narrow(self) -> bool:
        return self.has_class("narrow")

    # -- state in -------------------------------------------------------------------------

    def show(self, model: YardModel, header: str, tasks: tuple[tuple[str, str], ...] = ()) -> None:
        """New state from the console's refresh or run events. The yard keeps only presentation state."""

        self.model = model
        self._header = header
        self.tasks = tasks
        self.book.update(model.requests)
        if self.is_mounted:
            self.view.set_mood(model.mood)
            self._paint()

    def set_header(self, header: str) -> None:
        self._header = header
        if self.is_mounted:
            self.query_one("#yard-header", Static).update(Text(header, no_wrap=True, overflow="ellipsis"))

    def cheer(self) -> None:
        if self.is_mounted:
            self.view.cheer()

    def _paint(self) -> None:
        if not self.is_mounted:
            return
        model = self.model
        view = self.view
        self.query_one("#yard-header", Static).update(Text(self._header, no_wrap=True, overflow="ellipsis"))
        bubble = self.book.bubble()
        view.has_bubble = bubble is not None
        if view.selected == BUBBLE and bubble is None:
            view.selected = ACTOR
        self.query_one("#bubble", Bubble).show_request(bubble, view.size.width or self.app.size.width)
        self.query_one("#bubble", Bubble).set_class(view.selected == BUBBLE and view.has_focus, "selected")
        self.place_bubble()

        status = Text(no_wrap=True, overflow="ellipsis")
        if model is None:
            status.append("… 조회 전", style="dim")
        else:
            style = {"ok": "green", "bad": "red", "warn": "yellow", "muted": "dim"}.get(model.tag, "")
            status.append(model.label, style=style)
        status.append(f"  │  선택 ▶ {TARGET_NAMES.get(view.selected, '')} (Enter 상세)", style="bold cyan")
        self.query_one("#yard-status", Static).update(status)

        banner = self.query_one("#yard-banner", Static)
        note = model.stale_note if model is not None else None
        banner.display = bool(note)
        banner.update(Text(f"⚠ {note}" if note else ""))

        self._render_inbox()
        keys = ("Tab 영역 이동 · ←→ 대상 선택 · Enter·클릭 상세 · c 상세 콘솔 · 1-4 상세 탭 · d 말풍선 닫기 · "
                f"m 움직임 {view.motion.label}" + (" (좁은 화면·색상 제한: 정지)" if view.effective_motion is
                                                     Motion.OFF and view.motion is not Motion.OFF else "")
                + (" · n 모의 장면" if self.demo else "") + " · r 새로고침 · ? 도움말 · q 종료")
        self.query_one("#yard-keys", Static).update(Text(keys))

    def _render_inbox(self) -> None:
        inbox = self.query_one("#inbox", OptionList)
        open_ = self.book.open
        unread = sum(1 for r in open_ if not self.book.is_read(r.rid))
        inbox.border_title = f"요청함 · 미처리 {len(open_)}건" + (f" (안 읽음 {unread})" if open_ else "")
        options: list[tuple[str, str, bool]] = []
        for r in open_:
            mark = "·" if self.book.is_read(r.rid) else "●"
            read = " (읽음 · 해결 아님)" if self.book.is_read(r.rid) else ""
            options.append((f"req:{r.rid}", f"{mark} [{r.label}] {r.summary}{read}", False))
        if not open_:
            options.append(("none", "미처리 요청 없음", True))
        if self.narrow:
            for task_id, line in self.tasks:
                options.append((f"task:{task_id}", f"작업 {task_id} · {line}", False))
            options.append(("list", "작업 목록 열기", False))
        key = tuple(options)
        if key == self._options:
            return
        self._options = key
        highlighted = inbox.highlighted_option.id if inbox.highlighted_option is not None else None
        inbox.set_options([Option(text, id=oid, disabled=disabled) for oid, text, disabled in options])
        ids = [oid for oid, _, disabled in options if not disabled]
        if highlighted in ids:
            inbox.highlighted = [oid for oid, _, _ in options].index(highlighted)

    def place_bubble(self) -> None:
        try:
            bubble = self.query_one("#bubble", Bubble)
        except Exception:
            return
        if not bubble.display:
            return
        w, h = bubble.box
        x, y = self.view.bubble_anchor(w, h)
        if (x, y) != tuple(bubble.styles.offset):
            bubble.styles.offset = (x, y)

    # -- input --------------------------------------------------------------------------------

    def _open_target(self, target: str) -> None:
        model = self.model
        if target == BUBBLE:
            request = self.book.bubble()
            if request is not None:
                self.book.mark_read(request.rid)
                self.post_message(self.OpenRequest(request.tab, request.task_id, request.rid))
            return
        task = model.task_id if model is not None else None
        self.post_message(self.OpenRequest("tab-tasks", task))

    @on(YardView.Open)
    def _opened(self, event: YardView.Open) -> None:
        event.stop()
        self._open_target(event.target)

    @on(YardView.Selected)
    def _selected(self, event: YardView.Selected) -> None:
        event.stop()
        self._paint()

    @on(OptionList.OptionSelected, "#inbox")
    def _inbox_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        oid = event.option.id or ""
        if oid.startswith("req:"):
            rid = oid[4:]
            request = next((r for r in self.book.open if r.rid == rid), None)
            if request is not None:
                self.book.mark_read(rid)
                self.post_message(self.OpenRequest(request.tab, request.task_id, rid))
        elif oid.startswith("task:"):
            self.post_message(self.OpenRequest("tab-tasks", oid[5:]))
        elif oid == "list":
            self.post_message(self.OpenRequest("tab-tasks", None))

    def action_console(self) -> None:
        self.post_message(self.OpenRequest("tab-tasks", None))

    def action_motion(self) -> None:
        view = self.view
        view.set_motion(view.motion.next())
        self.app.notify(f"움직임: {view.motion.label}", timeout=3)
        self._paint()

    def action_dismiss_bubble(self) -> None:
        request = self.book.bubble()
        if request is not None:
            self.book.mark_read(request.rid)  # read, not resolved: it stays in the inbox
            self._paint()

    def action_next_scene(self) -> None:
        if not self.demo:
            return
        app = self.app
        if hasattr(app, "next_yard_scene"):
            app.next_yard_scene()

    def action_noop(self) -> None:
        pass

    def check_action(self, action: str, parameters) -> bool | None:
        if action == "next_scene":
            return self.demo
        return True

    # -- hidden or shown ----------------------------------------------------------------------

    def on_screen_suspend(self) -> None:
        focused = self.focused
        self._last_focus = focused.id if focused is not None else None
        self.view.pause()

    def on_screen_resume(self) -> None:
        self.view.resume()
        target = self._last_focus or "yard"
        try:
            self.query_one(f"#{target}").focus()
        except Exception:
            self.view.focus()
        self._paint()
