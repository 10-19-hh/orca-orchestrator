"""``python -m orca_orchestrator.handoff`` — what the last sessions left behind, and whether work can resume.

Without options it only reads: the handoff record is compared with the current
plan, code and results, and each task is shown as done, resumable, needing a
re-check, needing its run state confirmed, or running in another session,
with the last attempt's run IDs, completed and remaining checks, why it
stopped and what to do next.

``--confirm-stopped TASK --note TEXT`` records that the person checked the
task's last run is no longer running (refused while a session holds the task).
Nothing is run or resumed here; ``python -m orca_orchestrator.flow`` resumes.

Exit codes: 0 nothing blocks a resume (or the confirmation was recorded),
1 confirmation refused, 2 files unusable, 6 another session runs a task,
7 a run's end must be confirmed first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .tasks.codebase import code_basis
from .tasks.flow import inspect, resume_view
from .tasks.handoff import (
    CHECK_HINT,
    RESUME_TEXT,
    ClaimRefused,
    ResumeState,
    ResumeView,
    attempt_line,
    confirm_stopped,
    progress_line,
    run_line,
)
from .tasks.paths import handoff_path, tool_files
from .tasks.verification import VerificationSetupError
from .tui.render import issue_text, local_time

EXIT_OK, EXIT_REFUSED_CONFIRM, EXIT_UNUSABLE, EXIT_BUSY, EXIT_CHECK = 0, 1, 2, 6, 7


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orca_orchestrator.handoff",
        description="실행 기록(인수인계)을 현재 작업 정의·코드·검증 결과와 대조해 작업별 재개 가능 여부와 다음 행동을 보여 줍니다. "
        "아무것도 실행하거나 재개하지 않습니다. 재개는 python -m orca_orchestrator.flow로 직접 합니다.",
    )
    parser.add_argument("--tasks", required=True, metavar="PLAN_JSON", help="작업 정의 파일")
    parser.add_argument("--results", required=True, metavar="RESULTS_JSON", help="검증 결과 파일")
    parser.add_argument("--repo", default=".", metavar="DIR", help="저장소 루트 (기본: 현재 폴더)")
    parser.add_argument("--confirm-stopped", metavar="TASK_ID",
                        help="이 작업의 마지막 실행이 더 이상 돌지 않음을 확인했다고 기록합니다.")
    parser.add_argument("--note", metavar="TEXT", help="무엇을 확인했는지 (--confirm-stopped와 함께 필수)")
    return parser


def _say(line: str = "") -> None:
    print(line, flush=True)


def print_resume(resume: ResumeView, source_digest: str | None) -> None:
    _say(f"재개: {resume.verdict()}")
    _say(f"다음 행동: {resume.next_action()}")
    _say("작업별 상태:")
    for t in resume.tasks:
        _say(f"  {t.task_id}  {RESUME_TEXT[t.state]} · {t.reason}")
        a = t.attempt
        if a is None:
            _say("       실행 기록 없음")
            continue
        live = t.state is ResumeState.RUNNING
        _say(f"       마지막 실행 {attempt_line(a, live, local_time)}")
        _say(f"       {run_line(a, live)}")
        _say(f"       {progress_line(a)}")
        same = "현재와 같음" if source_digest and a.source_digest == source_digest else "현재와 다름"
        base = f"{a.base}({a.base_sha[:7]})" if a.base and a.base_sha else (a.base or "없음")
        changes = f"{a.changes_count}개" if a.changes_count is not None else "비교 불가"
        head = f"HEAD {a.git_head[:7]}{' + 커밋 전 변경' if a.git_dirty else ''} · " if a.git_head else ""
        digest = f"지문 {a.source_digest[:8]} ({same})" if a.source_digest else "지문 없음"
        _say(f"       시작 기준: {head}{digest} · 비교 기준 {base} · 변경 {changes}")
        if t.since:
            _say(f"       기록 후 바뀐 것: {', '.join(t.since)}")
        refs = [r.result_ref for r in a.runs if r.result_ref]
        if refs:
            _say(f"       결과 참조: {', '.join(refs)}")
        if a.next_action and a.next_action != t.next_action and t.state is not ResumeState.DONE:
            _say(f"       기록된 다음 행동: {a.next_action}")
        _say(f"       지금 할 일: {t.next_action}")
        if a.resolved:
            _say(f"       중지 확인: {local_time(a.resolved_at)} · {a.resolved_note}")
    if any(t.state is ResumeState.CHECK_RUN for t in resume.tasks):
        _say(CHECK_HINT)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.note and not args.confirm_stopped:
        parser.error("--note는 --confirm-stopped와 함께 씁니다.")
    if args.confirm_stopped and not (args.note and args.note.strip()):
        parser.error("--confirm-stopped에는 무엇을 확인했는지 --note로 남겨야 합니다.")
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    plan, results, repo = Path(args.tasks), Path(args.results), Path(args.repo)
    _say(f"인수인계 · {plan.name} · {results.name} · 기록 {handoff_path(results).name}")
    if args.confirm_stopped:
        try:
            attempt = confirm_stopped(results, args.confirm_stopped, args.note)
        except ClaimRefused as exc:
            _say(f"기록하지 않음 · {exc.message}")
            return EXIT_REFUSED_CONFIRM
        except VerificationSetupError as exc:
            _say(f"기록하지 않음 · {exc.message}")
            return EXIT_REFUSED_CONFIRM
        _say(f"중지 확인 기록: {attempt.task} · {attempt.attempt_id} · {args.note.strip()}")
        _say("재개는 python -m orca_orchestrator.flow로 직접 실행합니다.")
    try:
        view = inspect(plan, results, repo)
    except VerificationSetupError as exc:
        _say(f"대조하지 않음 · {exc.message}")
        for issue in exc.issues[:5]:
            _say(f"  · {issue_text(issue)}")
        return EXIT_UNUSABLE
    basis = code_basis(repo, tool_files(plan, results))
    if view.source is not None and view.source.digest:
        _say(f"현재 코드: {basis.text()} · 지문 {view.source.digest[:8]}")
    resume = resume_view(plan, results, view)
    print_resume(resume, view.source.digest if view.source else None)
    if args.confirm_stopped:
        return EXIT_OK
    if resume.error or (resume.blocking and resume.blocking.state is ResumeState.CHECK_RUN):
        return EXIT_CHECK
    if resume.blocking:
        return EXIT_BUSY
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
