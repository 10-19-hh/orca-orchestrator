"""Orca CLI adapter (COORDINATOR topology), Worker command contract and the real-dispatch gate."""

from .adapter import (
    PINNED_ORCA_VERSIONS,
    BindingContext,
    OrcaCliAdapter,
    RunRef,
    RuntimeStatus,
    resolve_cli,
)
from .dispatch import (
    PROHIBITED_FALLBACKS,
    AuthMode,
    DispatchDecision,
    DispatchGuard,
    DispatchPolicy,
    DispatchRequest,
    ExecutionApproval,
    LimitPhase,
    Outcome,
    Provider,
    ProviderBillingConfirmation,
    Reason,
    UsageLimitTracker,
    WorkDispatcher,
    observe_auth_environment,
)
from .errors import (
    CliUnavailableError,
    CoordinatorBindingError,
    InvalidResponseError,
    OrcaAdapterError,
    OrcaCommandError,
    OutcomeUnknownError,
    RuntimeUnavailableError,
)
from .runner import CommandResult, CommandRunner, SubprocessRunner
from .workers import (
    WORKER_CONTRACT,
    StartDisposition,
    WorkerObservation,
    WorkerPhase,
    WorkerStartReceipt,
    WorkerStartRequest,
    is_dispatch_id,
    parse_worker_start,
    worker_show_args,
    worker_start_args,
)
