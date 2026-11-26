"""Judges each task from recorded verification results (FR-06 input, FR-07 trace).

Only results that were supplied are used. A task is complete when every one of
its checks has a ``passed`` result; any ``failed`` result means it is not
complete. A task is ready only when every direct prerequisite is complete;
otherwise it waits and the reasons name each unfinished prerequisite.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .contract import Outcome, Requirement, Task, TaskPlan, VerificationResult, validate


class TaskState(str, Enum):
    VERIFIED = "verified"  # every check passed
    FAILED = "failed"  # at least one check failed: not complete
    READY = "ready"  # prerequisites complete, own checks not all passed yet
    WAITING = "waiting"  # a prerequisite is not complete


class WaitKind(str, Enum):
    PREREQUISITE_FAILED = "prerequisite_failed"
    PREREQUISITE_UNVERIFIED = "prerequisite_unverified"


@dataclass(frozen=True)
class WaitReason:
    kind: WaitKind
    task_id: str
    passed: int
    total: int


@dataclass(frozen=True)
class TaskProgress:
    task: Task
    state: TaskState
    passed: int
    total: int
    failed_checks: tuple[str, ...] = ()
    waiting_on: tuple[WaitReason, ...] = ()


@dataclass(frozen=True)
class RequirementProgress:
    requirement: Requirement
    task_ids: tuple[str, ...]
    open_task_ids: tuple[str, ...]

    @property
    def satisfied(self) -> bool:
        return bool(self.task_ids) and not self.open_task_ids


@dataclass(frozen=True)
class PlanProgress:
    tasks: tuple[TaskProgress, ...]
    requirements: tuple[RequirementProgress, ...]

    def count(self, state: TaskState) -> int:
        return sum(1 for t in self.tasks if t.state is state)

    def task(self, task_id: str) -> TaskProgress:
        return next(t for t in self.tasks if t.task.task_id == task_id)


class InvalidPlanError(ValueError):
    """evaluate() refuses a plan that has contract issues."""


def evaluate(plan: TaskPlan, results: tuple[VerificationResult, ...] = ()) -> PlanProgress:
    if validate(plan, results):
        raise InvalidPlanError("plan has contract issues; call validate() first")

    outcomes = {(r.task_id, r.check_id): r.outcome for r in results}
    own: dict[str, tuple[int, int, tuple[str, ...]]] = {}
    for task in plan.tasks:
        passed = sum(1 for c in task.checks if outcomes.get((task.task_id, c.check_id)) is Outcome.PASSED)
        failed = tuple(
            c.check_id for c in task.checks if outcomes.get((task.task_id, c.check_id)) is Outcome.FAILED
        )
        own[task.task_id] = (passed, len(task.checks), failed)

    def complete(task_id: str) -> bool:
        passed, total, _ = own[task_id]
        return passed == total

    progress: list[TaskProgress] = []
    for task in plan.tasks:
        passed, total, failed = own[task.task_id]
        if failed:
            state = TaskState.FAILED
        elif complete(task.task_id):
            state = TaskState.VERIFIED
        else:
            state = None
        reasons = []
        for dep in task.dependencies:
            if complete(dep):
                continue
            dep_passed, dep_total, dep_failed = own[dep]
            kind = WaitKind.PREREQUISITE_FAILED if dep_failed else WaitKind.PREREQUISITE_UNVERIFIED
            reasons.append(WaitReason(kind, dep, dep_passed, dep_total))
        if state is None:
            state = TaskState.WAITING if reasons else TaskState.READY
        progress.append(
            TaskProgress(
                task,
                state,
                passed,
                total,
                failed,
                tuple(reasons) if state is TaskState.WAITING else (),
            )
        )

    by_id = {p.task.task_id: p for p in progress}
    requirements = []
    for req in plan.requirements:
        linked = tuple(t.task_id for t in plan.tasks if req.requirement_id in t.requirement_ids)
        open_ids = tuple(tid for tid in linked if by_id[tid].state is not TaskState.VERIFIED)
        requirements.append(RequirementProgress(req, linked, open_ids))
    return PlanProgress(tuple(progress), tuple(requirements))
