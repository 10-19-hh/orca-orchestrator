"""``python -m orca_orchestrator`` — read-only Orca status screen."""

from __future__ import annotations

import argparse
import sys

from .orca.adapter import OrcaCliAdapter
from .orca.dispatch import DispatchGuard
from .orca.runner import SubprocessRunner
from .tasks import DEMO_RESULTS, DemoTaskSource, FileTaskSource
from .tui.app import run_interactive
from .tui.demo import SCENARIOS, demo_adapter
from .tui.readonly import ReadOnlyRunner
from .tui.render import render
from .tui.snapshot import collect_snapshot

SCREEN_TIMEOUT_SECONDS = 10.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orca_orchestrator",
        description="Orca Runtime, 바인딩, 현재 Run, 작업 전송 보류 이유, 작업 목록을 보여주는 읽기 전용 화면",
    )
    parser.add_argument(
        "--demo",
        nargs="?",
        const="connected",
        choices=list(SCENARIOS),
        metavar="SCENARIO",
        help="공개 픽스처로 표시합니다. 실제 CLI를 호출하지 않습니다. "
        f"SCENARIO: {', '.join(SCENARIOS)} (기본 connected)",
    )
    parser.add_argument(
        "--demo-results",
        choices=list(DEMO_RESULTS),
        metavar="RESULTS",
        help="데모 작업 목록에 쓸 선행 작업 검증 결과입니다. "
        f"RESULTS: {', '.join(DEMO_RESULTS)} (기본 passed). --demo와 함께 씁니다.",
    )
    parser.add_argument("--tasks", metavar="PLAN_JSON", help="작업 정의 파일(JSON)을 읽어 작업 목록을 표시합니다.")
    parser.add_argument(
        "--results",
        metavar="RESULTS_JSON",
        help="검증 결과 파일(JSON)입니다. --tasks와 함께 씁니다. 명령을 실행하지 않고 기록된 결과만 읽습니다.",
    )
    parser.add_argument("--once", action="store_true", help="화면을 한 번 출력하고 종료합니다.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.demo_results and not args.demo:
        parser.error("--demo-results는 --demo와 함께 써야 합니다.")
    if args.demo and (args.tasks or args.results):
        parser.error("--demo는 포함된 데모 작업 목록만 씁니다. --tasks/--results와 함께 쓸 수 없습니다.")
    if args.results and not args.tasks:
        parser.error("--results는 --tasks와 함께 써야 합니다.")

    if args.demo:
        adapter, _ = demo_adapter(args.demo)
        tasks = DemoTaskSource(args.demo_results or "passed")
    else:
        adapter = OrcaCliAdapter(ReadOnlyRunner(SubprocessRunner()), timeout=SCREEN_TIMEOUT_SECONDS)
        tasks = FileTaskSource(args.tasks, args.results) if args.tasks else None
    guard = DispatchGuard()  # default policy: real dispatch blocked

    def collect():
        return collect_snapshot(adapter, guard, demo=args.demo, tasks=tasks)

    if args.once or not (sys.stdin.isatty() and sys.stdout.isatty()):
        try:
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
        print("\n".join(render(collect())))
        return 0
    return run_interactive(collect)


if __name__ == "__main__":
    sys.exit(main())
