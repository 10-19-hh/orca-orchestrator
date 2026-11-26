"""Change review and the person's decision: code basis, protected paths, evidence links, approval."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from orca_orchestrator import review as cli
from orca_orchestrator.orca.runner import CommandResult
from orca_orchestrator.tasks import PLAN_SCHEMA, FileTaskSource, PlanFormatError, TaskState, parse_plan
from orca_orchestrator.tasks.codebase import GitError, GitReader, compare_changes, git_environment, source_state
from orca_orchestrator.tasks.flow import StopKind, inspect, run_flow
from orca_orchestrator.tasks.paths import approval_path, flow_status_path, lock_path, tool_files
from orca_orchestrator.tasks.progress import CheckState, StaleReason
from orca_orchestrator.tasks.review import (
    ApprovalState,
    BlockKind,
    Decision,
    DecisionRefused,
    load_review,
    record_decision,
)
from orca_orchestrator.tasks.verification import VerificationNotCompleted, verify
from orca_orchestrator.tui import render as r

from test_verify import FAIL_STDERR, NOW, OK_STDERR, PYTHON, FakeVerifyRunner, HookRunner

GIT = shutil.which("git")
CHECK = {"id": "unit-tests", "description": "단위 테스트 통과", "command": "python -m unittest discover -s tests"}


def review_plan(**changes):
    plan = {
        "schema": PLAN_SCHEMA,
        "base": "base",
        "requirements": [{"id": "R-1", "title": "계산", "acceptance": "a"},
                         {"id": "R-2", "title": "테스트", "acceptance": "a"}],
        "tasks": [
            {"id": "T-1", "title": "계산 함수 구현", "requirements": ["R-1"], "paths": ["src/calc/"],
             "protected_paths": ["docs/spec.md"], "checks": [dict(CHECK)]},
            {"id": "T-2", "title": "계산 함수 테스트", "requirements": ["R-2"], "depends_on": ["T-1"],
             "paths": ["tests/"], "protected_paths": ["docs/spec.md"], "checks": [dict(CHECK)]},
        ],
    }
    plan.update(changes)
    return plan


class Repo:
    """A throwaway Git repository: the base commit has the plan and spec; code and tests are new."""

    def __init__(self, test, plan=None, results_inside=True):
        self._tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self._tmp.cleanup)
        self.test = test
        self.root = Path(self._tmp.name) / "repo"
        for rel, text in {
            ".gitignore": "__pycache__/\n",
            "docs/spec.md": "# spec\n",
            "src/calc/__init__.py": "",
            "src/calc/ops.py": "def add(a, b):\n    return a + b\n",
            "tests/test_ops.py": "import unittest\n",
        }.items():
            self.write(rel, text)
        self.plan = self.root / "plan.json"
        self.plan.write_text(json.dumps(plan or review_plan(), ensure_ascii=False), encoding="utf-8")
        outside = Path(self._tmp.name) / "out"
        outside.mkdir()
        self.results = (self.root if results_inside else outside) / "results.json"
        self.git("init", "-q", "-b", "main")
        self.git("add", ".gitignore", "docs", "plan.json")
        self.commit("base")
        self.git("tag", "base")

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def append(self, rel, text):
        with open(self.root / rel, "a", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True,
                       stdin=subprocess.DEVNULL)

    def commit(self, message):
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                 "commit", "-q", "-m", message)

    def set_plan(self, plan):
        self.plan.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    def verify(self, task, outcome=(0, OK_STDERR), runner=None):
        runner = runner or FakeVerifyRunner(outcome)
        return verify(self.plan, self.results, task, "unit-tests", repo=self.root, environ={},
                      runner_factory=runner.factory, python=PYTHON, now=lambda: NOW)

    def verify_all(self):
        self.verify("T-1")
        self.verify("T-2")

    def review(self):
        return load_review(self.plan, self.results, self.root)

    def decide(self, decision=Decision.APPROVED, **kw):
        return record_decision(self.plan, self.results, decision, repo=self.root, now=lambda: NOW, **kw)

    def board(self):
        return FileTaskSource(self.plan, self.results, self.root).load()

    def approval_exists(self):
        return approval_path(self.results).exists()


def blocker_texts(review):
    return [b.text for b in review.blockers]


def row(lines, label):
    return next(line[len(label):].lstrip() for line in lines if line.startswith(label))


@unittest.skipUnless(GIT, "git is not installed")
class ApprovalTest(unittest.TestCase):
    def test_normal_approval(self):
        repo = Repo(self)
        repo.verify_all()
        review = repo.review()
        self.assertTrue(review.approvable)
        self.assertEqual(review.state, ApprovalState.NONE)
        self.assertEqual([c.text() for c in review.task("T-1").changes],
                         ["src/calc/__init__.py (추가)", "src/calc/ops.py (추가)"])
        self.assertEqual([c.text() for c in review.task("T-2").changes], ["tests/test_ops.py (추가)"])
        self.assertEqual(review.unlinked, ())
        self.assertFalse(repo.approval_exists())

        after, path = repo.decide()
        self.assertEqual(path, approval_path(repo.results))
        self.assertEqual(after.state, ApprovalState.APPROVED)
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["decision"], "approved")
        self.assertEqual(record["source_digest"], after.code.source.digest)
        self.assertEqual([e["task"] for e in record["evidence"]], ["T-1", "T-2"])
        self.assertEqual(record["changes"][0], {"path": "src/calc/__init__.py", "status": "added", "tasks": ["T-1"]})
        lines = r.task_lines(repo.board())
        self.assertTrue(row(lines, "최종 승인").startswith("승인됨 · "))
        self.assertTrue(row(lines, "보호 경로").startswith("변경 없음 · docs/spec.md"))

    def test_failed_verification_holds_approval(self):
        repo = Repo(self)
        repo.verify("T-1", (1, FAIL_STDERR))
        review = repo.review()
        self.assertFalse(review.approvable)
        self.assertEqual(blocker_texts(review), ["T-1: unit-tests 검증 실패", "T-2: unit-tests 결과 없음"])
        with self.assertRaises(DecisionRefused) as ctx:
            repo.decide()
        self.assertIn("보류 사유 2건", ctx.exception.message)
        self.assertFalse(repo.approval_exists())
        lines = r.task_lines(repo.board())
        self.assertEqual(row(lines, "최종 승인"), "보류 · 사유 2건 · 해결 전에는 승인할 수 없음")
        self.assertIn("  · T-1: unit-tests 검증 실패", lines)

    def test_protected_path_change_holds_approval(self):
        for how in ("worktree", "committed", "deleted"):
            with self.subTest(how):
                repo = Repo(self)
                if how == "deleted":
                    (repo.root / "docs" / "spec.md").unlink()
                else:
                    repo.append("docs/spec.md", "- more\n")
                if how == "committed":
                    repo.git("add", "docs")
                    repo.commit("edit spec")
                repo.verify_all()
                review = repo.review()
                status = "삭제" if how == "deleted" else "수정"
                self.assertEqual(blocker_texts(review), [f"T-1: 보호 경로 변경 docs/spec.md ({status})",
                                                         f"T-2: 보호 경로 변경 docs/spec.md ({status})"])
                with self.assertRaises(DecisionRefused) as ctx:
                    repo.decide(confirm_unlinked=True)
                self.assertEqual(ctx.exception.message, "승인할 수 없음 · 보류 사유 2건")
                self.assertFalse(repo.approval_exists())
                lines = r.task_lines(repo.board())
                self.assertEqual(row(lines, "보호 경로"), "변경 2건 · 승인 보류")
                self.assertIn(f"  docs/spec.md ({status}) → 연결된 작업 없음 · 확인 필요 · T-1, T-2 보호 경로", lines)

    def test_code_change_after_verification(self):
        repo = Repo(self)
        repo.verify_all()
        repo.append("src/calc/ops.py", "# edited after the tests ran\n")
        review = repo.review()
        states = [cp.stale_reason for tp in review.progress.tasks for cp in tp.checks]
        self.assertEqual(states, [StaleReason.CODE_CHANGED, StaleReason.CODE_CHANGED])
        self.assertEqual(blocker_texts(review), ["T-1: unit-tests 재확인 필요 (검증 후 코드 변경)",
                                                 "T-2: unit-tests 재확인 필요 (검증 후 코드 변경)"])
        with self.assertRaises(DecisionRefused):
            repo.decide()
        lines = r.task_lines(repo.board())
        self.assertIn("  T-1  재확인     계산 함수 구현 · R-1 · 선행 작업 없음 · 재확인 필요: unit-tests (검증 후 코드 변경)",
                      lines)
        self.assertTrue(row(lines, "코드 기준").endswith("현재 코드 결과 0/2"))

    def test_uncommitted_change_with_same_head_is_not_the_same_code(self):
        repo = Repo(self)
        repo.git("add", "-A")
        repo.commit("work")
        repo.verify_all()
        head_review = repo.review()
        self.assertTrue(head_review.approvable)
        self.assertFalse(head_review.code.basis.dirty)
        repo.append("tests/test_ops.py", "# not committed\n")
        review = repo.review()
        self.assertEqual(review.code.basis.head, head_review.code.basis.head)
        self.assertTrue(review.code.basis.dirty)
        self.assertFalse(review.approvable)
        self.assertEqual(review.progress.task("T-1").state, TaskState.RECHECK)

    def test_code_change_after_approval_needs_reapproval(self):
        repo = Repo(self)
        repo.verify_all()
        repo.decide()
        repo.append("src/calc/ops.py", "# edited after approval\n")
        review = repo.review()
        self.assertEqual(review.state, ApprovalState.REAPPROVE)
        self.assertEqual(review.stale_reasons, ("승인 후 코드 변경", "보류 사유 2건"))
        self.assertEqual(row(r.task_lines(repo.board()), "최종 승인"), "재승인 필요 · 승인 후 코드 변경 · 보류 사유 2건")

        report = run_flow(repo.plan, repo.results, repo=repo.root,
                          verify_fn=lambda *a, **kw: verify(*a, runner_factory=FakeVerifyRunner((0, OK_STDERR)).factory,
                                                            python=PYTHON, environ={}, now=lambda: NOW, **kw))
        self.assertEqual(report.stop.kind, StopKind.DONE)
        review = repo.review()
        self.assertEqual((review.approvable, review.state), (True, ApprovalState.REAPPROVE))
        self.assertEqual(review.stale_reasons, ("승인 후 코드 변경",))
        self.assertEqual(repo.decide()[0].state, ApprovalState.APPROVED)

    def test_plan_change_after_approval_needs_reapproval(self):
        repo = Repo(self)
        repo.verify_all()
        repo.decide()
        plan = review_plan()
        plan["tasks"][1]["title"] = "계산 함수 테스트 보강"  # not part of any check digest
        repo.set_plan(plan)
        review = repo.review()
        self.assertEqual([t.state for t in review.progress.tasks], [TaskState.VERIFIED, TaskState.VERIFIED])
        self.assertEqual((review.state, review.stale_reasons), (ApprovalState.REAPPROVE, ("승인 후 작업 정의 변경",)))
        self.assertEqual(row(r.task_lines(repo.board()), "최종 승인"), "재승인 필요 · 승인 후 작업 정의 변경")

    def test_change_outside_scope_after_approval_needs_reapproval(self):
        repo = Repo(self)
        repo.write("README.md", "notes\n")
        repo.verify_all()
        repo.decide(confirm_unlinked=True)
        repo.append("README.md", "more\n")
        review = repo.review()
        self.assertEqual(review.stale_reasons, ("승인 후 변경 파일 달라짐",))

    def test_unlinked_change_needs_confirmation(self):
        repo = Repo(self)
        repo.write("notes.txt", "x\n")
        repo.verify_all()
        review = repo.review()
        self.assertTrue(review.approvable)
        self.assertEqual([c.path for c in review.unlinked], ["notes.txt"])
        self.assertIn("  notes.txt (추가) → 연결된 작업 없음 · 확인 필요", r.task_lines(repo.board()))
        with self.assertRaises(DecisionRefused) as ctx:
            repo.decide()
        self.assertIn("--confirm-unlinked", ctx.exception.message)
        self.assertFalse(repo.approval_exists())
        repo.decide(confirm_unlinked=True)
        record = json.loads(approval_path(repo.results).read_text(encoding="utf-8"))
        self.assertEqual(record["confirmed_unlinked"], ["notes.txt"])

    def test_hold_is_recorded_with_a_reason(self):
        repo = Repo(self)
        repo.verify("T-1", (1, FAIL_STDERR))
        with self.assertRaises(DecisionRefused):
            repo.decide(Decision.HELD)
        self.assertFalse(repo.approval_exists())
        review, _ = repo.decide(Decision.HELD, note="명세 검토 후 다시 판단")
        self.assertEqual(review.state, ApprovalState.HELD)
        self.assertTrue(row(r.task_lines(repo.board()), "최종 승인").startswith("보류 (사용자 결정) · "))
        self.assertTrue(row(r.task_lines(repo.board()), "최종 승인").endswith(" · 명세 검토 후 다시 판단"))

    def test_unreadable_decision_file_is_not_overwritten(self):
        repo = Repo(self)
        repo.verify_all()
        approval_path(repo.results).write_text("{oops", encoding="utf-8")
        review = repo.review()
        self.assertEqual(review.blockers[0].kind, BlockKind.RECORD)
        with self.assertRaises(DecisionRefused):
            repo.decide()
        self.assertEqual(approval_path(repo.results).read_text(encoding="utf-8"), "{oops")


@unittest.skipUnless(GIT, "git is not installed")
class NeverAutomaticTest(unittest.TestCase):
    def test_flow_refresh_and_passing_tests_record_nothing(self):
        repo = Repo(self)
        report = run_flow(repo.plan, repo.results, repo=repo.root,
                          verify_fn=lambda *a, **kw: verify(*a, runner_factory=FakeVerifyRunner((0, OK_STDERR)).factory,
                                                            python=PYTHON, environ={}, now=lambda: NOW, **kw))
        self.assertEqual(report.stop.kind, StopKind.DONE)
        for _ in range(3):
            board = repo.board()
        self.assertTrue(board.review.approvable)
        self.assertEqual(board.review.state, ApprovalState.NONE)
        self.assertFalse(repo.approval_exists())
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--tasks", str(repo.plan), "--results", str(repo.results), "--repo", str(repo.root)])
        self.assertEqual(code, cli.EXIT_OK)
        self.assertFalse(repo.approval_exists())


@unittest.skipUnless(GIT, "git is not installed")
class CompatibilityTest(unittest.TestCase):
    def test_old_results_are_read_but_need_recheck(self):
        repo = Repo(self)
        old = {"schema": "orca-orchestrator/verification-results@1", "results": [
            {"task": "T-1", "check": "unit-tests", "outcome": "passed", "exit_code": 0, "check_digest": "0" * 16},
        ]}
        plan = parse_plan(review_plan())
        from orca_orchestrator.tasks.contract import check_digest
        old["results"][0]["check_digest"] = check_digest(plan.tasks[0], plan.tasks[0].checks[0])
        repo.results.write_text(json.dumps(old), encoding="utf-8")
        self.assertEqual(inspect(repo.plan, repo.results).progress.task("T-1").state, TaskState.VERIFIED)
        review = repo.review()
        cp = review.progress.task("T-1").checks[0]
        self.assertEqual((cp.state, cp.stale_reason), (CheckState.STALE, StaleReason.NO_SOURCE))
        self.assertIn("T-1: unit-tests 재확인 필요 (코드 기준 없는 결과)", blocker_texts(review))
        self.assertEqual(inspect(repo.plan, repo.results, repo.root).next.task_id, "T-1")  # flow re-checks it

    def test_plan_without_base_or_with_unknown_base(self):
        for base, text in ((None, "변경 비교 불가 · 작업 정의에 비교 기준(base)이 없음"),
                           ("no-such-tag", "변경 비교 불가 · 비교 기준 no-such-tag을(를) 저장소에서 찾을 수 없음")):
            with self.subTest(base=base):
                plan = review_plan()
                if base is None:
                    del plan["base"]
                else:
                    plan["base"] = base
                repo = Repo(self, plan)
                repo.verify_all()
                review = repo.review()
                self.assertEqual(blocker_texts(review), [text, "T-1: 보호 경로 확인 불가", "T-2: 보호 경로 확인 불가"])
                self.assertEqual(row(r.task_lines(repo.board()), "보호 경로"), "확인 불가 · 2개 작업")

    def test_not_a_repository_root(self):
        repo = Repo(self)
        changes = compare_changes(repo.root / "src", "base")
        self.assertEqual(changes.error, "--repo가 Git 저장소의 최상위 폴더가 아님")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(compare_changes(tmp, "base").error, "--repo가 Git 저장소가 아님")


class SourceDigestTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for rel in ("src/pkg/a.py", "tests/test_a.py"):
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text("x = 1\n", encoding="utf-8")
        self.plan = self.root / "tests" / "plan.json"
        self.results = self.root / "tests" / "results.json"

    def digest(self):
        return source_state(self.root, tool_files(self.plan, self.results)).digest

    def test_byproducts_do_not_change_the_digest(self):
        before = self.digest()
        for rel in ("src/pkg/__pycache__/a.cpython-314.pyc", "tests/a.pyc", "tests/.hidden",
                    "tests/.pytest_cache/v", "README.md", "docs/x.md"):
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text("cache", encoding="utf-8")
        for path in (self.plan, self.results, lock_path(self.results), flow_status_path(self.results),
                     approval_path(self.results), self.root / "tests" / ".results.json.123.tmp"):
            path.write_text("{}", encoding="utf-8")
        self.assertEqual(self.digest(), before)

    def test_code_and_test_edits_change_the_digest(self):
        before = self.digest()
        for rel in ("src/pkg/a.py", "tests/test_a.py", "src/pkg/new.py", "tests/data/fixture.json"):
            with self.subTest(rel):
                path = self.root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                old = path.read_bytes() if path.exists() else None
                path.write_text("changed\n", encoding="utf-8")
                self.assertNotEqual(self.digest(), before)
                if old is None:
                    path.unlink()
                else:
                    path.write_bytes(old)
                self.assertEqual(self.digest(), before)

    def test_code_changed_while_tests_ran_is_not_recorded(self):
        (self.root / "plan.json").write_text(json.dumps(review_plan()), encoding="utf-8")
        results = self.root / "results.json"
        runner = HookRunner((0, OK_STDERR), lambda: (self.root / "src/pkg/a.py").write_text("x = 2\n", encoding="utf-8"))
        with self.assertRaises(VerificationNotCompleted) as ctx:
            verify(self.root / "plan.json", results, "T-1", "unit-tests", repo=self.root, environ={},
                   runner_factory=runner.factory, python=PYTHON, now=lambda: NOW)
        self.assertIn("검증 대상 파일(src/, tests/)이 바뀌어", ctx.exception.message)
        self.assertFalse(results.exists())


class GitReaderTest(unittest.TestCase):
    def test_only_read_commands_reach_the_runner(self):
        calls = []

        class Runner:
            def run(self, argv, *, timeout, cwd=None):
                calls.append(list(argv))
                return CommandResult(tuple(argv), 0, "", "")

        git = GitReader("repo", Runner())
        for command in ("commit", "checkout", "reset", "push", "add", "config", "stash"):
            with self.assertRaises(GitError):
                git.run(command)
        self.assertEqual(calls, [])
        git.run("diff", "--name-status")
        self.assertEqual(calls[0][:6], ["git", "--no-optional-locks", "-c", "core.quotepath=off", "-C", "repo"])

    def test_git_does_not_receive_credentials_or_binding(self):
        secret = {"OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": "y", "ORCA_TERMINAL_HANDLE": "h", "KEEP_ME": "1"}
        with mock.patch.dict("os.environ", secret):
            env = git_environment()
        self.assertEqual({k for k in secret if k in env}, {"KEEP_ME"})
        self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")


class PlanContractTest(unittest.TestCase):
    def test_paths_and_base_are_validated(self):
        task = review_plan()["tasks"][0]
        bad = [
            ({"base": "-x"}, "base"),
            ({"base": "a b"}, "base"),
            ({"tasks": [dict(task, paths=["../x"])]}, "tasks[0].paths[0]"),
            ({"tasks": [dict(task, paths=["/abs"])]}, "tasks[0].paths[0]"),
            ({"tasks": [dict(task, paths=["src\\x"])]}, "tasks[0].paths[0]"),
            ({"tasks": [dict(task, protected_paths=["C:/x"])]}, "tasks[0].protected_paths[0]"),
            ({"tasks": [dict(task, protected_paths=["a//b"])]}, "tasks[0].protected_paths[0]"),
        ]
        for change, where in bad:
            with self.subTest(change):
                with self.assertRaises(PlanFormatError) as ctx:
                    parse_plan(review_plan(**change))
                self.assertEqual(ctx.exception.where, where)
        plan = parse_plan(review_plan())
        self.assertEqual((plan.base, plan.tasks[0].paths, plan.tasks[0].protected_paths),
                         ("base", ("src/calc/",), ("docs/spec.md",)))


@unittest.skipUnless(GIT, "git is not installed")
class CliTest(unittest.TestCase):
    def call(self, repo, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--tasks", str(repo.plan), "--results", str(repo.results), "--repo", str(repo.root),
                             *extra])
        return code, out.getvalue()

    def test_show_approve_and_hold(self):
        repo = Repo(self, results_inside=False)
        code, out = self.call(repo)
        self.assertEqual(code, cli.EXIT_BLOCKED)
        self.assertIn("  · T-1: unit-tests 결과 없음", out)
        code, out = self.call(repo, "--approve")
        self.assertEqual(code, cli.EXIT_BLOCKED)
        self.assertIn("승인을 기록하지 않음 · 승인할 수 없음 · 보류 사유 2건", out)
        self.assertFalse(repo.approval_exists())

        repo.verify_all()
        code, out = self.call(repo)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("  src/calc/ops.py (추가) → T-1", out)
        self.assertIn("      변경 파일: tests/test_ops.py (추가)", out)
        self.assertIn("(현재와 같음)", out)
        self.assertIn("최종 승인: 승인 가능 · review --approve로 직접 승인 (자동 승인 없음)", out)
        code, out = self.call(repo, "--approve", "--note", "로컬 검토 완료")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("승인 기록: results.approval.json", out)
        self.assertNotIn(str(repo.root), out)
        code, out = self.call(repo, "--hold", "배포 일정 확인")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("최종 승인: 보류 (사용자 결정) · ", out)
        self.assertIn("보류 기록: results.approval.json · 승인하려면 보류 사유를 해결한 뒤 --approve로 다시 기록합니다.", out)

    def test_refusals(self):
        repo = Repo(self)
        repo.plan.write_text("{oops", encoding="utf-8")
        code, out = self.call(repo)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("검토하지 않음 · 작업 정의 파일의 JSON 형식 오류", out)
        for argv in (["--note", "x"], ["--confirm-unlinked"], ["--approve", "--hold", "x"]):
            with self.subTest(argv):
                with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                    with self.assertRaises(SystemExit):
                        cli.main(["--tasks", "p", "--results", "r", *argv])


class DemoReviewTest(unittest.TestCase):
    def lines(self, variant):
        from orca_orchestrator.tasks import DemoTaskSource
        return r.task_lines(DemoTaskSource(variant).load())

    def test_demo_scenes(self):
        cases = {
            "passed": "보류 · 사유 1건 · 해결 전에는 승인할 수 없음",
            "approved": "승인됨 · ",
            "changed": "재승인 필요 · 승인 후 코드 변경 · 보류 사유 2건",
            "protected": "보류 · 사유 1건 · 해결 전에는 승인할 수 없음",
        }
        for variant, text in cases.items():
            with self.subTest(variant):
                self.assertTrue(row(self.lines(variant), "최종 승인").startswith(text))
        self.assertIn("  · T-1: 보호 경로 변경 docs/PRD.md (수정)", self.lines("protected"))
        self.assertIn("  README.md (수정) → 연결된 작업 없음 · 확인 필요", self.lines("approved"))

    def test_review_lines_reachable_in_a_narrow_low_tab(self):
        from orca_orchestrator.tui.demo import demo_adapter
        from orca_orchestrator.tui.snapshot import collect_snapshot
        from orca_orchestrator.orca import DispatchGuard
        from orca_orchestrator.tasks import DemoTaskSource
        from test_tui import NOW as TUI_NOW
        adapter, _ = demo_adapter("connected")
        snap = collect_snapshot(adapter, DispatchGuard(), demo="connected", now=TUI_NOW,
                                tasks=DemoTaskSource("changed"))
        offset, seen = 0, []
        while True:
            frame = r.layout(snap, 49, 10, offset)
            self.assertEqual(len(frame.lines), 10)
            self.assertTrue(all(r.display_width(line) <= 49 for line in frame.lines))
            seen.extend(frame.lines[3:-1])
            if frame.offset == frame.max_offset:
                break
            offset = frame.offset + 1
        for label in ("코드 기준", "변경", "보호 경로", "최종 승인", "  · T-2: screen-tests"):
            self.assertTrue(any(line.startswith(label) for line in seen), label)


if __name__ == "__main__":
    unittest.main()
