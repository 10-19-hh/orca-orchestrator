"""The Textual console driven headlessly: layout, keys, refresh behaviour, actions and the quit policy.

Skipped when Textual is not installed. Orca answers come from the public fixture and test commands are
scripted, so nothing here calls Orca, a model or a Worker.
"""

from __future__ import annotations

import asyncio
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

try:
    import textual  # noqa: F401
except ImportError:  # pragma: no cover - depends on the environment
    textual = None

from orca_orchestrator.console.bridge import Console, Target
from orca_orchestrator.orca import DispatchGuard
from orca_orchestrator.tasks import DemoTaskSource, TaskState
from orca_orchestrator.tasks.handoff import AttemptState, RunEnd, read_attempts
from orca_orchestrator.tasks.review import ApprovalState
from orca_orchestrator.tui.demo import demo_adapter
from orca_orchestrator.tui.readonly import ALLOWED_TAILS
from orca_orchestrator.tui.snapshot import collect_snapshot

from test_console import ScriptedRunner, real_console
from test_review import GIT, Repo

if textual is not None:
    from textual.widgets import DataTable, Input, Static

    from orca_orchestrator.console.app import (
        ApproveScreen,
        ConfirmScreen,
        ConsoleApp,
        HelpScreen,
        HoldScreen,
        QuitScreen,
        SnapshotReady,
    )


def demo_app(variant="passed", scenario="connected", collect=None):
    adapter, runner = demo_adapter(scenario)
    tasks = DemoTaskSource(variant)
    collect = collect or (lambda: collect_snapshot(adapter, DispatchGuard(), demo=scenario, tasks=tasks))
    return ConsoleApp(Console(Target(demo=scenario, demo_results=variant), collect)), runner


def text(app, selector) -> str:
    return str(app.query_one(selector, Static).render())


async def wait_until(pilot, predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await pilot.pause(0.02)
        if predicate():
            return
    raise AssertionError("condition not reached in time")


class AppTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Test bodies build Git repositories synchronously; do not report that as a slow UI callback.
        asyncio.get_running_loop().slow_callback_duration = 2.0


class Notes:
    def __init__(self, app):
        self.items = []
        app.notify = lambda message, **kw: self.items.append((message, kw.get("title", "")))

    def text(self):
        return "\n".join(f"{t} {m}" for m, t in self.items)


@unittest.skipIf(textual is None, "textual is not installed")
class DemoScreenTest(AppTestCase):
    async def test_scenarios_show_mode_connection_binding_and_hint(self):
        expected = {
            "connected": ("Runtime 연결됨 · Orca 1.4.264 (검증 버전)", "바인딩 있음", None),
            "disconnected": ("Runtime 연결 안 됨", "Run 조회 안 함 (Runtime 연결 안 됨)", "Orca Runtime에 연결되지 않았습니다"),
            "unbound": ("바인딩 없음", "Run 조회 안 함 (바인딩 없음)", "Coordinator 바인딩이 없습니다"),
            "invalid": ("Run 조회 불가", "바인딩 있음", "응답을 해석할 수 없습니다"),
        }
        for scenario, (first, second, hint) in expected.items():
            with self.subTest(scenario):
                app, _ = demo_app("passed", scenario)
                async with app.run_test(size=(140, 40)) as pilot:
                    await wait_until(pilot, lambda: app.snapshot is not None)
                    header = text(app, "#header")
                    self.assertIn(f"데모 ({scenario}) · 실제 CLI를 호출하지 않음", header)
                    self.assertIn(first, header)
                    self.assertIn(second, header)
                    self.assertIn("Worker 전송 보류 · 사유", header)
                    if hint:
                        self.assertIn(hint, header)

    async def test_select_filter_and_keep_selection_across_refresh(self):
        app, _ = demo_app("failed")
        async with app.run_test(size=(140, 40)) as pilot:
            await wait_until(pilot, lambda: app.snapshot is not None)
            self.assertEqual(app.selected, "T-1")
            self.assertIn("T-1 · 작업 계약 검증", text(app, "#task-detail"))
            self.assertIn("✗ 검증 실패", text(app, "#task-detail"))
            await pilot.press("down")
            await pilot.pause()
            self.assertEqual(app.selected, "T-2")
            self.assertIn("대기 이유   선행 작업 T-1 검증 실패", text(app, "#task-detail"))
            table = app.query_one("#task-table", DataTable)
            self.assertEqual(table.get_cell("T-2", "mark"), ">")
            self.assertEqual(table.get_cell("T-1", "mark"), " ")
            gen = app._gen
            await pilot.press("r")
            await wait_until(pilot, lambda: app._applied > gen)
            self.assertEqual((app.selected, table.cursor_row), ("T-2", 1))
            await pilot.press("f", "f", "f")  # 전체 → 진행 필요 → 준비 → 대기
            await pilot.pause()
            self.assertIn("필터 대기 · 1/2개", text(app, "#filter-bar"))
            self.assertEqual(app.selected, "T-2")
            await pilot.press("F", "F")  # back to 진행 필요
            await pilot.press("f", "f", "f", "f")  # → 검증 실패
            await pilot.pause()
            self.assertIn("필터 검증 실패 · 1/2개", text(app, "#filter-bar"))
            self.assertEqual(app.selected, "T-1")
            await pilot.press("f")  # 완료: nothing matches
            await pilot.pause()
            self.assertIn("필터 '완료'에 맞는 작업 없음", text(app, "#task-detail"))

    async def test_tabs_show_review_recovery_and_preflight(self):
        app, runner = demo_app("interrupted")
        async with app.run_test(size=(140, 40)) as pilot:
            await wait_until(pilot, lambda: app.snapshot is not None)
            await pilot.press("2")
            await pilot.pause()
            review = text(app, "#review-body")
            self.assertIn("최종 승인", review)
            self.assertIn("실행 상태 확인 필요", review)
            self.assertIn("최종 변경 승인은 Worker 실행 승인과 다릅니다", review)
            await pilot.press("3")
            await pilot.pause()
            recovery = text(app, "#recovery-body")
            self.assertIn("종료 미확인", recovery)
            self.assertIn("확인한 뒤 직접 기록:", recovery)
            await pilot.press("4")
            await pilot.pause()
            calls = len(runner.calls)
            await pilot.press("p")
            await wait_until(pilot, lambda: app.preflight_outcome is not None)
            body = text(app, "#preflight-body")
            self.assertIn("판정: 전송 차단 · 아무것도 보내지 않았습니다.", body)
            self.assertIn("worker-start", body)
            self.assertTrue(all(call[1:] in ALLOWED_TAILS for call in runner.calls[calls:]))

    async def test_disabled_key_says_why(self):
        app, _ = demo_app("passed")
        async with app.run_test(size=(140, 40)) as pilot:
            await wait_until(pilot, lambda: app.snapshot is not None)
            notes = Notes(app)
            await pilot.press("v")
            await pilot.pause()
            self.assertIn("데모 모드", notes.text())
            self.assertIsNone(app.action_state)
            self.assertNotIsInstance(app.screen, ConfirmScreen)

    async def test_hangul_keys_and_help(self):
        app, _ = demo_app("passed")
        async with app.run_test(size=(140, 40)) as pilot:
            await wait_until(pilot, lambda: app.snapshot is not None)
            gen = app._gen
            await pilot.press("ㄱ")
            await wait_until(pilot, lambda: app._applied > gen)
            await pilot.press("question_mark")
            await pilot.pause()
            self.assertIsInstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("ㅂ")
            await pilot.pause()
        self.assertEqual(app.return_code, 0)

    async def test_navigation_refresh_preflight_and_quit_start_no_process(self):
        app, runner = demo_app("worker")
        with mock.patch("subprocess.Popen", side_effect=AssertionError("process started")), \
                mock.patch("subprocess.run", side_effect=AssertionError("process started")):
            async with app.run_test(size=(140, 40)) as pilot:
                await wait_until(pilot, lambda: app.snapshot is not None)
                await pilot.press("down", "enter", "escape", "f", "r", "2", "3", "4", "p", "o", "p")
                await wait_until(pilot, lambda: app.preflight_outcome is not None and not app.preflight_running)
                await pilot.press("1", "ctrl+c")
                await pilot.pause()
        self.assertEqual(app.return_code, 0)
        self.assertTrue(runner.calls)
        self.assertTrue(all(call[1:] in ALLOWED_TAILS for call in runner.calls))


@unittest.skipIf(textual is None, "textual is not installed")
class LayoutTest(AppTestCase):
    async def test_narrow_terminal_stacks_and_switches_panels(self):
        app, _ = demo_app("failed")
        async with app.run_test(size=(80, 24)) as pilot:
            await wait_until(pilot, lambda: app.snapshot is not None)
            self.assertTrue(app.screen.has_class("narrow"))
            self.assertTrue(app.screen.has_class("short"))
            listing = app.query_one("#list-pane")
            detail = app.query_one("#detail-pane")
            self.assertLess(listing.region.y, detail.region.y)  # stacked
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.focus_name(), "작업 상세")
            self.assertTrue(app.screen.has_class("detail-only"))
            self.assertIn("포커스 작업 상세", app.progress_text())
            await pilot.press("escape")
            await pilot.pause()
            self.assertFalse(app.screen.has_class("detail-only"))
            await pilot.press("enter", "tab")  # focus moves on past the detail while the list is hidden
            await pilot.pause()
            self.assertTrue(app.screen.has_class("detail-only"))
            await pilot.press("escape")  # Esc always brings the list back
            await pilot.pause()
            self.assertFalse(app.screen.has_class("detail-only"))
            self.assertEqual(app.focus_name(), "작업 목록")
            await pilot.resize_terminal(150, 45)
            await pilot.pause()
            self.assertFalse(app.screen.has_class("narrow"))
            self.assertEqual(listing.region.y, detail.region.y)  # side by side
            await pilot.resize_terminal(60, 20)
            await pilot.pause()
            self.assertTrue(app.screen.has_class("narrow"))
            for widget in app.query("Static"):
                self.assertLessEqual(widget.region.right, 60)

    async def test_refresh_keeps_the_detail_scroll_position(self):
        from textual.containers import VerticalScroll

        app, _ = demo_app("passed")
        async with app.run_test(size=(120, 24)) as pilot:
            await wait_until(pilot, lambda: app.snapshot is not None and not app.refreshing)
            pane = app.query_one("#detail-pane", VerticalScroll)
            self.assertGreater(pane.max_scroll_y, 4)
            pane.scroll_to(y=4, animate=False)
            await pilot.pause()
            gen = app._gen
            await pilot.press("r")
            await wait_until(pilot, lambda: app._applied > gen)
            await pilot.pause()
            self.assertEqual((app.selected, pane.scroll_y), ("T-1", 4))
            await pilot.press("down")  # another task starts at the top
            await pilot.pause()
            self.assertEqual((app.selected, pane.scroll_y), ("T-2", 0))

    async def test_long_names_and_errors_wrap_in_the_detail(self):
        app, _ = demo_app("passed")
        async with app.run_test(size=(80, 30)) as pilot:
            await wait_until(pilot, lambda: app.snapshot is not None)
            detail = app.query_one("#task-detail", Static)
            self.assertLessEqual(detail.region.right, 80)
            self.assertGreater(detail.virtual_size.height, 10)  # wrapped, scrollable, nothing cut off


@unittest.skipIf(textual is None, "textual is not installed")
class RefreshBehaviourTest(AppTestCase):
    async def test_keys_work_while_a_refresh_is_blocked_and_refreshes_coalesce(self):
        release = threading.Event()
        adapter, _ = demo_adapter("connected")
        tasks = DemoTaskSource("failed")
        calls = []

        def slow():
            calls.append(time.monotonic())
            if len(calls) > 1:
                release.wait(10)
            return collect_snapshot(adapter, DispatchGuard(), demo="connected", tasks=tasks)

        app, _ = demo_app("failed", collect=slow)
        async with app.run_test(size=(140, 40)) as pilot:
            await wait_until(pilot, lambda: app.snapshot is not None)
            await pilot.press("r")  # this refresh blocks
            await pilot.pause()
            self.assertTrue(app.refreshing)
            self.assertIn("다시 조회 중", text(app, "#header"))
            await pilot.press("down", "f", "2", "1", "r", "r", "r")  # input still handled
            await pilot.pause()
            self.assertEqual(app.selected, "T-2")
            self.assertEqual(app.filter_index, 1)
            self.assertEqual(len(calls), 2)  # three more presses: no new process, one queued refresh
            release.set()
            await wait_until(pilot, lambda: len(calls) == 3 and not app.refreshing)
            await pilot.pause(0.1)
            self.assertEqual(len(calls), 3)

    async def test_failed_refresh_marks_the_screen_as_not_current(self):
        fail = threading.Event()
        adapter, _ = demo_adapter("connected")
        tasks = DemoTaskSource("passed")

        def collect():
            if fail.is_set():
                raise OSError("disk gone")
            return collect_snapshot(adapter, DispatchGuard(), demo="connected", tasks=tasks)

        app, _ = demo_app("passed", collect=collect)
        async with app.run_test(size=(140, 40)) as pilot:
            await wait_until(pilot, lambda: app.snapshot is not None)
            shown = app.snapshot
            fail.set()
            await pilot.press("r")
            await wait_until(pilot, lambda: app.failed is not None)
            self.assertIs(app.snapshot, shown)
            self.assertIn("조회 실패", text(app, "#header"))
            self.assertIn("최신이 아님", text(app, "#header"))
            self.assertIn("이전 조회 결과", text(app, "#task-detail"))
            fail.clear()
            await pilot.press("r")
            await wait_until(pilot, lambda: app.failed is None)
            self.assertNotIn("조회 실패", text(app, "#header"))

    async def test_a_stalled_refresh_is_given_up_and_a_new_one_can_start(self):
        release = threading.Event()
        self.addCleanup(release.set)
        adapter, _ = demo_adapter("connected")
        tasks = DemoTaskSource("passed")
        calls = []

        def collect():
            calls.append(time.monotonic())
            if len(calls) == 2:
                release.wait(20)  # an Orca or git call that never returns
            return collect_snapshot(adapter, DispatchGuard(), demo="connected", tasks=tasks)

        app, _ = demo_app("passed", collect=collect)
        with mock.patch("orca_orchestrator.console.app.STALL_SECONDS", 0.5):
            async with app.run_test(size=(140, 40)) as pilot:
                await wait_until(pilot, lambda: app.snapshot is not None and not app.refreshing)
                await pilot.press("r")
                await wait_until(pilot, lambda: app.failed is not None)
                self.assertFalse(app.refreshing)
                self.assertIn("끝나지 않음", text(app, "#header"))
                self.assertIn("최신이 아님", text(app, "#header"))
                await pilot.press("r")
                await wait_until(pilot, lambda: len(calls) == 3 and app.failed is None and not app.refreshing)
                applied, shown = app._applied, app.snapshot
                release.set()  # the stalled refresh finally returns
                await pilot.pause(0.3)
                self.assertEqual((app._applied, app.snapshot), (applied, shown))  # its late result was discarded

    async def test_an_older_result_never_replaces_a_newer_one(self):
        app, _ = demo_app("passed")
        async with app.run_test(size=(140, 40)) as pilot:
            await wait_until(pilot, lambda: app.snapshot is not None and not app.refreshing)
            newer = app.snapshot
            older = demo_app("failed")[0].ops.collect()
            app.post_message(SnapshotReady(app._applied - 1, older, None, datetime.now(timezone.utc)))
            await pilot.pause()
            self.assertIs(app.snapshot, newer)


@unittest.skipUnless(GIT, "git is not installed")
@unittest.skipIf(textual is None, "textual is not installed")
class ActionTest(AppTestCase):
    async def start(self, pilot, app):
        await wait_until(pilot, lambda: app.snapshot is not None and not app.refreshing)

    async def test_verify_after_confirmation_once_even_with_repeated_keys(self):
        repo = Repo(self)
        gate = threading.Event()
        runner = ScriptedRunner(gate=gate)
        app = ConsoleApp(real_console(repo, runner))
        async with app.run_test(size=(140, 40)) as pilot:
            await self.start(pilot, app)
            await pilot.press("v")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmScreen)
            body = "\n".join(str(w.render()) for w in app.screen.query(Static))
            self.assertIn("작업        T-1 · 계산 함수 구현", body)
            self.assertIn("검증 조건    unit-tests → python -m unittest discover -s tests", body)
            self.assertIn("대상 저장소  repo", body)
            await pilot.press("y", "y", "v", "v")
            await wait_until(pilot, lambda: runner.calls == 1)
            self.assertIsNotNone(app.action_state)
            self.assertNotIsInstance(app.screen, ConfirmScreen)
            gate.set()
            await wait_until(pilot, lambda: app.action_state is None)
            await wait_until(pilot, lambda: not app.refreshing)
            self.assertEqual(runner.calls, 1)
            self.assertEqual(app.last_outcome.status, "ok")
            self.assertEqual(repo.board().progress.task("T-1").state, TaskState.VERIFIED)
            await wait_until(pilot, lambda: "✓ 완료" in text(app, "#task-detail"))

    async def test_flow_and_hold_from_the_screen(self):
        repo = Repo(self)
        app = ConsoleApp(real_console(repo, ScriptedRunner()))
        async with app.run_test(size=(140, 40)) as pilot:
            await self.start(pilot, app)
            await pilot.press("s")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmScreen)
            await pilot.press("y")
            await wait_until(pilot, lambda: app.last_outcome is not None and app.action_state is None)
            self.assertTrue(app.last_outcome.title.startswith("흐름 끝남"))
            self.assertFalse(repo.approval_exists())
            await pilot.press("2")
            await wait_until(pilot, lambda: not app.refreshing)
            await pilot.press("h")
            await pilot.pause()
            self.assertIsInstance(app.screen, HoldScreen)
            await pilot.press("enter")  # empty reason: nothing recorded
            await pilot.pause()
            self.assertIsInstance(app.screen, HoldScreen)
            app.screen.query_one(Input).value = "명세 확인 후 결정"
            await pilot.press("enter")
            await wait_until(pilot, lambda: app.last_outcome.action == "hold" and app.action_state is None)
            self.assertEqual(repo.review().state, ApprovalState.HELD)

    async def test_approval_is_refused_when_the_code_changes_after_review(self):
        repo = Repo(self)
        repo.verify_all()
        app = ConsoleApp(real_console(repo))
        async with app.run_test(size=(140, 40)) as pilot:
            await self.start(pilot, app)
            await pilot.press("2", "a")
            await pilot.pause()
            self.assertIsInstance(app.screen, ApproveScreen)
            repo.append("src/calc/ops.py", "# edited while the dialog was open\n")
            await pilot.click("#confirm")
            await wait_until(pilot, lambda: app.last_outcome is not None and app.action_state is None)
            self.assertEqual(app.last_outcome.status, "refused")
            self.assertIn("검토한 뒤 바뀜 (검증 대상 코드", app.last_outcome.title)
            self.assertFalse(repo.approval_exists())

    async def test_approval_of_the_reviewed_state(self):
        repo = Repo(self)
        repo.write("notes.txt", "x\n")
        repo.verify_all()
        app = ConsoleApp(real_console(repo))
        async with app.run_test(size=(140, 40)) as pilot:
            await self.start(pilot, app)
            await pilot.press("2", "a")
            await pilot.pause()
            confirm = app.screen.query_one("#confirm")
            self.assertTrue(confirm.disabled)  # unlinked change not confirmed yet
            await pilot.click("#unlinked")
            await pilot.pause()
            self.assertFalse(confirm.disabled)
            await pilot.click("#confirm")
            await wait_until(pilot, lambda: app.last_outcome is not None and app.action_state is None)
            self.assertEqual(app.last_outcome.status, "ok")
            self.assertEqual(repo.review().state, ApprovalState.APPROVED)
            await wait_until(pilot, lambda: not app.refreshing)
            for decision in app.snapshot.decisions.values():  # the Worker gate is untouched
                self.assertEqual(decision.outcome.value, "BLOCKED")

    async def test_quit_while_running_stops_and_keeps_the_end_unconfirmed(self):
        repo = Repo(self)
        gate = threading.Event()
        runner = ScriptedRunner(gate=gate)
        app = ConsoleApp(real_console(repo, runner))
        async with app.run_test(size=(140, 40)) as pilot:
            await self.start(pilot, app)
            await pilot.press("v", "y")
            await wait_until(pilot, lambda: runner.calls == 1)
            await pilot.press("q")
            await pilot.pause()
            self.assertIsInstance(app.screen, QuitScreen)
            await pilot.press("escape")  # keep watching
            await pilot.pause()
            self.assertIsNotNone(app.action_state)
            await pilot.press("ctrl+c", "ctrl+c")  # ask, then 'stop and close'
            await wait_until(pilot, lambda: app.return_code is not None, timeout=20)
        attempt = read_attempts(repo.results)[0][-1]
        self.assertEqual((attempt.state, attempt.runs[-1].end), (AttemptState.UNKNOWN, RunEnd.UNKNOWN))
        self.assertFalse(repo.results.exists())

    async def test_close_after_the_run_ends(self):
        repo = Repo(self)
        gate = threading.Event()
        runner = ScriptedRunner(gate=gate)
        app = ConsoleApp(real_console(repo, runner))
        async with app.run_test(size=(140, 40)) as pilot:
            await self.start(pilot, app)
            await pilot.press("v", "y")
            await wait_until(pilot, lambda: runner.calls == 1)
            await pilot.press("q")
            await pilot.pause()
            await pilot.click("#after")
            await pilot.pause()
            self.assertTrue(app.quit_when_idle)
            gate.set()
            await wait_until(pilot, lambda: app.return_code is not None, timeout=20)
        self.assertEqual(repo.board().progress.task("T-1").state, TaskState.VERIFIED)

    async def test_terminate_signal_stops_like_stop_and_close(self):
        repo = Repo(self)
        gate = threading.Event()
        runner = ScriptedRunner(gate=gate)
        app = ConsoleApp(real_console(repo, runner))
        async with app.run_test(size=(140, 40)) as pilot:
            await self.start(pilot, app)
            await pilot.press("v", "y")
            await wait_until(pilot, lambda: runner.calls == 1)
            app.on_terminate_signal()
            await wait_until(pilot, lambda: app.return_code is not None, timeout=20)
        attempt = read_attempts(repo.results)[0][-1]
        self.assertEqual(attempt.runs[-1].end, RunEnd.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
