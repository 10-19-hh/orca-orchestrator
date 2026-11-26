"""Judges each task from recorded verification results (FR-06 input, FR-07 trace).

Only results that were supplied are used, and only when they still apply: a
result counts when its ``check_digest`` matches the current check definition.
A result without a digest, or with a different one, is *stale* and the check
needs re-checking. A task is complete when every check has a current
``passed`` result; any current ``failed`` result means it is not complete. A
task is ready only when every direct prerequisite is complete; otherwise it
waits and the reasons name each unfinished prerequisite.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .contract import (
    Check,
    Outcome,
    Requirement,
    Task,
    TaskPlan,
    VerificationResult,
    check_digest,
    validate,
)


class CheckState(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    MISSING = "missing"  # no result recorded
    STALE = "stale"  # a result exists but may not apply to the current definition


class StaleReason(str, Enum):
    NO_DIGEST = "no_digest"  # recorded without a check digest: applicability unknown
    CHANGED = "changed"  # the check definition changed after the result was recorded


@dataclass(frozen=True)
class CheckProgress:
    check: Check
    state: CheckState
    result: VerificationResult | None = None
    stale_reason: StaleReason | None = None


class TaskState(str, Enum):
    VERIFIED = "verified"  # every check passed (current results)
    FAILED = "failed"  # at least one check failed (current result): not complete
    RECHECK = "recheck"  # prerequisites complete; a result needs re-checking
    READY = "ready"  # prerequisites complete, own checks not all passed yet
    WAITING = "waiting"  # a prerequisite is not complete


class WaitKind(str, Enum):
    PREREQUISITE_FAILED = "prerequisite_failed"
    PREREQUISITE_RECHECK = "prerequisite_recheck"
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
    checks: tuple[CheckProgress, ...] = ()

    @property
    def stale_checks(self) -> tuple[CheckProgress, ...]:
        return tuple(c for c in self.checks if c.state is CheckState.STALE)


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

    @property
    def all_verified(self) -> bool:
        return bool(self.tasks) and all(t.state is TaskState.VERIFIED for t in self.tasks)


class InvalidPlanError(ValueError):
    """evaluate() refuses a plan that has contract issues."""


def _check_progress(task: Task, check: Check, result: VerificationResult | None) -> CheckProgress:
    if result is None:
        return CheckProgress(check, CheckState.MISSING)
    if result.check_digest is None:
        return CheckProgress(check, CheckState.STALE, result, StaleReason.NO_DIGEST)
    if result.check_digest != check_digest(task, check):
        return CheckProgress(check, CheckState.STALE, result, StaleReason.CHANGED)
    state = CheckState.PASSED if result.outcome is Outcome.PASSED else CheckState.FAILED
    return CheckProgress(check, state, result)


def evaluate(plan: TaskPlan, results: tuple[VerificationResult, ...] = ()) -> PlanProgress:
    if validate(plan, results):
        raise InvalidPlanError("plan has contract issues; call validate() first")

    by_key = {(r.task_id, r.check_id): r for r in results}
    checks: dict[str, tuple[CheckProgress, ...]] = {
        task.task_id: tuple(_check_progress(task, c, by_key.get((task.task_id, c.check_id))) for c in task.checks)
        for task in plan.tasks
    }

    def count(task_id: str, state: CheckState) -> int:
        return sum(1 for c in checks[task_id] if c.state is state)

    def complete(task_id: str) -> bool:
        return count(task_id, CheckState.PASSED) == len(checks[task_id])

    progress: list[TaskProgress] = []
    for task in plan.tasks:
        own = checks[task.task_id]
        passed = count(task.task_id, CheckState.PASSED)
        failed = tuple(c.check.check_id for c in own if c.state is CheckState.FAILED)
        reasons = []
        for dep in task.dependencies:
            if complete(dep):
                continue
            if count(dep, CheckState.FAILED):
                kind = WaitKind.PREREQUISITE_FAILED
            elif count(dep, CheckState.STALE):
                kind = WaitKind.PREREQUISITE_RECHECK
            else:
                kind = WaitKind.PREREQUISITE_UNVERIFIED
            reasons.append(WaitReason(kind, dep, count(dep, CheckState.PASSED), len(checks[dep])))
        if failed:
            state = TaskState.FAILED
        elif complete(task.task_id):
            state = TaskState.VERIFIED
        elif reasons:
            state = TaskState.WAITING
        elif count(task.task_id, CheckState.STALE):
            state = TaskState.RECHECK
        else:
            state = TaskState.READY
        progress.append(
            TaskProgress(
                task,
                state,
                passed,
                len(own),
                failed,
                tuple(reasons) if state is TaskState.WAITING else (),
                own,
            )
        )

    by_id = {p.task.task_id: p for p in progress}
    requirements = []
    for req in plan.requirements:
        linked = tuple(t.task_id for t in plan.tasks if req.requirement_id in t.requirement_ids)
        open_ids = tuple(tid for tid in linked if by_id[tid].state is not TaskState.VERIFIED)
        requirements.append(RequirementProgress(req, linked, open_ids))
    return PlanProgress(tuple(progress), tuple(requirements))
