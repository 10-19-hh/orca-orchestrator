"""Reads a task plan and verification results into a displayable board.

Sources only read files. A board is rebuilt on every refresh, so edits to the
files show up after pressing ``r``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Protocol

from .contract import PlanFormatError, PlanIssue, parse_plan, parse_results, validate
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


NOT_CONFIGURED = TaskBoard(BoardState.NOT_CONFIGURED)


class TaskSource(Protocol):
    def load(self) -> TaskBoard: ...


def _json(text: str, name: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanFormatError(name, f"JSON 형식 오류 ({exc.lineno}행)") from None


def build_board(plan_text: str, results_text: str | None, label: str, *,
                plan_name: str = "작업 정의", results_name: str = "검증 결과") -> TaskBoard:
    try:
        plan = parse_plan(_json(plan_text, plan_name))
        results = parse_results(_json(results_text, results_name)) if results_text is not None else ()
    except PlanFormatError as exc:
        return TaskBoard(BoardState.UNREADABLE, label, error=str(exc))
    issues = validate(plan, results)
    if issues:
        return TaskBoard(BoardState.INVALID, label, issues=issues)
    return TaskBoard(BoardState.OK, label, progress=evaluate(plan, results))


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
        return build_board(
            plan_text,
            results_text,
            self.label,
            plan_name=self.plan_path.name,
            results_name=self.results_path.name if self.results_path else "검증 결과",
        )


DEMO_PLAN = "demo_task_plan.json"
# variant -> (results fixture or None, label)
DEMO_RESULTS: dict[str, tuple[str | None, str]] = {
    "passed": ("demo_results_passed.json", "데모 계획 · 선행 작업 검증 통과"),
    "failed": ("demo_results_failed.json", "데모 계획 · 선행 작업 검증 실패"),
    "none": (None, "데모 계획 · 검증 결과 없음"),
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
        results_name, label = DEMO_RESULTS[self.variant]
        results_text = _fixture_text(results_name) if results_name else None
        return build_board(_fixture_text(DEMO_PLAN), results_text, label,
                           plan_name=DEMO_PLAN, results_name=results_name or "검증 결과")
