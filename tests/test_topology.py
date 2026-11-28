"""``topology@1``: contract, identity and lifecycle rules, read-only discovery, redaction and isolation.

Golden files live in ``tests/fixtures/topology_v1``; each is the list of documents of one synthetic DEMO scene.
After an intended contract change, regenerate them with
``ORCA_UPDATE_GOLDEN=1 python -m unittest discover -s tests -p test_topology.py`` and review the diff.
Nothing here starts Orca or Git: discovery runs against fake runners.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from orca_orchestrator.orca.runner import CommandResult, CommandTimeout
from orca_orchestrator.topology import (
    TOPOLOGY_SCHEMA,
    Activity,
    Connection,
    Intent,
    Lifecycle,
    TopologyTracker,
    decide_intent,
    parse_observation,
    topology_document,
)
from orca_orchestrator.topology import __main__ as topo_cli
from orca_orchestrator.topology import scenarios as sc
from orca_orchestrator.topology.discovery import (
    TOPOLOGY_TAILS,
    GitWorktreeReader,
    TopologyCommandRefused,
    TopologyDiscovery,
    TopologyReadOnlyRunner,
)
from orca_orchestrator.topology.model import (
    IdentityBasis,
    Provider,
    ProviderBasis,
    SessionState,
    SourceError,
    WorkerLink,
)
from orca_orchestrator.topology.parse import RawObservation, Reply

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "topology_v1"
SRC = Path(__file__).resolve().parents[1] / "src" / "orca_orchestrator"

REQUIRED_SCENES = {
    "empty-worktree", "one-codex", "one-claude", "codex-and-claude", "same-provider-three", "four-sessions",
    "eight-sessions", "three-worktrees", "terminals-and-agents", "unknown-provider", "new-session", "session-ended",
    "disconnected", "session-missing", "reconnect-unconfirmed", "duplicate-entity", "worker-finishing",
    "query-failed",
}
TOP_LEVEL = {"schema", "mode", "demo", "observed_at", "sequence", "runtime", "capabilities", "sources", "summary",
             "repositories", "worktrees", "tabs", "terminals", "agent_sessions", "diagnostics", "provenance",
             "not_included"}
ENUMS = {
    "lifecycle": {"active", "missing", "ended", "unknown"},
    "activity": {"working", "blocked", "waiting", "done", "unknown"},
    "provider": {"claude", "codex", "other", "unknown"},
    "connection": {"connected", "disconnected", "unknown"},
    "intent": {i.value for i in Intent},
    "availability": {"available", "partial", "unavailable", "unknown", "not_configured", "not_implemented"},
    "evidence": {"VERIFIED", "PARTIALLY_VERIFIED", "DOCUMENTED_ONLY", "UNVERIFIED", "NOT_AVAILABLE"},
    "room": {"open", "unobservable", "closing"},
}
FORBIDDEN_KEYS = {"handle", "ptyId", "pty_id", "path", "worktreePath", "preview", "title", "lastAssistantMessage",
                  "last_assistant_message", "paneTitle", "remoteUrl", "gitUsername", "argv", "message", "locator"}
SENTINELS = ("secret", "PREVIEW TEXT", "ASSISTANT TEXT", "term_example", "/example", "example-user", "example.invalid",
             "@@", "::")


def scene_docs(name: str) -> list[dict]:
    return topo_cli.demo_documents(name)


def run(steps: list[sc.Step], links=None) -> list[dict]:
    tracker = TopologyTracker("demo", {"scenario": "test"})
    return [topology_document(tracker.observe(parse_observation(sc.raw(step, sc.START + sc.STEP * i)), links))
            for i, step in enumerate(steps)]


def by_id(doc: dict) -> dict[str, dict]:
    return {s["session_id"]: s for s in doc["agent_sessions"]}


def validate(test: unittest.TestCase, doc: dict) -> None:
    """The contract as documented in docs/workspace-topology-v1.md."""

    test.assertEqual(set(doc), TOP_LEVEL)
    test.assertEqual(doc["schema"], TOPOLOGY_SCHEMA)
    test.assertIn(doc["mode"], {"live", "recorded", "demo"})
    test.assertEqual(doc["demo"] is not None, doc["mode"] == "demo")
    test.assertRegex(doc["observed_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
    for cap in doc["capabilities"]:
        test.assertIn(cap["evidence"], ENUMS["evidence"])
        test.assertIn(cap["availability"], ENUMS["availability"])
    for source in doc["sources"].values():
        test.assertIn(source["availability"], ENUMS["availability"])
    worktrees = {w["worktree_id"] for w in doc["worktrees"]}
    panes = {t["pane_key"] for t in doc["terminals"]}
    for w in doc["worktrees"]:
        test.assertIn(w["room"]["state"], ENUMS["room"])
        test.assertIn(w["lifecycle"], {"active", "missing", "removed", "unknown"})
    for t in doc["terminals"]:
        test.assertIn(t["role"], {"agent_session", "terminal_device"})
        test.assertEqual(t["pane_key"], f"{t['tab_id']}:{t['leaf_id']}")
    seen_ids = set()
    for s in doc["agent_sessions"]:
        test.assertNotIn(s["session_id"], seen_ids, "one pet per session")
        seen_ids.add(s["session_id"])
        for key in ("lifecycle", "activity", "provider", "connection"):
            test.assertIn(s[key], ENUMS[key], key)
        test.assertIn(s["presentation"]["intent"], ENUMS["intent"])
        test.assertIn(s["worktree_id"], worktrees)
        test.assertIsNone(s["activity_detail"])
        for place in s["places"]:
            test.assertIn(place["pane_key"], panes)
        test.assertEqual(s["identity"]["confirmed"], s["identity"]["basis"] == "orca_session_id")
        test.assertEqual(s["end_evidence"] is not None, s["lifecycle"] == "ended")
        if s["lifecycle"] != "active":
            test.assertEqual(s["activity"], "unknown")
    for path, key, _ in walk(doc):
        if path.endswith(".not_included") or ".not_included[" in path:
            continue
        test.assertNotIn(key, FORBIDDEN_KEYS, f"{path}.{key}")
    test.assertEqual(json.loads(json.dumps(doc)), doc)


def walk(value, path="$"):
    if isinstance(value, dict):
        for k, v in value.items():
            yield path, k, v
            yield from walk(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield path, i, v
            yield from walk(v, f"{path}[{i}]")
    else:
        assert value is None or isinstance(value, (str, int, float, bool)), (path, type(value))


class GoldenTest(unittest.TestCase):
    def test_every_required_scene_exists(self):
        self.assertEqual(set(sc.SCENES), REQUIRED_SCENES)

    def test_documents_match_golden_files(self):
        update = os.environ.get("ORCA_UPDATE_GOLDEN") == "1"
        GOLDEN.mkdir(parents=True, exist_ok=True)
        for name in sorted(sc.SCENES):
            docs = scene_docs(name)
            text = json.dumps(docs, ensure_ascii=False, indent=2) + "\n"
            path = GOLDEN / f"{name}.json"
            if update:
                path.write_text(text, encoding="utf-8", newline="\n")
            with self.subTest(scene=name):
                self.assertTrue(path.is_file(), f"missing golden {path.name}")
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), docs)
        self.assertEqual({p.stem for p in GOLDEN.glob("*.json")}, set(sc.SCENES))

    def test_every_document_follows_the_contract(self):
        for name in sc.SCENES:
            for i, doc in enumerate(scene_docs(name), 1):
                with self.subTest(scene=name, step=i):
                    validate(self, doc)
                    self.assertEqual(doc["mode"], "demo")
                    self.assertTrue(doc["provenance"]["synthetic"])
                    self.assertFalse(doc["provenance"]["sends_commands"])
                    self.assertIn("합성", doc["demo"]["note"])

    def test_same_input_gives_the_same_document(self):
        for name in ("eight-sessions", "reconnect-unconfirmed"):
            self.assertEqual(scene_docs(name), scene_docs(name))


class SceneMeaningTest(unittest.TestCase):
    def test_empty_worktree_has_a_room_and_no_pets(self):
        doc = scene_docs("empty-worktree")[0]
        self.assertEqual(doc["summary"]["rooms"], 1)
        self.assertEqual(doc["agent_sessions"], [])
        self.assertEqual(doc["worktrees"][0]["room"]["state"], "open")

    def test_same_provider_sessions_stay_separate(self):
        doc = scene_docs("same-provider-three")[0]
        sessions = doc["agent_sessions"]
        self.assertEqual([s["provider"] for s in sessions], ["codex"] * 3)
        self.assertEqual(len({s["session_id"] for s in sessions}), 3)
        self.assertEqual([s["arrival"] for s in sessions], [1, 2, 3])
        self.assertEqual([s["presentation"]["intent"] for s in sessions], ["work", "ambient", "attention"])

    def test_counts_for_two_four_eight_and_three_rooms(self):
        self.assertEqual(scene_docs("codex-and-claude")[0]["summary"]["sessions"], 2)
        self.assertEqual(scene_docs("four-sessions")[0]["summary"]["sessions"], 4)
        eight = scene_docs("eight-sessions")[0]
        self.assertEqual(eight["summary"]["sessions"], 8)
        self.assertEqual(sorted({s["provider"] for s in eight["agent_sessions"]}), ["claude", "codex"])
        rooms = scene_docs("three-worktrees")[0]
        per_room = [sum(1 for s in rooms["agent_sessions"] if s["worktree_id"] == w["worktree_id"])
                    for w in rooms["worktrees"]]
        self.assertEqual(per_room, [0, 2, 5])
        self.assertEqual(len(rooms["repositories"]), 2)

    def test_plain_terminals_are_devices_not_sessions(self):
        doc = scene_docs("terminals-and-agents")[0]
        roles = [(t["agent_identity"], t["role"]) for t in doc["terminals"]]
        self.assertEqual(roles, [(None, "terminal_device"), ("codex", "terminal_device"), ("claude", "agent_session")])
        self.assertEqual(len(doc["agent_sessions"]), 1)

    def test_unknown_provider_is_not_guessed(self):
        sessions = scene_docs("unknown-provider")[0]["agent_sessions"]
        self.assertEqual([(s["provider"], s["provider_basis"], s["agent_type"]) for s in sessions],
                         [("unknown", "none", None), ("other", "orca_agent_type", "gemini")])

    def test_provider_falls_back_to_the_pane_agent_type_only(self):
        p = sc.pane(sc.W1, 1, "claude")
        doc = run([sc.Step([sc.W1], [p], [sc.Agent("example-session-x", None, "working", p)])])[0]
        s = doc["agent_sessions"][0]
        self.assertEqual((s["provider"], s["provider_basis"]), ("claude", "terminal_agent_identity"))
        self.assertTrue(s["identity"]["confirmed"])  # the Orca session ID is the identity, not the type

    def test_new_session_is_marked_new_once(self):
        first, second = scene_docs("new-session")
        self.assertEqual(len(first["agent_sessions"]), 1)
        old, new = second["agent_sessions"]
        self.assertFalse(old["presentation"]["new"])
        self.assertTrue(new["presentation"]["new"])
        self.assertEqual(new["arrival"], 2)
        self.assertEqual(old["session_id"], first["agent_sessions"][0]["session_id"])

    def test_confirmed_end_needs_the_pane_exit(self):
        before, after = scene_docs("session-ended")
        s = after["agent_sessions"][0]
        self.assertEqual((s["lifecycle"], s["end_evidence"], s["presentation"]["intent"]),
                         ("ended", "terminal_exit", "exit"))
        self.assertEqual(after["summary"]["ended"], 1)
        self.assertEqual(after["summary"]["sessions"], 0)

    def test_missing_row_without_exit_is_not_an_end(self):
        _, missing, back = scene_docs("session-missing")
        gone = by_id(missing)["example-session-1-2"]
        self.assertEqual((gone["lifecycle"], gone["presentation"]["intent"]), ("missing", "unobservable"))
        self.assertIsNone(gone["end_evidence"])
        self.assertEqual(gone["last_activity"], "working")
        returned = by_id(back)["example-session-1-2"]
        self.assertEqual((returned["lifecycle"], returned["reappeared"], returned["arrival"]), ("active", True, 2))

    def test_disconnection_keeps_every_seat(self):
        before, after = scene_docs("disconnected")
        self.assertEqual(after["runtime"]["health"], "disconnected")
        self.assertEqual([s["session_id"] for s in after["agent_sessions"]],
                         [s["session_id"] for s in before["agent_sessions"]])
        for s in after["agent_sessions"]:
            self.assertEqual((s["lifecycle"], s["connection"], s["presentation"]["intent"]),
                             ("unknown", "disconnected", "unobservable"))
        self.assertEqual(after["worktrees"][0]["room"]["state"], "unobservable")
        self.assertEqual(after["sources"]["agent_sessions"]["availability"], "unknown")

    def test_reconnection_without_an_id_is_not_merged(self):
        first, _, third = scene_docs("reconnect-unconfirmed")
        named = by_id(third)["example-session-1-1"]
        self.assertEqual((named["reappeared"], named["incarnation_changed"]), (True, True))
        provisional = [s for s in third["agent_sessions"] if not s["identity"]["confirmed"]]
        self.assertEqual(len(provisional), 2)
        old, new = provisional
        self.assertEqual(old["session_id"], [s for s in first["agent_sessions"] if not s["identity"]["confirmed"]][0]
                         ["session_id"])
        self.assertEqual(old["lifecycle"], "missing")
        self.assertEqual(new["possible_predecessor"], old["session_id"])
        self.assertEqual(new["identity"]["basis"], "pane_incarnation")

    def test_one_session_in_two_panes_is_one_pet(self):
        doc = scene_docs("duplicate-entity")[0]
        self.assertEqual(len(doc["agent_sessions"]), 1)
        self.assertEqual(len(doc["agent_sessions"][0]["places"]), 2)
        self.assertEqual([t["session_ids"] for t in doc["terminals"]], [["example-session-1-1"]] * 2)

    def test_the_same_row_twice_is_one_place(self):
        p = sc.pane(sc.W1, 1, "codex")
        row = sc.Agent("example-session-1-1", "codex", "working", p)
        doc = run([sc.Step([sc.W1], [p], [row, row])])[0]
        self.assertEqual(len(doc["agent_sessions"]), 1)
        self.assertEqual([pl["pane_key"] for pl in doc["agent_sessions"][0]["places"]], [p.key])
        self.assertEqual(doc["terminals"][0]["session_ids"], ["example-session-1-1"])

    def test_worker_finishing_link_is_demo_only(self):
        doc = scene_docs("worker-finishing")[0]
        linked, plain = doc["agent_sessions"]
        self.assertEqual(linked["presentation"]["intent"], "finishing")
        self.assertEqual(linked["worker"]["basis"], "demo_synthetic")
        self.assertEqual(plain["presentation"]["intent"], "work")
        cap = {c["feature"]: c for c in doc["capabilities"]}["worker_session_link"]
        self.assertEqual((cap["evidence"], cap["availability"]), ("NOT_AVAILABLE", "available"))
        live_like = scene_docs("one-codex")[0]
        cap = {c["feature"]: c for c in live_like["capabilities"]}["worker_session_link"]
        self.assertEqual(cap["availability"], "not_implemented")

    def test_failed_query_keeps_the_last_observation(self):
        before, after = scene_docs("query-failed")
        self.assertEqual(after["sources"]["agent_sessions"]["error"], {"category": "outcome_unknown", "code": None})
        for old, now in zip(before["agent_sessions"], after["agent_sessions"]):
            self.assertEqual(now["session_id"], old["session_id"])
            self.assertEqual((now["lifecycle"], now["activity"], now["last_activity"]),
                             ("unknown", "unknown", old["activity"]))
            self.assertEqual(now["freshness"]["last_seen_at"], before["observed_at"])
            self.assertFalse(now["freshness"]["observed_now"])
            self.assertEqual(now["presentation"]["intent"], "unobservable")
        self.assertEqual(after["diagnostics"][0]["source"], "agent_sessions")


class RuleTest(unittest.TestCase):
    def test_intent_priority(self):
        base = SessionState("s", IdentityBasis.ORCA_SESSION_ID, "terminal", "w", Provider.CODEX,
                            ProviderBasis.ORCA_AGENT_TYPE, "codex", sc.START, sc.START, 1,
                            lifecycle=Lifecycle.ACTIVE, activity=Activity.WORKING, connection=Connection.CONNECTED)
        finishing = WorkerLink("d", "finishing", "demo_synthetic")
        cases = [
            (dict(lifecycle=Lifecycle.ENDED, activity=Activity.UNKNOWN), Intent.EXIT),
            (dict(activity=Activity.BLOCKED, connection=Connection.DISCONNECTED), Intent.ATTENTION),
            (dict(activity=Activity.WAITING, worker=finishing), Intent.ATTENTION),
            (dict(lifecycle=Lifecycle.MISSING, activity=Activity.UNKNOWN, worker=finishing), Intent.UNOBSERVABLE),
            (dict(connection=Connection.DISCONNECTED), Intent.UNOBSERVABLE),
            (dict(activity=Activity.UNKNOWN), Intent.UNOBSERVABLE),
            (dict(worker=finishing), Intent.FINISHING),
            (dict(), Intent.WORK),
            (dict(connection=Connection.UNKNOWN), Intent.WORK),
            (dict(activity=Activity.DONE, previous_activity=Activity.WORKING), Intent.WORK_DONE),
            (dict(activity=Activity.DONE, previous_activity=Activity.UNKNOWN), Intent.AMBIENT),
        ]
        for change, expected in cases:
            with self.subTest(change=change):
                self.assertIs(decide_intent(replace(base, **change)), expected)

    def test_work_done_is_shown_for_one_observation(self):
        p = sc.pane(sc.W1, 1, "claude")
        docs = run([sc.Step([sc.W1], [p], [sc.Agent("s1", "claude", state, p)]) for state in ("working", "done", "done")])
        self.assertEqual([d["agent_sessions"][0]["presentation"]["intent"] for d in docs], ["work", "work_done", "ambient"])

    def test_worktree_identity_survives_a_folder_move(self):
        p = sc.pane(sc.W1, 1, "codex")
        step = sc.Step([sc.W1], [p], [sc.Agent("s1", "codex", "working", p)])
        tracker = TopologyTracker("demo", {"scenario": "test"})
        a = topology_document(tracker.observe(parse_observation(sc.raw(step, sc.START))))
        # The folder moves: Orca's locator (<repoId>::<path>) changes everywhere, identity.key does not.
        text = json.dumps({k: v.result for k, v in sc.raw(step, sc.START + sc.STEP).replies.items()})
        moved = json.loads(text.replace("/example/repo-1", "/example/moved-repo-1"))
        second = RawObservation(sc.START + sc.STEP, {k: Reply(v) for k, v in moved.items()})
        b = topology_document(tracker.observe(parse_observation(second)))
        self.assertEqual(len(b["worktrees"]), 1)
        self.assertEqual(a["worktrees"][0]["worktree_id"], b["worktrees"][0]["worktree_id"])
        self.assertEqual(b["agent_sessions"][0]["worktree_id"], sc.W1.key)
        self.assertEqual(b["agent_sessions"][0]["arrival"], 1)
        self.assertFalse(b["agent_sessions"][0]["presentation"]["new"])

    def test_worktree_without_identity_uses_a_locator_hash(self):
        obs = sc.raw(sc.Step([sc.W1]), sc.START)
        del obs.replies["worktrees"].result["worktrees"][0]["identity"]  # type: ignore[index]
        doc = topology_document(TopologyTracker("demo", {}).observe(parse_observation(obs)))
        w = doc["worktrees"][0]
        self.assertEqual(w["identity_basis"], "locator_hash")
        self.assertRegex(w["worktree_id"], r"^loc:[0-9a-f]{16}$")

    def test_partial_listing_never_makes_sessions_missing(self):
        panes, agents = sc.sessions(sc.W1, [("codex", "working"), ("claude", "working")])
        truncated = sc.Step([sc.W1], panes, agents[:1])
        obs2 = sc.raw(truncated, sc.START + sc.STEP)
        obs2.replies["agents"].result["truncated"] = True  # type: ignore[index]
        tracker = TopologyTracker("demo", {})
        tracker.observe(parse_observation(sc.raw(sc.Step([sc.W1], panes, agents), sc.START)))
        doc = topology_document(tracker.observe(parse_observation(obs2)))
        self.assertEqual(doc["sources"]["agent_sessions"]["availability"], "partial")
        self.assertEqual(by_id(doc)["example-session-1-2"]["lifecycle"], "unknown")
        self.assertEqual(by_id(doc)["example-session-1-1"]["lifecycle"], "active")

    def test_omitted_hosts_make_a_listing_partial(self):
        obs = sc.raw(sc.Step([sc.W1]), sc.START)
        obs.replies["agents"].result["hostScope"]["omittedHostIds"] = ["example-host"]  # type: ignore[index]
        view = parse_observation(obs).agents
        self.assertEqual((view.availability.value, view.omitted_hosts), ("partial", 1))

    def test_unreadable_reply_and_bad_rows(self):
        obs = sc.raw(sc.Step([sc.W1]), sc.START)
        obs.replies["agents"] = Reply({"agents": "nope"})  # type: ignore[index]
        self.assertEqual(parse_observation(obs).agents.error, SourceError("invalid_response", "shape:agents"))
        p = sc.pane(sc.W1, 1, "codex")
        obs = sc.raw(sc.Step([sc.W1], [p], [sc.Agent("s1", "codex", "working", p)]), sc.START)
        obs.replies["agents"].result["agents"].append({"orcaSessionId": 7, "kind": "terminal"})  # type: ignore[index]
        obs.replies["agents"].result["agents"].append({"orcaSessionId": "s2", "kind": "robot", "state": "working",
                                                       "worktreeId": "x", "paneKey": "a:b"})  # type: ignore[index]
        view = parse_observation(obs).agents
        self.assertEqual((view.availability.value, view.invalid_items, len(view.items)), ("partial", 2, 1))

    def test_unknown_state_value_is_unknown_activity(self):
        p = sc.pane(sc.W1, 1, "codex")
        doc = run([sc.Step([sc.W1], [p], [sc.Agent("s1", "codex", "thinking", p)])])[0]
        s = doc["agent_sessions"][0]
        self.assertEqual((s["activity"], s["presentation"]["intent"]), ("unknown", "unobservable"))

    def test_disconnected_pane_is_unobservable_but_still_active(self):
        p = sc.pane(sc.W1, 1, "codex", connected=False)
        s = run([sc.Step([sc.W1], [p], [sc.Agent("s1", "codex", "working", p)])])[0]["agent_sessions"][0]
        self.assertEqual((s["lifecycle"], s["connection"], s["presentation"]["intent"]),
                         ("active", "disconnected", "unobservable"))

    def test_exit_for_another_incarnation_is_not_an_end(self):
        p = sc.pane(sc.W1, 1, "claude", inc="example-inc-old")
        replaced = sc.pane(sc.W1, 1, "claude", inc="example-inc-new", exit_cause="exited")
        docs = run([sc.Step([sc.W1], [p], [sc.Agent("s1", "claude", "working", p)]), sc.Step([sc.W1], [replaced], [])])
        self.assertEqual(docs[1]["agent_sessions"][0]["lifecycle"], "missing")

    def test_removed_worktree_needs_git_and_ends_its_sessions(self):
        p = sc.pane(sc.W2, 1, "codex")
        live = sc.Step([sc.W1, sc.W2], [p], [sc.Agent("s1", "codex", "working", p)])
        hidden = sc.Step([sc.W1], [], [], git_worktrees=[sc.W1, sc.W2])   # Git still has it
        removed = sc.Step([sc.W1], [], [], git_worktrees=[sc.W1])          # Git no longer has it
        docs = run([live, hidden, removed])
        self.assertEqual(docs[1]["worktrees"][1]["lifecycle"], "missing")
        self.assertEqual(docs[1]["agent_sessions"][0]["lifecycle"], "missing")
        self.assertEqual((docs[2]["worktrees"][1]["lifecycle"], docs[2]["worktrees"][1]["room"]["state"]),
                         ("removed", "closing"))
        s = docs[2]["agent_sessions"][0]
        self.assertEqual((s["lifecycle"], s["end_evidence"], s["presentation"]["intent"]),
                         ("ended", "worktree_removed", "exit"))
        prunable = run([live, sc.Step([sc.W1], [], [], git_worktrees=[sc.W1, sc.W2],
                                      git_prunable=frozenset({sc.W2.key}))])[1]
        self.assertEqual(prunable["worktrees"][1]["lifecycle"], "removed")

    def test_ended_sessions_are_dropped_after_retention(self):
        p = sc.pane(sc.W1, 1, "claude")
        gone = sc.pane(sc.W1, 1, "claude", exit_cause="exited")
        tracker = TopologyTracker("demo", {}, ended_retention=timedelta(seconds=45))
        steps = [sc.Step([sc.W1], [p], [sc.Agent("s1", "claude", "working", p)])] + [sc.Step([sc.W1], [gone], [])] * 3
        counts = [len(tracker.observe(parse_observation(sc.raw(s, sc.START + sc.STEP * i))).sessions)
                  for i, s in enumerate(steps)]
        self.assertEqual(counts, [1, 1, 1, 0])

    def test_arrival_order_is_stable_when_a_session_leaves(self):
        panes, agents = sc.sessions(sc.W1, [("codex", "working"), ("claude", "working"), ("codex", "done")])
        docs = run([sc.Step([sc.W1], panes, agents), sc.Step([sc.W1], panes, [agents[0], agents[2]])])
        self.assertEqual([(s["session_id"], s["arrival"]) for s in docs[1]["agent_sessions"]],
                         [("example-session-1-1", 1), ("example-session-1-2", 2), ("example-session-1-3", 3)])


class RedactionTest(unittest.TestCase):
    def test_no_private_value_in_any_scene(self):
        for name in sc.SCENES:
            text = json.dumps(scene_docs(name), ensure_ascii=False)
            for sentinel in SENTINELS:
                with self.subTest(scene=name, sentinel=sentinel):
                    self.assertNotIn(sentinel, text)

    def test_golden_files_hold_no_host_paths(self):
        for path in GOLDEN.glob("*.json"):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"[A-Za-z]:[\\/]|/Users/|/home/", text), path.name)


class FakeRunner:
    """Answers topology commands with demo replies; records every argv."""

    def __init__(self, raw: RawObservation, fail: dict[str, Exception] | None = None) -> None:
        self.raw = raw
        self.fail = fail or {}
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout, cwd=None):
        argv = tuple(argv)
        self.calls.append(argv)
        if argv[0] == "git":
            return CommandResult(argv, 0, sc.porcelain([sc.W1]), "")
        name = {v: k for k, v in TOPOLOGY_TAILS.items()}[argv[1:]]
        if name in self.fail:
            raise self.fail[name]
        reply = self.raw.reply(name)
        return CommandResult(argv, 0, json.dumps({"ok": True, "result": reply.result}), "")


class DiscoveryTest(unittest.TestCase):
    def discovery(self, raw: RawObservation, **kw):
        runner = FakeRunner(raw, kw.pop("fail", None))
        disc = TopologyDiscovery(runner, cli_path="orca-test", git=GitWorktreeReader(runner), environ={},
                                 clock=lambda: sc.START, **kw)
        return disc, runner

    def test_only_listed_read_commands_run(self):
        p = sc.pane(sc.W1, 1, "codex")
        disc, runner = self.discovery(sc.raw(sc.Step([sc.W1], [p], [sc.Agent("s1", "codex", "working", p)]), sc.START))
        doc = topology_document(TopologyTracker("live").observe(parse_observation(disc.collect())))
        tails = [c[1:] for c in runner.calls if c[0] == "orca-test"]
        self.assertEqual(tails, [TOPOLOGY_TAILS[n] for n in ("status", "repos", "worktrees", "terminals", "agents")])
        self.assertEqual([c for c in runner.calls if c[0] == "git"],
                         [("git", "--no-optional-locks", "-C", "/example/example-app", "worktree", "list",
                           "--porcelain")])
        self.assertEqual(doc["mode"], "live")
        self.assertFalse(doc["provenance"]["synthetic"])
        self.assertEqual(doc["summary"]["sessions"], 1)
        validate(self, doc)

    def test_live_clock_is_whole_seconds(self):
        disc = TopologyDiscovery(FakeRunner(sc.raw(sc.Step([sc.W1]), sc.START)), cli_path="orca-test", git=False,
                                 environ={})
        self.assertEqual(disc._clock().microsecond, 0)

    def test_disconnected_runtime_asks_nothing_else(self):
        disc, runner = self.discovery(sc.raw(sc.Step([sc.W1], connected=False), sc.START))
        observation = parse_observation(disc.collect())
        self.assertEqual(runner.calls, [("orca-test", "status", "--json")])
        self.assertEqual(observation.runtime.health, "disconnected")
        self.assertEqual(observation.agents.availability.value, "unknown")

    def test_one_failed_source_does_not_stop_the_others(self):
        p = sc.pane(sc.W1, 1, "codex")
        disc, runner = self.discovery(sc.raw(sc.Step([sc.W1], [p], [sc.Agent("s1", "codex", "working", p)]), sc.START),
                                      fail={"terminals": CommandTimeout("slow")})
        observation = parse_observation(disc.collect())
        self.assertEqual(observation.terminals.error, SourceError("outcome_unknown", None))
        self.assertEqual(observation.agents.availability.value, "available")
        self.assertEqual(len(runner.calls), 6)

    def test_other_commands_are_refused_before_running(self):
        class Never:
            def run(self, *a, **k):
                raise AssertionError("ran")

        guard = TopologyReadOnlyRunner(Never())
        for tail in (("terminal", "send", "--terminal", "x", "--text", "hi"), ("terminal", "create", "--json"),
                     ("terminal", "list", "--json"), ("agent", "read", "x", "--json"),
                     ("orchestration", "worker-start", "--json"), ("worktree", "rm", "--worktree", "x"),
                     ("terminal", "read", "--json"), ("worktree", "activate", "--json")):
            with self.subTest(tail=tail), self.assertRaises(TopologyCommandRefused):
                guard.run(("orca", *tail), timeout=1)

    def test_allowlist_is_read_only_words(self):
        words = {w for tail in TOPOLOGY_TAILS.values() for w in tail}
        self.assertEqual(words, {"status", "--json", "repo", "list", "worktree", "terminal",
                                 "--include-visual-layouts", "agent"})

    def test_package_never_names_a_sending_command(self):
        text = "\n".join(p.read_text(encoding="utf-8") for p in (SRC / "topology").glob("*.py"))
        # ("split" is a pane-layout node type in the document, not a command, so it is not listed here.)
        for word in ('"send"', "'send'", '"create"', '"worker-start"', '"stop"', '"close"', '"rename"',
                     '"activate"', '"read"', '"wait"', "terminal-send", "subprocess"):
            self.assertNotIn(word, text, word)


class IsolationTest(unittest.TestCase):
    def test_status_screen_and_snapshot_do_not_import_topology(self):
        code = ("import sys; import orca_orchestrator.__main__, orca_orchestrator.snapshot_document; "
                "print(any(m.startswith('orca_orchestrator.topology') for m in sys.modules))")
        env = dict(os.environ, PYTHONPATH=str(SRC.parent))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True)
        self.assertEqual(out.stdout.strip(), "False")

    def test_demo_never_builds_discovery(self):
        import orca_orchestrator.topology.discovery as discovery

        original = discovery.TopologyDiscovery
        discovery.TopologyDiscovery = None  # type: ignore[assignment,misc]
        try:
            self.assertEqual(len(scene_docs("disconnected")), 2)
        finally:
            discovery.TopologyDiscovery = original

    def test_recorded_replay_matches_and_is_marked(self):
        import tempfile

        raw = sc.raw(sc.Step([sc.W1]), sc.START)
        data = {"observations": [{"at": raw.at.isoformat(), "replies": {
            k: topo_cli._reply_to_json(v) for k, v in raw.replies.items()}}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            doc = topo_cli.recorded_documents(path)[0]
        self.assertEqual(doc["mode"], "recorded")
        self.assertFalse(doc["provenance"]["synthetic"])
        self.assertEqual(doc["summary"]["rooms"], 1)
        validate(self, doc)

    def test_text_summary_names_demo(self):
        lines = topo_cli.text_lines(scene_docs("query-failed")[1])
        self.assertTrue(lines[0].startswith("DEMO · "))
        self.assertIn("조회 제한: agent_sessions unavailable (outcome_unknown)", lines)


if __name__ == "__main__":
    unittest.main()
