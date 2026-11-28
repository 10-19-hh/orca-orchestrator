"""``python -m orca_orchestrator`` — Orca status and task console.

In an interactive terminal this opens the Textual console (``console.app``)
on the pixel yard (``console.yard_screen``); details open in the tabbed
console, which adds task selection, local verification, the flow, review,
approval and a read-only preflight. ``--console`` opens the tabs directly. ``--once`` and non-interactive output print the
read-only text screen exactly as before; ``--once --json`` prints the same
collection as one ``orca-orchestrator/snapshot@1`` JSON document instead
(``snapshot_document``); ``--plain`` opens the earlier
read-only interactive screen. Without Textual installed, the earlier screen
opens with a note on how to install it.

``--topology-preview`` opens a separate office preview fed by read-only
``topology@1`` polls (``console.office_screen``); with ``--demo`` it plays a
synthetic timeline. It never touches the yard, the console or Snapshot v1, and
the topology modules are imported only on that path.
"""

from __future__ import annotations

import argparse
import sys

from .orca.adapter import OrcaCliAdapter
from .orca.dispatch import DispatchGuard
from .orca.runner import SubprocessRunner
from .tasks import DEMO_RESULTS, DemoTaskSource, FileTaskSource
from .tasks.verification import DEFAULT_TIMEOUT_SECONDS
from .tui.app import run_interactive
from .tui.demo import SCENARIOS, demo_adapter
from .tui.readonly import ReadOnlyRunner
from .tui.render import render
from .tui.snapshot import collect_snapshot

SCREEN_TIMEOUT_SECONDS = 10.0
TUI_REQUIREMENT = "textual>=8.2.8,<9"
TUI_MISSING = ("대화형 TUI에 필요한 textual이 설치되어 있지 않아 기존 읽기 전용 화면을 엽니다. "
               f'설치: python -m pip install "{TUI_REQUIREMENT}" (저장소에서는 python -m pip install -e ".[tui]")')


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
    parser.add_argument(
        "--repo",
        metavar="DIR",
        help="검증 대상 코드와 변경을 확인할 저장소 루트입니다 (기본: 현재 폴더). --tasks와 함께 씁니다. "
        "읽기 전용 git 명령만 실행합니다.",
    )
    parser.add_argument("--once", action="store_true", help="화면을 한 번 출력하고 종료합니다.")
    parser.add_argument("--json", action="store_true",
                        help="--once와 함께: 같은 읽기 전용 조회 결과를 snapshot@1 JSON 문서 하나로 출력합니다 "
                        "(GUI용 계약, docs/snapshot-v1.md).")
    parser.add_argument("--plain", action="store_true",
                        help="대화형 TUI 대신 기존 읽기 전용 화면(r 새로고침 · q 종료)을 엽니다.")
    parser.add_argument("--console", action="store_true",
                        help="픽셀 마당 대신 기존 탭 콘솔(작업·변경 검토·복구·전송 전 점검)을 바로 엽니다.")
    parser.add_argument("--motion", choices=["normal", "reduced", "off"], default="normal",
                        help="마당 움직임: normal 보통, reduced 줄이기(걷기·달리기 없음), off 정지 (기본 normal). "
                        "마당에서 m으로도 바꿉니다.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, metavar="SECONDS",
                        help=f"TUI에서 실행하는 검증 하나의 제한 시간 (기본 {DEFAULT_TIMEOUT_SECONDS:g}초)")
    parser.add_argument("--topology-preview", action="store_true",
                        help="Orca가 보고하는 세션으로 움직이는 픽셀 사무실 미리보기 (읽기 전용 조회만, 기존 마당과 별개). "
                        "--demo와 함께 쓰면 합성 진행 장면을 보여 줍니다.")
    parser.add_argument("--topology-scene", metavar="SCENE",
                        help="--topology-preview --demo와 함께: timeline(기본) 또는 topology DEMO 장면 이름")
    parser.add_argument("--interval", type=float, metavar="SECONDS",
                        help=f"--topology-preview의 조회 간격 (LIVE 기본 {PREVIEW_LIVE_INTERVAL:g}초·최소 "
                        f"{PREVIEW_LIVE_MIN:g}초, DEMO 기본 {PREVIEW_DEMO_INTERVAL:g}초)")
    return parser


PREVIEW_LIVE_INTERVAL, PREVIEW_LIVE_MIN, PREVIEW_DEMO_INTERVAL, PREVIEW_DEMO_MIN = 5.0, 2.0, 3.0, 0.5
PREVIEW_CONFLICTS = (("tasks", "--tasks"), ("results", "--results"), ("repo", "--repo"),
                     ("demo_results", "--demo-results"), ("json", "--json"), ("plain", "--plain"),
                     ("console", "--console"))


def topology_preview(args, parser: argparse.ArgumentParser) -> int:
    """``--topology-preview``: the office preview fed by ``topology@1`` (read-only). Imported only here."""

    for attr, flag in PREVIEW_CONFLICTS:
        if getattr(args, attr):
            parser.error(f"--topology-preview는 {flag}와 함께 쓸 수 없습니다.")
    from .console.motion import Motion
    from .topology.feed import LiveSource, TimelineSource, TopologyFeed
    from .topology.scenarios import PREVIEW_STEP_NOTES, SCENES, preview_timeline

    if args.demo:
        name = args.topology_scene or "timeline"
        if name == "timeline":
            source = TimelineSource(preview_timeline(), PREVIEW_STEP_NOTES)
        elif name in SCENES:
            source = TimelineSource(SCENES[name])
        else:
            parser.error(f"--topology-scene: timeline 또는 {', '.join(SCENES)} 중 하나")
        interval, low = args.interval or PREVIEW_DEMO_INTERVAL, PREVIEW_DEMO_MIN
    else:
        if args.topology_scene:
            parser.error("--topology-scene은 --demo와 함께 써야 합니다.")
        source = LiveSource()
        interval, low = args.interval or PREVIEW_LIVE_INTERVAL, PREVIEW_LIVE_MIN
    if interval < low:
        parser.error(f"--interval은 {low:g}초 이상이어야 합니다.")
    feed = TopologyFeed(source)
    if args.once or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return preview_text(feed, source)
    try:
        from .console.office_screen import run_topology_preview
    except ImportError as exc:
        if not _missing_tui(exc):
            raise
        print(TUI_MISSING.replace("기존 읽기 전용 화면을 엽니다", "텍스트 요약을 출력합니다"), file=sys.stderr, flush=True)
        return preview_text(feed, source)
    return run_topology_preview(feed, interval=interval, motion=Motion(args.motion))


def preview_text(feed, source) -> int:
    """Non-interactive: one text summary per observation (every step of a DEMO scene, one live poll)."""

    from .topology.__main__ import text_lines

    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    polls = getattr(source, "total", 1)
    blocks = []
    for _ in range(polls):
        result = feed.poll_now()
        if result is None or result.document is None:
            print(f"조회 실패: {result.error if result else '진행 중'}", file=sys.stderr)
            return 1
        blocks.append("\n".join(text_lines(result.document)))
    feed.close()
    print("\n\n".join(blocks))
    return 0


def _missing_tui(exc: ImportError) -> bool:
    name = getattr(exc, "name", None) or ""
    return name.split(".")[0] in ("textual", "rich")


def _launch_args(args) -> tuple[str, ...]:
    out: list[str] = []
    for flag, value in (("--tasks", args.tasks), ("--results", args.results), ("--repo", args.repo)):
        if value:
            out += [flag, value]
    return tuple(out)


def open_console(args, collect) -> int:
    """The Textual console, or the earlier screen when Textual is not installed."""

    try:
        from .console.app import run_console
    except ImportError as exc:
        if not _missing_tui(exc):
            raise
        print(TUI_MISSING, file=sys.stderr, flush=True)
        return run_interactive(collect)
    from pathlib import Path

    from .console.bridge import Console, Target
    from .console.motion import Motion

    target = Target(
        plan=Path(args.tasks) if args.tasks else None,
        results=Path(args.results) if args.results else None,
        repo=Path(args.repo or ".") if args.tasks else None,
        demo=args.demo,
        demo_results=args.demo_results,
        timeout=args.timeout,
        args=_launch_args(args),
    )
    return run_console(Console(target, collect), yard=not args.console, motion=Motion(args.motion))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.topology_preview:
        return topology_preview(args, parser)
    if args.topology_scene or args.interval is not None:
        parser.error("--topology-scene·--interval은 --topology-preview와 함께 써야 합니다.")
    if args.demo_results and not args.demo:
        parser.error("--demo-results는 --demo와 함께 써야 합니다.")
    if args.demo and (args.tasks or args.results):
        parser.error("--demo는 포함된 데모 작업 목록만 씁니다. --tasks/--results와 함께 쓸 수 없습니다.")
    if args.results and not args.tasks:
        parser.error("--results는 --tasks와 함께 써야 합니다.")
    if args.repo and not args.tasks:
        parser.error("--repo는 --tasks와 함께 써야 합니다.")
    if args.timeout <= 0:
        parser.error("--timeout은 0보다 커야 합니다.")
    if args.json and not args.once:
        parser.error("--json은 --once와 함께 써야 합니다.")
    if args.json and (args.plain or args.console):
        parser.error("--json은 --plain·--console과 함께 쓸 수 없습니다.")

    if args.demo:
        adapter, _ = demo_adapter(args.demo)
        tasks = DemoTaskSource(args.demo_results or "passed")
    else:
        adapter = OrcaCliAdapter(ReadOnlyRunner(SubprocessRunner()), timeout=SCREEN_TIMEOUT_SECONDS)
        tasks = FileTaskSource(args.tasks, args.results, args.repo or ".") if args.tasks else None
    guard = DispatchGuard()  # default policy: real dispatch blocked

    def collect():
        return collect_snapshot(adapter, guard, demo=args.demo, tasks=tasks)

    if args.once or not (sys.stdin.isatty() and sys.stdout.isatty()):
        try:
            sys.stdout.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
        if args.json:
            import json

            from .snapshot_document import snapshot_document

            print(json.dumps(snapshot_document(collect()), ensure_ascii=False, indent=2))
            return 0
        print("\n".join(render(collect())))
        return 0
    if args.plain:
        return run_interactive(collect)
    return open_console(args, collect)


if __name__ == "__main__":
    sys.exit(main())
