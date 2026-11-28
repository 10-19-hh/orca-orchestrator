"""Thin connection between the interactive console and the existing logic. No UI framework here.

Every judgment shown or used here comes from the existing structured models:
completion and requirements from ``tasks.progress``, what the flow picks and
what blocks it from ``tasks.flow``, resume and run state from
``tasks.handoff``, what blocks approval from ``tasks.review``. This module
only decides which actions to offer from those models, and calls the existing
functions to carry them out:

* refresh: the injected collector (``tui.snapshot.collect_snapshot``, the
  same read-only collection as the text screen),
* one check: ``tasks.verification.verify`` while holding the task with
  ``tasks.handoff.claim_task`` (the same pattern as ``tasks.flow.run_flow``),
* the flow: ``tasks.flow.run_flow``,
* approval and hold: ``tasks.review.record_decision``; an approval carries the
  ``ReviewBasis`` the person looked at and is refused if anything changed,
* preflight: ``preflight.choose_task`` and ``preflight.run_preflight``
  (``status``/``run-current`` through the read-only runner; nothing is sent).

Nothing here starts a Worker, creates a Run or records a stop confirmation.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

from ..flow import STOP_TITLES
from ..orca.adapter import OrcaCliAdapter
from ..orca.dispatch import Provider
from ..orca.runner import CommandLaunchError, CommandResult, CommandTimeout, SubprocessRunner
from ..preflight import PREFLIGHT_TIMEOUT_SECONDS, choose_task, lines as preflight_lines, run_preflight
from ..tasks import BoardState, TaskState
from ..tasks.contract import Outcome
from ..tasks.flow import FlowReport, StopKind, check_text, run_flow, wait_reason_text
from ..tasks.handoff import RESUME_TEXT, ClaimRefused, ResumeState, claim_task
from ..tasks.progress import CheckState, TaskProgress
from ..tasks.review import (
    ApprovalState,
    Decision,
    DecisionRefused,
    ReviewBasis,
    approval_summary,
    record_decision,
)
from ..tasks.verification import (
    DEFAULT_TIMEOUT_SECONDS,
    SUPPORTED_CHECKS,
    VerificationNotCompleted,
    VerificationNotRecorded,
    VerificationNotStarted,
    VerificationRun,
    VerificationSetupError,
    VerificationTimedOut,
    prepare,
    verify,
)
from ..tui.readonly import ReadOnlyRunner
from ..tui.render import issue_text, local_time, next_text
from ..tui.snapshot import Snapshot

DEMO_NOTE = "데모 모드: 포함된 예시만 보여 주며 검증·흐름·승인을 실행하거나 기록하지 않음"
WORKER_NOTE = ("최종 변경 승인은 Worker 실행 승인과 다릅니다. 승인해도 Worker 작업 전송 차단은 그대로이며, "
               "이 빌드는 Worker에 작업을 보내지 않습니다.")
OUTPUT_TAIL_LINES = 15
POLL_SECONDS = 0.2

Progress = Callable[[str, Any], None]


# --- target ----------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """What the console works on, from the command line. Demo mode has no files."""

    plan: Path | None = None
    results: Path | None = None
    repo: Path | None = None
    demo: str | None = None
    demo_results: str | None = None
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    args: tuple[str, ...] = ()  # --tasks/--results/--repo exactly as given, for command guidance

    @property
    def repo_name(self) -> str:
        if self.demo:
            return "데모 (포함된 예시)"
        if self.repo is None:
            return "지정 안 함"
        return Path(self.repo).resolve().name or str(self.repo)

    @property
    def results_name(self) -> str:
        return self.results.name if self.results is not None else "지정 안 함"


@dataclass(frozen=True)
class Availability:
    enabled: bool
    reason: str


@dataclass(frozen=True)
class ActionOutcome:
    action: str  # verify, flow, approve, hold, preflight
    status: str  # ok, failed, refused, not_completed, not_recorded, busy, unresolved, stopped, blocked, error
    title: str
    lines: tuple[str, ...] = ()

    @property
    def good(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class VerifyPlan:
    task_id: str
    title: str
    check_id: str
    command: str  # program-defined command, as shown
    timeout: float
    repo_name: str
    results_name: str
    current: str  # the check's current result, as judged
    notes: tuple[str, ...]


@dataclass(frozen=True)
class FlowPlan:
    next_task: str
    reason: str
    checks: tuple[str, ...]
    timeout: float
    repo_name: str
    results_name: str
    later: tuple[str, ...]  # tasks the flow cannot select now, with why


class StopRequested(Exception):
    """The person asked to stop; raised after the running test process was killed."""


# --- running a check that can be stopped -------------------------------------------


class RunHandle:
    """The process a check is running, so the console can kill it when the person stops or quits."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None

    def attach(self, proc: subprocess.Popen | None) -> None:
        with self._lock:
            self._proc = proc

    def kill(self) -> bool:
        """Kills the running test process, if any. Its own children are not tracked."""

        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            proc.kill()
        except OSError:
            return False
        return True


class StoppableRunner(SubprocessRunner):
    """``TrackedSubprocessRunner`` that also stops when ``stop`` is set.

    Timeout behaviour is the same (kill, raise ``CommandTimeout``). On a stop
    request the process is killed and :class:`StopRequested` is raised, which
    ``verify`` records as an end that is not confirmed: the test process was
    killed, but whether processes it started ended is not known.
    """

    reports_start = True

    def __init__(self, env: Mapping[str, str] | None, stop: threading.Event, handle: RunHandle | None = None,
                 on_pid: Callable[[int], None] | None = None, poll: float = POLL_SECONDS) -> None:
        super().__init__(env)
        self._stop = stop
        self._handle = handle or RunHandle()
        self._on_pid = on_pid
        self._poll = poll

    def run(self, argv: Sequence[str], *, timeout: float, cwd: str | None = None,
            on_start: Callable[[int], None] | None = None) -> CommandResult:
        args = [str(a) for a in argv]
        if self._stop.is_set():  # nothing started: verify records it as not started
            raise CommandLaunchError("실행 전에 사용자가 중지함")
        try:
            proc = subprocess.Popen(args, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                    cwd=cwd, env=self._env)
        except OSError as exc:
            raise CommandLaunchError(f"{type(exc).__name__}: {exc.strerror or exc}") from None
        self._handle.attach(proc)
        deadline = time.monotonic() + timeout
        try:
            if on_start is not None:
                on_start(proc.pid)
            if self._on_pid is not None:
                self._on_pid(proc.pid)
            while True:
                remaining = deadline - time.monotonic()
                try:
                    stdout, stderr = proc.communicate(timeout=max(0.0, min(self._poll, remaining)))
                    break
                except subprocess.TimeoutExpired:
                    if self._stop.is_set():
                        self._kill(proc)
                        raise StopRequested("사용자가 중지함") from None
                    if time.monotonic() >= deadline:
                        self._kill(proc)
                        raise CommandTimeout(f"timed out after {timeout}s") from None
        except (StopRequested, CommandTimeout):
            raise
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        finally:
            self._handle.attach(None)
        return CommandResult(tuple(args), proc.returncode, stdout or "", stderr or "")

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        proc.kill()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass


# --- the console -------------------------------------------------------------------


def _python_name() -> str:
    return Path(sys.executable).name or "python"


def command_line(argv: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(argv)) if os.name == "nt" else shlex.join(argv)


class Console:
    """Offers and runs actions for one target. Thread-safe enough for one action at a time."""

    def __init__(
        self,
        target: Target,
        collect: Callable[[], Snapshot],
        *,
        preflight_adapter: Callable[[], OrcaCliAdapter] | None = None,
        verify_fn: Callable[..., VerificationRun] = verify,
        flow_fn: Callable[..., FlowReport] = run_flow,
        decide_fn: Callable[..., Any] = record_decision,
        runner_factory: Callable[..., Any] = StoppableRunner,
    ) -> None:
        self.target = target
        self._collect = collect
        self._preflight_adapter = preflight_adapter or self._default_preflight_adapter
        self._verify = verify_fn
        self._flow = flow_fn
        self._decide = decide_fn
        self._runner_factory = runner_factory
        self.handle = RunHandle()

    # -- reading ------------------------------------------------------------------

    def collect(self) -> Snapshot:
        return self._collect()

    def _default_preflight_adapter(self) -> OrcaCliAdapter:
        if self.target.demo:
            from ..tui.demo import demo_adapter

            return demo_adapter(self.target.demo)[0]
        return OrcaCliAdapter(ReadOnlyRunner(SubprocessRunner()), timeout=PREFLIGHT_TIMEOUT_SECONDS)

    # -- what can be done ------------------------------------------------------------

    def _common(self, snap: Snapshot | None, busy: str | None, stale: bool) -> str | None:
        t = self.target
        if t.demo:
            return DEMO_NOTE
        if t.plan is None:
            return "--tasks로 작업 정의 파일을 지정해야 실행할 수 있음"
        if t.results is None:
            return "--results로 검증 결과 파일을 지정해야 실행·기록할 수 있음"
        if snap is None:
            return "아직 조회하지 않음 · 조회가 끝난 뒤 다시 시도"
        board = snap.tasks
        if board.state is BoardState.INVALID:
            return f"작업 정의·검증 결과에 오류 {len(board.issues)}건 · 고친 뒤 r로 다시 조회"
        if board.state is not BoardState.OK:
            return f"작업 정의·검증 결과를 읽을 수 없음 · {board.error or board.state.value}"
        if busy:
            return f"{busy} 진행 중 · 끝난 뒤 다시 시도"
        if stale:
            return "마지막 조회가 실패해 화면이 최신이 아님 · r로 다시 조회"
        return None

    @staticmethod
    def supported_checks(tp: TaskProgress) -> tuple[str, ...]:
        return tuple(cp.check.check_id for cp in tp.checks if cp.check.check_id in SUPPORTED_CHECKS)

    @staticmethod
    def _task(snap: Snapshot, task_id: str | None) -> TaskProgress | None:
        progress = snap.tasks.progress
        if progress is None or task_id is None:
            return None
        return next((tp for tp in progress.tasks if tp.task.task_id == task_id), None)

    def verify_availability(self, snap: Snapshot | None, task_id: str | None, busy: str | None = None,
                            stale: bool = False) -> Availability:
        reason = self._common(snap, busy, stale)
        if reason:
            return Availability(False, reason)
        tp = self._task(snap, task_id)
        if tp is None:
            return Availability(False, "작업 목록에서 작업을 선택하세요")
        checks = self.supported_checks(tp)
        if not checks:
            return Availability(False, f"자동 실행을 지원하는 검증 조건이 없음 (지원: {', '.join(SUPPORTED_CHECKS)})")
        resume = snap.tasks.resume
        if resume is not None and resume.error:
            return Availability(False, f"실행 기록을 읽을 수 없어 실행하지 않음 · {resume.error}")
        if resume is not None:
            state = resume.task(tp.task.task_id)
            if state.state is ResumeState.CHECK_RUN:
                return Availability(False, f"이전 실행 종료 미확인 · {state.reason} · 복구 탭(3)에서 확인 방법 보기")
            if state.state in (ResumeState.RUNNING, ResumeState.FINISHING):
                return Availability(False, f"{RESUME_TEXT[state.state]} · {state.reason}")
        return Availability(True, f"{tp.task.task_id}/{self.chosen_check(tp)} 실행 가능")

    def chosen_check(self, tp: TaskProgress) -> str:
        """The supported check to run: the first without a current passed result, else the first."""

        checks = self.supported_checks(tp)
        by_id = {cp.check.check_id: cp for cp in tp.checks}
        return next((c for c in checks if by_id[c].state is not CheckState.PASSED), checks[0])

    def verify_plan(self, snap: Snapshot, task_id: str) -> VerifyPlan:
        tp = self._task(snap, task_id)
        check_id = self.chosen_check(tp)
        cp = next(c for c in tp.checks if c.check.check_id == check_id)
        notes = []
        if tp.state is TaskState.WAITING:
            waits = " · ".join(wait_reason_text(w) for w in tp.waiting_on)
            notes.append(f"선행 작업 미완료 ({waits}) · 결과는 기록되지만 작업은 선행 작업이 끝날 때까지 대기")
        if cp.state is CheckState.PASSED:
            notes.append("현재 코드에 유효한 통과 결과가 있음 · 다시 실행하면 같은 항목의 결과를 교체")
        elif cp.state is CheckState.FAILED:
            notes.append("현재 실패 결과가 있음 · 원인을 고쳤을 때만 다시 실행 (자동 재실행 없음)")
        notes += [
            "저장소 루트에서 셸 없이 한 번 실행 · 시간 초과·실행 실패·테스트 0개는 기록하지 않음",
            "작업 정의의 command 문자열은 실행하지 않음",
            "모델 인증 환경변수와 Orca 터미널 바인딩은 테스트 프로세스에 넘기지 않음",
            f"결과는 {self.target.results_name}의 {task_id}/{check_id} 항목만 바꾸고 다른 결과는 보존",
        ]
        return VerifyPlan(task_id, tp.task.title, check_id, SUPPORTED_CHECKS[check_id].display, self.target.timeout,
                          self.target.repo_name, self.target.results_name, check_text(cp), tuple(notes))

    def flow_availability(self, snap: Snapshot | None, busy: str | None = None, stale: bool = False) -> Availability:
        reason = self._common(snap, busy, stale)
        if reason:
            return Availability(False, reason)
        board = snap.tasks
        resume = board.resume
        if resume is not None and resume.error:
            return Availability(False, f"실행 기록을 읽을 수 없어 흐름을 시작하지 않음 · {resume.error}")
        block = resume.blocking if resume is not None else None
        if block is not None:
            return Availability(False, f"{RESUME_TEXT[block.state]} · {block.task_id} · {block.next_action}")
        if board.view is None or board.view.next is None:
            return Availability(False, f"다음 작업 {next_text(board)}")
        verb = "이어가기" if board.flow is not None else "시작"
        return Availability(True, f"흐름 {verb} 가능 · 다음 작업 {board.view.next.task_id}")

    def flow_plan(self, snap: Snapshot) -> FlowPlan:
        view = snap.tasks.view
        later = tuple(f"{b.task_id}: {b.reason}" for b in view.blockers)
        return FlowPlan(view.next.task_id, view.next.reason, view.next.check_ids, self.target.timeout,
                        self.target.repo_name, self.target.results_name, later)

    def approve_availability(self, snap: Snapshot | None, busy: str | None = None,
                             stale: bool = False) -> Availability:
        reason = self._common(snap, busy, stale)
        if reason:
            return Availability(False, reason)
        review = snap.tasks.review
        if review is None:
            return Availability(False, "저장소를 비교할 수 없어 검토가 없음 (--repo 확인)")
        if review.state is ApprovalState.APPROVED:
            return Availability(False, "현재 상태로 이미 승인됨 · 다시 기록할 필요 없음")
        if not review.approvable:
            first = review.blockers[0].text
            more = f" 외 {len(review.blockers) - 1}건" if len(review.blockers) > 1 else ""
            return Availability(False, f"보류 사유 {len(review.blockers)}건 · {first}{more}")
        extra = f" · 연결 없는 변경 {len(review.unlinked)}개 확인 필요" if review.unlinked else ""
        return Availability(True, f"승인 가능{extra}")

    def hold_availability(self, snap: Snapshot | None, busy: str | None = None, stale: bool = False) -> Availability:
        reason = self._common(snap, busy, stale)
        if reason:
            return Availability(False, reason)
        if snap.tasks.review is None:
            return Availability(False, "저장소를 비교할 수 없어 검토가 없음 (--repo 확인)")
        return Availability(True, "사유를 입력해 보류 기록 가능")

    # -- commands the person runs themselves ---------------------------------------------

    def cli(self, module: str, *extra: str) -> str:
        return command_line([_python_name(), "-m", f"orca_orchestrator.{module}", *self.target.args, *extra])

    def confirm_stopped_command(self, task_id: str) -> str:
        return self.cli("handoff", "--confirm-stopped", task_id, "--note", "확인한 내용")

    def observe_command(self) -> str:
        return self.cli("handoff", "--observe")

    # -- actions ------------------------------------------------------------------------

    def _runner(self, stop: threading.Event, progress: Progress):
        def factory(env):
            return self._runner_factory(env, stop, self.handle, lambda pid: progress("pid", pid))
        return factory

    def run_verify(self, task_id: str, check_id: str, stop: threading.Event, progress: Progress) -> ActionOutcome:
        t = self.target
        repo = Path(t.repo or ".").resolve()
        target = f"{task_id}/{check_id}"
        try:
            plan, _, _ = prepare(t.plan, t.results, task_id, check_id, repo)  # refuses before anything is recorded
            claim = claim_task(t.plan, t.results, plan, task_id, (check_id,), repo=repo)
        except ClaimRefused as exc:
            status = "busy" if exc.kind == ClaimRefused.BUSY else "unresolved"
            return ActionOutcome("verify", status, f"검증하지 않음 · {exc.message}",
                                 ("아무것도 실행하거나 바꾸지 않았습니다.", "복구 탭(3)에서 실행 기록과 확인 방법을 볼 수 있습니다."))
        except VerificationSetupError as exc:
            return _refused("verify", f"검증하지 않음 · {exc.message}", exc.issues)
        try:
            with claim:
                progress("start", (task_id, check_id))
                run = self._verify(t.plan, t.results, task_id, check_id, repo=repo, timeout=t.timeout,
                                   runner_factory=self._runner(stop, progress), claim=claim)
        except StopRequested:
            out = _stopped("verify", target, self.confirm_stopped_command(task_id))
        except VerificationSetupError as exc:
            out = _refused("verify", f"검증하지 않음 · {exc.message}", exc.issues)
        except VerificationNotRecorded as exc:
            out = _not_recorded("verify", target, exc, t.results_name)
        except VerificationTimedOut as exc:
            out = ActionOutcome("verify", "not_completed", f"검증 미완료 · {target} · {exc.message}", (
                "결과를 기록하지 않았습니다. 실행 기록에는 '실행 상태 확인 필요'로 남았습니다.",
                "테스트 프로세스와 그 하위 프로세스가 끝났는지 확인한 뒤 직접 기록:",
                f"  {self.confirm_stopped_command(task_id)}"))
        except VerificationNotStarted as exc:
            out = ActionOutcome("verify", "not_completed", f"검증 미완료 · {target} · {exc.message}",
                                ("결과를 기록하지 않았습니다. 완료 상태는 바뀌지 않습니다.",))
        except VerificationNotCompleted as exc:
            out = ActionOutcome("verify", "not_completed", f"검증 미완료 · {target} · {exc.message}",
                                (f"결과를 기록하지 않았습니다. 완료 상태는 바뀌지 않습니다. ({t.results_name})",))
        else:
            progress("result", run)
            out = _run_outcome(run, target, t.results_name)
        return _with_warnings(out, claim.warnings)

    def run_flow(self, stop: threading.Event, progress: Progress) -> ActionOutcome:
        t = self.target
        claims: list[Any] = []

        def verify_fn(plan_path, results_path, task_id, check_id, **kw):
            if stop.is_set():  # between checks: nothing was started for this one
                raise VerificationSetupError("사용자가 흐름을 멈춤 · 이 검증은 시작하지 않음")
            claim = kw.get("claim")
            if claim is not None and all(c is not claim for c in claims):
                claims.append(claim)
            return self._verify(plan_path, results_path, task_id, check_id,
                                runner_factory=self._runner(stop, progress), **kw)

        try:
            report = self._flow(t.plan, t.results, repo=t.repo or ".", timeout=t.timeout, verify_fn=verify_fn,
                                notify=progress)
        except StopRequested:
            out = _stopped("flow", "흐름", self.cli("handoff"))
            return _with_warnings(out, [w for c in claims for w in c.warnings])
        warnings = [w for c in claims for w in c.warnings]
        return _with_warnings(_flow_outcome(report, t.results_name, stop.is_set()), warnings)

    def approve(self, basis: ReviewBasis, confirm_unlinked: bool, note: str | None) -> ActionOutcome:
        t = self.target
        try:
            review, record = self._decide(t.plan, t.results, Decision.APPROVED, repo=t.repo or ".",
                                          note=note.strip() if note and note.strip() else None,
                                          confirm_unlinked=confirm_unlinked, expected=basis)
        except DecisionRefused as exc:
            blockers = tuple(f"· {b.text}" for b in exc.review.blockers) if exc.review is not None else ()
            return ActionOutcome("approve", "refused", f"승인을 기록하지 않음 · {exc.message}",
                                 (*blockers, "파일을 바꾸지 않았습니다.", "변경 검토 탭(2)에서 바뀐 내용을 확인한 뒤 다시 승인하세요."))
        except VerificationSetupError as exc:
            return _refused("approve", f"승인을 기록하지 않음 · {exc.message}", exc.issues)
        return ActionOutcome("approve", "ok", f"최종 승인 기록 · {approval_summary(review, local_time)}", (
            f"승인 기록: {record.name} · 이후 작업 정의·코드·변경 파일이 바뀌면 재승인 필요로 표시됩니다.", WORKER_NOTE))

    def hold(self, note: str) -> ActionOutcome:
        t = self.target
        try:
            review, record = self._decide(t.plan, t.results, Decision.HELD, repo=t.repo or ".", note=note)
        except DecisionRefused as exc:
            return ActionOutcome("hold", "refused", f"보류를 기록하지 않음 · {exc.message}", ("파일을 바꾸지 않았습니다.",))
        except VerificationSetupError as exc:
            return _refused("hold", f"보류를 기록하지 않음 · {exc.message}", exc.issues)
        return ActionOutcome("hold", "ok", f"보류 기록 · {approval_summary(review, local_time)}", (
            f"보류 기록: {record.name} · 승인하려면 보류 사유를 해결한 뒤 다시 승인합니다.",))

    def preflight(self, provider: Provider) -> ActionOutcome:
        """Read-only check up to sending work. Runs at most status and run-current; never sends."""

        t = self.target
        with_plan = bool(t.plan and t.results and not t.demo)
        args = SimpleNamespace(tasks=str(t.plan) if with_plan else None, results=str(t.results) if with_plan else None,
                               repo=str(t.repo) if with_plan and t.repo else None, task=None)
        try:
            task = choose_task(args)
        except VerificationSetupError as exc:
            return _refused("preflight", f"점검하지 않음 · {exc.message}", exc.issues)
        pf = run_preflight(self._preflight_adapter(), provider, task, offline=False)
        title = "판정: 전송 차단 · 아무것도 보내지 않았습니다." if pf.blocked else "판정: 전송 조건 충족 (이 빌드는 보내지 않음)"
        return ActionOutcome("preflight", "blocked" if pf.blocked else "ok", title, tuple(preflight_lines(pf)))


# --- outcome text ---------------------------------------------------------------------


def _refused(action: str, title: str, issues=()) -> ActionOutcome:
    lines = [f"  · {issue_text(i)}" for i in issues[:5]]
    if len(issues) > 5:
        lines.append(f"  · … 외 {len(issues) - 5}건")
    lines.append("파일을 바꾸지 않았습니다.")
    return ActionOutcome(action, "refused", title, tuple(lines))


def _stopped(action: str, target: str, command: str) -> ActionOutcome:
    return ActionOutcome(action, "stopped", f"중지함 · {target} · 테스트 프로세스를 중지했지만 하위 프로세스 종료는 확인하지 않음", (
        "결과를 기록하지 않았고, 실행 기록에는 '실행 상태 확인 필요'(종료 미확인)로 남았습니다.",
        "프로세스가 모두 끝났는지 확인한 뒤 직접 기록해야 다시 실행할 수 있습니다:",
        f"  {command}"))


def _not_recorded(action: str, target: str, exc: VerificationNotRecorded, results_name: str) -> ActionOutcome:
    outcome = "통과" if exc.result.outcome is Outcome.PASSED else "실패"
    lines = [f"  · {issue_text(i)}" for i in exc.issues[:5]]
    lines += [
        f"결과를 기록하지 않았습니다. 기존 결과 파일은 그대로입니다. ({results_name})",
        "복구: 잠금을 잡은 다른 저장, 디스크·권한, 결과 파일 형식을 확인한 뒤 같은 검증을 다시 실행하세요.",
    ]
    return ActionOutcome(action, "not_recorded",
                         f"기록 저장 실패 · {target} · 테스트는 끝났지만({outcome}, 종료 코드 {exc.result.exit_code}) {exc.message}",
                         tuple(lines))


def _run_outcome(run: VerificationRun, target: str, results_name: str) -> ActionOutcome:
    passed = run.result.outcome is Outcome.PASSED
    kept = f"다른 결과 {run.kept}건 보존" + (" · 이전 결과 교체" if run.replaced else "")
    lines = [f"기록: {results_name} ({kept})"]
    if not passed:
        tail = (run.output.stderr + run.output.stdout).rstrip().splitlines()[-OUTPUT_TAIL_LINES:]
        if tail:
            lines.append("출력 마지막 부분:")
            lines += [f"  {line}" for line in tail]
        lines.append("원인을 고친 뒤 직접 다시 검증하세요 (자동 재실행 없음).")
    word = "검증 통과" if passed else "검증 실패"
    return ActionOutcome("verify", "ok" if passed else "failed",
                         f"{word} · {target} · 종료 코드 {run.result.exit_code} · {run.summary}", tuple(lines))


FLOW_STATUS = {
    StopKind.DONE: "ok", StopKind.FAILED: "failed", StopKind.REFUSED: "refused",
    StopKind.NOT_COMPLETED: "not_completed", StopKind.NOT_RECORDED: "not_recorded", StopKind.WAITING: "ok",
    StopKind.LIMIT: "ok", StopKind.BUSY: "busy", StopKind.UNRESOLVED: "unresolved",
}


def _flow_outcome(report: FlowReport, results_name: str, stopped: bool) -> ActionOutcome:
    stop = report.stop
    target = f" · {stop.task_id}/{stop.check_id}" if stop.task_id and stop.check_id else (
        f" · {stop.task_id}" if stop.task_id else "")
    lines = []
    if report.verified:
        lines.append(f"이번 실행에서 완료한 작업: {', '.join(report.verified)}")
    if stop.kind is StopKind.FAILED:
        lines.append(f"실패 결과를 {results_name}에 기록했습니다. 원인을 고친 뒤 그 작업을 직접 검증하면 흐름을 이어갈 수 있습니다.")
    elif stop.kind in (StopKind.NOT_COMPLETED, StopKind.NOT_RECORDED):
        lines.append(f"이번 검증은 기록하지 않았습니다. {results_name}의 이전 결과는 그대로입니다.")
    elif stop.kind in (StopKind.BUSY, StopKind.UNRESOLVED):
        lines.append("아무것도 실행하지 않았습니다. 복구 탭(3)에서 실행 기록을 확인할 수 있습니다.")
    elif stop.kind is StopKind.DONE:
        lines.append("최종 승인은 흐름이 하지 않습니다. 변경 검토 탭(2)에서 확인한 뒤 직접 승인합니다.")
    status = "stopped" if stopped and stop.kind is StopKind.REFUSED else FLOW_STATUS[stop.kind]
    return ActionOutcome("flow", status, f"{STOP_TITLES[stop.kind]}{target} · {stop.message}", tuple(lines))


def _with_warnings(out: ActionOutcome, warnings: list[str]) -> ActionOutcome:
    if not warnings:
        return out
    extra = tuple(f"실행 기록 경고 · {w}" for w in dict.fromkeys(warnings)) + (
        "실행 기록이 갱신되지 않은 실행은 다음 조회에서 '실행 상태 확인 필요'로 보일 수 있습니다 (복구 탭 참고).",)
    return ActionOutcome(out.action, out.status, out.title, out.lines + extra)
