"""Orca CLI adapter for the COORDINATOR topology.

Only operations verified in Step 0 Experiment 1 on Orca 1.4.264 are wired:

* ``status --json``                         (read, no binding needed)
* ``orchestration run-current --json``      (read, Coordinator binding)
* ``orchestration run-create --objective <text> --json``
                                            (creates a Run namespace only; never places Workers)
* ``orchestration worker-show --dispatch <id> --json``
                                            (read; response shape from the v1.4.264 source, not yet observed)

``worker-start`` is not wired: its argv is only built (``workers.worker_start_args``).

Experiment 1 showed that an external process without an inherited
``ORCA_TERMINAL_HANDLE`` is rejected with ``no_active_sender_terminal``. The
adapter therefore never passes ``--from`` and never invents a terminal handle.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import (
    CliUnavailableError,
    CoordinatorBindingError,
    InvalidResponseError,
    OutcomeUnknownError,
    classify_orca_error,
)
from .runner import CommandLaunchError, CommandRunner, CommandTimeout, SubprocessRunner
from .workers import WorkerObservation, worker_observation, worker_show_args

# Version observed for CLI, desktop app and Runtime in Experiment 1.
PINNED_ORCA_VERSIONS = frozenset({"1.4.264"})

CLI_OVERRIDE_ENV = "ORCA_CLI_COMMAND"
# Folder of the bundled CLI that Orca puts into its own terminals (observed on macOS with Orca 1.4.268).
CLI_BIN_DIR_ENV = "ORCA_CLI_BIN_DIR"
TERMINAL_HANDLE_ENV = "ORCA_TERMINAL_HANDLE"

# Observed form: "run_" followed by 12 lowercase hex characters, e.g. run_0123456789ab
RUN_ID_PATTERN = re.compile(r"^run_[A-Za-z0-9]+$")
_STDERR_ERROR_PATTERN = re.compile(r"^error:\s*([a-z0-9_]+):\s*(.*)$", re.MULTILINE)

DEFAULT_TIMEOUT_SECONDS = 30.0


def resolve_cli(explicit: str | None = None, environ: Mapping[str, str] | None = None) -> str:
    """Pick the Orca CLI deterministically: explicit > ORCA_CLI_COMMAND > installed path > ORCA_CLI_BIN_DIR.

    ``ORCA_CLI_BIN_DIR`` is inherited from an Orca terminal; ``PATH`` is not
    searched (another program may be called ``orca``). The value is treated as
    a single executable path, never split by a shell. There is no fallback to
    another binary after a failure.
    """

    env = os.environ if environ is None else environ
    if explicit:
        return explicit
    override = env.get(CLI_OVERRIDE_ENV)
    if override:
        return override
    local_app_data = env.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "Programs" / "orca" / "resources" / "bin" / "orca.exe"
        if candidate.is_file():
            return str(candidate)
    bin_dir = env.get(CLI_BIN_DIR_ENV)
    if bin_dir:
        for name in ("orca.exe", "orca") if os.name == "nt" else ("orca",):
            candidate = Path(bin_dir) / name
            if candidate.is_file():
                return str(candidate)
    raise CliUnavailableError(
        f"Orca CLI not found. Set {CLI_OVERRIDE_ENV} to the installed orca executable."
    )


@dataclass(frozen=True)
class BindingContext:
    """Whether a Coordinator binding is inherited. The handle value is never stored."""

    terminal_handle_present: bool

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> "BindingContext":
        env = os.environ if environ is None else environ
        return cls(terminal_handle_present=bool(env.get(TERMINAL_HANDLE_ENV)))


@dataclass(frozen=True)
class RuntimeStatus:
    """Fields observed in ``status --json``. Missing fields stay ``None`` (UNKNOWN)."""

    target_kind: str | None
    app_running: bool | None
    runtime_state: str | None
    runtime_reachable: bool | None
    connection_state: str | None
    app_version: str | None
    graph_state: str | None

    @property
    def connected(self) -> bool:
        return (
            self.runtime_reachable is True
            and self.runtime_state == "ready"
            and self.connection_state == "connected"
        )

    @property
    def version_pinned(self) -> bool:
        return self.app_version in PINNED_ORCA_VERSIONS


@dataclass(frozen=True)
class RunRef:
    run_id: str
    objective: str | None


class OrcaCliAdapter:
    def __init__(
        self,
        runner: CommandRunner | None = None,
        cli_path: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        cwd: str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._runner = runner or SubprocessRunner()
        self._environ = os.environ if environ is None else environ
        self._cli_path = cli_path
        self._timeout = timeout
        self._cwd = cwd

    @property
    def environ(self) -> Mapping[str, str]:
        return self._environ

    @property
    def binding(self) -> BindingContext:
        return BindingContext.from_environ(self._environ)

    # ---- read operations -------------------------------------------------

    def status(self) -> RuntimeStatus:
        result = self._invoke(["status", "--json"], mutation=False)
        argv = self._last_argv
        app = _optional_dict(result, "app", self._last_argv)
        runtime = _optional_dict(result, "runtime", self._last_argv)
        graph = _optional_dict(result, "graph", self._last_argv)
        if runtime is None:
            raise InvalidResponseError(
                "status result has no runtime object", argv=self._last_argv
            )
        return RuntimeStatus(
            target_kind=_opt(result, "targetKind", str, argv),
            app_running=_opt(app, "running", bool, argv),
            runtime_state=_opt(runtime, "state", str, argv),
            runtime_reachable=_opt(runtime, "reachable", bool, argv),
            connection_state=_opt(runtime, "connectionState", str, argv),
            app_version=_opt(runtime, "appVersion", str, argv),
            graph_state=_opt(graph, "state", str, argv),
        )

    def run_current(self) -> RunRef | None:
        """Return the Run bound to this terminal, or ``None`` when none is bound.

        Orca 1.4.264 answers exit 0 with ``{"ok":true,"result":{"runId":null}}`` when
        no Run is bound. Only an explicit ``null`` means "no Run"; a missing ``runId``
        key is still an invalid response.
        """

        self._require_binding()
        result = self._invoke(["orchestration", "run-current", "--json"], mutation=False)
        if "runId" in result and result["runId"] is None:
            return None
        return self._run_ref(result)

    def worker_show(self, dispatch_id: str, run_id: str | None = None) -> WorkerObservation:
        """Orca's record of one Worker Dispatch. Read-only; needs no Coordinator binding.

        ``run_id`` is the Orca Run the start reported; a reply naming another
        Run (or another Dispatch) raises :class:`InvalidResponseError`. A timeout
        raises :class:`OutcomeUnknownError` (mutation=False); the caller treats
        the Worker's state as unknown.
        """

        result = self._invoke(worker_show_args(dispatch_id), mutation=False)
        return worker_observation(result, dispatch_id, self._last_argv, expected_run=run_id)

    # ---- mutation (Run namespace only) ------------------------------------

    def run_create(self, objective: str, *, retry_request_id: str | None = None) -> RunRef:
        """Create and bind a Run. A Run never schedules or places Workers.

        On timeout this raises :class:`OutcomeUnknownError` and does not retry.
        ``retry_request_id`` is only for exact recovery with an ID Orca reported;
        the adapter never fills it in by itself.
        """

        if not objective or not objective.strip():
            raise ValueError("objective must be non-empty")
        self._require_binding()
        args = ["orchestration", "run-create", "--objective", objective]
        if retry_request_id is not None:
            args += ["--retry-request", retry_request_id]
        args.append("--json")
        result = self._invoke(args, mutation=True)
        return self._run_ref(result)

    # ---- internals ---------------------------------------------------------

    _last_argv: tuple[str, ...] = ()

    def _require_binding(self) -> None:
        if not self.binding.terminal_handle_present:
            raise CoordinatorBindingError(
                "COORDINATOR requires an inherited Orca terminal binding "
                f"({TERMINAL_HANDLE_ENV}); run inside a live Orca terminal.",
                code="coordinator_binding_missing",
            )

    def _invoke(self, args: Sequence[str], *, mutation: bool) -> dict[str, Any]:
        cli = resolve_cli(self._cli_path, self._environ)
        argv = (cli, *args)
        self._last_argv = argv
        try:
            res = self._runner.run(argv, timeout=self._timeout, cwd=self._cwd)
        except CommandLaunchError as exc:
            raise CliUnavailableError(f"could not start Orca CLI: {exc}", argv=argv) from None
        except CommandTimeout as exc:
            hint = (
                " Outcome unknown: do not reissue as a new request; check run-current first."
                if mutation
                else ""
            )
            raise OutcomeUnknownError(f"{exc}.{hint}", mutation=mutation, argv=argv) from None

        payload = _parse_json_object(res.stdout)
        if payload is None:
            if res.exit_code != 0:
                match = _STDERR_ERROR_PATTERN.search(res.stderr)
                if match:
                    raise classify_orca_error(
                        match.group(1), match.group(2).strip(), argv=argv, exit_code=res.exit_code
                    )
                raise InvalidResponseError(
                    f"exit code {res.exit_code} without a JSON or recognizable error",
                    argv=argv,
                    exit_code=res.exit_code,
                )
            raise InvalidResponseError(
                "stdout is not a JSON object", argv=argv, exit_code=res.exit_code
            )

        ok = payload.get("ok")
        if not isinstance(ok, bool):
            raise InvalidResponseError(
                "response has no boolean 'ok'", argv=argv, exit_code=res.exit_code
            )
        if ok is False:
            error = payload.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message") if isinstance(error, dict) else None
            if not isinstance(code, str) or not code:
                raise InvalidResponseError(
                    "ok=false without an error code", argv=argv, exit_code=res.exit_code
                )
            if res.exit_code == 0:
                raise InvalidResponseError(
                    f"ok=false ({code}) with exit code 0", argv=argv, exit_code=0, code=code
                )
            raise classify_orca_error(
                code,
                message if isinstance(message, str) else None,
                argv=argv,
                exit_code=res.exit_code,
            )
        if res.exit_code != 0:
            raise InvalidResponseError(
                f"ok=true with exit code {res.exit_code}", argv=argv, exit_code=res.exit_code
            )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise InvalidResponseError("ok=true without a result object", argv=argv, exit_code=0)
        return result

    def _run_ref(self, result: dict[str, Any]) -> RunRef:
        run_id = result.get("runId")
        if not isinstance(run_id, str) or not RUN_ID_PATTERN.match(run_id):
            raise InvalidResponseError(
                f"missing or malformed runId: {run_id!r}", argv=self._last_argv, exit_code=0
            )
        objective = result.get("objective")
        return RunRef(run_id=run_id, objective=objective if isinstance(objective, str) else None)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _optional_dict(parent: dict[str, Any], key: str, argv: tuple[str, ...]) -> dict[str, Any] | None:
    value = parent.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidResponseError(f"'{key}' is not an object", argv=argv, exit_code=0)
    return value


def _opt(obj: dict[str, Any] | None, key: str, kind: type, argv: tuple[str, ...]) -> Any:
    """Absent or null stays None (UNKNOWN); a present value of the wrong type is invalid."""
    if obj is None:
        return None
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, kind):
        raise InvalidResponseError(
            f"'{key}' has unexpected type {type(value).__name__}", argv=argv, exit_code=0
        )
    return value
