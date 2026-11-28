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
    ACTIONS,
    WORKER_ACTIONS,
    CLAUDE_PALETTE,
    DESK_ROWS,
    DESK_SIZE,
    FRAME_NAMES,
    PETS,
    ROOM,
    desk_frames,
    pet_frames,
    pet_layers,
    pet_size,
    prop_frames,
    room,
    wall_height,
)
from orca_orchestrator.console.yard import (
    AT_BENCH,
    DEMO_SCENES,
    WAITING_ON_OTHERS,
    Mood,
    RequestBook,
    RequestKind,
    YardInput,
    demo_scene,
    derive_requests,
    yard_model,
)
from orca_orchestrator.tasks import BoardState, DemoTaskSource
from orca_orchestrator.tasks.handoff import ResumeState

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
        background = room(40, 20)
        before = px.copy(background)
        art = pet_frames("codex")["idle"]
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
    def test_every_frame_of_every_pet_has_one_size(self):
        self.assertEqual(set(PETS), {"codex", "claude"})
        self.assertEqual(pet_size("codex"), (12, 12))
        self.assertEqual(pet_size("claude"), (14, 7))
        for pet in PETS:
            frames = pet_frames(pet)
            self.assertEqual(set(frames), set(FRAME_NAMES))
            for name, art in frames.items():
                self.assertEqual((art.width, art.height), pet_size(pet), (pet, name))
            self.assertEqual(len({art.rows for art in frames.values()}), len(FRAME_NAMES), pet)  # all differ
        for name, art in desk_frames().items():
            self.assertEqual((art.width, art.height), DESK_SIZE, name)
        self.assertEqual(set(DESK_ROWS), {"off", "on1", "on2"})

    def test_every_action_has_a_body_and_known_props(self):
        props = prop_frames()
        for pet in PETS:
            for action in ACTIONS:
                for phase in range(4):
                    body, layers = pet_layers(pet, action, phase)
                    self.assertIn(body, FRAME_NAMES, (pet, action))
                    for prop, _, _ in layers:
                        self.assertIn(prop, props, (pet, action))
        self.assertEqual([p for p, _, _ in pet_layers("codex", "type")[1]], ["screen1", "keyboard"])
        self.assertEqual([p for p, _, _ in pet_layers("claude", "build")[1]], ["helmet", "hammer"])
        self.assertEqual(pet_layers("claude", "error")[0], "error")
        self.assertNotEqual(pet_layers("codex", "think", 0)[1], pet_layers("codex", "think", 1)[1])  # dots move

    def test_palette_swap_keeps_black_eyes_opaque(self):
        idle = pet_frames("claude", {"k": BLACK, "o": RED})["idle"]
        self.assertEqual(idle.pixel(4, 1), BLACK)  # an eye
        self.assertEqual(idle.pixel(2, 0), RED)
        self.assertIsNone(idle.pixel(0, 0))
        self.assertEqual(pet_frames("claude")["idle"].pixel(4, 1), CLAUDE_PALETTE["k"])

    def test_room_is_dark_fixed_and_fully_drawn(self):
        self.assertEqual(room(60, 24), room(60, 24))
        pixels = room(60, 24)
        self.assertTrue(all(c is not None for row in pixels for c in row))
        wall = wall_height(24)
        self.assertEqual(pixels[wall - 1][30], ROOM["base"])
        floor = {c for row in pixels[wall:] for c in row}
        self.assertTrue(floor >= {ROOM["tile_a"], ROOM["tile_b"], ROOM["rug"]})

        def luminance(color):
            r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        share = sum(luminance(c) < 60 for row in pixels for c in row) / (60 * 24)
        self.assertGreater(share, 0.85)  # a dark room


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
    def actor(self, width=100, height=16, motion=Motion.NORMAL, seed=3, size=(12, 12), slot=0):
        a = Actor(Layout(width, height), random.Random(seed), motion, now=0.0, size=size, slot=slot)
        return a

    def run_until(self, actor, start, seconds):
        pose = None
        t = start
        while t <= start + seconds:
            pose = actor.advance(t)
            t += 1.0 / FPS
        return pose, t

    def test_working_runs_to_the_desk_then_watches(self):
        # A local check: the pets go to the monitor and watch it (they used to type and build, as if coding).
        for slot, action in ((0, "watch"), (1, "watch")):
            a = self.actor(slot=slot)
            a.advance(0.0)
            a.set_mood(Mood.WORKING, 0.0)
            self.assertEqual(a.activity, "run")
            self.assertEqual(a.advance(0.125).action, "run")
            pose, _ = self.run_until(a, 0.25, 6.0)
            self.assertEqual(a.activity, "work")
            self.assertEqual((pose.x, pose.foot), tuple(int(v) for v in a.work_spot))
            self.assertEqual(pose.action, action)

    def test_work_spots_flank_the_desk(self):
        layout = Layout(100, 16)
        dx, _ = layout.desk_xy
        left = layout.work_spot(0, (12, 12))
        right = layout.work_spot(1, (14, 7))
        self.assertLessEqual(left[0] + 12, dx)
        self.assertGreaterEqual(right[0], dx + DESK_SIZE[0])
        self.assertLessEqual(right[0] + 14, layout.width)
        self.assertEqual(left[1], layout.desk_foot)

    def test_ready_does_not_send_the_pets_to_the_desk(self):
        a = self.actor()
        a.set_mood(Mood.READY, 0.0)
        self.assertNotEqual(a.activity, "run")
        self.run_until(a, 0.0, 30.0)
        self.assertNotIn(a.activity, ("run", "work"))

    def test_pets_roam_in_two_directions_and_avoid_the_desk(self):
        a = self.actor(width=120, height=20)
        a.set_mood(Mood.IDLE, 0.0)
        poses = [a.advance(t / FPS) for t in range(FPS * 60)]
        xs, feet = [p.x for p in poses], [p.foot for p in poses]
        self.assertGreater(max(xs) - min(xs), 30)
        self.assertGreater(max(feet) - min(feet), 6)  # toward and away from the viewer
        flo, fhi = a.layout.feet(12)
        self.assertTrue(all(flo <= f <= fhi for f in feet))
        walking = [p for p in poses if p.action == "walk"]
        self.assertGreater(len(walking), len(poses) // 4)
        self.assertEqual({p.dy for p in walking}, {0, 1})
        resting = {p.action for p in poses if p.action not in ("walk", "run")}
        self.assertTrue(resting <= {"idle", "blink", "sweep", "think"})
        self.assertGreater(len(resting), 1)
        for _ in range(50):
            x, foot = a._pick_spot()
            self.assertFalse(a.layout.blocked(x, foot, 12))

    def test_end_or_failure_while_running_shows_at_once(self):
        for mood, action in ((Mood.END_UNKNOWN, "question"), (Mood.FAILED, "error")):
            a = self.actor()
            a.advance(0.0)
            a.set_mood(Mood.WORKING, 0.0)
            a.advance(0.125)
            a.advance(0.25)
            here = (a.x, a.foot)
            self.assertNotEqual(here, a.work_spot)  # still on the way
            a.set_mood(mood, 0.3)
            self.assertEqual(a.advance(0.375).action, action, mood)
            self.assertEqual((a.x, a.foot), here)  # stops where it was
            pose, _ = self.run_until(a, 0.5, 10.0)
            self.assertEqual(pose.action, action)

    def test_unconfirmed_end_fidgets_in_place(self):
        a = self.actor()
        a.set_mood(Mood.END_UNKNOWN, 0.0)
        poses = [a.advance(t / FPS) for t in range(FPS * 4)]
        self.assertEqual({p.action for p in poses}, {"question"})
        self.assertEqual(len({p.x for p in poses}), 2)
        self.assertEqual(len({p.foot for p in poses}), 1)

    def test_passed_after_work_is_a_short_reaction_then_rest(self):
        a = self.actor(motion=Motion.REDUCED)
        a.set_mood(Mood.WORKING, 0.0)
        a.set_mood(Mood.PASSED, 1.0)
        self.assertEqual(a.advance(1.1).action, "happy")
        self.assertNotEqual(a.advance(1.0 + CELEBRATE_SECONDS + 0.1).action, "happy")

    def test_rest_actions_follow_the_state(self):
        for mood, expected in ((Mood.NO_DATA, "think"), (Mood.WAITING_RESET, "sleep")):
            a = self.actor(motion=Motion.REDUCED)
            a.set_mood(Mood.IDLE, 0.0)
            a.set_mood(mood, 0.0)
            self.assertEqual(a.advance(0.5).action, expected)
        a = self.actor(motion=Motion.REDUCED, slot=0)
        a.set_mood(Mood.REVIEW, 0.0)
        self.assertEqual(a.advance(0.5).action, "notify")

    def test_reduced_motion_never_walks_or_runs(self):
        a = self.actor(motion=Motion.REDUCED)
        here = (a.x, a.foot)
        self.run_until(a, 0.0, 60.0)
        self.assertEqual((a.x, a.foot), here)
        a.set_mood(Mood.WORKING, 61.0)
        self.assertEqual((a.x, a.foot), a.work_spot)  # placed, not run
        self.assertEqual(a.activity, "work")

    def test_off_is_a_still_picture(self):
        a = self.actor(motion=Motion.OFF)
        self.assertEqual(len({a.advance(t / 8) for t in range(400)}), 1)
        a.set_mood(Mood.WORKING, 1.0)
        self.assertEqual({a.advance(t / 8) for t in range(100)}, {a.advance(20.0)})

    def test_same_seed_same_walk(self):
        def path(seed):
            a = self.actor(seed=seed)
            return [a.advance(t / 8) for t in range(300)]
        self.assertEqual(path(7), path(7))

    def test_pets_start_apart_once_the_room_has_a_size(self):
        codex = Actor(Layout(0, 0), random.Random(1), size=(12, 12), slot=0, start=(0.25, 0.55))
        claude = Actor(Layout(0, 0), random.Random(2), size=(14, 7), slot=1, start=(0.55, 0.8))
        layout = Layout(120, 16)
        codex.set_layout(layout)
        claude.set_layout(layout)
        self.assertGreaterEqual(abs(codex.x - claude.x), 14)

    def test_resize_keeps_everything_inside(self):
        a = self.actor(width=120, height=20)
        a.x, a.foot = 100.0, 38.0
        for width, height in ((60, 12), (30, 9), (24, 9), (200, 30)):
            layout = Layout(width, height)
            a.set_layout(layout)
            rect = layout.actor_rect(a.x, a.foot, a.size)
            self.assertGreaterEqual(rect.x, 0)
            self.assertLessEqual(rect.x + rect.w, width)
            self.assertGreaterEqual(rect.y, 0)
            self.assertLessEqual(rect.y + rect.h, height)
            bx, by = layout.bubble_xy(rect, 30, 5)
            self.assertGreaterEqual(bx, 0)
            self.assertLessEqual(bx + 30, max(width, 30))
            self.assertGreaterEqual(by, 0)
            desk = layout.desk_rect()
            if desk is not None:
                self.assertLessEqual(desk.x + desk.w, width)
                self.assertLessEqual(desk.y + desk.h, height)

    def test_bubble_never_covers_the_pet_when_there_is_room(self):
        for width, height, x, foot in ((110, 20, 5, 30), (64, 9, 2, 16), (64, 9, 40, 16), (120, 12, 60, 20)):
            layout = Layout(width, height)
            actor = layout.actor_rect(x, foot, (12, 12))
            bx, by = layout.bubble_xy(actor, 20, 5)
            overlap_x = bx < actor.x + actor.w and actor.x < bx + 20
            overlap_y = by < actor.y + actor.h and actor.y < by + 5
            self.assertFalse(overlap_x and overlap_y, (width, height, x))

    def test_hit_rects_use_the_drawing_rule(self):
        layout = Layout(90, 14)
        rect = layout.actor_rect(10, 20, (12, 12))
        rows = px.cell_rows(20 - 12 + 1, 12)
        self.assertEqual((rect.y, rect.h), (rows.start, len(rows)))
        self.assertTrue(rect.contains(10, rows.start))
        self.assertFalse(rect.contains(9, rows.start))
        self.assertFalse(Layout(40, 14).has_desk)
        self.assertIsNone(Layout(40, 14).desk_rect())



class SemanticTest(unittest.TestCase):
    """What the pets show never claims more than the judgments say (Phase 1A)."""

    def actor(self, motion=Motion.NORMAL, slot=0, seed=5):
        return Actor(Layout(100, 16), random.Random(seed), motion, now=0.0, size=(12, 12), slot=slot)

    def poses(self, actor, start, seconds):
        out, t = [], start
        while t <= start + seconds:
            out.append(actor.advance(t))
            t += 1.0 / FPS
        return out, t

    def test_no_mood_ever_shows_coding_or_building(self):
        # No Worker is observed in this build, so no state, motion mode, slot or mood sequence may draw one.
        for motion in Motion:
            for slot in (0, 1):
                a, t = self.actor(motion, slot), 0.0
                for mood in list(Mood) + list(reversed(Mood)):
                    a.set_mood(mood, t)
                    a.cheer(t)
                    poses, t = self.poses(a, t, 4.0)
                    actions = {p.action for p in poses}
                    self.assertFalse(actions & WORKER_ACTIONS, (motion, slot, mood, actions))
                    self.assertLessEqual(actions, set(ACTIONS))

    def test_local_check_is_watched_at_the_monitor(self):
        self.assertEqual(AT_BENCH, frozenset({Mood.WORKING}))
        for motion in Motion:
            a = self.actor(motion)
            a.advance(0.0)
            a.set_mood(Mood.WORKING, 0.0)
            poses, _ = self.poses(a, 0.125, 6.0)
            self.assertEqual(poses[-1].action, "watch")
            self.assertEqual((poses[-1].x, poses[-1].foot), tuple(int(v) for v in a.work_spot))
            body, props = pet_layers("codex", "watch", poses[-1].phase)
            self.assertEqual(props, [])  # no screen, keyboard, helmet or hammer
            self.assertIn(body, ("idle", "blink"))

    def test_other_session_keeps_the_pets_in_place_and_away_from_the_desk(self):
        self.assertEqual(WAITING_ON_OTHERS, frozenset({Mood.OTHER_SESSION}))
        self.assertNotIn(Mood.OTHER_SESSION, AT_BENCH)
        for motion in Motion:
            for slot in (0, 1):
                a = self.actor(motion, slot)
                a.advance(0.0)
                a.set_mood(Mood.IDLE, 0.0)
                self.poses(a, 0.125, 3.0)
                spot = (a.x, a.foot)
                a.set_mood(Mood.OTHER_SESSION, 3.5)
                poses, _ = self.poses(a, 3.5, 6.0)
                self.assertIsNone(a.target)
                self.assertEqual((a.x, a.foot), spot)
                self.assertEqual({p.action for p in poses}, {"watch"})

    def test_other_session_label_names_no_agent(self):
        b = board("passed")
        first = b.resume.tasks[0]
        running = replace(first, state=ResumeState.RUNNING, attempt=None)  # another session's attempt
        other = yard_model(YardInput(board=replace(b, resume=replace(b.resume, tasks=(running, *b.resume.tasks[1:])))))
        self.assertIs(other.mood, Mood.OTHER_SESSION)
        self.assertIn("다른 세션 실행 중", other.label)
        self.assertIn("실행 주체는 상세에서 확인", other.label)
        self.assertNotRegex(other.label, "Codex|Claude|코딩|작업 중")

    def test_failure_and_unconfirmed_end_never_look_successful(self):
        for mood, expected in ((Mood.FAILED, "error"), (Mood.PROBLEM, "error"), (Mood.END_UNKNOWN, "question")):
            for motion in Motion:
                a = self.actor(motion)
                a.advance(0.0)
                a.set_mood(Mood.WORKING, 0.0)
                self.poses(a, 0.125, 6.0)
                a.set_mood(mood, 7.0)
                a.cheer(7.0)
                poses, _ = self.poses(a, 7.0, 4.0)
                self.assertEqual({p.action for p in poses}, {expected}, (mood, motion))

    def test_mock_scenes_say_so_and_the_local_check_scene_is_not_a_worker(self):
        for i, (_, mood, _) in enumerate(DEMO_SCENES):
            model = demo_scene(i)
            self.assertTrue(model.mock)
            self.assertIn("모의", model.label)
            if mood is Mood.WORKING:
                self.assertIn("로컬 검증", model.label)
                self.assertIn("Worker 아님", model.label)


if __name__ == "__main__":
    unittest.main()
