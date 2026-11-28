"""The ``snapshot@1`` document: JSON types, meaning, provenance, data minimisation and golden fixtures.

Golden files live in ``tests/fixtures/snapshot_v1``; each is the document of one bundled demo scene taken at a
fixed time. After an intended contract change, regenerate them with
``ORCA_UPDATE_GOLDEN=1 .venv/bin/python -m unittest discover -s tests -p test_snapshot_document.py``
and review the diff. Nothing here calls Orca, runs a process or touches the network.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import subprocess
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from unittest import mock

from orca_orchestrator import __main__ as cli
from orca_orchestrator.orca.adapter import OrcaCliAdapter
from orca_orchestrator.orca.dispatch import DispatchGuard
from orca_orchestrator.orca.errors import CliUnavailableError
from orca_orchestrator.snapshot_document import SNAPSHOT_SCHEMA, snapshot_document
from orca_orchestrator.tasks import DemoTaskSource
from orca_orchestrator.tui import snapshot as s
from orca_orchestrator.tui.demo import FixtureRunner, demo_adapter, load_fixture
from orca_orchestrator.tui.readonly import ReadOnlyRunner
from orca_orchestrator.tui.render import render
from orca_orchestrator.tui.snapshot import collect_snapshot

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "snapshot_v1"
TAKEN_AT = datetime(2026, 11, 28, 6, 0, tzinfo=timezone.utc)

# name -> (Orca demo scenario, demo results variant; None: no task list). The GUI states each one covers are in
# docs/snapshot-v1.md.
SCENES: dict[str, tuple[str, str | None]] = {
    "connected-no-tasks": ("connected", None),
    "connected-none": ("connected", "none"),
    "connected-passed": ("connected", "passed"),
    "connected-failed": ("connected", "failed"),
    "connected-protected": ("connected", "protected"),
    "connected-approved": ("connected", "approved"),
    "connected-changed": ("connected", "changed"),
    "connected-interrupted": ("connected", "interrupted"),
    "connected-worker": ("connected", "worker"),
    "disconnected-passed": ("disconnected", "passed"),
    "unbound-passed": ("unbound", "passed"),
    "invalid-passed": ("invalid", "passed"),
}

TOP_LEVEL = {"schema", "mode", "demo", "taken_at", "runtime", "binding", "run", "dispatch", "tasks", "flow",
             "review", "recovery", "workers", "mascots", "presentation", "requests", "not_included"}
SECTIONS = ("runtime", "binding", "run", "dispatch", "tasks", "flow", "review", "recovery", "presentation")
SOURCES = {"live", "recorded", "demo", "none"}
AVAILABILITY = {"available", "not_configured", "unavailable", "unknown", "not_implemented"}
FORBIDDEN_KEYS = {"owner_pid", "evidence", "command", "argv", "environ", "orca_message", "auth_env_present",
                  "check_digests", "resolved_note", "plan", "detail"}


def demo_snapshot(scenario: str, variant: str | None) -> s.Snapshot:
    adapter, _ = demo_adapter(scenario)
    tasks = DemoTaskSource(variant) if variant is not None else None
    return collect_snapshot(adapter, DispatchGuard(), demo=scenario, now=TAKEN_AT, tasks=tasks)


def walk(value, path="$"):
    """Yields (path, key, value) for every node."""

    if isinstance(value, dict):
        for k, v in value.items():
            yield path, k, v
            yield from walk(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield path, i, v
            yield from walk(v, f"{path}[{i}]")


def bundled_fixture_text() -> str:
    folder = resources.files("orca_orchestrator").joinpath("fixtures")
    return "\n".join(f.read_text(encoding="utf-8") for f in folder.iterdir() if f.name.endswith(".json"))


class ContractTest(unittest.TestCase):
    def test_json_types_only_and_schema(self):
        for name, (scenario, variant) in SCENES.items():
            doc = snapshot_document(demo_snapshot(scenario, variant))
            self.assertEqual(doc["schema"], SNAPSHOT_SCHEMA)
            self.assertEqual(SNAPSHOT_SCHEMA, "orca-orchestrator/snapshot@1")
            self.assertEqual(set(doc), TOP_LEVEL, name)
            for path, key, value in walk(doc):
                if isinstance(key, str):
                    self.assertIsInstance(key, str)
                self.assertTrue(value is None or isinstance(value, (dict, list, str, int, bool)),
                                f"{name} {path}.{key}: {type(value).__name__}")
            self.assertEqual(json.loads(json.dumps(doc, ensure_ascii=False)), doc)

    def test_deterministic_and_input_unchanged(self):
        snap = demo_snapshot("connected", "worker")
        before = repr(snap)
        first = json.dumps(snapshot_document(snap), ensure_ascii=False)
        second = json.dumps(snapshot_document(snap), ensure_ascii=False)
        self.assertEqual(first, second)
        self.assertEqual(repr(snap), before)
        again = json.dumps(snapshot_document(demo_snapshot("connected", "worker")), ensure_ascii=False)
        self.assertEqual(first, again)

    def test_no_io(self):
        snap = demo_snapshot("connected", "changed")
        boom = AssertionError("I/O during serialisation")
        with mock.patch("subprocess.run", side_effect=boom), mock.patch("subprocess.Popen", side_effect=boom), \
                mock.patch("socket.socket", side_effect=boom), mock.patch("builtins.open", side_effect=boom), \
                mock.patch("pathlib.Path.read_text", side_effect=boom), \
                mock.patch("pathlib.Path.write_text", side_effect=boom), \
                mock.patch("os.replace", side_effect=boom):
            doc = snapshot_document(snap)
        self.assertEqual(doc["schema"], SNAPSHOT_SCHEMA)
        self.assertIs(subprocess.run, subprocess.run)  # patches undone
        self.assertTrue(callable(socket.socket))

    def test_every_section_states_source_judgment_and_availability(self):
        for name, (scenario, variant) in SCENES.items():
            doc = snapshot_document(demo_snapshot(scenario, variant))
            for key in SECTIONS:
                section = doc[key]
                self.assertIn(section["source"], SOURCES, (name, key))
                self.assertIn(section["judgment"], ("observed", "inferred"), (name, key))
                self.assertIn(section["availability"], AVAILABILITY, (name, key))
            for part in ("live", "recorded"):
                self.assertIn(doc["workers"][part]["availability"], AVAILABILITY)


class MeaningTest(unittest.TestCase):
    def test_demo_is_marked_and_every_fact_comes_from_the_fixtures(self):
        doc = snapshot_document(demo_snapshot("connected", "passed"))
        self.assertEqual(doc["mode"], "demo")
        self.assertEqual(doc["demo"]["scenario"], "connected")
        self.assertIn("모의", doc["demo"]["note"])
        for key in SECTIONS:
            self.assertEqual(doc[key]["source"], "demo", key)
        self.assertEqual(doc["workers"]["recorded"]["source"], "demo")

    def live_like(self, environ, tasks=None) -> s.Snapshot:
        cases = load_fixture()["cases"]

        def result(case):
            body = case["json_response"]
            return case["exit_code"], json.dumps(body) if body is not None else "", case["stderr"]

        runner = FixtureRunner({("status", "--json"): result(cases["status_ready"]),
                                ("orchestration", "run-current", "--json"): result(cases["run_current_coordinator"])})
        adapter = OrcaCliAdapter(ReadOnlyRunner(runner), "/Users/someone/private/bin/orca", environ=environ)
        return collect_snapshot(adapter, DispatchGuard(), demo=None, now=TAKEN_AT, tasks=tasks)

    def test_live_mode_and_not_configured_tasks(self):
        doc = snapshot_document(self.live_like({}))
        self.assertEqual(doc["mode"], "live")
        self.assertIsNone(doc["demo"])
        self.assertEqual(doc["runtime"]["source"], "live")
        self.assertEqual(doc["tasks"]["source"], "none")
        self.assertEqual(doc["tasks"]["availability"], "not_configured")
        self.assertEqual(doc["tasks"]["tasks"], [])  # empty because nothing is configured, not "no tasks"
        self.assertEqual(doc["run"]["availability"], "unknown")  # unbound: never asked
        self.assertEqual(doc["presentation"]["mood"], "idle")

    def test_secrets_environment_and_local_paths_are_not_exposed(self):
        environ = {"ORCA_TERMINAL_HANDLE": "term_secret_handle", "OPENAI_API_KEY": "sk-openai-secret",
                   "ANTHROPIC_API_KEY": "sk-ant-secret", "HOME": "/Users/someone"}
        snap = self.live_like(environ, DemoTaskSource("worker"))
        text = json.dumps(snapshot_document(snap), ensure_ascii=False)
        for leaked in ("sk-openai-secret", "sk-ant-secret", "term_secret_handle", "OPENAI_API_KEY",
                       "ANTHROPIC_API_KEY", "/Users/someone"):
            self.assertNotIn(leaked, text)
        codes = {r["code"] for p in snapshot_document(snap)["dispatch"]["providers"] for r in p["reasons"]}
        self.assertIn("api_key_environment_present", codes)  # the fact is kept, the names are not

    def test_unavailable_runtime_keeps_category_not_message(self):
        snap = demo_snapshot("connected", "passed")
        error = CliUnavailableError("Orca CLI not found at /Users/someone/Applications/orca")
        snap = replace(snap, runtime=s.RuntimeView(s.CLI_MISSING, error=error), run=s.RunView(s.RUN_SKIPPED_RUNTIME))
        doc = snapshot_document(snap)
        self.assertEqual(doc["runtime"]["health"], "cli_missing")
        self.assertEqual(doc["runtime"]["availability"], "unavailable")
        self.assertIsNone(doc["runtime"]["status"])
        self.assertEqual(doc["runtime"]["error"], {"category": "cli_unavailable", "code": None})
        self.assertNotIn("/Users/someone", json.dumps(doc, ensure_ascii=False))
        self.assertEqual(doc["run"]["availability"], "unknown")

    def test_unknown_and_unavailable_run(self):
        self.assertEqual(snapshot_document(demo_snapshot("unbound", "passed"))["run"]["availability"], "unknown")
        invalid = snapshot_document(demo_snapshot("invalid", "passed"))["run"]
        self.assertEqual((invalid["state"], invalid["availability"]), ("unreadable", "unavailable"))
        self.assertIsNone(invalid["run_id"])

    def test_stale_results_and_approval_stay_stale(self):
        doc = snapshot_document(demo_snapshot("connected", "changed"))
        checks = [c for t in doc["tasks"]["tasks"] for c in t["checks"]]
        stale = [c for c in checks if c["state"] == "stale"]
        self.assertTrue(stale)
        for c in stale:
            self.assertIsNotNone(c["stale_reason"])
            self.assertIsNotNone(c["result"])  # recorded, but no longer counts
        self.assertEqual(doc["review"]["state"], "reapprove")
        self.assertNotIn(doc["presentation"]["mood"], ("passed", "review", "approved"))

    def test_failure_and_unconfirmed_end_are_not_success(self):
        failed = snapshot_document(demo_snapshot("connected", "failed"))
        self.assertEqual(failed["presentation"]["mood"], "failed")
        self.assertIn("check_failed", [r["kind"] for r in failed["requests"]])
        for variant in ("interrupted", "worker"):
            doc = snapshot_document(demo_snapshot("connected", variant))
            self.assertEqual(doc["presentation"]["mood"], "end_unknown")
            self.assertEqual(doc["requests"][0]["kind"], "end_unknown")
            # the run's end must be confirmed: the record never says it ended and nothing is resumable
            held = [t for t in doc["recovery"]["tasks"] if t["task_id"] == doc["requests"][0]["task_id"]]
            self.assertEqual([t["state"] for t in held], ["check_run"], variant)
            self.assertNotEqual(held[0]["attempt"]["state"], "ended", variant)
            self.assertFalse(held[0]["attempt"]["resolved"], variant)

    def test_workers_and_mascots_are_separate(self):
        for name, (scenario, variant) in SCENES.items():
            doc = snapshot_document(demo_snapshot(scenario, variant))
            live = doc["workers"]["live"]
            self.assertEqual((live["availability"], live["items"]), ("not_implemented", []))
            self.assertEqual([m["id"] for m in doc["mascots"]], ["codex", "claude"])
            for m in doc["mascots"]:
                self.assertIsNone(m["worker"], name)
                self.assertFalse(m["worker_actions_allowed"], name)
                self.assertEqual(m["role"], "companion")
            self.assertNotIn(doc["presentation"]["mood"], ("working", "other_session"), name)
            self.assertEqual(doc["presentation"]["local_run"], {"availability": "unknown", "value": None})
        recorded = snapshot_document(demo_snapshot("connected", "worker"))["workers"]["recorded"]["items"]
        self.assertEqual([(w["task_id"], w["agent"]) for w in recorded], [("T-2", "claude")])

    def test_worker_lifecycle_uses_existing_recorded_fields(self):
        baseline = snapshot_document(demo_snapshot("connected", "worker"))
        for variant, observed, resume_state in (
            ("worker-finishing", "finishing", "finishing"),
            ("worker-exited", "exited", "blocked"),
        ):
            with self.subTest(variant=variant):
                doc = snapshot_document(demo_snapshot("connected", variant))
                worker, = doc["workers"]["recorded"]["items"]
                task = next(task for task in doc["recovery"]["tasks"] if task["task_id"] == "T-2")
                run = task["attempt"]["runs"][-1]
                self.assertEqual(doc["schema"], SNAPSHOT_SCHEMA)
                self.assertEqual(worker.keys(), baseline["workers"]["recorded"]["items"][0].keys())
                self.assertEqual(run.keys(), baseline["recovery"]["tasks"][1]["attempt"]["runs"][0].keys())
                self.assertEqual((worker["observed_state"], run["observed_state"]), (observed, observed))
                self.assertEqual(task["state"], resume_state)
                self.assertIsNone(run["outcome"])
                self.assertFalse(doc["review"]["approvable"])
                self.assertFalse(doc["dispatch"]["sends_work"])
                self.assertEqual(doc["workers"]["live"], baseline["workers"]["live"])
                self.assertTrue(all(not mascot["worker_actions_allowed"] for mascot in doc["mascots"]))
                self.assertEqual(doc["presentation"]["mood"], "end_unknown" if observed == "finishing" else "ready")

    def test_no_invented_ids(self):
        bundled = bundled_fixture_text()
        for name, (scenario, variant) in SCENES.items():
            doc = snapshot_document(demo_snapshot(scenario, variant))
            for path, key, value in walk(doc):
                if key in ("run_id", "dispatch_id", "attempt_id", "session") and value is not None:
                    self.assertIn(value, bundled, f"{name} {path}.{key}")

    def test_allowlist_only(self):
        for name, (scenario, variant) in SCENES.items():
            doc = snapshot_document(demo_snapshot(scenario, variant))
            keys = {k for _, k, _ in walk(doc) if isinstance(k, str)}
            self.assertFalse(keys & FORBIDDEN_KEYS, (name, keys & FORBIDDEN_KEYS))


class GoldenTest(unittest.TestCase):
    def test_golden_fixtures_match(self):
        update = os.environ.get("ORCA_UPDATE_GOLDEN") == "1"
        if update:
            GOLDEN.mkdir(parents=True, exist_ok=True)
        for name, (scenario, variant) in SCENES.items():
            doc = snapshot_document(demo_snapshot(scenario, variant))
            path = GOLDEN / f"{name}.json"
            text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
            if update:
                path.write_text(text, encoding="utf-8")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), doc, name)
        self.assertEqual(sorted(p.stem for p in GOLDEN.glob("*.json")), sorted(SCENES))

    def test_golden_files_hold_no_local_or_secret_values(self):
        for path in GOLDEN.glob("*.json"):
            text = path.read_text(encoding="utf-8")
            for leaked in ("/Users/", "C:\\\\", "API_KEY", "sk-", "ORCA_TERMINAL_HANDLE", "demo-terminal"):
                self.assertNotIn(leaked, text, path.name)
            doc = json.loads(text)
            self.assertEqual(doc["mode"], "demo", path.name)
            self.assertEqual(doc["taken_at"], "2026-11-28T06:00:00Z", path.name)


class CliTest(unittest.TestCase):
    def call(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("subprocess.run", side_effect=AssertionError("process started")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("process started")):
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_demo_once_json_prints_one_document(self):
        code, out, _ = self.call("--demo", "--demo-results", "worker", "--once", "--json")
        self.assertEqual(code, 0)
        doc = json.loads(out)  # the whole of stdout is one JSON document
        self.assertEqual((doc["schema"], doc["mode"]), (SNAPSHOT_SCHEMA, "demo"))
        self.assertEqual(doc["presentation"]["mood"], "end_unknown")

    def test_json_needs_once_and_excludes_screens(self):
        for argv in (("--demo", "--json"), ("--demo", "--once", "--json", "--plain"),
                     ("--demo", "--once", "--json", "--console")):
            code, out, err = self.call(*argv)
            self.assertEqual(code, 2, argv)
            self.assertEqual(out, "")
            self.assertIn("--json", err)

    def test_text_once_is_unchanged(self):
        code, out, _ = self.call("--demo", "--once")
        self.assertEqual(code, 0)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(out)
        adapter, _ = demo_adapter("connected")
        expected = render(collect_snapshot(adapter, DispatchGuard(), demo="connected", tasks=DemoTaskSource("passed")))
        self.assertEqual(out.splitlines()[0], expected[0])
        self.assertEqual(out.splitlines()[3:], expected[3:])  # line 2 is the query time


if __name__ == "__main__":
    unittest.main()
