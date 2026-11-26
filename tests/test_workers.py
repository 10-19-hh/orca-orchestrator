"""Worker adapter, preflight and Worker recovery. Every Worker reply here comes from a fake executor.

Reply shapes follow the Orca v1.4.264 release source (concise ``--json`` views);
none of them was observed from a real Worker.
"""

from __future__ import annotations

import io
import json
import re
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone

from orca_orchestrator import handoff as handoff_cli
from orca_orchestrator import preflight
from orca_orchestrator.orca.adapter import OrcaCliAdapter
from orca_orchestrator.orca.dispatch import (
    DispatchGuard,
    DispatchPolicy,
    DispatchRequest,
    ExecutionApproval,
    Outcome,
    Provider,
    ProviderBillingConfirmation,
    Reason,
    WorkDispatcher,
)
from orca_orchestrator.orca.errors import InvalidResponseError, OrcaCommandError, OutcomeUnknownError
from orca_orchestrator.orca.runner import CommandLaunchError, CommandResult, CommandTimeout
from orca_orchestrator.orca.workers import (
    WORKER_CONTRACT,
    Evidence,
    StartDisposition,
    WorkerEnd,
    WorkerObservation,
    WorkerStartRequest,
    parse_worker_start,
    worker_show_args,
    worker_start_args,
)
from orca_orchestrator.tasks.flow import StopKind, inspect, resume_view
from orca_orchestrator.tasks.handoff import (
    AttemptState,
    ClaimRefused,
    ProbeState,
    ResumeState,
    RunEnd,
    claim_task,
    read_attempts,
    run_line,
)
from orca_orchestrator.tasks.paths import handoff_path
from orca_orchestrator.tasks.source import DemoTaskSource
from orca_orchestrator.tasks.worker_runs import (
    WORKER_CHECK,
    WorkerProbe,
    observe_runs,
    record_worker_start,
    worker_executor,
)
from orca_orchestrator.tui import app
from orca_orchestrator.tui import render as r
from orca_orchestrator.tui.demo import demo_adapter
from orca_orchestrator.tui.readonly import ReadOnlyRunner, ReadOnlyViolation
from orca_orchestrator.tui.snapshot import collect_snapshot

from support import BOUND_ENV, FAKE_CLI, RUN_CURRENT, STATUS, UNBOUND_ENV, FakeRunner, fixture_result
from test_flow import Scripted, chain_plan, flow
from test_verify import ROOT, Workspace

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
REQUEST = WorkerStartRequest(preflight.MINIMAL_SPEC, "claude", preflight.MINIMAL_TITLE, "run_0123456789ab")


def reply(payload, exit_code=0, stderr=""):
    return CommandResult(("orca",), exit_code, json.dumps(payload) if payload is not None else "", stderr)


READY = reply({"ok": True, "result": {"dispatchId": "dsp_test01", "taskId": "task_t1", "runId": "run_0123456789ab",
                                      "state": "ready", "agent": "claude", "mode": "pty",
                                      "next": "End your turn; Orca wakes you when it reports."}})
FAILED_START = reply({"ok": False, "result": {"dispatchId": "dsp_test02", "taskId": "task_t1", "state": "failed",
                                              "failedStage": "agent_readiness", "lastError": "agent did not start",
                                              "residualResources": ["terminal term_x"]}}, 1,
                     "error: start_failed: agent_readiness: agent did not start\n")
UNOBSERVED = reply({"ok": True, "result": {"dispatchId": "dsp_test03", "taskId": "task_t1", "state": "outcome_unknown",
                                           "next": "Task typed in but the worker hasn't been seen starting"}})
REFUSED = reply({"ok": False, "error": {"code": "no_active_sender_terminal", "message": "Could not determine the "
                                        "calling terminal for this orchestration command."}}, 1)
EMPTY = CommandResult(("orca",), 1, "", "")


def shown(state, liveness, dispatch="dsp_test01", stage="working"):
    body = {"dispatchId": dispatch, "taskId": "task_t1", "state": state, "stage": stage}
    if liveness is not None:
        body["liveness"] = liveness
    return (0, json.dumps({"ok": True, "result": body}), "")


class FakeSend:
    """The fake executor: answers worker-start with a canned reply (or raises) and counts sends."""

    def __init__(self, answer):
        self.answer = answer
        self.sent: list[list[str]] = []

    def __call__(self, args):
        self.sent.append(list(args))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


class FakeShow:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked: list[str] = []

    def __call__(self, dispatch_id):
        self.asked.append(dispatch_id)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer


def obs(state, liveness, dispatch="dsp_test01"):
    return WorkerObservation(dispatch, state, "working", liveness)


def take(ws, task_id="T-1"):
    plan = inspect(ws.plan, ws.results).plan
    return claim_task(ws.plan, ws.results, plan, task_id, (WORKER_CHECK,), repo=ws.root,
                      executor=worker_executor("claude"))


def send_worker(ws, send, task_id="T-1", cut=False):
    """One session: take the task, send one Worker start, then end normally (or die if ``cut``)."""

    claim = take(ws, task_id)
    receipt = record_worker_start(claim, REQUEST, send)
    if cut:
        claim._held.release()  # what the OS does when the session's process dies
    else:
        claim.close()
    return receipt


def resume(ws, probe=None):
    view = inspect(ws.plan, ws.results, ws.root)
    return resume_view(ws.plan, ws.results, view, probe)


def last(ws, task_id="T-1"):
    return [a for a in read_attempts(ws.results)[0] if a.task == task_id][-1]


# --- command shape and replies ------------------------------------------------------------


class CommandShapeTest(unittest.TestCase):
    def test_worker_start_argv_is_built_exactly(self):
        self.assertEqual(worker_start_args(REQUEST), [
            "orchestration", "worker-start", "--spec", preflight.MINIMAL_SPEC, "--task-title", preflight.MINIMAL_TITLE,
            "--worktree", "current", "--agent", "claude", "--run", "run_0123456789ab", "--json"])
        bare = worker_start_args(WorkerStartRequest("say hi", "codex", timeout_ms=60000))
        self.assertEqual(bare, ["orchestration", "worker-start", "--spec", "say hi", "--worktree", "current",
                                "--agent", "codex", "--timeout-ms", "60000", "--json"])
        for argv in (worker_start_args(REQUEST), bare):
            self.assertFalse({"--from", "--retry-request", "--retry-of", "--terminal"} & set(argv))

    def test_bad_requests_are_refused_before_anything_is_built(self):
        for bad in (WorkerStartRequest(" ", "claude"), WorkerStartRequest("x", "cursor"),
                    WorkerStartRequest("x", "claude", worktree="new-child"),
                    WorkerStartRequest("x", "claude", timeout_ms=0)):
            with self.assertRaises(ValueError):
                worker_start_args(bad)
        self.assertEqual(worker_show_args("dsp_1"), ["orchestration", "worker-show", "--dispatch", "dsp_1", "--json"])
        for bad in ("", "--json", "dsp 1", "dsp;rm"):
            with self.assertRaises(ValueError):
                worker_show_args(bad)

    def test_contract_separates_documented_from_observed(self):
        observed = [i.name for i in WORKER_CONTRACT if i.observed is Evidence.VERIFIED]
        self.assertEqual(observed, ["Run 생성·조회와 Coordinator 바인딩"])  # no Worker behaviour claimed as seen
        self.assertTrue(all(i.documented is not Evidence.UNVERIFIED for i in WORKER_CONTRACT))
        start = next(i for i in WORKER_CONTRACT if i.name == "worker-start 응답")
        self.assertEqual((start.documented, start.observed), (Evidence.SOURCE_VERIFIED, Evidence.UNVERIFIED))


class ReceiptTest(unittest.TestCase):
    def test_only_ready_with_a_dispatch_counts_as_started(self):
        got = parse_worker_start(READY)
        self.assertEqual((got.disposition, got.dispatch_id, got.task_id, got.run_id),
                         (StartDisposition.STARTED, "dsp_test01", "task_t1", "run_0123456789ab"))
        no_id = reply({"ok": True, "result": {"state": "ready"}})
        self.assertIs(parse_worker_start(no_id).disposition, StartDisposition.UNKNOWN)

    def test_failed_unobserved_and_unreadable_replies_are_unknown(self):
        failed = parse_worker_start(FAILED_START)
        self.assertEqual((failed.disposition, failed.dispatch_id, failed.failed_stage),
                         (StartDisposition.UNKNOWN, "dsp_test02", "agent_readiness"))
        self.assertIn("다시 보내지 않음", failed.text())
        unobserved = parse_worker_start(UNOBSERVED)  # exit 0, but the turn was not seen starting
        self.assertEqual((unobserved.disposition, unobserved.dispatch_id), (StartDisposition.UNKNOWN, "dsp_test03"))
        for bad in (EMPTY, CommandResult(("orca",), 0, "Orca says hello", ""), reply([1, 2]),
                    reply({"ok": False, "error": {"code": "internal_error"}}, 1), reply({"ok": True}),
                    reply({"ok": True, "result": {"dispatchId": "bad id;", "state": "ready"}})):
            got = parse_worker_start(bad)
            self.assertIs(got.disposition, StartDisposition.UNKNOWN, bad.stdout)
            self.assertIsNone(got.dispatch_id)

    def test_refused_before_a_dispatch_exists_is_not_started(self):
        got = parse_worker_start(REFUSED)
        self.assertEqual((got.disposition, got.dispatch_id), (StartDisposition.NOT_STARTED, None))
        self.assertTrue(got.error.startswith("no_active_sender_terminal"))


class ObservationTest(unittest.TestCase):
    def test_exited_needs_settled_state_and_proven_exit(self):
        cases = [
            (("succeeded", "exited"), WorkerEnd.EXITED),
            (("stopped", "exited (operator_close)"), WorkerEnd.EXITED),
            (("working", "live"), WorkerEnd.RUNNING),
            (("succeeded", "live"), WorkerEnd.UNKNOWN),  # settled, but the agent process is still there
            (("working", "exited"), WorkerEnd.UNKNOWN),  # process gone, Dispatch not settled
            (("failed", "unverifiable (stale_status)"), WorkerEnd.UNKNOWN),
            (("succeeded", None), WorkerEnd.UNKNOWN),  # liveness never evaluated
            ((None, "live"), WorkerEnd.UNKNOWN),
            (("succeeded", "dead"), WorkerEnd.UNKNOWN),  # unknown verdict word
        ]
        for (state, liveness), end in cases:
            self.assertIs(obs(state, liveness).end(), end, (state, liveness))

    def test_adapter_reads_worker_show_and_reports_failures(self):
        runner = FakeRunner({("orchestration", "worker-show", "--dispatch", "dsp_test01", "--json"):
                             shown("succeeded", "exited", stage="released")})
        adapter = OrcaCliAdapter(runner, FAKE_CLI, environ=UNBOUND_ENV)  # no binding needed to read
        seen = adapter.worker_show("dsp_test01")
        self.assertEqual((seen.state, seen.stage, seen.liveness, seen.end()),
                         ("succeeded", "released", "exited", WorkerEnd.EXITED))
        self.assertEqual(runner.calls, [(FAKE_CLI, "orchestration", "worker-show", "--dispatch", "dsp_test01", "--json")])

        def timeout():
            raise CommandTimeout("timed out after 10s")

        for answer, error in ((timeout, OutcomeUnknownError),
                              ((1, json.dumps({"ok": False, "error": {"code": "dispatch_not_found",
                                                                      "message": "no such dispatch"}}), ""),
                               OrcaCommandError),
                              (shown("working", "live", dispatch="dsp_other"), InvalidResponseError),
                              ((0, "", ""), InvalidResponseError)):
            runner = FakeRunner({("orchestration", "worker-show", "--dispatch", "dsp_test01", "--json"): answer})
            with self.assertRaises(error):
                OrcaCliAdapter(runner, FAKE_CLI, environ=UNBOUND_ENV).worker_show("dsp_test01")


# --- the gate ------------------------------------------------------------------------------


def confirmed(**kw):
    return ProviderBillingConfirmation(subscription_auth_verified=True, billing_path_verified=True,
                                       extra_usage_disabled=True, auto_recharge_disabled=True,
                                       api_key_environment_cleared=True, **kw)


class GateTest(unittest.TestCase):
    def evaluate(self, policy, environ=None):
        status = demo_adapter("connected")[0].status()
        return DispatchGuard(policy).evaluate(DispatchRequest(Provider.CLAUDE, "T-1"), runtime=status,
                                              binding_present=True, environ=environ or {}, now=NOW)

    def test_missing_approval_blocks_even_when_everything_else_is_confirmed(self):
        policy = DispatchPolicy(real_dispatch_enabled=True, experiment_4a_status="PASSED",
                                providers={Provider.CLAUDE: confirmed()})
        decision = self.evaluate(policy)
        self.assertIs(decision.outcome, Outcome.BLOCKED)
        self.assertEqual(decision.reason_codes,
                         (Reason.EXECUTION_APPROVAL_MISSING, Reason.WORKER_DISPATCH_CONTRACT_UNVERIFIED))

    def test_this_build_never_allows_sending(self):
        policy = DispatchPolicy(real_dispatch_enabled=True, experiment_4a_status="PASSED",
                                execution_approval=ExecutionApproval("user", NOW, "4-B"),
                                providers={Provider.CLAUDE: confirmed()})
        decision = self.evaluate(policy)
        self.assertEqual(decision.reason_codes, (Reason.WORKER_DISPATCH_CONTRACT_UNVERIFIED,))
        runner = FakeRunner({STATUS: fixture_result("status_ready")})
        dispatcher = WorkDispatcher(OrcaCliAdapter(runner, FAKE_CLI, environ=BOUND_ENV), DispatchGuard(policy))
        self.assertIs(dispatcher.dispatch(DispatchRequest(Provider.CLAUDE, "T-1"), now=NOW).outcome, Outcome.BLOCKED)
        self.assertEqual([c[1:] for c in runner.calls], [STATUS])

    def test_no_production_code_sends_worker_start(self):
        src = ROOT / "src" / "orca_orchestrator"
        literal, caller = [], []
        for path in src.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(src).as_posix()
            if re.search(r"""["']worker-start["']""", text):
                literal.append(rel)
            if "record_worker_start(" in text and rel != "tasks/worker_runs.py":
                caller.append(rel)
        self.assertEqual(literal, ["orca/workers.py"])  # built only, by the argv builder
        self.assertEqual(caller, [])  # the recorder is only driven by tests' fake executor


# --- preflight --------------------------------------------------------------------------------


def run_main(module, argv, **kw):
    out = io.StringIO()
    with redirect_stdout(out):
        code = module.main(argv, **kw)
    return code, out.getvalue().splitlines()


class PreflightTest(unittest.TestCase):
    def test_reads_status_and_run_only_and_blocks(self):
        runner = FakeRunner({STATUS: fixture_result("status_ready"),
                             RUN_CURRENT: fixture_result("run_current_coordinator")})
        adapter = OrcaCliAdapter(ReadOnlyRunner(runner), FAKE_CLI, environ=BOUND_ENV)
        pf = preflight.run_preflight(adapter, Provider.CLAUDE, preflight.choose_task(_args()), offline=False, now=NOW)
        self.assertEqual([c[1:] for c in runner.calls], [STATUS, RUN_CURRENT])
        self.assertTrue(pf.blocked)
        self.assertIn(Reason.EXECUTION_APPROVAL_MISSING, pf.decision.reason_codes)
        self.assertEqual(pf.command[:2], ["orchestration", "worker-start"])
        self.assertEqual(pf.command[-3:], ["--run", "run_0123456789ab", "--json"])
        text = "\n".join(preflight.lines(pf))
        self.assertIn("run_0123456789ab · 현재 터미널에 바인딩됨", text)
        self.assertIn("[승인] Worker 실행에 대한 사용자 승인 없음", text)
        self.assertIn("[Worker 실행 계약] worker-start 계약 미검증", text)
        self.assertIn("판정: 전송 차단", text)

    def test_offline_runs_nothing_and_unbound_skips_the_run(self):
        runner = FakeRunner({})  # any command would fail the test
        adapter = OrcaCliAdapter(ReadOnlyRunner(runner), FAKE_CLI, environ=BOUND_ENV)
        pf = preflight.run_preflight(adapter, Provider.CODEX, preflight.choose_task(_args()), offline=True, now=NOW)
        self.assertEqual(runner.calls, [])
        self.assertIn("  [Orca 환경] Runtime을 조회하지 않음 (--offline)", preflight.lines(pf))
        runner = FakeRunner({STATUS: fixture_result("status_ready")})
        adapter = OrcaCliAdapter(ReadOnlyRunner(runner), FAKE_CLI, environ=UNBOUND_ENV)
        pf = preflight.run_preflight(adapter, Provider.CLAUDE, preflight.choose_task(_args()), offline=False, now=NOW)
        self.assertEqual([c[1:] for c in runner.calls], [STATUS])
        self.assertIn(Reason.COORDINATOR_BINDING_MISSING, pf.decision.reason_codes)
        self.assertNotIn("--run", pf.command)

    def test_auth_variable_names_shown_values_never(self):
        env = dict(BOUND_ENV, OPENAI_API_KEY="fake-key-value-123")
        adapter = OrcaCliAdapter(ReadOnlyRunner(FakeRunner({})), FAKE_CLI, environ=env)
        pf = preflight.run_preflight(adapter, Provider.CODEX, preflight.choose_task(_args()), offline=True, now=NOW)
        text = "\n".join(preflight.lines(pf))
        self.assertIn("[인증] 대체 인증 환경변수 설정됨: OPENAI_API_KEY (값은 읽지 않음)", text)
        self.assertNotIn("fake-key-value", text)
        self.assertNotIn("term_fake_handle", text)  # the terminal handle is never shown either

    def test_demo_cli_uses_fixture_and_exits_blocked(self):
        code, out = run_main(preflight, ["--demo"])
        self.assertEqual(code, preflight.EXIT_BLOCKED)
        self.assertEqual(out[0], "Worker 전송 전 점검 · 확인만 함 (Run 생성·Worker 기동·작업 전송 없음)")
        self.assertIn('orca orchestration worker-start --spec "<작업 내용 128자>" --task-title "4-B minimal worker" '
                      "--worktree current --agent claude --run run_0123456789ab --json", "\n".join(out))
        self.assertEqual(out[-1], "판정: 전송 차단 · 아무것도 보내지 않았습니다.")

    def test_task_with_unconfirmed_worker_run_is_not_offered(self):
        ws = Workspace(self, plan=chain_plan())
        send_worker(ws, FakeSend(FAILED_START))
        code, out = run_main(preflight, ["--offline", "--tasks", str(ws.plan), "--results", str(ws.results),
                                         "--repo", str(ws.root)])
        self.assertEqual(code, preflight.EXIT_BLOCKED)
        self.assertIn("작업        T-1 · T-1 작업 · 재개 판단 실행 상태 확인 필요", out)
        blocked = [line for line in out if line.startswith("  [작업·복구]")]
        self.assertEqual(blocked, ["  [작업·복구] T-1 실행 상태 확인 필요 · handoff --observe로 상태 조회 · "
                                   "종료 확인 전 다시 보내지 않음"])

    def test_next_task_from_plan_is_named(self):
        ws = Workspace(self, plan=chain_plan())
        code, out = run_main(preflight, ["--offline", "--tasks", str(ws.plan), "--results", str(ws.results),
                                         "--repo", str(ws.root)])
        self.assertEqual(code, preflight.EXIT_BLOCKED)
        self.assertIn("작업        T-1 · T-1 작업 · 재개 판단 이어가기 가능", out)
        self.assertFalse(any(line.startswith("  [작업·복구]") for line in out))


def _args(**kw):
    class A:
        tasks = results = repo = task = None

    a = A()
    for k, v in kw.items():
        setattr(a, k, v)
    return a


# --- Worker runs in the execution record ---------------------------------------------------


class WorkerRecordTest(unittest.TestCase):
    def test_started_worker_records_dispatch_and_stays_open(self):
        ws = Workspace(self, plan=chain_plan())
        send = FakeSend(READY)
        receipt = send_worker(ws, send)
        self.assertIs(receipt.disposition, StartDisposition.STARTED)
        self.assertEqual(send.sent, [worker_start_args(REQUEST)])
        a = last(ws)
        run = a.last_run
        self.assertEqual((a.executor, a.checks, a.state), ("orca-worker:claude", ("worker",), AttemptState.UNKNOWN))
        self.assertEqual((run.run_id, run.end, run.observed_state, run.message),
                         ("dispatch:dsp_test01", None, "running", "시작됨 (ready)"))
        self.assertIn("handoff --observe", a.next_action)
        doc = json.loads(handoff_path(ws.results).read_text(encoding="utf-8"))
        self.assertEqual(doc["attempts"][0]["runs"][0]["observed"]["state"], "running")

    def test_local_verification_records_keep_their_format(self):
        ws = Workspace(self, plan=chain_plan())
        flow(ws, Scripted({}), max_tasks=1)
        doc = json.loads(handoff_path(ws.results).read_text(encoding="utf-8"))
        run = doc["attempts"][0]["runs"][0]
        self.assertEqual(sorted(run), ["check", "end", "ended_at", "exit_code", "message", "outcome", "result_ref",
                                       "run_id", "started_at"])  # no "observed": local runs are never queried

    def test_running_worker_blocks_resend_and_reassignment(self):
        ws = Workspace(self, plan=chain_plan())
        first = FakeSend(READY)
        send_worker(ws, first)
        view = resume(ws)  # default: the last stored observation (running at start)
        self.assertIs(view.task("T-1").state, ResumeState.RUNNING)
        self.assertIn("handoff --observe", view.task("T-1").next_action)
        with self.assertRaises(ClaimRefused) as caught:
            take(ws)
        self.assertEqual(caught.exception.kind, ClaimRefused.UNRESOLVED)
        script = Scripted({})
        report = flow(ws, script)
        self.assertIs(report.stop.kind, StopKind.BUSY)
        self.assertEqual(script.calls, [])
        live = WorkerProbe(FakeShow(obs("working", "live")))
        self.assertIs(resume(ws, live).task("T-1").state, ResumeState.RUNNING)
        self.assertEqual(len(first.sent), 1)

    def test_unknown_or_missing_status_blocks(self):
        for answer in (obs("succeeded", "unverifiable (stale_status)"), obs("weird", None),
                       OutcomeUnknownError("timed out", mutation=False),
                       InvalidResponseError("worker-show reply has no result object")):
            ws = Workspace(self, plan=chain_plan())
            send_worker(ws, FakeSend(READY))
            probe = WorkerProbe(FakeShow(answer))
            self.assertIs(resume(ws, probe).task("T-1").state, ResumeState.CHECK_RUN, answer)
            plan = inspect(ws.plan, ws.results).plan
            with self.assertRaises(ClaimRefused):
                claim_task(ws.plan, ws.results, plan, "T-1", (WORKER_CHECK,), repo=ws.root, probe=probe)

    def test_proven_exit_lets_the_next_session_take_the_task(self):
        ws = Workspace(self, plan=chain_plan())
        send_worker(ws, FakeSend(READY))
        probe = WorkerProbe(FakeShow(obs("succeeded", "exited")))
        self.assertIs(resume(ws, probe).task("T-1").state, ResumeState.RESUMABLE)
        plan = inspect(ws.plan, ws.results).plan
        claim = claim_task(ws.plan, ws.results, plan, "T-1", (WORKER_CHECK,), repo=ws.root, probe=probe)
        claim.close()
        first = [a for a in read_attempts(ws.results)[0] if a.task == "T-1"][0]
        self.assertEqual(first.resolved_note, "실행기 상태 조회로 종료 확인")

    def test_timeout_and_missing_reply_record_unknown_and_block_resend(self):
        for answer, run_id in ((CommandTimeout("timed out after 30s"), None), (EMPTY, None),
                               (FAILED_START, "dispatch:dsp_test02"), (UNOBSERVED, "dispatch:dsp_test03")):
            ws = Workspace(self, plan=chain_plan())
            send = FakeSend(answer)
            send_worker(ws, send)
            a = last(ws)
            self.assertEqual((a.state, a.last_run.end, a.last_run.run_id), (AttemptState.UNKNOWN, RunEnd.UNKNOWN, run_id))
            view = resume(ws)
            self.assertIs(view.task("T-1").state, ResumeState.CHECK_RUN)
            with self.assertRaises(ClaimRefused):
                take(ws)
            self.assertIs(flow(ws, Scripted({})).stop.kind, StopKind.UNRESOLVED)
            self.assertEqual(len(send.sent), 1)
            if run_id is None:
                self.assertTrue(run_line(a, False).startswith("실행 ID 미확인 · 종료 미확인 · worker "))

    def test_refusal_before_dispatch_does_not_block(self):
        for answer in (REFUSED, CommandLaunchError("FileNotFoundError: orca")):
            ws = Workspace(self, plan=chain_plan())
            send_worker(ws, FakeSend(answer))
            a = last(ws)
            self.assertEqual((a.state, a.last_run.end), (AttemptState.ENDED, RunEnd.NOT_STARTED))
            take(ws).close()  # nothing is running, so the task can be taken again

    def test_cut_off_session_after_start_never_sends_twice(self):
        ws = Workspace(self, plan=chain_plan())
        send = FakeSend(READY)
        send_worker(ws, send, cut=True)  # process died right after the Worker started
        a = last(ws)
        self.assertEqual((a.state, a.last_run.run_id), (AttemptState.RUNNING, "dispatch:dsp_test01"))
        for probe in (None, WorkerProbe(FakeShow(obs("working", "live"))),
                      WorkerProbe(FakeShow(obs("failed", "unverifiable")))):
            with self.assertRaises(ClaimRefused):
                plan = inspect(ws.plan, ws.results).plan
                claim_task(ws.plan, ws.results, plan, "T-1", (WORKER_CHECK,), repo=ws.root, probe=probe)
        self.assertEqual(len(send.sent), 1)

    def test_observe_stores_what_orca_reported(self):
        ws = Workspace(self, plan=chain_plan())
        send_worker(ws, FakeSend(READY))
        show = FakeShow(obs("succeeded", "exited"))
        seen = observe_runs(ws.results, WorkerProbe(show), now=lambda: NOW)
        self.assertEqual([(o.task, o.run_id, o.state) for o in seen], [("T-1", "dispatch:dsp_test01", ProbeState.EXITED)])
        self.assertEqual(show.asked, ["dsp_test01"])
        run = last(ws).last_run
        self.assertEqual((run.observed_at, run.observed_state), ("2026-11-27T09:00:00Z", "exited"))
        self.assertIs(resume(ws).task("T-1").state, ResumeState.RESUMABLE)  # judged by results from now on
        self.assertIsNone(last(ws).resolved_at)  # observing never resolves; the next claim does

    def test_handoff_observe_reads_worker_show_only(self):
        ws = Workspace(self, plan=chain_plan())
        send_worker(ws, FakeSend(READY))
        runner = FakeRunner({("orchestration", "worker-show", "--dispatch", "dsp_test01", "--json"):
                             shown("working", "live")})
        adapter = OrcaCliAdapter(ReadOnlyRunner(runner, allow_worker_show=True), FAKE_CLI, environ=UNBOUND_ENV)
        code, out = run_main(handoff_cli, ["--tasks", str(ws.plan), "--results", str(ws.results), "--repo",
                                           str(ws.root), "--observe"], adapter=adapter)
        self.assertEqual(code, handoff_cli.EXIT_BUSY)
        self.assertEqual([c[1:] for c in runner.calls],
                         [("orchestration", "worker-show", "--dispatch", "dsp_test01", "--json")])
        self.assertIn("Worker 조회: T-1 · dispatch:dsp_test01 · 실행 중 · 상태 working · 단계 working · liveness live",
                      out)
        self.assertTrue(any(line.strip().startswith("관측 ") and "실행 중" in line for line in out))


class ScreenTest(unittest.TestCase):
    def test_demo_shows_last_observation_and_next_action(self):
        board = DemoTaskSource("worker").load()
        lines = r.resume_lines(board)
        self.assertEqual(lines[0], "재개        실행 상태 확인 필요 · T-2 · worker 시작됨 (ready) · 종료 미확인")
        self.assertIn("  실행 ID dispatch:dsp_example01 · 종료 미확인", lines)
        self.assertTrue(any(line.startswith("  관측 ") and line.endswith("종료 미확인 · worker-show 조회 실패 "
                                                                         "(outcome_unknown)") for line in lines))
        self.assertEqual(lines[-1], "  다음 행동: handoff --observe로 상태 조회 · 종료 확인 전 다시 보내지 않음")
        self.assertIn("  · T-2: 실행 상태 확인 필요 · worker 시작됨 (ready) · 종료 미확인", r.review_lines(board))

    def test_refresh_never_starts_a_worker(self):
        runner = FakeRunner({STATUS: fixture_result("status_ready"),
                             RUN_CURRENT: fixture_result("run_current_coordinator")})
        adapter = OrcaCliAdapter(ReadOnlyRunner(runner), FAKE_CLI, environ=BOUND_ENV)
        tasks = DemoTaskSource("worker")

        def collect():
            return collect_snapshot(adapter, DispatchGuard(), now=NOW, tasks=tasks)

        keys = iter(["r", "r", "q"])
        out: list[str] = []
        self.assertEqual(app.run_loop(collect, lambda: next(keys), out.append), 0)
        self.assertEqual([c[1:] for c in runner.calls], [STATUS, RUN_CURRENT] * 3)
        with self.assertRaises(ReadOnlyViolation):
            ReadOnlyRunner(runner).run([FAKE_CLI, *worker_start_args(REQUEST)], timeout=1)
        with self.assertRaises(ReadOnlyViolation):  # even the Worker status read needs the explicit flag
            ReadOnlyRunner(runner).run([FAKE_CLI, *worker_show_args("dsp_1")], timeout=1)
        with self.assertRaises(ReadOnlyViolation):
            ReadOnlyRunner(runner, allow_worker_show=True).run([FAKE_CLI, *worker_start_args(REQUEST)], timeout=1)
        self.assertTrue(any("Worker 명령" in chunk for chunk in out))


if __name__ == "__main__":
    unittest.main()
