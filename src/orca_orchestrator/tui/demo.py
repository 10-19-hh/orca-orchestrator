"""Demo mode: replays the bundled public fixture. Never starts a process."""

from __future__ import annotations

import json
from importlib import resources
from typing import Sequence

from ..orca.adapter import OrcaCliAdapter
from ..orca.runner import CommandResult
from .readonly import ReadOnlyRunner

FIXTURE_NAME = "orca_cli_1.4.264.json"
DEMO_CLI = "orca-demo"  # placeholder path; demo never executes it
DEMO_HANDLE = "demo-terminal"

STATUS = ("status", "--json")
RUN_CURRENT = ("orchestration", "run-current", "--json")

# scenario -> (status case, run-current case or raw response, bound)
SCENARIOS: dict[str, tuple[str, object, bool]] = {
    "connected": ("status_ready", "run_current_coordinator", True),
    "disconnected": ("status_not_running", "run_current_coordinator", True),
    "unbound": ("status_ready", "run_current_coordinator", False),
    # Synthetic: a run-current reply that is not JSON.
    "invalid": ("status_ready", (0, "Orca says hello\n", ""), True),
}


def load_fixture() -> dict:
    text = resources.files("orca_orchestrator").joinpath("fixtures", FIXTURE_NAME).read_text(
        encoding="utf-8"
    )
    return json.loads(text)


def _case_result(case: dict) -> tuple[int, str, str]:
    body = case["json_response"]
    return case["exit_code"], json.dumps(body) if body is not None else "", case["stderr"]


class FixtureRunner:
    """Answers allowed commands from recorded responses and records each call."""

    def __init__(self, responses: dict[tuple[str, ...], tuple[int, str, str]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], *, timeout: float, cwd: str | None = None) -> CommandResult:
        argv = tuple(argv)
        self.calls.append(argv)
        exit_code, stdout, stderr = self._responses[argv[1:]]
        return CommandResult(argv, exit_code, stdout, stderr)


def demo_adapter(scenario: str = "connected") -> tuple[OrcaCliAdapter, FixtureRunner]:
    status_case, run_case, bound = SCENARIOS[scenario]
    cases = load_fixture()["cases"]
    run_result = _case_result(cases[run_case]) if isinstance(run_case, str) else run_case
    runner = FixtureRunner({STATUS: _case_result(cases[status_case]), RUN_CURRENT: run_result})
    # The demo uses its own environment so the real shell's variables are not shown.
    environ = {"ORCA_TERMINAL_HANDLE": DEMO_HANDLE} if bound else {}
    adapter = OrcaCliAdapter(ReadOnlyRunner(runner), DEMO_CLI, environ=environ)
    return adapter, runner
