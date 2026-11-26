from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Sequence

from orca_orchestrator.orca.runner import CommandResult

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads(
    (ROOT / "src" / "orca_orchestrator" / "fixtures" / "orca_cli_1.4.264.json").read_text(encoding="utf-8")
)
CASES = FIXTURES["cases"]
EXAMPLE_RUN_ID = "run_0123456789ab"  # example value used in the fixture

FAKE_CLI = r"C:\fake\orca.exe"
BOUND_ENV = {"ORCA_TERMINAL_HANDLE": "term_fake_handle"}
UNBOUND_ENV: dict[str, str] = {}


def fixture_result(name: str) -> tuple[int, str, str]:
    case = CASES[name]
    stdout = json.dumps(case["json_response"]) if case["json_response"] is not None else ""
    return case["exit_code"], stdout, case["stderr"]


class FakeRunner:
    """Records argv and replays canned output keyed by the command after the CLI path."""

    def __init__(self, responses: dict[tuple[str, ...], tuple[int, str, str] | Callable[[], None]] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], *, timeout: float, cwd: str | None = None) -> CommandResult:
        argv = tuple(argv)
        self.calls.append(argv)
        key = self._key(argv[1:])
        response = self.responses.get(key)
        if response is None:
            raise AssertionError(f"unexpected command: {argv[1:]}")
        if callable(response):
            response()  # raises
            raise AssertionError("callable response must raise")
        exit_code, stdout, stderr = response
        return CommandResult(argv, exit_code, stdout, stderr)

    @staticmethod
    def _key(tail: Sequence[str]) -> tuple[str, ...]:
        # Commands are keyed without free-text values such as --objective.
        out, skip = [], False
        for arg in tail:
            if skip:
                skip = False
                continue
            out.append(arg)
            if arg in ("--objective", "--retry-request"):
                skip = True
        return tuple(out)


STATUS = ("status", "--json")
RUN_CURRENT = ("orchestration", "run-current", "--json")
NO_RUN_KEY_MISSING = (0, '{"ok": true, "result": {}}', "")  # synthetic: runId key absent
RUN_CREATE = ("orchestration", "run-create", "--objective", "--json")
