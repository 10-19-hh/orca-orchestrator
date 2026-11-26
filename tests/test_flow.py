from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from orca_orchestrator import flow as cli
from orca_orchestrator.tasks import (
    PLAN_SCHEMA,
    RESULTS_SCHEMA,
    BoardState,
    FileTaskSource,
    Outcome,
    TaskState,
    WaitKind,
)
from orca_orchestrator.tasks.flow import (
    SELECTION_RULE,
    UNSUPPORTED_HINT,
    VERIFY_HINT,
    FlowStatus,
    StopKind,
    flow_status_path,
    inspect,
    read_flow_status,
    run_flow,
    trace_lines,
    write_flow_status,
)
from orca_orchestrator.tasks.verification import results_lock, verify
from orca_orchestrator.orca.runner import CommandTimeout
from orca_orchestrator.tui import render as r

from test_verify import FAIL_STDERR, NOW, OK_STDERR, PYTHON, ZERO_OK, FakeVerifyRunner, Workspace


def task(tid, req, deps=(), checks=("unit-tests",), command=None):
    data = {"id": tid, "title": f"{tid} 작업", "requirements": [req],
            "checks": [{"id": c, "description": f"{c} 통과", **({"command": command} if command else {})}
                       for c in checks]}
    if deps:
        data["depends_on"] = list(deps)
    return data


def chain_plan(**overrides):
    tasks = {
        "T-1": task("T-1", "FR-01"),
        "T-2": task("T-2", "FR-06", deps=("T-1",)),
        "T-3": task("T-3", "FR-07", deps=("T-2",)),
    }
    tasks.update(overrides)
    return {
        "schema": PLAN_SCHEMA,
        "requirements": [{"id": r_, "title": f"{r_} 요구사항", "acceptance": "a"} for r_ in ("FR-01", "FR-06", "FR-07")],
        "tasks": list(tasks.values()),
    }


class Scripted:
    """verify_fn for run_flow: real verify() with a fake test runner chosen per task."""

    def __init__(self, outcomes, default=(0, OK_STDERR), **verify_kwargs):
        self.outcomes = outcomes
        self.default = default
        self.calls = []
        self.verify_kwargs = verify_kwargs
        self.during = {}

    def __call__(self, plan, results, task_id, check_id, **kw):
        self.calls.append((task_id, check_id))
        if task_id in self.during:
            self.during.pop(task_id)()
        runner = FakeVerifyRunner(self.outcomes.get(task_id, self.default))
        return verify(plan, results, task_id, check_id, runner_factory=runner.factory, python=PYTHON,
                      now=lambda: NOW, **kw, **self.verify_kwargs)


def flow(ws, script, **kw):
    return run_flow(ws.plan, ws.results, repo=ws.root, verify_fn=script, **kw)


def tasks_called(script):
    return [t for t, _ in script.calls]


class OrderedFlowTest(unittest.TestCase):
    def test_prerequisites_complete_in_order(self):
        ws = Workspace(self, plan=chain_plan())
        script = Scripted({})
        report = flow(ws, script)
        self.assertEqual(tasks_called(script), ["T-1", "T-2", "T-3"])
        self.assertEqual((report.stop.kind, report.verified), (StopKind.DONE, ["T-1", "T-2", "T-3"]))
        self.assertIn("최종 사용자 승인 대기", report.stop.message)
        stored = ws.stored()
        self.assertEqual([(s.task_id, s.outcome) for s in stored],
                         [("T-1", Outcome.PASSED), ("T-2", Outcome.PASSED), ("T-3", Outcome.PASSED)])
        self.assertTrue(all(s.check_digest for s in stored))
        status = read_flow_status(flow_status_path(ws.results))
        self.assertEqual((status.state, status.last.kind, status.verified), ("stopped", StopKind.DONE, ("T-1", "T-2", "T-3")))
        board = FileTaskSource(ws.plan, ws.results).load()
        self.assertTrue(board.progress.all_verified)
        self.assertEqual(r.approval_text(board), "대기 · 로컬 검증 모두 통과 · 사용자 최종 승인 필요 (자동 승인 없음)")
        self.assertEqual(r.next_text(board), "없음 · 모든 작업 로컬 검증 완료")

    def test_selection_follows_plan_order_and_explains(self):
        plan = chain_plan()
        plan["tasks"] = [task("B", "FR-01"), task("A", "FR-01"), task("C", "FR-06", deps=("A",))]
        ws = Workspace(self, plan=plan)
        view = inspect(ws.plan, ws.results)
        self.assertEqual(view.next.task_id, "B")  # plan order, not ID order
        self.assertEqual(view.next.reason, "선행 작업 없음 · unit-tests 결과 없음")
        self.assertEqual([b.task_id for b in view.blockers], ["C"])
        self.assertEqual(view.blockers[0].reason, "선행 작업 A 검증 결과 없음")
        script = Scripted({})
        flow(ws, script)
        self.assertEqual(tasks_called(script), ["B", "A", "C"])
        self.assertEqual(inspect(ws.plan, ws.results).next, None)

    def test_same_input_gives_same_choice(self):
        ws = Workspace(self, plan=chain_plan())
        first, second = inspect(ws.plan, ws.results), inspect(ws.plan, ws.results)
        self.assertEqual((first.next, first.blockers), (second.next, second.blockers))


class FailureStopsFlowTest(unittest.TestCase):
    def test_prerequisite_failure_blocks_successor(self):
        ws = Workspace(self, plan=chain_plan())
        script = Scripted({"T-2": (1, FAIL_STDERR)})
        report = flow(ws, script)
        self.assertEqual(tasks_called(script), ["T-1", "T-2"])
        self.assertEqual((report.stop.kind, report.stop.task_id, report.stop.check_id),
                         (StopKind.FAILED, "T-2", "unit-tests"))
        self.assertIn("종료 코드 1", report.stop.message)
        progress = inspect(ws.plan, ws.results).progress
        self.assertEqual(progress.task("T-2").state, TaskState.FAILED)
        self.assertEqual([(w.kind, w.task_id) for w in progress.task("T-3").waiting_on],
                         [(WaitKind.PREREQUISITE_FAILED, "T-2")])

        again = Scripted({})
        report = flow(ws, again)
        self.assertEqual(again.calls, [])  # the failed check is never re-run by the flow
        self.assertEqual(report.stop.kind, StopKind.WAITING)
        view = inspect(ws.plan, ws.results)
        self.assertEqual([(b.task_id, b.reason, b.hint) for b in view.blockers], [
            ("T-2", "unit-tests 검증 실패", VERIFY_HINT),
            ("T-3", "선행 작업 T-2 검증 실패", ""),
        ])

    def test_fix_and_verify_then_flow_continues(self):
        ws = Workspace(self, plan=chain_plan())
        flow(ws, Scripted({"T-2": (1, FAIL_STDERR)}))
        verify(ws.plan, ws.results, "T-2", "unit-tests", repo=ws.root,
               runner_factory=FakeVerifyRunner((0, OK_STDERR)).factory, python=PYTHON, now=lambda: NOW)
        script = Scripted({})
        report = flow(ws, script)
        self.assertEqual((tasks_called(script), report.stop.kind), (["T-3"], StopKind.DONE))

    def test_no_tests_timeout_and_unsaved_stop_without_recording(self):
        cases = {
            "no tests": (Scripted({"T-2": (0, ZERO_OK)}), StopKind.NOT_COMPLETED, "실행된 테스트가 없음"),
            "timeout": (Scripted({"T-2": CommandTimeout("t")}), StopKind.NOT_COMPLETED, "제한 시간"),
        }
        for name, (script, kind, text) in cases.items():
            with self.subTest(name):
                ws = Workspace(self, plan=chain_plan())
                report = flow(ws, script)
                self.assertEqual((report.stop.kind, report.stop.task_id), (kind, "T-2"))
                self.assertIn(text, report.stop.message)
                self.assertEqual([s.task_id for s in ws.stored()], ["T-1"])
                self.assertEqual(tasks_called(script), ["T-1", "T-2"])

        ws = Workspace(self, plan=chain_plan())
        script = Scripted({}, lock_timeout=0.2)
        script.during["T-2"] = lambda: setattr(self, "_held", results_lock(ws.results)) or self._held.__enter__()
        report = flow(ws, script)
        self._held.__exit__(None, None, None)
        self.assertEqual((report.stop.kind, report.stop.task_id), (StopKind.NOT_RECORDED, "T-2"))
        self.assertIn("테스트는 끝났지만(통과)", report.stop.message)
        self.assertEqual([s.task_id for s in ws.stored()], ["T-1"])


class ResumeTest(unittest.TestCase):
    def test_stop_then_resume_without_rerunning_done_tasks(self):
        ws = Workspace(self, plan=chain_plan())
        first = Scripted({})
        report = flow(ws, first, max_tasks=1)
        self.assertEqual((tasks_called(first), report.stop.kind), (["T-1"], StopKind.LIMIT))
        self.assertIn("다음 작업 T-2", report.stop.message)
        second = Scripted({})
        report = flow(ws, second)
        self.assertEqual((tasks_called(second), report.stop.kind), (["T-2", "T-3"], StopKind.DONE))

    def test_resume_after_interrupted_run(self):
        ws = Workspace(self, plan=chain_plan())
        flow(ws, Scripted({"T-2": CommandTimeout("t")}))
        # A crash can leave a "running" status behind; the next run simply replaces it.
        write_flow_status(flow_status_path(ws.results),
                          FlowStatus("running", "2026-11-26T11:00:00Z", ("T-2", "unit-tests", "2026-11-26T11:00:00Z")))
        board = FileTaskSource(ws.plan, ws.results).load()
        self.assertIn("실행 중 · T-2/unit-tests", r.flow_text(board))
        script = Scripted({})
        report = flow(ws, script)
        self.assertEqual((tasks_called(script), report.stop.kind), (["T-2", "T-3"], StopKind.DONE))
        self.assertEqual(read_flow_status(flow_status_path(ws.results)).state, "stopped")

    def test_plan_change_marks_recheck_and_reruns_only_that_task(self):
        ws = Workspace(self, plan=chain_plan())
        flow(ws, Scripted({}))
        changed = chain_plan(**{"T-2": task("T-2", "FR-06", deps=("T-1",), command="python -m unittest -v")})
        ws.plan.write_text(json.dumps(changed), encoding="utf-8")
        view = inspect(ws.plan, ws.results)
        t2, t3 = view.progress.task("T-2"), view.progress.task("T-3")
        self.assertEqual(t2.state, TaskState.RECHECK)
        self.assertEqual(t3.state, TaskState.VERIFIED)  # its own check is unchanged and still current
        self.assertEqual(view.next.task_id, "T-2")
        self.assertIn("unit-tests 재확인 필요 (검증 조건 변경)", view.next.reason)
        self.assertFalse(view.progress.all_verified)
        script = Scripted({})
        report = flow(ws, script)
        self.assertEqual((tasks_called(script), report.stop.kind), (["T-2"], StopKind.DONE))

    def test_results_without_digest(self):
        ws = Workspace(self, plan=chain_plan(), results={"schema": RESULTS_SCHEMA, "results": [
            {"task": "T-1", "check": "unit-tests", "outcome": "passed", "exit_code": 0}]})
        view = inspect(ws.plan, ws.results)
        self.assertEqual(view.progress.task("T-1").state, TaskState.RECHECK)
        self.assertIn("unit-tests 재확인 필요 (계획 정보 없는 결과)", view.next.reason)
        script = Scripted({})
        flow(ws, script, max_tasks=1)
        self.assertEqual(tasks_called(script), ["T-1"])  # a stale pass is re-checked

        ws = Workspace(self, plan=chain_plan(), results={"schema": RESULTS_SCHEMA, "results": [
            {"task": "T-1", "check": "unit-tests", "outcome": "failed", "exit_code": 1}]})
        script = Scripted({})
        report = flow(ws, script)
        self.assertEqual((script.calls, report.stop.kind), ([], StopKind.WAITING))  # a stale failure is not
        self.assertEqual(inspect(ws.plan, ws.results).blockers[0].reason, "unit-tests 이전 실패 결과 재확인 필요")

    def test_existing_and_concurrent_results_are_kept(self):
        ws = Workspace(self, plan=chain_plan())
        script = Scripted({})
        # While T-1's tests run, another process records T-3 (e.g. a manual verify).
        script.during["T-1"] = lambda: verify(
            ws.plan, ws.results, "T-3", "unit-tests", repo=ws.root,
            runner_factory=FakeVerifyRunner((0, OK_STDERR)).factory, python=PYTHON, now=lambda: NOW)
        report = flow(ws, script)
        self.assertEqual(tasks_called(script), ["T-1", "T-2"])  # T-3 already had a current result
        self.assertEqual(sorted(s.task_id for s in ws.stored()), ["T-1", "T-2", "T-3"])
        self.assertEqual(report.stop.kind, StopKind.DONE)


class BlockedFlowTest(unittest.TestCase):
    def test_unsupported_check_waits_with_reason(self):
        ws = Workspace(self, plan=chain_plan(**{"T-2": task("T-2", "FR-06", deps=("T-1",),
                                                              checks=("unit-tests", "screen-review"))}))
        script = Scripted({})
        report = flow(ws, script)
        self.assertEqual(tasks_called(script), ["T-1", "T-2"])  # T-2's runnable check still runs
        self.assertEqual((report.stop.kind, report.stop.task_id), (StopKind.WAITING, "T-2"))
        view = inspect(ws.plan, ws.results)
        self.assertEqual((view.blockers[0].reason, view.blockers[0].hint),
                         ("자동 실행할 수 없는 검증 조건 screen-review", UNSUPPORTED_HINT))
        self.assertEqual(view.blockers[1].reason, "선행 작업 T-2 검증 미완료 (1/2 통과)")

    def test_invalid_plan_is_refused(self):
        plan = chain_plan(**{"T-1": task("T-1", "FR-01", deps=("T-3",))})
        ws = Workspace(self, plan=plan)
        script = Scripted({})
        report = flow(ws, script)
        self.assertEqual((script.calls, report.stop.kind), ([], StopKind.REFUSED))

    def test_plan_command_text_never_runs(self):
        ws = Workspace(self, plan=chain_plan(**{"T-1": task("T-1", "FR-01", command="echo hacked")}))
        seen = []

        def capture(plan, results, task_id, check_id, **kw):
            runner = FakeVerifyRunner((0, OK_STDERR))
            run = verify(plan, results, task_id, check_id, runner_factory=runner.factory, python=PYTHON,
                         now=lambda: NOW, **kw)
            seen.extend(argv for argv, _, _ in runner.calls)
            return run

        run_flow(ws.plan, ws.results, repo=ws.root, verify_fn=capture)
        self.assertTrue(seen)
        self.assertTrue(all(argv == [PYTHON, "-m", "unittest", "discover", "-s", "tests"] for argv in seen))


class TraceAndScreenTest(unittest.TestCase):
    def test_trace_from_requirement_to_result(self):
        ws = Workspace(self, plan=chain_plan())
        flow(ws, Scripted({"T-2": (1, FAIL_STDERR)}))
        lines = trace_lines(inspect(ws.plan, ws.results).progress)
        self.assertEqual(lines[0], "FR-01 충족 · FR-01 요구사항")
        self.assertEqual(lines[1], "  T-1 완료 · T-1 작업")
        self.assertTrue(lines[2].startswith("    unit-tests 통과 · 종료 코드 0 · python -m unittest discover -s tests · "
                                            "2026-11-26T11:00:00Z · Ran 4 tests · OK"))
        self.assertEqual(lines[4], "  T-2 검증 실패 · T-2 작업")
        self.assertIn("unit-tests 실패 · 종료 코드 1", lines[5])
        self.assertEqual(lines[6:], ["FR-07 미충족 · FR-07 요구사항", "  T-3 대기 · T-3 작업", "    unit-tests 결과 없음"])

    def test_screen_shows_next_flow_and_approval(self):
        ws = Workspace(self, plan=chain_plan())
        flow(ws, Scripted({"T-2": (1, FAIL_STDERR)}))
        lines = r.task_lines(FileTaskSource(ws.plan, ws.results).load())
        rows = {label: line[len(label):].lstrip() for line in lines for label in ("다음 작업", "흐름", "최종 승인")
                if line.startswith(label)}
        self.assertEqual(rows["다음 작업"], "없음 · T-2: unit-tests 검증 실패 (외 1건)")
        self.assertTrue(rows["흐름"].startswith("중단 · T-2/unit-tests · "))
        self.assertIn("검증 실패 (종료 코드 1)", rows["흐름"])
        self.assertEqual(rows["최종 승인"], "대기 · 로컬 검증 완료 1/3 작업 · 사용자 승인은 모든 검증 후")

    def test_screen_marks_recheck(self):
        ws = Workspace(self, plan=chain_plan())
        flow(ws, Scripted({}))
        ws.plan.write_text(json.dumps(chain_plan(**{"T-1": task("T-1", "FR-01", command="x")})), encoding="utf-8")
        lines = r.task_lines(FileTaskSource(ws.plan, ws.results).load())
        self.assertIn("  T-1  재확인     T-1 작업 · FR-01 · 선행 작업 없음 · 재확인 필요: unit-tests (검증 조건 변경)", lines)
        self.assertIn("다음 작업   T-1 · 선행 작업 없음 · unit-tests 재확인 필요 (검증 조건 변경)", lines)

    def test_unreadable_flow_status_is_reported_not_fatal(self):
        ws = Workspace(self, plan=chain_plan())
        flow(ws, Scripted({}), max_tasks=1)
        flow_status_path(ws.results).write_text("{oops", encoding="utf-8")
        board = FileTaskSource(ws.plan, ws.results).load()
        self.assertEqual(board.state, BoardState.OK)
        self.assertTrue(r.flow_text(board).startswith("조회 불가 · results.flow.json"))

    def test_status_file_name(self):
        self.assertEqual(flow_status_path(Path("x/results.json")).name, "results.flow.json")
        self.assertEqual(flow_status_path(Path("x/results")).name, "results.flow.json")


class CliTest(unittest.TestCase):
    def call(self, ws, script, *extra):
        def patched(*args, **kwargs):
            return run_flow(*args, verify_fn=script, **kwargs)

        out = io.StringIO()
        with mock.patch.object(cli, "run_flow", patched), redirect_stdout(out):
            code = cli.main(["--tasks", str(ws.plan), "--results", str(ws.results), "--repo", str(ws.root), *extra])
        return code, out.getvalue()

    def test_exit_codes(self):
        cases = [
            ({}, (), 0, "흐름 끝남 · 모든 작업의 로컬 검증 통과 · 최종 사용자 승인 대기"),
            ({"T-2": (1, FAIL_STDERR)}, (), 1, "흐름 중단 · T-2/unit-tests · 검증 실패 (종료 코드 1)"),
            ({"T-1": (0, ZERO_OK)}, (), 3, "흐름 중단 · T-1/unit-tests · 실행된 테스트가 없음"),
            ({}, ("--max-tasks", "2"), 5, "흐름 멈춤 · T-3 · 지정한 작업 수(2)만큼 실행 · 다음 작업 T-3"),
        ]
        for outcomes, extra, code, text in cases:
            with self.subTest(code=code):
                ws = Workspace(self, plan=chain_plan())
                got, out = self.call(ws, Scripted(outcomes), *extra)
                self.assertEqual(got, code, out)
                self.assertIn(text, out)
                self.assertIn(f"선택 규칙: {SELECTION_RULE}", out)
                self.assertIn(cli.APPROVAL_LINE, out)
                self.assertIn("흐름 상태: results.flow.json", out)
                self.assertNotIn(str(ws.root), out)

    def test_output_explains_each_step(self):
        ws = Workspace(self, plan=chain_plan())
        _, out = self.call(ws, Scripted({"T-2": (1, FAIL_STDERR)}))
        self.assertIn("[1] 선택 T-1 · 선행 작업 없음 · unit-tests 결과 없음", out)
        self.assertIn("    실행 T-1/unit-tests → python -m unittest discover -s tests (한 번, 제한 600초)", out)
        self.assertIn("    통과 · 종료 코드 0 · Ran 4 tests · OK · 기록 results.json", out)
        self.assertIn("[2] 선택 T-2 · 선행 T-1 완료 · unit-tests 결과 없음", out)
        self.assertIn("  T-2: unit-tests 검증 실패 — " + VERIFY_HINT, out)
        self.assertIn("  T-3: 선행 작업 T-2 검증 실패", out)
        self.assertIn("추적 (요구사항 → 작업 → 검증 조건 → 결과):", out)

    def test_waiting_exit_code_and_refusal(self):
        ws = Workspace(self, plan=chain_plan())
        self.call(ws, Scripted({"T-2": (1, FAIL_STDERR)}))
        code, out = self.call(ws, Scripted({}))
        self.assertEqual(code, 4)
        self.assertIn("흐름 멈춤 · T-2 · 선택할 수 있는 작업 없음 · T-2: unit-tests 검증 실패", out)

        bad = Workspace(self, plan=chain_plan(**{"T-1": task("T-1", "FR-01", deps=("T-3",))}))
        code, out = self.call(bad, Scripted({}))
        self.assertEqual(code, 2)
        self.assertIn("  · 순환 의존성: T-1 → T-3 → T-2 → T-1", out)

    def test_dry_run_changes_nothing(self):
        ws = Workspace(self, plan=chain_plan())
        script = Scripted({})
        code, out = self.call(ws, script, "--dry-run")
        self.assertEqual((code, script.calls), (0, []))
        self.assertFalse(ws.results.exists())
        self.assertFalse(flow_status_path(ws.results).exists())
        self.assertIn("미리 보기: 아무것도 실행하거나 기록하지 않습니다.", out)
        self.assertIn("다음 작업: T-1 · 선행 작업 없음 · unit-tests 결과 없음", out)

    def test_argument_checks(self):
        for argv in ([], ["--tasks", "p", "--results", "r", "--max-tasks", "0"],
                     ["--tasks", "p", "--results", "r", "--timeout", "0"]):
            with self.subTest(argv):
                with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                    with self.assertRaises(SystemExit):
                        cli.main(argv)


class RealProcessFlowTest(unittest.TestCase):
    """The real unit-test command against a small throwaway repository."""

    def test_two_tasks_then_screen(self):
        ws = Workspace(self, plan=chain_plan(**{"T-3": task("T-3", "FR-07", deps=("T-2",), checks=("screen-review",))}))
        (ws.root / "tests" / "test_ok.py").write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
            encoding="utf-8")
        report = run_flow(ws.plan, ws.results, repo=ws.root, timeout=120)
        self.assertEqual((report.stop.kind, report.verified), (StopKind.WAITING, ["T-1", "T-2"]))
        self.assertEqual([run.summary for run in report.runs], ["Ran 1 test · OK", "Ran 1 test · OK"])
        board = FileTaskSource(ws.plan, ws.results).load()
        self.assertEqual([t.state for t in board.progress.tasks],
                         [TaskState.VERIFIED, TaskState.VERIFIED, TaskState.READY])
        self.assertEqual(r.next_text(board), "없음 · T-3: 자동 실행할 수 없는 검증 조건 screen-review")


if __name__ == "__main__":
    unittest.main()
