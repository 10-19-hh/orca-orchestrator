from __future__ import annotations

import json
import sys
import unittest

from orca_orchestrator.orca import (
    CliUnavailableError,
    CoordinatorBindingError,
    InvalidResponseError,
    OrcaCliAdapter,
    OrcaCommandError,
    OutcomeUnknownError,
    RuntimeUnavailableError,
    SubprocessRunner,
    resolve_cli,
)
from orca_orchestrator.orca.runner import CommandLaunchError, CommandTimeout

from support import (
    BOUND_ENV,
    CASES,
    EXAMPLE_RUN_ID,
    FAKE_CLI,
    FIXTURES,
    NO_RUN_KEY_MISSING,
    ROOT,
    RUN_CREATE,
    RUN_CURRENT,
    STATUS,
    UNBOUND_ENV,
    FakeRunner,
    fixture_result,
)


def adapter(responses, env=BOUND_ENV):
    runner = FakeRunner(responses)
    return OrcaCliAdapter(runner, FAKE_CLI, environ=env), runner


class FixtureConsistencyTest(unittest.TestCase):
    """Recorded responses must be internally consistent (exit code, ok flag, stderr)."""

    def test_fixture_cases_are_consistent(self):
        self.assertEqual(FIXTURES["cli_version"], "1.4.264")
        for name, case in CASES.items():
            with self.subTest(name):
                body = case["json_response"]
                self.assertIsInstance(body["ok"], bool)
                self.assertEqual(case["argv_tail"][-1], "--json")
                if body["ok"]:
                    self.assertEqual(case["exit_code"], 0)
                    self.assertIsInstance(body["result"], dict)
                    self.assertEqual(case["stderr"], "")
                else:
                    self.assertNotEqual(case["exit_code"], 0)
                    error = body["error"]
                    self.assertEqual(
                        case["stderr"], f"error: {error['code']}: {error['message']}\n"
                    )


class StatusTest(unittest.TestCase):
    def test_ready_runtime(self):
        a, runner = adapter({STATUS: fixture_result("status_ready")}, env=UNBOUND_ENV)
        status = a.status()
        self.assertTrue(status.connected)
        self.assertTrue(status.version_pinned)
        self.assertEqual(status.app_version, "1.4.264")
        self.assertEqual(status.target_kind, "local")
        self.assertEqual(runner.calls, [(FAKE_CLI, "status", "--json")])

    def test_runtime_not_running_is_reported_not_raised(self):
        a, _ = adapter({STATUS: fixture_result("status_not_running")})
        status = a.status()
        self.assertFalse(status.connected)
        self.assertEqual(status.runtime_state, "not_running")
        self.assertIs(status.runtime_reachable, False)
        self.assertIsNone(status.connection_state)  # absent stays UNKNOWN
        self.assertIsNone(status.app_version)
        self.assertFalse(status.version_pinned)

    def test_non_json_stdout(self):
        a, _ = adapter({STATUS: (0, "Orca is running\n", "")})
        with self.assertRaises(InvalidResponseError):
            a.status()

    def test_missing_ok(self):
        a, _ = adapter({STATUS: (0, json.dumps({"result": {}}), "")})
        with self.assertRaises(InvalidResponseError):
            a.status()

    def test_missing_runtime_object(self):
        a, _ = adapter({STATUS: (0, json.dumps({"ok": True, "result": {"app": {}}}), "")})
        with self.assertRaises(InvalidResponseError):
            a.status()

    def test_wrong_field_type(self):
        body = {"ok": True, "result": {"runtime": {"state": "ready", "reachable": "yes"}}}
        a, _ = adapter({STATUS: (0, json.dumps(body), "")})
        with self.assertRaises(InvalidResponseError):
            a.status()

    def test_ok_true_with_nonzero_exit(self):
        exit_code, stdout, stderr = fixture_result("status_ready")
        a, _ = adapter({STATUS: (2, stdout, stderr)})
        with self.assertRaises(InvalidResponseError) as cm:
            a.status()
        self.assertEqual(cm.exception.exit_code, 2)


class RunCurrentTest(unittest.TestCase):
    def test_success_returns_run_id(self):
        a, runner = adapter({RUN_CURRENT: fixture_result("run_current_coordinator")})
        ref = a.run_current()
        self.assertEqual(ref.run_id, EXAMPLE_RUN_ID)
        self.assertEqual(runner.calls, [(FAKE_CLI, "orchestration", "run-current", "--json")])

    def test_requires_binding_before_invoking(self):
        a, runner = adapter({RUN_CURRENT: fixture_result("run_current_coordinator")}, env=UNBOUND_ENV)
        with self.assertRaises(CoordinatorBindingError) as cm:
            a.run_current()
        self.assertEqual(cm.exception.code, "coordinator_binding_missing")
        self.assertEqual(runner.calls, [])

    def test_runtime_unavailable(self):
        a, _ = adapter({RUN_CURRENT: fixture_result("run_create_runtime_unavailable")})
        with self.assertRaises(RuntimeUnavailableError) as cm:
            a.run_current()
        self.assertEqual(cm.exception.code, "runtime_unavailable")
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertIn("Start the Orca app first.", cm.exception.orca_message)

    def test_explicit_null_run_id_means_no_run(self):
        a, runner = adapter({RUN_CURRENT: fixture_result("run_current_no_run")})
        self.assertIsNone(a.run_current())
        self.assertEqual(len(runner.calls), 1)

    def test_missing_run_id_key_is_invalid_not_no_run(self):
        a, _ = adapter({RUN_CURRENT: NO_RUN_KEY_MISSING})
        with self.assertRaises(InvalidResponseError):
            a.run_current()

    def test_missing_run_id(self):
        a, _ = adapter({RUN_CURRENT: (0, json.dumps({"ok": True, "result": {"objective": "x"}}), "")})
        with self.assertRaises(InvalidResponseError):
            a.run_current()

    def test_non_string_run_id_is_invalid(self):
        for value in (0, False, "", [], {}):
            with self.subTest(value=value):
                a, _ = adapter({RUN_CURRENT: (0, json.dumps({"ok": True, "result": {"runId": value}}), "")})
                with self.assertRaises(InvalidResponseError):
                    a.run_current()

    def test_malformed_run_id(self):
        body = {"ok": True, "result": {"runId": "not-a-run"}}
        a, _ = adapter({RUN_CURRENT: (0, json.dumps(body), "")})
        with self.assertRaises(InvalidResponseError):
            a.run_current()

    def test_ok_false_with_exit_zero_is_invalid(self):
        body = {"ok": False, "error": {"code": "something", "message": "m"}}
        a, _ = adapter({RUN_CURRENT: (0, json.dumps(body), "")})
        with self.assertRaises(InvalidResponseError) as cm:
            a.run_current()
        self.assertEqual(cm.exception.code, "something")

    def test_ok_false_without_code_is_invalid(self):
        a, _ = adapter({RUN_CURRENT: (1, json.dumps({"ok": False, "error": {}}), "")})
        with self.assertRaises(InvalidResponseError):
            a.run_current()

    def test_error_parsed_from_stderr_when_stdout_empty(self):
        _, _, stderr = fixture_result("run_create_runtime_unavailable")
        a, _ = adapter({RUN_CURRENT: (1, "", stderr)})
        with self.assertRaises(RuntimeUnavailableError) as cm:
            a.run_current()
        self.assertEqual(cm.exception.code, "runtime_unavailable")

    def test_unrecognized_failure(self):
        a, _ = adapter({RUN_CURRENT: (3, "", "segfault\n")})
        with self.assertRaises(InvalidResponseError) as cm:
            a.run_current()
        self.assertEqual(cm.exception.exit_code, 3)

    def test_unknown_orca_code_is_generic_command_error(self):
        body = {"ok": False, "error": {"code": "runtime_access_denied", "message": "denied"}}
        a, _ = adapter({RUN_CURRENT: (1, json.dumps(body), "")})
        with self.assertRaises(OrcaCommandError) as cm:
            a.run_current()
        self.assertNotIsInstance(cm.exception, RuntimeUnavailableError)
        self.assertEqual(cm.exception.code, "runtime_access_denied")


class RunCreateTest(unittest.TestCase):
    def test_success_argv_has_no_from(self):
        a, runner = adapter({RUN_CREATE: fixture_result("run_create_coordinator")})
        ref = a.run_create("objective text")
        self.assertEqual(ref.run_id, EXAMPLE_RUN_ID)
        self.assertEqual(
            runner.calls,
            [(FAKE_CLI, "orchestration", "run-create", "--objective", "objective text", "--json")],
        )
        self.assertNotIn("--from", runner.calls[0])

    def test_null_run_id_is_invalid_for_run_create(self):
        # Reuse the observed no-Run body as a run-create reply: creation must return an ID.
        a, _ = adapter({RUN_CREATE: fixture_result("run_current_no_run")})
        with self.assertRaises(InvalidResponseError):
            a.run_create("x")

    def test_unbound_terminal_error_from_orca(self):
        # Experiment 1 EXTERNAL result, replayed for a caller that looked bound.
        a, _ = adapter({RUN_CREATE: fixture_result("run_create_external_unbound")})
        with self.assertRaises(CoordinatorBindingError) as cm:
            a.run_create("x")
        self.assertEqual(cm.exception.code, "no_active_sender_terminal")
        self.assertTrue(cm.exception.orca_message.startswith("Could not determine the calling terminal"))

    def test_timeout_is_outcome_unknown_and_not_retried(self):
        def boom():
            raise CommandTimeout("timed out after 30s")

        runner = FakeRunner({RUN_CREATE: boom})
        a = OrcaCliAdapter(runner, FAKE_CLI, environ=BOUND_ENV)
        with self.assertRaises(OutcomeUnknownError) as cm:
            a.run_create("x")
        self.assertTrue(cm.exception.mutation)
        self.assertEqual(len(runner.calls), 1)

    def test_explicit_retry_request_only(self):
        runner = FakeRunner({("orchestration", "run-create", "--objective", "--retry-request", "--json"):
                             fixture_result("run_create_coordinator")})
        a = OrcaCliAdapter(runner, FAKE_CLI, environ=BOUND_ENV)
        a.run_create("x", retry_request_id="req_1")
        self.assertEqual(runner.calls[0][-3:], ("--retry-request", "req_1", "--json"))

    def test_empty_objective_rejected(self):
        a, runner = adapter({})
        with self.assertRaises(ValueError):
            a.run_create("  ")
        self.assertEqual(runner.calls, [])


class CliResolutionTest(unittest.TestCase):
    def test_launch_failure(self):
        def boom():
            raise CommandLaunchError("FileNotFoundError")

        a = OrcaCliAdapter(FakeRunner({STATUS: boom}), FAKE_CLI, environ={})
        with self.assertRaises(CliUnavailableError):
            a.status()

    def test_override_env(self):
        self.assertEqual(resolve_cli(None, {"ORCA_CLI_COMMAND": "X:\\orca.exe"}), "X:\\orca.exe")

    def test_explicit_wins(self):
        self.assertEqual(resolve_cli("A", {"ORCA_CLI_COMMAND": "B"}), "A")

    def test_not_found(self):
        with self.assertRaises(CliUnavailableError):
            resolve_cli(None, {"LOCALAPPDATA": str(ROOT / "nonexistent")})

    def test_bin_dir_inherited_from_an_orca_terminal(self):
        import os
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            name = "orca.exe" if os.name == "nt" else "orca"
            env = {"ORCA_CLI_BIN_DIR": tmp, "PATH": tmp}
            with self.assertRaises(CliUnavailableError):  # folder without the CLI
                resolve_cli(None, env)
            (Path(tmp) / name).write_text("", encoding="utf-8")
            self.assertEqual(resolve_cli(None, env), str(Path(tmp) / name))
            self.assertEqual(resolve_cli(None, {**env, "ORCA_CLI_COMMAND": "B"}), "B")
            self.assertEqual(resolve_cli("A", env), "A")
            with self.assertRaises(CliUnavailableError):  # PATH alone is never searched
                resolve_cli(None, {"PATH": tmp})


class SubprocessRunnerTest(unittest.TestCase):
    """Exercises the real runner against Python, not Orca."""

    def test_argv_without_shell(self):
        code = "import json,sys; print(json.dumps({'ok': True, 'result': {'argv': sys.argv[1:]}}))"
        res = SubprocessRunner().run([sys.executable, "-c", code, "a b", "&& echo x"], timeout=30)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(json.loads(res.stdout)["result"]["argv"], ["a b", "&& echo x"])

    def test_missing_executable(self):
        with self.assertRaises(CommandLaunchError):
            SubprocessRunner().run([str(ROOT / "no-such-orca.exe")], timeout=5)

    def test_timeout(self):
        with self.assertRaises(CommandTimeout):
            SubprocessRunner().run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.5)


if __name__ == "__main__":
    unittest.main()
