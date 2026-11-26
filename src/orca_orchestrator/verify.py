"""``python -m orca_orchestrator.verify`` — run one supported local check and record it.

Exit codes: 0 passed, 1 failed, 2 refused before running (nothing changed),
3 not completed (timeout or launch failure; nothing recorded).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .tasks.contract import Outcome
from .tasks.verification import (
    DEFAULT_TIMEOUT_SECONDS,
    SUPPORTED_CHECKS,
    VerificationNotCompleted,
    VerificationNotRecorded,
    VerificationSetupError,
    verify,
)
from .tui.render import issue_text

EXIT_PASSED, EXIT_FAILED, EXIT_REFUSED, EXIT_NOT_COMPLETED = 0, 1, 2, 3
OUTPUT_TAIL_LINES = 15


def build_parser() -> argparse.ArgumentParser:
    checks = ", ".join(f"{c.check_id} ({c.display})" for c in SUPPORTED_CHECKS.values())
    parser = argparse.ArgumentParser(
        prog="python -m orca_orchestrator.verify",
        description="작업 정의의 검증 조건 하나를 로컬에서 한 번 실행하고 결과 파일에 기록합니다. "
        f"지원 검증 조건: {checks}. 작업 정의 JSON의 command 문자열은 실행하지 않습니다.",
    )
    parser.add_argument("--tasks", required=True, metavar="PLAN_JSON", help="작업 정의 파일")
    parser.add_argument("--results", required=True, metavar="RESULTS_JSON",
                        help="검증 결과 파일. 없으면 만들고, 다른 작업의 결과는 보존합니다.")
    parser.add_argument("--task", required=True, metavar="TASK_ID", help="검증할 작업 ID")
    parser.add_argument("--check", required=True, metavar="CHECK_ID", help="실행할 검증 조건 ID")
    parser.add_argument("--repo", default=".", metavar="DIR", help="테스트를 실행할 저장소 루트 (기본: 현재 폴더)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, metavar="SECONDS",
                        help=f"제한 시간 (기본 {DEFAULT_TIMEOUT_SECONDS:g}초). 넘기면 결과를 기록하지 않습니다.")
    return parser


def _say(line: str = "") -> None:
    print(line, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout은 0보다 커야 합니다.")
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    target = f"{args.task}/{args.check}"
    results_name = Path(args.results).name
    command = SUPPORTED_CHECKS.get(args.check)
    if command is not None:
        _say(f"실행: {target} → {command.display} (한 번, 제한 {args.timeout:g}초)")
    try:
        run = verify(args.tasks, args.results, args.task, args.check, repo=args.repo, timeout=args.timeout)
    except VerificationSetupError as exc:
        _say(f"검증하지 않음 · {exc.message}")
        for issue in exc.issues[:5]:
            _say(f"  · {issue_text(issue)}")
        if len(exc.issues) > 5:
            _say(f"  · … 외 {len(exc.issues) - 5}건")
        _say("파일을 바꾸지 않았습니다.")
        return EXIT_REFUSED
    except VerificationNotRecorded as exc:
        outcome = "통과" if exc.result.outcome is Outcome.PASSED else "실패"
        _say(f"검증 미완료 · {target} · 테스트는 끝났지만({outcome}, 종료 코드 {exc.result.exit_code}) {exc.message}")
        for issue in exc.issues[:5]:
            _say(f"  · {issue_text(issue)}")
        _say(f"결과를 기록하지 않았습니다. 기존 결과 파일은 그대로입니다. ({results_name})")
        return EXIT_NOT_COMPLETED
    except VerificationNotCompleted as exc:
        _say(f"검증 미완료 · {target} · {exc.message}")
        _say(f"결과를 기록하지 않았습니다. 완료 상태는 바뀌지 않습니다. ({results_name})")
        return EXIT_NOT_COMPLETED

    passed = run.result.exit_code == 0
    _say(f"검증 {'통과' if passed else '실패'} · {target} · 종료 코드 {run.result.exit_code} · {run.summary}")
    kept = f"다른 결과 {run.kept}건 보존" + (" · 이전 결과 교체" if run.replaced else "")
    _say(f"기록: {results_name} ({kept})")
    if not passed:
        tail = (run.output.stderr + run.output.stdout).rstrip().splitlines()[-OUTPUT_TAIL_LINES:]
        if tail:
            _say("출력 마지막 부분:")
            for line in tail:
                _say(f"  {line}")
    return EXIT_PASSED if passed else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
