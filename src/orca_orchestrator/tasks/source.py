"""Reads a task plan, verification results and flow status into a displayable board.

Sources only read files. A board is rebuilt on every refresh, so edits to the
files (including what a running flow writes) show up after pressing ``r``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Protocol

from .contract import PlanFormatError, PlanIssue, parse_plan, parse_results, validate
from .flow import FlowStatus, FlowStatusError, FlowView, flow_status_path, parse_flow_status, select
from .progress import PlanProgress, evaluate


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
                flow_text: str | None = None, flow_name: str = "흐름 상태") -> TaskBoard:
    try:
        plan = parse_plan(_json(plan_text, plan_name))
        results = parse_results(_json(results_text, results_name)) if results_text is not None else ()
    except PlanFormatError as exc:
        return TaskBoard(BoardState.UNREADABLE, label, error=str(exc))
    issues = validate(plan, results)
    if issues:
        return TaskBoard(BoardState.INVALID, label, issues=issues)
    progress = evaluate(plan, results)
    flow, flow_error = _flow(flow_text, flow_name)
    return TaskBoard(BoardState.OK, label, progress=progress, view=select(plan, progress),
                     flow=flow, flow_error=flow_error)


class FileTaskSource:
    def __init__(self, plan_path: str | Path, results_path: str | Path | None = None) -> None:
        self.plan_path = Path(plan_path)
        self.results_path = Path(results_path) if results_path is not None else None

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
        if self.results_path is not None:
            flow_path = flow_status_path(self.results_path)
            flow_name = flow_path.name
            if flow_path.exists():
                try:
                    flow_text = flow_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    flow_text = "\0"  # reported as unreadable below
        return build_board(
            plan_text,
            results_text,
            self.label,
            plan_name=self.plan_path.name,
            results_name=self.results_path.name if self.results_path else "검증 결과",
            flow_text=flow_text,
            flow_name=flow_name,
        )


DEMO_PLAN = "demo_task_plan.json"
# variant -> (results fixture or None, flow status fixture or None, label)
DEMO_RESULTS: dict[str, tuple[str | None, str | None, str]] = {
    "passed": ("demo_results_passed.json", None, "데모 계획 · 선행 작업 검증 통과"),
    "failed": ("demo_results_failed.json", "demo_flow_failed.json", "데모 계획 · 선행 작업 검증 실패"),
    "none": (None, None, "데모 계획 · 검증 결과 없음"),
}


def _fixture_text(name: str) -> str:
    return resources.files("orca_orchestrator").joinpath("fixtures", name).read_text(encoding="utf-8")


class DemoTaskSource:
    """Bundled demo plan with two dependent tasks; reads package data only."""

    def __init__(self, variant: str = "passed") -> None:
        if variant not in DEMO_RESULTS:
            raise ValueError(f"unknown demo results variant: {variant}")
        self.variant = variant

    def load(self) -> TaskBoard:
        results_name, flow_name, label = DEMO_RESULTS[self.variant]
        results_text = _fixture_text(results_name) if results_name else None
        flow_text = _fixture_text(flow_name) if flow_name else None
        return build_board(_fixture_text(DEMO_PLAN), results_text, label,
                           plan_name=DEMO_PLAN, results_name=results_name or "검증 결과",
                           flow_text=flow_text, flow_name=flow_name or "흐름 상태")
