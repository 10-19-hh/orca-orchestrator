"""Adapter error types. Orca error codes and messages are kept verbatim."""

from __future__ import annotations


class OrcaAdapterError(Exception):
    category = "adapter_error"

    def __init__(
        self,
        message: str,
        *,
        argv: tuple[str, ...] = (),
        exit_code: int | None = None,
        code: str | None = None,
        orca_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.argv = argv
        self.exit_code = exit_code
        self.code = code
        self.orca_message = orca_message


class CliUnavailableError(OrcaAdapterError):
    """The Orca CLI could not be resolved or started."""

    category = "cli_unavailable"


class OutcomeUnknownError(OrcaAdapterError):
    """The command timed out. For a mutation, Orca may or may not have applied it.

    The adapter never reissues the command. Check with a read command
    (``run_current``) before deciding anything.
    """

    category = "outcome_unknown"

    def __init__(self, message: str, *, mutation: bool, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.mutation = mutation


class InvalidResponseError(OrcaAdapterError):
    """Output did not match the response shape observed in Step 0."""

    category = "invalid_response"


class OrcaCommandError(OrcaAdapterError):
    """Orca returned ``ok=false`` with an error code."""

    category = "orca_error"


class RuntimeUnavailableError(OrcaCommandError):
    """The CLI could not reach a running Orca Runtime (``runtime_unavailable``)."""

    category = "runtime_unavailable"


class CoordinatorBindingError(OrcaCommandError):
    """The caller is not bound to a live Orca terminal (COORDINATOR precondition)."""

    category = "coordinator_binding"


RUNTIME_UNAVAILABLE_CODES = frozenset({"runtime_unavailable"})
BINDING_ERROR_CODES = frozenset(
    {"no_active_sender_terminal", "terminal_handle_stale", "coordinator_binding_missing"}
)


def classify_orca_error(
    code: str,
    orca_message: str | None,
    *,
    argv: tuple[str, ...],
    exit_code: int | None,
) -> OrcaCommandError:
    if code in RUNTIME_UNAVAILABLE_CODES:
        cls: type[OrcaCommandError] = RuntimeUnavailableError
    elif code in BINDING_ERROR_CODES:
        cls = CoordinatorBindingError
    else:
        cls = OrcaCommandError
    return cls(
        f"{code}: {orca_message}" if orca_message else code,
        argv=argv,
        exit_code=exit_code,
        code=code,
        orca_message=orca_message,
    )
