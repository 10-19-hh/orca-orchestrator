from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from unittest import mock

from orca_orchestrator import __main__ as cli
from orca_orchestrator.orca import DispatchGuard, OrcaCliAdapter, Reason
from orca_orchestrator.orca.runner import CommandTimeout
from orca_orchestrator.tasks import (
    DEMO_RESULTS,
    PLAN_SCHEMA,
    RESULTS_SCHEMA,
    DemoTaskSource,
    FileTaskSource,
    IssueCode,
    build_board,
)
from orca_orchestrator.tasks.codebase import source_state
from orca_orchestrator.tasks.paths import tool_files
from orca_orchestrator.tui import app, render as r
from orca_orchestrator.tui.demo import SCENARIOS, demo_adapter
from orca_orchestrator.tui.readonly import ALLOWED_TAILS, ReadOnlyRunner, ReadOnlyViolation
from orca_orchestrator.tui.snapshot import collect_snapshot

from support import (
    BOUND_ENV,
    FAKE_CLI,
    NO_RUN_KEY_MISSING,
    RUN_CURRENT,
    STATUS,
    UNBOUND_ENV,
    FakeRunner,
    fixture_result,
)

NOW = datetime(2026, 11, 26, 5, 0, tzinfo=timezone.utc)


def screen(adapter, demo=None, width=80):
    snap = collect_snapshot(adapter, DispatchGuard(), demo=demo, now=NOW)
    return snap, r.render(snap, width)


def live_adapter(responses, env=BOUND_ENV):
    runner = FakeRunner(responses)
    return OrcaCliAdapter(ReadOnlyRunner(runner), FAKE_CLI, environ=env), runner


def text(lines):
    return "\n".join(lines)


class DemoScenarioTest(unittest.TestCase):
    EXPECTED = {
        # scenario: (commands run, runtime text, run text, hint)
        "connected": (2, "연결됨 · Orca 1.4.264 (검증 버전)", "run_0123456789ab · Example objective", None),
        "disconnected": (1, "연결 안 됨 · 상태 not_running", "조회 안 함 (Runtime 연결 안 됨)", r.HINT_RUNTIME_DOWN),
        "unbound": (1, "연결됨 · Orca 1.4.264", "조회 안 함 (바인딩 없음)", r.HINT_UNBOUND),
        "invalid": (2, "연결됨 · Orca 1.4.264", "현재 Run    조회 불가", r.HINT_UNREADABLE),
    }

    def test_scenarios(self):
        self.assertEqual(set(SCENARIOS), set(self.EXPECTED))
        for scenario, (calls, runtime, run, hint) in self.EXPECTED.items():
            with self.subTest(scenario):
                adapter, runner = demo_adapter(scenario)
                _, lines = screen(adapter, demo=scenario)
                body = text(lines)
                self.assertIn("데모", lines[0])
                self.assertIn("실제 CLI를 호출하지 않음", body)
                self.assertIn(runtime, body)
                self.assertIn(run, body)
                self.assertIn("사용량      Claude 미확인 · Codex 미확인", body)
                self.assertIn("작업 전송   보류", body)
                self.assertIn(r.REASON_LABELS[Reason.WORKER_DISPATCH_CONTRACT_UNVERIFIED], body)
                self.assertEqual(lines[-1], r.FOOTER)
                if hint:
                    self.assertEqual(lines[-2], hint)
                else:
                    self.assertFalse(any(line.startswith("안내:") for line in lines))
                self.assertEqual(len(runner.calls), calls)
                self.assertTrue(all(c[1:] in ALLOWED_TAILS for c in runner.calls))
                self.assertTrue(all(c[0] == "orca-demo" for c in runner.calls))

    def test_demo_never_starts_a_process(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("process started")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("process started")):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(cli.main(["--demo", "--once"]), 0)
        self.assertIn("데모 (connected)", out.getvalue())

    def test_demo_ignores_real_environment(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "x", "ORCA_TERMINAL_HANDLE": ""}):
            adapter, _ = demo_adapter("connected")
            _, lines = screen(adapter, demo="connected")
        self.assertNotIn("OPENAI_API_KEY", text(lines))
        self.assertIn("Coordinator 터미널 바인딩 있음", text(lines))


class LiveStateTest(unittest.TestCase):
    """Real-mode wiring with a fake command runner."""

    def test_connected_and_bound(self):
        adapter, runner = live_adapter({
            STATUS: fixture_result("status_ready"),
            RUN_CURRENT: fixture_result("run_current_coordinator"),
        })
        _, lines = screen(adapter)
        self.assertNotIn("데모", lines[0])
        self.assertIn("run_0123456789ab", text(lines))
        self.assertEqual([c[1:] for c in runner.calls], [STATUS, RUN_CURRENT])

    def test_runtime_unavailable_error(self):
        adapter, runner = live_adapter({STATUS: fixture_result("run_create_runtime_unavailable")})
        snap, lines = screen(adapter)
        self.assertIn("Runtime     연결 안 됨 · 버전 미확인", text(lines))
        self.assertEqual(r.hint(snap), r.HINT_RUNTIME_DOWN)
        self.assertEqual(len(runner.calls), 1)  # run-current is skipped

    def test_status_not_json(self):
        adapter, runner = live_adapter({STATUS: (0, "not json", "")})
        snap, lines = screen(adapter)
        self.assertIn("Runtime     조회 불가", text(lines))
        self.assertIn("조회 안 함 (Runtime 연결 안 됨)", text(lines))
        self.assertEqual(r.hint(snap), r.HINT_UNREADABLE)
        self.assertIn(r.REASON_LABELS[Reason.RUNTIME_STATUS_INVALID], text(lines))
        self.assertEqual(len(runner.calls), 1)

    def test_status_wrong_shape(self):
        body = {"ok": True, "result": {"runtime": {"reachable": "yes"}}}
        adapter, _ = live_adapter({STATUS: (0, json.dumps(body), "")})
        _, lines = screen(adapter)
        self.assertIn("Runtime     조회 불가", text(lines))

    def test_cli_missing(self):
        adapter = OrcaCliAdapter(ReadOnlyRunner(FakeRunner()), environ={"LOCALAPPDATA": "Z:\\nowhere"})
        snap, lines = screen(adapter)
        self.assertIn("Runtime     Orca CLI 없음", text(lines))
        self.assertEqual(r.hint(snap), r.HINT_CLI_MISSING)

    def test_unbound_skips_run_current(self):
        adapter, runner = live_adapter({STATUS: fixture_result("status_ready")}, env=UNBOUND_ENV)
        snap, lines = screen(adapter)
        self.assertEqual(r.hint(snap), r.HINT_UNBOUND)
        self.assertIn("바인딩      없음", text(lines))
        self.assertEqual([c[1:] for c in runner.calls], [STATUS])

    def test_stale_binding(self):
        stale = {"ok": False, "error": {"code": "terminal_handle_stale", "message": "stale"}}
        adapter, _ = live_adapter({
            STATUS: fixture_result("status_ready"),
            RUN_CURRENT: (1, json.dumps(stale), ""),
        })
        snap, lines = screen(adapter)
        self.assertIn("조회 불가 · 바인딩 무효 (terminal_handle_stale)", text(lines))
        self.assertEqual(r.hint(snap), r.HINT_BINDING_INVALID)

    def test_status_timeout_is_not_reported_as_unreadable(self):
        def slow():
            raise CommandTimeout("timed out after 10.0s")

        adapter, runner = live_adapter({STATUS: slow})
        snap, lines = screen(adapter)
        self.assertIn("Runtime     조회 시간 초과", text(lines))
        self.assertIn("조회 안 함 (Runtime 연결 안 됨)", text(lines))
        self.assertEqual(r.hint(snap), r.HINT_TIMED_OUT)
        self.assertNotIn(r.HINT_UNREADABLE, text(lines))
        self.assertEqual(len(runner.calls), 1)  # no automatic retry

    def test_run_current_timeout(self):
        def slow():
            raise CommandTimeout("timed out after 10.0s")

        adapter, runner = live_adapter({STATUS: fixture_result("status_ready"), RUN_CURRENT: slow})
        snap, lines = screen(adapter)
        self.assertIn("현재 Run    조회 시간 초과", text(lines))
        self.assertEqual(r.hint(snap), r.HINT_TIMED_OUT)
        self.assertEqual(len(runner.calls), 2)

    def test_explicit_null_run_id_shows_no_run(self):
        adapter, runner = live_adapter({
            STATUS: fixture_result("status_ready"),
            RUN_CURRENT: fixture_result("run_current_no_run"),
        })
        snap, lines = screen(adapter)
        self.assertIn("현재 Run    없음 (바인딩된 Run 없음)", text(lines))
        self.assertIsNone(r.hint(snap))
        self.assertNotIn("조회 불가", text(lines))
        self.assertEqual([c[1:] for c in runner.calls], [STATUS, RUN_CURRENT])

    def test_missing_run_id_key_shows_unreadable(self):
        adapter, _ = live_adapter({
            STATUS: fixture_result("status_ready"),
            RUN_CURRENT: NO_RUN_KEY_MISSING,
        })
        snap, lines = screen(adapter)
        self.assertIn("현재 Run    조회 불가", text(lines))
        self.assertNotIn(r.NO_RUN, text(lines))
        self.assertEqual(r.hint(snap), r.HINT_UNREADABLE)

    def test_run_current_malformed_run_id(self):
        adapter, _ = live_adapter({
            STATUS: fixture_result("status_ready"),
            RUN_CURRENT: (0, json.dumps({"ok": True, "result": {"runId": 7}}), ""),
        })
        _, lines = screen(adapter)
        self.assertIn("현재 Run    조회 불가", text(lines))

    def test_auth_environment_names_only(self):
        env = dict(BOUND_ENV, OPENAI_API_KEY="sk-not-shown")
        adapter, _ = live_adapter({
            STATUS: fixture_result("status_ready"),
            RUN_CURRENT: fixture_result("run_current_coordinator"),
        }, env=env)
        _, lines = screen(adapter)
        body = text(lines)
        self.assertIn("· Codex: 대체 인증 환경변수 설정됨: OPENAI_API_KEY", body)
        self.assertNotIn("Claude: 대체 인증", body)
        self.assertNotIn("sk-not-shown", body)


class ReadOnlyTest(unittest.TestCase):
    def test_mutation_refused_before_reaching_runner(self):
        adapter, runner = live_adapter({})
        with self.assertRaises(ReadOnlyViolation):
            adapter.run_create("x")
        self.assertEqual(runner.calls, [])

    def test_allowlist(self):
        self.assertEqual(ALLOWED_TAILS, {STATUS, RUN_CURRENT})


class RenderTest(unittest.TestCase):
    def test_every_reason_has_a_label(self):
        self.assertEqual(set(REASON for REASON in Reason), set(r.REASON_LABELS))

    def test_hints_fit_80_columns(self):
        for name in ("HINT_CLI_MISSING", "HINT_RUNTIME_DOWN", "HINT_UNBOUND",
                     "HINT_BINDING_INVALID", "HINT_UNREADABLE", "HINT_TIMED_OUT"):
            self.assertLessEqual(r.display_width(getattr(r, name)), 80, name)

    def test_lines_fit_narrow_terminal(self):
        adapter, _ = demo_adapter("disconnected")
        _, lines = screen(adapter, demo="disconnected", width=50)
        self.assertTrue(all(r.display_width(line) <= 50 for line in lines))
        self.assertTrue(any(line.endswith("…") for line in lines))


class LoopTest(unittest.TestCase):
    def run_keys(self, keys, collect=None):
        adapter, _ = demo_adapter("connected")
        count = {"n": 0}

        def default_collect():
            count["n"] += 1
            return collect_snapshot(adapter, DispatchGuard(), demo="connected", now=NOW)

        it = iter(keys)
        out: list[str] = []
        code = app.run_loop(collect or default_collect, lambda: next(it), out.append)
        return code, count["n"], out

    def test_refresh_and_quit(self):
        code, collected, out = self.run_keys(["x", "r", "R", "ㄱ", "q", "never-read"])
        self.assertEqual(code, 0)
        self.assertEqual(collected, 4)  # initial draw + three refreshes
        self.assertTrue(all(chunk.startswith(app.CLEAR) for chunk in out))

    def test_other_quit_keys(self):
        for key in ("Q", "ㅂ", "\x03", ""):
            with self.subTest(repr(key)):
                code, collected, _ = self.run_keys([key])
                self.assertEqual((code, collected), (0, 1))

    def test_terminal_restored_on_quit_and_on_error(self):
        @contextmanager
        def keys():
            it = iter(["r", "q"])
            yield lambda: next(it)

        adapter, _ = demo_adapter("connected")
        good = lambda: collect_snapshot(adapter, DispatchGuard(), demo="connected", now=NOW)

        def bad():
            raise RuntimeError("boom")

        for collect, raises in ((good, False), (bad, True)):
            out = io.StringIO()
            with mock.patch.object(app, "_key_reader", keys), mock.patch.object(app.sys, "stdout", out):
                if raises:
                    with self.assertRaises(RuntimeError):
                        app.run_interactive(collect)
                else:
                    self.assertEqual(app.run_interactive(collect), 0)
            value = out.getvalue()
            self.assertTrue(value.startswith(app.ENTER_SCREEN))
            self.assertTrue(value.endswith(app.LEAVE_SCREEN))


class MainTest(unittest.TestCase):
    def test_real_mode_uses_read_only_subprocess_runner(self):
        fake = FakeRunner({STATUS: fixture_result("run_create_runtime_unavailable")})
        out = io.StringIO()
        with mock.patch.object(cli, "SubprocessRunner", lambda: fake), \
                mock.patch.dict("os.environ", {"ORCA_CLI_COMMAND": FAKE_CLI}), redirect_stdout(out):
            self.assertEqual(cli.main(["--once"]), 0)
        self.assertNotIn("데모", out.getvalue())
        self.assertIn(r.HINT_RUNTIME_DOWN, out.getvalue())
        self.assertEqual([c[1:] for c in fake.calls], [STATUS])

    def test_unknown_demo_scenario_rejected(self):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(["--demo", "nope"])


def task_screen(variant="passed", scenario="connected", width=80):
    adapter, runner = demo_adapter(scenario)
    snap = collect_snapshot(adapter, DispatchGuard(), demo=scenario, now=NOW, tasks=DemoTaskSource(variant))
    return r.render(snap, width), runner


def section(lines):
    start = next(i for i, line in enumerate(lines) if line.startswith("작업 목록"))
    end = next(i for i, line in enumerate(lines) if line.startswith("요구사항"))
    return lines[start:end + 1]


def write_file(directory, name, content):
    path = Path(directory) / name
    path.write_text(content, encoding="utf-8")
    return str(path)


class TaskScreenTest(unittest.TestCase):
    def test_prerequisite_passed(self):
        lines, _ = task_screen("passed")
        self.assertEqual(section(lines), [
            "작업 목록   2개 · 완료 1 · 준비 1 · 데모 계획 · 선행 작업 검증 통과",
            "  T-1  완료       작업 계약 검증 · FR-01 · 검증 1/1 통과",
            "  T-2  준비       요구사항 추적 표시 · FR-07 · 선행 T-1 완료",
            "요구사항    FR-01 충족 · FR-07 미충족 (T-2 미완료)",
        ])

    def test_prerequisite_failed(self):
        lines, _ = task_screen("failed")
        self.assertEqual(section(lines), [
            "작업 목록   2개 · 검증 실패 1 · 대기 1 · 데모 계획 · 선행 작업 검증 실패",
            "  T-1  검증 실패  작업 계약 검증 · FR-01 · 검증 실패: unit-tests",
            "  T-2  대기       요구사항 추적 표시 · FR-07 · 선행 T-1",
            "                  대기 사유: 선행 작업 T-1 검증 실패",
            "요구사항    FR-01 미충족 (T-1 미완료) · FR-07 미충족 (T-2 미완료)",
        ])

    def test_no_results(self):
        lines, _ = task_screen("none")
        body = text(section(lines))
        self.assertIn("  T-1  준비       작업 계약 검증 · FR-01 · 선행 작업 없음", body)
        self.assertIn("대기 사유: 선행 작업 T-1 검증 결과 없음", body)

    def test_task_section_keeps_orca_view_and_hint_position(self):
        for variant in DEMO_RESULTS:
            for scenario in SCENARIOS:
                with self.subTest(variant=variant, scenario=scenario):
                    lines, runner = task_screen(variant, scenario)
                    self.assertEqual(lines[-1], r.FOOTER)
                    self.assertTrue(all(c[1:] in ALLOWED_TAILS for c in runner.calls))
                    self.assertTrue(all(r.display_width(line) <= 80 for line in lines))
                    if scenario == "connected":
                        self.assertLessEqual(len(lines), 38)
                        self.assertFalse(any(line.startswith("안내:") for line in lines))

    def test_invalid_plan_lists_issues_and_judges_nothing(self):
        plan = {
            "schema": PLAN_SCHEMA,
            "requirements": [{"id": "FR-01", "title": "t", "acceptance": "a"}],
            "tasks": [
                {"id": "T-1", "title": "a", "requirements": ["FR-01"], "depends_on": ["T-2"],
                 "checks": [{"id": "c", "description": "d"}]},
                {"id": "T-2", "title": "b", "requirements": ["FR-01"], "depends_on": ["T-1", "T-9"],
                 "checks": [{"id": "c", "description": "d"}]},
                {"id": "T-2", "title": "c", "requirements": ["FR-01"],
                 "checks": [{"id": "c", "description": "d"}]},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "plan.json", json.dumps(plan))
            adapter, _ = demo_adapter("connected")
            snap = collect_snapshot(adapter, DispatchGuard(), now=NOW, tasks=FileTaskSource(path))
            lines = r.render(snap)
        body = text(lines)
        header = "작업 목록   오류 3건 · 상태를 판정하지 않음"
        self.assertIn(header, lines)
        self.assertIn("  출처 plan.json · 검증 결과 없음", lines)
        self.assertIn("  · 중복 작업 ID: T-2", lines)
        self.assertIn("  · T-2의 선행 작업 T-9이(가) 정의되지 않음", lines)
        self.assertIn("  · 순환 의존성: T-1 → T-2 → T-1", lines)
        rest = text(lines[lines.index(header):])
        for label in ("완료", "준비", "대기 사유", "요구사항"):
            self.assertNotIn(label, rest)
        self.assertNotIn(tmp, body)

    def test_many_issues_are_capped(self):
        tasks = [{"id": f"T-{i}", "title": "t", "requirements": [], "checks": []} for i in range(3)]
        plan = {"schema": PLAN_SCHEMA, "requirements": [], "tasks": tasks}
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "plan.json", json.dumps(plan))
            lines = r.task_lines(FileTaskSource(path).load())
        self.assertEqual(lines[0], "작업 목록   오류 6건 · 상태를 판정하지 않음")
        self.assertEqual(len(lines), 2 + r.MAX_ISSUE_LINES + 1)
        self.assertEqual(lines[-1], "  · … 외 2건")

    def test_unreadable_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "plan.json", "{not json")
            lines = r.task_lines(FileTaskSource(path).load())
        self.assertEqual(lines[0], "작업 목록   조회 불가 · plan.json: JSON 형식 오류 (1행)")

    def test_every_issue_has_a_label(self):
        self.assertEqual(set(IssueCode), set(r.ISSUE_LABELS))

    def test_not_configured(self):
        adapter, _ = demo_adapter("connected")
        _, lines = screen(adapter, demo="connected")
        self.assertIn("작업 목록   " + r.TASKS_NOT_CONFIGURED, lines)


class TaskOptionTest(unittest.TestCase):
    def run_main(self, argv):
        out = io.StringIO()
        with mock.patch("subprocess.run", side_effect=AssertionError("process started")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("process started")), \
                redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_demo_shows_two_dependent_tasks(self):
        cases = (
            (["--demo", "--once"], "  T-2  준비"),
            (["--demo", "--demo-results", "failed", "--once"], "  T-2  대기"),
            (["--demo", "--demo-results", "none", "--once"], "  T-2  대기"),
        )
        for argv, expected in cases:
            with self.subTest(argv):
                code, out = self.run_main(argv)
                self.assertEqual(code, 0)
                self.assertIn("  T-1  ", out)
                self.assertIn(expected, out)

    def test_real_mode_reads_task_files(self):
        fake = FakeRunner({STATUS: fixture_result("run_create_runtime_unavailable")})
        bundled = resources.files("orca_orchestrator").joinpath("fixtures")
        with tempfile.TemporaryDirectory() as tmp:
            plan = write_file(tmp, "plan.json", bundled.joinpath("demo_task_plan.json").read_text(encoding="utf-8"))
            failed = json.loads(bundled.joinpath("demo_results_failed.json").read_text(encoding="utf-8"))
            current = source_state(tmp, tool_files(Path(plan), Path(tmp) / "results.json")).digest
            failed["results"][0]["source_digest"] = current  # verified against this (empty) repository
            results = write_file(tmp, "results.json", json.dumps(failed))
            out = io.StringIO()
            with mock.patch.object(cli, "SubprocessRunner", lambda: fake), \
                    mock.patch.dict("os.environ", {"ORCA_CLI_COMMAND": FAKE_CLI}), redirect_stdout(out):
                code = cli.main(["--once", "--tasks", plan, "--results", results, "--repo", tmp])
        self.assertEqual(code, 0)
        self.assertIn("대기 사유: 선행 작업 T-1 검증 실패", out.getvalue())
        self.assertIn("변경        비교 불가 · --repo가 Git 저장소가 아님", out.getvalue())
        self.assertIn("  · T-1: 보호 경로 확인 불가", out.getvalue())
        self.assertIn("plan.json · results.json", out.getvalue())
        self.assertNotIn(tmp, out.getvalue())
        self.assertEqual([c[1:] for c in fake.calls], [STATUS])

    def test_option_combinations_rejected(self):
        for argv in (["--demo-results", "failed"], ["--demo", "--tasks", "p.json"],
                     ["--results", "r.json"], ["--demo", "--demo-results", "maybe"], ["--repo", "."]):
            with self.subTest(argv):
                with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                    with self.assertRaises(SystemExit):
                        cli.main(argv)


class StaticTasks:
    def __init__(self, board):
        self.board = board

    def load(self):
        return self.board


def long_plan(task_count=12):
    tasks = [
        {
            "id": f"T-{i}",
            "title": f"작업 {i}",
            "requirements": ["FR-01"],
            "depends_on": [f"T-{i - 1}"] if i > 1 else [],
            "checks": [{"id": "unit", "description": "단위 테스트"}],
        }
        for i in range(1, task_count + 1)
    ]
    plan = {"schema": PLAN_SCHEMA, "requirements": [{"id": "FR-01", "title": "t", "acceptance": "a"}],
            "tasks": tasks}
    results = {"schema": RESULTS_SCHEMA,
               "results": [{"task": "T-1", "check": "unit", "outcome": "failed", "exit_code": 1}]}
    return json.dumps(plan), json.dumps(results)


def long_snapshot(task_count=12):
    """Runtime down (hint line), API-key env for both providers (extra reasons), long task list."""

    plan, results = long_plan(task_count)
    board = build_board(plan, results, "plan.json · results.json")
    env = dict(BOUND_ENV, OPENAI_API_KEY="x", ANTHROPIC_API_KEY="y")
    adapter, _ = live_adapter({STATUS: fixture_result("run_create_runtime_unavailable")}, env)
    return collect_snapshot(adapter, DispatchGuard(), now=NOW, tasks=StaticTasks(board))


def demo_snapshot(variant="failed"):
    adapter, _ = demo_adapter("connected")
    return collect_snapshot(adapter, DispatchGuard(), demo="connected", now=NOW, tasks=DemoTaskSource(variant))


TITLE = "Orca Orchestrator · 읽기 전용 상태"


class LayoutTest(unittest.TestCase):
    def assert_frame(self, frame, height, width=79):
        self.assertLessEqual(len(frame.lines), height)
        self.assertTrue(frame.lines[0].startswith(TITLE), frame.lines[0])
        self.assertTrue(frame.lines[-1].startswith("r 새로고침 · q 종료"), frame.lines[-1])
        self.assertTrue(all(r.display_width(line) <= width for line in frame.lines))

    def test_tall_terminal_shows_demo_without_scrolling(self):
        for variant in DEMO_RESULTS:
            with self.subTest(variant):
                snap = demo_snapshot(variant)
                frame = r.layout(snap, 79, 40)
                self.assert_frame(frame, 40)
                self.assertEqual(frame.lines, r.render(snap, 79))
                self.assertEqual((frame.offset, frame.max_offset), (0, 0))
                self.assertEqual(frame.lines[-1], r.FOOTER)

    def test_80x24_demo_scrolls_to_task_flow_lines(self):
        for variant in DEMO_RESULTS:
            with self.subTest(variant):
                snap = demo_snapshot(variant)
                _, body = r.render_parts(snap, 79)
                top = r.layout(snap, 79, 24)
                self.assert_frame(top, 24)
                self.assertEqual(len(top.lines), 24)
                bottom = r.layout(snap, 79, 24, offset=10_000)
                self.assert_frame(bottom, 24)
                self.assertEqual(bottom.lines[-2], body[-1])  # the bottom rule is reachable
                self.assertTrue(any(line.startswith("최종 승인") for line in bottom.lines))
                self.assertTrue(any(line.startswith("다음 작업") for line in bottom.lines))

    def test_80x24_long_tasks_and_reasons_scroll(self):
        snap = long_snapshot()
        header, body = r.render_parts(snap, 79)
        self.assertGreater(len(header) + len(body) + 1, 24)
        self.assertIn(r.HINT_RUNTIME_DOWN, body)
        self.assertIn("  · " + r.REASON_LABELS[Reason.API_KEY_ENVIRONMENT_PRESENT], body)

        top = r.layout(snap, 79, 24)
        self.assert_frame(top, 24)
        self.assertEqual(len(top.lines), 24)
        self.assertEqual(top.lines[:3], header)
        self.assertEqual(top.lines[3:-1], body[:20])
        self.assertEqual(top.lines[-1], f"{r.FOOTER} · ↑↓ 이동 · 1–20/{len(body)}줄 · 아래 {len(body) - 20}줄 더 있음")

        bottom = r.layout(snap, 79, 24, offset=10_000)
        self.assert_frame(bottom, 24)
        self.assertEqual(bottom.offset, len(body) - 20)
        self.assertEqual(bottom.lines[-2], r.HINT_RUNTIME_DOWN)
        self.assertIn(f"위 {len(body) - 20}줄 더 있음", bottom.lines[-1])

        middle = r.layout(snap, 79, 24, offset=2)
        self.assertIn("위 2줄·아래", middle.lines[-1])
        self.assertEqual(r.layout(snap, 79, 24, offset=-5).offset, 0)

    def test_lower_terminals_keep_title_and_keys(self):
        snap = long_snapshot()
        _, body = r.render_parts(snap, 79)
        for height in (20, 12, 8, 5, 4, 3, 2, 1):
            with self.subTest(height=height):
                frame = r.layout(snap, 79, height)
                self.assert_frame(frame, max(height, 2))
                self.assertEqual(len(frame.lines), max(height, 2))
                if height > 2:
                    self.assertIn("↑↓ 이동", frame.lines[-1])
                else:
                    self.assertIn(r.TOO_LOW.format(n=len(body)), frame.lines[-1])
                self.assertTrue(set(frame.lines[1:-1]) <= set(body) | set(r.render_parts(snap, 79)[0]))

    def test_scrolling_reaches_every_body_line(self):
        snap = long_snapshot(20)
        _, body = r.render_parts(snap, 79)
        seen = []
        offset = 0
        while True:
            frame = r.layout(snap, 79, 10, offset)
            seen.extend(frame.lines[3:-1])
            if frame.offset == frame.max_offset:
                break
            offset = frame.offset + 1
        self.assertEqual(list(dict.fromkeys(seen)), list(dict.fromkeys(body)))

    def test_scroll_footer_fits_80_columns(self):
        self.assertLessEqual(r.display_width(r.scroll_footer(98, 118, 240)), 79)


class ScrollLoopTest(unittest.TestCase):
    def drive(self, keys, size, snap_factory=long_snapshot):
        calls = {"n": 0}

        def collect():
            calls["n"] += 1
            return snap_factory()

        out = []
        it = iter(keys)
        code = app.run_loop(collect, lambda: next(it), out.append, size)
        frames = [chunk[len(app.CLEAR):].split("\r\n") for chunk in out]
        return code, calls["n"], out, frames

    def test_keys_scroll_without_requerying(self):
        keys = [app.DOWN, "j", "ㅓ", app.END, app.UP, "k", app.HOME, app.PAGE_DOWN, " ", "b", "G", "g", "x", "q"]
        code, collected, out, frames = self.drive(keys, lambda: (80, 24))
        self.assertEqual((code, collected), (0, 1))
        self.assertEqual(len(frames), 13)  # first draw + 12 scroll keys; "x" ignored
        for chunk, lines in zip(out, frames):
            self.assertTrue(chunk.startswith(app.CLEAR))
            self.assertFalse(chunk.endswith("\n"))  # never scrolls the terminal itself
            self.assertEqual(len(lines), 24)
            self.assertTrue(lines[0].startswith(TITLE))
            self.assertTrue(lines[-1].startswith(r.FOOTER))
        positions = [lines[-1].split(" · ")[3] for lines in frames]
        self.assertEqual(positions[0], positions[-1])  # g returns to the top
        self.assertEqual(frames[4][-2], r.HINT_RUNTIME_DOWN)  # END shows the last line

    def test_refresh_keeps_position_and_quit_still_works(self):
        code, collected, _, frames = self.drive([app.DOWN, app.DOWN, "r", "q"], lambda: (80, 24))
        self.assertEqual((code, collected), (0, 2))
        self.assertEqual(frames[2][3:], frames[3][3:])  # same window after refresh

    def test_resize_between_keys(self):
        sizes = iter([(80, 24), (80, 10), (60, 6)])
        code, _, _, frames = self.drive([app.DOWN, app.DOWN, "q"], lambda: next(sizes))
        self.assertEqual(code, 0)
        self.assertEqual([len(f) for f in frames], [24, 10, 6])
        self.assertTrue(all(r.display_width(line) <= 59 for line in frames[2]))

    def test_short_content_unchanged(self):
        code, _, _, frames = self.drive([app.DOWN, "q"], lambda: (80, 40), lambda: demo_snapshot("passed"))
        self.assertEqual(frames[0], r.render(demo_snapshot("passed"), 79))
        self.assertEqual(frames[0], frames[1])  # nothing to scroll

    def test_key_decoding(self):
        self.assertEqual(app.decode_windows("\xe0", lambda: "H"), app.UP)
        self.assertEqual(app.decode_windows("\x00", lambda: "P"), app.DOWN)
        self.assertEqual(app.decode_windows("\xe0", lambda: "I"), app.PAGE_UP)
        self.assertEqual(app.decode_windows("\xe0", lambda: "Q"), app.PAGE_DOWN)
        self.assertEqual(app.decode_windows("\xe0", lambda: "G"), app.HOME)
        self.assertEqual(app.decode_windows("\xe0", lambda: "O"), app.END)
        self.assertEqual(app.decode_windows("\xe0", lambda: ";"), app.UNKNOWN_KEY)
        self.assertEqual(app.decode_windows("q", lambda: self.fail("read too far")), "q")
        for seq, key in (("[A", app.UP), ("OB", app.DOWN), ("[5~", app.PAGE_UP), ("[6~", app.PAGE_DOWN),
                         ("[H", app.HOME), ("[4~", app.END), ("", app.UNKNOWN_KEY), ("[Z", app.UNKNOWN_KEY)):
            with self.subTest(seq):
                self.assertEqual(app.decode_escape(seq), key)
        self.assertNotIn(app.UNKNOWN_KEY, app.SCROLL_KEYS)
        self.assertFalse(set(app.SCROLL_KEYS) & (app.REFRESH_KEYS | app.QUIT_KEYS))


class OnceOutputTest(unittest.TestCase):
    def run_real(self, argv, tty):
        fake = FakeRunner({STATUS: fixture_result("run_create_runtime_unavailable")})
        plan, results = long_plan()
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = write_file(tmp, "plan.json", plan)
            results_path = write_file(tmp, "results.json", results)
            out = io.StringIO()
            with mock.patch.object(cli, "SubprocessRunner", lambda: fake), \
                    mock.patch.object(cli, "run_interactive", side_effect=AssertionError("interactive")), \
                    mock.patch.object(cli.sys.stdin, "isatty", lambda: tty, create=True), \
                    mock.patch.dict("os.environ", {"ORCA_CLI_COMMAND": FAKE_CLI}), redirect_stdout(out):
                code = cli.main(argv + ["--tasks", plan_path, "--results", results_path, "--repo", tmp])
        return code, out.getvalue().splitlines()

    def test_once_and_no_tty_print_everything(self):
        for argv, tty in ((["--once"], True), ([], False)):
            with self.subTest(argv=argv, tty=tty):
                code, lines = self.run_real(argv, tty)
                self.assertEqual(code, 0)
                self.assertGreater(len(lines), 24)
                self.assertEqual(lines[-1], r.FOOTER)
                self.assertEqual(lines[-2], r.HINT_RUNTIME_DOWN)
                self.assertTrue(any(line.startswith("  T-12") for line in lines))
                self.assertFalse(any("↑↓ 이동" in line for line in lines))


class NarrowTaskFlowScreenTest(unittest.TestCase):
    def test_flow_lines_in_a_narrow_low_tab(self):
        snap = demo_snapshot("failed")
        _, body = r.render_parts(snap, 49)
        self.assertTrue(any(line.startswith("흐름") for line in body))
        offset, seen = 0, []
        while True:
            frame = r.layout(snap, 49, 10, offset)
            self.assertEqual(len(frame.lines), 10)
            self.assertTrue(frame.lines[0].startswith("Orca Orchestrator"))
            self.assertTrue(frame.lines[-1].startswith("r 새로고침"))
            self.assertTrue(all(r.display_width(line) <= 49 for line in frame.lines))
            seen.extend(frame.lines[3:-1])
            if frame.offset == frame.max_offset:
                break
            offset = frame.offset + 1
        for label in ("다음 작업", "흐름", "최종 승인"):
            self.assertTrue(any(line.startswith(label) for line in seen), label)


if __name__ == "__main__":
    unittest.main()
