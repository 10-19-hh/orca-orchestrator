"""``python -m orca_orchestrator.review`` — review changes and evidence, then approve or hold.

Without ``--approve`` or ``--hold`` it only reads and prints. The decision is
written to ``<results>.approval.json`` only by this command and only when the
person passes one of those options. Approval is refused unless every check is
verified for the current plan and code, the change set since the plan's base
could be determined and no protected path changed. Changes that no task's
``paths`` cover must be confirmed with ``--confirm-unlinked``.

Exit codes: 0 approvable / approved / decision recorded, 1 approval blocked or
refused, 2 the files could not be used (nothing changed).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .tasks.codebase import scope_text
from .tasks.review import (
    ApprovalState,
    Decision,
    DecisionRefused,
    Review,
    approval_summary,
    basis_text,
    change_lines,
    changes_summary,
    load_review,
    protected_summary,
    record_decision,
    review_trace_lines,
)
from .tasks.verification import VerificationSetupError
from .tui.render import issue_text, local_time

EXIT_OK, EXIT_BLOCKED, EXIT_REFUSED = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orca_orchestrator.review",
        description="현재 코드에 유효한 검증 결과, 기준 버전 대비 변경 파일, 보호 경로, 요구사항 추적을 보여 줍니다. "
        "--approve 또는 --hold를 줄 때만 사용자 결정을 기록합니다. 자동 승인은 없습니다.",
    )
    parser.add_argument("--tasks", required=True, metavar="PLAN_JSON", help="작업 정의 파일")
    parser.add_argument("--results", required=True, metavar="RESULTS_JSON", help="검증 결과 파일")
    parser.add_argument("--repo", default=".", metavar="DIR", help="검토할 저장소 루트 (기본: 현재 폴더)")
    decision = parser.add_mutually_exclusive_group()
    decision.add_argument("--approve", action="store_true",
                          help="보류 사유가 없을 때만 최종 승인을 기록합니다.")
    decision.add_argument("--hold", metavar="REASON", help="사유와 함께 보류를 기록합니다.")
    parser.add_argument("--note", metavar="TEXT", help="승인 기록에 남길 메모 (--approve와 함께)")
    parser.add_argument("--confirm-unlinked", action="store_true",
                        help="연결된 작업이 없는 변경 파일을 확인했음을 기록합니다 (--approve와 함께).")
    return parser


def _say(line: str = "") -> None:
    print(line, flush=True)


def print_review(review: Review, plan_name: str, results_name: str) -> None:
    code = review.code
    _say(f"검토 · {plan_name} · {results_name}")
    files = f" (검증 대상 {scope_text()} 파일 {code.source.files}개)" if code.source.digest else ""
    _say(f"코드 기준: {basis_text(code)}{files}")
    _say(f"변경: {changes_summary(review)}")
    for line in change_lines(review):
        _say(f"  {line}")
    _say(f"보호 경로: {protected_summary(review)}")
    _say("추적 (요구사항 → 작업 → 검증 조건 → 결과 · 검증한 코드 · 변경 파일):")
    for line in review_trace_lines(review):
        _say(f"  {line}")
    _say(f"최종 승인: {approval_summary(review, local_time)}")
    if review.state is not ApprovalState.APPROVED:
        for blocker in review.blockers:
            _say(f"  · {blocker.text}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.note or args.confirm_unlinked) and not args.approve:
        parser.error("--note와 --confirm-unlinked는 --approve와 함께 씁니다.")
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    plan_name, results_name = Path(args.tasks).name, Path(args.results).name
    decision = Decision.APPROVED if args.approve else (Decision.HELD if args.hold is not None else None)
    try:
        if decision is None:
            review = load_review(args.tasks, args.results, args.repo)
            print_review(review, plan_name, results_name)
            return EXIT_OK if review.approvable else EXIT_BLOCKED
        review, record = record_decision(args.tasks, args.results, decision, repo=args.repo,
                                         note=args.note if decision is Decision.APPROVED else args.hold,
                                         confirm_unlinked=args.confirm_unlinked)
    except VerificationSetupError as exc:
        _say(f"검토하지 않음 · {exc.message}")
        for issue in exc.issues[:5]:
            _say(f"  · {issue_text(issue)}")
        _say("파일을 바꾸지 않았습니다.")
        return EXIT_REFUSED
    except DecisionRefused as exc:
        if exc.review is not None:
            print_review(exc.review, plan_name, results_name)
        word = "승인을" if decision is Decision.APPROVED else "보류를"
        _say(f"{word} 기록하지 않음 · {exc.message}")
        _say("파일을 바꾸지 않았습니다.")
        return EXIT_BLOCKED

    print_review(review, plan_name, results_name)
    if decision is Decision.APPROVED:
        _say(f"승인 기록: {record.name} · 이후 작업 정의·코드·변경 파일이 바뀌면 상태 화면에 재승인 필요로 표시됩니다.")
    else:
        _say(f"보류 기록: {record.name} · 승인하려면 보류 사유를 해결한 뒤 --approve로 다시 기록합니다.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
