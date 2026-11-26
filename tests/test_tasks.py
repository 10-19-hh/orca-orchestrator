from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from orca_orchestrator.tasks.contract import check_digest
from orca_orchestrator.tasks import (
    PLAN_SCHEMA,
    RESULTS_SCHEMA,
    BoardState,
    DemoTaskSource,
    FileTaskSource,
    InvalidPlanError,
    IssueCode,
    PlanFormatError,
    TaskState,
    WaitKind,
    build_board,
    evaluate,
    find_cycles,
    parse_plan,
    parse_results,
    validate,
)


def req(rid):
    return {"id": rid, "title": f"{rid} title", "acceptance": f"{rid} acceptance"}


def task(tid, reqs=("FR-01",), deps=(), checks=("unit",)):
    data = {
        "id": tid,
        "title": f"{tid} title",
        "requirements": list(reqs),
        "checks": [{"id": c, "description": f"{c} passes"} for c in checks],
    }
    if deps:
        data["depends_on"] = list(deps)
    return data


def plan_data(tasks=None, reqs=("FR-01", "FR-07")):
    return {
        "schema": PLAN_SCHEMA,
        "requirements": [req(r) for r in reqs],
        "tasks": tasks if tasks is not None else [
            task("T-1", ("FR-01",)),
            task("T-2", ("FR-07",), deps=("T-1",)),
        ],
    }


def results_data(*entries):
    return {
        "schema": RESULTS_SCHEMA,
        "results": [
            {"task": t, "check": c, "outcome": o, **({"exit_code": e} if e is not None else {})}
            for t, c, o, e in entries
        ],
    }


def stamped(plan, results):
    """Adds the current check digest to each result, as verify would record it."""

    parsed = parse_plan(plan)
    digests = {(tk.task_id, c.check_id): check_digest(tk, c) for tk in parsed.tasks for c in tk.checks}
    out = copy.deepcopy(results)
    for entry in out["results"]:
        key = (entry["task"], entry["check"])
        if key in digests:
            entry["check_digest"] = digests[key]
    return out


def codes(issues):
    return [(i.code, i.subject) for i in issues]


class ContractTest(unittest.TestCase):
    def test_valid_plan_links_ids(self):
        plan = parse_plan(plan_data())
        self.assertEqual(validate(plan), ())
        t2 = plan.tasks[1]
        self.assertEqual((t2.task_id, t2.requirement_ids, t2.dependencies), ("T-2", ("FR-07",), ("T-1",)))
        self.assertEqual(t2.checks[0].check_id, "unit")
        self.assertEqual(plan.requirements[0].acceptance, "FR-01 acceptance")

    def test_duplicate_ids(self):
        data = plan_data(
            [task("T-1"), task("T-1"), task("T-2", checks=("unit", "unit"))],
            reqs=("FR-01", "FR-01"),
        )
        self.assertEqual(
            codes(validate(parse_plan(data))),
            [
                (IssueCode.DUPLICATE_REQUIREMENT_ID, ("FR-01",)),
                (IssueCode.DUPLICATE_TASK_ID, ("T-1",)),
                (IssueCode.DUPLICATE_CHECK_ID, ("T-2", "unit")),
            ],
        )

    def test_unknown_dependency_and_requirement(self):
        data = plan_data([task("T-1", ("FR-99",)), task("T-2", deps=("T-9",))])
        self.assertEqual(
            codes(validate(parse_plan(data))),
            [
                (IssueCode.UNKNOWN_REQUIREMENT, ("T-1", "FR-99")),
                (IssueCode.UNKNOWN_DEPENDENCY, ("T-2", "T-9")),
            ],
        )

    def test_task_needs_requirement_and_check(self):
        data = plan_data([task("T-1", reqs=(), checks=())])
        self.assertEqual(
            codes(validate(parse_plan(data))),
            [
                (IssueCode.TASK_WITHOUT_REQUIREMENT, ("T-1",)),
                (IssueCode.TASK_WITHOUT_CHECKS, ("T-1",)),
            ],
        )

    def test_cycles(self):
        cases = {
            "two": ([task("A", deps=("B",)), task("B", deps=("A",))], [("A", "B", "A")]),
            "self": ([task("A", deps=("A",))], [("A", "A")]),
            "three": (
                [task("A", deps=("C",)), task("B", deps=("A",)), task("C", deps=("B",)), task("D", deps=("A",))],
                [("A", "C", "B", "A")],
            ),
            "two separate": (
                [task("A", deps=("B",)), task("B", deps=("A",)), task("C", deps=("D",)), task("D", deps=("C",))],
                [("A", "B", "A"), ("C", "D", "C")],
            ),
            "diamond is not a cycle": (
                [task("A"), task("B", deps=("A",)), task("C", deps=("A",)), task("D", deps=("B", "C"))],
                [],
            ),
        }
        for name, (tasks, expected) in cases.items():
            with self.subTest(name):
                plan = parse_plan(plan_data(tasks))
                self.assertEqual(find_cycles(plan), expected)
                self.assertEqual(
                    [i.subject for i in validate(plan) if i.code is IssueCode.DEPENDENCY_CYCLE], expected
                )

    def test_format_errors(self):
        def broken(mutate):
            data = plan_data()
            mutate(data)
            return data

        cases = {
            "schema missing": (broken(lambda d: d.pop("schema")), "$"),
            "schema wrong": (broken(lambda d: d.update(schema="other@1")), "$.schema"),
            "unknown field": (broken(lambda d: d["tasks"][1].update(depends=["T-1"])), "tasks[1]"),
            "id not string": (broken(lambda d: d["tasks"][0].update(id=1)), "tasks[0].id"),
            "id blank": (broken(lambda d: d["tasks"][0].update(id=" ")), "tasks[0].id"),
            "id padded": (broken(lambda d: d["tasks"][0].update(id="T-1 ")), "tasks[0].id"),
            "deps not list": (broken(lambda d: d["tasks"][1].update(depends_on="T-1")), "tasks[1].depends_on"),
            "repeated dep": (broken(lambda d: d["tasks"][1].update(depends_on=["T-1", "T-1"])),
                             "tasks[1].depends_on[1]"),
            "acceptance missing": (broken(lambda d: d["requirements"][0].pop("acceptance")), "requirements[0]"),
            "check missing id": (broken(lambda d: d["tasks"][0]["checks"][0].pop("id")), "tasks[0].checks[0]"),
            "not an object": ([], "$"),
        }
        for name, (data, where) in cases.items():
            with self.subTest(name):
                with self.assertRaises(PlanFormatError) as ctx:
                    parse_plan(data)
                self.assertEqual(ctx.exception.where, where)

    def test_result_format_errors(self):
        good = results_data(("T-1", "unit", "passed", 0))
        cases = {
            "outcome": ("outcome", "ok"),
            "exit code type": ("exit_code", "0"),
            "exit code bool": ("exit_code", True),
            "unknown field": ("note", "x"),
        }
        for name, (key, value) in cases.items():
            with self.subTest(name):
                data = copy.deepcopy(good)
                data["results"][0][key] = value
                with self.assertRaises(PlanFormatError):
                    parse_results(data)

    def test_result_issues(self):
        plan = parse_plan(plan_data())
        results = parse_results(results_data(
            ("T-9", "unit", "passed", None),
            ("T-1", "lint", "passed", None),
            ("T-1", "unit", "passed", 2),
            ("T-1", "unit", "failed", 1),
        ))
        self.assertEqual(
            codes(validate(plan, results)),
            [
                (IssueCode.RESULT_UNKNOWN_TASK, ("T-9",)),
                (IssueCode.RESULT_UNKNOWN_CHECK, ("T-1", "lint")),
                (IssueCode.RESULT_EXIT_CODE_MISMATCH, ("T-1", "unit", "2")),
                (IssueCode.DUPLICATE_RESULT, ("T-1", "unit")),
            ],
        )


class ProgressTest(unittest.TestCase):
    def judge(self, data, *entries):
        plan = parse_plan(data)
        results = parse_results(stamped(data, results_data(*entries)))
        self.assertEqual(validate(plan, results), ())
        return evaluate(plan, results)

    def test_prerequisite_passed_makes_successor_ready(self):
        progress = self.judge(plan_data(), ("T-1", "unit", "passed", 0))
        t1, t2 = progress.tasks
        self.assertEqual((t1.state, t1.passed, t1.total), (TaskState.VERIFIED, 1, 1))
        self.assertEqual((t2.state, t2.waiting_on), (TaskState.READY, ()))
        fr01, fr07 = progress.requirements
        self.assertTrue(fr01.satisfied)
        self.assertEqual((fr07.satisfied, fr07.open_task_ids), (False, ("T-2",)))

    def test_prerequisite_failed_makes_successor_wait(self):
        progress = self.judge(plan_data(), ("T-1", "unit", "failed", 1))
        t1, t2 = progress.tasks
        self.assertEqual((t1.state, t1.failed_checks), (TaskState.FAILED, ("unit",)))
        self.assertEqual(t2.state, TaskState.WAITING)
        self.assertEqual([(w.kind, w.task_id) for w in t2.waiting_on], [(WaitKind.PREREQUISITE_FAILED, "T-1")])
        self.assertFalse(any(r.satisfied for r in progress.requirements))

    def test_no_results(self):
        progress = self.judge(plan_data())
        t1, t2 = progress.tasks
        self.assertEqual(t1.state, TaskState.READY)
        self.assertEqual(t2.state, TaskState.WAITING)
        self.assertEqual([(w.kind, w.passed, w.total) for w in t2.waiting_on],
                         [(WaitKind.PREREQUISITE_UNVERIFIED, 0, 1)])

    def test_partial_checks_are_not_complete(self):
        data = plan_data([task("T-1", checks=("unit", "lint")), task("T-2", ("FR-07",), deps=("T-1",))])
        progress = self.judge(data, ("T-1", "unit", "passed", 0))
        t1, t2 = progress.tasks
        self.assertEqual((t1.state, t1.passed, t1.total), (TaskState.READY, 1, 2))
        self.assertEqual([(w.kind, w.passed, w.total) for w in t2.waiting_on],
                         [(WaitKind.PREREQUISITE_UNVERIFIED, 1, 2)])

    def test_one_failed_check_blocks_completion(self):
        data = plan_data([task("T-1", checks=("unit", "lint")), task("T-2", ("FR-07",), deps=("T-1",))])
        progress = self.judge(data, ("T-1", "unit", "passed", 0), ("T-1", "lint", "failed", 1))
        self.assertEqual(progress.task("T-1").state, TaskState.FAILED)
        self.assertEqual(progress.task("T-2").state, TaskState.WAITING)

    def test_waits_on_every_unfinished_prerequisite(self):
        data = plan_data([task("A"), task("B"), task("C", deps=("A", "B"))])
        progress = self.judge(data, ("A", "unit", "passed", 0), ("B", "unit", "failed", 1))
        self.assertEqual([(w.kind, w.task_id) for w in progress.task("C").waiting_on],
                         [(WaitKind.PREREQUISITE_FAILED, "B")])

    def test_requirement_without_tasks_is_unmet(self):
        progress = self.judge(plan_data([task("T-1")]), ("T-1", "unit", "passed", 0))
        fr07 = progress.requirements[1]
        self.assertEqual((fr07.task_ids, fr07.satisfied), ((), False))

    def test_evaluate_refuses_invalid_plan(self):
        plan = parse_plan(plan_data([task("A", deps=("B",)), task("B", deps=("A",))]))
        with self.assertRaises(InvalidPlanError):
            evaluate(plan)


class SourceTest(unittest.TestCase):
    def test_demo_variants(self):
        expected = {
            "passed": (TaskState.VERIFIED, TaskState.READY),
            "failed": (TaskState.FAILED, TaskState.WAITING),
            "none": (TaskState.READY, TaskState.WAITING),
        }
        for variant, states in expected.items():
            with self.subTest(variant):
                board = DemoTaskSource(variant).load()
                self.assertEqual(board.state, BoardState.OK)
                self.assertEqual(tuple(t.state for t in board.progress.tasks), states)
                self.assertEqual(board.progress.tasks[1].task.dependencies, ("T-1",))

    def test_unknown_demo_variant(self):
        with self.assertRaises(ValueError):
            DemoTaskSource("maybe")

    def test_board_states(self):
        self.assertEqual(build_board("{", None, "x").state, BoardState.UNREADABLE)
        self.assertIn("JSON 형식 오류", build_board("{", None, "x").error)
        bad = json.dumps(plan_data([task("A", deps=("A",))]))
        board = build_board(bad, None, "x")
        self.assertEqual((board.state, board.progress), (BoardState.INVALID, None))
        self.assertEqual(board.issues[0].code, IssueCode.DEPENDENCY_CYCLE)

    def test_file_source_rereads_and_hides_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            results_path = Path(tmp) / "results.json"
            plan_path.write_text(json.dumps(plan_data()), encoding="utf-8")
            results_path.write_text(json.dumps(stamped(plan_data(), results_data(("T-1", "unit", "failed", 1)))),
                                    encoding="utf-8")
            source = FileTaskSource(plan_path, results_path)
            self.assertEqual(source.load().progress.task("T-2").state, TaskState.WAITING)
            results_path.write_text(json.dumps(stamped(plan_data(), results_data(("T-1", "unit", "passed", 0)))),
                                    encoding="utf-8")
            board = source.load()
            self.assertEqual(board.progress.task("T-2").state, TaskState.READY)
            self.assertEqual(board.label, "plan.json · results.json")
            self.assertNotIn(tmp, board.label)

            missing = FileTaskSource(Path(tmp) / "nope.json").load()
            self.assertEqual(missing.state, BoardState.UNREADABLE)
            self.assertNotIn(tmp, missing.error)


class DigestTest(unittest.TestCase):
    def test_digest_tracks_check_definition_only(self):
        base = parse_plan(plan_data())
        t1 = base.tasks[0]
        digest = check_digest(t1, t1.checks[0])
        self.assertRegex(digest, r"^[0-9a-f]{16}$")
        same = copy.deepcopy(plan_data())
        same["tasks"][0]["title"] = "renamed"
        same["tasks"][0]["requirements"] = ["FR-07"]
        t = parse_plan(same).tasks[0]
        self.assertEqual(check_digest(t, t.checks[0]), digest)
        for field, value in (("description", "other"), ("command", "python -m unittest -v")):
            with self.subTest(field):
                changed = copy.deepcopy(plan_data())
                changed["tasks"][0]["checks"][0][field] = value
                t = parse_plan(changed).tasks[0]
                self.assertNotEqual(check_digest(t, t.checks[0]), digest)

    def test_digest_field_is_optional_and_checked(self):
        ok = results_data(("T-1", "unit", "passed", 0))
        self.assertIsNone(parse_results(ok)[0].check_digest)
        for bad in ("ABCDEF0123456789", "123", 5):
            with self.subTest(bad=bad):
                data = copy.deepcopy(ok)
                data["results"][0]["check_digest"] = bad
                with self.assertRaises(PlanFormatError):
                    parse_results(data)

    def test_stale_results_need_recheck(self):
        plan = parse_plan(plan_data())
        unstamped = parse_results(results_data(("T-1", "unit", "passed", 0)))
        progress = evaluate(plan, unstamped)
        self.assertEqual(progress.task("T-1").state, TaskState.RECHECK)
        self.assertEqual(progress.task("T-2").waiting_on[0].kind, WaitKind.PREREQUISITE_RECHECK)
        wrong = copy.deepcopy(stamped(plan_data(), results_data(("T-1", "unit", "failed", 1))))
        wrong["results"][0]["check_digest"] = "0" * 16
        progress = evaluate(plan, parse_results(wrong))
        self.assertEqual(progress.task("T-1").state, TaskState.RECHECK)  # a stale failure is not current either
        self.assertEqual(progress.task("T-1").stale_checks[0].stale_reason.value, "changed")


if __name__ == "__main__":
    unittest.main()
