"""Local verification flow: pick the next task, verify it, record, decide again.

The flow is deterministic. Tasks are considered in plan order; the first task
whose prerequisites are complete and that has a check the program can run
(``SUPPORTED_CHECKS``) that still needs a result is selected. Its checks run
one at a time through :func:`tasks.verification.verify`, which reuses the
results-file lock and merge. The flow stops at the first failure, at a run with
no tests, a timeout or launch failure, or a save that could not be recorded.

Nothing is retried. A check whose current result is ``failed`` is never run
again by the flow; the person fixes the cause and runs ``verify`` themselves.
A result whose check definition changed, whose verified code differs from the
files on disk now (``src/``, ``tests/``), or that lacks either digest is stale;
a stale *passed* result is re-checked, a stale *failed* result is left for the
person, like any failure.

What the flow did last is kept in ``<results>.flow.json`` so the status screen
can show the current task and why the flow stopped. Writing it never decides
anything: the results file stays the single source for completion.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .codebase import source_state
from .contract import Outcome, TaskPlan, VerificationResult, validate
from .paths import flow_status_path, tool_files
from .progress import (
    CheckProgress,
    CheckState,
    PlanProgress,
    StaleReason,
    TaskProgress,
    TaskState,
    WaitKind,
    WaitReason,
    evaluate,
)
from .verification import (
    DEFAULT_TIMEOUT_SECONDS,
    SUPPORTED_CHECKS,
    VerificationNotCompleted,
    VerificationNotRecorded,
    VerificationRun,
    VerificationSetupError,
    load_plan,
    load_results,
    verify,
)

FLOW_SCHEMA = "orca-orchestrator/flow-status@1"

STALE_TEXT = {
    StaleReason.NO_DIGEST: "계획 정보 없는 결과",
    StaleReason.CHANGED: "검증 조건 변경",
    StaleReason.NO_SOURCE: "코드 기준 없는 결과",
    StaleReason.CODE_CHANGED: "검증 후 코드 변경",
    StaleReason.SOURCE_UNKNOWN: "현재 코드 확인 불가",
}


# --- judging what comes next ------------------------------------------------------


def wait_reason_text(reason: WaitReason) -> str:
    if reason.kind is WaitKind.PREREQUISITE_FAILED:
        return f"선행 작업 {reason.task_id} 검증 실패"
    if reason.kind is WaitKind.PREREQUISITE_RECHECK:
        return f"선행 작업 {reason.task_id} 재확인 필요"
    if reason.passed == 0:
        return f"선행 작업 {reason.task_id} 검증 결과 없음"
    return f"선행 작업 {reason.task_id} 검증 미완료 ({reason.passed}/{reason.total} 통과)"


def runnable(cp: CheckProgress) -> bool:
    """The flow may run this check: supported, and missing or a stale *passed* result."""

    if cp.check.check_id not in SUPPORTED_CHECKS:
        return False
    if cp.state is CheckState.MISSING:
        return True
    return cp.state is CheckState.STALE and cp.result is not None and cp.result.outcome is Outcome.PASSED


def _need_text(cp: CheckProgress) -> str:
    if cp.state is CheckState.MISSING:
        return f"{cp.check.check_id} 결과 없음"
    return f"{cp.check.check_id} 재확인 필요 ({STALE_TEXT[cp.stale_reason]})"


SELECTION_RULE = "계획 순서에서 선행 작업이 모두 완료됐고 실행할 검증 조건이 남은 첫 작업"


@dataclass(frozen=True)
class Selection:
    task_id: str
    check_ids: tuple[str, ...]  # runnable checks, plan order
    reason: str  # why this task: prerequisites and what each check needs


@dataclass(frozen=True)
class Blocker:
    task_id: str
    reason: str  # short, fits a screen line
    hint: str = ""  # what the person can do


@dataclass(frozen=True)
class FlowView:
    plan: TaskPlan
    progress: PlanProgress
    next: Selection | None
    blockers: tuple[Blocker, ...]  # unfinished tasks that cannot be selected now, plan order

    @property
    def all_verified(self) -> bool:
        return self.progress.all_verified


VERIFY_HINT = "원인을 고친 뒤 verify로 직접 다시 실행 (흐름은 자동 재실행하지 않음)"
UNSUPPORTED_HINT = "흐름이 자동 실행하는 검증 조건은 " + ", ".join(SUPPORTED_CHECKS) + "뿐임"


def _blocker(tp: TaskProgress) -> Blocker | None:
    tid = tp.task.task_id
    if tp.state is TaskState.VERIFIED:
        return None
    if tp.state is TaskState.FAILED:
        return Blocker(tid, f"{', '.join(tp.failed_checks)} 검증 실패", VERIFY_HINT)
    if tp.state is TaskState.WAITING:
        return Blocker(tid, " · ".join(wait_reason_text(w) for w in tp.waiting_on))
    unsupported = [cp.check.check_id for cp in tp.checks
                   if cp.state is not CheckState.PASSED and cp.check.check_id not in SUPPORTED_CHECKS]
    stale_failed = [cp.check.check_id for cp in tp.checks
                    if cp.state is CheckState.STALE and not runnable(cp) and cp.check.check_id in SUPPORTED_CHECKS]
    if stale_failed:
        return Blocker(tid, f"{', '.join(stale_failed)} 이전 실패 결과 재확인 필요", VERIFY_HINT)
    if unsupported:
        return Blocker(tid, f"자동 실행할 수 없는 검증 조건 {', '.join(unsupported)}", UNSUPPORTED_HINT)
    return None


def select(plan: TaskPlan, progress: PlanProgress) -> FlowView:
    selection = None
    blockers = []
    for tp in progress.tasks:
        candidates = tuple(cp for cp in tp.checks if runnable(cp))
        if selection is None and tp.state in (TaskState.READY, TaskState.RECHECK) and candidates:
            deps = ", ".join(tp.task.dependencies)
            parts = [f"선행 {deps} 완료" if deps else "선행 작업 없음"]
            parts += [_need_text(cp) for cp in candidates]
            selection = Selection(tp.task.task_id, tuple(cp.check.check_id for cp in candidates), " · ".join(parts))
            continue
        if tp.state in (TaskState.READY, TaskState.RECHECK) and candidates:
            continue  # selectable later, not blocked
        blocker = _blocker(tp)
        if blocker:
            blockers.append(blocker)
    return FlowView(plan, progress, selection, tuple(blockers))


def inspect(plan_path: Path, results_path: Path, repo: Path | None = None) -> FlowView:
    """Reads both files and judges; raises VerificationSetupError when they cannot be used.

    With ``repo``, results are also judged against the code on disk now.
    """

    plan = load_plan(plan_path)
    results = load_results(results_path)
    issues = validate(plan, results)
    if issues:
        raise VerificationSetupError("작업 정의 또는 검증 결과에 오류가 있어 판단하지 않음", issues)
    source = source_state(repo, tool_files(plan_path, results_path)) if repo is not None else None
    return select(plan, evaluate(plan, results, source))


# --- requirement trace (FR-07) ------------------------------------------------------

TASK_STATE_TEXT = {
    TaskState.VERIFIED: "완료",
    TaskState.FAILED: "검증 실패",
    TaskState.RECHECK: "재확인",
    TaskState.READY: "준비",
    TaskState.WAITING: "대기",
}


def check_text(cp: CheckProgress) -> str:
    r = cp.result
    if cp.state is CheckState.MISSING:
        return f"{cp.check.check_id} 결과 없음"
    detail = []
    if r is not None and r.exit_code is not None:
        detail.append(f"종료 코드 {r.exit_code}")
    if r is not None and r.evidence:
        detail.append(r.evidence)
    tail = f" · {' · '.join(detail)}" if detail else ""
    if cp.state is CheckState.STALE:
        previous = "통과" if r.outcome is Outcome.PASSED else "실패"
        return f"{cp.check.check_id} 재확인 필요 ({STALE_TEXT[cp.stale_reason]}, 이전 {previous}){tail}"
    return f"{cp.check.check_id} {'통과' if cp.state is CheckState.PASSED else '실패'}{tail}"


def trace_lines(progress: PlanProgress) -> list[str]:
    """Requirement → task → check → recorded result, as indented text."""

    by_id = {tp.task.task_id: tp for tp in progress.tasks}
    lines = []
    for rp in progress.requirements:
        req = rp.requirement
        lines.append(f"{req.requirement_id} {'충족' if rp.satisfied else '미충족'} · {req.title}")
        if not rp.task_ids:
            lines.append("  연결된 작업 없음")
        for tid in rp.task_ids:
            tp = by_id[tid]
            lines.append(f"  {tid} {TASK_STATE_TEXT[tp.state]} · {tp.task.title}")
            for cp in tp.checks:
                lines.append(f"    {check_text(cp)}")
    return lines


# --- flow status file -------------------------------------------------------------


class StopKind(str, Enum):
    DONE = "done"  # every task verified locally; final approval is the person's
    WAITING = "waiting"  # nothing can be selected; reasons listed
    LIMIT = "limit"  # --max-tasks reached
    FAILED = "failed"
    NOT_COMPLETED = "not_completed"  # timeout, launch failure, no tests ran
    NOT_RECORDED = "not_recorded"  # ran, but the result could not be saved
    REFUSED = "refused"  # plan/results unusable, nothing ran


@dataclass(frozen=True)
class FlowEvent:
    kind: StopKind
    message: str
    at: str  # UTC ISO time
    task_id: str | None = None
    check_id: str | None = None


@dataclass(frozen=True)
class FlowStatus:
    state: str  # "running" or "stopped"
    updated_at: str
    current: tuple[str, str, str] | None = None  # task, check, started_at
    last: FlowEvent | None = None
    verified: tuple[str, ...] = ()  # tasks completed by this flow run


class FlowStatusError(ValueError):
    pass


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def status_document(status: FlowStatus) -> dict[str, Any]:
    doc: dict[str, Any] = {"schema": FLOW_SCHEMA, "state": status.state, "updated_at": status.updated_at}
    doc["current"] = (
        {"task": status.current[0], "check": status.current[1], "started_at": status.current[2]}
        if status.current else None
    )
    if status.last is None:
        doc["last"] = None
    else:
        ev = status.last
        doc["last"] = {"kind": ev.kind.value, "message": ev.message, "at": ev.at,
                       "task": ev.task_id, "check": ev.check_id}
    doc["verified"] = list(status.verified)
    return doc


def write_flow_status(path: Path, status: FlowStatus) -> None:
    text = json.dumps(status_document(status), ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _str(value: Any, where: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise FlowStatusError(f"{where}: 문자열이 아님")
    return value


def parse_flow_status(data: Any) -> FlowStatus:
    if not isinstance(data, dict) or data.get("schema") != FLOW_SCHEMA:
        raise FlowStatusError(f"schema가 '{FLOW_SCHEMA}'가 아님")
    state = data.get("state")
    if state not in ("running", "stopped"):
        raise FlowStatusError("state가 running 또는 stopped가 아님")
    current = data.get("current")
    if current is not None:
        if not isinstance(current, dict):
            raise FlowStatusError("current: 객체가 아님")
        current = (_str(current.get("task"), "current.task"), _str(current.get("check"), "current.check"),
                   _str(current.get("started_at"), "current.started_at"))
    last = data.get("last")
    if last is not None:
        if not isinstance(last, dict):
            raise FlowStatusError("last: 객체가 아님")
        try:
            kind = StopKind(last.get("kind"))
        except ValueError:
            raise FlowStatusError("last.kind를 알 수 없음") from None
        last = FlowEvent(kind, _str(last.get("message"), "last.message"), _str(last.get("at"), "last.at"),
                         _str(last.get("task"), "last.task", True), _str(last.get("check"), "last.check", True))
    verified = data.get("verified", [])
    if not isinstance(verified, list) or not all(isinstance(v, str) for v in verified):
        raise FlowStatusError("verified: 문자열 목록이 아님")
    return FlowStatus(state, _str(data.get("updated_at"), "updated_at"), current, last, tuple(verified))


def read_flow_status(path: Path) -> FlowStatus | None:
    """None when the flow has never run for these results."""

    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise FlowStatusError(f"{path.name}을(를) 읽을 수 없음") from None
    return parse_flow_status(data)


# --- running ---------------------------------------------------------------------


@dataclass
class FlowReport:
    stop: FlowEvent
    view: FlowView | None
    runs: list[VerificationRun] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)


Notify = Callable[[str, Any], None]


def run_flow(
    plan_path: str | Path,
    results_path: str | Path,
    *,
    repo: str | Path = ".",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_tasks: int | None = None,
    verify_fn: Callable[..., VerificationRun] = verify,
    notify: Notify = lambda kind, payload: None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> FlowReport:
    """Verify ready tasks one check at a time until done, blocked or stopped.

    ``notify(kind, payload)`` reports progress: ``"select"`` (Selection),
    ``"start"`` ((task, check)), ``"result"`` (VerificationRun), ``"warning"`` (str).
    """

    plan_path, results_path = Path(plan_path), Path(results_path)
    status_path = flow_status_path(results_path)
    verified: list[str] = []
    runs: list[VerificationRun] = []
    attempted: set[tuple[str, str]] = set()
    selected_tasks = 0

    def save_status(state: str, current=None, last=None) -> None:
        try:
            write_flow_status(status_path, FlowStatus(state, _iso(now()), current, last, tuple(verified)))
        except OSError as exc:
            notify("warning", f"흐름 상태 파일을 쓰지 못함 ({status_path.name}: {type(exc).__name__})")

    def stop(kind: StopKind, message: str, view: FlowView | None, task=None, check=None) -> FlowReport:
        event = FlowEvent(kind, message, _iso(now()), task, check)
        if results_path.parent.is_dir():
            save_status("stopped", last=event)
        return FlowReport(event, view, runs, verified)

    while True:
        try:
            view = inspect(plan_path, results_path, Path(repo))
        except VerificationSetupError as exc:
            return stop(StopKind.REFUSED, exc.message, None)
        selection = view.next
        if selection is None:
            if view.all_verified:
                return stop(StopKind.DONE, "모든 작업의 로컬 검증 통과 · 최종 사용자 승인 대기", view)
            first = view.blockers[0] if view.blockers else None
            message = f"선택할 수 있는 작업 없음 · {first.task_id}: {first.reason}" if first else "선택할 수 있는 작업 없음"
            return stop(StopKind.WAITING, message, view, first.task_id if first else None)
        if max_tasks is not None and selected_tasks >= max_tasks:
            return stop(StopKind.LIMIT, f"지정한 작업 수({max_tasks})만큼 실행 · 다음 작업 {selection.task_id}", view,
                        selection.task_id)
        selected_tasks += 1
        notify("select", selection)
        for check_id in selection.check_ids:
            key = (selection.task_id, check_id)
            if key in attempted:  # cannot happen with a valid result file; never loop
                return stop(StopKind.NOT_COMPLETED, "같은 검증 조건이 다시 선택되어 멈춤", view, *key)
            attempted.add(key)
            notify("start", key)
            save_status("running", current=(selection.task_id, check_id, _iso(now())))
            try:
                run = verify_fn(plan_path, results_path, selection.task_id, check_id, repo=repo, timeout=timeout)
            except VerificationSetupError as exc:
                return stop(StopKind.REFUSED, exc.message, view, *key)
            except VerificationNotRecorded as exc:
                outcome = "통과" if exc.result.outcome is Outcome.PASSED else "실패"
                return stop(StopKind.NOT_RECORDED, f"테스트는 끝났지만({outcome}) {exc.message}", view, *key)
            except VerificationNotCompleted as exc:
                return stop(StopKind.NOT_COMPLETED, exc.message, view, *key)
            runs.append(run)
            notify("result", run)
            if run.result.outcome is not Outcome.PASSED:
                return stop(StopKind.FAILED, f"검증 실패 (종료 코드 {run.result.exit_code}) · {run.summary}", view, *key)
        try:
            after = inspect(plan_path, results_path, Path(repo))
        except VerificationSetupError as exc:
            return stop(StopKind.REFUSED, exc.message, None)
        done = next((t for t in after.progress.tasks if t.task.task_id == selection.task_id), None)
        if done is not None and done.state is TaskState.VERIFIED:
            verified.append(selection.task_id)
