"""The console's connection layer (no UI framework): what it offers, what it runs, what it records."""

from __future__ import annotations

import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from orca_orchestrator import __main__ as cli
from orca_orchestrator.console import view as v
from orca_orchestrator.console.bridge import (
    Availability,
    DEMO_NOTE,
    WORKER_NOTE,
    Console,
    RunHandle,
    StoppableRunner,
    StopRequested,
    Target,
)
from orca_orchestrator.orca import DispatchGuard, Reason
from orca_orchestrator.orca.dispatch import Provider
from orca_orchestrator.orca.runner import CommandResult
from orca_orchestrator.tasks import DemoTaskSource, FileTaskSource, TaskState
from orca_orchestrator.tasks.handoff import AttemptState, ResumeState, RunEnd, read_attempts
from orca_orchestrator.tasks.locks import hold
from orca_orchestrator.tasks.paths import approval_path, run_lock_path
from orca_orchestrator.tasks.review import ApprovalState, review_basis
from orca_orchestrator.tui.demo import demo_adapter
from orca_orchestrator.tui.snapshot import collect_snapshot

from test_review import GIT, Repo
from test_verify import FAIL_STDERR, OK_STDERR

SLOW_TEST = "import time, unittest\n\nclass T(unittest.TestCase):\n    def test_slow(self):\n        time.sleep({s})\n"


class ScriptedRunner:
    """Stands in for StoppableRunner: fake test output, optionally held open until released or stopped."""

    reports_start = True

    def __init__(self, outcome=(0, OK_STDERR), gate: threading.Event | None = None, during=None):
        self.outcome = outcome
        self.gate = gate
        self.during = during
        self.calls = 0
        self.stop: threading.Event | None = None
        self.on_pid = None

    def factory(self, env, stop, handle, on_pid):
        self.stop, self.on_pid = stop, on_pid
        return self

    def run(self, argv, *, timeout, cwd=None, on_start=None):
        self.calls += 1
        if on_start is not None:
            on_start(4242)
        if self.on_pid is not None:
            self.on_pid(4242)
        if self.during is not None:
            self.during()
        if self.gate is not None:
            while not self.gate.wait(0.02):
                if self.stop.is_set():
                    raise StopRequested("사용자가 중지함")
        code, stderr = self.outcome
        return CommandResult(tuple(argv), code, "", stderr)


def real_console(repo: Repo, runner=None, **kw) -> Console:
    adapter, _ = demo_adapter("connected")  # Orca part from the public fixture: no process
    source = FileTaskSource(repo.plan, repo.results, repo.root)
    collect = lambda: collect_snapshot(adapter, DispatchGuard(), tasks=source)  # noqa: E731
    target = Target(plan=repo.plan, results=repo.results, repo=repo.root,
                    args=("--tasks", str(repo.plan), "--results", str(repo.results), "--repo", str(repo.root)))
    if runner is not None:
        kw.setdefault("runner_factory", runner.factory)
    return Console(target, collect, **kw)


def demo_console(variant="passed", scenario="connected") -> Console:
    adapter, _ = demo_adapter(scenario)
    tasks = DemoTaskSource(variant)
    collect = lambda: collect_snapshot(adapter, DispatchGuard(), demo=scenario, tasks=tasks)  # noqa: E731
    return Console(Target(demo=scenario, demo_results=variant), collect)


def events():
    seen = []
    return seen, (lambda kind, payload: seen.append((kind, payload)))


class DemoAvailabilityTest(unittest.TestCase):
    def test_demo_offers_nothing_that_records(self):
        console = demo_console("passed")
        snap = console.collect()
        for av in (console.verify_availability(snap, "T-2"), console.flow_availability(snap),
                   console.approve_availability(snap), console.hold_availability(snap)):
            self.assertEqual((av.enabled, av.reason), (False, DEMO_NOTE))

    def test_demo_preflight_runs_no_process_and_sends_nothing(self):
        console = demo_console("passed", "unbound")
        with mock.patch("subprocess.run", side_effect=AssertionError("process started")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("process started")):
            out = console.preflight(Provider.CLAUDE)
        self.assertEqual(out.status, "blocked")
        self.assertEqual(out.title, "판정: 전송 차단 · 아무것도 보내지 않았습니다.")
        text = "\n".join(out.lines)
        self.assertIn("바인딩      없음", text)
        self.assertIn("구성만 함 · 이 빌드에는 worker-start를 보내는 경로가 없음", text)


@unittest.skipUnless(GIT, "git is not installed")
class AvailabilityTest(unittest.TestCase):
    def test_without_results_nothing_runs(self):
        repo = Repo(self)
        console = Console(Target(plan=repo.plan, repo=repo.root), real_console(repo).collect)
        snap = console.collect()
        self.assertIn("--results", console.verify_availability(snap, "T-1").reason)

    def test_verify_and_flow_follow_the_existing_judgment(self):
        repo = Repo(self)
        console = real_console(repo)
        snap = console.collect()
        self.assertTrue(console.verify_availability(snap, "T-1").enabled)
        flow = console.flow_availability(snap)
        self.assertEqual((flow.enabled, flow.reason), (True, "흐름 시작 가능 · 다음 작업 T-1"))
        self.assertFalse(console.approve_availability(snap).enabled)
        self.assertIn("보류 사유", console.approve_availability(snap).reason)
        self.assertTrue(console.hold_availability(snap).enabled)
        busy = console.verify_availability(snap, "T-1", busy="검증 T-1/unit-tests")
        self.assertEqual(busy.reason, "검증 T-1/unit-tests 진행 중 · 끝난 뒤 다시 시도")
        stale = console.flow_availability(snap, stale=True)
        self.assertIn("최신이 아님", stale.reason)

    def test_plan_shows_target_scope_and_what_stays_untouched(self):
        repo = Repo(self)
        console = real_console(repo)
        plan = console.verify_plan(console.collect(), "T-2")
        self.assertEqual((plan.task_id, plan.check_id, plan.command), ("T-2", "unit-tests",
                                                                        "python -m unittest discover -s tests"))
        self.assertEqual(plan.repo_name, "repo")
        self.assertTrue(plan.notes[0].startswith("선행 작업 미완료 (선행 작업 T-1 검증 결과 없음)"))
        self.assertIn("작업 정의의 command 문자열은 실행하지 않음", plan.notes)


@unittest.skipUnless(GIT, "git is not installed")
class VerifyTest(unittest.TestCase):
    def test_verify_records_and_reports_progress(self):
        repo = Repo(self)
        runner = ScriptedRunner()
        console = real_console(repo, runner)
        seen, progress = events()
        out = console.run_verify("T-1", "unit-tests", threading.Event(), progress)
        self.assertEqual(out.status, "ok")
        self.assertTrue(out.title.startswith("검증 통과 · T-1/unit-tests · 종료 코드 0 · Ran 4 tests · OK"))
        self.assertEqual([k for k, _ in seen], ["start", "pid", "result"])
        board = repo.board()
        self.assertEqual(board.progress.task("T-1").state, TaskState.VERIFIED)
        attempt = read_attempts(repo.results)[0][-1]
        self.assertEqual((attempt.state, attempt.runs[-1].run_id), (AttemptState.ENDED, "pid:4242"))

    def test_failure_shows_output_and_no_automatic_rerun(self):
        repo = Repo(self)
        runner = ScriptedRunner((1, FAIL_STDERR))
        out = real_console(repo, runner).run_verify("T-1", "unit-tests", threading.Event(), events()[1])
        self.assertEqual(out.status, "failed")
        self.assertIn("원인을 고친 뒤 직접 다시 검증하세요 (자동 재실행 없음).", out.lines)
        self.assertEqual(runner.calls, 1)

    def test_another_session_holding_the_task_is_refused_and_nothing_runs(self):
        repo = Repo(self)
        runner = ScriptedRunner()
        held = hold(run_lock_path(repo.results, "T-1"))
        self.addCleanup(held.release)
        out = real_console(repo, runner).run_verify("T-1", "unit-tests", threading.Event(), events()[1])
        self.assertEqual(out.status, "busy")
        self.assertEqual(runner.calls, 0)
        self.assertFalse(repo.results.exists())

    def test_two_presses_in_one_console_cannot_run_the_task_twice(self):
        repo = Repo(self)
        gate = threading.Event()
        runner = ScriptedRunner(gate=gate)
        console = real_console(repo, runner)
        results = []
        first = threading.Thread(target=lambda: results.append(
            console.run_verify("T-1", "unit-tests", threading.Event(), events()[1])))
        first.start()
        deadline = time.monotonic() + 10
        while runner.calls == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        second = console.run_verify("T-1", "unit-tests", threading.Event(), events()[1])
        gate.set()
        first.join(10)
        self.assertEqual(second.status, "busy")  # the run lock, not the screen, refused it
        self.assertEqual((results[0].status, runner.calls), ("ok", 1))

    def test_result_that_cannot_be_saved_is_reported_with_recovery(self):
        repo = Repo(self)
        runner = ScriptedRunner(during=lambda: repo.results.write_text("{broken", encoding="utf-8"))
        out = real_console(repo, runner).run_verify("T-1", "unit-tests", threading.Event(), events()[1])
        self.assertEqual(out.status, "not_recorded")
        self.assertTrue(out.title.startswith("기록 저장 실패 · T-1/unit-tests · 테스트는 끝났지만(통과, 종료 코드 0)"))
        self.assertIn("결과를 기록하지 않았습니다. 기존 결과 파일은 그대로입니다. (results.json)", out.lines)
        self.assertEqual(repo.results.read_text(encoding="utf-8"), "{broken")

    def test_stop_before_the_process_starts_is_recorded_as_not_started(self):
        repo = Repo(self)
        stop = threading.Event()
        stop.set()
        out = real_console(repo).run_verify("T-1", "unit-tests", stop, events()[1])
        self.assertEqual(out.status, "not_completed")
        run = read_attempts(repo.results)[0][-1].runs[-1]
        self.assertEqual(run.end, RunEnd.NOT_STARTED)
        console = real_console(repo)
        self.assertTrue(console.verify_availability(console.collect(), "T-1").enabled)

    def test_stopping_a_real_run_kills_it_and_keeps_the_end_unconfirmed(self):
        repo = Repo(self)
        repo.write("tests/test_slow.py", SLOW_TEST.format(s=30))
        console = real_console(repo)
        stop = threading.Event()
        pids, outcome = [], []

        def progress(kind, payload):
            if kind == "pid":
                pids.append(payload)
                stop.set()

        worker = threading.Thread(target=lambda: outcome.append(console.run_verify("T-1", "unit-tests", stop, progress)))
        started = time.monotonic()
        worker.start()
        worker.join(60)
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(outcome[0].status, "stopped")
        self.assertIn("orca_orchestrator.handoff --tasks", outcome[0].lines[-1])
        self.assertIn("--confirm-stopped T-1 --note", outcome[0].lines[-1])
        with self.assertRaises(OSError):
            os.kill(pids[0], 0)  # the test process is gone
        attempt = read_attempts(repo.results)[0][-1]
        self.assertEqual((attempt.state, attempt.runs[-1].end), (AttemptState.UNKNOWN, RunEnd.UNKNOWN))
        self.assertIn("중단됨 (StopRequested) · 테스트 프로세스 종료 미확인", attempt.runs[-1].message)
        snap = console.collect()
        self.assertEqual(snap.tasks.resume.task("T-1").state, ResumeState.CHECK_RUN)
        self.assertFalse(console.verify_availability(snap, "T-1").enabled)
        self.assertFalse(console.flow_availability(snap).enabled)
        self.assertFalse(repo.results.exists())


@unittest.skipUnless(GIT, "git is not installed")
class FlowTest(unittest.TestCase):
    def test_flow_runs_in_order_and_never_approves(self):
        repo = Repo(self)
        runner = ScriptedRunner()
        seen, progress = events()
        out = real_console(repo, runner).run_flow(threading.Event(), progress)
        self.assertEqual(out.status, "ok")
        self.assertTrue(out.title.startswith("흐름 끝남 · 모든 작업의 로컬 검증 통과"))
        self.assertEqual([p.task_id for k, p in seen if k == "select"], ["T-1", "T-2"])
        self.assertFalse(repo.approval_exists())

    def test_stop_between_checks_starts_nothing_more(self):
        repo = Repo(self)
        stop = threading.Event()

        def progress(kind, payload):
            if kind == "result":
                stop.set()

        runner = ScriptedRunner()
        out = real_console(repo, runner).run_flow(stop, progress)
        self.assertEqual(out.status, "stopped")
        self.assertIn("사용자가 흐름을 멈춤 · 이 검증은 시작하지 않음", out.title)
        self.assertEqual(runner.calls, 1)
        resume = repo.board().resume
        self.assertIsNone(resume.blocking)  # nothing was cut off: T-2 never started


@unittest.skipUnless(GIT, "git is not installed")
class ApprovalTest(unittest.TestCase):
    def verified(self):
        repo = Repo(self)
        repo.verify_all()
        return repo, real_console(repo)

    def test_approve_what_was_reviewed(self):
        repo, console = self.verified()
        snap = console.collect()
        out = console.approve(review_basis(snap.tasks.review), False, "확인함")
        self.assertEqual(out.status, "ok")
        self.assertIn(WORKER_NOTE, out.lines)
        self.assertEqual(repo.review().state, ApprovalState.APPROVED)

    def test_change_after_review_refuses_and_writes_nothing(self):
        cases = {
            "검증 대상 코드": lambda repo: repo.append("src/calc/ops.py", "# later\n"),
            "작업 정의": lambda repo: repo.set_plan({**json.loads(repo.plan.read_text(encoding="utf-8")),
                                                  "requirements": [{"id": "R-1", "title": "계산", "acceptance": "b"},
                                                                   {"id": "R-2", "title": "테스트", "acceptance": "a"}]}),
            "변경 파일": lambda repo: repo.write("pyproject.toml", "[project]\ndependencies = ['x']\n"),
            "검증 결과": lambda repo: repo.verify("T-2", (1, FAIL_STDERR)),
        }
        for label, change in cases.items():
            with self.subTest(label):
                repo, console = self.verified()
                if label == "변경 파일":
                    repo.write("pyproject.toml", "[project]\n")
                    snap_reviewed = console.collect()
                else:
                    snap_reviewed = console.collect()
                basis = review_basis(snap_reviewed.tasks.review)
                change(repo)
                out = console.approve(basis, True, None)
                self.assertEqual(out.status, "refused")
                self.assertIn("검토한 뒤 바뀜", out.title)
                self.assertIn(label, out.title)
                self.assertFalse(repo.approval_exists())

    def test_unlinked_changes_need_the_explicit_confirmation(self):
        repo, console = self.verified()
        repo.write("notes.txt", "x\n")
        basis = review_basis(console.collect().tasks.review)
        self.assertEqual(basis.unlinked, ("notes.txt",))
        refused = console.approve(basis, False, None)
        self.assertIn("--confirm-unlinked", refused.title)
        self.assertEqual(console.approve(basis, True, None).status, "ok")

    def test_approval_never_clears_the_worker_gate(self):
        repo, console = self.verified()
        console.approve(review_basis(console.collect().tasks.review), False, None)
        snap = console.collect()
        self.assertEqual(snap.tasks.review.state, ApprovalState.APPROVED)
        for decision in snap.decisions.values():
            self.assertEqual(decision.outcome.value, "BLOCKED")
            self.assertIn(Reason.EXECUTION_APPROVAL_MISSING, decision.reason_codes)
            self.assertIn(Reason.WORKER_DISPATCH_CONTRACT_UNVERIFIED, decision.reason_codes)

    def test_hold_with_reason(self):
        repo, console = self.verified()
        out = console.hold("명세 확인 후 결정")
        self.assertEqual(out.status, "ok")
        self.assertEqual(repo.review().state, ApprovalState.HELD)


@unittest.skipUnless(GIT, "git is not installed")
class RecoveryTextTest(unittest.TestCase):
    def test_confirm_command_is_exact_and_nothing_is_recorded_by_the_screen(self):
        repo = Repo(self)
        console = real_console(repo)
        command = console.confirm_stopped_command("T-2")
        argv = shlex.split(command) if os.name != "nt" else command.split()
        self.assertEqual(argv[1:3], ["-m", "orca_orchestrator.handoff"])
        self.assertEqual(argv[3:], ["--tasks", str(repo.plan), "--results", str(repo.results), "--repo",
                                    str(repo.root), "--confirm-stopped", "T-2", "--note", "확인한 내용"])

    def test_recovery_tab_shows_unconfirmed_runs_and_the_command(self):
        console = demo_console("interrupted")
        board = console.collect().tasks
        text = "\n".join(line for _, line in v.recovery_tab(board, console.confirm_stopped_command,
                                                            console.observe_command()))
        self.assertIn("실행 상태 확인 필요", text)
        self.assertIn("종료 미확인", text)
        self.assertIn("확인한 뒤 직접 기록:", text)
        self.assertIn("이 화면은 종료 확인을 기록하지 않습니다", text)

    def test_worker_scene_points_to_read_only_observe(self):
        console = demo_console("worker")
        board = console.collect().tasks
        text = "\n".join(line for _, line in v.recovery_tab(board, console.confirm_stopped_command,
                                                            console.observe_command()))
        self.assertIn("관측", text)
        self.assertIn("상태 조회(읽기 전용):", text)
        self.assertIn("--observe", text)


class StoppableRunnerTest(unittest.TestCase):
    def test_timeout_is_reported_like_the_tracked_runner(self):
        from orca_orchestrator.orca.runner import CommandTimeout

        runner = StoppableRunner(None, threading.Event(), RunHandle())
        with self.assertRaises(CommandTimeout):
            runner.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)

    def test_output_and_pid(self):
        pids = []
        runner = StoppableRunner(None, threading.Event(), RunHandle(), pids.append)
        result = runner.run([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
                            timeout=30, on_start=pids.append)
        self.assertEqual((result.exit_code, result.stdout.strip(), result.stderr.strip()), (0, "out", "err"))
        self.assertEqual(len(pids), 2)

    def test_handle_kills_the_running_process(self):
        handle = RunHandle()
        stop = threading.Event()
        runner = StoppableRunner(None, stop, handle)
        box = []
        t = threading.Thread(target=lambda: box.append(self._run(runner)))
        t.start()
        deadline = time.monotonic() + 10
        while handle._proc is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(handle.kill())
        t.join(10)
        self.assertNotEqual(box[0], 0)

    @staticmethod
    def _run(runner):
        try:
            return runner.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=60).exit_code
        except Exception as exc:  # pragma: no cover - reported to the assertion
            return exc


class ViewTest(unittest.TestCase):
    def test_header_marks_demo_and_stale_results(self):
        console = demo_console("passed")
        snap = console.collect()
        from datetime import datetime, timezone

        now = datetime(2026, 11, 26, 5, 0, tzinfo=timezone.utc)
        lines = v.header_lines(snap, console.target, v.RefreshInfo(now, False))
        self.assertEqual(len(lines), 2)
        self.assertIn("데모 (connected) · 실제 CLI를 호출하지 않음", lines[0][1])
        self.assertIn("Worker 전송 보류 · 사유 8건 (4: 상세)", lines[1][1])
        self.assertTrue(lines[1][1].startswith("조회 "))  # most important state first; the line is cut at the right
        failed = v.header_lines(snap, console.target, v.RefreshInfo(now, False, now, "OSError: boom"))
        self.assertEqual(failed[2][0], "bad")
        self.assertIn("조회 실패", failed[2][1])
        self.assertIn("최신이 아님", failed[2][1])

    def test_scenarios_in_the_header(self):
        expected = {"connected": "Runtime 연결됨", "disconnected": "Runtime 연결 안 됨",
                    "unbound": "바인딩 없음", "invalid": "Run 조회 불가"}
        for scenario, text in expected.items():
            with self.subTest(scenario):
                console = demo_console("passed", scenario)
                lines = v.header_lines(console.collect(), console.target, v.RefreshInfo(None, False))
                self.assertIn(text, "\n".join(line for _, line in lines))

    def test_every_state_has_a_mark_and_label(self):
        for state in TaskState:
            self.assertTrue(v.state_label(state)[0] in "✓✗↻▸-")
            self.assertGreater(len(v.state_label(state)), 2)

    def test_detail_shows_requirements_checks_changes_and_last_run(self):
        console = demo_console("passed")
        board = console.collect().tasks
        text = "\n".join(line for _, line in v.task_detail(board, "T-1", []))
        for expected in ("요구사항    FR-01 충족", "인수 조건:", "그래프      시작 ─▶ [T-1 ✓] ─▶ T-2 ▸",
                         "검증 1/1", "결과 ✓ 통과 · 현재 작업 정의·코드에 유효", "검증한 코드", "(현재와 같음)",
                         "변경 파일   src/orca_orchestrator/tasks/contract.py (수정)",
                         "마지막 실행", "실행 ID pid:4242", "확인 완료 unit-tests"):
            self.assertIn(expected, text)
        # Each fact once: the test summary is in the check record and the run line, not a third time.
        self.assertEqual(text.count("Ran 187 tests"), 2)
        self.assertNotIn("중단·종료 이유", text)
        self.assertNotIn("T-1 · 종료", text)  # the task ID is not repeated in its own last run

    def test_actions_sharing_a_reason_are_listed_once(self):
        off = Availability(False, "데모 모드")
        lines = v.action_lines([("v", "검증 실행", off), ("s", "흐름", off), ("x", "중지", Availability(True, ""))])
        self.assertEqual([line for _, line in lines],
                         ["동작        v·s 검증 실행, 흐름: 사용 불가 · 데모 모드", "            x 중지: 가능"])

    def test_dependency_graph(self):
        board = demo_console("failed").collect().tasks
        rows = {row.task_id: row for row in v.task_rows(board)}
        self.assertEqual((rows["T-1"].depth, rows["T-1"].graph_id), (0, "T-1"))
        self.assertEqual((rows["T-2"].depth, rows["T-2"].graph_id), (1, "└▶ T-2"))
        self.assertEqual(rows["T-2"].deps_text, "T-1 ✗")
        self.assertEqual(v.graph_line(board, "T-2"), "T-1 ✗ ─▶ [T-2 -] ─▶ 끝")

    def test_wrapped_lines_continue_under_the_value(self):
        lines = v.wrap_lines([v.kv("", "요구사항", "가나다 라마바 사아자 차카타 파하"), ("", "├─ alpha beta gamma delta epsilon")], 26)
        self.assertEqual([line for _, line in lines],
                         ["요구사항    가나다 라마바", "            사아자 차카타", "            파하",
                          "├─ alpha beta gamma delta", "   epsilon"])
        self.assertTrue(all(v.r.display_width(line) <= 26 for _, line in lines))

    def test_tree_lines(self):
        src = ["A", "  a1", "    x", "  a2", "    y", "    z", "B", "  b1"]
        self.assertEqual(v.tree_lines(src), ["A", "├─ a1", "│  └─ x", "└─ a2", "   ├─ y", "   └─ z", "B", "└─ b1"])

    def test_preflight_groups_reasons(self):
        snap = demo_console("passed").collect()
        text = "\n".join(line for _, line in v.dispatch_checklist(snap))
        self.assertIn("작업 전송   ⛔ 보류 · 사유 8건", text)
        for group in ("승인", "4-A", "인증", "과금", "실행 계약", "명령 근거"):
            self.assertIn(f"\n{group}", text)
        self.assertEqual(text.count("✗ "), 8)

    def test_stale_and_blocked_scenes(self):
        changed = "\n".join(line for _, line in v.task_detail(demo_console("changed").collect().tasks, "T-1", []))
        self.assertIn("재확인 필요 (검증 후 코드 변경, 이전 통과)", changed)
        protected = "\n".join(line for _, line in v.review_tab(demo_console("protected").collect().tasks, []))
        self.assertIn("보호 경로 변경", protected)
        self.assertIn(WORKER_NOTE, protected)
        interrupted = demo_console("interrupted").collect().tasks
        rows = {row.task_id: row for row in v.task_rows(interrupted)}
        self.assertTrue(any(row.run_note == "! 실행 확인 필요" for row in rows.values()))

    def test_filters(self):
        board = demo_console("failed").collect().tasks
        rows = v.task_rows(board)
        names = dict(v.FILTERS)
        self.assertEqual([r.task_id for r in rows if names["검증 실패"](r)], ["T-1"])
        self.assertEqual([r.task_id for r in rows if names["대기"](r)], ["T-2"])
        self.assertEqual([r.task_id for r in rows if names["완료"](r)], [])


class MainTest(unittest.TestCase):
    """How ``python -m orca_orchestrator`` chooses between the console, the earlier screen and plain output."""

    def tty(self):
        return mock.patch.multiple(sys.stdin, isatty=lambda: True), mock.patch.multiple(sys.stdout, isatty=lambda: True)

    def test_interactive_terminal_opens_the_console(self):
        try:
            import textual  # noqa: F401
        except ImportError:
            self.skipTest("textual is not installed")
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch.object(sys.stdout, "isatty", return_value=True), \
                mock.patch("orca_orchestrator.console.app.run_console", return_value=0) as run, \
                mock.patch.object(cli, "run_interactive") as plain:
            self.assertEqual(cli.main(["--demo", "--demo-results", "failed"]), 0)
        plain.assert_not_called()
        ops = run.call_args.args[0]
        self.assertEqual((ops.target.demo, ops.target.demo_results), ("connected", "failed"))
        self.assertEqual(ops.collect().tasks.label, "데모 계획 · 선행 작업 검증 실패")

    def test_plain_opens_the_earlier_screen(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch.object(sys.stdout, "isatty", return_value=True), \
                mock.patch.object(cli, "run_interactive", return_value=0) as plain:
            self.assertEqual(cli.main(["--demo", "--plain"]), 0)
        plain.assert_called_once()

    def test_without_textual_the_earlier_screen_opens_with_a_note(self):
        err = io.StringIO()
        loaded = [name for name in sys.modules
                  if name.split(".")[0] == "textual" or name == "orca_orchestrator.console.app"]
        with mock.patch.dict(sys.modules), \
                mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch.object(sys.stdout, "isatty", return_value=True), \
                mock.patch("sys.stderr", err), \
                mock.patch.object(cli, "run_interactive", return_value=0) as plain:
            for name in loaded:
                del sys.modules[name]
            sys.modules["textual"] = None  # import of textual fails as if it were not installed
            self.assertEqual(cli.main(["--demo"]), 0)
        plain.assert_called_once()
        self.assertIn("textual이 설치되어 있지 않아 기존 읽기 전용 화면을 엽니다", err.getvalue())
        self.assertIn('pip install "textual>=8.2.8,<9"', err.getvalue())

    def test_once_and_pipes_still_print_the_text_screen(self):
        out = io.StringIO()
        with mock.patch.object(cli, "open_console", side_effect=AssertionError("console opened")), \
                mock.patch.object(cli, "run_interactive", side_effect=AssertionError("screen opened")), \
                redirect_stdout(out):
            self.assertEqual(cli.main(["--demo", "--once"]), 0)
            self.assertEqual(cli.main(["--demo"]), 0)  # stdout is not a terminal here
        self.assertEqual(out.getvalue().count("Orca Orchestrator · 읽기 전용 상태 · 데모 (connected)"), 2)

    def test_timeout_must_be_positive(self):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(["--demo", "--timeout", "0"])


if __name__ == "__main__":
    unittest.main()
