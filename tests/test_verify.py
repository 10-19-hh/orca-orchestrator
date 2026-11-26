from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from orca_orchestrator import verify as cli
from orca_orchestrator.orca.runner import CommandLaunchError, CommandResult, CommandTimeout, SubprocessRunner
from orca_orchestrator.tasks import (
    PLAN_SCHEMA,
    RESULTS_SCHEMA,
    FileTaskSource,
    IssueCode,
    Outcome,
    TaskState,
    parse_results,
)
from orca_orchestrator.tasks.verification import (
    SUPPORTED_CHECKS,
    STRIPPED_ENV_NAMES,
    VerificationNotCompleted,
    VerificationNotRecorded,
    VerificationSetupError,
    lock_path,
    results_lock,
    verify,
)

from support import ROOT

NOW = datetime(2026, 11, 26, 11, 0, 0, tzinfo=timezone.utc)
PYTHON = "C:/fake/python.exe"
UNIT_ARGV = [PYTHON, "-m", "unittest", "discover", "-s", "tests"]
OK_STDERR = "....\n----------------------------------------------------------------------\nRan 4 tests in 0.01s\n\nOK\n"
FAIL_STDERR = "F.\n======\nFAIL: test_x\n----\nRan 2 tests in 0.01s\n\nFAILED (failures=1)\n"


def plan_doc(command="python -m unittest discover -s tests"):
    return {
        "schema": PLAN_SCHEMA,
        "requirements": [
            {"id": "FR-01", "title": "t", "acceptance": "a"},
            {"id": "FR-07", "title": "t", "acceptance": "a"},
        ],
        "tasks": [
            {"id": "T-1", "title": "a", "requirements": ["FR-01"],
             "checks": [{"id": "unit-tests", "description": "d", "command": command}]},
            {"id": "T-2", "title": "b", "requirements": ["FR-07"], "depends_on": ["T-1"],
             "checks": [{"id": "screen-tests", "description": "d"}, {"id": "unit-tests", "description": "d"}]},
        ],
    }


def results_doc(*entries):
    return {"schema": RESULTS_SCHEMA, "results": [dict(e) for e in entries]}


class FakeVerifyRunner:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []
        self.env = None

    def factory(self, env):
        self.env = env
        return self

    def run(self, argv, *, timeout, cwd=None):
        self.calls.append((list(argv), timeout, cwd))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        code, stderr = self.outcome
        return CommandResult(tuple(argv), code, "", stderr)


class Workspace:
    """A temporary repo root with tests/, a plan file and an optional results file."""

    def __init__(self, test, plan=None, results=None):
        self._tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "tests").mkdir()
        self.plan = self.root / "plan.json"
        self.results = self.root / "results.json"
        self.plan.write_text(json.dumps(plan or plan_doc()), encoding="utf-8")
        if results is not None:
            self.results.write_text(json.dumps(results), encoding="utf-8")

    def run(self, runner, task="T-1", check="unit-tests", environ=None, **kw):
        return verify(self.plan, self.results, task, check, repo=self.root, environ=environ or {},
                      runner_factory=runner.factory, python=PYTHON, now=lambda: NOW, **kw)

    def snapshot(self):
        return self.results.read_bytes() if self.results.exists() else None

    def stored(self):
        return parse_results(json.loads(self.results.read_text(encoding="utf-8")))


class OutcomeTest(unittest.TestCase):
    def test_exit_zero_records_passed(self):
        ws = Workspace(self)
        runner = FakeVerifyRunner((0, OK_STDERR))
        run = ws.run(runner)
        self.assertEqual(runner.calls, [(UNIT_ARGV, 600.0, str(ws.root.resolve()))])
        (stored,) = ws.stored()
        self.assertEqual((stored.task_id, stored.check_id, stored.outcome, stored.exit_code),
                         ("T-1", "unit-tests", Outcome.PASSED, 0))
        self.assertEqual(stored.evidence,
                         "python -m unittest discover -s tests · 2026-11-26T11:00:00Z · Ran 4 tests · OK")
        self.assertEqual((run.kept, run.replaced), (0, False))
        board = FileTaskSource(ws.plan, ws.results).load()
        self.assertEqual(board.progress.task("T-1").state, TaskState.VERIFIED)

    def test_nonzero_exit_records_failed(self):
        for code in (1, 5, -1):
            with self.subTest(exit_code=code):
                ws = Workspace(self)
                ws.run(FakeVerifyRunner((code, FAIL_STDERR)))
                (stored,) = ws.stored()
                self.assertEqual((stored.outcome, stored.exit_code), (Outcome.FAILED, code))
                progress = FileTaskSource(ws.plan, ws.results).load().progress
                self.assertEqual(progress.task("T-1").state, TaskState.FAILED)
                self.assertEqual(progress.task("T-2").state, TaskState.WAITING)

    def test_timeout_and_launch_failure_record_nothing(self):
        existing = results_doc({"task": "T-1", "check": "unit-tests", "outcome": "failed", "exit_code": 1})
        for error, text in ((CommandTimeout("timed out"), "제한 시간 600초"),
                            (CommandLaunchError("FileNotFoundError: x"), "실행하지 못함")):
            for results in (None, existing):
                with self.subTest(error=type(error).__name__, existing=results is not None):
                    ws = Workspace(self, results=results)
                    before = ws.snapshot()
                    runner = FakeVerifyRunner(error)
                    with self.assertRaises(VerificationNotCompleted) as ctx:
                        ws.run(runner)
                    self.assertIn(text, ctx.exception.message)
                    self.assertEqual(len(runner.calls), 1)  # tried once, never retried
                    self.assertEqual(ws.snapshot(), before)
                    self.assertEqual(sorted(p.name for p in ws.root.iterdir()),
                                     sorted(["plan.json", "tests"] + (["results.json"] if results else [])))

    def test_runs_once_with_given_timeout(self):
        ws = Workspace(self)
        runner = FakeVerifyRunner((0, OK_STDERR))
        ws.run(runner, timeout=12.5)
        self.assertEqual([c[1] for c in runner.calls], [12.5])


class PreserveTest(unittest.TestCase):
    def test_other_results_kept_and_same_pair_replaced(self):
        existing = results_doc(
            {"task": "T-2", "check": "screen-tests", "outcome": "passed", "exit_code": 0, "evidence": "manual"},
            {"task": "T-1", "check": "unit-tests", "outcome": "failed", "exit_code": 1},
            {"task": "T-2", "check": "unit-tests", "outcome": "failed"},
        )
        ws = Workspace(self, results=existing)
        run = ws.run(FakeVerifyRunner((0, OK_STDERR)))
        stored = ws.stored()
        self.assertEqual([(r.task_id, r.check_id, r.outcome) for r in stored], [
            ("T-2", "screen-tests", Outcome.PASSED),
            ("T-1", "unit-tests", Outcome.PASSED),
            ("T-2", "unit-tests", Outcome.FAILED),
        ])
        self.assertEqual(stored[0].evidence, "manual")
        self.assertIsNone(stored[2].exit_code)
        self.assertEqual((run.kept, run.replaced), (2, True))
        progress = FileTaskSource(ws.plan, ws.results).load().progress
        self.assertEqual(progress.task("T-2").state, TaskState.FAILED)

    def test_second_task_does_not_touch_first(self):
        ws = Workspace(self)
        ws.run(FakeVerifyRunner((0, OK_STDERR)), task="T-1")
        ws.run(FakeVerifyRunner((1, FAIL_STDERR)), task="T-2")
        self.assertEqual([(r.task_id, r.outcome) for r in ws.stored()],
                         [("T-1", Outcome.PASSED), ("T-2", Outcome.FAILED)])

    def test_file_is_readable_by_status_screen_after_each_write(self):
        ws = Workspace(self)
        source = FileTaskSource(ws.plan, ws.results)
        ws.run(FakeVerifyRunner((1, FAIL_STDERR)))
        self.assertEqual(source.load().progress.task("T-2").state, TaskState.WAITING)
        ws.run(FakeVerifyRunner((0, OK_STDERR)))
        self.assertEqual(source.load().progress.task("T-2").state, TaskState.READY)  # what r re-reads


class RefusalTest(unittest.TestCase):
    def assert_refused(self, ws, text, task="T-1", check="unit-tests", issues=None):
        before = ws.snapshot()
        runner = FakeVerifyRunner((0, OK_STDERR))
        with self.assertRaises(VerificationSetupError) as ctx:
            ws.run(runner, task=task, check=check)
        self.assertIn(text, ctx.exception.message)
        if issues is not None:
            self.assertEqual([i.code for i in ctx.exception.issues], issues)
        self.assertEqual(runner.calls, [])
        self.assertEqual(ws.snapshot(), before)

    def test_unknown_task_or_check(self):
        self.assert_refused(Workspace(self), "작업 T-9이(가) 작업 정의에 없음", task="T-9")
        self.assert_refused(Workspace(self), "작업 T-1에 검증 조건 screen-tests이(가) 없음", check="screen-tests")

    def test_defined_but_unsupported_check(self):
        self.assert_refused(Workspace(self), "screen-tests은(는) 실행을 지원하지 않음", task="T-2", check="screen-tests")

    def test_invalid_plan(self):
        plan = plan_doc()
        plan["tasks"][0]["depends_on"] = ["T-2"]
        self.assert_refused(Workspace(self, plan=plan), "작업 정의에 오류", issues=[IssueCode.DEPENDENCY_CYCLE])

    def test_unreadable_plan(self):
        ws = Workspace(self)
        ws.plan.write_text("{", encoding="utf-8")
        self.assert_refused(ws, "작업 정의 파일의 JSON 형식 오류 (plan.json 1행)")
        ws.plan.unlink()
        self.assert_refused(ws, "작업 정의 파일을 읽을 수 없음 (plan.json)")

    def test_bad_existing_results_are_not_overwritten(self):
        ws = Workspace(self)
        ws.results.write_text("not json", encoding="utf-8")
        self.assert_refused(ws, "검증 결과 파일의 JSON 형식 오류")
        ws.results.write_text(json.dumps({"schema": RESULTS_SCHEMA, "results": [{"task": "T-1"}]}), encoding="utf-8")
        self.assert_refused(ws, "검증 결과 형식 오류")
        stale = results_doc({"task": "T-3", "check": "unit-tests", "outcome": "passed"})
        ws.results.write_text(json.dumps(stale), encoding="utf-8")
        self.assert_refused(ws, "기존 검증 결과에 오류", issues=[IssueCode.RESULT_UNKNOWN_TASK])

    def test_missing_tests_folder_or_results_folder(self):
        ws = Workspace(self)
        (ws.root / "tests").rmdir()
        self.assert_refused(ws, "tests 폴더가 없음")
        ws2 = Workspace(self)
        ws2.results = ws2.root / "missing" / "results.json"
        self.assert_refused(ws2, "저장할 폴더가 없음")


class CommandSafetyTest(unittest.TestCase):
    def test_plan_command_text_is_never_executed(self):
        for text in ("echo hacked", "del /s /q C:\\", "python -c \"import os; os.remove('x')\""):
            with self.subTest(text):
                ws = Workspace(self, plan=plan_doc(command=text))
                runner = FakeVerifyRunner((0, OK_STDERR))
                ws.run(runner)
                (argv, _, _), = runner.calls
                self.assertEqual(argv, UNIT_ARGV)
                self.assertNotIn(text, " ".join(argv))

    def test_only_unit_tests_are_supported(self):
        self.assertEqual(list(SUPPORTED_CHECKS), ["unit-tests"])

    def test_child_environment(self):
        ws = Workspace(self)
        runner = FakeVerifyRunner((0, OK_STDERR))
        environ = {"PATH": "p", "PYTHONPATH": "old", "OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": "y",
                   "CLAUDE_CODE_OAUTH_TOKEN": "z", "ORCA_TERMINAL_HANDLE": "term_x", "KEEP": "1"}
        ws.run(runner, environ=environ)
        root = ws.root.resolve()
        self.assertEqual(runner.env, {"PATH": "p", "KEEP": "1",
                                      "PYTHONPATH": os.pathsep.join([str(root / "src"), str(root / "tests")])})
        self.assertTrue({"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ORCA_TERMINAL_HANDLE"} <= STRIPPED_ENV_NAMES)

    def test_subprocess_runner_uses_argv_without_shell(self):
        completed = subprocess.CompletedProcess(["x"], 0, "", "")
        with mock.patch("subprocess.run", return_value=completed) as run:
            SubprocessRunner(env={"A": "1"}).run(["python", "-m", "unittest"], timeout=3, cwd="d")
            SubprocessRunner().run(["python"], timeout=3)
        first, second = run.call_args_list
        self.assertEqual(first.args[0], ["python", "-m", "unittest"])
        self.assertIs(first.kwargs["shell"], False)
        self.assertEqual((first.kwargs["env"], first.kwargs["cwd"], first.kwargs["timeout"]), ({"A": "1"}, "d", 3))
        self.assertIsNone(second.kwargs["env"])  # Orca calls keep inheriting the environment


class RealProcessTest(unittest.TestCase):
    """Runs the real command against a tiny throwaway repository (not this one)."""

    def write_test(self, ws, body):
        (ws.root / "tests" / "test_sample.py").write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n    def test_x(self):\n" f"        {body}\n",
            encoding="utf-8",
        )

    def run_real(self, ws):
        return verify(ws.plan, ws.results, "T-1", "unit-tests", repo=ws.root, timeout=120)

    def test_pass_then_fail(self):
        ws = Workspace(self)
        self.write_test(ws, "self.assertTrue(True)")
        run = self.run_real(ws)
        self.assertEqual((run.result.outcome, run.result.exit_code, run.summary),
                         (Outcome.PASSED, 0, "Ran 1 test · OK"))
        self.write_test(ws, "self.assertTrue(False)")
        run = self.run_real(ws)
        self.assertEqual((run.result.outcome, run.result.exit_code), (Outcome.FAILED, 1))
        self.assertIn("FAILED (failures=1)", run.summary)
        self.assertEqual([r.outcome for r in ws.stored()], [Outcome.FAILED])

    def test_real_timeout_records_nothing(self):
        ws = Workspace(self)
        self.write_test(ws, "import time; time.sleep(30)")
        with self.assertRaises(VerificationNotCompleted):
            verify(ws.plan, ws.results, "T-1", "unit-tests", repo=ws.root, timeout=1)
        self.assertFalse(ws.results.exists())

    def test_missing_interpreter_records_nothing(self):
        ws = Workspace(self)
        with self.assertRaises(VerificationNotCompleted) as ctx:
            verify(ws.plan, ws.results, "T-1", "unit-tests", repo=ws.root, python=str(ws.root / "no-python.exe"))
        self.assertIn("실행하지 못함", ctx.exception.message)
        self.assertFalse(ws.results.exists())


class CliTest(unittest.TestCase):
    def call(self, ws, outcome, *extra, task="T-1", check="unit-tests", **verify_kwargs):
        runner = FakeVerifyRunner(outcome)
        real_verify = cli.verify

        def fake_verify(*args, **kwargs):
            return real_verify(*args, runner_factory=runner.factory, python=PYTHON, now=lambda: NOW,
                               **kwargs, **verify_kwargs)

        out = io.StringIO()
        argv = ["--tasks", str(ws.plan), "--results", str(ws.results), "--task", task, "--check", check,
                "--repo", str(ws.root), *extra]
        with mock.patch.object(cli, "verify", fake_verify), redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue(), runner

    def test_exit_codes_and_messages(self):
        ws = Workspace(self)
        code, out, _ = self.call(ws, (0, OK_STDERR))
        self.assertEqual(code, cli.EXIT_PASSED)
        self.assertIn("실행: T-1/unit-tests → python -m unittest discover -s tests (한 번, 제한 600초)", out)
        self.assertIn("검증 통과 · T-1/unit-tests · 종료 코드 0 · Ran 4 tests · OK", out)
        self.assertIn("기록: results.json (다른 결과 0건 보존)", out)

        code, out, _ = self.call(ws, (1, FAIL_STDERR))
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertIn("검증 실패 · T-1/unit-tests · 종료 코드 1", out)
        self.assertIn("이전 결과 교체", out)
        self.assertIn("  FAILED (failures=1)", out)

        code, out, _ = self.call(ws, CommandTimeout("t"), "--timeout", "5")
        self.assertEqual(code, cli.EXIT_NOT_COMPLETED)
        self.assertIn("검증 미완료 · T-1/unit-tests · 제한 시간 5초 안에 끝나지 않음", out)
        self.assertIn("결과를 기록하지 않았습니다", out)
        self.assertEqual(ws.stored()[0].outcome, Outcome.FAILED)  # unchanged by the timeout

        code, out, runner = self.call(ws, (0, OK_STDERR), task="T-2", check="screen-tests")
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("검증하지 않음 · 검증 조건 screen-tests은(는) 실행을 지원하지 않음", out)
        self.assertEqual(runner.calls, [])
        self.assertNotIn(str(ws.root), out)

    def test_issue_list_on_invalid_plan(self):
        plan = plan_doc()
        plan["tasks"][1]["depends_on"] = ["T-1", "T-9"]
        code, out, _ = self.call(Workspace(self, plan=plan), (0, OK_STDERR))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("  · T-2의 선행 작업 T-9이(가) 정의되지 않음", out)

    def test_arguments_required(self):
        for argv in ([], ["--tasks", "p", "--results", "r", "--task", "T-1"],
                     ["--tasks", "p", "--results", "r", "--task", "T-1", "--check", "c", "--timeout", "0"]):
            with self.subTest(argv):
                with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                    with self.assertRaises(SystemExit):
                        cli.main(argv)


ZERO_OK = "\n----------------------------------------------------------------------\nRan 0 tests in 0.000s\n\nOK\n"
ZERO_NO_TESTS = "\n----------------------------------------------------------------------\nRan 0 tests in 0.000s\n\nNO TESTS RAN\n"
SRC_ENV = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="utf-8")

HOLD_LOCK_SCRIPT = r"""
import sys, time
from pathlib import Path
from orca_orchestrator.tasks import Outcome, VerificationResult
from orca_orchestrator.tasks.verification import load_results, merge_results, results_lock, write_results
path = Path(sys.argv[1])
with results_lock(path):
    latest = load_results(path)          # read before the other writer
    print("locked", flush=True)
    time.sleep(float(sys.argv[2]))
    merged, _ = merge_results(latest, VerificationResult("T-2", "unit-tests", Outcome.FAILED, 1, "child"))
    write_results(path, merged)
print("released", flush=True)
"""


def plan_two_unit_tasks():
    plan = plan_doc()
    plan["tasks"][1]["checks"] = [{"id": "unit-tests", "description": "d"}]
    return plan


class HookRunner(FakeVerifyRunner):
    """Runs ``during`` while the fake test command is 'running'."""

    def __init__(self, outcome, during):
        super().__init__(outcome)
        self.during = during

    def run(self, argv, *, timeout, cwd=None):
        self.during()
        return super().run(argv, timeout=timeout, cwd=cwd)


class ConcurrentSaveTest(unittest.TestCase):
    def test_result_saved_by_another_run_during_tests_is_kept(self):
        ws = Workspace(self, plan=plan_two_unit_tasks())
        other = lambda: ws.run(FakeVerifyRunner((1, FAIL_STDERR)), task="T-2")  # noqa: E731
        run = ws.run(HookRunner((0, OK_STDERR), other), task="T-1")
        self.assertEqual([(r.task_id, r.outcome) for r in ws.stored()],
                         [("T-2", Outcome.FAILED), ("T-1", Outcome.PASSED)])
        self.assertEqual((run.kept, run.replaced), (1, False))

    def test_save_waits_for_another_process_holding_the_lock(self):
        ws = Workspace(self, plan=plan_two_unit_tasks())
        child = subprocess.Popen([sys.executable, "-c", HOLD_LOCK_SCRIPT, str(ws.results), "0.8"],
                                 stdout=subprocess.PIPE, text=True, env=SRC_ENV)
        self.addCleanup(child.wait, 10)
        self.assertEqual(child.stdout.readline().strip(), "locked")
        started = time.monotonic()
        ws.run(FakeVerifyRunner((0, OK_STDERR)), task="T-1")
        waited = time.monotonic() - started
        self.assertEqual(child.stdout.readline().strip(), "released")
        child.stdout.close()
        self.assertGreaterEqual(waited, 0.4)  # blocked until the other writer finished
        self.assertEqual([(r.task_id, r.evidence) for r in ws.stored()][0], ("T-2", "child"))
        self.assertEqual(sorted(r.task_id for r in ws.stored()), ["T-1", "T-2"])

    def test_two_real_processes_on_one_results_file(self):
        ws = Workspace(self, plan=plan_two_unit_tasks())
        (ws.root / "tests" / "test_slow.py").write_text(
            "import time, unittest\n\nclass T(unittest.TestCase):\n"
            "    def test_slow(self):\n        time.sleep(0.6)\n",
            encoding="utf-8",
        )
        procs = [
            subprocess.Popen(
                [sys.executable, "-m", "orca_orchestrator.verify", "--tasks", str(ws.plan),
                 "--results", str(ws.results), "--task", task, "--check", "unit-tests", "--repo", str(ws.root)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", env=SRC_ENV,
            )
            for task in ("T-1", "T-2")
        ]
        outputs = [p.communicate(timeout=120)[0] for p in procs]
        self.assertEqual([p.returncode for p in procs], [0, 0], outputs)
        self.assertEqual(sorted((r.task_id, r.outcome) for r in ws.stored()),
                         [("T-1", Outcome.PASSED), ("T-2", Outcome.PASSED)])

    def test_lock_timeout_keeps_file(self):
        existing = results_doc({"task": "T-2", "check": "screen-tests", "outcome": "passed"})
        ws = Workspace(self, results=existing)
        before = ws.snapshot()
        with results_lock(ws.results):
            with self.assertRaises(VerificationNotRecorded) as ctx:
                ws.run(FakeVerifyRunner((0, OK_STDERR)), lock_timeout=0.2)
        self.assertIn("0.2초 안에 끝나지 않아", ctx.exception.message)
        self.assertEqual(ctx.exception.result.outcome, Outcome.PASSED)
        self.assertEqual(ws.snapshot(), before)


class SaveFailureTest(unittest.TestCase):
    EXISTING = results_doc({"task": "T-2", "check": "screen-tests", "outcome": "passed", "exit_code": 0})

    def assert_not_recorded(self, ws, during, text, issues=None):
        with self.assertRaises(VerificationNotRecorded) as ctx:
            ws.run(HookRunner((0, OK_STDERR), during))
        self.assertIn(text, ctx.exception.message)
        if issues is not None:
            self.assertEqual([i.code for i in ctx.exception.issues], issues)
        self.assertEqual(list(ws.root.glob("*.tmp")), [])
        return ctx.exception

    def test_latest_file_unreadable_is_preserved(self):
        for broken in ("{not json", json.dumps({"schema": RESULTS_SCHEMA, "results": [{"task": "T-1"}]})):
            with self.subTest(broken=broken[:10]):
                ws = Workspace(self, results=self.EXISTING)
                self.assert_not_recorded(ws, lambda: ws.results.write_text(broken, encoding="utf-8"),
                                         "저장 직전 확인 실패")
                self.assertEqual(ws.results.read_text(encoding="utf-8"), broken)

    def test_latest_results_with_errors_are_preserved(self):
        ws = Workspace(self, results=self.EXISTING)
        stale = json.dumps(results_doc({"task": "T-9", "check": "unit-tests", "outcome": "passed"}))
        self.assert_not_recorded(ws, lambda: ws.results.write_text(stale, encoding="utf-8"),
                                 "최신 검증 결과에 오류", [IssueCode.RESULT_UNKNOWN_TASK])
        self.assertEqual(ws.results.read_text(encoding="utf-8"), stale)

    def test_plan_changed_during_run(self):
        ws = Workspace(self, results=self.EXISTING)
        before = ws.snapshot()
        plan = plan_doc()
        plan["tasks"][0]["checks"] = [{"id": "lint", "description": "d"}]
        self.assert_not_recorded(ws, lambda: ws.plan.write_text(json.dumps(plan), encoding="utf-8"),
                                 "최신 작업 정의와 결과가 맞지 않음", [IssueCode.RESULT_UNKNOWN_CHECK])
        self.assertEqual(ws.snapshot(), before)

    def test_write_failure_keeps_original_and_removes_temp(self):
        ws = Workspace(self, results=self.EXISTING)
        before = ws.snapshot()
        with mock.patch("orca_orchestrator.tasks.verification.os.replace", side_effect=OSError("disk full")):
            err = self.assert_not_recorded(ws, lambda: None, "결과 파일을 저장하지 못함 (results.json: OSError)")
        self.assertEqual(ws.snapshot(), before)
        self.assertEqual(err.result.outcome, Outcome.PASSED)

    def test_busy_destination_is_retried_briefly_then_reported(self):
        ws = Workspace(self, results=self.EXISTING)
        before = ws.snapshot()
        with mock.patch("orca_orchestrator.tasks.verification.os.replace", side_effect=PermissionError("busy")) as rep, \
                mock.patch("orca_orchestrator.tasks.verification.time.sleep"):
            self.assert_not_recorded(ws, lambda: None, "PermissionError")
        self.assertEqual(rep.call_count, 5)
        self.assertEqual(ws.snapshot(), before)

    def test_lock_file_is_separate_and_left_in_place(self):
        ws = Workspace(self)
        ws.run(FakeVerifyRunner((0, OK_STDERR)))
        self.assertTrue(lock_path(ws.results).exists())
        self.assertEqual(lock_path(ws.results).name, "results.json.lock")
        self.assertEqual(FileTaskSource(ws.plan, ws.results).load().progress.task("T-1").state, TaskState.VERIFIED)


class NoTestsTest(unittest.TestCase):
    EXISTING = results_doc({"task": "T-1", "check": "unit-tests", "outcome": "failed", "exit_code": 1})

    def test_zero_tests_is_not_completed(self):
        cases = {
            "exit 0, Ran 0 tests (3.11)": (0, ZERO_OK),
            "exit 5, NO TESTS RAN (3.12+)": (5, ZERO_NO_TESTS),
        }
        for name, outcome in cases.items():
            for results in (None, self.EXISTING):
                with self.subTest(name, existing=results is not None):
                    ws = Workspace(self, results=results)
                    before = ws.snapshot()
                    with self.assertRaises(VerificationNotCompleted) as ctx:
                        ws.run(FakeVerifyRunner(outcome))
                    self.assertNotIsInstance(ctx.exception, VerificationNotRecorded)
                    self.assertIn("실행된 테스트가 없음", ctx.exception.message)
                    self.assertEqual(ws.snapshot(), before)
                    self.assertFalse(lock_path(ws.results).exists())  # never reached the save step

    def test_exit_zero_without_test_count_is_not_passed(self):
        ws = Workspace(self, results=self.EXISTING)
        before = ws.snapshot()
        with self.assertRaises(VerificationNotCompleted) as ctx:
            ws.run(FakeVerifyRunner((0, "")))
        self.assertIn("테스트 수를 확인할 수 없어", ctx.exception.message)
        self.assertEqual(ws.snapshot(), before)

    def test_real_empty_tests_folder(self):
        ws = Workspace(self, results=self.EXISTING)
        before = ws.snapshot()
        with self.assertRaises(VerificationNotCompleted) as ctx:
            verify(ws.plan, ws.results, "T-1", "unit-tests", repo=ws.root, timeout=120)
        self.assertIn("실행된 테스트가 없음", ctx.exception.message)
        self.assertEqual(ws.snapshot(), before)

    def test_cli_reports_no_tests(self):
        ws = Workspace(self, results=self.EXISTING)
        code, out, _ = CliTest.call(self, ws, (0, ZERO_OK))
        self.assertEqual(code, cli.EXIT_NOT_COMPLETED)
        self.assertIn("검증 미완료 · T-1/unit-tests · 실행된 테스트가 없음 (tests 폴더 확인, 종료 코드 0)", out)
        self.assertIn("결과를 기록하지 않았습니다", out)
        self.assertEqual(ws.stored()[0].outcome, Outcome.FAILED)

    def test_cli_reports_unsaved_outcome(self):
        ws = Workspace(self, results=self.EXISTING)
        with results_lock(ws.results):
            code, out, _ = CliTest.call(self, ws, (0, OK_STDERR), lock_timeout=0.2)
        self.assertEqual(code, cli.EXIT_NOT_COMPLETED)
        self.assertIn("테스트는 끝났지만(통과, 종료 코드 0)", out)
        self.assertIn("기존 결과 파일은 그대로입니다", out)
        self.assertEqual(ws.stored()[0].outcome, Outcome.FAILED)


if __name__ == "__main__":
    unittest.main()
