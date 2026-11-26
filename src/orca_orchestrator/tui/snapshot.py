"""Collects one read-only view of Orca and the dispatch gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..orca.adapter import OrcaCliAdapter, RunRef, RuntimeStatus
from ..orca.dispatch import (
    DispatchDecision,
    DispatchGuard,
    DispatchRequest,
    Provider,
    observe_auth_environment,
)
from ..orca.errors import (
    CliUnavailableError,
    CoordinatorBindingError,
    OrcaAdapterError,
    OrcaCommandError,
    OutcomeUnknownError,
    RuntimeUnavailableError,
)
from ..tasks import NOT_CONFIGURED, TaskBoard, TaskSource

# Runtime health
CONNECTED = "connected"
DISCONNECTED = "disconnected"
CLI_MISSING = "cli_missing"
TIMED_OUT = "timed_out"
UNREADABLE = "unreadable"

# Current-run state
RUN_OK = "ok"
RUN_NONE = "none"
RUN_SKIPPED_UNBOUND = "skipped_unbound"
RUN_SKIPPED_RUNTIME = "skipped_runtime"
RUN_BINDING_INVALID = "binding_invalid"
RUN_TIMED_OUT = "timed_out"
RUN_UNREADABLE = "unreadable"


@dataclass(frozen=True)
class RuntimeView:
    health: str
    status: RuntimeStatus | None = None
    error: OrcaAdapterError | None = None


@dataclass(frozen=True)
class RunView:
    state: str
    run: RunRef | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class Snapshot:
    taken_at: datetime
    demo: str | None
    runtime: RuntimeView
    bound: bool
    run: RunView
    experiment_4a_status: str
    decisions: dict[Provider, DispatchDecision] = field(default_factory=dict)
    auth_env_present: dict[Provider, tuple[str, ...]] = field(default_factory=dict)
    tasks: TaskBoard = NOT_CONFIGURED


def _runtime_view(adapter: OrcaCliAdapter) -> RuntimeView:
    try:
        status = adapter.status()
    except CliUnavailableError as exc:
        return RuntimeView(CLI_MISSING, error=exc)
    except RuntimeUnavailableError as exc:
        return RuntimeView(DISCONNECTED, error=exc)
    except OutcomeUnknownError as exc:
        return RuntimeView(TIMED_OUT, error=exc)
    except OrcaAdapterError as exc:  # invalid response, other Orca error
        return RuntimeView(UNREADABLE, error=exc)
    return RuntimeView(CONNECTED if status.connected else DISCONNECTED, status=status)


def _run_view(adapter: OrcaCliAdapter, runtime: RuntimeView, bound: bool) -> RunView:
    if not bound:
        return RunView(RUN_SKIPPED_UNBOUND)
    if runtime.health != CONNECTED:
        return RunView(RUN_SKIPPED_RUNTIME)
    try:
        run = adapter.run_current()
    except CoordinatorBindingError as exc:
        return RunView(RUN_BINDING_INVALID, error_code=exc.code)
    except OutcomeUnknownError:
        return RunView(RUN_TIMED_OUT)
    except OrcaCommandError as exc:
        return RunView(RUN_UNREADABLE, error_code=exc.code)
    except OrcaAdapterError:
        return RunView(RUN_UNREADABLE)
    return RunView(RUN_NONE) if run is None else RunView(RUN_OK, run=run)


def collect_snapshot(
    adapter: OrcaCliAdapter,
    guard: DispatchGuard,
    *,
    demo: str | None = None,
    now: datetime | None = None,
    tasks: TaskSource | None = None,
) -> Snapshot:
    """Runs at most ``status --json`` and, when bound and connected, ``run-current``.

    ``tasks`` only reads local files (or bundled demo data); it runs nothing.
    """

    now = now or datetime.now(timezone.utc)
    runtime = _runtime_view(adapter)
    bound = adapter.binding.terminal_handle_present
    run = _run_view(adapter, runtime, bound)

    decisions: dict[Provider, DispatchDecision] = {}
    auth_env: dict[Provider, tuple[str, ...]] = {}
    for provider in Provider:
        decisions[provider] = guard.evaluate(
            DispatchRequest(provider, task_id="status-screen"),
            runtime=runtime.status,
            runtime_error=runtime.error,
            binding_present=bound,
            environ=adapter.environ,
            now=now,
        )
        auth_env[provider] = tuple(
            name
            for name, state in observe_auth_environment(provider, adapter.environ).items()
            if state == "SET"
        )
    return Snapshot(
        taken_at=now,
        demo=demo,
        runtime=runtime,
        bound=bound,
        run=run,
        experiment_4a_status=guard.policy.experiment_4a_status,
        decisions=decisions,
        auth_env_present=auth_env,
        tasks=tasks.load() if tasks is not None else NOT_CONFIGURED,
    )
