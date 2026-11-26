"""Worker commands for the installed Orca 1.4.264: what is known, how to build them, how to read replies.

Nothing here runs a command. :func:`worker_start_args` only *builds* the
``orchestration worker-start`` argv that a later, separately approved build
would send; this build has no code path that sends it (see ``dispatch.py``).
``worker-show`` is a read command and is the only Worker command the adapter
runs (``OrcaCliAdapter.worker_show``).

Evidence levels follow the Step 0 contract: ``DOCUMENTED`` (installed help or
official docs), ``SOURCE_VERIFIED`` (Orca v1.4.264 release source or the
installed 1.4.264 CLI bundle), ``VERIFIED`` (observed on this host),
``PARTIAL`` (partly observed), ``UNVERIFIED`` (not observed).

Dispatch IDs are opaque. Experiment 4-B handled a real one, but its format
was not recorded, so nothing here assumes a prefix or an alphabet: an ID is
any non-empty string that is safe as one argv element (:func:`is_dispatch_id`),
and a reply is trusted only for the Dispatch (and Run) it names.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .errors import InvalidResponseError
from .runner import CommandResult

ORCA_RELEASE = "v1.4.264"

# Agents this project plans to use; Orca accepts more ids (cursor, opencode, ...).
WORKER_AGENTS = ("claude", "codex")

# worker-show: Dispatches in these states carry an outcome (fleet projection, v1.4.264).
SETTLED_STATES = frozenset({"succeeded", "failed", "stopped", "abandoned"})
# worker-show liveness verdicts (fleet projection, v1.4.264), optionally followed by " (detail)".
LIVENESS_VERDICTS = frozenset({"live", "exited", "unverifiable"})
# worker-show terminalState once Orca gave the Worker's terminal back (4-B observed reclaimable -> released).
TERMINAL_RELEASED = "released"

# worker-start errors raised before Orca records a Dispatch (CLI checks, binding, Runtime reachability).
NOT_SENT_ERROR_CODES = frozenset({
    "invalid_argument",
    "no_active_sender_terminal",
    "terminal_handle_stale",
    "coordinator_binding_missing",
    "runtime_unavailable",
    "incompatible_runtime",
})


def is_dispatch_id(value: object) -> bool:
    """A Dispatch ID we can pass on and store. No format is assumed beyond argv safety.

    Refused: non-strings, empty values, surrounding whitespace, unprintable
    characters (they would break a record line) and a leading ``-`` (it would
    read as an option).
    """

    return (isinstance(value, str) and bool(value) and value == value.strip()
            and not value.startswith("-") and value.isprintable())


# --- what is known about the commands ------------------------------------------------


class Evidence(str, Enum):
    VERIFIED = "VERIFIED"
    SOURCE_VERIFIED = "SOURCE_VERIFIED"
    DOCUMENTED = "DOCUMENTED"
    PARTIAL = "PARTIAL"
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
        Evidence.DOCUMENTED, Evidence.VERIFIED,
        "(--task | --spec | --spec-file) (--agent | --terminal) [--worktree] [--run] [--retry-of] "
        "[--timeout-ms] [--json]. 호출별 실행 표면·계정·Provider 인자와 --dry-run 없음 "
        "(설치 1.4.264 agent-context 명령 스키마). 4-B에서 --spec·--task-title·--worktree current·"
        "--agent claude·--run·--timeout-ms·--json 조합을 1회 실행",
    ),
    ContractItem(
        "worker-start 응답",
        Evidence.SOURCE_VERIFIED, Evidence.VERIFIED,
        "--json 간략 응답 {ok, result: {dispatchId, taskId, runId, state, mode, failedStage, lastError, next}}. "
        "state ready 또는 턴 시작 미관측이면 종료 코드 0, failed·outcome_unknown은 1. "
        "4-B 1회: 종료 코드 0·ok·ready·mode terminal·Run·Task·Dispatch ID (ID 형식은 기록하지 않음)",
    ),
    ContractItem(
        "worker-show 조회",
        Evidence.SOURCE_VERIFIED, Evidence.PARTIAL,
        "worker-show --dispatch <id> --json은 --full 없이 간략 응답 {dispatchId, taskId, runId, state, "
        "stage, liveness, terminalState, lastError, next}을 냄 (설치 1.4.264 CLI 번들). 4-B 관측기는 "
        "같은 Dispatch의 전체 기록(dispatch·worker·projection·terminalResource …)을 읽었고 "
        "간략 응답 원문은 남지 않음",
    ),
    ContractItem(
        "Worker 상태 값",
        Evidence.SOURCE_VERIFIED, Evidence.VERIFIED,
        "정산 상태 succeeded·failed·stopped·abandoned, liveness live·exited·unverifiable (소스). "
        "4-B 관측: ready → succeeded·settled(liveness live·터미널 reclaimable) → "
        "succeeded·settled(liveness exited·터미널 released)",
    ),
    ContractItem(
        "실패·미확인 후 처리",
        Evidence.DOCUMENTED, Evidence.UNVERIFIED,
        "실패한 worker-start는 다시 실행하지 않고 failedStage를 읽음. 무응답·시간 초과·unverifiable은 "
        "종료 증거가 아님. 재시도는 같은 Task에 --retry-of (설치 orchestration 스킬 안내). "
        "4-B에서는 실패 경로가 나오지 않음",
    ),
    ContractItem(
        "Worker 실행 표면·인증",
        Evidence.DOCUMENTED, Evidence.PARTIAL,
        "Worker는 호스트의 새 에이전트 탭 설정(Chat/CLI)을 따르고 호출 셸 환경을 전달하지 않음. "
        "4-B에서 mode terminal은 관측, 실행 파일·인자·구독 인증은 확인하지 못함",
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
    """``--full`` is never added: the adapter reads the concise reply, which names the Dispatch at the top."""

    if not is_dispatch_id(dispatch_id):
        raise ValueError(f"unusable dispatch id: {dispatch_id!r}")
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
    error: str | None = None  # reply problems and lastError, or "<code>: <message>" for a refused call
    next: str | None = None
    mode: str | None = None  # launch surface Orca reported (terminal / chat)

    def text(self) -> str:
        if self.disposition is StartDisposition.STARTED:
            return f"시작됨 ({self.state}{f' · {self.mode}' if self.mode else ''})"
        if self.disposition is StartDisposition.NOT_STARTED:
            return f"전송 거부 · Dispatch 없음 ({self.error or self.state})"
        if self.state == "failed":
            return f"시작 실패 ({self.failed_stage or 'start'}) · 남은 터미널 가능 · 다시 보내지 않음"
        if self.state == "outcome_unknown":
            return "시작 결과 미확인 (outcome_unknown) · 다시 보내지 않음"
        detail = " · ".join(x for x in (self.state, self.error) if x) or "내용 없음"
        return f"응답 해석 불가 ({detail}) · 생성 여부 미확인 · 다시 보내지 않음"


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


def _mistyped(obj: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    """Keys present with a value that is neither null nor a string."""

    return [k for k in keys if obj.get(k) is not None and not isinstance(obj.get(k), str)]


START_FIELDS = ("dispatchId", "taskId", "runId", "state", "mode", "failedStage", "lastError", "next")


def parse_worker_start(result: CommandResult, expected_run: str | None = None) -> WorkerStartReceipt:
    """Reads a worker-start reply. Never raises: anything not clearly ready or refused is UNKNOWN.

    Ready counts as started only with a usable ``dispatchId``, string-typed
    fields and, when ``expected_run`` (the ``--run`` sent) is given, no other
    Run named in the reply. Text in the reply (an echoed marker, a message)
    is never read as proof of anything.
    """

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
    problems = [f"{k} 타입 오류" for k in _mistyped(body, START_FIELDS)]
    raw = body.get("dispatchId")
    dispatch = raw if is_dispatch_id(raw) else None
    if dispatch is None and "dispatchId 타입 오류" not in problems:
        problems.append("dispatchId 없음" if raw in (None, "") else "dispatchId 사용 불가")
    run = _s(body, "runId")
    if expected_run and run and run != expected_run:
        problems.append(f"Run 불일치 (보낸 Run {expected_run} · 응답 {run})")
    state = _s(body, "state")
    started = (state == "ready" and payload["ok"] is True and result.exit_code == 0 and not problems)
    last_error = _s(body, "lastError")
    return WorkerStartReceipt(
        StartDisposition.STARTED if started else StartDisposition.UNKNOWN,
        state, dispatch, _s(body, "taskId"), run, _s(body, "failedStage"),
        " · ".join(problems + ([last_error] if last_error else [])) or None,
        _s(body, "next"), _s(body, "mode"),
    )


class WorkerPhase(str, Enum):
    """Where a Worker stands, from three separate pieces of evidence: the work, the process, the terminal."""

    WORKING = "working"  # not settled, process live
    FINISHING = "finishing"  # settled, but the process is live or the terminal is not released yet
    EXITED = "exited"  # settled, process exited and terminal released
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
    run_id: str | None = None

    @property
    def verdict(self) -> str | None:
        if not self.liveness:
            return None
        word = self.liveness.split(" ", 1)[0]
        return word if word in LIVENESS_VERDICTS else None

    @property
    def settled(self) -> bool:
        return self.state in SETTLED_STATES

    def phase(self) -> WorkerPhase:
        """Ended only when Orca settled the Dispatch, proved the process exited and released the terminal.

        A Worker's own ``succeeded`` says nothing about the task's checks or
        the person's approval: those are judged from the results file as usual.
        """

        if self.settled:
            if self.verdict == "exited" and self.terminal_state == TERMINAL_RELEASED:
                return WorkerPhase.EXITED
            if self.verdict in ("live", "exited"):
                return WorkerPhase.FINISHING
            return WorkerPhase.UNKNOWN
        if self.state and self.verdict == "live":
            return WorkerPhase.WORKING
        return WorkerPhase.UNKNOWN

    def unknown_items(self) -> list[str]:
        """What this reply did not establish."""

        missing = []
        if not self.state:
            missing.append("작업 상태")
        if self.verdict in (None, "unverifiable"):
            missing.append("프로세스")
        if not self.terminal_state:
            missing.append("터미널")
        return missing

    def text(self) -> str:
        parts = [f"작업 {self.state or '상태 없음'}" + (f"({self.stage})" if self.stage else ""),
                 f"프로세스 {self.liveness or '없음'}", f"터미널 {self.terminal_state or '없음'}"]
        if self.last_error:
            parts.append(f"오류 {self.last_error}")
        missing = self.unknown_items()
        if missing:
            parts.append(f"미확인 {'·'.join(missing)}")
        return " · ".join(parts)


SHOW_FIELDS = ("dispatchId", "taskId", "runId", "state", "stage", "liveness", "terminalState", "lastError", "next")


def worker_observation(body: dict[str, Any], dispatch_id: str, argv: tuple[str, ...] = (),
                       expected_run: str | None = None) -> WorkerObservation:
    """Builds an observation from worker-show's ``result`` object (the adapter checked ``ok`` and the exit code).

    The reply must name the queried Dispatch (and not another Run than
    ``expected_run``) and carry strings where strings are expected; otherwise
    nothing in it is trusted. The full record (``--full``: dispatch, worker,
    projection, ...) names no top-level ``dispatchId`` and is refused; this
    adapter never asks for it.
    """

    def refuse(message: str) -> InvalidResponseError:
        return InvalidResponseError(f"worker-show {message}", argv=argv, exit_code=0)

    wrong = _mistyped(body, SHOW_FIELDS)
    if wrong:
        raise refuse(f"field types: {', '.join(wrong)}")
    shown = _s(body, "dispatchId")
    if shown is None:
        raise refuse("reply names no dispatchId")
    if shown != dispatch_id:
        raise refuse(f"answered for {shown}, not {dispatch_id}")
    run = _s(body, "runId")
    if expected_run and run and run != expected_run:
        raise refuse(f"answered for Run {run}, not {expected_run}")
    return WorkerObservation(dispatch_id, _s(body, "state"), _s(body, "stage"), _s(body, "liveness"),
                             _s(body, "terminalState"), _s(body, "lastError"), _s(body, "next"), run)
