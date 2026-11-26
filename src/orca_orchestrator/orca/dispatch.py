"""Gate for sending real work to a Worker. Blocked by default.

This build never sends work. ``WorkDispatcher.dispatch`` only evaluates the
gate (using the read-only ``status --json``) and returns the reasons it is
blocked. The worker-start contract has not been verified on the installed
Orca, so that reason cannot be cleared by configuration.

When a provider reaches its usage limit the dispatcher enters
``WAITING_FOR_RESET`` and waits for the reset time. It never switches to API-key
billing, never buys or spends extra credits and never retries on its own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping

from .adapter import OrcaCliAdapter, RuntimeStatus
from .errors import OrcaAdapterError, RuntimeUnavailableError


class Provider(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"


class AuthMode(str, Enum):
    SUBSCRIPTION = "subscription"
    API_KEY = "api_key"


# Names only; values are never read beyond "set or not".
AUTH_ENV_NAMES: dict[Provider, tuple[str, ...]] = {
    Provider.CLAUDE: (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "ANTHROPIC_BASE_URL",
    ),
    Provider.CODEX: ("OPENAI_API_KEY", "CODEX_API_KEY"),
}

# Actions this component must never take in response to a limit or failure.
PROHIBITED_FALLBACKS = ("switch_to_api_key", "purchase_or_spend_extra_credits", "automatic_retry")


def observe_auth_environment(
    provider: Provider, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    env = os.environ if environ is None else environ
    return {name: ("SET" if env.get(name) else "UNSET") for name in AUTH_ENV_NAMES[provider]}


class Outcome(str, Enum):
    ALLOWED = "ALLOWED"
    BLOCKED = "BLOCKED"
    WAITING_FOR_RESET = "WAITING_FOR_RESET"


class Reason(str, Enum):
    REAL_DISPATCH_DISABLED = "real_dispatch_disabled"
    EXECUTION_APPROVAL_MISSING = "execution_approval_missing"
    EXPERIMENT_4A_NOT_PASSED = "experiment_4a_not_passed"
    SUBSCRIPTION_AUTH_UNVERIFIED = "subscription_auth_unverified"
    BILLING_PATH_UNVERIFIED = "billing_path_unverified"
    EXTRA_USAGE_NOT_CONFIRMED_OFF = "extra_usage_not_confirmed_off"
    AUTO_RECHARGE_NOT_CONFIRMED_OFF = "auto_recharge_not_confirmed_off"
    API_KEY_BILLING_FORBIDDEN = "api_key_billing_forbidden"
    API_KEY_ENVIRONMENT_PRESENT = "api_key_environment_present"
    AUTOMATIC_RETRY_FORBIDDEN = "automatic_retry_forbidden"
    COORDINATOR_BINDING_MISSING = "coordinator_binding_missing"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    RUNTIME_STATUS_INVALID = "runtime_status_invalid"
    ORCA_VERSION_UNPINNED = "orca_version_unpinned"
    USAGE_LIMIT_WAITING_FOR_RESET = "usage_limit_waiting_for_reset"
    USAGE_RECHECK_REQUIRED = "usage_recheck_required"
    WORKER_DISPATCH_CONTRACT_UNVERIFIED = "worker_dispatch_contract_unverified"


@dataclass(frozen=True)
class BlockReason:
    code: Reason
    detail: str


@dataclass(frozen=True)
class ExecutionApproval:
    """A person's explicit approval for real Worker execution in one stage."""

    approved_by: str
    approved_at: datetime
    scope: str


@dataclass(frozen=True)
class ProviderBillingConfirmation:
    """Each flag must be confirmed by the user or official evidence; all default to False."""

    subscription_auth_verified: bool = False
    billing_path_verified: bool = False
    extra_usage_disabled: bool = False
    auto_recharge_disabled: bool = False
    api_key_environment_cleared: bool = False
    evidence_ref: str | None = None


@dataclass(frozen=True)
class DispatchPolicy:
    real_dispatch_enabled: bool = False
    execution_approval: ExecutionApproval | None = None
    experiment_4a_status: str = "CONDITIONAL"
    providers: Mapping[Provider, ProviderBillingConfirmation] = field(default_factory=dict)


@dataclass(frozen=True)
class DispatchRequest:
    provider: Provider
    task_id: str
    auth_mode: AuthMode = AuthMode.SUBSCRIPTION
    retry_of: str | None = None


@dataclass(frozen=True)
class DispatchDecision:
    outcome: Outcome
    reasons: tuple[BlockReason, ...]
    resume_after: datetime | None = None
    prohibited_fallbacks: tuple[str, ...] = PROHIBITED_FALLBACKS

    @property
    def reason_codes(self) -> tuple[Reason, ...]:
        return tuple(r.code for r in self.reasons)


class LimitPhase(str, Enum):
    AVAILABLE = "AVAILABLE"
    WAITING_FOR_RESET = "WAITING_FOR_RESET"
    RECHECK_REQUIRED = "RECHECK_REQUIRED"


@dataclass(frozen=True)
class LimitHold:
    provider: Provider
    reset_at: datetime | None
    observed_at: datetime
    source: str


class UsageLimitTracker:
    """Holds providers that hit their limit until a fresh observation after reset.

    UNKNOWN usage is not treated as 0%. Passing the reset time alone does not
    release the hold; it moves to RECHECK_REQUIRED and needs a new observation.
    """

    def __init__(self) -> None:
        self._holds: dict[Provider, LimitHold] = {}

    def record_limit_reached(
        self,
        provider: Provider,
        *,
        observed_at: datetime,
        reset_at: datetime | None,
        source: str,
    ) -> LimitHold:
        hold = LimitHold(provider, _aware(reset_at), _aware(observed_at), source)
        self._holds[provider] = hold
        return hold

    def phase(self, provider: Provider, now: datetime) -> tuple[LimitPhase, LimitHold | None]:
        hold = self._holds.get(provider)
        if hold is None:
            return LimitPhase.AVAILABLE, None
        if hold.reset_at is None or _aware(now) < hold.reset_at:
            return LimitPhase.WAITING_FOR_RESET, hold
        return LimitPhase.RECHECK_REQUIRED, hold

    def release_after_recheck(
        self, provider: Provider, *, observed_at: datetime, limit_reached: bool
    ) -> bool:
        """Release only with an observation taken after the reset time showing capacity."""

        hold = self._holds.get(provider)
        if hold is None:
            return True
        observed = _aware(observed_at)
        if limit_reached or hold.reset_at is None or observed < hold.reset_at:
            return False
        del self._holds[provider]
        return True


class DispatchGuard:
    def __init__(self, policy: DispatchPolicy | None = None, limits: UsageLimitTracker | None = None):
        self.policy = policy or DispatchPolicy()
        self.limits = limits or UsageLimitTracker()

    def evaluate(
        self,
        request: DispatchRequest,
        *,
        runtime: RuntimeStatus | None,
        runtime_error: OrcaAdapterError | None = None,
        binding_present: bool,
        environ: Mapping[str, str] | None = None,
        now: datetime | None = None,
    ) -> DispatchDecision:
        now = _aware(now or datetime.now(timezone.utc))
        policy = self.policy
        reasons: list[BlockReason] = []

        def block(code: Reason, detail: str) -> None:
            reasons.append(BlockReason(code, detail))

        if not policy.real_dispatch_enabled:
            block(Reason.REAL_DISPATCH_DISABLED, "Real work dispatch is off by default.")
        if policy.execution_approval is None:
            block(Reason.EXECUTION_APPROVAL_MISSING, "No explicit user approval for Worker execution.")
        if policy.experiment_4a_status != "PASSED":
            block(
                Reason.EXPERIMENT_4A_NOT_PASSED,
                f"Experiment 4-A is {policy.experiment_4a_status}; Worker execution requires PASSED.",
            )

        conf = policy.providers.get(request.provider, ProviderBillingConfirmation())
        name = request.provider.value
        if not conf.subscription_auth_verified:
            block(Reason.SUBSCRIPTION_AUTH_UNVERIFIED, f"{name}: Worker subscription auth not verified.")
        if not conf.billing_path_verified:
            block(Reason.BILLING_PATH_UNVERIFIED, f"{name}: billing path not verified as subscription-only.")
        if not conf.extra_usage_disabled:
            block(Reason.EXTRA_USAGE_NOT_CONFIRMED_OFF, f"{name}: extra usage / credit use not confirmed off.")
        if not conf.auto_recharge_disabled:
            block(Reason.AUTO_RECHARGE_NOT_CONFIRMED_OFF, f"{name}: auto-recharge not confirmed off.")

        if request.auth_mode is not AuthMode.SUBSCRIPTION:
            block(Reason.API_KEY_BILLING_FORBIDDEN, "Only included subscription usage is allowed.")
        present = [n for n, v in observe_auth_environment(request.provider, environ).items() if v == "SET"]
        if present and not conf.api_key_environment_cleared:
            block(
                Reason.API_KEY_ENVIRONMENT_PRESENT,
                f"{name}: alternate auth variables set in this process: {', '.join(present)}.",
            )
        if request.retry_of is not None:
            block(Reason.AUTOMATIC_RETRY_FORBIDDEN, f"Retry of {request.retry_of} is not sent automatically.")

        if not binding_present:
            block(Reason.COORDINATOR_BINDING_MISSING, "Not running inside a bound Orca terminal.")
        if runtime_error is not None:
            code = (
                Reason.RUNTIME_UNAVAILABLE
                if isinstance(runtime_error, RuntimeUnavailableError)
                or runtime_error.category in ("cli_unavailable", "outcome_unknown")
                else Reason.RUNTIME_STATUS_INVALID
            )
            block(code, f"{runtime_error.category}: {runtime_error}")
        elif runtime is None or not runtime.connected:
            block(Reason.RUNTIME_UNAVAILABLE, "Orca Runtime is not ready and connected.")
        elif not runtime.version_pinned:
            block(Reason.ORCA_VERSION_UNPINNED, f"Orca {runtime.app_version} is not a verified version.")

        phase, hold = self.limits.phase(request.provider, now)
        resume_after = None
        if phase is LimitPhase.WAITING_FOR_RESET:
            resume_after = hold.reset_at if hold else None
            block(
                Reason.USAGE_LIMIT_WAITING_FOR_RESET,
                f"{name}: usage limit reached; waiting for reset at "
                f"{resume_after.isoformat() if resume_after else 'UNKNOWN (re-observe manually)'}.",
            )
        elif phase is LimitPhase.RECHECK_REQUIRED:
            block(Reason.USAGE_RECHECK_REQUIRED, f"{name}: reset time passed; a fresh usage observation is required.")

        block(
            Reason.WORKER_DISPATCH_CONTRACT_UNVERIFIED,
            "worker-start is not verified on the installed Orca; this build does not send work.",
        )

        outcome = Outcome.WAITING_FOR_RESET if phase is LimitPhase.WAITING_FOR_RESET else Outcome.BLOCKED
        return DispatchDecision(outcome, tuple(reasons), resume_after)


class WorkDispatcher:
    """Evaluates the gate. Only ``status --json`` (read-only) is ever invoked."""

    def __init__(self, adapter: OrcaCliAdapter, guard: DispatchGuard | None = None,
                 environ: Mapping[str, str] | None = None) -> None:
        self.adapter = adapter
        self.guard = guard or DispatchGuard()
        self._environ = environ

    def dispatch(self, request: DispatchRequest, *, now: datetime | None = None) -> DispatchDecision:
        runtime: RuntimeStatus | None = None
        runtime_error: OrcaAdapterError | None = None
        try:
            runtime = self.adapter.status()
        except OrcaAdapterError as exc:
            runtime_error = exc
        decision = self.guard.evaluate(
            request,
            runtime=runtime,
            runtime_error=runtime_error,
            binding_present=self.adapter.binding.terminal_handle_present,
            environ=self._environ if self._environ is not None else self.adapter.environ,
            now=now,
        )
        if decision.outcome is Outcome.ALLOWED:  # unreachable in this build
            raise RuntimeError("real Worker dispatch is not implemented")
        return decision


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
