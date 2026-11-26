"""Change review and the person's final decision (FR-06, FR-07, FR-08).

A review puts together, for the repository as it is now:

* verification results judged against the current plan and the current code
  (a result only counts when its ``source_digest`` matches the files under
  ``src/`` and ``tests/`` now),
* the files that differ from the plan's ``base``, each linked to the tasks
  whose ``paths`` cover it; files no task covers need the person's confirmation,
* changes to each task's ``protected_paths``,
* what blocks approval, and
* the decision the person last recorded and whether it still applies.

Only :func:`record_decision` writes, and only when the person runs
``python -m orca_orchestrator.review --approve`` or ``--hold``. The flow, the
status screen, a refresh or passing tests never create or change a decision.
An approval stops applying when the plan, the verified code, the change set or
the base changes, or when something blocks approval again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .codebase import (
    ChangeSet,
    CodeBasis,
    FileChange,
    GitReader,
    SourceState,
    code_basis,
    compare_changes,
    source_state,
)
from .contract import (
    PlanFormatError,
    TaskPlan,
    VerificationResult,
    parse_plan,
    path_matches,
    plan_digest,
    validate,
)
from .flow import STALE_TEXT, TASK_STATE_TEXT, check_text
from .paths import approval_path, tool_files
from .progress import CheckState, PlanProgress, TaskState, evaluate
from .verification import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    LockTimeout,
    VerificationSetupError,
    _read_json,
    load_results,
    results_lock,
    write_json,
)

APPROVAL_SCHEMA = "orca-orchestrator/approval@1"


# --- inputs --------------------------------------------------------------------


@dataclass(frozen=True)
class CodeState:
    source: SourceState
    basis: CodeBasis
    changes: ChangeSet


def read_code(repo: str | Path, plan: TaskPlan, exclude: tuple[Path, ...] = (),
              git: GitReader | None = None) -> CodeState:
    git = git or GitReader(repo)
    return CodeState(source_state(repo, exclude), code_basis(repo, exclude, git),
                     compare_changes(repo, plan.base, exclude, git))


class Decision(str, Enum):
    APPROVED = "approved"
    HELD = "held"


@dataclass(frozen=True)
class ApprovalRecord:
    decision: Decision
    at: str
    note: str | None = None
    plan_digest: str | None = None
    source_digest: str | None = None
    base_sha: str | None = None
    changes_digest: str | None = None
    confirmed_unlinked: tuple[str, ...] = ()


class ApprovalFormatError(ValueError):
    pass


_RECORD_EXTRA = ("base", "git_head", "git_dirty", "changes", "evidence", "requirements")


def _opt_str(data: dict, key: str) -> str | None:
    value = data.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise ApprovalFormatError(f"{key}: 문자열이 아님")
    return value


def parse_approval(data: Any) -> ApprovalRecord:
    if not isinstance(data, dict) or data.get("schema") != APPROVAL_SCHEMA:
        raise ApprovalFormatError(f"schema가 '{APPROVAL_SCHEMA}'가 아님")
    known = {"schema", "decision", "at", "note", "plan_digest", "source_digest", "base_sha",
             "changes_digest", "confirmed_unlinked", *_RECORD_EXTRA}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ApprovalFormatError(f"알 수 없는 필드 '{unknown[0]}'")
    try:
        decision = Decision(data.get("decision"))
    except ValueError:
        raise ApprovalFormatError("decision이 approved 또는 held가 아님") from None
    at = _opt_str(data, "at")
    if at is None:
        raise ApprovalFormatError("at 없음")
    confirmed = data.get("confirmed_unlinked", [])
    if not isinstance(confirmed, list) or not all(isinstance(p, str) for p in confirmed):
        raise ApprovalFormatError("confirmed_unlinked: 문자열 목록이 아님")
    return ApprovalRecord(decision, at, _opt_str(data, "note"), _opt_str(data, "plan_digest"),
                          _opt_str(data, "source_digest"), _opt_str(data, "base_sha"),
                          _opt_str(data, "changes_digest"), tuple(confirmed))


def parse_approval_text(text: str | None, name: str) -> tuple[ApprovalRecord | None, str | None]:
    """(record, error); both None when there is no record yet."""

    if text is None:
        return None, None
    try:
        return parse_approval(json.loads(text)), None
    except json.JSONDecodeError:
        return None, f"{name}: JSON 형식 오류"
    except ApprovalFormatError as exc:
        return None, f"{name}: {exc}"


# --- judging -------------------------------------------------------------------


class BlockKind(str, Enum):
    CODE = "code"  # the current code could not be fingerprinted
    TASK = "task"  # a task is not verified for the current plan and code
    REQUIREMENT = "requirement"  # a requirement has no task
    COMPARE = "compare"  # the change set could not be determined
    PROTECTED = "protected"  # a protected path changed, or could not be checked
    RECORD = "record"  # the decision file exists but cannot be read


@dataclass(frozen=True)
class Blocker:
    kind: BlockKind
    subject: str
    text: str


class ApprovalState(str, Enum):
    NONE = "none"  # no decision recorded
    APPROVED = "approved"  # approved, and it still applies
    REAPPROVE = "reapprove"  # approved earlier, but something changed since
    HELD = "held"  # the person put it on hold


@dataclass(frozen=True)
class TaskChanges:
    task_id: str
    changes: tuple[FileChange, ...]  # changed files under the task's paths
    protected: tuple[FileChange, ...]  # changed files under the task's protected paths


@dataclass(frozen=True)
class Review:
    plan: TaskPlan
    plan_digest: str
    progress: PlanProgress
    code: CodeState
    tasks: tuple[TaskChanges, ...]  # plan order
    unlinked: tuple[FileChange, ...]  # changes no task's paths cover: need confirmation
    blockers: tuple[Blocker, ...]
    record: ApprovalRecord | None
    state: ApprovalState
    stale_reasons: tuple[str, ...] = ()  # why an approval no longer applies

    @property
    def approvable(self) -> bool:
        return not self.blockers

    def task(self, task_id: str) -> TaskChanges:
        return next(t for t in self.tasks if t.task_id == task_id)

    def linked_tasks(self, path: str) -> tuple[str, ...]:
        return tuple(t.task_id for t in self.tasks if any(c.path == path for c in t.changes))

    @property
    def current_results(self) -> tuple[int, int]:
        """(results that apply to the current plan and code, results recorded)."""

        recorded = [cp for tp in self.progress.tasks for cp in tp.checks if cp.result is not None]
        current = [cp for cp in recorded if cp.state in (CheckState.PASSED, CheckState.FAILED)]
        return len(current), len(recorded)


def _check_needs(tp) -> str:
    parts = []
    for cp in tp.checks:
        if cp.state is CheckState.PASSED:
            continue
        if cp.state is CheckState.FAILED:
            parts.append(f"{cp.check.check_id} 검증 실패")
        elif cp.state is CheckState.MISSING:
            parts.append(f"{cp.check.check_id} 결과 없음")
        else:
            parts.append(f"{cp.check.check_id} 재확인 필요 ({STALE_TEXT[cp.stale_reason]})")
    return ", ".join(parts)


def build_review(plan: TaskPlan, digest: str, results: tuple[VerificationResult, ...], code: CodeState,
                 record: ApprovalRecord | None = None, record_error: str | None = None) -> Review:
    """Pure judgment from already-read inputs. ``plan`` and ``results`` must be valid."""

    progress = evaluate(plan, results, code.source)
    changes = code.changes
    found = changes.changes if changes.ok else ()
    tasks = tuple(
        TaskChanges(
            t.task_id,
            tuple(c for c in found if path_matches(c.path, t.paths)),
            tuple(c for c in found if path_matches(c.path, t.protected_paths)),
        )
        for t in plan.tasks
    )
    unlinked = tuple(c for c in found if not any(path_matches(c.path, t.paths) for t in plan.tasks))

    blockers: list[Blocker] = []
    if record_error:
        blockers.append(Blocker(BlockKind.RECORD, "", f"승인 기록을 읽을 수 없음 · {record_error}"))
    if code.source.digest is None:
        blockers.append(Blocker(BlockKind.CODE, "", f"현재 코드 확인 불가 · {code.source.error}"))
    for tp in progress.tasks:
        if tp.state is not TaskState.VERIFIED:
            blockers.append(Blocker(BlockKind.TASK, tp.task.task_id, f"{tp.task.task_id}: {_check_needs(tp)}"))
    for rp in progress.requirements:
        if not rp.task_ids:
            rid = rp.requirement.requirement_id
            blockers.append(Blocker(BlockKind.REQUIREMENT, rid, f"{rid}: 연결된 작업 없음"))
    if not changes.ok:
        blockers.append(Blocker(BlockKind.COMPARE, "", f"변경 비교 불가 · {changes.error}"))
        for t in plan.tasks:
            if t.protected_paths:
                blockers.append(Blocker(BlockKind.PROTECTED, t.task_id, f"{t.task_id}: 보호 경로 확인 불가"))
    else:
        for tc in tasks:
            for change in tc.protected:
                blockers.append(Blocker(BlockKind.PROTECTED, tc.task_id,
                                        f"{tc.task_id}: 보호 경로 변경 {change.text()}"))

    state, reasons = ApprovalState.NONE, []
    if record is not None and record.decision is Decision.HELD:
        state = ApprovalState.HELD
    elif record is not None:
        if record.plan_digest != digest:
            reasons.append("승인 후 작업 정의 변경")
        if record.source_digest != code.source.digest:
            reasons.append("승인 후 코드 변경")
        if record.base_sha != changes.base_sha:
            reasons.append("비교 기준 변경")
        elif record.changes_digest != changes.digest and record.source_digest == code.source.digest:
            reasons.append("승인 후 변경 파일 달라짐")
        if blockers:
            reasons.append(f"보류 사유 {len(blockers)}건")
        state = ApprovalState.REAPPROVE if reasons else ApprovalState.APPROVED
    return Review(plan, digest, progress, code, tasks, unlinked, tuple(blockers), record, state, tuple(reasons))


def load_review(plan_path: str | Path, results_path: str | Path, repo: str | Path = ".",
                git: GitReader | None = None) -> Review:
    """Reads the plan, results and decision files and the repository; writes nothing.

    Raises VerificationSetupError when the plan or results cannot be used.
    """

    plan_path, results_path = Path(plan_path), Path(results_path)
    data = _read_json(plan_path, "작업 정의")
    try:
        plan = parse_plan(data)
    except PlanFormatError as exc:
        raise VerificationSetupError(f"작업 정의 형식 오류 ({plan_path.name}): {exc}") from None
    results = load_results(results_path)
    issues = validate(plan, results)
    if issues:
        raise VerificationSetupError("작업 정의 또는 검증 결과에 오류가 있어 검토하지 않음", issues)
    record_file = approval_path(results_path)
    text = None
    if record_file.exists():
        try:
            text = record_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = "\0"
    record, error = parse_approval_text(text, record_file.name)
    code = read_code(repo, plan, tool_files(plan_path, results_path), git)
    return build_review(plan, plan_digest(data), results, code, record, error)


# --- recording the person's decision -------------------------------------------


class DecisionRefused(Exception):
    def __init__(self, message: str, review: Review | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.review = review


def approval_document(review: Review, decision: Decision, at: str, note: str | None) -> dict[str, Any]:
    code = review.code
    by_task = {tp.task.task_id: tp for tp in review.progress.tasks}
    return {
        "schema": APPROVAL_SCHEMA,
        "decision": decision.value,
        "at": at,
        "note": note,
        "plan_digest": review.plan_digest,
        "source_digest": code.source.digest,
        "base": review.plan.base,
        "base_sha": code.changes.base_sha,
        "changes_digest": code.changes.digest,
        "git_head": code.basis.head,
        "git_dirty": code.basis.dirty,
        "changes": [{"path": c.path, "status": c.status.value, "tasks": list(review.linked_tasks(c.path))}
                    for c in (code.changes.changes if code.changes.ok else ())],
        "confirmed_unlinked": [c.path for c in review.unlinked] if decision is Decision.APPROVED else [],
        "evidence": [
            {"task": tid, "check": cp.check.check_id, "state": cp.state.value,
             "outcome": cp.result.outcome.value if cp.result else None,
             "exit_code": cp.result.exit_code if cp.result else None,
             "evidence": cp.result.evidence if cp.result else None,
             "source_digest": cp.result.source_digest if cp.result else None}
            for tid, tp in by_task.items() for cp in tp.checks
        ],
        "requirements": [{"id": rp.requirement.requirement_id, "satisfied": rp.satisfied, "tasks": list(rp.task_ids)}
                         for rp in review.progress.requirements],
    }


def record_decision(
    plan_path: str | Path,
    results_path: str | Path,
    decision: Decision,
    *,
    repo: str | Path = ".",
    note: str | None = None,
    confirm_unlinked: bool = False,
    git: GitReader | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> tuple[Review, Path]:
    """Re-reads everything under the results lock, checks, and writes the decision file.

    Approval is refused (nothing written) unless every blocker is clear and,
    when changes no task covers exist, ``confirm_unlinked`` is given.
    A hold can always be recorded and needs a ``note``.
    """

    results_path = Path(results_path)
    if decision is Decision.HELD and not (note and note.strip()):
        raise DecisionRefused("보류 사유를 입력해야 합니다 (--hold \"사유\")")
    if not results_path.parent.is_dir():
        raise VerificationSetupError(f"검증 결과 폴더가 없음 ({results_path.name})")
    target = approval_path(results_path)
    try:
        with results_lock(results_path, lock_timeout):
            review = load_review(plan_path, results_path, repo, git)
            if any(b.kind is BlockKind.RECORD for b in review.blockers):
                raise DecisionRefused(f"기존 승인 기록을 읽을 수 없어 덮어쓰지 않음 ({target.name})", review)
            if decision is Decision.APPROVED:
                if not review.approvable:
                    raise DecisionRefused(f"승인할 수 없음 · 보류 사유 {len(review.blockers)}건", review)
                if review.unlinked and not confirm_unlinked:
                    raise DecisionRefused(
                        f"연결된 작업이 없는 변경 {len(review.unlinked)}개를 확인해야 함 (--confirm-unlinked)", review)
            at = now().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            doc = approval_document(review, decision, at, note.strip() if note else None)
            try:
                write_json(target, doc)
            except OSError as exc:
                raise DecisionRefused(f"승인 기록을 저장하지 못함 ({target.name}: {type(exc).__name__})", review) from None
    except LockTimeout:
        raise DecisionRefused(f"다른 저장이 {lock_timeout:g}초 안에 끝나지 않아 기록하지 않음") from None
    return load_review(plan_path, results_path, repo, git), target


# --- text ----------------------------------------------------------------------


def basis_text(code: CodeState) -> str:
    source = code.source
    if source.digest is None:
        return f"확인 불가 · {source.error}"
    return f"{code.basis.text()} · 지문 {source.digest[:8]}"


def changes_summary(review: Review) -> str:
    changes = review.code.changes
    if not changes.ok:
        return f"비교 불가 · {changes.error}"
    base = f"{changes.base}({changes.base_sha[:7]})" if changes.base_sha else str(changes.base)
    if not changes.changes:
        return f"{base} 대비 변경 없음"
    linked = [f"{tc.task_id} {len(tc.changes)}" for tc in review.tasks if tc.changes]
    parts = [f"{base} 대비 {len(changes.changes)}개", *linked]
    if review.unlinked:
        parts.append(f"확인 필요 {len(review.unlinked)}")
    return " · ".join(parts)


def change_lines(review: Review) -> list[str]:
    """One line per changed file: which tasks it belongs to, or that it needs confirmation."""

    changes = review.code.changes
    if not changes.ok:
        return []
    lines = []
    for change in changes.changes:
        tasks = review.linked_tasks(change.path)
        target = ", ".join(tasks) if tasks else "연결된 작업 없음 · 확인 필요"
        guarded = [tc.task_id for tc in review.tasks if any(c.path == change.path for c in tc.protected)]
        if guarded:
            target += f" · {', '.join(guarded)} 보호 경로"
        lines.append(f"{change.text()} → {target}")
    return lines


def protected_summary(review: Review) -> str:
    guarded = [t for t in review.plan.tasks if t.protected_paths]
    if not guarded:
        return "지정 안 함"
    hits = [b for b in review.blockers if b.kind is BlockKind.PROTECTED]
    if not review.code.changes.ok:
        return f"확인 불가 · {len(guarded)}개 작업"
    if hits:
        return f"변경 {len(hits)}건 · 승인 보류"
    paths = sorted({p for t in guarded for p in t.protected_paths})
    return f"변경 없음 · {', '.join(paths)}"


def approval_summary(review: Review, local_time: Callable[[str], str] = lambda s: s) -> str:
    record = review.record
    if review.state is ApprovalState.APPROVED:
        return f"승인됨 · {local_time(record.at)} · 코드 {(record.source_digest or '')[:8]}"
    if review.state is ApprovalState.REAPPROVE:
        return f"재승인 필요 · {' · '.join(review.stale_reasons)}"
    if review.state is ApprovalState.HELD:
        return f"보류 (사용자 결정) · {local_time(record.at)} · {record.note}"
    if review.approvable:
        extra = f" · 연결 없는 변경 {len(review.unlinked)}개 확인 필요" if review.unlinked else ""
        return f"승인 가능 · review --approve로 직접 승인{extra} (자동 승인 없음)"
    return f"보류 · 사유 {len(review.blockers)}건 · 해결 전에는 승인할 수 없음"


def review_trace_lines(review: Review) -> list[str]:
    """Requirement → task → check → result, with the code each result verified and the task's changes."""

    by_id = {tp.task.task_id: tp for tp in review.progress.tasks}
    current = review.code.source.digest
    lines = []
    for rp in review.progress.requirements:
        req = rp.requirement
        lines.append(f"{req.requirement_id} {'충족' if rp.satisfied else '미충족'} · {req.title}")
        if not rp.task_ids:
            lines.append("  연결된 작업 없음")
        for tid in rp.task_ids:
            tp = by_id[tid]
            lines.append(f"  {tid} {TASK_STATE_TEXT[tp.state]} · {tp.task.title}")
            for cp in tp.checks:
                code = ""
                if cp.result is not None:
                    sd = cp.result.source_digest
                    if sd is None:
                        code = " · 검증한 코드 기록 없음"
                    else:
                        same = "현재와 같음" if sd == current else "현재와 다름"
                        head = f"HEAD {cp.result.git_head[:7]}{' + 커밋 전 변경' if cp.result.git_dirty else ''}, " \
                            if cp.result.git_head else ""
                        code = f" · 검증한 코드 {head}지문 {sd[:8]} ({same})"
                lines.append(f"    {check_text(cp)}{code}")
            tc = review.task(tid)
            if review.code.changes.ok:
                changed = ", ".join(c.text() for c in tc.changes) if tc.changes else "없음"
                lines.append(f"    변경 파일: {changed}")
                for change in tc.protected:
                    lines.append(f"    보호 경로 변경: {change.text()}")
    return lines
