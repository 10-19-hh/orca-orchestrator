"""``python -m orca_orchestrator.preflight`` — everything up to sending work to a Worker, without sending it.

Shows the planned Provider, the Coordinator binding, the Run and the task the
Worker would get, the exact ``worker-start`` command that would be sent, what
is known about the Worker commands, the Experiment 4-A evidence for the
Provider, the approvals still needed and every reason sending is blocked.

It runs at most ``status --json`` and, when bound and connected,
``orchestration run-current --json`` (through the read-only runner). It never
creates a Run, starts a Worker or sends a task. ``--offline`` runs nothing;
``--demo`` replays the bundled public fixture.

Exit codes: 0 would send (never in this build), 1 sending blocked, 2 files unusable.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .orca.adapter import OrcaCliAdapter, RunRef, RuntimeStatus
from .orca.dispatch import (
    DispatchDecision,
    DispatchGuard,
    DispatchRequest,
    Provider,
    Reason,
    observe_auth_environment,
)
from .orca.errors import CoordinatorBindingError, OrcaAdapterError
from .orca.evidence import EXPERIMENT_4A_STATUS, EXPERIMENT_4B_CLAUDE_STATUS, PROVIDER_EVIDENCE
from .orca.runner import SubprocessRunner
from .orca.workers import WORKER_CONTRACT, WorkerStartRequest, worker_start_args
from .tasks.handoff import RESUME_TEXT, ResumeState
from .tasks.verification import VerificationSetupError
from .tui.demo import demo_adapter
from .tui.readonly import ReadOnlyRunner
from .tui.render import REASON_LABELS, display_width, issue_text

EXIT_WOULD_SEND, EXIT_BLOCKED, EXIT_UNUSABLE = 0, 1, 2
PREFLIGHT_TIMEOUT_SECONDS = 10.0

PROVIDER_TEXT = {Provider.CLAUDE: "Claude", Provider.CODEX: "Codex"}

# The single task planned for Experiment 4-B/4-C: no file changes, one fixed line back.
MINIMAL_TITLE = "4-B minimal worker"
MINIMAL_SPEC = ("Do not read, create or modify any file and do not run any command. "
                "Reply with exactly this line and nothing else: ORCA-WORKER-OK")

CATEGORY = {
    Reason.EXECUTION_APPROVAL_MISSING: "승인",
    Reason.EXPERIMENT_4A_NOT_PASSED: "정책·과금 검증",
    Reason.SUBSCRIPTION_AUTH_UNVERIFIED: "인증",
    Reason.API_KEY_ENVIRONMENT_PRESENT: "인증",
    Reason.BILLING_PATH_UNVERIFIED: "과금",
    Reason.EXTRA_USAGE_NOT_CONFIRMED_OFF: "과금",
    Reason.AUTO_RECHARGE_NOT_CONFIRMED_OFF: "과금",
    Reason.API_KEY_BILLING_FORBIDDEN: "과금",
    Reason.REAL_DISPATCH_DISABLED: "Worker 실행 계약",
    Reason.WORKER_DISPATCH_CONTRACT_UNVERIFIED: "Worker 실행 계약",
    Reason.AUTOMATIC_RETRY_FORBIDDEN: "Worker 실행 계약",
    Reason.COORDINATOR_BINDING_MISSING: "Orca 환경",
    Reason.RUNTIME_UNAVAILABLE: "Orca 환경",
    Reason.RUNTIME_STATUS_INVALID: "Orca 환경",
    Reason.ORCA_VERSION_UNPINNED: "Orca 환경",
    Reason.USAGE_LIMIT_WAITING_FOR_RESET: "사용량",
    Reason.USAGE_RECHECK_REQUIRED: "사용량",
}
CATEGORY_ORDER = ("승인", "인증", "과금", "정책·과금 검증", "Worker 실행 계약", "Orca 환경", "사용량", "작업·복구")


@dataclass(frozen=True)
class TaskChoice:
    label: str  # what the screen calls the task
    spec: str
    title: str
    blocked: str | None = None  # why this task cannot be sent now (handoff, plan)


@dataclass
class Preflight:
    provider: Provider
    bound: bool
    runtime: RuntimeStatus | None
    runtime_error: OrcaAdapterError | None
    runtime_queried: bool
    run: RunRef | None
    run_note: str
    task: TaskChoice
    command: list[str]
    decision: DispatchDecision
    auth_names: tuple[str, ...]
    extra: list[tuple[str, str]] = field(default_factory=list)  # (category, text)

    @property
    def blocked(self) -> bool:
        return bool(self.decision.reasons or self.extra)


def _query(adapter: OrcaCliAdapter, offline: bool) -> tuple[RuntimeStatus | None, OrcaAdapterError | None,
                                                            RunRef | None, str]:
    if offline:
        return None, None, None, "조회 안 함 (--offline)"
    runtime: RuntimeStatus | None = None
    error: OrcaAdapterError | None = None
    try:
        runtime = adapter.status()
    except OrcaAdapterError as exc:
        error = exc
    if not adapter.binding.terminal_handle_present:
        return runtime, error, None, "조회 안 함 (바인딩 없음)"
    if runtime is None or not runtime.connected:
        return runtime, error, None, "조회 안 함 (Runtime 연결 안 됨)"
    try:
        run = adapter.run_current()
    except CoordinatorBindingError as exc:
        return runtime, error, None, f"조회 실패 · 바인딩 무효 ({exc.code})"
    except OrcaAdapterError as exc:
        return runtime, error, None, f"조회 실패 ({exc.category})"
    if run is None:
        return runtime, error, None, "바인딩된 Run 없음 · 보내면 worker-start --spec이 새 Run을 만들고 바인딩함 (점검은 만들지 않음)"
    return runtime, error, run, "현재 터미널에 바인딩됨"


def choose_task(args) -> TaskChoice:
    """The 4-B minimal task, or the task the flow would take next from a plan (read-only)."""

    if not args.tasks:
        return TaskChoice("4-B 최소 작업 · 파일 수정 없이 지정 문자열 한 줄만 반환", MINIMAL_SPEC, MINIMAL_TITLE)
    from .tasks.flow import inspect, resume_view  # local: plan files are optional here

    plan, results = Path(args.tasks), Path(args.results)
    view = inspect(plan, results, Path(args.repo or "."))
    resume = resume_view(plan, results, view)
    wanted = args.task or resume.next_task
    if wanted is None:
        blocking = resume.blocking
        why = (f"{blocking.task_id} {RESUME_TEXT[blocking.state]} · {blocking.next_action}" if blocking
               else resume.verdict())
        return TaskChoice("보낼 작업 없음", "", "", blocked=why)
    task = next((t for t in view.plan.tasks if t.task_id == wanted), None)
    if task is None:
        return TaskChoice(wanted, "", "", blocked=f"{wanted}: 작업 정의에 없음")
    spec = f"[{task.task_id}] {task.title} (요구사항 {', '.join(task.requirement_ids)})"
    state = resume.task(wanted)
    label = f"{task.task_id} · {task.title} · 재개 판단 {RESUME_TEXT[state.state]}"
    blocking = resume.blocking
    if blocking is not None:
        return TaskChoice(label, spec, task.task_id,
                          blocked=f"{blocking.task_id} {RESUME_TEXT[blocking.state]} · {blocking.next_action}")
    if state.state not in (ResumeState.RESUMABLE, ResumeState.RECHECK):
        return TaskChoice(label, spec, task.task_id, blocked=f"{wanted} {RESUME_TEXT[state.state]} · {state.reason}")
    return TaskChoice(label, spec, task.task_id)


def run_preflight(adapter: OrcaCliAdapter, provider: Provider, task: TaskChoice, *, offline: bool,
                  guard: DispatchGuard | None = None, now: datetime | None = None) -> Preflight:
    guard = guard or DispatchGuard()
    runtime, error, run, run_note = _query(adapter, offline)
    bound = adapter.binding.terminal_handle_present
    request = WorkerStartRequest(task.spec or MINIMAL_SPEC, provider.value, task.title or MINIMAL_TITLE,
                                 run.run_id if run else None)
    command = worker_start_args(request)
    decision = guard.evaluate(
        DispatchRequest(provider, task_id=task.title or "preflight"),
        runtime=runtime, runtime_error=error, binding_present=bound,
        environ=adapter.environ, now=now or datetime.now(timezone.utc),
    )
    names = tuple(n for n, v in observe_auth_environment(provider, adapter.environ).items() if v == "SET")
    pf = Preflight(provider, bound, runtime, error, not offline, run, run_note, task, command, decision, names)
    if task.blocked:
        pf.extra.append(("작업·복구", task.blocked))
    return pf


# --- text -------------------------------------------------------------------------------


def _quote(arg: str) -> str:
    return subprocess.list2cmdline([arg])


def command_text(command: list[str], spec: str) -> str:
    shown = [f'"<작업 내용 {len(spec)}자>"' if a == spec else _quote(a) for a in command]
    return "orca " + " ".join(shown)


def _row(label: str, value: str) -> str:
    return label + " " * max(2, 12 - display_width(label)) + value


def runtime_text(pf: Preflight) -> str:
    if not pf.runtime_queried:
        return "조회 안 함 (--offline)"
    if pf.runtime_error is not None:
        return f"조회 실패 ({pf.runtime_error.category})"
    rt = pf.runtime
    if rt is None:
        return "응답 없음"
    version = rt.app_version or "버전 미확인"
    pinned = "검증 버전" if rt.version_pinned else "검증되지 않은 버전"
    state = "연결됨" if rt.connected else f"연결 안 됨 ({rt.runtime_state or '상태 미확인'})"
    return f"Orca {version} ({pinned}) · Runtime {state}"


def reason_text(pf: Preflight, code: Reason) -> str:
    label = REASON_LABELS[code]
    if code is Reason.EXPERIMENT_4A_NOT_PASSED:
        label += f" (현재 {EXPERIMENT_4A_STATUS})"
    if code is Reason.API_KEY_ENVIRONMENT_PRESENT and pf.auth_names:
        label += f": {', '.join(pf.auth_names)} (값은 읽지 않음)"
    if code in (Reason.RUNTIME_UNAVAILABLE, Reason.RUNTIME_STATUS_INVALID) and not pf.runtime_queried:
        label = "Runtime을 조회하지 않음 (--offline)"
    return label


def lines(pf: Preflight) -> list[str]:
    name = PROVIDER_TEXT[pf.provider]
    out = [
        "Worker 전송 전 점검 · 확인만 함 (Run 생성·Worker 기동·작업 전송 없음)",
        _row("Provider", f"{name} (Orca agent {pf.provider.value}) · 실행 표면은 Orca의 새 에이전트 탭 설정을 따름"),
        _row("Orca", runtime_text(pf)),
        _row("바인딩", "Coordinator 터미널 바인딩 있음 (핸들 값은 표시하지 않음)" if pf.bound else "없음 · Orca 터미널 안에서 실행해야 함"),
        _row("Run", f"{pf.run.run_id} · {pf.run_note}" if pf.run else pf.run_note),
        _row("작업", pf.task.label),
    ]
    if pf.task.spec:
        out.append(_row("보낼 명령", command_text(pf.command, pf.task.spec)))
        out.append(_row("", "구성만 함 · 이 빌드에는 worker-start를 보내는 경로가 없음"))
    out.append("Worker 명령 근거 (문서·소스 / 실측)")
    for item in WORKER_CONTRACT:
        out.append(f"  · {item.name}: {item.documented.value} / {item.observed.value}")
    out.append(f"4-A 근거 ({name}) · {EXPERIMENT_4A_STATUS}")
    for ev in PROVIDER_EVIDENCE[pf.provider]:
        out.append(f"  · {ev.item}: {ev.status}")
        out.append(f"      확보: {ev.secured}")
        out.append(f"      남음: {ev.missing}")
    out += [
        "필요한 승인",
        f"  · 4-A 통과 (현재 {EXPERIMENT_4A_STATUS}): 위 '남음' 항목의 근거",
        (f"  · 4-B 실행 승인 (사용자): 지난 1회 승인은 실행에 사용됨 (결과 {EXPERIMENT_4B_CLAUDE_STATUS}) · "
         "다시 실행하려면 새 승인" if pf.provider is Provider.CLAUDE
         else f"  · 4-C 실행 승인 (사용자): {name} 최소 Worker 1회 · 없음"),
        f"  · {name} 사용량 측정 분리: 사용자 또는 별도 수행자 (같은 사용량 풀의 다른 세션과 분리)",
        "  · 실제 전송 경로: 이 빌드에 없음 · 별도 변경과 승인 필요",
    ]
    grouped: dict[str, list[str]] = {}
    for r in pf.decision.reasons:
        grouped.setdefault(CATEGORY[r.code], []).append(reason_text(pf, r.code))
    for category, text in pf.extra:
        grouped.setdefault(category, []).append(text)
    total = sum(len(v) for v in grouped.values())
    out.append(f"차단 사유 {total}건")
    for category in CATEGORY_ORDER:
        for text in grouped.get(category, []):
            out.append(f"  [{category}] {text}")
    out.append("판정: 전송 차단 · 아무것도 보내지 않았습니다." if pf.blocked
               else "판정: 전송 조건 충족 (이 빌드는 보내지 않음)")
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orca_orchestrator.preflight",
        description="Worker에 작업을 보내기 직전까지 확인합니다: 예정 Provider, Coordinator 바인딩, Run과 작업, "
        "보낼 worker-start 명령, 필요한 승인과 차단 사유. Run을 만들거나 Worker를 기동하거나 작업을 보내지 않습니다.",
    )
    parser.add_argument("--provider", choices=[p.value for p in Provider], default="claude",
                        help="예정 Provider (기본 claude)")
    parser.add_argument("--tasks", metavar="PLAN_JSON", help="작업 정의 파일. 없으면 4-B 최소 작업으로 점검합니다.")
    parser.add_argument("--results", metavar="RESULTS_JSON", help="검증 결과 파일 (--tasks와 함께)")
    parser.add_argument("--repo", metavar="DIR", help="저장소 루트 (--tasks와 함께, 기본: 현재 폴더)")
    parser.add_argument("--task", metavar="TASK_ID", help="보낼 작업 (기본: 흐름이 다음에 고를 작업)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="Orca 명령을 하나도 실행하지 않습니다.")
    mode.add_argument("--demo", action="store_true", help="공개 픽스처의 Orca 응답으로 점검합니다. 실제 CLI를 호출하지 않습니다.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.results or args.repo or args.task) and not args.tasks:
        parser.error("--results, --repo, --task는 --tasks와 함께 씁니다.")
    if args.tasks and not args.results:
        parser.error("--tasks에는 --results가 필요합니다.")
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    if args.demo:
        adapter, _ = demo_adapter("connected")
    else:
        adapter = OrcaCliAdapter(ReadOnlyRunner(SubprocessRunner()), timeout=PREFLIGHT_TIMEOUT_SECONDS)
    try:
        task = choose_task(args)
    except VerificationSetupError as exc:
        print(f"점검하지 않음 · {exc.message}")
        for issue in exc.issues[:5]:
            print(f"  · {issue_text(issue)}")
        return EXIT_UNUSABLE
    pf = run_preflight(adapter, Provider(args.provider), task, offline=args.offline)
    for line in lines(pf):
        print(line, flush=True)
    return EXIT_BLOCKED if pf.blocked else EXIT_WOULD_SEND


if __name__ == "__main__":
    sys.exit(main())
