from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from orca_orchestrator.orca import (
    AuthMode,
    DispatchGuard,
    DispatchPolicy,
    DispatchRequest,
    ExecutionApproval,
    LimitPhase,
    OrcaCliAdapter,
    Outcome,
    PROHIBITED_FALLBACKS,
    Provider,
    ProviderBillingConfirmation,
    Reason,
    UsageLimitTracker,
    WorkDispatcher,
    observe_auth_environment,
)

from support import BOUND_ENV, FAKE_CLI, STATUS, UNBOUND_ENV, FakeRunner, fixture_result

NOW = datetime(2026, 11, 26, 5, 0, tzinfo=timezone.utc)

ALL_CONFIRMED = ProviderBillingConfirmation(
    subscription_auth_verified=True,
    billing_path_verified=True,
    extra_usage_disabled=True,
    auto_recharge_disabled=True,
    api_key_environment_cleared=True,
    evidence_ref="test",
)
FULL_POLICY = DispatchPolicy(
    real_dispatch_enabled=True,
    execution_approval=ExecutionApproval("user", NOW, "4-B"),
    experiment_4a_status="PASSED",
    providers={Provider.CLAUDE: ALL_CONFIRMED, Provider.CODEX: ALL_CONFIRMED},
)


def dispatcher(status_case="status_ready", env=BOUND_ENV, policy=None, limits=None, status_response=None):
    runner = FakeRunner({STATUS: status_response or fixture_result(status_case)})
    adapter = OrcaCliAdapter(runner, FAKE_CLI, environ=env)
    return WorkDispatcher(adapter, DispatchGuard(policy, limits)), runner


class DefaultBlockTest(unittest.TestCase):
    def test_default_policy_blocks_with_reasons(self):
        d, runner = dispatcher()
        decision = d.dispatch(DispatchRequest(Provider.CLAUDE, "task-1"), now=NOW)
        self.assertEqual(decision.outcome, Outcome.BLOCKED)
        for code in (
            Reason.REAL_DISPATCH_DISABLED,
            Reason.EXECUTION_APPROVAL_MISSING,
            Reason.EXPERIMENT_4A_NOT_PASSED,
            Reason.SUBSCRIPTION_AUTH_UNVERIFIED,
            Reason.BILLING_PATH_UNVERIFIED,
            Reason.EXTRA_USAGE_NOT_CONFIRMED_OFF,
            Reason.AUTO_RECHARGE_NOT_CONFIRMED_OFF,
            Reason.WORKER_DISPATCH_CONTRACT_UNVERIFIED,
        ):
            self.assertIn(code, decision.reason_codes)
        self.assertNotIn(Reason.RUNTIME_UNAVAILABLE, decision.reason_codes)
        # Only the read-only status command was invoked.
        self.assertEqual(runner.calls, [(FAKE_CLI, "status", "--json")])

    def test_default_4a_is_conditional(self):
        self.assertEqual(DispatchPolicy().experiment_4a_status, "CONDITIONAL")

    def test_fully_confirmed_policy_still_blocked_by_unverified_contract(self):
        d, runner = dispatcher(policy=FULL_POLICY)
        decision = d.dispatch(DispatchRequest(Provider.CODEX, "task-1"), now=NOW)
        self.assertEqual(decision.outcome, Outcome.BLOCKED)
        self.assertEqual(decision.reason_codes, (Reason.WORKER_DISPATCH_CONTRACT_UNVERIFIED,))
        self.assertEqual(runner.calls, [(FAKE_CLI, "status", "--json")])

    def test_prohibited_fallbacks_are_declared(self):
        d, _ = dispatcher()
        decision = d.dispatch(DispatchRequest(Provider.CLAUDE, "t"), now=NOW)
        self.assertEqual(
            decision.prohibited_fallbacks,
            ("switch_to_api_key", "purchase_or_spend_extra_credits", "automatic_retry"),
        )
        self.assertEqual(PROHIBITED_FALLBACKS, decision.prohibited_fallbacks)


class SafetyReasonTest(unittest.TestCase):
    def test_api_key_auth_mode_forbidden(self):
        d, _ = dispatcher(policy=FULL_POLICY)
        decision = d.dispatch(DispatchRequest(Provider.CODEX, "t", auth_mode=AuthMode.API_KEY), now=NOW)
        self.assertIn(Reason.API_KEY_BILLING_FORBIDDEN, decision.reason_codes)

    def test_api_key_environment_names_only(self):
        env = dict(BOUND_ENV, OPENAI_API_KEY="sk-secret-value")
        policy = DispatchPolicy(providers={Provider.CODEX: ProviderBillingConfirmation()})
        d, _ = dispatcher(env=env, policy=policy)
        decision = d.dispatch(DispatchRequest(Provider.CODEX, "t"), now=NOW)
        reason = next(r for r in decision.reasons if r.code is Reason.API_KEY_ENVIRONMENT_PRESENT)
        self.assertIn("OPENAI_API_KEY", reason.detail)
        self.assertNotIn("sk-secret-value", repr(decision))

    def test_observe_auth_environment_reports_set_unset(self):
        obs = observe_auth_environment(Provider.CODEX, {"OPENAI_API_KEY": "v"})
        self.assertEqual(obs, {"OPENAI_API_KEY": "SET", "CODEX_API_KEY": "UNSET"})

    def test_retry_is_not_sent_automatically(self):
        d, _ = dispatcher(policy=FULL_POLICY)
        decision = d.dispatch(DispatchRequest(Provider.CLAUDE, "t", retry_of="w1"), now=NOW)
        self.assertIn(Reason.AUTOMATIC_RETRY_FORBIDDEN, decision.reason_codes)

    def test_missing_binding(self):
        d, _ = dispatcher(env=UNBOUND_ENV, policy=FULL_POLICY)
        decision = d.dispatch(DispatchRequest(Provider.CLAUDE, "t"), now=NOW)
        self.assertIn(Reason.COORDINATOR_BINDING_MISSING, decision.reason_codes)


class RuntimeFailureTest(unittest.TestCase):
    def test_runtime_not_running(self):
        d, _ = dispatcher(status_case="status_not_running", policy=FULL_POLICY)
        decision = d.dispatch(DispatchRequest(Provider.CLAUDE, "t"), now=NOW)
        self.assertEqual(decision.outcome, Outcome.BLOCKED)
        self.assertIn(Reason.RUNTIME_UNAVAILABLE, decision.reason_codes)

    def test_runtime_unavailable_error(self):
        d, _ = dispatcher(status_response=fixture_result("run_create_runtime_unavailable"), policy=FULL_POLICY)
        decision = d.dispatch(DispatchRequest(Provider.CLAUDE, "t"), now=NOW)
        self.assertIn(Reason.RUNTIME_UNAVAILABLE, decision.reason_codes)

    def test_invalid_status_response(self):
        d, _ = dispatcher(status_response=(0, "not json", ""), policy=FULL_POLICY)
        decision = d.dispatch(DispatchRequest(Provider.CLAUDE, "t"), now=NOW)
        self.assertIn(Reason.RUNTIME_STATUS_INVALID, decision.reason_codes)

    def test_unpinned_version(self):
        body = json.loads(fixture_result("status_ready")[1])
        body["result"]["runtime"]["appVersion"] = "1.5.0"
        d, _ = dispatcher(status_response=(0, json.dumps(body), ""), policy=FULL_POLICY)
        decision = d.dispatch(DispatchRequest(Provider.CLAUDE, "t"), now=NOW)
        self.assertIn(Reason.ORCA_VERSION_UNPINNED, decision.reason_codes)


class UsageLimitTest(unittest.TestCase):
    def test_limit_reached_waits_for_reset(self):
        limits = UsageLimitTracker()
        reset = NOW + timedelta(hours=2)
        limits.record_limit_reached(Provider.CODEX, observed_at=NOW, reset_at=reset, source="test")
        d, runner = dispatcher(policy=FULL_POLICY, limits=limits)
        decision = d.dispatch(DispatchRequest(Provider.CODEX, "t"), now=NOW)
        self.assertEqual(decision.outcome, Outcome.WAITING_FOR_RESET)
        self.assertEqual(decision.resume_after, reset)
        self.assertIn(Reason.USAGE_LIMIT_WAITING_FOR_RESET, decision.reason_codes)
        self.assertNotIn(Reason.API_KEY_BILLING_FORBIDDEN, decision.reason_codes)
        self.assertEqual(len(runner.calls), 1)  # no retry, no other command
        # Other provider is unaffected by this hold.
        other = d.dispatch(DispatchRequest(Provider.CLAUDE, "t"), now=NOW)
        self.assertNotIn(Reason.USAGE_LIMIT_WAITING_FOR_RESET, other.reason_codes)

    def test_unknown_reset_time_still_waits(self):
        limits = UsageLimitTracker()
        limits.record_limit_reached(Provider.CLAUDE, observed_at=NOW, reset_at=None, source="test")
        phase, _ = limits.phase(Provider.CLAUDE, NOW + timedelta(days=30))
        self.assertIs(phase, LimitPhase.WAITING_FOR_RESET)

    def test_reset_passed_requires_fresh_observation(self):
        limits = UsageLimitTracker()
        reset = NOW + timedelta(hours=1)
        limits.record_limit_reached(Provider.CODEX, observed_at=NOW, reset_at=reset, source="test")
        later = reset + timedelta(minutes=1)
        d, _ = dispatcher(policy=FULL_POLICY, limits=limits)
        decision = d.dispatch(DispatchRequest(Provider.CODEX, "t"), now=later)
        self.assertEqual(decision.outcome, Outcome.BLOCKED)
        self.assertIn(Reason.USAGE_RECHECK_REQUIRED, decision.reason_codes)
        # A stale observation (before reset) or one still at the limit does not release.
        self.assertFalse(limits.release_after_recheck(Provider.CODEX, observed_at=NOW, limit_reached=False))
        self.assertFalse(limits.release_after_recheck(Provider.CODEX, observed_at=later, limit_reached=True))
        self.assertTrue(limits.release_after_recheck(Provider.CODEX, observed_at=later, limit_reached=False))
        self.assertIs(limits.phase(Provider.CODEX, later)[0], LimitPhase.AVAILABLE)


if __name__ == "__main__":
    unittest.main()
