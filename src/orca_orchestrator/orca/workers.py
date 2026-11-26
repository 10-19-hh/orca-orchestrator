"""Worker commands for the installed Orca 1.4.264: what is known, how to build them, how to read replies.

Nothing here runs a command. :func:`worker_start_args` only *builds* the
``orchestration worker-start`` argv that a later, separately approved build
would send; this build has no code path that sends it (see ``dispatch.py``).
``worker-show`` is a read command and is the only Worker command the adapter
runs (``OrcaCliAdapter.worker_show``).

Evidence levels follow the Step 0 contract: ``DOCUMENTED`` (installed help or
official docs), ``SOURCE_VERIFIED`` (Orca v1.4.264 release source),
``VERIFIED`` (observed on this host), ``UNVERIFIED`` (not observed).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .errors import InvalidResponseError
from .runner import CommandResult

ORCA_RELEASE = "v1.4.264"

# Agents this project plans to use; Orca accepts more ids (cursor, opencode, ...).
WORKER_AGENTS = ("claude", "codex")

DISPATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

# worker-show: Dispatches in these states carry an outcome (fleet projection, v1.4.264).
SETTLED_STATES = frozenset({"succeeded", "failed", "stopped", "abandoned"})
# worker-show liveness verdicts (fleet projection, v1.4.264), optionally followed by " (detail)".
LIVENESS_VERDICTS = frozenset({"live", "exited", "unverifiable"})

# worker-start errors raised before Orca records a Dispatch (CLI checks, binding, Runtime reachability).
NOT_SENT_ERROR_CODES = frozenset({
    "invalid_argument",
    "no_active_sender_terminal",
    "terminal_handle_stale",
    "coordinator_binding_missing",
    "runtime_unavailable",
    "incompatible_runtime",
})


# --- what is known about the commands ------------------------------------------------


class Evidence(str, Enum):
    VERIFIED = "VERIFIED"
    SOURCE_VERIFIED = "SOURCE_VERIFIED"
    DOCUMENTED = "DOCUMENTED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class ContractItem:
    name: str
    documented: Evidence  # from installed help, official docs or release source
    observed: Evidence  # from running it on this host
    basis: str


WORKER_CONTRACT: tuple[ContractItem, ...] = (
    ContractItem(
        "Run 생성·조회와 Coordinator 바인딩",
        Evidence.DOCUMENTED, Evidence.VERIFIED,
        "run-create·run-current를 Orca 터미널 안의 Python에서 실행해 확인 (Experiment 1)",
    ),
    ContractItem(
        "worker-start 인자",
        Evidence.DOCUMENTED, Evidence.UNVERIFIED,
        "(--task | --spec | --spec-file) (--agent | --terminal) [--worktree] [--run] [--retry-of] "
        "[--timeout-ms] [--json]. 호출별 실행 표면·계정·Provider 인자와 --dry-run 없음 "
        "(설치 1.4.264 agent-context 명령 스키마)",
    ),
    ContractItem(
        "worker-start 응답",
        Evidence.SOURCE_VERIFIED, Evidence.UNVERIFIED,
        "--json 간략 응답 {ok, result: {dispatchId, taskId, runId, state, failedStage, lastError, next}}. "
        "state ready 또는 턴 시작 미관측이면 종료 코드 0, failed·outcome_unknown은 1",
    ),
    ContractItem(
        "worker-show 조회",
        Evidence.DOCUMENTED, Evidence.UNVERIFIED,
        "worker-show --dispatch <id> --json (설치 스키마). 간략 응답 {dispatchId, taskId, runId, state, "
        "stage, liveness, terminalState, lastError, next} (릴리스 소스)",
    ),
    ContractItem(
        "Worker 상태 값",
        Evidence.SOURCE_VERIFIED, Evidence.UNVERIFIED,
        "정산 상태 succeeded·failed·stopped·abandoned, liveness live·exited·unverifiable. "
        "정산 전 상태 값 전체 목록은 확인하지 못함",
    ),
    ContractItem(
        "실패·미확인 후 처리",
        Evidence.DOCUMENTED, Evidence.UNVERIFIED,
        "실패한 worker-start는 다시 실행하지 않고 failedStage를 읽음. 무응답·시간 초과·unverifiable은 "
        "종료 증거가 아님. 재시도는 같은 Task에 --retry-of (설치 orchestration 스킬 안내)",
    ),
    ContractItem(
        "Worker 실행 표면·인증",
        Evidence.DOCUMENTED, Evidence.UNVERIFIED,
        "Worker는 호스트의 새 에이전트 탭 설정(Chat/CLI)을 따르고 호출 셸 환경을 전달하지 않음. "
        "실제 표면·바이너리·인증은 기동 전에 조회할 공개 명령이 없음",
    ),
)


# --- building worker-start (never sent in this build) -------------------------------


@dataclass(frozen=True)
class WorkerStartRequest:
    spec: str  # the Task text the Worker receives
    agent: str
    task_title: str | None = None
    run_id: str | None = None  # None: the Run bound to the calling terminal (or a new one)
    worktree: str = "current"
    timeout_ms: int | None = None


def worker_start_args(request: WorkerStartRequest) -> list[str]:
    """The argv tail (after the executable) for one new Worker. Builds only; never runs.

    ``--from`` is never passed (the caller's own terminal binding is used) and
    ``--retry-request``/``--retry-of`` are never filled in: a retry is a
    separate decision by the person.
    """

    if not request.spec or not request.spec.strip():
        raise ValueError("spec must be non-empty")
    if request.agent not in WORKER_AGENTS:
        raise ValueError(f"agent must be one of {', '.join(WORKER_AGENTS)}")
    if request.worktree != "current":
        raise ValueError("only --worktree current is planned for the first Worker test")
    if request.timeout_ms is not None and request.timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive")
    args = ["orchestration", "worker-start", "--spec", request.spec]
    if request.task_title:
        args += ["--task-title", request.task_title]
    args += ["--worktree", request.worktree, "--agent", request.agent]
    if request.run_id:
        args += ["--run", request.run_id]
    if request.timeout_ms is not None:
        args += ["--timeout-ms", str(request.timeout_ms)]
    args.append("--json")
    return args


def worker_show_args(dispatch_id: str) -> list[str]:
    if not DISPATCH_ID_PATTERN.match(dispatch_id or ""):
        raise ValueError(f"malformed dispatch id: {dispatch_id!r}")
    return ["orchestration", "worker-show", "--dispatch", dispatch_id, "--json"]


# --- reading replies -----------------------------------------------------------------


class StartDisposition(str, Enum):
    STARTED = "started"  # Orca reported ready: a Worker is running the Task
    NOT_STARTED = "not_started"  # refused before a Dispatch was recorded
    UNKNOWN = "unknown"  # failed start, outcome_unknown, timeout or unreadable reply


@dataclass(frozen=True)
class WorkerStartReceipt:
    disposition: StartDisposition
    state: str | None  # Orca's state, verbatim (ready, failed, outcome_unknown, ...)
    dispatch_id: str | None
    task_id: str | None = None
    run_id: str | None = None
    failed_stage: str | None = None
    error: str | None = None  # lastError, or "<code>: <message>" for a refused call
    next: str | None = None

    def text(self) -> str:
        if self.disposition is StartDisposition.STARTED:
            return f"시작됨 ({self.state})"
        if self.disposition is StartDisposition.NOT_STARTED:
            return f"전송 거부 · Dispatch 없음 ({self.error or self.state})"
        if self.state == "failed":
            return f"시작 실패 ({self.failed_stage or 'start'}) · 남은 터미널 가능 · 다시 보내지 않음"
        if self.state == "outcome_unknown":
            return "시작 결과 미확인 (outcome_unknown) · 다시 보내지 않음"
        return f"응답 해석 불가 ({self.state or self.error or '내용 없음'}) · 생성 여부 미확인"


def _payload(result: CommandResult) -> dict[str, Any] | None:
    text = result.stdout.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _s(obj: dict[str, Any], key: str) -> str | None:
    value = obj.get(key)
    return value if isinstance(value, str) and value else None


def parse_worker_start(result: CommandResult) -> WorkerStartReceipt:
    """Reads a worker-start reply. Never raises: anything not clearly ready or refused is UNKNOWN."""

    payload = _payload(result)
    if payload is None or not isinstance(payload.get("ok"), bool):
        return WorkerStartReceipt(StartDisposition.UNKNOWN, None, None,
                                  error=f"JSON 응답 없음 (종료 코드 {result.exit_code})")
    error = payload.get("error")
    body = payload.get("result")
    if payload["ok"] is False and isinstance(error, dict) and not isinstance(body, dict):
        code = _s(error, "code")
        message = _s(error, "message")
        text = f"{code}: {message}" if code and message else (code or "오류 코드 없음")
        disposition = StartDisposition.NOT_STARTED if code in NOT_SENT_ERROR_CODES else StartDisposition.UNKNOWN
        return WorkerStartReceipt(disposition, None, None, error=text)
    if not isinstance(body, dict):
        return WorkerStartReceipt(StartDisposition.UNKNOWN, None, None, error="result 객체 없음")
    dispatch = _s(body, "dispatchId")
    if dispatch is not None and not DISPATCH_ID_PATTERN.match(dispatch):
        dispatch = None
    state = _s(body, "state")
    started = state == "ready" and payload["ok"] is True and result.exit_code == 0 and dispatch is not None
    return WorkerStartReceipt(
        StartDisposition.STARTED if started else StartDisposition.UNKNOWN,
        state, dispatch, _s(body, "taskId"), _s(body, "runId"), _s(body, "failedStage"),
        _s(body, "lastError"), _s(body, "next"),
    )


class WorkerEnd(str, Enum):
    RUNNING = "running"
    EXITED = "exited"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class WorkerObservation:
    dispatch_id: str
    state: str | None
    stage: str | None
    liveness: str | None  # verdict with optional "(detail)"
    terminal_state: str | None = None
    last_error: str | None = None
    next: str | None = None

    @property
    def verdict(self) -> str | None:
        if not self.liveness:
            return None
        word = self.liveness.split(" ", 1)[0]
        return word if word in LIVENESS_VERDICTS else None

    def end(self) -> WorkerEnd:
        """Exited only when Orca both settled the Dispatch and proved the process exited."""

        settled = self.state in SETTLED_STATES
        if settled and self.verdict == "exited":
            return WorkerEnd.EXITED
        if not settled and self.state and self.verdict == "live":
            return WorkerEnd.RUNNING
        return WorkerEnd.UNKNOWN

    def text(self) -> str:
        parts = [f"상태 {self.state or '없음'}", f"liveness {self.liveness or '없음'}"]
        if self.stage:
            parts.insert(1, f"단계 {self.stage}")
        if self.last_error:
            parts.append(f"오류 {self.last_error}")
        return " · ".join(parts)


def worker_observation(body: dict[str, Any], dispatch_id: str, argv: tuple[str, ...] = ()) -> WorkerObservation:
    """Builds an observation from worker-show's ``result`` object (the adapter checked ``ok`` and the exit code)."""

    shown = _s(body, "dispatchId")
    if shown is not None and shown != dispatch_id:
        raise InvalidResponseError(f"worker-show answered for {shown}, not {dispatch_id}", argv=argv, exit_code=0)
    return WorkerObservation(dispatch_id, _s(body, "state"), _s(body, "stage"), _s(body, "liveness"),
                             _s(body, "terminalState"), _s(body, "lastError"), _s(body, "next"))
