"""Text for the console's panels, from the existing structured models. No UI framework here.

Each line is ``(tag, text)``. The tag only names a style (``h`` heading,
``ok``, ``bad``, ``warn``, ``muted``, ``key``, or ``""``); every state is also
spelled out in the text, so nothing depends on colour. Nothing is judged here:
states, reasons and next actions come from ``tasks.progress``, ``tasks.flow``,
``tasks.handoff`` and ``tasks.review`` and their existing text helpers.

Layout rules shared by every panel: a fact is shown once; rows are
``label  value`` with the value at a fixed column (``kv``/``more``); the
dependency graph and the requirement trace are drawn as graphs, not prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ..orca.dispatch import Provider, Reason
from ..orca.workers import WORKER_CONTRACT, Evidence
from ..tasks import BoardState, TaskBoard, TaskState
from ..tasks.codebase import scope_text
from ..tasks.flow import STALE_TEXT, wait_reason_text
from ..tasks.handoff import (
    RESUME_TEXT,
    SESSION_ID,
    ResumeState,
    attempt_line,
    check_hint,
    is_worker_run,
    observation_lines,
    progress_line,
    run_line,
)
from ..tasks.progress import CheckState
from ..tasks.review import (
    ApprovalState,
    approval_summary,
    basis_text,
    change_lines,
    changes_summary,
    protected_summary,
    review_trace_lines,
)
from ..tasks.verification import SUPPORTED_CHECKS
from ..tui import render as r
from ..tui import snapshot as s
from .bridge import WORKER_NOTE, Availability

Line = tuple[str, str]

STATE_MARKS = {
    TaskState.VERIFIED: "✓",
    TaskState.FAILED: "✗",
    TaskState.RECHECK: "↻",
    TaskState.READY: "▸",
    TaskState.WAITING: "-",
}
STATE_TAGS = {
    TaskState.VERIFIED: "ok",
    TaskState.FAILED: "bad",
    TaskState.RECHECK: "warn",
    TaskState.READY: "",
    TaskState.WAITING: "muted",
}
RUN_NOTES = {
    ResumeState.CHECK_RUN: "! 실행 확인 필요",
    ResumeState.RUNNING: "실행 중",
    ResumeState.FINISHING: RESUME_TEXT[ResumeState.FINISHING],
}


def state_label(state: TaskState) -> str:
    return f"{STATE_MARKS[state]} {r.TASK_STATE_LABELS[state]}"


# --- shared layout helpers ----------------------------------------------------------------------

LABEL = 12  # label column of "label  value" rows, in terminal cells


def kv(tag: str, label: str, value: str) -> Line:
    """A row whose value starts at a fixed column, so the eye can run down the labels."""

    return tag, label + " " * max(1, LABEL - r.display_width(label)) + value


def more(tag: str, value: str) -> Line:
    """A continuation of the row above, under its value."""

    return tag, " " * LABEL + value


BLANK: Line = ("", "")


def action_lines(actions: list[tuple[str, str, Availability]]) -> list[Line]:
    """Keys sharing a state and reason are listed once ("v·s … 사용 불가 · 데모 모드 …")."""

    groups: dict[tuple[bool, str], list[tuple[str, str]]] = {}
    for key, name, av in actions:
        groups.setdefault((av.enabled, av.reason), []).append((key, name))
    out: list[Line] = []
    for i, ((enabled, reason), members) in enumerate(groups.items()):
        keys = "·".join(key for key, _ in members)
        names = ", ".join(name for _, name in members)
        text = f"{keys} {names}: {'가능' if enabled else '사용 불가'}" + (f" · {reason}" if reason else "")
        out.append(kv("key" if enabled else "muted", "동작" if i == 0 else "", text))
    return out


def _hang(text: str) -> int:
    """Column where wrapped parts of ``text`` continue: under a kv value, or under a tree/indented body."""

    lead = len(text) - len(text.lstrip(" │├└─▶"))
    if lead:
        return r.display_width(text[:lead])
    used = 0
    for i, ch in enumerate(text):  # a kv row: a label, then spaces up to exactly LABEL cells, then the value
        if used == LABEL:
            return LABEL if i and text[i - 1] == " " and ch != " " else 0
        used += r.display_width(ch)
        if used > LABEL:
            break
    return 0


def _wrap_one(text: str, width: int) -> list[str]:
    indent = _hang(text)
    if indent > width // 2:
        indent = 0
    lead = len(text) - len(text.lstrip(" "))
    out, line, used = [], text[:lead], lead
    limit = width
    for word in text[lead:].split(" "):
        piece = (" " if line.strip() else "") + word
        w = r.display_width(piece)
        if used + w <= limit:
            line, used = line + piece, used + w
            continue
        if line.strip():
            out.append(line)
            line, used, limit = " " * indent, indent, width
            piece = word
            w = r.display_width(word)
        for ch in piece:  # a word longer than the line is split by cell width
            cw = r.display_width(ch)
            if used + cw > limit and line.strip():
                out.append(line)
                line, used = " " * indent, indent
            line, used = line + ch, used + cw
    out.append(line)
    return out


def wrap_lines(lines: list[Line], width: int) -> list[Line]:
    """Wraps long lines to ``width`` cells, continuing under the value column instead of at the left edge."""

    if width < 20:
        return lines
    out: list[Line] = []
    for tag, text in lines:
        if r.display_width(text) <= width:
            out.append((tag, text))
        else:
            out += [(tag, part) for part in _wrap_one(text, width)]
    return out


def tree_lines(lines: list[str], indent: int = 2) -> list[str]:
    """Indented text (``indent`` spaces per level) drawn as a tree with ├─ └─ │ connectors."""

    levels = [(len(line) - len(line.lstrip(" "))) // indent for line in lines]

    def has_next_sibling(i: int) -> bool:
        for j in range(i + 1, len(lines)):
            if levels[j] < levels[i]:
                return False
            if levels[j] == levels[i]:
                return True
        return False

    out: list[str] = []
    open_levels: dict[int, bool] = {}  # level -> a later sibling at that level is still to come
    for i, line in enumerate(lines):
        level, body = levels[i], line.lstrip(" ")
        if level == 0:
            out.append(body)
            continue
        last = not has_next_sibling(i)
        open_levels[level] = not last
        rails = "".join("│  " if open_levels.get(k) else "   " for k in range(1, level))
        out.append(rails + ("└─ " if last else "├─ ") + body)
    return out


# --- task list and dependency graph -------------------------------------------------------------


@dataclass(frozen=True)
class TaskRow:
    task_id: str
    title: str
    state: TaskState
    label: str  # mark and state, e.g. "✓ 완료"
    checks: str  # "passed/total"
    run_note: str  # how its last run stands, when that matters
    resume: ResumeState | None
    depth: int = 0  # longest prerequisite chain above this task (0: none)
    deps: tuple[tuple[str, TaskState], ...] = ()  # direct prerequisites and their states

    @property
    def graph_id(self) -> str:
        """The ID drawn as a node of the dependency graph: indented by depth, with an arrow from above."""

        if self.depth == 0:
            return self.task_id
        return "  " * (self.depth - 1) + "└▶ " + self.task_id

    @property
    def deps_text(self) -> str:
        return ", ".join(f"{d} {STATE_MARKS[st]}" for d, st in self.deps)


def graph_depths(board: TaskBoard) -> dict[str, int]:
    """Depth of each task in the dependency DAG (a valid board has no cycles)."""

    tasks = {tp.task.task_id: tp.task for tp in board.progress.tasks}
    depth: dict[str, int] = {}

    def visit(task_id: str, path: frozenset[str]) -> int:
        if task_id not in depth:
            deps = [d for d in tasks[task_id].dependencies if d in tasks and d not in path]
            depth[task_id] = 1 + max((visit(d, path | {task_id}) for d in deps), default=-1)
        return depth[task_id]

    for task_id in tasks:
        visit(task_id, frozenset())
    return depth


def dependents(board: TaskBoard, task_id: str) -> list[str]:
    return [tp.task.task_id for tp in board.progress.tasks if task_id in tp.task.dependencies]


def node_text(board: TaskBoard, task_id: str) -> str:
    return f"{task_id} {STATE_MARKS[board.progress.task(task_id).state]}"


def graph_line(board: TaskBoard, task_id: str) -> str:
    """Prerequisites ─▶ [this task] ─▶ the tasks that wait on it."""

    deps = board.progress.task(task_id).task.dependencies
    before = ", ".join(node_text(board, d) for d in deps) if deps else "시작"
    after = ", ".join(node_text(board, d) for d in dependents(board, task_id)) or "끝"
    return f"{before} ─▶ [{node_text(board, task_id)}] ─▶ {after}"


def _run_note(board: TaskBoard, task_id: str) -> tuple[str, ResumeState | None]:
    resume = board.resume
    if resume is None:
        return "", None
    t = resume.task(task_id)
    note = RUN_NOTES.get(t.state, "")
    if t.state is ResumeState.RUNNING and t.attempt is not None and t.attempt.session == SESSION_ID:
        note = "이 화면에서 실행 중"
    return note, t.state


def task_rows(board: TaskBoard) -> list[TaskRow]:
    if board.state is not BoardState.OK or board.progress is None:
        return []
    depths = graph_depths(board)
    rows = []
    for tp in board.progress.tasks:
        note, resume = _run_note(board, tp.task.task_id)
        deps = tuple((d, board.progress.task(d).state) for d in tp.task.dependencies)
        rows.append(TaskRow(tp.task.task_id, tp.task.title, tp.state, state_label(tp.state),
                            f"{tp.passed}/{tp.total}", note, resume, depths[tp.task.task_id], deps))
    return rows


FILTERS: tuple[tuple[str, Callable[[TaskRow], bool]], ...] = (
    ("전체", lambda row: True),
    ("진행 필요", lambda row: row.state is not TaskState.VERIFIED),
    ("준비", lambda row: row.state is TaskState.READY),
    ("대기", lambda row: row.state is TaskState.WAITING),
    ("재확인", lambda row: row.state is TaskState.RECHECK),
    ("검증 실패", lambda row: row.state is TaskState.FAILED),
    ("완료", lambda row: row.state is TaskState.VERIFIED),
    ("실행 확인 필요", lambda row: row.resume in RUN_NOTES),
)


def board_note(board: TaskBoard) -> list[Line]:
    """Why there is no task list, when there is none."""

    if board.state is BoardState.NOT_CONFIGURED:
        return [("muted", f"작업 목록: {r.TASKS_NOT_CONFIGURED}")]
    if board.state is BoardState.UNREADABLE:
        return [("bad", f"작업 목록 {r.UNREADABLE} · {board.error}"), ("muted", f"출처 {board.label}")]
    if board.state is BoardState.INVALID:
        lines: list[Line] = [("bad", f"작업 정의 오류 {len(board.issues)}건 · 상태를 판정하지 않음"),
                             ("muted", f"출처 {board.label}")]
        lines += [("bad", f"· {r.issue_text(issue)}") for issue in board.issues]
        return lines
    return []


# --- header ----------------------------------------------------------------------------


@dataclass(frozen=True)
class RefreshInfo:
    shown_at: datetime | None  # when the snapshot on screen was taken
    running: bool  # a refresh is in progress
    failed_at: datetime | None = None  # the last refresh failed at this time
    error: str | None = None


def _clock(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%H:%M:%S") if moment else "없음"


def dispatch_reason_count(snap: s.Snapshot) -> int:
    return len({code for d in snap.decisions.values() for code in d.reason_codes})


SEP = "  │  "


def header_lines(snap: s.Snapshot | None, target, refresh: RefreshInfo) -> list[Line]:
    """Two lines: who and where, then the most important state first (the line is cut at the right).

    The code basis lives in the review tab. A failed refresh and a guidance hint get a line of their own.
    """

    mode = f"데모 ({target.demo}) · 실제 CLI를 호출하지 않음" if target.demo else "실제 연결"
    first = f"Orca Orchestrator · {mode} · 저장소 {target.repo_name}"
    if snap is None:
        state = "조회 중…" if refresh.running else "조회 전"
        return [("h", first), ("muted", f"{state}{SEP}Worker 전송 보류 (이 빌드는 Worker에 작업을 보내지 않음)")]
    waiting = [p for p, d in snap.decisions.items() if d.outcome.value == "WAITING_FOR_RESET"]
    dispatch = f"Worker 전송 보류 · 사유 {dispatch_reason_count(snap)}건 (4: 상세)"
    if waiting:
        dispatch += " · 리셋 대기: " + ", ".join(r.PROVIDER_NAMES[p] for p in waiting)
    shown = f"조회 {_clock(refresh.shown_at)}" + (" · 다시 조회 중…" if refresh.running else "")
    second = SEP.join([
        shown,
        dispatch,
        f"Runtime {r.runtime_text(snap.runtime)} · 바인딩 {'있음' if snap.bound else '없음'}",
        f"Run {r.run_text(snap.run)}",
    ])
    lines = [("h", first), ("warn" if refresh.running else "", second)]
    if refresh.failed_at is not None:
        lines.append(("bad", f"조회 실패 {_clock(refresh.failed_at)} ({refresh.error}) · "
                             f"표시 내용은 {_clock(refresh.shown_at)} 기준이며 최신이 아님"))
    hint = r.hint(snap)
    if hint:
        lines.append(("warn", hint))
    return lines


# --- task detail -------------------------------------------------------------------------


def _check_lines(board: TaskBoard, cp, label: str) -> list[Line]:
    out: list[Line] = [kv("", label, f"{cp.check.check_id} · {cp.check.description}")]
    if cp.state is CheckState.PASSED:
        out.append(more("ok", "결과 ✓ 통과 · 현재 작업 정의·코드에 유효"))
    elif cp.state is CheckState.FAILED:
        out.append(more("bad", "결과 ✗ 실패 · 현재 작업 정의·코드 기준 결과"))
    elif cp.state is CheckState.MISSING:
        out.append(more("muted", "결과 없음"))
    else:
        previous = "통과" if cp.result.outcome.value == "passed" else "실패"
        out.append(more("warn", f"결과 ↻ 재확인 필요 ({STALE_TEXT[cp.stale_reason]}, 이전 {previous}) · "
                                "현재 상태에 유효하지 않음"))
    result = cp.result
    if result is not None:
        detail = [f"종료 코드 {result.exit_code}"] if result.exit_code is not None else []
        if result.evidence:
            detail.append(result.evidence)
        if detail:
            out.append(more("muted", " · ".join(detail)))
        sd = result.source_digest
        if sd is None:
            out.append(more("muted", "검증한 코드 기록 없음"))
        else:
            current = board.review.code.source.digest if board.review is not None else None
            same = "" if current is None else (" (현재와 같음)" if sd == current else " (현재와 다름)")
            head = (f"HEAD {result.git_head[:7]}{' + 커밋 전 변경' if result.git_dirty else ''} · "
                    if result.git_head else "")
            out.append(more("muted", f"검증한 코드 {head}지문 {sd[:8]}{same}"))
    command = SUPPORTED_CHECKS.get(cp.check.check_id)
    if command is None:
        out.append(more("muted", "자동 실행 미지원 · 결과 파일에 기록된 결과만 사용"))
    elif result is None:
        out.append(more("muted", f"자동 실행 {command.display}"))
    return out


def _attempt_lines(attempt, live: bool, label: str = "", show_task: bool = False) -> list[Line]:
    """One attempt in three rows at most; what the check result already says is not repeated."""

    head = attempt_line(attempt, live, r.local_time)
    if not show_task and head.startswith(f"{attempt.task} · "):
        head = head[len(attempt.task) + 3:]
    run = run_line(attempt, live)
    out: list[Line] = [kv("", label, head) if label else more("", head)]
    out.append(more("warn" if "종료 미확인" in run else "", run))
    out.extend(more("muted", seen) for seen in observation_lines(attempt, r.local_time))
    out.append(more("", progress_line(attempt)))
    if attempt.reason and attempt.reason not in run:
        out.append(more("", f"중단·종료 이유: {attempt.reason}"))
    if attempt.resolved:
        out.append(more("muted", f"중지 확인 {r.local_time(attempt.resolved_at)} · {attempt.resolved_note}"))
    return out


def task_detail(board: TaskBoard, task_id: str | None, actions: list[tuple[str, str, Availability]]) -> list[Line]:
    if board.state is not BoardState.OK:
        return board_note(board) or [("muted", "작업 없음")]
    progress = board.progress
    tp = next((t for t in progress.tasks if t.task.task_id == task_id), None) if task_id else None
    if tp is None:
        return [("muted", "왼쪽 목록에서 작업을 선택하세요 (↑↓ 이동)")]
    task = tp.task
    out: list[Line] = [("h", f"{task.task_id} · {task.title}"),
                       kv(STATE_TAGS[tp.state], "상태", f"{state_label(tp.state)} · 검증 {tp.passed}/{tp.total} 통과")]
    resume = board.resume.task(task.task_id) if board.resume is not None else None
    if resume is not None:
        tag = "warn" if resume.state in RUN_NOTES else ""
        out.append(kv(tag, "재개", f"{RESUME_TEXT[resume.state]} · {resume.reason}"))
        out.append(kv("", "다음 행동", resume.next_action))

    out += [BLANK, kv("", "그래프", graph_line(board, task.task_id))]
    for reason in tp.waiting_on:
        out.append(kv("warn", "대기 이유", wait_reason_text(reason)))

    by_req = {rp.requirement.requirement_id: rp for rp in progress.requirements}
    for i, rid in enumerate(task.requirement_ids):
        rp = by_req[rid]
        status = "충족" if rp.satisfied else (f"미충족 ({', '.join(rp.open_task_ids)} 미완료)" if rp.task_ids
                                               else "미충족 (연결된 작업 없음)")
        out.append(kv("ok" if rp.satisfied else "", "요구사항" if i == 0 else "",
                      f"{rid} {status} · {rp.requirement.title}"))
        out.append(more("muted", f"인수 조건: {rp.requirement.acceptance}"))

    out.append(BLANK)
    for i, cp in enumerate(tp.checks):
        out += _check_lines(board, cp, f"검증 {tp.passed}/{tp.total}" if i == 0 else "")

    review = board.review
    label = "변경 파일"
    if review is None:
        out.append(kv("muted", label, "저장소 비교 없음 (--repo)"))
    elif not review.code.changes.ok:
        out.append(kv("warn", label, f"비교 불가 · {review.code.changes.error}"))
    else:
        tc = review.task(task.task_id)
        files = [("", c.text()) for c in tc.changes] + [("bad", f"보호 경로 변경 {c.text()} · 승인 보류")
                                                        for c in tc.protected]
        if not files:
            files = [("muted", f"{', '.join(task.paths)} 아래 변경 없음" if task.paths
                      else "작업 정의에 paths 없음 · 연결할 변경 없음")]
        out += [kv(tag, label if i == 0 else "", text) for i, (tag, text) in enumerate(files)]

    out.append(BLANK)
    attempt = resume.attempt if resume is not None else None
    if attempt is None:
        out.append(kv("muted", "마지막 실행", "실행 기록 없음"))
    else:
        out += _attempt_lines(attempt, resume.state is ResumeState.RUNNING, "마지막 실행")

    if actions:
        out += [BLANK, *action_lines(actions)]
    return out


# --- review tab ---------------------------------------------------------------------------


def _trace_tag(line: str) -> str:
    body = line.lstrip(" ")
    if " 미충족" in body or " 결과 없음" in body:
        return "warn"
    if " 실패" in body or "재확인" in body:
        return "bad" if " 실패" in body else "warn"
    if line == body and " 충족" in body:
        return "ok"
    return ""


def review_tab(board: TaskBoard, actions: list[tuple[str, str, Availability]]) -> list[Line]:
    if board.state is not BoardState.OK:
        return board_note(board) or [("muted", "작업 없음")]
    review = board.review
    if review is None:
        return [("muted", "저장소를 비교하지 않아 변경 검토가 없음 (--tasks와 --repo로 지정)")]

    out: list[Line] = [("h", "최종 승인")]
    tag = {ApprovalState.APPROVED: "ok", ApprovalState.REAPPROVE: "warn", ApprovalState.HELD: "warn"}.get(
        review.state, "ok" if review.approvable else "warn")
    summary = approval_summary(review, r.local_time).replace("review --approve로 직접 승인", "a 키로 직접 승인")
    out.append(kv(tag, "상태", summary))
    if review.state is not ApprovalState.APPROVED:
        out += [kv("bad", "보류 사유" if i == 0 else "", f"· {b.text}") for i, b in enumerate(review.blockers)]
    out += action_lines(actions)
    out.append(more("muted", WORKER_NOTE))

    code = review.code
    current, recorded = review.current_results
    out += [BLANK, ("h", "코드와 변경"), kv("", "코드 기준", basis_text(code))]
    if code.source.digest:
        out.append(more("muted", f"검증 대상 {scope_text()} 파일 {code.source.files}개"))
    if recorded:
        out.append(kv("ok" if current == recorded else "warn", "유효한 결과", f"현재 코드 기준 {current}/{recorded}"))
    out.append(kv("", "변경", changes_summary(review)))
    for line in change_lines(review):
        tag = "bad" if "보호 경로" in line else ("warn" if "확인 필요" in line else "")
        out.append(more(tag, line))
    if review.unlinked:
        out.append(more("warn", f"연결된 작업이 없는 변경 {len(review.unlinked)}개 · 승인할 때 내용을 확인했다고 "
                                "직접 표시해야 함"))
    protected = protected_summary(review)
    out.append(kv("bad" if "보류" in protected else "", "보호 경로", protected))

    out += [BLANK, ("h", "추적 그래프 (요구사항 → 작업 → 검증 → 결과 · 변경 파일)")]
    raw = review_trace_lines(review)
    out += [(_trace_tag(src), drawn) for src, drawn in zip(raw, tree_lines(raw))]
    return out


# --- recovery tab ---------------------------------------------------------------------------


RECOVERY_NOTE = ("이 화면은 종료 확인을 기록하지 않습니다. 실행 ID의 프로세스가 끝났는지 직접 확인한 뒤 "
                 "위 명령에 확인한 내용을 적어 실행하세요. 명령의 인자는 이 화면을 연 인자 그대로입니다.")


def recovery_tab(board: TaskBoard, confirm_command: Callable[[str], str], observe_command: str) -> list[Line]:
    if board.state is not BoardState.OK:
        return board_note(board) or [("muted", "작업 없음")]
    resume = board.resume
    out: list[Line] = [("h", "재개 판단")]
    if resume is None:
        return out + [("muted", "실행 기록 없음")]
    blocked = resume.error or (resume.blocking is not None)
    out.append(kv("warn" if blocked else "", "판단", resume.verdict()))
    out.append(kv("", "다음 행동", resume.next_action()))
    out.append(kv("", "마지막 흐름", r.flow_text(board) or "흐름 기록 없음"))

    out += [BLANK, ("h", "작업별 실행 기록")]
    needs_check = False
    for t in resume.tasks:
        tag = "warn" if t.state in RUN_NOTES else (
            "ok" if t.state is ResumeState.DONE else "")
        out.append(BLANK)
        out.append(kv(tag, t.task_id, f"{RESUME_TEXT[t.state]} · {t.reason}"))
        if t.attempt is None:
            out.append(more("muted", "실행 기록 없음"))
        else:
            out += _attempt_lines(t.attempt, t.state is ResumeState.RUNNING)
            if t.since:
                out.append(more("muted", f"기록 후 바뀐 것: {', '.join(t.since)}"))
        out.append(more("key" if t.state in (ResumeState.CHECK_RUN, ResumeState.FINISHING) else "",
                        f"지금 할 일: {t.next_action}"))
        if t.state is ResumeState.FINISHING:
            out.append(more("key", f"상태 조회(읽기 전용): {observe_command}"))
        if t.state is ResumeState.CHECK_RUN:
            needs_check = True
            runs = [run.run_id for run in t.attempt.runs] if t.attempt else []
            for hint in check_hint(runs).splitlines():
                out.append(more("warn", hint))
            if any(is_worker_run(run) for run in runs if run):
                out.append(more("key", f"상태 조회(읽기 전용): {observe_command}"))
            out.append(more("key", f"확인한 뒤 직접 기록: {confirm_command(t.task_id)}"))
    if needs_check:
        out += [BLANK, ("muted", RECOVERY_NOTE)]
    return out


# --- preflight tab ----------------------------------------------------------------------------


REASON_GROUPS: tuple[tuple[str, frozenset[Reason]], ...] = (
    ("승인", frozenset({Reason.REAL_DISPATCH_DISABLED, Reason.EXECUTION_APPROVAL_MISSING,
                       Reason.AUTOMATIC_RETRY_FORBIDDEN})),
    ("4-A", frozenset({Reason.EXPERIMENT_4A_NOT_PASSED})),
    ("인증", frozenset({Reason.SUBSCRIPTION_AUTH_UNVERIFIED, Reason.API_KEY_ENVIRONMENT_PRESENT})),
    ("과금", frozenset({Reason.BILLING_PATH_UNVERIFIED, Reason.EXTRA_USAGE_NOT_CONFIRMED_OFF,
                       Reason.AUTO_RECHARGE_NOT_CONFIRMED_OFF, Reason.API_KEY_BILLING_FORBIDDEN})),
    ("Orca 환경", frozenset({Reason.COORDINATOR_BINDING_MISSING, Reason.RUNTIME_UNAVAILABLE,
                            Reason.RUNTIME_STATUS_INVALID, Reason.ORCA_VERSION_UNPINNED})),
    ("사용량", frozenset({Reason.USAGE_LIMIT_WAITING_FOR_RESET, Reason.USAGE_RECHECK_REQUIRED})),
    ("실행 계약", frozenset({Reason.WORKER_DISPATCH_CONTRACT_UNVERIFIED})),
)


def dispatch_checklist(snap: s.Snapshot) -> list[Line]:
    """The dispatch gate's reasons grouped by what has to be settled (approval, auth, billing, ...)."""

    providers = list(snap.decisions)
    codes_by = {p: snap.decisions[p].reason_codes for p in providers}
    ordered: list[Reason] = []
    for p in providers:
        ordered += [code for code in codes_by[p] if code not in ordered]
    status = f"⛔ 보류 · 사유 {len(ordered)}건 · 현재 빌드는 Worker에 작업을 보내지 않음"
    waiting = [p for p in providers if snap.decisions[p].outcome.value == "WAITING_FOR_RESET"]
    if waiting:
        status += " · 리셋 대기: " + ", ".join(r.PROVIDER_NAMES[p] for p in waiting)
    out: list[Line] = [kv("bad" if ordered else "ok", "작업 전송", status)]
    grouped = [(name, [c for c in ordered if c in members]) for name, members in REASON_GROUPS]
    known = set().union(*(members for _, members in REASON_GROUPS))
    grouped.append(("기타", [c for c in ordered if c not in known]))
    for name, codes in grouped:
        for i, code in enumerate(codes):
            having = [p for p in providers if code in codes_by[p]]
            if len(having) == len(providers):
                text = f"✗ {r._reason_label(snap, code, None)}"
            else:
                text = "✗ " + " · ".join(f"{r.PROVIDER_NAMES[p]}: {r._reason_label(snap, code, p)}" for p in having)
            out.append(kv("bad", name if i == 0 else "", text))
    documented = sum(1 for i in WORKER_CONTRACT if i.documented is not Evidence.UNVERIFIED)
    observed = sum(1 for i in WORKER_CONTRACT if i.observed is Evidence.VERIFIED)
    total = len(WORKER_CONTRACT)
    out.append(kv("muted", "명령 근거", f"문서·소스 {documented}/{total} · 실측 {observed}/{total}"))
    return out


def connection_lines(snap: s.Snapshot) -> list[Line]:
    """The header's Orca part in full (the header itself is cut to the terminal width)."""

    out: list[Line] = [
        kv("", "Runtime", r.runtime_text(snap.runtime)),
        kv("", "바인딩", "Coordinator 터미널 바인딩 있음 (Orca 터미널에서 상속, 핸들 값은 표시하지 않음)"
           if snap.bound else "없음"),
        kv("", "현재 Run", r.run_text(snap.run)),
        kv("muted", "사용량", " · ".join(f"{r.PROVIDER_NAMES[p]} {r.UNKNOWN}" for p in Provider)),
    ]
    hint = r.hint(snap)
    if hint:
        out.append(("warn", hint))
    if not snap.bound:
        out.append(("muted", "바인딩이 없어도 로컬 작업·검증·검토 정보는 표시합니다. Run 조회와 Worker 전송 점검의 "
                             "바인딩 항목만 제한됩니다."))
    return out


def preflight_tab(snap: s.Snapshot | None, provider: Provider, outcome, running: bool,
                  available: Availability) -> list[Line]:
    out: list[Line] = []
    if snap is not None:
        out += [("h", "Worker 전송 판단")] + dispatch_checklist(snap) + [BLANK]
        out += [("h", "Orca 연결")] + connection_lines(snap) + [BLANK]
    out += [("h", "전송 전 점검 · 확인만 함 (Run 생성·Worker 기동·작업 전송 없음)"),
            ("key" if available.enabled else "muted",
             f"p 점검 실행 · o Provider 전환 (현재 {r.PROVIDER_NAMES[provider]}) · {available.reason}")]
    if running:
        out.append(("warn", "점검 중… (status·run-current만 읽기 전용으로 조회)"))
    if outcome is None:
        out.append(("muted", "아직 점검하지 않음"))
        return out
    out.append(("bad" if outcome.status != "ok" else "", outcome.title))
    out += [("bad" if line.lstrip().startswith("[") else "", line) for line in outcome.lines]
    return out
