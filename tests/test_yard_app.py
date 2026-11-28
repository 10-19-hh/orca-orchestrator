"""The yard screen driven headlessly: clicks, keys, details, requests, motion, sizes and colours.

Skipped when Textual is not installed. Orca answers come from the public fixture and the clock is a
fake one, so nothing here waits on real time for a frame, calls Orca, runs a check or records anything.
"""

from __future__ import annotations

import random
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

try:
    import textual  # noqa: F401
except ImportError:  # pragma: no cover - depends on the environment
    textual = None

from orca_orchestrator import __main__ as cli
from orca_orchestrator.console.bridge import ActionOutcome, Console, Target
from orca_orchestrator.orca import DispatchGuard
from orca_orchestrator.tasks import DemoTaskSource
from orca_orchestrator.tui.demo import demo_adapter
from orca_orchestrator.tui.snapshot import collect_snapshot

from test_console_app import AppTestCase, Notes, wait_until

if textual is not None:
    from textual.widgets import OptionList, Static

    from orca_orchestrator.console.app import (
        ActionFinished,
        ActionProgress,
        ConsoleApp,
        HelpScreen,
        HoldScreen,
        SnapshotReady,
    )
    from orca_orchestrator.console.motion import Motion
    from orca_orchestrator.console.yard import Mood
    from orca_orchestrator.console.yard_screen import Bubble, YardScreen, YardView


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def yard_app(variant="passed", *, motion=None, colors="full", scenario="connected"):
    adapter, runner = demo_adapter(scenario)
    tasks = DemoTaskSource(variant)
    calls = []

    def collect():
        calls.append(1)
        return collect_snapshot(adapter, DispatchGuard(), demo=scenario, tasks=tasks)

    clock = Clock()
    app = ConsoleApp(Console(Target(demo=scenario, demo_results=variant), collect), yard=True,
                     motion=motion or Motion.NORMAL, clock=clock, rng=random.Random(1), colors=colors)
    return app, runner, calls, clock


async def ready(pilot, app):
    await wait_until(pilot, lambda: app.snapshot is not None and isinstance(app.screen, YardScreen)
                     and app.yard.model is not None and app.yard.model.mood is not Mood.NO_DATA)
    await pilot.pause()


def view(app) -> YardView:
    return app.yard.query_one("#yard", YardView)


def center(rect):
    return rect.x + rect.w // 2, rect.y + rect.h // 2


def line_text(widget, y) -> str:
    return "".join(seg.text for seg in widget.render_line(y))


@unittest.skipIf(textual is None, "textual is not installed")
class YardScreenTest(AppTestCase):
    async def test_yard_is_the_default_screen_and_says_it_is_a_mock(self):
        app, *_ = yard_app("none")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            header = str(app.yard.query_one("#yard-header", Static).render())
            self.assertIn("모의 상태 · 실제 CLI·실행·기록 없음", header)
            self.assertIn("Worker 전송 차단", header)
            self.assertIn("조회 ", header)
            status = str(app.yard.query_one("#yard-status", Static).render())
            self.assertIn("실행 시작 아님", status)
            self.assertIs(app.yard.model.mood, Mood.READY)
            self.assertIs(app.screen.focused, view(app))

    async def test_frames_never_collect_run_or_record(self):
        app, runner, calls, clock = yard_app("passed")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            await wait_until(pilot, lambda: not app.refreshing)
            before = (len(calls), len(runner.calls))
            ops = app.ops
            guarded = {name: mock.Mock(side_effect=AssertionError(name))
                       for name in ("run_verify", "run_flow", "approve", "hold", "preflight", "collect")}
            with mock.patch.multiple(ops, **guarded):
                yard = view(app)
                for _ in range(400):
                    clock.t += 0.125
                    yard.tick()
                await pilot.pause(0.4)  # the real interval timer ticks too
            self.assertGreater(yard.ticks, 400)
            self.assertEqual((len(calls), len(runner.calls)), before)
            for fn in guarded.values():
                fn.assert_not_called()

    async def test_click_character_opens_its_task_and_esc_returns_with_focus(self):
        app, *_ = yard_app("failed")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            rect = view(app).target_rects()["actor"]
            self.assertTrue(await pilot.click("#yard", offset=center(rect)))
            await pilot.pause()
            self.assertNotIsInstance(app.screen, YardScreen)
            self.assertEqual(app.active_tab(), "tab-tasks")
            self.assertEqual(app.selected, "T-1")
            self.assertEqual(app.screen.focused.id, "detail-pane")
            self.assertIn("T-1 · 작업 계약 검증", str(app.query_one("#task-detail", Static).render()))
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, YardScreen)
            self.assertIs(app.screen.focused, view(app))
            self.assertEqual(view(app).selected, "actor")

            await pilot.press("tab")  # the inbox; then open from it and come back to it
            await pilot.pause()
            inbox = app.yard.query_one("#inbox", OptionList)
            self.assertIs(app.screen.focused, inbox)
            if inbox.highlighted is None:
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, YardScreen)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIs(app.screen.focused, app.yard.query_one("#inbox", OptionList))

    async def test_click_bench_opens_and_grass_does_nothing(self):
        app, *_ = yard_app("none")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            yard = view(app)
            rects = yard.target_rects()
            grass = (rects["actor"].x + rects["actor"].w + 3, rects["actor"].y + 1)
            self.assertIsNone(yard.target_at(*grass))
            await pilot.click("#yard", offset=grass)
            await pilot.pause()
            self.assertIsInstance(app.screen, YardScreen)
            await pilot.click("#yard", offset=center(rects["bench"]))
            await pilot.pause()
            self.assertEqual(app.active_tab(), "tab-tasks")
            self.assertEqual(app.selected, "T-1")

    async def test_bubble_opens_its_request_and_closing_is_not_resolving(self):
        app, *_ = yard_app("worker")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            bubble = app.yard.query_one("#bubble", Bubble)
            self.assertTrue(bubble.display)
            self.assertEqual(bubble.request.rid, "end_unknown:T-2")
            self.assertIn("종료 미확인", str(bubble.render()))
            await pilot.click("#bubble")
            await pilot.pause()
            self.assertEqual(app.active_tab(), "tab-recovery")
            await pilot.press("escape")
            await pilot.pause()
            book = app.yard.book
            self.assertTrue(book.is_read("end_unknown:T-2"))
            self.assertIn("end_unknown:T-2", [r.rid for r in book.open])  # read, still open
            self.assertNotEqual(bubble.request.rid, "end_unknown:T-2")
            await pilot.press("d")  # closing the next bubble only marks it read
            await pilot.pause()
            self.assertEqual(len(book.open), 3)
            self.assertIn("(읽음 · 해결 아님)", str(app.yard.query_one("#inbox", OptionList)
                                                 .get_option_at_index(0).prompt))

    async def test_keyboard_reaches_every_target(self):
        app, *_ = yard_app("worker")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            await pilot.press("right")
            self.assertEqual(view(app).selected, "bench")
            await pilot.press("right")
            self.assertEqual(view(app).selected, "bubble")
            self.assertIn("선택 ▶ 말풍선", str(app.yard.query_one("#yard-status", Static).render()))
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.active_tab(), "tab-recovery")
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(view(app).selected, "bubble")  # selection kept
            await pilot.press("left", "left", "enter")
            await pilot.pause()
            self.assertEqual(app.active_tab(), "tab-tasks")
            self.assertEqual(app.selected, "T-2")
            await pilot.press("escape")
            await pilot.press("2")  # a detail tab straight from the yard
            await pilot.pause()
            self.assertEqual(app.active_tab(), "tab-review")
            await pilot.press("escape")
            await pilot.press("c")
            await pilot.pause()
            self.assertNotIsInstance(app.screen, YardScreen)
            await pilot.press("y")  # back to the yard from the console
            await pilot.pause()
            self.assertIsInstance(app.screen, YardScreen)

    async def test_resize_keeps_targets_inside_and_clickable(self):
        app, *_ = yard_app("worker")
        async with app.run_test(size=(140, 36)) as pilot:
            await ready(pilot, app)
            for size in ((100, 30), (76, 24), (150, 40)):
                await pilot.resize_terminal(*size)
                await pilot.pause()
                yard = view(app)
                for name, rect in yard.target_rects().items():
                    self.assertLessEqual(rect.x + rect.w, yard.size.width, (size, name))
                    self.assertLessEqual(rect.y + rect.h, yard.size.height, (size, name))
                bubble = app.yard.query_one("#bubble", Bubble)
                x, y = bubble.styles.offset.x.value, bubble.styles.offset.y.value
                self.assertGreaterEqual(x, 0)
                self.assertLessEqual(x + bubble.box[0], yard.size.width)
                self.assertGreaterEqual(y, 0)
                await pilot.click("#yard", offset=center(yard.target_rects()["actor"]))
                await pilot.pause()
                self.assertEqual(app.active_tab(), "tab-tasks", size)
                await pilot.press("escape")
                await pilot.pause()
                self.assertIsInstance(app.screen, YardScreen)

    async def test_dialogs_and_text_input_are_not_taken_by_yard_keys(self):
        app, *_ = yard_app("none")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            motion = view(app).motion
            await pilot.press("question_mark")
            await pilot.pause()
            self.assertIsInstance(app.screen, HelpScreen)
            self.assertIn("마당 (기본 화면)", str(app.screen.query_one("#dialog-body Static", Static).render()))
            await pilot.press("m", "d", "c", "n", "1")
            await pilot.pause()
            self.assertIsInstance(app.screen, HelpScreen)
            self.assertIs(view(app).motion, motion)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, YardScreen)

            answers = []
            app.push_screen(HoldScreen(), answers.append)
            await pilot.pause()
            await pilot.press("m", "c", "d", "n", "y", "2")
            await pilot.pause()
            self.assertIsInstance(app.screen, HoldScreen)
            from textual.widgets import Input
            self.assertEqual(app.screen.query_one("#reason", Input).value, "mcdny2")
            self.assertIs(view(app).motion, motion)
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(answers, [None])
            self.assertIsInstance(app.screen, YardScreen)

    async def test_recording_keys_do_nothing_on_the_yard(self):
        app, *_ = yard_app("none")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            notes = Notes(app)
            await pilot.press("v", "s", "a", "h", "p", "o", "x", "f", "enter")
            await pilot.pause()
            # Enter opens the character's detail; nothing else happened.
            self.assertEqual(app.active_tab(), "tab-tasks")
            self.assertIsNone(app.action_state)
            self.assertEqual(notes.items, [])
            self.assertEqual(app.filter_index, 0)

    async def test_motion_modes_and_pausing_while_hidden(self):
        app, *_ = yard_app("none")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            yard = view(app)
            self.assertTrue(yard.running)
            await pilot.press("m")
            self.assertIs(yard.motion, Motion.REDUCED)
            self.assertTrue(yard.running)
            await pilot.press("m")
            self.assertIs(yard.motion, Motion.OFF)
            self.assertFalse(yard.running)
            self.assertIn("움직임 정지", str(app.yard.query_one("#yard-keys", Static).render()))
            await pilot.press("m")
            self.assertTrue(yard.running)
            await pilot.press("c")
            await pilot.pause()
            self.assertFalse(yard.running)  # hidden behind the console
            await pilot.press("escape")
            await pilot.pause()
            self.assertTrue(yard.running)
            app.on_app_blur()
            self.assertFalse(yard.running)
            app.on_app_focus()
            self.assertTrue(yard.running)

    async def test_motion_off_from_the_start(self):
        app, *_ = yard_app("none", motion=Motion.OFF)
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            self.assertFalse(view(app).running)
            before = [line_text(view(app), y) for y in range(view(app).size.height)]
            for _ in range(50):
                view(app).tick()
            self.assertEqual([line_text(view(app), y) for y in range(view(app).size.height)], before)

    async def test_narrow_screen_is_still_and_lists_tasks(self):
        app, *_ = yard_app("worker")
        async with app.run_test(size=(60, 20)) as pilot:
            await ready(pilot, app)
            self.assertTrue(app.yard.narrow)
            self.assertTrue(view(app).still)
            self.assertFalse(view(app).running)
            inbox = app.yard.query_one("#inbox", OptionList)
            ids = [inbox.get_option_at_index(i).id for i in range(inbox.option_count)]
            self.assertIn("task:T-1", ids)
            self.assertIn("task:T-2", ids)
            self.assertIn("req:end_unknown:T-2", ids)
            await pilot.resize_terminal(120, 34)
            await pilot.pause()
            self.assertFalse(app.yard.narrow)
            self.assertTrue(view(app).running)

    async def test_no_color_environment_renders_the_text_scene(self):
        with mock.patch.dict("os.environ", {"NO_COLOR": "1"}):
            app, *_ = yard_app("worker", colors="auto")
        async with app.run_test(size=(120, 34)) as pilot:  # Textual's monochrome filter is active
            await ready(pilot, app)
            await pilot.pause(0.2)
            self.assertEqual(view(app).color_mode, "text")
            self.assertIn("작업대", line_text(view(app), 1))
            self.assertIsInstance(app.screen, YardScreen)

    def test_colour_support_decision(self):
        from orca_orchestrator.console.yard_screen import colors_for
        self.assertEqual(colors_for("truecolor", False), "full")
        self.assertEqual(colors_for("256", False), "full")  # Rich lowers the pixel colours to 256
        for system in ("standard", "windows", None):
            self.assertEqual(colors_for(system, False), "text")
        self.assertEqual(colors_for("truecolor", True), "text")

    async def test_limited_colours_use_the_text_scene(self):
        app, *_ = yard_app("none", colors="text")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            yard = view(app)
            self.assertFalse(yard.running)
            self.assertIn("작업대", line_text(yard, 1))
            self.assertNotIn("▀", "".join(line_text(yard, y) for y in range(yard.size.height)))
            await pilot.click("#yard", offset=center(yard.target_rects()["bench"]))
            await pilot.pause()
            self.assertEqual(app.active_tab(), "tab-tasks")

    async def test_stale_refresh_shows_last_observation(self):
        app, *_ = yard_app("none")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            await wait_until(pilot, lambda: not app.refreshing)
            app._inflight = 999
            app._gen = 999
            app.on_snapshot_ready(SnapshotReady(999, None, "boom", datetime.now(timezone.utc)))
            await pilot.pause()
            banner = app.yard.query_one("#yard-banner", Static)
            self.assertTrue(banner.display)
            self.assertIn("마지막 관측", str(banner.render()))
            self.assertIn("최신이 아님", str(banner.render()))
            self.assertIn("최신 아님", str(app.yard.query_one("#yard-header", Static).render()))
            self.assertIs(app.yard.model.mood, Mood.READY)  # the last judged state, nothing invented

    async def test_refresh_does_not_duplicate_requests_and_keeps_read_marks(self):
        app, *_ = yard_app("worker")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            first = [r.rid for r in app.yard.book.open]
            app.yard.book.mark_read(first[0])
            for _ in range(3):
                gen = app._applied
                await pilot.press("r")
                await wait_until(pilot, lambda: app._applied > gen)
            self.assertEqual([r.rid for r in app.yard.book.open], first)
            self.assertTrue(app.yard.book.is_read(first[0]))
            inbox = app.yard.query_one("#inbox", OptionList)
            self.assertEqual(inbox.option_count, len(first))

    async def test_reconnect_rebuilds_open_requests(self):
        rids = []
        for _ in range(2):
            app, *_ = yard_app("worker")
            async with app.run_test(size=(120, 34)) as pilot:
                await ready(pilot, app)
                rids.append([r.rid for r in app.yard.book.open])
                self.assertEqual(app.yard.book.read, set())
                app.yard.book.mark_read(rids[-1][0])
        self.assertEqual(rids[0], rids[1])

    async def test_run_events_drive_the_character_and_end_beats_running(self):
        app, *_ = yard_app("none")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            self.assertIs(app.yard.model.mood, Mood.READY)
            app.on_action_progress(ActionProgress("start", ("T-1", "unit-tests")))
            await pilot.pause()
            self.assertIs(app.yard.model.mood, Mood.WORKING)
            self.assertEqual(view(app).actor.activity, "run")
            self.assertIn("T-1/unit-tests 로컬 검증 실행 중", str(app.yard.query_one("#yard-status", Static).render()))
            app.on_action_finished(ActionFinished(ActionOutcome("verify", "stopped", "중지됨")))
            await pilot.pause()
            self.assertIsNone(app.local_run)
            self.assertIsNot(app.yard.model.mood, Mood.WORKING)
            self.assertNotEqual(view(app).actor.activity, "run")  # switched before reaching the bench

        app, *_ = yard_app("interrupted")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            app.on_action_progress(ActionProgress("start", ("T-1", "unit-tests")))
            await pilot.pause()
            self.assertIs(app.yard.model.mood, Mood.END_UNKNOWN)

    async def test_demo_scenes_step_through_without_running_anything(self):
        app, runner, calls, _ = yard_app("passed")
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            await wait_until(pilot, lambda: not app.refreshing)
            before = (len(calls), len(runner.calls))
            seen = []
            for _ in range(5):
                await pilot.press("n")
                await pilot.pause()
                seen.append(app.yard.model.mood)
                self.assertTrue(app.yard.model.mock)
                self.assertIn("모의 장면", str(app.yard.query_one("#yard-header", Static).render()))
            self.assertEqual(seen, [Mood.IDLE, Mood.WORKING, Mood.WORKING, Mood.END_UNKNOWN, Mood.PASSED])
            await pilot.press("n")
            await pilot.pause()
            self.assertFalse(app.yard.model.mock)
            self.assertEqual((len(calls), len(runner.calls)), before)

    async def test_scene_key_is_off_outside_the_demo(self):
        app, *_ = yard_app("none")
        app.ops.target = Target(demo=None)  # as if real; the collector still reads the fixture
        app.yard.demo = False
        async with app.run_test(size=(120, 34)) as pilot:
            await ready(pilot, app)
            await pilot.press("n")
            await pilot.pause()
            self.assertIsNone(app.yard_scene)
            self.assertNotIn("모의 장면", str(app.yard.query_one("#yard-keys", Static).render()))


@unittest.skipIf(textual is None, "textual is not installed")
class YardCliTest(unittest.TestCase):
    def run_cli(self, argv):
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch.object(sys.stdout, "isatty", return_value=True), \
                mock.patch("orca_orchestrator.console.app.run_console", return_value=0) as run:
            self.assertEqual(cli.main(argv), 0)
        return run.call_args

    def test_yard_is_the_default_and_console_opens_the_tabs(self):
        call = self.run_cli(["--demo"])
        self.assertEqual((call.kwargs["yard"], call.kwargs["motion"]), (True, Motion.NORMAL))
        call = self.run_cli(["--demo", "--console", "--motion", "off"])
        self.assertEqual((call.kwargs["yard"], call.kwargs["motion"]), (False, Motion.OFF))
        call = self.run_cli(["--demo", "--motion", "reduced"])
        self.assertEqual(call.kwargs["motion"], Motion.REDUCED)


if __name__ == "__main__":
    unittest.main()
