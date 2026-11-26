"""``python -m orca_orchestrator`` — read-only Orca status screen."""

from __future__ import annotations

import argparse
import sys

from .orca.adapter import OrcaCliAdapter
from .orca.dispatch import DispatchGuard
from .orca.runner import SubprocessRunner
from .tui.app import run_interactive
from .tui.demo import SCENARIOS, demo_adapter
from .tui.readonly import ReadOnlyRunner
from .tui.render import render
from .tui.snapshot import collect_snapshot

SCREEN_TIMEOUT_SECONDS = 10.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orca_orchestrator",
        description="Orca Runtime, 바인딩, 현재 Run, 작업 전송 보류 이유를 보여주는 읽기 전용 화면",
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
    parser.add_argument("--once", action="store_true", help="화면을 한 번 출력하고 종료합니다.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.demo:
        adapter, _ = demo_adapter(args.demo)
    else:
        adapter = OrcaCliAdapter(ReadOnlyRunner(SubprocessRunner()), timeout=SCREEN_TIMEOUT_SECONDS)
    guard = DispatchGuard()  # default policy: real dispatch blocked

    def collect():
        return collect_snapshot(adapter, guard, demo=args.demo)

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
