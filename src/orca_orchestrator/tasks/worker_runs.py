"""Orca Worker runs in the execution record (FR-05, FR-09). Verified with a fake executor only.

* :func:`record_worker_start` records one Worker start in a task's attempt: the
  Dispatch ID as run ID (``dispatch:<id>``), what the start receipt said and,
  when the start failed, timed out or was unreadable, an end of ``unknown`` so
  the task cannot be sent again until someone confirms the Worker stopped.
  It takes the sending function as an argument. This build has no function
  that sends ``worker-start``: the dispatch gate always refuses, so only tests
  call this with a fake executor.
* :class:`WorkerProbe` asks Orca (``worker-show``, read-only) how a recorded
  Dispatch stands. The work (settled state), the process (liveness) and the
  terminal (terminalState) are separate evidence: only all three ended
  (settled, exited, released) counts as ended; settled with the process or
  terminal still there is "finishing"; running blocks as running; anything
  else, including a failed or mismatched reply, is unknown.
* :func:`observe_runs` is the explicit ``handoff --observe``: it queries each
  unresolved Worker run not yet seen ended and stores the observation,
  keeping the last usable reply when a query fails. It never resolves,
  resends or stops anything.

A Worker's ``succeeded`` is Orca's report on its Dispatch, not a passed check:
the task stays unverified until its checks pass in the results file, and
approval stays the person's separate decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..orca.errors import OrcaAdapterError
from ..orca.runner import CommandLaunchError, CommandResult, CommandTimeout
from ..orca.workers import (
    StartDisposition,
    WorkerObservation,
    WorkerPhase,
    WorkerStartReceipt,
    WorkerStartRequest,
    parse_worker_start,
    worker_start_args,
)
from .handoff import (
    WORKER_RUN_PREFIX,
    Attempt,
    CheckRun,
    Claim,
    ProbeState,
    RunEnd,
    _now_iso,
    _unresolved,
    _update_file,
    is_worker_run,
    last_attempts,
    observed_doc,
    parse_attempt,
    read_attempts,
)
from .verification import DEFAULT_LOCK_TIMEOUT_SECONDS

EXECUTOR_WORKER = "orca-worker"
WORKER_CHECK = "worker"  # the run's name in the attempt: one Worker Dispatch per attempt

PHASE_TO_PROBE = {WorkerPhase.WORKING: ProbeState.RUNNING, WorkerPhase.FINISHING: ProbeState.FINISHING,
                  WorkerPhase.EXITED: ProbeState.EXITED, WorkerPhase.UNKNOWN: ProbeState.UNKNOWN}
UNCONFIRMABLE = "확인 불가: 작업·프로세스·터미널"


def worker_executor(agent: str) -> str:
    return f"{EXECUTOR_WORKER}:{agent}"


def dispatch_of(run_id: str | None) -> str | None:
    return run_id[len(WORKER_RUN_PREFIX):] if is_worker_run(run_id) else None


# --- sending (fake executor only in this build) -----------------------------------------


Send = Callable[[list[str]], CommandResult]  # argv tail after the Orca executable


def record_worker_start(claim: Claim, request: WorkerStartRequest, send: Send) -> WorkerStartReceipt:
    """Sends one worker-start through ``send`` and records what happened. Never retries."""

    args = worker_start_args(request)  # invalid requests fail here, before anything is recorded
    claim.run_started(WORKER_CHECK)
    try:
        result = send(args)
    except CommandTimeout:
        receipt = WorkerStartReceipt(StartDisposition.UNKNOWN, None, None, error="timeout")
        claim.run_ended(WORKER_CHECK, RunEnd.UNKNOWN,
                        message="worker-start 응답 시간 초과 · Worker 생성 여부 미확인 · 다시 보내지 않음")
        return receipt
    except CommandLaunchError as exc:
        receipt = WorkerStartReceipt(StartDisposition.NOT_STARTED, None, None, error=str(exc))
        claim.run_ended(WORKER_CHECK, RunEnd.NOT_STARTED, message=f"Orca CLI를 시작하지 못함 ({exc})")
        return receipt
    receipt = parse_worker_start(result, expected_run=request.run_id)
    if receipt.dispatch_id:
        claim.executor_started(WORKER_CHECK, f"{WORKER_RUN_PREFIX}{receipt.dispatch_id}",
                               request.run_id or receipt.run_id)
    if receipt.disposition is StartDisposition.STARTED:
        claim.executor_observed(WORKER_CHECK, ProbeState.RUNNING, receipt.text())
        claim.run_open(WORKER_CHECK, receipt.text())
    elif receipt.disposition is StartDisposition.NOT_STARTED:
        claim.run_ended(WORKER_CHECK, RunEnd.NOT_STARTED, message=receipt.text())
    else:
        if receipt.dispatch_id:
            claim.executor_observed(WORKER_CHECK, ProbeState.UNKNOWN, receipt.text())
        claim.run_ended(WORKER_CHECK, RunEnd.UNKNOWN, exit_code=result.exit_code, message=receipt.text())
    return receipt


# --- reading Worker status ----------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    state: ProbeState
    text: str
    failed: bool = False  # no usable reply: the query failed, or the reply was for something else


class WorkerProbe:
    """ExecutorProbe over ``worker-show``. ``show(dispatch, run)`` is ``OrcaCliAdapter.worker_show`` or a fake."""

    def __init__(self, show: Callable[[str, str | None], WorkerObservation]) -> None:
        self._show = show

    def observe(self, run: CheckRun | None) -> Reading:
        dispatch = dispatch_of(run.run_id if run else None)
        if dispatch is None:
            return Reading(ProbeState.UNKNOWN, "Worker 실행 ID 없음 · 조회하지 않음", True)
        try:
            seen = self._show(dispatch, run.orca_run)
        except OrcaAdapterError as exc:
            return Reading(ProbeState.UNKNOWN, f"worker-show 조회 실패 ({exc.category}) · {UNCONFIRMABLE}", True)
        except ValueError as exc:
            return Reading(ProbeState.UNKNOWN, f"worker-show 조회 불가 ({exc}) · {UNCONFIRMABLE}", True)
        return Reading(PHASE_TO_PROBE[seen.phase()], seen.text())

    def status(self, attempt: Attempt, run: CheckRun | None) -> ProbeState:
        if run is None or not is_worker_run(run.run_id):
            return ProbeState.UNKNOWN  # local test processes are not queried
        return self.observe(run).state


@dataclass(frozen=True)
class Observed:
    task: str
    run_id: str
    state: ProbeState
    text: str
    failed: bool = False
    last_seen: tuple[str, str, str | None] | None = None


def observe_runs(
    results_path: Path,
    probe: WorkerProbe,
    *,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> list[Observed]:
    """Queries every unresolved Worker run (last attempt per task) and stores what Orca reported.

    A run already seen ended is not queried again (its end is final). When a
    query fails, the stored state becomes unknown and the last usable reply
    is kept next to it.
    """

    attempts, error = read_attempts(Path(results_path))
    if error:
        return []
    todo = [a for a in last_attempts(attempts).values()
            if _unresolved(a) and a.last_run is not None and is_worker_run(a.last_run.run_id)
            and a.last_run.observed_state != ProbeState.EXITED.value]
    seen = [(a, probe.observe(a.last_run)) for a in todo]
    if not seen:
        return []
    at = _now_iso(now)
    out: list[Observed] = []

    def change(docs: list[dict]) -> None:
        out.clear()
        for a, reading in seen:
            for d in docs:
                if d.get("attempt") == a.attempt_id and d.get("runs"):
                    current = parse_attempt(d).last_run
                    if current is None or current.run_id != a.last_run.run_id:
                        continue
                    last = None
                    if reading.failed:
                        last = current.last_seen if current.observe_failed or current.observed_at is None else \
                            (current.observed_at, current.observed_state, current.observed)
                    d["runs"][-1]["observed"] = observed_doc(at, reading.state.value, reading.text, reading.failed, last)
                    out.append(Observed(a.task, a.last_run.run_id, reading.state, reading.text, reading.failed, last))

    _update_file(Path(results_path), change, lock_timeout)
    return list(out)
