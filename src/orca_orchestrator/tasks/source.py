"""Reads a task plan, verification results, flow status and review into a displayable board.

Sources only read files (and, with a repository, run read-only Git commands).
A board is rebuilt on every refresh, so edits to the files (including what a
running flow writes) and to the code show up after pressing ``r``. Loading a
board never records a decision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Callable, Protocol

from .codebase import ChangeSet, ChangeStatus, CodeBasis, FileChange, SourceState
from .contract import PlanFormatError, PlanIssue, TaskPlan, parse_plan, parse_results, plan_digest, validate
from .flow import FlowStatus, FlowStatusError, FlowView, parse_flow_status, select
from .paths import approval_path, flow_status_path, tool_files
from .progress import PlanProgress, evaluate
from .review import CodeState, Review, build_review, parse_approval_text, read_code


class BoardState(str, Enum):
    OK = "ok"
    INVALID = "invalid"  # contract issues: nothing is judged
    UNREADABLE = "unreadable"  # file, JSON or shape problem
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class TaskBoard:
    state: BoardState
    label: str = ""
    progress: PlanProgress | None = None
    issues: tuple[PlanIssue, ...] = ()
    error: str | None = None
    view: FlowView | None = None  # next task and blockers, when OK
    flow: FlowStatus | None = None  # what the last flow run recorded, if any
    flow_error: str | None = None  # the flow status file exists but cannot be read
    review: Review | None = None  # code basis, changes and approval, when a repository is known


NOT_CONFIGURED = TaskBoard(BoardState.NOT_CONFIGURED)


class TaskSource(Protocol):
    def load(self) -> TaskBoard: ...


def _json(text: str, name: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanFormatError(name, f"JSON 형식 오류 ({exc.lineno}행)") from None


def _flow(flow_text: str | None, flow_name: str) -> tuple[FlowStatus | None, str | None]:
    if flow_text is None:
        return None, None
    try:
        return parse_flow_status(json.loads(flow_text)), None
    except (json.JSONDecodeError, FlowStatusError) as exc:
        return None, f"{flow_name}: {exc}" if isinstance(exc, FlowStatusError) else f"{flow_name}: JSON 형식 오류"


def build_board(plan_text: str, results_text: str | None, label: str, *,
                plan_name: str = "작업 정의", results_name: str = "검증 결과",
                flow_text: str | None = None, flow_name: str = "흐름 상태",
                code: Callable[[TaskPlan], CodeState] | None = None,
                approval_text: str | None = None, approval_name: str = "승인 기록") -> TaskBoard:
    """``code`` reads the current code for a plan; without it there is no review."""

    try:
        plan_data = _json(plan_text, plan_name)
        plan = parse_plan(plan_data)
        results = parse_results(_json(results_text, results_name)) if results_text is not None else ()
    except PlanFormatError as exc:
        return TaskBoard(BoardState.UNREADABLE, label, error=str(exc))
    issues = validate(plan, results)
    if issues:
        return TaskBoard(BoardState.INVALID, label, issues=issues)
    review = None
    if code is not None:
        record, record_error = parse_approval_text(approval_text, approval_name)
        review = build_review(plan, plan_digest(plan_data), results, code(plan), record, record_error)
        progress = review.progress
    else:
        progress = evaluate(plan, results)
    flow, flow_error = _flow(flow_text, flow_name)
    return TaskBoard(BoardState.OK, label, progress=progress, view=select(plan, progress),
                     flow=flow, flow_error=flow_error, review=review)


def _read_optional(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "\0"  # reported as unreadable by the parser


class FileTaskSource:
    """Plan and results files; with ``repo``, also the code review of that repository."""

    def __init__(self, plan_path: str | Path, results_path: str | Path | None = None,
                 repo: str | Path | None = None) -> None:
        self.plan_path = Path(plan_path)
        self.results_path = Path(results_path) if results_path is not None else None
        self.repo = Path(repo) if repo is not None else None

    @property
    def label(self) -> str:
        # File names only: the screen never shows a local directory.
        if self.results_path is None:
            return f"{self.plan_path.name} · 검증 결과 없음"
        return f"{self.plan_path.name} · {self.results_path.name}"

    def _read(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise PlanFormatError(path.name, "파일을 읽을 수 없음") from None

    def load(self) -> TaskBoard:
        try:
            plan_text = self._read(self.plan_path)
            results_text = self._read(self.results_path) if self.results_path else None
        except PlanFormatError as exc:
            return TaskBoard(BoardState.UNREADABLE, self.label, error=str(exc))
        flow_text, flow_name = None, "흐름 상태"
        approval_text, approval_name = None, "승인 기록"
        if self.results_path is not None:
            flow_path = flow_status_path(self.results_path)
            flow_name = flow_path.name
            flow_text = _read_optional(flow_path)
            record = approval_path(self.results_path)
            approval_name = record.name
            approval_text = _read_optional(record)
        code = None
        if self.repo is not None:
            repo, own = self.repo, tool_files(self.plan_path, self.results_path)
            code = lambda plan: read_code(repo, plan, own)  # noqa: E731
        return build_board(
            plan_text,
            results_text,
            self.label,
            plan_name=self.plan_path.name,
            results_name=self.results_path.name if self.results_path else "검증 결과",
            flow_text=flow_text,
            flow_name=flow_name,
            code=code,
            approval_text=approval_text,
            approval_name=approval_name,
        )


DEMO_PLAN = "demo_task_plan.json"
DEMO_CODE = "demo_code.json"


@dataclass(frozen=True)
class DemoVariant:
    results: str | None  # results fixture
    flow: str | None  # flow status fixture
    approval: str | None  # decision fixture
    code: str  # key in demo_code.json: the repository state this scene shows
    label: str


DEMO_RESULTS: dict[str, DemoVariant] = {
    "passed": DemoVariant("demo_results_passed.json", None, None, "current", "데모 계획 · 선행 작업 검증 통과"),
    "failed": DemoVariant("demo_results_failed.json", "demo_flow_failed.json", None, "current",
                          "데모 계획 · 선행 작업 검증 실패"),
    "none": DemoVariant(None, None, None, "current", "데모 계획 · 검증 결과 없음"),
    "approved": DemoVariant("demo_results_all.json", None, "demo_approval.json", "current", "데모 계획 · 사용자 승인됨"),
    "changed": DemoVariant("demo_results_all.json", None, "demo_approval.json", "changed",
                           "데모 계획 · 승인 후 코드 변경"),
    "protected": DemoVariant("demo_results_all.json", None, None, "protected", "데모 계획 · 보호 경로 변경"),
}


def _fixture_text(name: str) -> str:
    return resources.files("orca_orchestrator").joinpath("fixtures", name).read_text(encoding="utf-8")


def demo_code_state(key: str) -> CodeState:
    """A fixed repository state from package data; no file or Git access."""

    data = json.loads(_fixture_text(DEMO_CODE))[key]
    changes = data["changes"]
    return CodeState(
        SourceState(data["source_digest"], data["source_files"]),
        CodeBasis(data["head"], data["dirty"]),
        ChangeSet(changes["base"], changes["base_sha"],
                  tuple(FileChange(c["path"], ChangeStatus(c["status"])) for c in changes["files"]),
                  changes["digest"]),
    )


class DemoTaskSource:
    """Bundled demo plan with two dependent tasks; reads package data only."""

    def __init__(self, variant: str = "passed") -> None:
        if variant not in DEMO_RESULTS:
            raise ValueError(f"unknown demo results variant: {variant}")
        self.variant = variant

    def load(self) -> TaskBoard:
        v = DEMO_RESULTS[self.variant]
        state = demo_code_state(v.code)
        return build_board(_fixture_text(DEMO_PLAN), _fixture_text(v.results) if v.results else None, v.label,
                           plan_name=DEMO_PLAN, results_name=v.results or "검증 결과",
                           flow_text=_fixture_text(v.flow) if v.flow else None, flow_name=v.flow or "흐름 상태",
                           code=lambda plan: state,
                           approval_text=_fixture_text(v.approval) if v.approval else None,
                           approval_name=v.approval or "승인 기록")
