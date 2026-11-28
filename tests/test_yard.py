"""The yard's pure layers: pixel composition, half blocks, sprites, state mapping, requests and motion.

No UI framework, no clock and no randomness that is not seeded here.
"""

from __future__ import annotations

import random
import unittest
from dataclasses import replace
from types import SimpleNamespace

from orca_orchestrator.console import pixels as px
from orca_orchestrator.console.motion import (
    CELEBRATE_SECONDS,
    FPS,
    Actor,
    Layout,
    Motion,
)
from orca_orchestrator.console.sprites import (
    BENCH_ROWS,
    BENCH_SIZE,
    CHARACTER_PALETTE,
    CHARACTER_SIZE,
    FRAME_ROWS,
    bench_frames,
    character_frames,
    garden,
)
from orca_orchestrator.console.yard import (
    DEMO_SCENES,
    Mood,
    RequestBook,
    RequestKind,
    YardInput,
    demo_scene,
    derive_requests,
    yard_model,
)
from orca_orchestrator.tasks import BoardState, DemoTaskSource

BLACK, WHITE, RED, BG = "#000000", "#ffffff", "#ff0000", "#336633"


def board(variant: str):
    return DemoTaskSource(variant).load()


class PixelTest(unittest.TestCase):
    def test_black_pixels_stay_opaque_and_only_the_dot_is_transparent(self):
        art = px.sprite(["k.k", ".w."], {"k": BLACK, "w": WHITE})
        canvas = px.compose(px.blank(3, 2, BG), [(art, 0, 0)])
        self.assertEqual(canvas, [[BLACK, BG, BLACK], [BG, WHITE, BG]])
        cells = px.to_cells(canvas)
        self.assertEqual(cells[0][0], px.Cell("▀", BLACK, BG))  # black eye over the background, not erased
        self.assertEqual(cells[0][1], px.Cell("▀", BG, WHITE))

    def test_half_block_rules(self):
        self.assertEqual(px.cell_for(RED, RED), px.Cell("█", RED, RED))
        self.assertEqual(px.cell_for(RED, BLACK), px.Cell("▀", RED, BLACK))
        self.assertEqual(px.cell_for(None, RED), px.Cell("▄", RED, None))
        self.assertEqual(px.cell_for(RED, None), px.Cell("▀", RED, None))
        self.assertEqual(px.cell_for(None, None), px.Cell(" ", None, None))
        self.assertEqual(px.cell_for(BLACK, BLACK), px.Cell("█", BLACK, BLACK))

    def test_odd_height_leaves_the_last_lower_half_empty(self):
        cells = px.to_cells(px.blank(2, 3, RED))
        self.assertEqual(len(cells), 2)
        self.assertEqual(cells[1], [px.Cell("▀", RED, None)] * 2)
        self.assertEqual(list(px.cell_rows(1, 3)), [0, 1])
        self.assertEqual(list(px.cell_rows(2, 16)), list(range(1, 9)))
        self.assertEqual(list(px.cell_rows(3, 16)), list(range(1, 10)))  # odd start covers 9 rows

    def test_moving_leaves_no_trace_and_the_background_is_untouched(self):
        background = garden(40, 20, None)
        before = px.copy(background)
        art = character_frames()["idle"]
        first = px.compose(background, [(art, 2, 3)])
        second = px.compose(background, [(art, 20, 3)])
        self.assertEqual(background, before)
        for y in range(3, 3 + art.height):
            for x in range(2, 2 + art.width):
                if x < 20:
                    self.assertEqual(second[y][x], background[y][x])
        self.assertNotEqual(first, second)
        self.assertEqual(px.to_cells(second)[0], px.to_cells(background)[0])

    def test_off_canvas_pixels_are_clipped(self):
        art = px.sprite(["kk", "kk"], {"k": BLACK})
        canvas = px.compose(px.blank(3, 3, BG), [(art, -1, 2)])
        self.assertEqual(canvas[2], [BLACK, BG, BG])

    def test_sprites_are_validated(self):
        with self.assertRaises(ValueError):
            px.sprite(["kk", "k"], {"k": BLACK})
        with self.assertRaises(ValueError):
            px.sprite(["kz"], {"k": BLACK})
        art = px.sprite(["k."], {"k": BLACK})
        self.assertEqual(art.mirrored().rows, (".k",))
        self.assertEqual(art.recolored({"k": RED}).pixel(0, 0), RED)


class SpriteTest(unittest.TestCase):
    def test_every_frame_has_the_documented_size(self):
        for name, art in character_frames().items():
            self.assertEqual((art.width, art.height), CHARACTER_SIZE, name)
        for name, art in bench_frames().items():
            self.assertEqual((art.width, art.height), BENCH_SIZE, name)
        self.assertEqual(set(FRAME_ROWS), {"idle", "blink", "walk1", "walk2", "run1", "run2", "work1", "work2",
                                           "happy", "worried"})
        self.assertEqual(set(BENCH_ROWS), {"off", "on1", "on2"})

    def test_palette_swap_keeps_black_outline_opaque(self):
        frames = character_frames({"k": BLACK, "t": RED})
        idle = frames["idle"]
        self.assertEqual(idle.pixel(5, 6), BLACK)  # an eye
        self.assertEqual(idle.pixel(5, 2), RED)
        self.assertIsNone(idle.pixel(0, 0))
        self.assertEqual(CHARACTER_PALETTE["k"], character_frames()["idle"].pixel(5, 6))

    def test_garden_is_the_same_for_the_same_size(self):
        self.assertEqual(garden(30, 12, 20), garden(30, 12, 20))
        self.assertTrue(all(c is not None for row in garden(30, 12, 20) for c in row))


class MappingTest(unittest.TestCase):
    def test_ready_is_not_running(self):
        model = yard_model(YardInput(board=board("none")))
        self.assertIs(model.mood, Mood.READY)
        self.assertIn("실행 시작 아님", model.label)

    def test_only_a_start_event_means_working(self):
        model = yard_model(YardInput(board=board("none"), local_run=("T-1", "unit-tests")))
        self.assertIs(model.mood, Mood.WORKING)
        self.assertIn("로컬 검증", model.label)
        self.assertNotIn("Claude", model.label)
        self.assertNotIn("Codex", model.label)

    def test_unconfirmed_end_wins_over_everything(self):
        for variant in ("interrupted", "worker"):
            model = yard_model(YardInput(board=board(variant), local_run=("T-1", "unit-tests")))
            self.assertIs(model.mood, Mood.END_UNKNOWN, variant)
            self.assertEqual(model.requests[0].kind, RequestKind.END_UNKNOWN)
            self.assertEqual(model.requests[0].tab, "tab-recovery")

    def test_failure_passed_review_and_approval_are_different(self):
        failed = yard_model(YardInput(board=board("failed")))
        self.assertIs(failed.mood, Mood.FAILED)
        self.assertIn("자동 재시도 없음", failed.label)
        review = yard_model(YardInput(board=board("protected")))
        self.assertIs(review.mood, Mood.REVIEW)
        self.assertIn("최종 승인 아님", review.label)
        approved = yard_model(YardInput(board=board("approved")))
        self.assertIs(approved.mood, Mood.APPROVED)
        self.assertIn("사용자 최종 승인됨", approved.label)
        self.assertNotIn("unlinked:-", [r.rid for r in approved.requests])  # confirmed by the approval

    def test_stale_shows_the_last_observation_and_invents_nothing(self):
        model = yard_model(YardInput(board=board("none"), stale=True))
        self.assertIs(model.mood, Mood.READY)
        self.assertIn("마지막 관측", model.stale_note)
        self.assertIn("최신이 아님", model.stale_note)
        no_data = yard_model(YardInput(board=None, stale=True))
        self.assertIs(no_data.mood, Mood.NO_DATA)

    def test_blocked_dispatch_is_not_a_failure_and_reset_wait_needs_the_gate(self):
        blocked = {"claude": SimpleNamespace(outcome=SimpleNamespace(value="BLOCKED"))}
        model = yard_model(YardInput(board=board("none"), decisions=blocked))
        self.assertIs(model.mood, Mood.READY)
        waiting = {"claude": SimpleNamespace(outcome=SimpleNamespace(value="WAITING_FOR_RESET"))}
        model = yard_model(YardInput(board=board("none"), decisions=waiting))
        self.assertIs(model.mood, Mood.WAITING_RESET)
        self.assertNotRegex(model.label, r"\d+%|\d+분|\d+:\d\d")  # no invented usage, progress or reset time

    def test_unjudged_board_is_a_problem_not_idle(self):
        invalid = replace(board("none"), state=BoardState.INVALID, progress=None)
        model = yard_model(YardInput(board=invalid))
        self.assertIs(model.mood, Mood.PROBLEM)
        self.assertEqual(model.requests[0].kind, RequestKind.BOARD)


class RequestTest(unittest.TestCase):
    def test_requests_have_stable_ids_and_no_duplicates(self):
        first = derive_requests(board("worker"))
        second = derive_requests(board("worker"))
        self.assertEqual([r.rid for r in first], [r.rid for r in second])
        self.assertEqual(len({r.rid for r in first}), len(first))
        self.assertEqual([r.priority for r in first], sorted(r.priority for r in first))

    def test_read_is_not_resolved_and_new_evidence_resolves(self):
        book = RequestBook()
        book.update(derive_requests(board("worker")))
        top = book.bubble()
        self.assertEqual(top.rid, "end_unknown:T-2")
        book.mark_read(top.rid)
        self.assertIn(top.rid, [r.rid for r in book.open])  # still open
        self.assertNotEqual(book.bubble().rid, top.rid)  # the next one shows
        book.update(derive_requests(board("worker")))  # a refresh keeps the read mark
        self.assertTrue(book.is_read(top.rid))
        book.update(derive_requests(board("passed")))  # evidence changed: resolved and forgotten
        self.assertNotIn(top.rid, [r.rid for r in book.open])
        self.assertFalse(book.is_read(top.rid))

    def test_reconnect_rebuilds_every_open_request_unread(self):
        old = RequestBook()
        old.update(derive_requests(board("worker")))
        for r in old.open:
            old.mark_read(r.rid)
        fresh = RequestBook()
        fresh.update(derive_requests(board("worker")))
        self.assertEqual([r.rid for r in fresh.open], [r.rid for r in old.open])
        self.assertEqual(fresh.read, set())

    def test_demo_scenes_are_marked_as_mock(self):
        moods = [demo_scene(i).mood for i in range(len(DEMO_SCENES))]
        self.assertEqual(moods, [Mood.IDLE, Mood.WORKING, Mood.WORKING, Mood.END_UNKNOWN, Mood.PASSED])
        for i in range(len(DEMO_SCENES)):
            scene = demo_scene(i)
            self.assertTrue(scene.mock)
            self.assertTrue(all(r.kind is RequestKind.DEMO for r in scene.requests))
        self.assertIn("모의", demo_scene(1).label)


class MotionTest(unittest.TestCase):
    def actor(self, width=100, height=14, motion=Motion.NORMAL, seed=3):
        return Actor(Layout(width, height), random.Random(seed), motion, now=0.0)

    def run_until(self, actor, start, seconds):
        pose = None
        t = start
        while t <= start + seconds:
            pose = actor.advance(t)
            t += 1.0 / FPS
        return pose, t

    def test_working_runs_to_the_bench_then_works(self):
        a = self.actor()
        a.advance(0.0)
        a.set_mood(Mood.WORKING, 0.0)
        self.assertEqual(a.activity, "run")
        pose = a.advance(0.125)
        self.assertIn(pose.frame, ("run1", "run2"))
        pose, _ = self.run_until(a, 0.25, 5.0)
        self.assertEqual(a.activity, "work")
        self.assertEqual(pose.x, a.layout.work_x)
        self.assertIn(pose.frame, ("work1", "work2"))
        self.assertIn(pose.bench, ("on1", "on2"))

    def test_ready_does_not_send_the_character_to_the_bench(self):
        a = self.actor()
        a.set_mood(Mood.READY, 0.0)
        self.assertNotEqual(a.activity, "run")
        self.run_until(a, 0.0, 30.0)
        self.assertNotIn(a.activity, ("run", "work"))
        lo, hi = a.layout.wander
        self.assertTrue(lo <= a.x <= hi)

    def test_end_or_failure_while_running_shows_at_once(self):
        for mood in (Mood.END_UNKNOWN, Mood.FAILED):
            a = self.actor()
            a.advance(0.0)
            a.set_mood(Mood.WORKING, 0.0)
            a.advance(0.125)
            a.advance(0.25)
            x = a.x
            self.assertLess(x, a.layout.work_x)  # still on the way
            a.set_mood(mood, 0.3)
            pose = a.advance(0.375)
            self.assertEqual(pose.frame, "worried", mood)
            self.assertEqual(a.x, x)  # stops where it was; no arrival needed
            pose, _ = self.run_until(a, 0.5, 10.0)
            self.assertEqual(pose.frame, "worried")

    def test_passed_after_work_is_a_short_reaction_then_rest(self):
        a = self.actor(motion=Motion.REDUCED)
        a.set_mood(Mood.WORKING, 0.0)
        a.set_mood(Mood.PASSED, 1.0)
        self.assertEqual(a.advance(1.1).frame, "happy")
        self.assertIn(a.advance(1.0 + CELEBRATE_SECONDS + 0.1).frame, ("idle", "blink"))

    def test_reduced_motion_never_walks_or_runs(self):
        a = self.actor(motion=Motion.REDUCED)
        x = a.x
        self.run_until(a, 0.0, 60.0)
        self.assertEqual(a.x, x)
        a.set_mood(Mood.WORKING, 61.0)
        self.assertEqual(a.x, a.layout.work_x)  # placed, not run
        self.assertEqual(a.activity, "work")

    def test_off_is_a_still_picture(self):
        a = self.actor(motion=Motion.OFF)
        poses = {a.advance(t / 8) for t in range(400)}
        self.assertEqual(len(poses), 1)
        a.set_mood(Mood.WORKING, 1.0)
        self.assertEqual({a.advance(t / 8) for t in range(100)}, {a.advance(20.0)})

    def test_same_seed_same_walk(self):
        def path(seed):
            a = self.actor(seed=seed)
            return [a.advance(t / 8) for t in range(300)]
        self.assertEqual(path(7), path(7))
        self.assertTrue(any(p.frame in ("walk1", "walk2") for p in path(7)))

    def test_resize_keeps_everything_inside(self):
        a = self.actor(width=120)
        a.x = 100.0
        for width, height in ((60, 12), (30, 9), (24, 9), (200, 30)):
            layout = Layout(width, height)
            a.set_layout(layout)
            rect = layout.actor_rect(a.x)
            self.assertGreaterEqual(rect.x, 0)
            self.assertLessEqual(rect.x + rect.w, width)
            self.assertLessEqual(rect.y + rect.h, height)
            bx, by = layout.bubble_xy(a.x, 30, 5)
            self.assertGreaterEqual(bx, 0)
            self.assertLessEqual(bx + 30, max(width, 30))
            self.assertGreaterEqual(by, 0)
            bench = layout.bench_rect()
            if bench is not None:
                self.assertLessEqual(bench.x + bench.w, width)
                self.assertLessEqual(bench.y + bench.h, height)

    def test_bubble_never_covers_the_character_when_there_is_room(self):
        for width, height, x in ((110, 20, 5), (64, 9, 2), (64, 9, 40), (120, 9, 60)):
            layout = Layout(width, height)
            actor = layout.actor_rect(x)
            bx, by = layout.bubble_xy(x, 20, 5)
            overlap_x = bx < actor.x + actor.w and actor.x < bx + 20
            overlap_y = by < actor.y + actor.h and actor.y < by + 5
            self.assertFalse(overlap_x and overlap_y, (width, height, x))
            self.assertLessEqual(bx + 20, width)

    def test_hit_rects_use_the_drawing_rule(self):
        layout = Layout(90, 14)
        rect = layout.actor_rect(10)
        rows = px.cell_rows(layout.actor_y, CHARACTER_SIZE[1])
        self.assertEqual((rect.y, rect.h), (rows.start, len(rows)))
        self.assertTrue(rect.contains(10, rows.start))
        self.assertFalse(rect.contains(9, rows.start))
        self.assertFalse(Layout(40, 14).has_bench)
        self.assertIsNone(Layout(40, 14).bench_rect())


if __name__ == "__main__":
    unittest.main()
