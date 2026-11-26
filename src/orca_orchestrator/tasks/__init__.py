"""Task contract, dependency checks and progress judgment. Read-only; runs nothing."""

from .contract import (
    PLAN_SCHEMA,
    RESULTS_SCHEMA,
    Check,
    IssueCode,
    Outcome,
    PlanFormatError,
    PlanIssue,
    Requirement,
    Task,
    TaskPlan,
    VerificationResult,
    find_cycles,
    parse_plan,
    parse_results,
    validate,
)
from .progress import (
    InvalidPlanError,
    PlanProgress,
    RequirementProgress,
    TaskProgress,
    TaskState,
    WaitKind,
    WaitReason,
    evaluate,
)
from .source import (
    DEMO_RESULTS,
    NOT_CONFIGURED,
    BoardState,
    DemoTaskSource,
    FileTaskSource,
    TaskBoard,
    TaskSource,
    build_board,
)

__all__ = [
    "PLAN_SCHEMA", "RESULTS_SCHEMA", "Check", "IssueCode", "Outcome", "PlanFormatError",
    "PlanIssue", "Requirement", "Task", "TaskPlan", "VerificationResult", "find_cycles",
    "parse_plan", "parse_results", "validate", "InvalidPlanError", "PlanProgress",
    "RequirementProgress", "TaskProgress", "TaskState", "WaitKind", "WaitReason", "evaluate",
    "DEMO_RESULTS", "NOT_CONFIGURED", "BoardState", "DemoTaskSource", "FileTaskSource",
    "TaskBoard", "TaskSource", "build_board",
]
