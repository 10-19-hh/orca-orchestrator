"""``python -m orca_orchestrator.flow`` — verify ready tasks in order and record each result.

Exit codes: 0 every task verified locally (final approval still the person's),
1 a check failed, 2 refused before running, 3 not completed or not recorded,
4 stopped with tasks left that cannot be selected, 5 stopped at --max-tasks.
``--dry-run`` only judges and prints; it runs and writes nothing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .tasks.flow import (
    SELECTION_RULE,
    FlowView,
    Selection,
    StopKind,
    flow_status_path,
    inspect,
    run_flow,
    trace_lines,
)
from .tasks.verification import (
    DEFAULT_TIMEOUT_SECONDS,
    SUPPORTED_CHECKS,
    VerificationRun,
    VerificationSetupError,
)
from .tui.render import issue_text

EXIT_CODES = {
    StopKind.DONE: 0,
    StopKind.FAILED: 1,
    StopKind.REFUSED: 2,
    StopKind.NOT_COMPLETED: 3,
    StopKind.NOT_RECORDED: 3,
    StopKind.WAITING: 4,
    StopKind.LIMIT: 5,
}
STOP_TITLES = {
    StopKind.DONE: "흐름 끝남",
    StopKind.WAITING: "흐름 멈춤",
    StopKind.LIMIT: "흐름 멈춤",
    StopKind.FAILED: "흐름 중단",
    StopKind.NOT_COMPLETED: "흐름 중단",
    StopKind.NOT_RECORDED: "흐름 중단",
    StopKind.REFUSED: "흐름 중단",
}
APPROVAL_LINE = "최종 승인: 대기 · 로컬 검증 결과와 별개로 사용자가 직접 승인합니다 (자동 승인 없음)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orca_orchestrator.flow",
        description="작업 정의에서 검증할 수 있는 작업을 계획 순서대로 골라 하나씩 로컬 검증하고 결과를 기록합니다. "
        f"자동 실행하는 검증 조건: {', '.join(SUPPORTED_CHECKS)}. 실패하거나 결과를 남기지 못하면 멈추며 다시 실행하지 않습니다.",
    )
    parser.add_argument("--tasks", required=True, metavar="PLAN_JSON", help="작업 정의 파일")
    parser.add_argument("--results", required=True, metavar="RESULTS_JSON",
                        help="검증 결과 파일. 없으면 만들고, 이미 유효한 결과가 있는 검증은 다시 실행하지 않습니다.")
    parser.add_argument("--repo", default=".", metavar="DIR", help="테스트를 실행할 저장소 루트 (기본: 현재 폴더)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, metavar="SECONDS",
                        help=f"검증 하나의 제한 시간 (기본 {DEFAULT_TIMEOUT_SECONDS:g}초)")
    parser.add_argument("--max-tasks", type=int, metavar="N", help="이번 실행에서 검증할 작업 수 상한")
    parser.add_argument("--dry-run", action="store_true", help="다음 작업과 대기 이유만 보여 주고 아무것도 실행하지 않습니다.")
    return parser


def _say(line: str = "") -> None:
    print(line, flush=True)


def _print_view(view: FlowView) -> None:
    if view.next is not None:
        _say(f"다음 작업: {view.next.task_id} · {view.next.reason}")
    elif view.all_verified:
        _say("다음 작업: 없음 · 모든 작업의 로컬 검증 통과")
    else:
        _say("다음 작업: 없음")
    if view.blockers:
        _say("진행할 수 없는 작업:")
        for b in view.blockers:
            _say(f"  {b.task_id}: {b.reason}" + (f" — {b.hint}" if b.hint else ""))
    _say("추적 (요구사항 → 작업 → 검증 조건 → 결과):")
    for line in trace_lines(view.progress):
        _say(f"  {line}")
    _say(APPROVAL_LINE)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout은 0보다 커야 합니다.")
    if args.max_tasks is not None and args.max_tasks < 1:
        parser.error("--max-tasks는 1 이상이어야 합니다.")
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    plan_name, results_name = Path(args.tasks).name, Path(args.results).name
    _say(f"작업 정의 {plan_name} · 검증 결과 {results_name}")
    _say(f"선택 규칙: {SELECTION_RULE}")

    if args.dry_run:
        try:
            view = inspect(Path(args.tasks), Path(args.results))
        except VerificationSetupError as exc:
            _say(f"판단하지 않음 · {exc.message}")
            for issue in exc.issues[:5]:
                _say(f"  · {issue_text(issue)}")
            return EXIT_CODES[StopKind.REFUSED]
        _say("미리 보기: 아무것도 실행하거나 기록하지 않습니다.")
        _print_view(view)
        return 0

    step = {"n": 0}

    def notify(kind: str, payload) -> None:
        if kind == "select":
            step["n"] += 1
            sel: Selection = payload
            _say(f"[{step['n']}] 선택 {sel.task_id} · {sel.reason}")
        elif kind == "start":
            task, check = payload
            _say(f"    실행 {task}/{check} → {SUPPORTED_CHECKS[check].display} (한 번, 제한 {args.timeout:g}초)")
        elif kind == "result":
            run: VerificationRun = payload
            verdict = "통과" if run.result.exit_code == 0 else "실패"
            _say(f"    {verdict} · 종료 코드 {run.result.exit_code} · {run.summary} · 기록 {results_name}")
        elif kind == "warning":
            _say(f"    경고 · {payload}")

    report = run_flow(args.tasks, args.results, repo=args.repo, timeout=args.timeout,
                      max_tasks=args.max_tasks, notify=notify)
    stop = report.stop
    target = f" · {stop.task_id}/{stop.check_id}" if stop.task_id and stop.check_id else (
        f" · {stop.task_id}" if stop.task_id else "")
    _say(f"{STOP_TITLES[stop.kind]}{target} · {stop.message}")
    if report.verified:
        _say(f"이번 실행에서 완료한 작업: {', '.join(report.verified)}")
    if stop.kind is StopKind.FAILED:
        _say(f"실패 결과를 {results_name}에 기록했습니다. 원인을 고친 뒤 verify로 이 검증을 다시 실행하면 같은 명령으로 이어갈 수 있습니다.")
    elif stop.kind in (StopKind.NOT_COMPLETED, StopKind.NOT_RECORDED):
        _say(f"이번 검증은 기록하지 않았습니다. {results_name}의 이전 결과는 그대로이며 같은 명령으로 이어서 실행할 수 있습니다.")
    try:
        view = inspect(Path(args.tasks), Path(args.results))
    except VerificationSetupError as exc:
        view = None
        for issue in exc.issues[:5]:
            _say(f"  · {issue_text(issue)}")
    if view is not None:
        _print_view(view)
    _say(f"흐름 상태: {flow_status_path(Path(args.results)).name}")
    return EXIT_CODES[stop.kind]


if __name__ == "__main__":
    sys.exit(main())
