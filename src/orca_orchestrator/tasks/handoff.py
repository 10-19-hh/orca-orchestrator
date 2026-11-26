"""Execution records, handoff and recovery between sessions (FR-05, FR-09).

``<results>.handoff.json`` keeps one *attempt* per time a session took a task:
the task's requirements, checks and scope, the plan's base and the code it
started from, the executor and each check's run ID, how each run ended, what is
done and what remains, why it stopped and what to do next. It refers to the
results file and the change set by name and digest; logs and diffs are not
copied. The results file stays the only source for completion, the approval
file for the person's decision, ``.flow.json`` for what the last flow did.

One session at a time works on a task: it holds ``<results>.run-<id>.lock``
for the whole attempt. The operating system releases that lock when the
process ends for any reason, so an attempt still marked ``running`` whose lock
is free was cut off. Such an attempt, and one that ended without proof that
everything stopped (a timeout, an interruption), blocks every new run of the
task until the person confirms it stopped (:func:`confirm_stopped`) or an
executor status check confirms it.

Worker runs (``tasks/worker_runs.py``) use the same record: Orca's Dispatch ID
as run ID (``dispatch:<id>``) and the last status Orca reported for it.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

from .codebase import ChangeSet, CodeBasis, SourceState, code_basis, compare_changes, source_state
from .contract import Outcome, TaskPlan, check_digest, plan_digest
from .locks import LockTimeout, exclusive, hold, is_held
from .paths import handoff_path, lock_path, run_lock_path, tool_files
from .progress import CheckState, PlanProgress, TaskState, evaluate
from .verification import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    VerificationSetupError,
    _read_json,
    load_results,
    write_json,
)

HANDOFF_SCHEMA = "orca-orchestrator/handoff@1"
SESSION_ID = f"s-{uuid.uuid4().hex[:8]}"  # this process
EXECUTOR_LOCAL = "local-unittest"
MAX_ATTEMPTS = 200  # oldest finished attempts are dropped beyond this


def _now_iso(now: Callable[[], datetime]) -> str:
    return now().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- records -------------------------------------------------------------------


class RunEnd(str, Enum):
    EXITED = "exited"  # the process ended; its outcome is known (recorded or explained)
    NOT_STARTED = "not_started"  # it never started
    UNKNOWN = "unknown"  # timed out or cut off: that everything stopped is not confirmed


class AttemptState(str, Enum):
    RUNNING = "running"
    ENDED = "ended"
    UNKNOWN = "unknown"  # ended, but at least one run's end is unknown


@dataclass(frozen=True)
class CheckRun:
    check: str
    started_at: str
    run_id: str | None = None  # executor's ID for this run; None: not observable
    ended_at: str | None = None
    end: RunEnd | None = None  # None while running (or when cut off)
    outcome: str | None = None  # passed / failed, when recorded in the results file
    exit_code: int | None = None
    message: str | None = None
    result_ref: str | None = None  # "<results file>#<task>/<check>"
    # last executor status seen for this run (Worker runs): when, which end it implies, what was seen
    observed_at: str | None = None
    observed_state: str | None = None  # running / exited / unknown
    observed: str | None = None


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    task: str
    requirements: tuple[str, ...]
    checks: tuple[str, ...]  # planned in this attempt
    paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    base: str | None
    base_sha: str | None
    changes_digest: str | None
    changes_count: int | None
    plan_digest: str | None
    check_digests: dict[str, str]
    source_digest: str | None
    git_head: str | None
    git_dirty: bool | None
    session: str
    owner_pid: int | None
    executor: str
    started_at: str
    state: AttemptState
    runs: tuple[CheckRun, ...] = ()
    ended_at: str | None = None
    done: tuple[str, ...] = ()
    remaining: tuple[str, ...] = ()
    reason: str | None = None
    next_action: str | None = None
    resolved_at: str | None = None
    resolved_by: str | None = None
    resolved_note: str | None = None

    @property
    def resolved(self) -> bool:
        return self.resolved_at is not None

    @property
    def last_run(self) -> CheckRun | None:
        return self.runs[-1] if self.runs else None


class HandoffFormatError(ValueError):
    pass


def _run_doc(r: CheckRun) -> dict[str, Any]:
    doc = {"check": r.check, "started_at": r.started_at, "run_id": r.run_id, "ended_at": r.ended_at,
           "end": r.end.value if r.end else None, "outcome": r.outcome, "exit_code": r.exit_code,
           "message": r.message, "result_ref": r.result_ref}
    if r.observed_at is not None:  # only runs an executor status query looked at
        doc["observed"] = {"at": r.observed_at, "state": r.observed_state, "text": r.observed}
    return doc


def attempt_doc(a: Attempt) -> dict[str, Any]:
    return {
        "attempt": a.attempt_id, "task": a.task, "requirements": list(a.requirements), "checks": list(a.checks),
        "paths": list(a.paths), "protected_paths": list(a.protected_paths),
        "base": a.base, "base_sha": a.base_sha, "changes_digest": a.changes_digest, "changes_count": a.changes_count,
        "plan_digest": a.plan_digest, "check_digests": dict(a.check_digests),
        "source_digest": a.source_digest, "git_head": a.git_head, "git_dirty": a.git_dirty,
        "session": a.session, "owner_pid": a.owner_pid, "executor": a.executor,
        "started_at": a.started_at, "state": a.state.value, "runs": [_run_doc(r) for r in a.runs],
        "ended_at": a.ended_at, "done": list(a.done), "remaining": list(a.remaining),
        "reason": a.reason, "next_action": a.next_action,
        "resolved": None if not a.resolved else
        {"at": a.resolved_at, "session": a.resolved_by, "note": a.resolved_note},
    }


def _str(d: dict, key: str, optional: bool = True) -> str | None:
    value = d.get(key)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise HandoffFormatError(f"{key}: 문자열이 아님")
    return value


def _strs(d: dict, key: str) -> tuple[str, ...]:
    value = d.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise HandoffFormatError(f"{key}: 문자열 목록이 아님")
    return tuple(value)


def _int(d: dict, key: str) -> int | None:
    value = d.get(key)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise HandoffFormatError(f"{key}: 정수가 아님")
    return value


def _parse_run(d: Any) -> CheckRun:
    if not isinstance(d, dict):
        raise HandoffFormatError("runs: 객체가 아님")
    try:
        end = RunEnd(d["end"]) if d.get("end") is not None else None
    except ValueError:
        raise HandoffFormatError("runs.end를 알 수 없음") from None
    observed = d.get("observed")
    if observed is not None:
        if not isinstance(observed, dict):
            raise HandoffFormatError("runs.observed: 객체가 아님")
        if observed.get("state") not in {p.value for p in ProbeState}:
            raise HandoffFormatError("runs.observed.state를 알 수 없음")
    return CheckRun(_str(d, "check", False), _str(d, "started_at", False), _str(d, "run_id"), _str(d, "ended_at"),
                    end, _str(d, "outcome"), _int(d, "exit_code"), _str(d, "message"), _str(d, "result_ref"),
                    _str(observed, "at", False) if observed else None, observed["state"] if observed else None,
                    _str(observed, "text") if observed else None)


def parse_attempt(d: Any) -> Attempt:
    if not isinstance(d, dict):
        raise HandoffFormatError("attempts: 객체가 아님")
    try:
        state = AttemptState(d.get("state"))
    except ValueError:
        raise HandoffFormatError("state를 알 수 없음") from None
    digests = d.get("check_digests", {})
    if not isinstance(digests, dict) or not all(isinstance(v, str) for v in digests.values()):
        raise HandoffFormatError("check_digests: 객체가 아님")
    dirty = d.get("git_dirty")
    if dirty is not None and not isinstance(dirty, bool):
        raise HandoffFormatError("git_dirty: true/false가 아님")
    resolved = d.get("resolved")
    if resolved is not None and not isinstance(resolved, dict):
        raise HandoffFormatError("resolved: 객체가 아님")
    runs = d.get("runs", [])
    if not isinstance(runs, list):
        raise HandoffFormatError("runs: 목록이 아님")
    return Attempt(
        _str(d, "attempt", False), _str(d, "task", False), _strs(d, "requirements"), _strs(d, "checks"),
        _strs(d, "paths"), _strs(d, "protected_paths"), _str(d, "base"), _str(d, "base_sha"),
        _str(d, "changes_digest"), _int(d, "changes_count"), _str(d, "plan_digest"), dict(digests),
        _str(d, "source_digest"), _str(d, "git_head"), dirty, _str(d, "session", False), _int(d, "owner_pid"),
        _str(d, "executor", False), _str(d, "started_at", False), state, tuple(_parse_run(r) for r in runs),
        _str(d, "ended_at"), _strs(d, "done"), _strs(d, "remaining"), _str(d, "reason"), _str(d, "next_action"),
        _str(resolved, "at") if resolved else None, _str(resolved, "session") if resolved else None,
        _str(resolved, "note") if resolved else None,
    )


def parse_handoff(data: Any) -> tuple[Attempt, ...]:
    if not isinstance(data, dict) or data.get("schema") != HANDOFF_SCHEMA:
        raise HandoffFormatError(f"schema가 '{HANDOFF_SCHEMA}'가 아님")
    attempts = data.get("attempts")
    if not isinstance(attempts, list):
        raise HandoffFormatError("attempts: 목록이 아님")
    return tuple(parse_attempt(a) for a in attempts)


def parse_handoff_text(text: str | None, name: str) -> tuple[tuple[Attempt, ...], str | None]:
    """(attempts, error). No file means no attempts yet."""

    if text is None:
        return (), None
    try:
        return parse_handoff(json.loads(text)), None
    except json.JSONDecodeError:
        return (), f"{name}: JSON 형식 오류"
    except HandoffFormatError as exc:
        return (), f"{name}: {exc}"


def read_attempts(results_path: Path) -> tuple[tuple[Attempt, ...], str | None]:
    path = handoff_path(results_path)
    if not path.exists():
        return (), None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return (), f"{path.name}: 읽을 수 없음"
    return parse_handoff_text(text, path.name)


def last_attempts(attempts: tuple[Attempt, ...]) -> dict[str, Attempt]:
    out: dict[str, Attempt] = {}
    for a in attempts:
        out[a.task] = a
    return out


# --- executor status ---------------------------------------------------------------


class ProbeState(str, Enum):
    RUNNING = "running"
    EXITED = "exited"
    UNKNOWN = "unknown"


class ExecutorProbe(Protocol):
    """Asks the executor whether a recorded run still runs. Read-only."""

    def status(self, attempt: Attempt, run: CheckRun | None) -> ProbeState: ...


class RecordedProbe:
    """The default probe. Queries nothing: answers from the last executor observation in the record.

    Local test processes are never queried, so their end stays unknown until
    the person confirms it. A Worker seen settled and exited stays exited. Anything else (running when
    last seen, never seen, a local test process) is not proof that it ended.
    """

    def status(self, attempt: Attempt, run: CheckRun | None) -> ProbeState:
        if run is None or run.observed_state is None:
            return ProbeState.UNKNOWN
        return ProbeState(run.observed_state)


# --- claiming a task --------------------------------------------------------------


class ClaimRefused(VerificationSetupError):
    """Another session holds the task, or an earlier attempt's end is not confirmed. Nothing ran."""

    BUSY = "busy"
    UNRESOLVED = "unresolved"

    def __init__(self, message: str, kind: str, task_id: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.task_id = task_id


def _unresolved(a: Attempt | None) -> bool:
    return a is not None and not a.resolved and a.state in (AttemptState.RUNNING, AttemptState.UNKNOWN)


def _update_file(results_path: Path, change: Callable[[list[dict]], None], lock_timeout: float) -> None:
    """Read-modify-replace the handoff file under its own lock. Never overwrites an unreadable file."""

    path = handoff_path(results_path)
    with exclusive(lock_path(path), lock_timeout):
        docs: list[dict] = []
        if path.exists():
            data = _read_json(path, "실행 기록")
            try:
                parse_handoff(data)
            except HandoffFormatError as exc:
                raise VerificationSetupError(f"실행 기록 형식 오류 ({path.name}): {exc}") from None
            docs = data["attempts"]
        change(docs)
        while len(docs) > MAX_ATTEMPTS:
            oldest = next((i for i, d in enumerate(docs) if d.get("state") == "ended" or d.get("resolved")), None)
            if oldest is None:
                break
            docs.pop(oldest)
        write_json(path, {"schema": HANDOFF_SCHEMA, "attempts": docs})


def _remaining(plan: TaskPlan, task_id: str, results_path: Path, source: SourceState | None) -> tuple[str, ...]:
    task = next(t for t in plan.tasks if t.task_id == task_id)
    try:
        progress = evaluate(plan, load_results(results_path), source)
    except Exception:  # unreadable or invalid results: everything still needs checking
        return tuple(c.check_id for c in task.checks)
    tp = progress.task(task_id)
    return tuple(cp.check.check_id for cp in tp.checks if cp.state is not CheckState.PASSED)


def run_text(r: CheckRun) -> str:
    if r.end is None:
        return f"{r.check} {r.message} · 종료 미확인" if r.message else f"{r.check} 종료 기록 없음"
    if r.outcome:
        word = "통과" if r.outcome == Outcome.PASSED.value else "실패"
        code = f" (종료 코드 {r.exit_code})" if r.exit_code is not None else ""
        return f"{r.check} {word}{code}" + (f" · {r.message}" if r.message else "")
    return f"{r.check} {r.message or r.end.value}"


CHECK_HINT = "확인할 것: 실행 ID의 테스트 프로세스와 그 하위 프로세스가 모두 끝났는지 (작업 관리자 등)"
WORKER_RUN_PREFIX = "dispatch:"  # run IDs of Orca Worker Dispatches
WORKER_CHECK_HINT = ("확인할 것: Orca에서 Worker가 정산(succeeded·failed·stopped·abandoned)되고 liveness가 "
                     "exited인지 · 응답 없음·시간 초과·unverifiable은 종료 증거가 아님 · "
                     "조회로 확인되지 않으면 Orca에서 직접 확인한 뒤 handoff --confirm-stopped")


def is_worker_run(run_id: str | None) -> bool:
    return bool(run_id) and run_id.startswith(WORKER_RUN_PREFIX)


def check_hint(run_ids) -> str:
    """What the person checks before confirming a stop, for the executors involved."""

    ids = [r for r in run_ids if r]
    if any(is_worker_run(r) for r in ids):
        return WORKER_CHECK_HINT if all(is_worker_run(r) for r in ids) else f"{WORKER_CHECK_HINT}\n{CHECK_HINT}"
    return CHECK_HINT


def confirm_action(task_id: str, run_id: str | None) -> str:
    if is_worker_run(run_id):
        return "handoff --observe로 상태 조회 · 종료 확인 전 다시 보내지 않음"
    return f"{run_id or '실행 ID 미확인'} 종료 확인 후 handoff --confirm-stopped {task_id}"


def stored_next_action(task_id: str, state: AttemptState, runs: tuple[CheckRun, ...],
                       remaining: tuple[str, ...]) -> str:
    last = runs[-1] if runs else None
    if state is AttemptState.UNKNOWN:
        return confirm_action(task_id, last.run_id if last else None)
    if last and last.outcome == Outcome.FAILED.value:
        return f"원인을 고친 뒤 verify --task {task_id} --check {last.check}로 직접 다시 실행"
    if last and (last.end is RunEnd.NOT_STARTED or (last.end is RunEnd.EXITED and last.outcome is None)):
        return "원인을 확인한 뒤 flow로 다시 실행"
    if remaining:
        return f"flow로 남은 확인 {', '.join(remaining)} 진행"
    return "flow로 다음 작업 진행 (모두 끝났으면 review로 확인 후 승인)"


class Claim:
    """One session's attempt at one task. Updates go to the handoff file; the run lock is held until close()."""

    def __init__(self, results_path: Path, plan: TaskPlan, task_id: str, attempt_id: str, held, source: SourceState,
                 lock_timeout: float, now: Callable[[], datetime]) -> None:
        self.results_path = results_path
        self.plan = plan
        self.task_id = task_id
        self.attempt_id = attempt_id
        self._held = held
        self._source = source
        self._lock_timeout = lock_timeout
        self._now = now
        self._runs: list[CheckRun] = []
        self.warnings: list[str] = []
        self.closed = False

    # each update is best effort: a missing end record is later read as "cut off", the safe side
    def _write(self) -> None:
        runs = [_run_doc(r) for r in self._runs]

        def change(docs: list[dict]) -> None:
            for d in docs:
                if d.get("attempt") == self.attempt_id:
                    d["runs"] = runs

        try:
            _update_file(self.results_path, change, self._lock_timeout)
        except (OSError, LockTimeout, VerificationSetupError) as exc:
            self.warnings.append(f"실행 기록을 갱신하지 못함 ({type(exc).__name__})")

    def run_started(self, check_id: str) -> None:
        self._runs.append(CheckRun(check_id, _now_iso(self._now)))
        self._write()

    def executor_started(self, check_id: str, run_id: str) -> None:
        self._runs[-1] = replace(self._runs[-1], run_id=run_id)
        self._write()

    def executor_observed(self, check_id: str, state: "ProbeState", text: str) -> None:
        """Records what the executor reported for the current run (Worker start receipt or status query)."""

        self._runs[-1] = replace(self._runs[-1], observed_at=_now_iso(self._now), observed_state=state.value,
                                 observed=text)
        self._write()

    def run_open(self, check_id: str, message: str) -> None:
        """The run continues outside this process (a Worker); its end stays unrecorded until observed."""

        self._runs[-1] = replace(self._runs[-1], message=message)
        self._write()

    def run_ended(self, check_id: str, end: RunEnd, *, outcome: str | None = None, exit_code: int | None = None,
                  message: str | None = None, result_ref: str | None = None) -> None:
        if not self._runs or self._runs[-1].check != check_id or self._runs[-1].end is not None:
            self._runs.append(CheckRun(check_id, _now_iso(self._now)))
        self._runs[-1] = replace(self._runs[-1], ended_at=_now_iso(self._now), end=end, outcome=outcome,
                                 exit_code=exit_code, message=message, result_ref=result_ref)
        self._write()

    def close(self, interrupted: str | None = None) -> None:
        """Ends the attempt and releases the task. ``interrupted`` names an exception that cut a run off."""

        if self.closed:
            return
        self.closed = True
        try:
            if interrupted and self._runs and self._runs[-1].end is None and not is_worker_run(self._runs[-1].run_id):
                self._runs[-1] = replace(self._runs[-1], ended_at=_now_iso(self._now), end=RunEnd.UNKNOWN,
                                         message=f"중단됨 ({interrupted}) · 테스트 프로세스 종료 미확인")
            state = AttemptState.UNKNOWN if any(r.end in (RunEnd.UNKNOWN, None) for r in self._runs) \
                else AttemptState.ENDED
            done = tuple(r.check for r in self._runs if r.outcome == Outcome.PASSED.value)
            remaining = _remaining(self.plan, self.task_id, self.results_path, self._source)
            last = self._runs[-1] if self._runs else None
            reason = run_text(last) if last else "실행한 확인 없음"
            next_action = stored_next_action(self.task_id, state, tuple(self._runs), remaining)
            runs = [_run_doc(r) for r in self._runs]
            ended = _now_iso(self._now)

            def change(docs: list[dict]) -> None:
                for d in docs:
                    if d.get("attempt") == self.attempt_id:
                        d.update(state=state.value, runs=runs, ended_at=ended, done=list(done),
                                 remaining=list(remaining), reason=reason, next_action=next_action)

            try:
                _update_file(self.results_path, change, self._lock_timeout)
            except (OSError, LockTimeout, VerificationSetupError) as exc:
                self.warnings.append(f"실행 종료를 기록하지 못함 ({type(exc).__name__})")
        finally:
            self._held.release()

    def __enter__(self) -> "Claim":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close(exc_type.__name__ if exc_type is not None else None)


def claim_task(
    plan_path: Path,
    results_path: Path,
    plan: TaskPlan,
    task_id: str,
    checks: tuple[str, ...],
    *,
    repo: Path,
    source: SourceState | None = None,
    basis: CodeBasis | None = None,
    executor: str = EXECUTOR_LOCAL,
    probe: ExecutorProbe | None = None,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Claim:
    """Takes ``task_id`` for this session and records the attempt, or raises ClaimRefused.

    An earlier attempt whose end is not confirmed refuses the claim, unless
    ``probe`` (an executor status query; default: the last stored observation) confirms it ended.
    """

    plan_path, results_path = Path(plan_path), Path(results_path)
    probe = probe or RecordedProbe()
    held = hold(run_lock_path(results_path, task_id))
    if held is None:
        raise ClaimRefused(f"다른 세션이 {task_id}을(를) 실행 중이어서 시작하지 않음", ClaimRefused.BUSY, task_id)
    try:
        own = tool_files(plan_path, results_path)
        source = source or source_state(repo, own)
        basis = basis or code_basis(repo, own)
        changes: ChangeSet = compare_changes(repo, plan.base, own)
        try:
            plan_fp = plan_digest(json.loads(plan_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            plan_fp = None
        task = next(t for t in plan.tasks if t.task_id == task_id)
        attempt_id = f"a-{now().astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
        attempt = Attempt(
            attempt_id, task_id, task.requirement_ids, tuple(checks), task.paths, task.protected_paths,
            plan.base, changes.base_sha, changes.digest, len(changes.changes) if changes.ok else None, plan_fp,
            {c.check_id: check_digest(task, c) for c in task.checks if c.check_id in checks},
            source.digest, basis.head, basis.dirty, SESSION_ID, os.getpid(), executor, _now_iso(now),
            AttemptState.RUNNING,
        )
        refused: list[ClaimRefused] = []

        def change(docs: list[dict]) -> None:
            previous = last_attempts(tuple(parse_attempt(d) for d in docs)).get(task_id)
            if _unresolved(previous) and probe.status(previous, previous.last_run) is ProbeState.EXITED:
                for d in docs:
                    if d.get("attempt") == previous.attempt_id:
                        d["resolved"] = {"at": _now_iso(now), "session": SESSION_ID, "note": "실행기 상태 조회로 종료 확인"}
            elif _unresolved(previous):  # we hold the run lock, so its session is gone
                refused.append(ClaimRefused(
                    f"{task_id}의 이전 실행({previous.attempt_id})이 끝났는지 확인되지 않아 시작하지 않음 · "
                    f"handoff로 확인한 뒤 --confirm-stopped {task_id}", ClaimRefused.UNRESOLVED, task_id))
                raise _Abort
            docs.append(attempt_doc(attempt))

        try:
            _update_file(results_path, change, lock_timeout)
        except _Abort:
            raise refused[0] from None
        except LockTimeout:
            raise VerificationSetupError("실행 기록 잠금을 얻지 못해 시작하지 않음") from None
        except OSError as exc:
            raise VerificationSetupError(f"실행 기록을 저장하지 못해 시작하지 않음 ({type(exc).__name__})") from None
        return Claim(results_path, plan, task_id, attempt_id, held, source, lock_timeout, now)
    except BaseException:
        held.release()
        raise


class _Abort(Exception):
    pass


def confirm_stopped(
    results_path: Path,
    task_id: str,
    note: str,
    *,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Attempt:
    """The person confirms the task's last attempt is no longer running. Refused while a session holds it."""

    results_path = Path(results_path)
    if not note or not note.strip():
        raise VerificationSetupError("무엇을 확인했는지 --note로 남겨야 합니다")
    held = hold(run_lock_path(results_path, task_id), wait=0)
    if held is None:
        raise ClaimRefused(f"{task_id}을(를) 실행 중인 세션이 있어 기록하지 않음", ClaimRefused.BUSY, task_id)
    try:
        found: list[Attempt] = []

        def change(docs: list[dict]) -> None:
            index = max((i for i, d in enumerate(docs) if d.get("task") == task_id), default=None)
            if index is None or not _unresolved(parse_attempt(docs[index])):
                raise _Abort
            docs[index]["resolved"] = {"at": _now_iso(now), "session": SESSION_ID, "note": note.strip()}
            found.append(parse_attempt(docs[index]))

        try:
            _update_file(results_path, change, lock_timeout)
        except _Abort:
            raise VerificationSetupError(f"{task_id}에 확인이 필요한 실행 기록이 없음") from None
        except LockTimeout:
            raise VerificationSetupError("실행 기록 잠금을 얻지 못해 기록하지 않음") from None
        return found[0]
    finally:
        held.release()


# --- comparing before a resume ------------------------------------------------------


class ResumeState(str, Enum):
    DONE = "done"  # verified for the current plan and code; not run again
    RESUMABLE = "resumable"  # 이어가기 가능
    RECHECK = "recheck"  # 재확인 필요: plan or code changed since the result
    FAILED = "failed"  # a current result failed: fix, then verify by hand
    WAITING = "waiting"  # prerequisites not complete
    BLOCKED = "blocked"  # ready, but the flow cannot run what remains (e.g. an unsupported check)
    RUNNING = "running"  # another live session (or the executor) runs it
    CHECK_RUN = "check_run"  # 실행 상태 확인 필요: cut off or end unknown


RESUME_TEXT = {
    ResumeState.DONE: "완료",
    ResumeState.RESUMABLE: "이어가기 가능",
    ResumeState.RECHECK: "재확인 필요",
    ResumeState.FAILED: "검증 실패",
    ResumeState.WAITING: "대기",
    ResumeState.BLOCKED: "진행 불가",
    ResumeState.RUNNING: "다른 세션 실행 중",
    ResumeState.CHECK_RUN: "실행 상태 확인 필요",
}


@dataclass(frozen=True)
class TaskResume:
    task_id: str
    state: ResumeState
    reason: str
    next_action: str
    attempt: Attempt | None = None
    since: tuple[str, ...] = ()  # what changed after the attempt started


@dataclass(frozen=True)
class ResumeView:
    tasks: tuple[TaskResume, ...]
    next_task: str | None
    attempts: tuple[Attempt, ...] = ()
    error: str | None = None  # the handoff file exists but cannot be read

    def task(self, task_id: str) -> TaskResume:
        return next(t for t in self.tasks if t.task_id == task_id)

    @property
    def last(self) -> Attempt | None:
        return max(self.attempts, key=lambda a: a.started_at) if self.attempts else None

    @property
    def blocking(self) -> TaskResume | None:
        """The first task that stops any new run: run state unknown first, then another live session."""

        for state in (ResumeState.CHECK_RUN, ResumeState.RUNNING):
            hit = next((t for t in self.tasks if t.state is state), None)
            if hit:
                return hit
        return None

    def verdict(self) -> str:
        if self.error:
            return f"실행 상태 확인 필요 · 실행 기록을 읽을 수 없음 ({self.error})"
        block = self.blocking
        if block:
            return f"{RESUME_TEXT[block.state]} · {block.task_id} · {block.reason}"
        if self.next_task:
            t = self.task(self.next_task)
            if t.state is ResumeState.RECHECK:
                return f"재확인 필요 · 다음 {t.task_id} · flow로 직접 재개하면 다시 검증"
            return f"이어가기 가능 · 다음 {t.task_id} · flow로 직접 재개"
        if self.tasks and all(t.state is ResumeState.DONE for t in self.tasks):
            return "완료 · 다시 실행할 작업 없음"
        open_ = next((t for t in self.tasks if t.state is not ResumeState.DONE), None)
        return f"진행할 수 없음 · {open_.task_id}: {open_.reason}" if open_ else "작업 없음"

    def next_action(self) -> str:
        """The one thing to do next for the whole plan."""

        if self.error:
            return "실행 기록 파일을 확인 (읽을 수 없는 기록은 덮어쓰지 않음)"
        block = self.blocking
        if block:
            return block.next_action
        if self.next_task:
            again = self.task(self.next_task).state is ResumeState.RECHECK
            return f"flow로 {self.next_task} {'다시 검증' if again else '진행'}"
        if self.tasks and all(t.state is ResumeState.DONE for t in self.tasks):
            return "review로 변경과 증거를 확인한 뒤 승인"
        open_ = next((t for t in self.tasks if t.state is not ResumeState.DONE), None)
        return f"{open_.task_id}: {open_.next_action}" if open_ else "없음"


def _since(a: Attempt, plan: TaskPlan, digest: str | None, source: SourceState | None) -> tuple[str, ...]:
    out = []
    task = next((t for t in plan.tasks if t.task_id == a.task), None)
    if task is None:
        return ("작업이 작업 정의에서 빠짐",)
    current = {c.check_id: check_digest(task, c) for c in task.checks}
    if any(current.get(c) != d for c, d in a.check_digests.items()):
        out.append("검증 조건 변경")
    elif digest is not None and a.plan_digest is not None and digest != a.plan_digest:
        out.append("작업 정의 변경")
    if source is not None and source.digest is not None and a.source_digest != source.digest:
        out.append("코드 변경")
    return tuple(out)


def _run_ids(a: Attempt) -> str:
    ids = [r.run_id for r in a.runs if r.run_id]
    return ", ".join(ids) if ids else "미확인"


def reconcile(
    plan: TaskPlan,
    progress: PlanProgress,
    attempts: tuple[Attempt, ...],
    *,
    next_task: str | None,
    live: Callable[[str], bool],
    probe: ExecutorProbe | None = None,
    digest: str | None = None,
    source: SourceState | None = None,
    error: str | None = None,
    blocked: dict[str, tuple[str, str]] | None = None,
) -> ResumeView:
    """Compares the recorded attempts with the current plan, code and results. Pure apart from ``live``/``probe``.

    ``blocked`` maps tasks the flow cannot select to (reason, hint), from the flow's blockers.
    """

    probe = probe or RecordedProbe()
    blocked = blocked or {}
    lasts = last_attempts(attempts)
    out = []
    for tp in progress.tasks:
        tid = tp.task.task_id
        a = lasts.get(tid)
        since = _since(a, plan, digest, source) if a else ()
        if _unresolved(a):
            if a.state is AttemptState.RUNNING and live(tid):
                out.append(TaskResume(tid, ResumeState.RUNNING, f"세션 {a.session} 실행 중 (시작 {a.started_at})",
                                      f"세션 {a.session}이 끝나기를 기다린 뒤 다시 확인", a, since))
                continue
            seen = probe.status(a, a.last_run)
            if seen is ProbeState.RUNNING:
                wait = ("실행이 끝나기를 기다린 뒤 handoff --observe로 다시 조회 (다시 보내지 않음)"
                        if a.last_run and is_worker_run(a.last_run.run_id) else "실행이 끝나기를 기다린 뒤 다시 확인")
                out.append(TaskResume(tid, ResumeState.RUNNING, f"실행기에서 실행 중 (실행 ID {_run_ids(a)})",
                                      wait, a, since))
                continue
            if seen is ProbeState.UNKNOWN:
                why = ("프로그램 중단 (종료 기록 없음)" if a.state is AttemptState.RUNNING
                       else (a.reason or "종료 미확인"))
                last_id = next((r.run_id for r in reversed(a.runs) if r.run_id), None)
                out.append(TaskResume(tid, ResumeState.CHECK_RUN, why, confirm_action(tid, last_id), a, since))
                continue
            # the executor confirmed the end: judge from the results like any finished attempt
        if tp.state in (TaskState.READY, TaskState.RECHECK) and tid in blocked and tid != next_task:
            reason, hint = blocked[tid]
            out.append(TaskResume(tid, ResumeState.BLOCKED, reason, hint or "검증 조건을 확인", a, since))
            continue
        if tp.state is TaskState.VERIFIED:
            out.append(TaskResume(tid, ResumeState.DONE, "현재 작업 정의·코드에 유효한 결과",
                                  "다시 실행하지 않음", a, since))
        elif tp.state is TaskState.RECHECK:
            stale = [cp for cp in tp.checks if cp.state is CheckState.STALE]
            passed_before = all(cp.result and cp.result.outcome is Outcome.PASSED for cp in stale)
            why = ", ".join(sorted({f"{cp.check.check_id}" for cp in stale})) + " 결과가 현재 상태와 다름"
            if since:
                why += f" (기록 후 {', '.join(since)})"
            action = "flow로 다시 검증" if passed_before else "verify로 직접 다시 실행"
            out.append(TaskResume(tid, ResumeState.RECHECK, why, action, a, since))
        elif tp.state is TaskState.FAILED:
            action = a.next_action if a and a.next_action else \
                f"원인을 고친 뒤 verify --task {tid} --check {tp.failed_checks[0]}로 직접 다시 실행"
            out.append(TaskResume(tid, ResumeState.FAILED, f"{', '.join(tp.failed_checks)} 검증 실패", action, a, since))
        elif tp.state is TaskState.WAITING:
            deps = ", ".join(w.task_id for w in tp.waiting_on)
            out.append(TaskResume(tid, ResumeState.WAITING, f"선행 작업 {deps} 미완료", f"{deps} 완료 후 진행", a, since))
        else:
            remaining = [cp.check.check_id for cp in tp.checks if cp.state is not CheckState.PASSED]
            why = f"남은 확인 {', '.join(remaining)}"
            if a is not None and a.resolved:
                why += " · 이전 실행 중지 확인됨"
            out.append(TaskResume(tid, ResumeState.RESUMABLE, why, "flow로 이어서 실행", a, since))
    return ResumeView(tuple(out), next_task, attempts, error)


def read_resume(
    results_path: Path,
    plan: TaskPlan,
    progress: PlanProgress,
    next_task: str | None,
    *,
    digest: str | None = None,
    source: SourceState | None = None,
    probe: ExecutorProbe | None = None,
    blocked: dict[str, tuple[str, str]] | None = None,
) -> ResumeView:
    """Reads the handoff file and checks which task locks are held right now. Writes nothing."""

    results_path = Path(results_path)
    attempts, error = read_attempts(results_path)
    return reconcile(plan, progress, attempts, next_task=next_task,
                     live=lambda tid: is_held(run_lock_path(results_path, tid)),
                     probe=probe, digest=digest, source=source, error=error, blocked=blocked)


def blocked_map(blockers) -> dict[str, tuple[str, str]]:
    """Flow blockers (task_id, reason, hint) as reconcile's ``blocked``."""

    return {b.task_id: (b.reason, b.hint) for b in blockers}


# --- text ----------------------------------------------------------------------


ATTEMPT_TEXT = {AttemptState.RUNNING: "실행 중", AttemptState.ENDED: "종료", AttemptState.UNKNOWN: "종료 미확인"}


def attempt_line(a: Attempt, live: bool | None, local_time: Callable[[str], str] = lambda s: s) -> str:
    """Task, how the attempt stands, when it started and which session ran it."""

    if a.state is AttemptState.RUNNING:
        state = "실행 중" if live else "중단 (종료 기록 없음)"
    else:
        state = ATTEMPT_TEXT[a.state]
    if a.resolved:
        state += " · 중지 확인됨"
    return " · ".join([a.task, state, f"시작 {local_time(a.started_at)}", f"세션 {a.session}"])


def run_line(a: Attempt, live: bool | None) -> str:
    """Executor run IDs and how the last run ended (or why it is unknown)."""

    last = a.last_run
    if last is None:
        return "실행 ID 없음 · 실행한 확인 없음"
    if last.end is None:
        how = "실행 중" if live else "종료 미확인"
    else:
        how = run_text(last)
        if last.end is RunEnd.UNKNOWN:
            how = f"종료 미확인 · {how}"
    return f"실행 ID {_run_ids(a)} · {how}"


OBSERVED_TEXT = {ProbeState.RUNNING.value: "실행 중", ProbeState.EXITED.value: "종료 확인",
                 ProbeState.UNKNOWN.value: "종료 미확인"}


def observation_line(a: Attempt, local_time: Callable[[str], str] = lambda s: s) -> str | None:
    """The executor's last reported status for the attempt's last run, if one was ever observed."""

    last = a.last_run
    if last is None or last.observed_at is None:
        return None
    return " · ".join(x for x in (f"관측 {local_time(last.observed_at)}",
                                  OBSERVED_TEXT.get(last.observed_state, last.observed_state), last.observed) if x)


def progress_line(a: Attempt) -> str:
    if a.state is AttemptState.RUNNING:
        done = [r.check for r in a.runs if r.outcome == Outcome.PASSED.value]
        remaining = [c for c in a.checks if c not in done]
    else:
        done, remaining = list(a.done), list(a.remaining)
    return f"확인 완료 {', '.join(done) or '없음'} · 남은 확인 {', '.join(remaining) or '없음'}"
