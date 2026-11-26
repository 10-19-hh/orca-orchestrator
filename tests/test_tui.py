from __future__ import annotations

import io
import json
import unittest
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
from unittest import mock

from orca_orchestrator import __main__ as cli
from orca_orchestrator.orca import DispatchGuard, OrcaCliAdapter, Reason
from orca_orchestrator.orca.runner import CommandTimeout
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


if __name__ == "__main__":
    unittest.main()
