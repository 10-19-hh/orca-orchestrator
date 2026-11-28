"""Interactive console for an Orca terminal tab (Textual). The only module that imports Textual.

Reading (refresh, preflight) runs in daemon threads and never blocks keys.
Refreshes are serialized: a refresh asked for while one runs is coalesced
into one more after it, so key repeats never pile up processes and an older
result never replaces a newer one. A failed refresh keeps the last snapshot
on screen but marks it as not current; so does a refresh still running after
``STALL_SECONDS``, whose late result is then discarded.

An action that records something (verify, flow, approve, hold) runs one at
a time in its own thread. Quit policy while one runs:

* q, Ctrl+C or Ctrl+Q ask: keep watching, close when it ends, or stop and
  close. Stopping kills the test process; whether processes it started ended
  is not confirmed, so the run is recorded with an unconfirmed end and waits
  for the person's confirmation (``handoff --confirm-stopped``), like a timeout.
* Pressing Ctrl+C again while stopping closes without waiting for the record;
  the record then reads as cut off, which also needs confirmation.
* SIGTERM and SIGHUP stop the run the same way and close.

Nothing here starts a Worker, and approving a change never touches the Worker
dispatch gate.

With ``yard=True`` (the default of ``python -m orca_orchestrator``) the pixel
yard (``yard_screen``) is pushed over this screen. The yard only shows state
it is given here and opens these tabs as its detail screens; Esc on a detail
opened from the yard goes back to it. ``--console`` opens the tabs directly.
"""

from __future__ import annotations

import asyncio
import os
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Checkbox, DataTable, Footer, Input, RichLog, Static, TabbedContent, TabPane

from ..orca.dispatch import Provider
from ..tasks.review import review_basis
from ..tasks.verification import SUPPORTED_CHECKS
from ..tui import render as r
from ..tui.render import fit, local_time
from . import view as v
from .bridge import WORKER_NOTE, ActionOutcome, Availability, Console
from .motion import Motion
from .yard import DEMO_SCENES, YardInput, demo_scene, yard_model
from .yard_screen import YardScreen

STYLES = {"h": "bold", "ok": "green", "bad": "red", "warn": "yellow", "muted": "dim", "key": "bold cyan", "": ""}
OUTCOME_TAGS = {"ok": "ok", "failed": "bad", "refused": "warn", "not_completed": "warn", "not_recorded": "bad",
                "busy": "warn", "unresolved": "warn", "stopped": "warn", "blocked": "warn", "error": "bad"}
SEVERITY = {"ok": "information", "failed": "error", "not_recorded": "error", "error": "error"}
TITLE_WIDTH = 34
FORCE_EXIT_AFTER_SIGNAL_SECONDS = 15.0
# A read-only refresh or preflight still running after this long is given up (Orca CLI and git calls
# have 10-15 s timeouts, but a child that keeps its pipes open can outlive them).
STALL_SECONDS = 45.0
TABLE_COLUMNS = ((" ", "mark"), ("그래프", "id"), ("상태", "state"), ("검증", "checks"), ("선행", "deps"),
                 ("실행", "run"), ("이름", "title"))
KEY_ACTIONS = {"v": "verify", "ㅍ": "verify", "s": "flow", "ㄴ": "flow", "a": "approve", "ㅁ": "approve",
               "h": "hold", "ㅗ": "hold", "p": "preflight", "ㅔ": "preflight"}

FOCUS_NAMES = {
    "task-table": "작업 목록",
    "detail-pane": "작업 상세",
    "review-pane": "변경 검토",
    "recovery-pane": "복구",
    "preflight-pane": "전송 전 점검",
    "events": "진행·결과",
}

# Console actions that do nothing while the yard is on screen (they run, record, filter or change the gate view).
YARD_BLOCKED = frozenset({"verify", "flow", "approve", "hold", "preflight", "provider", "stop", "filter", "back"})

YARD_HELP_LINES = [
    ("h", "마당 (기본 화면)"),
    ("", "Tab / Shift+Tab 마당·요청함 이동 · ←→↑↓ 캐릭터·작업대·말풍선 선택 · Enter 또는 클릭 상세 열기"),
    ("", "c 상세 콘솔 · 1~4 상세 탭 바로 열기 · d 말풍선 닫기(읽음 표시일 뿐 해결 아님)"),
    ("", "m 움직임 보통 → 줄이기 → 정지 · 데모에서 n 모의 장면 (대기→시작→작업→확인 요청→검증 통과)"),
    ("", "상세 화면에서 Esc 또는 y로 마당에 돌아옴 · 마당에서는 검증·흐름·승인이 실행되지 않음"),
    ("muted", "클릭과 Enter는 상세만 엽니다. 실행·승인·보류는 상세 화면의 기존 확인 절차로만 기록됩니다."),
    ("", ""),
]

HELP_LINES = [
    ("h", "단축키 (한글 입력 상태에서도 같은 자리의 키가 동작)"),
    ("", "r 새로고침 · q 종료 (Ctrl+C, Ctrl+Q도 같음) · ? 도움말"),
    ("", "1 작업 · 2 변경 검토 · 3 복구 · 4 전송 전 점검 (탭 이동)"),
    ("", "Tab / Shift+Tab 포커스 이동 · ↑↓ PgUp PgDn Home End 이동·스크롤"),
    ("", ""),
    ("h", "작업 탭"),
    ("", "f / F 상태 필터 · Enter 상세로 이동 · Esc 목록으로"),
    ("", "v 선택한 작업의 지원되는 로컬 검증 실행 (실행 전 확인)"),
    ("", "s 로컬 검증 흐름 시작·이어가기 (실행 전 확인)"),
    ("", "x 실행 중지 (테스트 프로세스 중지 · 종료 미확인으로 기록)"),
    ("", ""),
    ("h", "변경 검토 탭"),
    ("", "a 최종 승인 (검토한 상태가 그대로일 때만 기록) · h 사유를 입력해 보류"),
    ("muted", WORKER_NOTE),
    ("", ""),
    ("h", "전송 전 점검 탭"),
    ("", "p 점검 실행 (읽기 전용) · o Provider 전환 · 실제 전송 동작은 없음"),
    ("", ""),
    ("muted", "Esc, q 또는 ?로 닫기"),
]


def to_text(lines: list[tuple[str, str]], *, wrap: bool = True) -> Text:
    text = Text(no_wrap=not wrap, overflow="fold" if wrap else "ellipsis")
    for i, (tag, line) in enumerate(lines):
        if i:
            text.append("\n")
        text.append(line, style=STYLES.get(tag, ""))
    return text


# --- messages from worker threads --------------------------------------------------------


class SnapshotReady(Message):
    def __init__(self, gen: int, snapshot, error: str | None, finished: datetime) -> None:
        super().__init__()
        self.gen = gen
        self.snapshot = snapshot
        self.error = error
        self.finished = finished


class ActionProgress(Message):
    def __init__(self, kind: str, payload) -> None:
        super().__init__()
        self.kind = kind
        self.payload = payload


class ActionFinished(Message):
    def __init__(self, outcome: ActionOutcome) -> None:
        super().__init__()
        self.outcome = outcome


class PreflightFinished(Message):
    def __init__(self, outcome: ActionOutcome, token: int = 0) -> None:
        super().__init__()
        self.outcome = outcome
        self.token = token


@dataclass
class RunningAction:
    name: str
    label: str
    stop: threading.Event
    started: float
    task: str | None = None
    check: str | None = None
    pid: int | None = None


# --- dialogs ---------------------------------------------------------------------------------


DIALOG_CSS = """
#dialog { width: 90%; max-width: 110; height: auto; max-height: 90%; border: thick $accent;
          background: $surface; padding: 0 1; }
#dialog-title { text-style: bold; padding: 0 0 1 0; }
#dialog-body { height: auto; max-height: 20; }
#dialog-keys { color: $text-muted; padding: 1 0 0 0; }
#dialog-buttons { height: auto; padding: 1 0 0 0; }
#dialog-buttons Button { margin: 0 1 0 0; }
#dialog Input { margin: 1 0 0 0; }
#dialog Checkbox { margin: 1 0 0 0; }
"""


class Dialog(ModalScreen):
    """A modal that answers once, so a repeated key cannot dismiss it twice."""

    DEFAULT_CSS = "Dialog { align: center middle; }\n" + DIALOG_CSS

    def __init__(self) -> None:
        super().__init__()
        self._answered = False

    def answer(self, value) -> None:
        if self._answered:
            return
        self._answered = True
        self.dismiss(value)


class ConfirmScreen(Dialog):
    BINDINGS = [
        Binding("y", "confirm", "실행"), Binding("ㅛ", "confirm", show=False),
        Binding("n", "cancel", "취소"), Binding("ㅜ", "cancel", show=False),
        Binding("escape", "cancel", "취소", show=False),
    ]

    def __init__(self, title: str, lines: list[tuple[str, str]], confirm_label: str) -> None:
        super().__init__()
        self._title = title
        self._lines = lines
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._title, id="dialog-title")
            with VerticalScroll(id="dialog-body"):
                yield Static(to_text(self._lines))
            yield Static("y 실행 · n 또는 Esc 취소 · Tab으로 버튼 이동", id="dialog-keys")
            with Horizontal(id="dialog-buttons"):
                yield Button(self._confirm_label, id="confirm", variant="primary")
                yield Button("취소", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    @on(Button.Pressed, "#confirm")
    def action_confirm(self) -> None:
        self.answer(True)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.answer(False)


class ApproveScreen(Dialog):
    """Final approval of the change. Unlinked changes need an explicit tick."""

    BINDINGS = [Binding("escape", "cancel", "취소")]

    def __init__(self, lines: list[tuple[str, str]], unlinked: tuple[str, ...]) -> None:
        super().__init__()
        self._lines = lines
        self._unlinked = unlinked

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("최종 승인 기록", id="dialog-title")
            with VerticalScroll(id="dialog-body"):
                yield Static(to_text(self._lines))
            if self._unlinked:
                yield Checkbox(f"연결된 작업이 없는 변경 {len(self._unlinked)}개의 내용을 확인했습니다", id="unlinked")
            yield Input(placeholder="메모 (선택)", id="note")
            yield Static("Tab으로 이동 · Space로 확인 표시 · Esc 취소", id="dialog-keys")
            with Horizontal(id="dialog-buttons"):
                yield Button("승인 기록", id="confirm", variant="success", disabled=bool(self._unlinked))
                yield Button("취소", id="cancel")

    def on_mount(self) -> None:
        (self.query_one("#unlinked", Checkbox) if self._unlinked else self.query_one("#note", Input)).focus()

    @on(Checkbox.Changed, "#unlinked")
    def _ticked(self, event: Checkbox.Changed) -> None:
        self.query_one("#confirm", Button).disabled = not event.value

    @on(Button.Pressed, "#confirm")
    def _confirm(self) -> None:
        confirmed = bool(self._unlinked) and self.query_one("#unlinked", Checkbox).value
        if self._unlinked and not confirmed:
            return
        self.answer((confirmed, self.query_one("#note", Input).value))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.answer(None)


class HoldScreen(Dialog):
    BINDINGS = [Binding("escape", "cancel", "취소")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("보류 기록", id="dialog-title")
            yield Static(to_text([("", "보류 사유를 입력합니다. 사유는 승인 기록 파일에 남고 상태 화면에 표시됩니다."),
                                  ("muted", "보류는 언제든 기록할 수 있으며 승인하려면 사유를 해결한 뒤 다시 승인합니다.")]))
            yield Input(placeholder="보류 사유 (필수)", id="reason")
            yield Static("Enter 또는 '보류 기록'으로 기록 · Esc 취소", id="dialog-keys")
            with Horizontal(id="dialog-buttons"):
                yield Button("보류 기록", id="confirm", variant="warning", disabled=True)
                yield Button("취소", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#reason", Input).focus()

    @on(Input.Changed, "#reason")
    def _typed(self, event: Input.Changed) -> None:
        self.query_one("#confirm", Button).disabled = not event.value.strip()

    @on(Input.Submitted, "#reason")
    @on(Button.Pressed, "#confirm")
    def _confirm(self) -> None:
        reason = self.query_one("#reason", Input).value.strip()
        if reason:
            self.answer(reason)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.answer(None)


class QuitScreen(Dialog):
    BINDINGS = [Binding("escape", "stay", "계속 보기")]

    def __init__(self, lines: list[tuple[str, str]]) -> None:
        super().__init__()
        self._lines = lines

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("실행 중에 닫기", id="dialog-title")
            yield Static(to_text(self._lines))
            yield Static("Esc 계속 보기 · Ctrl+C를 한 번 더 누르면 중지하고 닫기", id="dialog-keys")
            with Horizontal(id="dialog-buttons"):
                yield Button("계속 보기", id="stay", variant="primary")
                yield Button("끝나면 닫기", id="after")
                yield Button("중지하고 닫기", id="stop", variant="error")

    def on_mount(self) -> None:
        self.query_one("#stay", Button).focus()

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.answer(event.button.id)

    def action_stay(self) -> None:
        self.answer("stay")


class HelpScreen(Dialog):
    BINDINGS = [Binding("escape", "close", "닫기"), Binding("q", "close", show=False),
                Binding("question_mark", "close", show=False), Binding("ㅂ", "close", show=False)]

    def __init__(self, yard: bool = False) -> None:
        super().__init__()
        self._yard = yard

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("도움말", id="dialog-title")
            with VerticalScroll(id="dialog-body"):
                yield Static(to_text((YARD_HELP_LINES if self._yard else []) + HELP_LINES))

    def action_close(self) -> None:
        self.answer(None)


# --- the application ----------------------------------------------------------------------------


class ConsoleApp(App[int]):
    TITLE = "Orca Orchestrator"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { layout: vertical; }
    #header { height: auto; max-height: 4; padding: 0 1; background: $panel; text-wrap: nowrap; text-overflow: ellipsis; }
    #tabs { height: 1fr; }
    #tabs TabPane { padding: 0; }
    #tasks-pane { layout: horizontal; height: 1fr; }
    #list-pane { width: 70; min-width: 40; border-top: solid $primary-darken-2; }
    #detail-pane { width: 1fr; border-top: solid $primary-darken-2; padding: 0 1; }
    #filter-bar { height: 1; color: $text-muted; padding: 0 1; }
    #task-table { height: 1fr; }
    #review-pane, #recovery-pane, #preflight-pane { padding: 0 1; }
    #bottom { height: 6; border-top: solid $primary-darken-2; }
    #progress-line { height: 1; padding: 0 1; text-wrap: nowrap; text-overflow: ellipsis; }
    #events { height: 1fr; padding: 0 1; }
    #list-pane:focus-within, #detail-pane:focus, #bottom:focus-within { border-top: heavy $accent; }
    .narrow #tasks-pane { layout: vertical; }
    .narrow #list-pane { width: 1fr; min-width: 0; height: 9; }
    .narrow #detail-pane { width: 1fr; height: 1fr; }
    .narrow.detail-only #list-pane { display: none; }
    .short #bottom { height: 4; }
    .narrow.short #list-pane { height: 7; }
    .narrow.short #bottom { height: 3; }
    .idle #bottom { height: 2; }
    .idle #events { display: none; }
    """

    BINDINGS = [
        Binding("r", "refresh", "새로고침"), Binding("ㄱ", "refresh", show=False),
        Binding("f", "filter(1)", "필터"), Binding("F", "filter(-1)", show=False),
        Binding("ㄹ", "filter(1)", show=False),
        Binding("v", "verify", "검증 실행"), Binding("ㅍ", "verify", show=False),
        Binding("s", "flow", "흐름 실행"), Binding("ㄴ", "flow", show=False),
        Binding("x", "stop", "실행 중지"), Binding("ㅌ", "stop", show=False),
        Binding("a", "approve", "최종 승인"), Binding("ㅁ", "approve", show=False),
        Binding("h", "hold", "보류"), Binding("ㅗ", "hold", show=False),
        Binding("p", "preflight", "점검 실행"), Binding("ㅔ", "preflight", show=False),
        Binding("o", "provider", "Provider"), Binding("ㅐ", "provider", show=False),
        Binding("1", "tab('tab-tasks')", show=False), Binding("2", "tab('tab-review')", show=False),
        Binding("3", "tab('tab-recovery')", show=False), Binding("4", "tab('tab-preflight')", show=False),
        Binding("escape", "back", show=False),
        Binding("y", "yard", "마당"), Binding("ㅛ", "yard", show=False),
        Binding("question_mark", "help", "도움말"),
        Binding("q", "request_quit", "종료"), Binding("ㅂ", "request_quit", show=False),
        Binding("ctrl+c", "request_quit", show=False, priority=True),
        Binding("ctrl+q", "request_quit", show=False, priority=True),
    ]

    def __init__(self, ops: Console, *, yard: bool = False, motion: Motion = Motion.NORMAL,
                 clock=time.monotonic, rng=None, colors: str = "auto") -> None:
        super().__init__()
        self.ops = ops  # not 'console': Textual's App uses that name for its Rich console
        self.yard: YardScreen | None = (
            YardScreen(clock=clock, rng=rng, motion=motion, colors=colors, demo=bool(ops.target.demo))
            if yard else None)
        self.from_yard = False  # the tabs on screen were opened from the yard: Esc goes back to it
        self.yard_scene: int | None = None  # demo only: a mock scene shown instead of the snapshot
        self.local_run: tuple[str, str] | None = None  # (task, check) between a start event and its result
        self.snapshot = None
        self.shown_at: datetime | None = None
        self.failed: tuple[datetime, str] | None = None
        self._inflight: int | None = None  # generation of the refresh running now
        self._inflight_since = 0.0
        self._refresh_again = False
        self._gen = 0
        self._applied = 0
        self.selected: str | None = None
        self._selected_index = 0
        self.filter_index = 0
        self.action_state: RunningAction | None = None
        self.last_outcome: ActionOutcome | None = None
        self.quit_when_idle = False
        self.stopping = False
        self.provider = Provider.CLAUDE
        self.preflight_running = False
        self._preflight_token = 0
        self._preflight_since = 0.0
        self.preflight_outcome: ActionOutcome | None = None
        self._signals: list[int] = []
        self._logged = False  # the bottom log stays folded until something is written to it

    # -- layout ------------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with TabbedContent(id="tabs", initial="tab-tasks"):
            with TabPane("1 작업", id="tab-tasks"):
                with Container(id="tasks-pane"):
                    with Vertical(id="list-pane"):
                        yield Static(id="filter-bar")
                        yield DataTable(id="task-table", cursor_type="row", zebra_stripes=False)
                    with VerticalScroll(id="detail-pane"):
                        yield Static(id="task-detail")
            with TabPane("2 변경 검토", id="tab-review"):
                with VerticalScroll(id="review-pane"):
                    yield Static(id="review-body")
            with TabPane("3 복구", id="tab-recovery"):
                with VerticalScroll(id="recovery-pane"):
                    yield Static(id="recovery-body")
            with TabPane("4 전송 전 점검", id="tab-preflight"):
                with VerticalScroll(id="preflight-pane"):
                    yield Static(id="preflight-body")
        with Vertical(id="bottom"):
            yield Static(id="progress-line")
            yield RichLog(id="events", wrap=True, markup=False, highlight=False, max_lines=500)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#task-table", DataTable)
        self.query_one("#list-pane").border_title = "작업 목록"
        self.query_one("#detail-pane").border_title = "작업 상세"
        self.query_one("#bottom").border_title = "진행·결과"
        self._apply_size(self.size.width, self.size.height)
        self.watch(self.screen, "focused", self._focus_changed)
        self.set_interval(1.0, self._tick)
        self._install_signals()
        table.focus()
        self._render_all()
        if self.yard is not None:
            self.install_screen(self.yard, "yard")
            self.push_screen("yard")
            self._update_yard()
        self.request_refresh()

    def on_resize(self, event) -> None:
        self._apply_size(event.size.width, event.size.height)
        self.call_after_refresh(self._rewrap)

    def _rewrap(self) -> None:
        self._render_detail()
        self._render_review()
        self._render_recovery()
        self._render_preflight()

    def _apply_size(self, width: int, height: int) -> None:
        screen = self.screen
        screen.set_class(width < 100, "narrow")
        screen.set_class(height < 30, "short")
        if width >= 100:
            screen.remove_class("detail-only")

    def _focus_changed(self, focused: Widget | None) -> None:
        if focused is not None and focused.id == "detail-pane" and self.screen.has_class("narrow"):
            self.screen.add_class("detail-only")
        elif focused is not None and focused.id == "task-table":
            self.screen.remove_class("detail-only")
        self._render_progress()

    def focus_name(self) -> str:
        focused = self.screen.focused
        if focused is None:
            return "없음"
        if focused.id in FOCUS_NAMES:
            return FOCUS_NAMES[focused.id]
        return "탭 목록" if "Tabs" in type(focused).__name__ else type(focused).__name__

    # -- state helpers ---------------------------------------------------------------------------

    def busy(self) -> str | None:
        return self.action_state.label if self.action_state is not None else None

    def stale(self) -> bool:
        return self.failed is not None

    def availability(self, name: str) -> Availability:
        c, snap, busy, stale = self.ops, self.snapshot, self.busy(), self.stale()
        if name == "verify":
            return c.verify_availability(snap, self.visible_selection(), busy, stale)
        if name == "flow":
            return c.flow_availability(snap, busy, stale)
        if name == "approve":
            return c.approve_availability(snap, busy, stale)
        if name == "hold":
            return c.hold_availability(snap, busy, stale)
        if name == "preflight":
            if self.preflight_running:
                return Availability(False, "점검 중")
            return Availability(True, "읽기 전용 (status·run-current만 조회)")
        return Availability(True, "")

    def active_tab(self) -> str:
        try:
            return self.query_one("#tabs", TabbedContent).active
        except Exception:
            return "tab-tasks"

    def visible_rows(self) -> list[v.TaskRow]:
        if self.snapshot is None:
            return []
        keep = v.FILTERS[self.filter_index][1]
        return [row for row in v.task_rows(self.snapshot.tasks) if keep(row)]

    def visible_selection(self) -> str | None:
        ids = [row.task_id for row in self.visible_rows()]
        return self.selected if self.selected in ids else None

    def check_action(self, action: str, parameters) -> bool | None:
        if action == "yard":
            return self.yard is not None and not self.on_yard()
        if self.on_yard() and action in YARD_BLOCKED:
            return False  # the yard shows state and opens details; it never runs, records or switches the Provider
        tab = self.active_tab()
        if action == "filter":
            return tab == "tab-tasks"
        if action in ("verify", "flow"):
            if tab != "tab-tasks":
                return False
            return True if self.availability(action).enabled else None
        if action in ("approve", "hold"):
            if tab != "tab-review":
                return False
            return True if self.availability(action).enabled else None
        if action == "preflight":
            if tab != "tab-preflight":
                return False
            return True if self.availability(action).enabled else None
        if action == "provider":
            return tab == "tab-preflight"
        if action == "stop":
            return self.action_state is not None
        return True

    # -- refresh ----------------------------------------------------------------------------------

    @property
    def refreshing(self) -> bool:
        return self._inflight is not None

    def request_refresh(self) -> None:
        """Starts a read-only refresh, or asks for one more after the one running."""

        if self.refreshing:
            self._refresh_again = True
            return
        self._gen += 1
        gen = self._gen
        self._inflight, self._inflight_since = gen, time.monotonic()
        ops = self.ops

        def work() -> None:
            try:
                snap, error = ops.collect(), None
            except Exception as exc:  # shown as a failed refresh; the old snapshot is marked stale
                snap, error = None, f"{type(exc).__name__}: {exc}"
            self.post_message(SnapshotReady(gen, snap, error, datetime.now(timezone.utc)))

        threading.Thread(target=work, name="console-refresh", daemon=True).start()
        self._render_header()

    def on_snapshot_ready(self, message: SnapshotReady) -> None:
        if message.gen != self._inflight:
            return  # a refresh given up as stalled, or an older one: never shown over newer state
        self._inflight = None
        if message.gen > self._applied:
            self._applied = message.gen
            if message.error is not None:
                self.failed = (message.finished, message.error)
                self._log("bad", f"조회 실패 · {message.error} · 표시 내용은 이전 조회 결과입니다")
            else:
                self.snapshot = message.snapshot
                self.shown_at = message.finished
                self.failed = None
            self._render_all()
        if self._refresh_again:
            self._refresh_again = False
            self.request_refresh()
        else:
            self._render_header()

    # -- rendering ----------------------------------------------------------------------------------

    def _stale_banner(self) -> list[tuple[str, str]]:
        if self.failed is None:
            return []
        when = self.shown_at.astimezone().strftime("%H:%M:%S") if self.shown_at else "없음"
        return [("bad", f"이전 조회 결과 ({when} 기준) · 최신이 아님 · 조회 실패: {self.failed[1]}"), ("", "")]

    def _wrap(self, lines: list[tuple[str, str]], pane: str) -> list[tuple[str, str]]:
        """Wraps to the pane so continuation lines stay under the value column (Rich would restart at 0)."""

        try:
            widget = self.query_one(pane)
        except Exception:
            return lines
        width = widget.content_size.width - widget.scrollbar_size_vertical - 1
        if width <= 0:  # not laid out yet; or a hidden tab: use the screen width
            width = self.size.width - 4
        return v.wrap_lines(lines, width)

    def _render_all(self) -> None:
        self._render_header()
        self._render_tasks()
        self._render_review()
        self._render_recovery()
        self._render_preflight()
        self._render_progress()
        self.refresh_bindings()
        self._update_yard()

    # -- the yard (presentation only) ----------------------------------------------------------------

    def on_yard(self) -> bool:
        return self.yard is not None and self.screen is self.yard

    def yard_header(self) -> str:
        target, snap = self.ops.target, self.snapshot
        if target.demo:
            mode = "모의 상태 · 실제 CLI·실행·기록 없음"
        else:
            mode = "실제 연결"
        if self.yard_scene is not None:
            mode += f" · 모의 장면 {self.yard_scene + 1}/{len(DEMO_SCENES)} {DEMO_SCENES[self.yard_scene][0]} (n 다음)"
        parts = [f"Orca Orchestrator · {target.repo_name}", mode]
        if snap is not None:
            parts.append(f"{r.runtime_text(snap.runtime)} · 바인딩 {'있음' if snap.bound else '없음'}")
            parts.append("Worker 전송 차단 (이 빌드)")
        when = self.shown_at.astimezone().strftime("%H:%M:%S") if self.shown_at else "없음"
        fresh = "조회 중…" if self.refreshing and self.shown_at is None else f"조회 {when}"
        if self.failed is not None:
            fresh += " · 최신 아님"
        elif self.refreshing:
            fresh += " · 다시 조회 중"
        parts.append(fresh)
        return " │ ".join(parts)

    def _update_yard(self) -> None:
        if self.yard is None:
            return
        snap = self.snapshot
        if self.yard_scene is not None:
            model = demo_scene(self.yard_scene)
        else:
            model = yard_model(YardInput(
                board=snap.tasks if snap is not None else None,
                decisions=snap.decisions if snap is not None else {},
                local_run=self.local_run,
                stale=self.stale(),
                shown_at=self.shown_at,
                demo=bool(self.ops.target.demo),
            ))
        rows = v.task_rows(snap.tasks) if snap is not None else []
        tasks = tuple((row.task_id, row.label + (f" · {row.run_note}" if row.run_note else "")) for row in rows)
        self.yard.show(model, self.yard_header(), tasks)

    def next_yard_scene(self) -> None:
        """Demo only: steps through mock scenes, then back to the demo snapshot. Nothing runs or is recorded."""

        if not self.ops.target.demo:
            return
        nxt = 0 if self.yard_scene is None else self.yard_scene + 1
        self.yard_scene = nxt if nxt < len(DEMO_SCENES) else None
        self._update_yard()

    def on_yard_screen_open_request(self, message: YardScreen.OpenRequest) -> None:
        self.open_from_yard(message.tab, message.task_id)

    def open_from_yard(self, tab: str, task_id: str | None = None) -> None:
        """Shows a detail tab (and the task) for something picked in the yard. Opening runs nothing."""

        if self.on_yard():
            self.pop_screen()
        self.from_yard = True
        if task_id:
            ids = [row.task_id for row in self.visible_rows()]
            if task_id not in ids:
                self.filter_index = 0
            if self.snapshot is not None and task_id in [row.task_id for row in self.visible_rows()]:
                self.selected = task_id
                self._render_tasks()
        self.action_tab(tab)
        if tab == "tab-tasks" and task_id and self.visible_selection() == task_id:
            self.query_one("#detail-pane", VerticalScroll).focus()
        self.refresh_bindings()

    def action_yard(self) -> None:
        if self.yard is None or self.on_yard() or isinstance(self.screen, ModalScreen):
            return
        self.from_yard = False
        self.screen.remove_class("detail-only")
        self.push_screen("yard")
        self._update_yard()

    def on_app_blur(self) -> None:
        if self.yard is not None and self.yard.is_mounted:
            self.yard.view.pause()

    def on_app_focus(self) -> None:
        if self.on_yard() and self.yard.is_mounted:
            self.yard.view.resume()

    def _render_header(self) -> None:
        info = v.RefreshInfo(self.shown_at, self.refreshing,
                             self.failed[0] if self.failed else None, self.failed[1] if self.failed else None)
        lines = v.header_lines(self.snapshot, self.ops.target, info)
        if self.snapshot is not None and self.snapshot.tasks.label:
            tag, first = lines[0]
            lines[0] = (tag, f"{first} · 작업 정의 {self.snapshot.tasks.label}")
        self.query_one("#header", Static).update(to_text(lines, wrap=False))
        if self.yard is not None:
            self.yard.set_header(self.yard_header())

    def _render_tasks(self) -> None:
        table = self.query_one("#task-table", DataTable)
        rows = v.task_rows(self.snapshot.tasks) if self.snapshot is not None else []
        label, keep = v.FILTERS[self.filter_index]
        shown = [row for row in rows if keep(row)]
        self.query_one("#filter-bar", Static).update(
            f"필터 {label} · {len(shown)}/{len(rows)}개 · f 다음 필터 · F 이전 필터")
        ids = [row.task_id for row in shown]
        if self.selected not in ids and ids:
            self.selected = ids[min(self._selected_index, len(ids) - 1)]
        y = table.scroll_y
        table.clear(columns=True)  # columns too, so widths follow what is shown now
        for label, key in TABLE_COLUMNS:
            table.add_column(label, key=key)
        for row in shown:
            style = STYLES[v.STATE_TAGS[row.state]]
            note_style = STYLES["warn"] if row.run_note else ""
            deps = Text()
            for i, (dep, state) in enumerate(row.deps):
                deps.append(", " if i else "")
                deps.append(f"{dep} {v.STATE_MARKS[state]}", style=STYLES[v.STATE_TAGS[state]])
            table.add_row(">" if row.task_id == self.selected else " ", row.graph_id, Text(row.label, style=style),
                          row.checks, deps, Text(row.run_note, style=note_style), fit(row.title, TITLE_WIDTH),
                          key=row.task_id)
        if self.selected in ids:
            index = ids.index(self.selected)
            self._selected_index = index
            table.move_cursor(row=index, animate=False, scroll=False)
        table.scroll_to(y=y, animate=False)
        self._render_detail()

    def _task_actions(self) -> list[tuple[str, str, Availability]]:
        return [("v", "검증 실행", self.availability("verify")), ("s", "흐름 시작·이어가기", self.availability("flow"))]

    def _render_detail(self, reset_scroll: bool = False) -> None:
        if self.snapshot is None:
            lines = [("muted", "조회 중…")]
        else:
            board = self.snapshot.tasks
            selection = self.visible_selection()
            if selection is None and v.task_rows(board):
                lines = [("muted", f"필터 '{v.FILTERS[self.filter_index][0]}'에 맞는 작업 없음 · f로 필터 변경")]
            else:
                lines = v.task_detail(board, selection, self._task_actions())
            lines = self._stale_banner() + lines
        self.query_one("#task-detail", Static).update(to_text(self._wrap(lines, "#detail-pane")))
        if reset_scroll:
            self.query_one("#detail-pane", VerticalScroll).scroll_home(animate=False)

    def _render_review(self) -> None:
        if self.snapshot is None:
            lines = [("muted", "조회 중…")]
        else:
            when = self.shown_at.astimezone().strftime("%H:%M:%S") if self.shown_at else "없음"
            lines = self._stale_banner() + [("muted", f"{when} 조회 기준 · 승인 시 다시 대조해 바뀌었으면 기록하지 않음"),
                                            ("", "")]
            lines += v.review_tab(self.snapshot.tasks, [("a", "최종 승인", self.availability("approve")),
                                                        ("h", "보류", self.availability("hold"))])
        self.query_one("#review-body", Static).update(to_text(self._wrap(lines, "#review-pane")))

    def _render_recovery(self) -> None:
        if self.snapshot is None:
            lines = [("muted", "조회 중…")]
        else:
            lines = self._stale_banner() + v.recovery_tab(self.snapshot.tasks, self.ops.confirm_stopped_command,
                                                          self.ops.observe_command())
        self.query_one("#recovery-body", Static).update(to_text(self._wrap(lines, "#recovery-pane")))

    def _render_preflight(self) -> None:
        lines = v.preflight_tab(self.snapshot, self.provider, self.preflight_outcome, self.preflight_running,
                                self.availability("preflight"))
        self.query_one("#preflight-body", Static).update(to_text(self._wrap(lines, "#preflight-pane")))

    def progress_text(self) -> str:
        a = self.action_state
        if a is None:
            last = f" · 마지막 결과: {self.last_outcome.title}" if self.last_outcome else ""
            text = f"실행 중인 작업 없음{last}"
        elif self.stopping:
            text = (f"중지하는 중… {a.label} · 실행 기록을 남긴 뒤 닫습니다 "
                    "(Ctrl+C를 한 번 더 누르면 기다리지 않고 닫음)")
        else:
            elapsed = int(time.monotonic() - a.started)
            where = f" · {a.task}/{a.check}" if a.task else ""
            pid = f" · pid {a.pid}" if a.pid else (" · 프로세스 시작 전" if a.task else "")
            limit = f" · 제한 {self.ops.target.timeout:g}초" if a.task else ""
            close = " · 끝나면 닫음" if self.quit_when_idle else ""
            text = f"실행 중 · {a.label}{where}{pid} · 경과 {elapsed}초{limit}{close} · x 중지"
        return f"{text} · 포커스 {self.focus_name()}"

    def _render_progress(self) -> None:
        try:
            line = self.query_one("#progress-line", Static)
        except Exception:
            return
        tag = "warn" if self.action_state is not None else ""
        self.screen.set_class(self.action_state is None and not self._logged, "idle")
        line.update(Text(self.progress_text(), style=STYLES[tag], no_wrap=True, overflow="ellipsis"))

    def _tick(self) -> None:
        now = time.monotonic()
        if self.refreshing and now - self._inflight_since > STALL_SECONDS:
            self._inflight = None  # its result, if it ever comes, is discarded
            self.failed = (datetime.now(timezone.utc), f"조회가 {STALL_SECONDS:g}초 넘게 끝나지 않음 (Orca CLI·git 응답 대기)")
            self._log("bad", f"조회 실패 · {self.failed[1]} · 표시 내용은 이전 조회 결과입니다")
            self._render_all()
            if self._refresh_again:
                self._refresh_again = False
                self.request_refresh()
        if self.preflight_running and now - self._preflight_since > STALL_SECONDS:
            self._preflight_token += 1
            self.preflight_running = False
            self.preflight_outcome = ActionOutcome("preflight", "error",
                                                   f"점검이 {STALL_SECONDS:g}초 넘게 끝나지 않음 · p로 다시 점검")
            self._render_preflight()
            self.refresh_bindings()
        if self.action_state is not None:
            self._render_progress()

    def _log(self, tag: str, text: str) -> None:
        if not self._logged:
            self._logged = True
            self._render_progress()
        stamp = datetime.now().strftime("%H:%M:%S")
        self.query_one("#events", RichLog).write(Text(f"{stamp} {text}", style=STYLES.get(tag, "")))

    # -- selection and navigation ---------------------------------------------------------------------

    @on(DataTable.RowHighlighted, "#task-table")
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table = event.data_table
        if event.cursor_row != table.cursor_row or event.row_key is None:
            return  # an older event from before a rebuild
        key = event.row_key.value
        if key == self.selected:
            return
        previous, self.selected = self.selected, key
        self._selected_index = event.cursor_row
        for row_key, mark in ((previous, " "), (key, ">")):
            try:
                table.update_cell(row_key, "mark", mark)
            except Exception:
                pass
        self._render_detail(reset_scroll=True)
        self.refresh_bindings()

    @on(DataTable.RowSelected, "#task-table")
    def _row_selected(self) -> None:
        self.query_one("#detail-pane", VerticalScroll).focus()

    @on(TabbedContent.TabActivated)
    def _tab_activated(self) -> None:
        self.call_after_refresh(self._rewrap)  # a tab hidden until now had no width to wrap to
        self.refresh_bindings()
        self._render_progress()

    def action_tab(self, tab: str) -> None:
        if self.on_yard():  # 1-4 on the yard open that detail tab
            self.pop_screen()
            self.from_yard = True
        if tab == "tab-tasks":
            self.screen.remove_class("detail-only")
        self.query_one("#tabs", TabbedContent).active = tab
        pane = {"tab-tasks": "#task-table", "tab-review": "#review-pane", "tab-recovery": "#recovery-pane",
                "tab-preflight": "#preflight-pane"}[tab]
        self.query_one(pane).focus()

    def action_back(self) -> None:
        """Esc: back to the task list (also when a narrow screen shows only the detail)."""

        focused = self.screen.focused
        if self.yard is not None and self.from_yard:
            self.action_yard()  # a detail opened from the yard: back to the yard, its selection and focus kept
            return
        if self.screen.has_class("detail-only") or (focused is not None and focused.id == "detail-pane"):
            self.screen.remove_class("detail-only")
            if self.active_tab() == "tab-tasks":
                self.query_one("#task-table", DataTable).focus()
        elif self.yard is not None:
            self.action_yard()

    def action_filter(self, step: int) -> None:
        self.filter_index = (self.filter_index + step) % len(v.FILTERS)
        self._selected_index = 0
        self._render_tasks()
        self.refresh_bindings()

    def action_refresh(self) -> None:
        self.request_refresh()

    def action_help(self) -> None:
        if not isinstance(self.screen, HelpScreen):
            self.push_screen(HelpScreen(yard=self.yard is not None))

    def _deny(self, availability: Availability) -> None:
        self.notify(availability.reason, title="실행할 수 없음", severity="warning")

    def on_key(self, event) -> None:
        """A key for an action shown as disabled: say why instead of doing nothing.

        Runs before the binding is checked; a disabled binding then does nothing.
        Keys typed into a dialog are left alone.
        """

        action = KEY_ACTIONS.get(event.key)
        if action is None or isinstance(self.screen, ModalScreen) or self.on_yard():
            return
        if self.check_action(action, ()) is None:
            self._deny(self.availability(action))

    # -- actions that record ----------------------------------------------------------------------------

    def _start_action(self, name: str, label: str, work) -> bool:
        if self.action_state is not None:
            self.notify(f"{self.action_state.label} 진행 중 · 끝난 뒤 다시 시도", severity="warning")
            return False
        stop = threading.Event()
        self.action_state = RunningAction(name, label, stop, time.monotonic())
        self._log("h", f"시작 · {label}")

        def progress(kind: str, payload) -> None:
            self.post_message(ActionProgress(kind, payload))

        def run() -> None:
            try:
                outcome = work(stop, progress)
            except BaseException as exc:  # the thread must always report back
                outcome = ActionOutcome(name, "error", f"예기치 않은 오류 · {type(exc).__name__}: {exc}",
                                        ("실행 기록 상태는 복구 탭(3)에서 확인하세요.",))
            self.post_message(ActionFinished(outcome))

        threading.Thread(target=run, name=f"console-{name}", daemon=True).start()
        self._render_all()
        return True

    def on_action_progress(self, message: ActionProgress) -> None:
        a = self.action_state
        kind, payload = message.kind, message.payload
        if kind == "select":
            self._log("", f"선택 {payload.task_id} · {payload.reason}")
        elif kind == "start":
            task, check = payload
            self.local_run = (task, check)  # the yard's only source for "a local run started"
            if a is not None:
                a.task, a.check, a.pid = task, check, None
            command = SUPPORTED_CHECKS[check].display if check in SUPPORTED_CHECKS else check
            self._log("", f"실행 {task}/{check} → {command} (한 번, 제한 {self.ops.target.timeout:g}초)")
            self.request_refresh()
        elif kind == "pid":
            if a is not None:
                a.pid = payload
            self._log("muted", f"테스트 프로세스 pid:{payload} 시작")
        elif kind == "result":
            run = payload
            self.local_run = None
            word = "통과" if run.result.outcome.value == "passed" else "실패"
            if word == "통과" and self.yard is not None:
                self.yard.cheer()
            self._log("ok" if word == "통과" else "bad",
                      f"{word} · {run.result.task_id}/{run.result.check_id} · 종료 코드 {run.result.exit_code} · "
                      f"{run.summary} · 기록 {self.ops.target.results_name}")
            self.request_refresh()
        elif kind == "warning":
            self._log("warn", f"경고 · {payload}")
        self._render_progress()
        self._update_yard()

    def on_action_finished(self, message: ActionFinished) -> None:
        outcome = message.outcome
        self.action_state = None
        self.local_run = None
        self.stopping = False
        self.last_outcome = outcome
        tag = OUTCOME_TAGS.get(outcome.status, "")
        self._log(tag, outcome.title)
        for line in outcome.lines:
            self._log("muted" if tag == "ok" else tag, f"  {line}")
        self.notify(outcome.title, severity=SEVERITY.get(outcome.status, "warning"), timeout=8)
        if self.quit_when_idle:
            self.exit(0)
            return
        self._render_all()
        self.request_refresh()

    def action_verify(self) -> None:
        availability = self.availability("verify")
        if not availability.enabled:
            self._deny(availability)
            return
        plan = self.ops.verify_plan(self.snapshot, self.visible_selection())
        lines = [
            ("", f"대상 저장소  {plan.repo_name} (--repo)"),
            ("", f"작업        {plan.task_id} · {plan.title}"),
            ("", f"검증 조건    {plan.check_id} → {plan.command}"),
            ("", f"범위        이 작업의 이 검증 조건 하나 · 한 번 · 제한 {plan.timeout:g}초"),
            ("", f"기록        {plan.results_name}"),
            ("muted", f"현재 결과    {plan.current}"),
            ("", ""),
        ] + [("warn" if i < 2 and "선행" in note else "muted", f"· {note}") for i, note in enumerate(plan.notes)]

        def chosen(ok: bool | None) -> None:
            if ok:
                self._run_verify(plan)

        self.push_screen(ConfirmScreen("로컬 검증 실행", lines, "검증 실행"), chosen)

    def _run_verify(self, plan) -> None:
        availability = self.availability("verify")  # the state may have changed while the dialog was open
        if not availability.enabled or self.visible_selection() != plan.task_id:
            self._deny(availability if not availability.enabled else Availability(False, "선택한 작업이 바뀌어 실행하지 않음"))
            return
        self._start_action("verify", f"검증 {plan.task_id}/{plan.check_id}",
                           lambda stop, progress: self.ops.run_verify(plan.task_id, plan.check_id, stop, progress))

    def action_flow(self) -> None:
        availability = self.availability("flow")
        if not availability.enabled:
            self._deny(availability)
            return
        plan = self.ops.flow_plan(self.snapshot)
        lines = [
            ("", f"대상 저장소  {plan.repo_name} (--repo)"),
            ("", f"다음 작업    {plan.next_task} · {plan.reason}"),
            ("", f"실행할 검증  {', '.join(plan.checks)} (작업마다 한 번 · 제한 {plan.timeout:g}초)"),
            ("", f"기록        {plan.results_name} · 흐름 상태와 실행 기록 파일"),
            ("muted", "· 선택 규칙: 계획 순서에서 선행 작업이 모두 완료됐고 실행할 검증 조건이 남은 첫 작업"),
            ("muted", "· 통과하면 다음 작업을 다시 고르고, 실패·미완료·기록 실패에서 멈춤 (자동 재실행 없음)"),
            ("muted", "· 이미 현재 코드에 유효한 결과가 있는 검증은 다시 실행하지 않음"),
            ("muted", "· 흐름은 최종 승인을 기록하지 않음 · Worker에 작업을 보내지 않음"),
        ]
        if plan.later:
            lines += [("", ""), ("", "지금 고를 수 없는 작업:")] + [("muted", f"  {item}") for item in plan.later]

        def chosen(ok: bool | None) -> None:
            if ok:
                again = self.availability("flow")
                if not again.enabled:
                    self._deny(again)
                    return
                self._start_action("flow", "로컬 검증 흐름", self.ops.run_flow)

        self.push_screen(ConfirmScreen("로컬 검증 흐름 시작·이어가기", lines, "흐름 실행"), chosen)

    def action_stop(self) -> None:
        a = self.action_state
        if a is None:
            return
        lines = [("", f"실행 중: {a.label}" + (f" · {a.task}/{a.check}" if a.task else "")),
                 ("warn", "테스트 프로세스를 중지합니다. 그 하위 프로세스가 끝났는지는 확인하지 않으므로 "
                          "실행 기록에 '실행 상태 확인 필요'(종료 미확인)로 남습니다."),
                 ("muted", "흐름이 검증 사이에 있으면 다음 검증을 시작하지 않고 멈춥니다.")]

        def chosen(ok: bool | None) -> None:
            if ok:
                self.begin_stop(quit_after=False)

        self.push_screen(ConfirmScreen("실행 중지", lines, "중지"), chosen)

    def begin_stop(self, *, quit_after: bool) -> None:
        a = self.action_state
        if a is None:
            if quit_after:
                self.exit(0)
            return
        self.quit_when_idle = self.quit_when_idle or quit_after
        self.stopping = self.stopping or quit_after
        a.stop.set()
        self._log("warn", "중지 요청 · 테스트 프로세스를 중지하고 실행 기록을 남기는 중")
        self._render_progress()

    def action_approve(self) -> None:
        availability = self.availability("approve")
        if not availability.enabled:
            self._deny(availability)
            return
        review = self.snapshot.tasks.review
        basis = review_basis(review)  # exactly what this screen shows
        code = review.code
        when = self.shown_at.astimezone().strftime("%H:%M:%S") if self.shown_at else "없음"
        current, recorded = review.current_results
        lines = [
            ("", f"대상 저장소  {self.ops.target.repo_name} · {self.snapshot.tasks.label}"),
            ("", f"검토한 상태  {when} 조회 · 코드 지문 {(code.source.digest or '없음')[:8]} · "
                 f"변경 {len(code.changes.changes)}개 · 현재 코드 결과 {current}/{recorded}"),
            ("muted", "기록하기 직전에 작업 정의·코드·변경 파일·검증 결과·기존 승인 기록을 다시 읽어 이 상태와 대조합니다. "
                      "하나라도 바뀌었으면 기록하지 않고 다시 검토하도록 안내합니다."),
            ("warn", WORKER_NOTE),
        ]
        if review.unlinked:
            lines += [("", ""), ("warn", f"연결된 작업이 없는 변경 {len(review.unlinked)}개:")]
            lines += [("", f"  {path}") for path in basis.unlinked]

        def chosen(answer) -> None:
            if answer is None:
                return
            confirmed, note = answer
            again = self.availability("approve")
            if not again.enabled:
                self._deny(again)
                return
            self._start_action("approve", "최종 승인 기록",
                               lambda stop, progress: self.ops.approve(basis, confirmed, note))

        self.push_screen(ApproveScreen(lines, basis.unlinked), chosen)

    def action_hold(self) -> None:
        availability = self.availability("hold")
        if not availability.enabled:
            self._deny(availability)
            return

        def chosen(reason: str | None) -> None:
            if reason:
                self._start_action("hold", "보류 기록", lambda stop, progress: self.ops.hold(reason))

        self.push_screen(HoldScreen(), chosen)

    # -- preflight (read-only) --------------------------------------------------------------------------

    def action_preflight(self) -> None:
        if self.preflight_running:
            return
        self.preflight_running = True
        self._preflight_token += 1
        self._preflight_since = time.monotonic()
        provider, ops, token = self.provider, self.ops, self._preflight_token

        def work() -> None:
            try:
                outcome = ops.preflight(provider)
            except Exception as exc:
                outcome = ActionOutcome("preflight", "error", f"점검 실패 · {type(exc).__name__}: {exc}")
            self.post_message(PreflightFinished(outcome, token))

        threading.Thread(target=work, name="console-preflight", daemon=True).start()
        self._render_preflight()
        self.refresh_bindings()

    def on_preflight_finished(self, message: PreflightFinished) -> None:
        if message.token != self._preflight_token:
            return  # given up as stalled
        self.preflight_running = False
        self.preflight_outcome = message.outcome
        self._log("muted", f"전송 전 점검 · {message.outcome.title}")
        self._render_preflight()
        self.refresh_bindings()

    def action_provider(self) -> None:
        self.provider = Provider.CODEX if self.provider is Provider.CLAUDE else Provider.CLAUDE
        self.preflight_outcome = None
        self._render_preflight()

    # -- quitting ------------------------------------------------------------------------------------

    def running_lines(self) -> list[tuple[str, str]]:
        a = self.action_state
        elapsed = int(time.monotonic() - a.started)
        where = f" · {a.task}/{a.check}" if a.task else ""
        pid = f" · pid {a.pid}" if a.pid else ""
        return [("", f"실행 중: {a.label}{where}{pid} · 경과 {elapsed}초"),
                ("", "계속 보기: 닫지 않습니다."),
                ("", "끝나면 닫기: 실행이 끝나고 결과와 실행 기록을 남긴 뒤 닫습니다."),
                ("warn", "중지하고 닫기: 테스트 프로세스를 중지하고 닫습니다. 하위 프로세스 종료는 확인하지 않으므로 "
                         "'실행 상태 확인 필요'로 기록되며, 확인한 뒤 handoff --confirm-stopped로 직접 기록해야 "
                         "다시 실행할 수 있습니다.")]

    def action_request_quit(self) -> None:
        if self.action_state is None:
            self.exit(0)
            return
        if self.stopping:
            self.force_exit()
            return
        if isinstance(self.screen, QuitScreen):
            self.screen.answer("stop")
            return

        def chosen(choice: str | None) -> None:
            if choice == "after":
                self.quit_when_idle = True
                self.notify("실행이 끝나면 닫습니다", timeout=5)
                self._render_progress()
            elif choice == "stop":
                self.begin_stop(quit_after=True)

        self.push_screen(QuitScreen(self.running_lines()), chosen)

    def force_exit(self) -> None:
        killed = self.ops.handle.kill()
        note = " · 테스트 프로세스를 중지함" if killed else ""
        self.exit(0, message=f"기록을 기다리지 않고 닫았습니다{note} · 다음 조회에서 '실행 상태 확인 필요'로 보일 수 있습니다 "
                             "(python -m orca_orchestrator.handoff로 확인).")

    def _install_signals(self) -> None:
        if self.is_headless or os.name == "nt":
            return
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGHUP):
            try:
                loop.add_signal_handler(sig, self.on_terminate_signal)
                self._signals.append(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                pass

    def on_terminate_signal(self) -> None:
        """SIGTERM/SIGHUP: stop a running check like 'stop and close', then close."""

        if self.action_state is None:
            self.exit(0)
            return
        self.begin_stop(quit_after=True)
        self.set_timer(FORCE_EXIT_AFTER_SIGNAL_SECONDS, self.force_exit)

    def on_unmount(self) -> None:
        if not self._signals:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for sig in self._signals:
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                pass


def run_console(ops: Console, *, yard: bool = False, motion: Motion = Motion.NORMAL) -> int:
    app = ConsoleApp(ops, yard=yard, motion=motion)
    result = app.run()
    return int(result or 0) if app.return_code in (None, 0) else int(app.return_code)
