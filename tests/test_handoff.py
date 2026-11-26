"""Execution records, handoff and recovery: resume, cut-off sessions, two sessions, unknown ends."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stdout

from orca_orchestrator import handoff as cli
from orca_orchestrator.orca.runner import CommandTimeout
from orca_orchestrator.tasks import FileTaskSource
from orca_orchestrator.tasks.codebase import source_state
from orca_orchestrator.tasks.flow import StopKind, inspect, resume_view, run_flow
from orca_orchestrator.tasks.handoff import (
    AttemptState,
    ClaimRefused,
    ProbeState,
    ResumeState,
    claim_task,
    confirm_stopped,
    read_attempts,
    run_line,
)
from orca_orchestrator.tasks.paths import handoff_path, tool_files
from orca_orchestrator.tasks.review import BlockKind
from orca_orchestrator.tasks.verification import VerificationSetupError, verify
from orca_orchestrator.tui import render as r

from test_flow import Scripted, chain_plan, flow, task, tasks_called
from test_verify import ROOT, Workspace

SRC_ENV = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests")]),
               PYTHONIOENCODING="utf-8")


class FakeWorkerProbe:
    """Stands in for an executor status query (e.g. a Worker's state in Orca)."""

    def __init__(self, state):
        self.state = state
        self.asked = []

    def status(self, attempt, run):
        self.asked.append((attempt.task, run.run_id if run else None))
        return self.state


def resume(ws, probe=None):
    view = inspect(ws.plan, ws.results, ws.root)
    return resume_view(ws.plan, ws.results, view, probe)


def cut_off(ws, task_id="T-1", run_id="pid:999"):
    """A session that took the task and started a run, then vanished without recording the end."""

    plan = inspect(ws.plan, ws.results).plan
    claim = claim_task(ws.plan, ws.results, plan, task_id, ("unit-tests",), repo=ws.root)
    claim.run_started("unit-tests")
    claim.executor_started("unit-tests", run_id)
    claim._held.release()  # what the OS does when the process dies
    return claim


def row(lines, label):
    return next(line[len(label):].lstrip() for line in lines if line.startswith(label))


class RecordTest(unittest.TestCase):
    def test_attempt_records_scope_basis_and_references(self):
        plan = chain_plan(**{"T-1": dict(task("T-1", "FR-01"), paths=["src/a/"], protected_paths=["docs/x.md"])})
        ws = Workspace(self, plan=plan)
        flow(ws, Scripted({}), max_tasks=1)
        (a,) = read_attempts(ws.results)[0]
        self.assertEqual((a.task, a.requirements, a.checks, a.paths, a.protected_paths),
                         ("T-1", ("FR-01",), ("unit-tests",), ("src/a/",), ("docs/x.md",)))
        self.assertEqual(a.source_digest, source_state(ws.root, tool_files(ws.plan, ws.results)).digest)
        self.assertEqual((a.state, a.done, a.remaining), (AttemptState.ENDED, ("unit-tests",), ()))
        self.assertEqual(a.runs[0].result_ref, "results.json#T-1/unit-tests")
        self.assertIsNone(a.runs[0].run_id)  # the fake runner cannot report a process ID
        self.assertEqual(run_line(a, False), "실행 ID 미확인 · unit-tests 통과 (종료 코드 0) · Ran 4 tests · OK")
        self.assertIsNone(a.base)  # no base in this plan: nothing to compare, recorded as such
        text = handoff_path(ws.results).read_text(encoding="utf-8")
        self.assertNotIn("------", text)  # test output is referenced through results, not copied
        self.assertNotIn(str(ws.root), text)

    def test_real_runner_records_the_process_id(self):
        ws = Workspace(self)
        (ws.root / "tests" / "test_ok.py").write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n", encoding="utf-8")
        verify(ws.plan, ws.results, "T-1", "unit-tests", repo=ws.root, timeout=120)
        (a,) = read_attempts(ws.results)[0]
        self.assertRegex(a.runs[0].run_id, r"^pid:\d+$")
        self.assertEqual(a.runs[0].end.value, "exited")


class ResumeAcrossSessionsTest(unittest.TestCase):
    def test_another_session_continues_after_a_normal_end(self):
        ws = Workspace(self, plan=chain_plan())
        report = flow(ws, Scripted({}), max_tasks=1)
        self.assertEqual(report.stop.kind, StopKind.LIMIT)
        view = resume(ws)
        self.assertEqual(view.verdict(), "이어가기 가능 · 다음 T-2 · flow로 직접 재개")
        self.assertEqual(view.task("T-1").state, ResumeState.DONE)
        lines = r.task_lines(FileTaskSource(ws.plan, ws.results, ws.root).load())
        self.assertEqual(row(lines, "재개"), "이어가기 가능 · 다음 T-2 · flow로 직접 재개")
        self.assertIn("  다음 행동: flow로 T-2 진행", lines)
        second = Scripted({})
        report = flow(ws, second)
        self.assertEqual((tasks_called(second), report.stop.kind), (["T-2", "T-3"], StopKind.DONE))
        self.assertEqual(resume(ws).verdict(), "완료 · 다시 실행할 작업 없음")
        third = Scripted({})
        self.assertEqual((flow(ws, third).stop.kind, third.calls), (StopKind.DONE, []))

    def test_cut_off_session_blocks_a_duplicate_run(self):
        ws = Workspace(self, plan=chain_plan())
        cut_off(ws)
        view = resume(ws)
        t1 = view.task("T-1")
        self.assertEqual((t1.state, t1.reason), (ResumeState.CHECK_RUN, "프로그램 중단 (종료 기록 없음)"))
        self.assertEqual(t1.next_action, "pid:999 종료 확인 후 handoff --confirm-stopped T-1")
        script = Scripted({})
        report = flow(ws, script)
        self.assertEqual((report.stop.kind, report.stop.task_id, script.calls), (StopKind.UNRESOLVED, "T-1", []))
        with self.assertRaises(ClaimRefused) as ctx:
            verify(ws.plan, ws.results, "T-1", "unit-tests", repo=ws.root, runner_factory=lambda env: self.fail("ran"))
        self.assertEqual(ctx.exception.kind, ClaimRefused.UNRESOLVED)
        board = FileTaskSource(ws.plan, ws.results, ws.root).load()
        lines = r.task_lines(board)
        self.assertEqual(row(lines, "재개"), "실행 상태 확인 필요 · T-1 · 프로그램 중단 (종료 기록 없음)")
        self.assertEqual(row(lines, "다음 작업"), "보류 · T-1 실행 상태 확인 필요 (재개 참고)")
        self.assertIn("  실행 ID pid:999 · 종료 미확인", lines)
        self.assertIn(BlockKind.RUN, [b.kind for b in board.review.blockers])  # approval waits too

        with self.assertRaises(VerificationSetupError):
            confirm_stopped(ws.results, "T-1", " ")
        confirm_stopped(ws.results, "T-1", "pid:999 종료 확인")
        self.assertEqual(resume(ws).task("T-1").state, ResumeState.RESUMABLE)
        script = Scripted({})
        self.assertEqual((flow(ws, script).stop.kind, tasks_called(script)), (StopKind.DONE, ["T-1", "T-2", "T-3"]))
        with self.assertRaises(VerificationSetupError):  # nothing left to confirm
            confirm_stopped(ws.results, "T-1", "again")

    def test_two_sessions_cannot_take_the_same_task(self):
        ws = Workspace(self, plan=chain_plan())
        plan = inspect(ws.plan, ws.results).plan
        first = claim_task(ws.plan, ws.results, plan, "T-1", ("unit-tests",), repo=ws.root)
        try:
            with self.assertRaises(ClaimRefused) as ctx:
                claim_task(ws.plan, ws.results, plan, "T-1", ("unit-tests",), repo=ws.root)
            self.assertEqual(ctx.exception.kind, ClaimRefused.BUSY)
            script = Scripted({})
            report = flow(ws, script)
            self.assertEqual((report.stop.kind, script.calls), (StopKind.BUSY, []))
            self.assertEqual(resume(ws).task("T-1").state, ResumeState.RUNNING)
            lines = r.task_lines(FileTaskSource(ws.plan, ws.results).load())
            self.assertTrue(row(lines, "재개").startswith("다른 세션 실행 중 · T-1 · 세션 s-"))
            with self.assertRaises(ClaimRefused):  # cannot declare a live session stopped
                confirm_stopped(ws.results, "T-1", "x")
        finally:
            first.close()
        script = Scripted({})
        self.assertEqual(flow(ws, script).stop.kind, StopKind.DONE)
        self.assertEqual(len(read_attempts(ws.results)[0]), 4)  # first (closed, nothing ran) + T-1..T-3

    def test_code_and_plan_changes_after_an_attempt_need_recheck(self):
        ws = Workspace(self, plan=chain_plan())
        (ws.root / "tests" / "test_a.py").write_text("x = 1\n", encoding="utf-8")
        flow(ws, Scripted({}))
        (ws.root / "tests" / "test_a.py").write_text("x = 2\n", encoding="utf-8")
        view = resume(ws)
        t1 = view.task("T-1")
        self.assertEqual((t1.state, t1.since), (ResumeState.RECHECK, ("코드 변경",)))
        self.assertIn("기록 후 코드 변경", t1.reason)
        self.assertEqual(view.verdict(), "재확인 필요 · 다음 T-1 · flow로 직접 재개하면 다시 검증")
        script = Scripted({})
        flow(ws, script)
        self.assertEqual(tasks_called(script), ["T-1", "T-2", "T-3"])

        ws.plan.write_text(json.dumps(chain_plan(**{"T-2": task("T-2", "FR-06", deps=("T-1",), command="x")})),
                           encoding="utf-8")
        t2 = resume(ws).task("T-2")
        self.assertEqual((t2.state, t2.since), (ResumeState.RECHECK, ("검증 조건 변경",)))

    def test_unknown_end_is_held_until_confirmed_or_the_executor_says_so(self):
        ws = Workspace(self, plan=chain_plan())
        flow(ws, Scripted({"T-1": CommandTimeout("t")}))
        self.assertEqual(resume(ws).task("T-1").state, ResumeState.CHECK_RUN)
        for state, expected in ((ProbeState.UNKNOWN, ResumeState.CHECK_RUN),
                                (ProbeState.RUNNING, ResumeState.RUNNING),
                                (ProbeState.EXITED, ResumeState.RESUMABLE)):
            with self.subTest(state):
                probe = FakeWorkerProbe(state)
                self.assertEqual(resume(ws, probe).task("T-1").state, expected)
                self.assertEqual(probe.asked, [("T-1", None)])
        blocked = Scripted({})
        self.assertEqual(run_flow(ws.plan, ws.results, repo=ws.root, verify_fn=blocked,
                                  probe=FakeWorkerProbe(ProbeState.RUNNING)).stop.kind, StopKind.BUSY)
        script = Scripted({})
        report = run_flow(ws.plan, ws.results, repo=ws.root, verify_fn=script, probe=FakeWorkerProbe(ProbeState.EXITED))
        self.assertEqual((report.stop.kind, tasks_called(script)), (StopKind.DONE, ["T-1", "T-2", "T-3"]))
        first = read_attempts(ws.results)[0][0]
        self.assertEqual(first.resolved_note, "실행기 상태 조회로 종료 확인")

    def test_unreadable_record_blocks_and_is_kept(self):
        ws = Workspace(self, plan=chain_plan())
        handoff_path(ws.results).write_text("{oops", encoding="utf-8")
        script = Scripted({})
        report = flow(ws, script)
        self.assertEqual((report.stop.kind, script.calls), (StopKind.UNRESOLVED, []))
        with self.assertRaises(VerificationSetupError):
            verify(ws.plan, ws.results, "T-1", "unit-tests", repo=ws.root, runner_factory=lambda env: self.fail("ran"))
        self.assertEqual(handoff_path(ws.results).read_text(encoding="utf-8"), "{oops")
        self.assertTrue(row(r.task_lines(FileTaskSource(ws.plan, ws.results).load()), "재개")
                        .startswith("실행 상태 확인 필요 · 실행 기록을 읽을 수 없음"))

    def test_screen_only_reads(self):
        ws = Workspace(self, plan=chain_plan())
        for _ in range(3):
            FileTaskSource(ws.plan, ws.results, ws.root).load()
        self.assertFalse(handoff_path(ws.results).exists())
        flow(ws, Scripted({}), max_tasks=1)
        before = handoff_path(ws.results).read_bytes()
        FileTaskSource(ws.plan, ws.results, ws.root).load()
        self.assertEqual(handoff_path(ws.results).read_bytes(), before)


class CliTest(unittest.TestCase):
    def call(self, ws, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--tasks", str(ws.plan), "--results", str(ws.results), "--repo", str(ws.root), *extra])
        return code, out.getvalue()

    def test_show_and_confirm(self):
        ws = Workspace(self, plan=chain_plan())
        code, out = self.call(ws)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("재개: 이어가기 가능 · 다음 T-1 · flow로 직접 재개", out)
        cut_off(ws)
        code, out = self.call(ws)
        self.assertEqual(code, cli.EXIT_CHECK)
        self.assertIn("  T-1  실행 상태 확인 필요 · 프로그램 중단 (종료 기록 없음)", out)
        self.assertIn("       실행 ID pid:999 · 종료 미확인", out)
        self.assertIn("확인할 것: 실행 ID의 테스트 프로세스와 그 하위 프로세스가 모두 끝났는지", out)
        self.assertNotIn(str(ws.root), out)
        code, out = self.call(ws, "--confirm-stopped", "T-1", "--note", "프로세스 없음 확인")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("중지 확인 기록: T-1 · ", out)
        self.assertIn("재개: 이어가기 가능 · 다음 T-1", out)
        code, out = self.call(ws, "--confirm-stopped", "T-1", "--note", "다시")
        self.assertEqual(code, cli.EXIT_REFUSED_CONFIRM)

    def test_busy_exit_code(self):
        ws = Workspace(self, plan=chain_plan())
        plan = inspect(ws.plan, ws.results).plan
        with claim_task(ws.plan, ws.results, plan, "T-1", ("unit-tests",), repo=ws.root):
            code, out = self.call(ws)
        self.assertEqual(code, cli.EXIT_BUSY)
        self.assertIn("재개: 다른 세션 실행 중 · T-1", out)


SLOW_TEST = "import time, unittest\n\nclass T(unittest.TestCase):\n    def test_slow(self):\n        time.sleep({s})\n"


class RealSessionsTest(unittest.TestCase):
    """Separate Python processes as sessions, the real test runner, and a real kill."""

    def flow_proc(self, ws):
        return subprocess.Popen(
            [sys.executable, "-m", "orca_orchestrator.flow", "--tasks", str(ws.plan), "--results", str(ws.results),
             "--repo", str(ws.root), "--timeout", "120"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", env=SRC_ENV)

    def wait_for_run_id(self, ws, deadline=60):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            attempts, _ = read_attempts(ws.results)
            if attempts and attempts[-1].runs and attempts[-1].runs[-1].run_id:
                return attempts[-1].runs[-1].run_id
            time.sleep(0.1)
        self.fail("the first session never recorded a run")

    def plan_one_task(self, ws):
        ws.plan.write_text(json.dumps(chain_plan(**{"T-2": task("T-2", "FR-06", deps=("T-1",), checks=("manual",)),
                                                    "T-3": task("T-3", "FR-07", deps=("T-1",), checks=("manual",))})),
                           encoding="utf-8")

    def test_second_session_is_refused_while_the_first_runs(self):
        ws = Workspace(self)
        self.plan_one_task(ws)
        (ws.root / "tests" / "test_slow.py").write_text(SLOW_TEST.format(s=3), encoding="utf-8")
        first = self.flow_proc(ws)
        self.addCleanup(first.wait, 120)
        self.wait_for_run_id(ws)
        second = self.flow_proc(ws)
        out2 = second.communicate(timeout=120)[0]
        out1 = first.communicate(timeout=120)[0]
        self.assertEqual(second.returncode, 6, out2)
        self.assertIn("흐름 시작 안 함 · T-1 · 세션 s-", out2)
        self.assertEqual(first.returncode, 4, out1)  # T-1 done; T-2/T-3 need a check the flow cannot run
        attempts, _ = read_attempts(ws.results)
        self.assertEqual([(a.task, a.state) for a in attempts], [("T-1", AttemptState.ENDED)])

    def test_killed_session_then_new_session_is_held_until_confirmed(self):
        ws = Workspace(self)
        self.plan_one_task(ws)
        (ws.root / "tests" / "test_slow.py").write_text(SLOW_TEST.format(s=60), encoding="utf-8")
        first = self.flow_proc(ws)
        run_id = self.wait_for_run_id(ws)
        first.kill()  # the session dies without recording the end
        first.communicate(timeout=30)
        view = resume(ws)
        self.assertEqual((view.task("T-1").state, view.task("T-1").reason),
                         (ResumeState.CHECK_RUN, "프로그램 중단 (종료 기록 없음)"))
        second = self.flow_proc(ws)
        out2 = second.communicate(timeout=120)[0]
        self.assertEqual(second.returncode, 7, out2)
        self.assertEqual(len(read_attempts(ws.results)[0]), 1)  # nothing new started
        try:  # the orphaned test process is still ours to stop
            os.kill(int(run_id.split(":")[1]), signal.SIGTERM)
        except OSError:
            pass
        (ws.root / "tests" / "test_slow.py").write_text(SLOW_TEST.format(s=0), encoding="utf-8")
        confirm_stopped(ws.results, "T-1", f"{run_id} 종료 확인")
        third = self.flow_proc(ws)
        out3 = third.communicate(timeout=120)[0]
        self.assertEqual(third.returncode, 4, out3)
        self.assertEqual(resume(ws).task("T-1").state, ResumeState.DONE)


if __name__ == "__main__":
    unittest.main()
