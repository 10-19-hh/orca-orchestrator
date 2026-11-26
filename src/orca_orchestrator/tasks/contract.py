"""Explicit task contract (FR-01): requirements, tasks, dependencies and checks.

A plan is read from JSON that a person wrote on purpose. Nothing here parses PRD
prose or runs a command. Structural problems raise :class:`PlanFormatError`;
contract problems (duplicate IDs, unknown references, cycles) are returned by
:func:`validate` as :class:`PlanIssue` values so all of them can be shown.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

PLAN_SCHEMA = "orca-orchestrator/task-plan@1"
RESULTS_SCHEMA = "orca-orchestrator/verification-results@1"


class PlanFormatError(ValueError):
    """The JSON does not have the documented shape."""

    def __init__(self, where: str, problem: str) -> None:
        self.where = where
        self.problem = problem
        super().__init__(f"{where}: {problem}")


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    title: str
    acceptance: str


@dataclass(frozen=True)
class Check:
    """A verification condition.

    ``command`` is a description for people and is never executed. Local runs
    use the program-defined command registered for ``check_id``
    (see ``tasks.verification.SUPPORTED_CHECKS``).
    """

    check_id: str
    description: str
    command: str | None = None


@dataclass(frozen=True)
class Task:
    task_id: str
    title: str
    requirement_ids: tuple[str, ...]
    dependencies: tuple[str, ...]
    checks: tuple[Check, ...]


@dataclass(frozen=True)
class TaskPlan:
    requirements: tuple[Requirement, ...]
    tasks: tuple[Task, ...]


class Outcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True)
class VerificationResult:
    task_id: str
    check_id: str
    outcome: Outcome
    exit_code: int | None = None
    evidence: str | None = None


class IssueCode(str, Enum):
    DUPLICATE_REQUIREMENT_ID = "duplicate_requirement_id"
    DUPLICATE_TASK_ID = "duplicate_task_id"
    DUPLICATE_CHECK_ID = "duplicate_check_id"
    TASK_WITHOUT_REQUIREMENT = "task_without_requirement"
    UNKNOWN_REQUIREMENT = "unknown_requirement"
    TASK_WITHOUT_CHECKS = "task_without_checks"
    UNKNOWN_DEPENDENCY = "unknown_dependency"
    DEPENDENCY_CYCLE = "dependency_cycle"
    RESULT_UNKNOWN_TASK = "result_unknown_task"
    RESULT_UNKNOWN_CHECK = "result_unknown_check"
    DUPLICATE_RESULT = "duplicate_result"
    RESULT_EXIT_CODE_MISMATCH = "result_exit_code_mismatch"


@dataclass(frozen=True)
class PlanIssue:
    code: IssueCode
    subject: tuple[str, ...]


# --- parsing -----------------------------------------------------------------


def _object(value: Any, where: str, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict:
    if not isinstance(value, dict):
        raise PlanFormatError(where, "객체가 아님")
    for key in required:
        if key not in value:
            raise PlanFormatError(where, f"필수 필드 '{key}' 없음")
    for key in value:
        if key not in required and key not in optional:
            raise PlanFormatError(where, f"알 수 없는 필드 '{key}'")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanFormatError(where, "비어 있지 않은 문자열이 아님")
    if value != value.strip():
        raise PlanFormatError(where, "앞뒤 공백이 있음")
    return value


def _optional_text(value: Any, where: str) -> str | None:
    return None if value is None else _text(value, where)


def _id_list(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise PlanFormatError(where, "목록이 아님")
    items = tuple(_text(item, f"{where}[{i}]") for i, item in enumerate(value))
    seen: set[str] = set()
    for i, item in enumerate(items):
        if item in seen:
            raise PlanFormatError(f"{where}[{i}]", f"중복 항목 '{item}'")
        seen.add(item)
    return items


def _list(value: Any, where: str) -> list:
    if not isinstance(value, list):
        raise PlanFormatError(where, "목록이 아님")
    return value


def _schema(data: Any, expected: str, keys: tuple[str, ...]) -> dict:
    data = _object(data, "$", ("schema", *keys))
    if data["schema"] != expected:
        raise PlanFormatError("$.schema", f"'{expected}'가 아님")
    return data


def parse_plan(data: Any) -> TaskPlan:
    data = _schema(data, PLAN_SCHEMA, ("requirements", "tasks"))
    requirements = []
    for i, raw in enumerate(_list(data["requirements"], "requirements")):
        where = f"requirements[{i}]"
        raw = _object(raw, where, ("id", "title", "acceptance"))
        requirements.append(
            Requirement(
                _text(raw["id"], f"{where}.id"),
                _text(raw["title"], f"{where}.title"),
                _text(raw["acceptance"], f"{where}.acceptance"),
            )
        )
    tasks = []
    for i, raw in enumerate(_list(data["tasks"], "tasks")):
        where = f"tasks[{i}]"
        raw = _object(raw, where, ("id", "title", "requirements", "checks"), ("depends_on",))
        checks = []
        for j, check in enumerate(_list(raw["checks"], f"{where}.checks")):
            cw = f"{where}.checks[{j}]"
            check = _object(check, cw, ("id", "description"), ("command",))
            checks.append(
                Check(
                    _text(check["id"], f"{cw}.id"),
                    _text(check["description"], f"{cw}.description"),
                    _optional_text(check.get("command"), f"{cw}.command"),
                )
            )
        tasks.append(
            Task(
                _text(raw["id"], f"{where}.id"),
                _text(raw["title"], f"{where}.title"),
                _id_list(raw["requirements"], f"{where}.requirements"),
                _id_list(raw.get("depends_on", []), f"{where}.depends_on"),
                tuple(checks),
            )
        )
    return TaskPlan(tuple(requirements), tuple(tasks))


def parse_results(data: Any) -> tuple[VerificationResult, ...]:
    data = _schema(data, RESULTS_SCHEMA, ("results",))
    results = []
    for i, raw in enumerate(_list(data["results"], "results")):
        where = f"results[{i}]"
        raw = _object(raw, where, ("task", "check", "outcome"), ("exit_code", "evidence"))
        try:
            outcome = Outcome(raw["outcome"])
        except ValueError:
            raise PlanFormatError(f"{where}.outcome", "'passed' 또는 'failed'가 아님") from None
        exit_code = raw.get("exit_code")
        if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
            raise PlanFormatError(f"{where}.exit_code", "정수가 아님")
        results.append(
            VerificationResult(
                _text(raw["task"], f"{where}.task"),
                _text(raw["check"], f"{where}.check"),
                outcome,
                exit_code,
                _optional_text(raw.get("evidence"), f"{where}.evidence"),
            )
        )
    return tuple(results)


# --- validation --------------------------------------------------------------


def _duplicates(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    dups: list[str] = []
    for item in ids:
        if item in seen and item not in dups:
            dups.append(item)
        seen.add(item)
    return dups


def find_cycles(plan: TaskPlan) -> list[tuple[str, ...]]:
    """Each distinct dependency cycle once, as ``(a, b, ..., a)`` in plan order.

    Edges to undefined tasks are ignored (reported separately). For a duplicated
    task ID the first definition is used.
    """

    graph: dict[str, tuple[str, ...]] = {}
    for task in plan.tasks:
        graph.setdefault(task.task_id, task.dependencies)
    white, grey, black = 0, 1, 2
    color = dict.fromkeys(graph, white)
    cycles: list[tuple[str, ...]] = []
    seen: set[frozenset[str]] = set()

    def visit(node: str, path: list[str]) -> None:
        color[node] = grey
        path.append(node)
        for dep in graph[node]:
            if dep not in graph:
                continue
            if color[dep] == grey:
                cycle = tuple(path[path.index(dep):]) + (dep,)
                key = frozenset(cycle)
                if key not in seen:
                    seen.add(key)
                    cycles.append(cycle)
            elif color[dep] == white:
                visit(dep, path)
        path.pop()
        color[node] = black

    for node in graph:
        if color[node] == white:
            visit(node, [])
    return cycles


def validate(plan: TaskPlan, results: tuple[VerificationResult, ...] = ()) -> tuple[PlanIssue, ...]:
    issues: list[PlanIssue] = []
    add = lambda code, *subject: issues.append(PlanIssue(code, subject))  # noqa: E731

    for rid in _duplicates([r.requirement_id for r in plan.requirements]):
        add(IssueCode.DUPLICATE_REQUIREMENT_ID, rid)
    for tid in _duplicates([t.task_id for t in plan.tasks]):
        add(IssueCode.DUPLICATE_TASK_ID, tid)

    requirement_ids = {r.requirement_id for r in plan.requirements}
    task_ids = {t.task_id for t in plan.tasks}
    for task in plan.tasks:
        for cid in _duplicates([c.check_id for c in task.checks]):
            add(IssueCode.DUPLICATE_CHECK_ID, task.task_id, cid)
        if not task.requirement_ids:
            add(IssueCode.TASK_WITHOUT_REQUIREMENT, task.task_id)
        for rid in task.requirement_ids:
            if rid not in requirement_ids:
                add(IssueCode.UNKNOWN_REQUIREMENT, task.task_id, rid)
        if not task.checks:
            add(IssueCode.TASK_WITHOUT_CHECKS, task.task_id)
        for dep in task.dependencies:
            if dep not in task_ids:
                add(IssueCode.UNKNOWN_DEPENDENCY, task.task_id, dep)
    for cycle in find_cycles(plan):
        add(IssueCode.DEPENDENCY_CYCLE, *cycle)

    checks_by_task: dict[str, set[str]] = {}
    for task in plan.tasks:
        checks_by_task.setdefault(task.task_id, {c.check_id for c in task.checks})
    seen_results: set[tuple[str, str]] = set()
    for result in results:
        key = (result.task_id, result.check_id)
        if result.task_id not in checks_by_task:
            add(IssueCode.RESULT_UNKNOWN_TASK, result.task_id)
        elif result.check_id not in checks_by_task[result.task_id]:
            add(IssueCode.RESULT_UNKNOWN_CHECK, *key)
        if key in seen_results:
            add(IssueCode.DUPLICATE_RESULT, *key)
        seen_results.add(key)
        if result.outcome is Outcome.PASSED and result.exit_code not in (None, 0):
            add(IssueCode.RESULT_EXIT_CODE_MISMATCH, *key, str(result.exit_code))
    return tuple(issues)
