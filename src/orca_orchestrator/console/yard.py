"""What the yard shows, from the existing judgments. No UI framework, no I/O, no clock.

This is a projection. The yard's mood, label and requests are computed from
the snapshot the console already collected (``TaskBoard`` progress, resume
and review judgments, the dispatch decisions) and from the console's own
run events. Nothing here reads files, runs a command or records anything,
and nothing the yard does (time passing, a click, the character arriving at
the bench, a bubble closed) changes a task state, a result file, a lock or
an approval.

Rules kept from the PRD:

* READY is not running. Only a local run whose start event arrived (the
  console's ``start`` progress) sends the character to the bench.
* An unconfirmed end beats everything else, including a run shown as going.
* Passed checks and the person's final approval are different labels.
* A stale or failed refresh shows the last observation and says so; it never
  invents a new running state.
* Failure, unconfirmed end, a failed refresh and the blocked Worker dispatch
  are different states; the dispatch block is not a failure.
* Waiting for a usage reset is shown only when the dispatch gate itself says
  so; there are no invented counts, progress or reset times.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..tasks import BoardState, TaskState
from ..tasks.handoff import SESSION_ID, ResumeState
from ..tasks.progress import CheckState
from ..tasks.review import ApprovalState, BlockKind


class Mood(str, Enum):
    NO_DATA = "no_data"  # nothing collected yet
    IDLE = "idle"  # nothing to do or nothing judged: walk and rest
    READY = "ready"  # a task can start; not running
    WORKING = "working"  # a local run's start was seen in this console
    OTHER_SESSION = "other_session"  # the run record says another live session runs a task
    FAILED = "failed"  # a current check result failed
    END_UNKNOWN = "end_unknown"  # a run's end is not confirmed
    PASSED = "passed"  # every check passed; final approval is separate
    REVIEW = "review"  # passed, and the change waits for the person's review or approval
    APPROVED = "approved"  # the person approved and it still applies
    WAITING_RESET = "waiting_reset"  # the dispatch gate reports a usage reset wait
    PROBLEM = "problem"  # the task list cannot be judged


# Marks so no state depends on colour alone.
MOOD_MARKS = {
    Mood.NO_DATA: "…", Mood.IDLE: "·", Mood.READY: "▸", Mood.WORKING: "⚙", Mood.OTHER_SESSION: "⚙",
    Mood.FAILED: "✗", Mood.END_UNKNOWN: "?", Mood.PASSED: "✓", Mood.REVIEW: "✓", Mood.APPROVED: "✓",
    Mood.WAITING_RESET: "z", Mood.PROBLEM: "!",
}
MOOD_TAGS = {
    Mood.NO_DATA: "muted", Mood.IDLE: "muted", Mood.READY: "", Mood.WORKING: "warn", Mood.OTHER_SESSION: "warn",
    Mood.FAILED: "bad", Mood.END_UNKNOWN: "warn", Mood.PASSED: "ok", Mood.REVIEW: "ok", Mood.APPROVED: "ok",
    Mood.WAITING_RESET: "muted", Mood.PROBLEM: "bad",
}
# Moods that send the pets to the desk monitor to watch. Only a local check started in this console: the pets
# watch it and do not work on it. Another session's run is not theirs to watch: they wait where they are, and
# nothing here says which agent (if any) runs it.
AT_BENCH = frozenset({Mood.WORKING})
WAITING_ON_OTHERS = frozenset({Mood.OTHER_SESSION})


class RequestKind(str, Enum):
    END_UNKNOWN = "end_unknown"
    RECORD_UNREADABLE = "record_unreadable"
    BOARD = "board"
    CHECK_FAILED = "check_failed"
    PROTECTED = "protected"
    CHECK_MISSING = "check_missing"
    RECHECK = "recheck"
    UNLINKED = "unlinked"
    COMPARE = "compare"
    APPROVAL = "approval"
    REAPPROVE = "reapprove"
    DEMO = "demo"


PRIORITY = {
    RequestKind.END_UNKNOWN: 0, RequestKind.RECORD_UNREADABLE: 0, RequestKind.BOARD: 0,
    RequestKind.CHECK_FAILED: 1, RequestKind.PROTECTED: 1,
    RequestKind.CHECK_MISSING: 2, RequestKind.RECHECK: 2, RequestKind.COMPARE: 2,
    RequestKind.UNLINKED: 3, RequestKind.APPROVAL: 3, RequestKind.REAPPROVE: 3,
    RequestKind.DEMO: 3,
}
KIND_LABELS = {
    RequestKind.END_UNKNOWN: "종료 미확인", RequestKind.RECORD_UNREADABLE: "실행 기록 읽기 불가",
    RequestKind.BOARD: "작업 정의 확인", RequestKind.CHECK_FAILED: "검증 실패",
    RequestKind.PROTECTED: "보호 경로 변경", RequestKind.CHECK_MISSING: "검증 결과 없음",
    RequestKind.RECHECK: "재확인 필요", RequestKind.UNLINKED: "연결 없는 변경",
    RequestKind.COMPARE: "변경 비교 불가", RequestKind.APPROVAL: "최종 승인 필요",
    RequestKind.REAPPROVE: "재승인 필요", RequestKind.DEMO: "확인 요청 (모의)",
}

# Where Enter on a request goes: one of the console's four detail tabs.
TASKS, REVIEW, RECOVERY, PREFLIGHT = "tab-tasks", "tab-review", "tab-recovery", "tab-preflight"


@dataclass(frozen=True)
class Request:
    """Something the person has to look at. ``rid`` is stable: same target and kind, same ID."""

    rid: str
    kind: RequestKind
    task_id: str | None
    summary: str  # short; the bubble and the inbox line
    tab: str  # the detail tab that holds the evidence

    @property
    def priority(self) -> int:
        return PRIORITY[self.kind]

    @property
    def label(self) -> str:
        return KIND_LABELS[self.kind]


def _rid(kind: RequestKind, target: str | None) -> str:
    return f"{kind.value}:{target or '-'}"


def _req(kind: RequestKind, target: str | None, summary: str, tab: str, task_id: str | None = None) -> Request:
    return Request(_rid(kind, target), kind, task_id if task_id is not None else target, summary, tab)


def derive_requests(board) -> tuple[Request, ...]:
    """Every open request the current judgments give evidence for, most urgent first, without duplicates."""

    out: dict[str, Request] = {}

    def add(request: Request) -> None:
        out.setdefault(request.rid, request)

    if board is None:
        return ()
    if board.state in (BoardState.INVALID, BoardState.UNREADABLE):
        what = "작업 정의 오류" if board.state is BoardState.INVALID else "작업 목록을 읽을 수 없음"
        add(_req(RequestKind.BOARD, board.state.value, f"{what} · 상태를 판정하지 않음", TASKS, task_id=""))
        return tuple(out.values())
    if board.state is not BoardState.OK or board.progress is None:
        return ()

    resume = board.resume
    if resume is not None:
        if resume.error:
            add(_req(RequestKind.RECORD_UNREADABLE, None, "실행 기록 파일을 읽을 수 없음 · 기록 동작 보류", RECOVERY))
        for t in resume.tasks:
            if t.state is ResumeState.CHECK_RUN:
                add(_req(RequestKind.END_UNKNOWN, t.task_id, f"{t.task_id} 실행 종료를 확인해야 함", RECOVERY))

    for tp in board.progress.tasks:
        tid = tp.task.task_id
        if tp.state is TaskState.FAILED:
            add(_req(RequestKind.CHECK_FAILED, tid, f"{tid} 검증 실패 · 자동 재시도 없음", TASKS))
        elif tp.state in (TaskState.READY, TaskState.RECHECK):
            states = {cp.state for cp in tp.checks}
            if CheckState.MISSING in states:
                add(_req(RequestKind.CHECK_MISSING, tid, f"{tid} 필요한 검증 결과 없음", TASKS))
            if CheckState.STALE in states:
                add(_req(RequestKind.RECHECK, tid, f"{tid} 결과가 현재 코드·정의와 맞지 않음", TASKS))

    review = board.review
    if review is not None:
        for b in review.blockers:
            if b.kind is BlockKind.PROTECTED:
                add(_req(RequestKind.PROTECTED, b.subject or None, f"{b.subject or '작업'} 보호 경로 변경 · 승인 보류",
                         REVIEW))
            elif b.kind is BlockKind.COMPARE:
                add(_req(RequestKind.COMPARE, None, "기준 대비 변경을 비교할 수 없음 · 승인 보류", REVIEW))
        # An approval that still applies already confirmed the unlinked changes.
        if review.unlinked and review.state is not ApprovalState.APPROVED:
            add(_req(RequestKind.UNLINKED, None, f"연결된 작업이 없는 변경 {len(review.unlinked)}개 확인", REVIEW))
        if review.approvable and review.state is ApprovalState.NONE:
            add(_req(RequestKind.APPROVAL, None, "검증 통과 · 변경 검토와 최종 승인 필요", REVIEW))
        elif review.state is ApprovalState.REAPPROVE:
            add(_req(RequestKind.REAPPROVE, None, "승인 후 바뀐 것이 있음 · 다시 검토 필요", REVIEW))

    return tuple(sorted(out.values(), key=lambda r: (r.priority, r.rid)))


@dataclass
class RequestBook:
    """The inbox. Reading or closing a bubble marks it read; only new evidence resolves a request.

    ``update`` replaces the open set with what the judgments say now: a request
    that the evidence no longer supports disappears, a new one appears unread,
    and one that stays keeps its read mark. A fresh console (reconnect) starts
    with an empty book and rebuilds every open request from the files.
    """

    open: tuple[Request, ...] = ()
    read: set[str] = field(default_factory=set)

    def update(self, requests: tuple[Request, ...]) -> tuple[Request, ...]:
        self.open = requests
        self.read &= {r.rid for r in requests}
        return self.open

    def mark_read(self, rid: str) -> None:
        if any(r.rid == rid for r in self.open):
            self.read.add(rid)

    def bubble(self) -> Request | None:
        """The most urgent unread request; the bubble shows one at a time."""

        return next((r for r in self.open if r.rid not in self.read), None)

    def is_read(self, rid: str) -> bool:
        return rid in self.read


@dataclass(frozen=True)
class YardInput:
    """What the mapping needs, all from state the console already has."""

    board: object | None  # TaskBoard of the snapshot on screen
    decisions: dict = field(default_factory=dict)  # Provider -> DispatchDecision
    local_run: tuple[str, str] | None = None  # (task, check) between a start event and its result
    stale: bool = False  # the last refresh failed or stalled
    shown_at: datetime | None = None
    demo: bool = False


@dataclass(frozen=True)
class YardModel:
    mood: Mood
    label: str  # short state line, with a mark
    task_id: str | None  # the task the character and bench stand for (None: the task list)
    requests: tuple[Request, ...]
    stale_note: str | None = None
    mock: bool = False  # a demo scene: nothing here happened

    @property
    def tag(self) -> str:
        return MOOD_TAGS[self.mood]


def _clock(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%H:%M:%S") if moment else "없음"


def _other_session(board) -> str | None:
    if board.resume is None:
        return None
    for t in board.resume.tasks:
        if t.state is ResumeState.RUNNING and not (t.attempt is not None and t.attempt.session == SESSION_ID):
            return t.task_id
    return None


def _first(board, states: tuple[TaskState, ...]) -> str | None:
    return next((tp.task.task_id for tp in board.progress.tasks if tp.state in states), None)


def yard_model(data: YardInput) -> YardModel:
    """The mood and label for the current judgments. Pure: the same input gives the same model."""

    stale_note = (f"마지막 관측 {_clock(data.shown_at)} · 최신이 아님 · 기록 동작 보류" if data.stale else None)
    board = data.board
    requests = derive_requests(board)

    def model(mood: Mood, text: str, task_id: str | None) -> YardModel:
        return YardModel(mood, f"{MOOD_MARKS[mood]} {text}", task_id, requests, stale_note)

    unknown = next((r for r in requests if r.kind is RequestKind.END_UNKNOWN), None)
    if unknown is not None:
        return model(Mood.END_UNKNOWN, f"{unknown.task_id} 종료 미확인 · 확인 전에는 다시 실행하지 않음", unknown.task_id)

    if data.local_run is not None:
        task, check = data.local_run
        return model(Mood.WORKING, f"{task}/{check} 로컬 검증 실행 중 (이 화면 · Worker 아님)", task)

    if board is None:
        return model(Mood.NO_DATA, "조회 전 · 상태를 아직 모름", None)
    if board.state is BoardState.NOT_CONFIGURED:
        return model(Mood.IDLE, "작업 목록 없음 (--tasks로 지정) · 대기", None)
    if board.state is not BoardState.OK or board.progress is None:
        return model(Mood.PROBLEM, "작업 목록을 판정할 수 없음 · 상세에서 확인", None)

    other = _other_session(board)
    if other is not None:
        return model(Mood.OTHER_SESSION, f"{other} 다른 세션 실행 중 (실행 기록 기준 · 실행 주체는 상세에서 확인)", other)

    if any(r.kind is RequestKind.RECORD_UNREADABLE for r in requests):
        return model(Mood.PROBLEM, "실행 기록을 읽을 수 없음 · 복구 탭 확인", None)

    failed = _first(board, (TaskState.FAILED,))
    if failed is not None:
        return model(Mood.FAILED, f"{failed} 검증 실패 · 자동 재시도 없음", failed)

    review = board.review
    tasks = board.progress.tasks
    if tasks and all(tp.state is TaskState.VERIFIED for tp in tasks):
        last = tasks[-1].task.task_id
        if review is not None and review.state is ApprovalState.APPROVED:
            return model(Mood.APPROVED, "검증 통과 · 사용자 최종 승인됨", last)
        if review is not None:
            return model(Mood.REVIEW, "검증 통과 · 최종 승인 아님 · 변경 검토 필요", last)
        return model(Mood.PASSED, "검증 통과 · 최종 승인은 별도", last)

    waiting = [p for p, d in data.decisions.items() if getattr(d.outcome, "value", "") == "WAITING_FOR_RESET"]
    if waiting:
        return model(Mood.WAITING_RESET, "사용량 리셋 대기 (전송 게이트 판단) · 리셋 시각 미확인이면 대기 유지", None)

    ready = _first(board, (TaskState.READY, TaskState.RECHECK))
    if ready is not None:
        count = sum(1 for tp in tasks if tp.state in (TaskState.READY, TaskState.RECHECK))
        return model(Mood.READY, f"준비 {count}개 ({ready} 먼저) · 실행 시작 아님", ready)
    return model(Mood.IDLE, "대기 · 선행 작업 완료를 기다림", _first(board, (TaskState.WAITING,)))


# --- demo scenes ---------------------------------------------------------------------------

DEMO_SCENES: tuple[tuple[str, Mood, str], ...] = (
    ("대기", Mood.IDLE, "대기 · 마당 산책 (모의)"),
    ("시작", Mood.WORKING, "T-1/unit-tests 로컬 검증 시작 (모의 · Worker 아님)"),
    ("작업", Mood.WORKING, "T-1/unit-tests 로컬 검증 실행 중 (모의 · Worker 아님)"),
    ("확인 요청", Mood.END_UNKNOWN, "T-1 확인 요청 (모의) · 상세에서 근거 확인"),
    ("검증 통과", Mood.PASSED, "T-1 검증 통과 (모의) · 최종 승인 아님"),
)


def demo_scene(index: int) -> YardModel:
    """A mock scene for the demo. Nothing is run or recorded; every line says it is a mock."""

    name, mood, text = DEMO_SCENES[index % len(DEMO_SCENES)]
    requests: tuple[Request, ...] = ()
    if mood is Mood.END_UNKNOWN:
        requests = (Request("demo:T-1", RequestKind.DEMO, "T-1", "모의 확인 요청 · 실제 기록 없음", RECOVERY),)
    elif mood is Mood.PASSED:
        requests = (Request("demo:review", RequestKind.DEMO, None, "모의 · 변경 검토 화면 열기", REVIEW),)
    return YardModel(mood, f"{MOOD_MARKS[mood]} {text}", "T-1", requests, None, mock=True)
