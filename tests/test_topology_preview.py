"""Topology Preview (``--topology-preview``): seats, pet behaviour, the polling feed, the DEMO timeline and the app.

The office model and the feed need no UI framework. App tests run headless and are skipped without Textual.
Live discovery is never started here: sources are the synthetic timeline or fakes.
"""

from __future__ import annotations

import contextlib
import io
import random
import sys
import threading
import unittest
from datetime import timedelta
from unittest import mock

try:
    import textual  # noqa: F401
except ImportError:  # pragma: no cover - depends on the environment
    textual = None

from orca_orchestrator import __main__ as cli
from orca_orchestrator.console.motion import Motion
from orca_orchestrator.console.office import (
    AMBIENT_FLAVORS,
    OfficeActor,
    OfficeGeometry,
    SeatBook,
    default_room,
    rooms,
)
from orca_orchestrator.console.sprites import WORKER_ACTIONS
from orca_orchestrator.topology import scenarios as sc
from orca_orchestrator.topology.feed import TimelineSource, TopologyFeed
from orca_orchestrator.topology.model import SourceError
from orca_orchestrator.topology.parse import Reply

from test_console_app import AppTestCase, wait_until

if textual is not None:
    from orca_orchestrator.console.office_screen import OfficeView, TopologyPreviewApp

FORBIDDEN_ACTIONS = set(WORKER_ACTIONS) | {"think"}


def session(sid, arrival, lifecycle="active", intent="work", wt="w1", provider="codex"):
    return {"session_id": sid, "arrival": arrival, "lifecycle": lifecycle, "worktree_id": wt,
            "provider": provider, "presentation": {"intent": intent}}


def timeline_docs() -> list[dict]:
    feed = TopologyFeed(TimelineSource(sc.preview_timeline(), sc.PREVIEW_STEP_NOTES))
    return [feed.poll_now().document for _ in range(len(sc.preview_timeline().steps))]


def main_room(doc):
    return [s for s in doc["agent_sessions"] if s["worktree_id"] == sc.W1.key]


class SeatBookTest(unittest.TestCase):
    def test_arrival_order_lowest_free_slot_and_no_moves(self):
        book = SeatBook()
        self.assertEqual(book.sync("w1", [session("a", 1), session("b", 2)]), {"a": 0, "b": 1})
        self.assertEqual(book.sync("w1", [session("a", 1), session("b", 2), session("c", 3)]),
                         {"a": 0, "b": 1, "c": 2})
        # a ends: its seat is free; b and c stay; the next arrival takes the lowest free seat
        self.assertEqual(book.sync("w1", [session("a", 1, "ended"), session("b", 2), session("c", 3)]),
                         {"b": 1, "c": 2})
        self.assertEqual(book.sync("w1", [session("b", 2), session("c", 3), session("d", 4)]),
                         {"b": 1, "c": 2, "d": 0})
        self.assertEqual(book.desks("w1"), 3)

    def test_missing_and_unknown_keep_their_seat(self):
        book = SeatBook()
        book.sync("w1", [session("a", 1), session("b", 2)])
        self.assertEqual(book.sync("w1", [session("a", 1, "missing"), session("b", 2, "unknown")]), {"a": 0, "b": 1})

    def test_rooms_are_separate_and_same_provider_sessions_each_get_a_seat(self):
        book = SeatBook()
        three = [session(f"c{i}", i, provider="codex") for i in range(1, 4)]
        self.assertEqual(book.sync("w1", three), {"c1": 0, "c2": 1, "c3": 2})
        self.assertEqual(book.sync("w2", [session("x", 1, wt="w2")]), {"x": 0})
        self.assertEqual(book.desks("w1"), 3)
        self.assertEqual(book.desks("w2"), 1)

    def test_desks_stay_when_sessions_leave(self):
        book = SeatBook()
        book.sync("w1", [session("a", 1), session("b", 2)])
        book.sync("w1", [])
        self.assertEqual(book.desks("w1"), 2)


class GeometryTest(unittest.TestCase):
    def test_seats_are_on_the_floor_inside_the_room_and_do_not_overlap(self):
        for size in ((80, 24), (110, 34), (160, 45), (220, 60)):
            geom = OfficeGeometry(*size)
            with self.subTest(size=size):
                self.assertTrue(geom.fits)
                self.assertGreaterEqual(geom.capacity, 1)
                rects = [geom.desk_rect(k) for k in range(geom.capacity)]
                for k in range(geom.capacity):
                    x, foot = geom.seat(k)
                    self.assertGreater(foot, geom.wall)
                    self.assertLessEqual(foot, geom.px_height - 2)
                    self.assertGreaterEqual(x, geom.desk_x0)
                    self.assertLessEqual(x + 14, geom.width)
                for i, a in enumerate(rects):
                    for b in rects[i + 1:]:
                        overlap = a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h
                        self.assertFalse(overlap)
                self.assertIsNone(geom.seat(geom.capacity))
                self.assertLess(geom.door[0], geom.lounge_right)

    def test_bigger_screens_hold_more_seats(self):
        self.assertLess(OfficeGeometry(110, 34).capacity, OfficeGeometry(220, 60).capacity)
        self.assertGreaterEqual(OfficeGeometry(200, 50).capacity, 8)
        self.assertEqual(OfficeGeometry(40, 10).capacity, 0)


class ActorTest(unittest.TestCase):
    def setUp(self):
        self.geom = OfficeGeometry(160, 40)
        self.seat = self.geom.seat(0)

    def actor(self, motion=Motion.NORMAL, at_door=False):
        return OfficeActor.create("example-session", "codex", (12, 12), self.geom, motion, 0.0, at_door=at_door)

    def run_for(self, actor, start, seconds):
        poses = []
        t = start
        while t < start + seconds:
            t += 0.125
            poses.append(actor.advance(t))
        return poses, t

    def test_work_walks_to_the_own_desk_and_stays_with_the_generic_pose(self):
        a = self.actor()
        a.set_intent("work", self.seat, 0.0)
        poses, _ = self.run_for(a, 0.0, 10)
        self.assertEqual(a.state, "seated")
        self.assertEqual((poses[-1].x, poses[-1].foot), tuple(int(v) for v in self.seat))
        self.assertEqual(poses[-1].action, "watch")
        self.assertFalse({p.action for p in poses} & FORBIDDEN_ACTIONS)

    def test_done_then_working_again(self):
        a = self.actor()
        a.set_intent("work", self.seat, 0.0)
        _, t = self.run_for(a, 0.0, 10)
        a.set_intent("work_done", self.seat, t)
        poses, t = self.run_for(a, t, 1)
        self.assertEqual(poses[-1].action, "happy")
        a.set_intent("ambient", self.seat, t)
        poses, t = self.run_for(a, t, 20)
        self.assertNotEqual(a.state, "seated")
        self.assertLess(min(p.x for p in poses[-40:]), self.geom.lounge_right)  # back in the shared space
        self.assertFalse({p.action for p in poses} & FORBIDDEN_ACTIONS)
        a.set_intent("work", self.seat, t)
        self.run_for(a, t, 10)
        self.assertEqual(a.state, "seated")

    def test_ambient_uses_only_rest_flavours_without_thinking(self):
        self.assertNotIn("think", AMBIENT_FLAVORS)
        a = self.actor()
        a.set_intent("ambient", None, 0.0)
        poses, _ = self.run_for(a, 0.0, 120)
        self.assertLessEqual({p.action for p in poses}, {"idle", "blink", "walk", "sweep", "sleep"})

    def test_unobservable_stops_where_it_is(self):
        a = self.actor()
        a.set_intent("work", self.seat, 0.0)
        self.run_for(a, 0.0, 0.5)  # still walking
        a.set_intent("unobservable", self.seat, 0.5)
        poses, _ = self.run_for(a, 0.5, 10)
        self.assertEqual({(p.x, p.foot) for p in poses}, {(poses[0].x, poses[0].foot)})
        self.assertEqual({p.action for p in poses}, {"question"})
        self.assertTrue(a.dim)
        a.set_intent("work", self.seat, 10.5)
        self.assertFalse(a.dim)

    def test_attention_waits_at_the_desk(self):
        a = self.actor()
        a.set_intent("attention", self.seat, 0.0)
        poses, _ = self.run_for(a, 0.0, 10)
        self.assertEqual((a.state, poses[-1].action), ("seated", "notify"))

    def test_exit_walks_to_the_door_and_is_gone(self):
        a = self.actor()
        a.set_intent("work", self.seat, 0.0)
        _, t = self.run_for(a, 0.0, 10)
        a.set_intent("exit", self.seat, t)
        poses, _ = self.run_for(a, t, 15)
        self.assertTrue(a.gone)
        self.assertIsNone(poses[-1])

    def test_reduced_motion_places_without_walking(self):
        a = self.actor(Motion.REDUCED)
        a.set_intent("work", self.seat, 0.0)
        self.assertEqual((a.state, (a.x, a.foot)), ("seated", self.seat))
        poses, _ = self.run_for(a, 0.0, 5)
        self.assertNotIn("walk", {p.action for p in poses})

    def test_a_new_session_enters_through_the_door(self):
        a = self.actor(at_door=True)
        self.assertEqual((a.x, a.foot), self.geom.door)

    def test_same_session_same_choices(self):
        def trail():
            a = OfficeActor.create("example-session-9", "claude", (14, 7), self.geom, Motion.NORMAL, 0.0,
                                   at_door=False, salt=3)
            a.set_intent("ambient", None, 0.0)
            return [(p.action, p.x, p.foot) for p in self.run_for(a, 0.0, 30)[0]]

        self.assertEqual(trail(), trail())


class FeedTest(unittest.TestCase):
    def test_the_tracker_lasts_across_polls(self):
        docs = timeline_docs()
        self.assertEqual([d["sequence"] for d in docs], list(range(1, 12)))
        reconnect = {s["session_id"]: s for s in main_room(docs[9])}
        self.assertTrue(reconnect["example-session-1-1"]["reappeared"])
        self.assertTrue(reconnect["example-session-1-1"]["incarnation_changed"])

    def test_one_poll_at_a_time_and_closed_feeds_discard(self):
        gate, entered = threading.Event(), threading.Event()

        class Slow:
            mode = "live"

            def collect(self):
                entered.set()
                gate.wait(5)
                return sc.raw(sc.Step([sc.W1]), sc.START), None

        feed = TopologyFeed(Slow())
        self.assertTrue(feed.begin())
        results = []
        worker = threading.Thread(target=lambda: results.append(feed.poll()))
        worker.start()
        entered.wait(5)
        self.assertFalse(feed.begin())          # a second poll is refused while one runs
        self.assertIsNone(feed.poll_now())
        feed.close()
        gate.set()
        worker.join(5)
        self.assertEqual(results, [None])       # closed meanwhile: the result is discarded
        self.assertIsNone(feed.last_document)
        self.assertEqual(feed.tracker.sequence, 0)  # and the tracker never saw it
        self.assertFalse(feed.begin())

    def test_a_failed_poll_keeps_the_last_document(self):
        calls = []

        class Flaky:
            mode = "live"

            def collect(self):
                calls.append(1)
                if len(calls) == 2:
                    raise OSError("boom")
                return sc.raw(sc.Step([sc.W1], *sc.sessions(sc.W1, [("codex", "working")])), sc.START), None

        feed = TopologyFeed(Flaky())
        first = feed.poll_now()
        failed = feed.poll_now()
        self.assertIsNone(failed.document)
        self.assertIn("OSError", failed.error)
        self.assertIs(feed.last_document, first.document)
        third = feed.poll_now()
        self.assertEqual(third.document["sequence"], 2)  # the failed poll never reached the tracker

    def test_unanswered_sources_keep_sessions_unknown(self):
        step = sc.Step([sc.W1], *sc.sessions(sc.W1, [("claude", "working")]))
        failing = sc.Step([sc.W1], *sc.sessions(sc.W1, [("claude", "working")]),
                          overrides={"agents": Reply(error=SourceError("outcome_unknown"))})
        feed = TopologyFeed(TimelineSource(sc.Scene("t", "t", "t", (step, failing))))
        feed.poll_now()
        doc = feed.poll_now().document
        self.assertEqual(doc["agent_sessions"][0]["lifecycle"], "unknown")


class TimelineTest(unittest.TestCase):
    """The DEMO timeline covers the preview's checks, in order."""

    def test_steps(self):
        docs = timeline_docs()
        intents = [{s["session_id"][-3:]: s["presentation"]["intent"] for s in main_room(d)} for d in docs]
        self.assertEqual(intents[0], {})                                                   # 1 no sessions
        self.assertEqual(intents[1], {"1-1": "work"})                                      # 2 Codex
        self.assertEqual(intents[2], {"1-1": "work", "1-2": "work"})                       # 3 Claude
        self.assertEqual(len(intents[3]), 4)                                               # 4 2+2
        self.assertEqual({s["provider"] for s in main_room(docs[3])}, {"claude", "codex"})
        self.assertEqual(intents[4]["1-1"], "work_done")                                   # 5 working -> done
        self.assertEqual(intents[5]["1-1"], "work")                                        # 6 done -> working
        devices = [t for t in docs[6]["terminals"] if t["role"] == "terminal_device"]
        self.assertEqual(len(devices), 1)                                                  # 7 plain terminal
        self.assertEqual(len(main_room(docs[6])), 4)
        self.assertEqual(main_room(docs[7])[3]["lifecycle"], "missing")                    # 8 temporary gap
        self.assertEqual(set(intents[8].values()), {"unobservable"})                       # 9 disconnected
        self.assertEqual(docs[8]["runtime"]["health"], "disconnected")
        self.assertEqual(sorted(intents[9]), sorted(intents[7]))                           # 10 no duplicates
        self.assertEqual(intents[10]["1-2"], "exit")                                       # 11 confirmed end
        rooms_4 = rooms(docs[3])
        self.assertEqual([r.sessions for r in rooms_4], [4, 1])                            # two rooms to switch
        self.assertEqual(default_room(docs[0]), sc.W1.key)


class CliTest(unittest.TestCase):
    def run_cli(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_demo_once_prints_every_step(self):
        code, text = self.run_cli(["--topology-preview", "--demo", "--once"])
        self.assertEqual(code, 0)
        self.assertEqual(text.count("장면 preview-timeline ("), 11)
        self.assertIn("퇴장 (종료 확인)", text)

    def test_a_named_scene(self):
        code, text = self.run_cli(["--topology-preview", "--demo", "--topology-scene", "eight-sessions", "--once"])
        self.assertEqual(code, 0)
        self.assertIn("세션 8", text)

    def test_conflicting_or_misplaced_options(self):
        for argv in (["--topology-preview", "--demo", "--json"], ["--topology-preview", "--console"],
                     ["--topology-preview", "--tasks", "plan.json"], ["--interval", "5"],
                     ["--topology-scene", "timeline"], ["--topology-preview", "--topology-scene", "timeline"],
                     ["--topology-preview", "--interval", "1"], ["--topology-preview", "--demo", "--topology-scene",
                                                                 "nope"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.main(argv)

    @unittest.skipIf(textual is not None, "only meaningful without Textual")
    def test_without_textual_prints_the_text_summary(self):
        class Tty(io.StringIO):
            def isatty(self):
                return True

        err, out = io.StringIO(), Tty()
        with mock.patch.object(sys.stdin, "isatty", return_value=True), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(out):
            code = cli.main(["--topology-preview", "--demo", "--topology-scene", "one-claude"])
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("textual", err.getvalue())
        self.assertIn("장면 one-claude (1/1)", text)

    @unittest.skipIf(textual is None, "textual is not installed")
    def test_live_preview_uses_the_read_only_source(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch.object(sys.stdout, "isatty", return_value=True), \
                mock.patch("orca_orchestrator.console.office_screen.run_topology_preview", return_value=0) as run, \
                mock.patch("orca_orchestrator.topology.feed.LiveSource") as live:
            live.return_value.mode = "live"
            self.assertEqual(cli.main(["--topology-preview", "--interval", "7", "--motion", "reduced"]), 0)
        live.assert_called_once_with()
        self.assertEqual((run.call_args.kwargs["interval"], run.call_args.kwargs["motion"]), (7.0, Motion.REDUCED))

    @unittest.skipIf(textual is None, "textual is not installed")
    def test_default_screen_is_unchanged(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch.object(sys.stdout, "isatty", return_value=True), \
                mock.patch("orca_orchestrator.console.app.run_console", return_value=0) as run:
            self.assertEqual(cli.main(["--demo"]), 0)
        self.assertTrue(run.call_args.kwargs["yard"])


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@unittest.skipIf(textual is None, "textual is not installed")
class PreviewAppTest(AppTestCase):
    def app(self, source=None, size_motion=Motion.NORMAL):
        source = source or TimelineSource(sc.preview_timeline(), sc.PREVIEW_STEP_NOTES)
        feed = TopologyFeed(source)
        clock = FakeClock()
        return TopologyPreviewApp(feed, interval=3600, motion=size_motion, clock=clock, colors="full", salt=1), clock

    async def step(self, pilot, app, key="n"):
        before = app.applied
        await pilot.press(key)
        await wait_until(pilot, lambda: app.applied > before)

    async def frames(self, pilot, app, clock, seconds=8.0):
        actions = set()
        for _ in range(int(seconds * 8)):
            clock.t += 0.125
            app.office.redraw()
            actions |= {p.action for p in app.office._poses.values()}
        await pilot.pause()
        return actions

    async def test_pets_follow_the_timeline_without_restarting(self):
        app, clock = self.app()
        async with app.run_test(size=(160, 44)) as pilot:
            await wait_until(pilot, lambda: app.applied == 1)
            office = app.office
            self.assertEqual(office.actors, {})                                    # 1 no sessions
            seen_actions = set()
            counts = []
            pets: dict = {}
            for _ in range(10):
                await self.step(pilot, app)
                seen_actions |= await self.frames(pilot, app, clock)
                counts.append(len(office.actors))
                for sid, actor in office.actors.items():  # the same pet object for the whole session: no respawn
                    self.assertIs(pets.setdefault(sid, actor), actor)
            self.assertEqual(counts, [1, 2, 4, 4, 4, 4, 4, 4, 4, 3])               # exit at the end
            self.assertFalse(seen_actions & FORBIDDEN_ACTIONS)
            self.assertEqual(app.poll_skipped, 0)
            self.assertEqual(app.poll_started, 11)
            header = str(app.query_one("#pv-header").render())
            self.assertIn("DEMO 합성 장면 · 단계 11/11", header)

    async def test_new_session_gets_a_desk_and_existing_seats_never_move(self):
        app, clock = self.app()
        async with app.run_test(size=(160, 44)) as pilot:
            await wait_until(pilot, lambda: app.applied == 1)
            await self.step(pilot, app)
            await self.frames(pilot, app, clock)
            first = dict(app.office.slots)
            await self.step(pilot, app)
            await self.step(pilot, app)
            for sid, slot in first.items():
                self.assertEqual(app.office.slots[sid], slot)
            self.assertEqual(sorted(app.office.slots.values()), [0, 1, 2, 3])
            self.assertEqual(app.office.seats.desks(app.room_id), 4)

    async def test_room_switch_shows_that_rooms_sessions(self):
        app, clock = self.app()
        async with app.run_test(size=(160, 44)) as pilot:
            await wait_until(pilot, lambda: app.applied == 1)
            for _ in range(3):
                await self.step(pilot, app)
            main = app.room_id
            self.assertEqual(len(app.office.actors), 4)
            await pilot.press("right_square_bracket")
            await pilot.pause()
            self.assertNotEqual(app.room_id, main)
            self.assertEqual(set(app.office.actors), {"example-session-2-1"})
            await pilot.press("left_square_bracket")
            await pilot.pause()
            self.assertEqual((app.room_id, len(app.office.actors)), (main, 4))

    async def test_disconnection_and_gap_keep_seats_and_pets(self):
        app, clock = self.app()
        async with app.run_test(size=(160, 44)) as pilot:
            await wait_until(pilot, lambda: app.applied == 1)
            for _ in range(7):
                await self.step(pilot, app)
            await self.frames(pilot, app, clock)
            slots = dict(app.office.slots)
            self.assertEqual(app.office.actors["example-session-1-4"].intent, "unobservable")   # 8 gap
            await self.step(pilot, app)                                                             # 9 disconnected
            await self.frames(pilot, app, clock)
            self.assertEqual(len(app.office.actors), 4)
            self.assertEqual({a.intent for a in app.office.actors.values()}, {"unobservable"})
            self.assertTrue(all(a.dim for a in app.office.actors.values()))
            self.assertEqual(app.office.slots, slots)
            self.assertIn("관측 제한", str(app.query_one("#pv-banner").render()))
            await self.step(pilot, app)                                                             # 10 reconnect
            self.assertEqual(set(app.office.actors), set(slots))
            self.assertEqual(app.office.slots, slots)

    async def test_resize_keeps_pets_inside_and_seats_assigned(self):
        app, clock = self.app()
        async with app.run_test(size=(200, 50)) as pilot:
            await wait_until(pilot, lambda: app.applied == 1)
            for _ in range(3):
                await self.step(pilot, app)
            await self.frames(pilot, app, clock)
            slots = dict(app.office.slots)
            for size in ((120, 36), (90, 30), (200, 50)):
                await pilot.resize_terminal(*size)
                await pilot.pause()
                await self.frames(pilot, app, clock, 4)
                office = app.office
                with self.subTest(size=size):
                    self.assertEqual(office.slots, slots)
                    for sid, pose in office._poses.items():
                        self.assertGreaterEqual(pose.x, 0)
                        self.assertLessEqual(pose.x + office.actors[sid].size[0], office.geom.width + 1)
                        self.assertLessEqual(pose.foot, office.geom.px_height)

    async def test_overlapping_polls_are_skipped_and_quitting_mid_poll_is_clean(self):
        gate, entered = threading.Event(), threading.Event()
        collects = []

        class Slow:
            mode = "live"

            def collect(self):
                collects.append(1)
                entered.set()
                gate.wait(10)
                return sc.raw(sc.Step([sc.W1], *sc.sessions(sc.W1, [("codex", "working")])), sc.START), None

        app, _ = self.app(Slow())
        async with app.run_test(size=(140, 40)) as pilot:
            await wait_until(pilot, lambda: entered.is_set())
            await pilot.press("r")
            await pilot.press("r")
            self.assertEqual((app.poll_started, app.poll_skipped), (1, 2))
            await pilot.press("q")
            await pilot.pause()
        self.assertTrue(app.feed.closed)
        gate.set()
        for _ in range(100):
            if not app.feed.in_flight:
                break
            await __import__("asyncio").sleep(0.02)
        self.assertFalse(app.feed.in_flight)
        self.assertEqual(app.applied, 0)
        self.assertIsNone(app.feed.last_document)
        self.assertEqual(len(collects), 1)

    async def test_failed_poll_keeps_the_office_and_says_so(self):
        calls = []

        class Flaky:
            mode = "live"

            def collect(self):
                calls.append(1)
                if len(calls) == 2:
                    raise TimeoutError("slow orca")
                return sc.raw(sc.Step([sc.W1], *sc.sessions(sc.W1, [("codex", "working"), ("claude", "done")])),
                              sc.START + timedelta(seconds=len(calls))), None

        app, clock = self.app(Flaky())
        async with app.run_test(size=(140, 40)) as pilot:
            await wait_until(pilot, lambda: app.applied == 1)
            before = set(app.office.actors)
            await self.step(pilot, app, "r")
            self.assertIsNotNone(app.failed)
            self.assertEqual(set(app.office.actors), before)
            self.assertIn("조회 실패", str(app.query_one("#pv-banner").render()))
            await self.step(pilot, app, "r")
            self.assertIsNone(app.failed)

    async def test_frames_never_poll(self):
        app, clock = self.app()
        async with app.run_test(size=(140, 40)) as pilot:
            await wait_until(pilot, lambda: app.applied == 1)
            started = app.poll_started
            await self.frames(pilot, app, clock, 20)
            self.assertEqual(app.poll_started, started)

    async def test_clicking_a_pet_selects_it(self):
        app, clock = self.app()
        async with app.run_test(size=(160, 44)) as pilot:
            await wait_until(pilot, lambda: app.applied == 1)
            for _ in range(3):
                await self.step(pilot, app)
            await self.frames(pilot, app, clock)
            office = app.office
            target = "example-session-1-2"
            pose = office._poses[target]
            rect = office.geom.actor_rect(pose.x, pose.foot, office.actors[target].size)
            await pilot.click(OfficeView, offset=(rect.x + rect.w // 2, rect.y + rect.h // 2))
            await pilot.pause()
            self.assertEqual(office.selected, target)
            self.assertIn("example-session-1-2", str(app.query_one("#pv-list").render()))


if __name__ == "__main__":
    unittest.main()
