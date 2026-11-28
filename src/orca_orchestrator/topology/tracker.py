"""Keeps identities and lifecycles across observations and decides each session's scene intent.

Rules (``docs/workspace-topology-v1.md`` § identity and lifecycle):

* A session's identity is Orca's ``orcaSessionId``. Rows with the same ID are one
  session with several places (an agent resumed in two panes): one pet, not two.
* A row without an ID gets a provisional ID from its worktree, pane and the pane's
  process incarnation. A new incarnation in the same pane is a new provisional
  session; the old one is not merged into it (``possible_predecessor`` only).
* The same ``orcaSessionId`` seen again after a gap is the same session
  (``reappeared``), also with a new incarnation (``incarnation_changed``).
* Several sessions of one provider stay separate; provider never merges sessions.
* A terminal pane without an agent row is a terminal device, never a session,
  even when ``agentIdentity`` names an agent type.
* Absence from a complete listing makes a session ``missing`` (stale, seat kept).
  Absence from a failed or partial listing makes it ``unknown``. Neither is an end.
* ``ended`` needs explicit evidence: the session's pane reports ``exitCause`` for
  the incarnation the session was seen in, or its worktree is confirmed removed
  (Orca stopped listing it and Git no longer registers it or marks it prunable).
* A disconnected runtime or a failed query keeps everything as last seen and
  marks it unknown. Nothing is deleted because it could not be observed.

The tracker only reads parsed observations; it runs nothing.
"""

from __future__ import annotations

import copy
import hashlib
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Mapping

from .model import (
    ORCA_STATES,
    Activity,
    AgentRowObs,
    Availability,
    Connection,
    EndEvidence,
    IdentityBasis,
    Intent,
    KNOWN_PROVIDERS,
    Lifecycle,
    Observation,
    Place,
    Provider,
    ProviderBasis,
    RepoState,
    SessionState,
    SourceView,
    TabState,
    TerminalObs,
    TerminalState,
    Topology,
    WorkerLink,
    WorktreeLifecycle,
    WorktreeState,
)
from .parse import locator_hash

ENDED_RETENTION = timedelta(minutes=10)
_ATTENTION = (Activity.BLOCKED, Activity.WAITING)


def normalize_path(path: str) -> str:
    """Compares Orca and Git paths: slashes unified, trailing slash dropped, drive paths case-folded."""

    p = path.replace("\\", "/").rstrip("/")
    return p.casefold() if len(p) >= 2 and p[1] == ":" else p


def provisional_id(worktree_id: str, pane_key: str, incarnation: str | None) -> str:
    digest = hashlib.sha256(f"{worktree_id}|{pane_key}|{incarnation or '-'}".encode("utf-8")).hexdigest()
    return "prov:" + digest[:16]


def decide_intent(st: SessionState) -> Intent:
    """First matching rule wins; the order is the behaviour priority of the Studio spec."""

    if st.lifecycle is Lifecycle.ENDED:
        return Intent.EXIT
    if st.lifecycle is Lifecycle.ACTIVE and st.activity in _ATTENTION:
        return Intent.ATTENTION
    if (st.lifecycle is not Lifecycle.ACTIVE or st.connection is Connection.DISCONNECTED
            or st.activity is Activity.UNKNOWN):
        return Intent.UNOBSERVABLE
    if st.worker is not None and st.worker.phase == "finishing":
        return Intent.FINISHING
    if st.activity is Activity.WORKING:
        return Intent.WORK
    if st.previous_activity is Activity.WORKING:
        return Intent.WORK_DONE
    return Intent.AMBIENT


class TopologyTracker:
    def __init__(self, mode: str = "live", demo: dict | None = None,
                 ended_retention: timedelta = ENDED_RETENTION) -> None:
        if mode not in ("live", "recorded", "demo"):
            raise ValueError(mode)
        self.mode, self.demo = mode, demo
        self.ended_retention = ended_retention
        self.sequence = 0
        self.repos: dict[str, RepoState] = {}
        self.worktrees: dict[str, WorktreeState] = {}
        self.locators: dict[str, str] = {}
        self.terminals: dict[str, TerminalState] = {}
        self.tabs: dict[tuple[str, str], TabState] = {}
        self.sessions: dict[str, SessionState] = {}
        self._session_arrival: dict[str, int] = defaultdict(int)
        self._worktree_arrival = 0

    # --- public ---------------------------------------------------------------------------

    def observe(self, obs: Observation, worker_links: Mapping[str, WorkerLink] | None = None) -> Topology:
        self.sequence += 1
        at = obs.at
        diagnostics: list[dict] = []
        self._repos(obs, at)
        self._worktrees(obs, at)
        current_terms = self._terminals(obs, at)
        self._tabs(obs)
        self._sessions(obs, at, current_terms, worker_links or {})
        self._expire(at)
        for name, view in self._source_views(obs).items():
            if view.availability not in (Availability.AVAILABLE, Availability.NOT_CONFIGURED):
                diagnostics.append({
                    "source": name, "availability": view.availability.value,
                    "category": view.error.category if view.error else None,
                    "code": view.error.code if view.error else None,
                    "invalid_items": view.invalid_items, "truncated": view.truncated,
                    "omitted_hosts": view.omitted_hosts,
                })
        unknown_refs = sorted({t.worktree_id for t in self.terminals.values() if t.worktree_id not in self.worktrees}
                              | {s.worktree_id for s in self.sessions.values() if s.worktree_id not in self.worktrees})
        if unknown_refs:
            diagnostics.append({"source": "relations", "availability": "partial", "category": "unknown_worktree_ref",
                                "code": None, "invalid_items": len(unknown_refs), "truncated": False,
                                "omitted_hosts": 0})
        return self._topology(obs, diagnostics, worker_links)

    # --- sources --------------------------------------------------------------------------

    @staticmethod
    def _source_views(obs: Observation) -> dict[str, SourceView]:
        return {"repositories": obs.repos, "worktrees": obs.worktrees, "terminals": obs.terminals,
                "tabs": obs.tabs, "agent_sessions": obs.agents, "git": obs.git}

    def _repos(self, obs: Observation, at: datetime) -> None:
        view = obs.repos
        seen: set[str] = set()
        if view.usable:
            for r in view.items:
                seen.add(r.repo_id)
                st = self.repos.get(r.repo_id)
                if st is None:
                    self.repos[r.repo_id] = RepoState(r.repo_id, r.display_name, r.kind, at, at, True, r.path)
                else:
                    st.display_name, st.kind, st.path, st.last_seen_at, st.listed = r.display_name, r.kind, r.path, at, True
        for rid, st in self.repos.items():
            if rid not in seen:
                st.listed = False if view.complete else None

    def _worktrees(self, obs: Observation, at: datetime) -> None:
        view = obs.worktrees
        seen: set[str] = set()
        if view.usable:
            for w in view.items:
                seen.add(w.key)
                self.locators[w.locator] = w.key
                st = self.worktrees.get(w.key)
                if st is None:
                    self._worktree_arrival += 1
                    st = WorktreeState(w.key, w.identity_basis, w.repo_id, w.display_name, w.branch, w.is_main,
                                       w.archived, w.host, at, at, self._worktree_arrival)
                    self.worktrees[w.key] = st
                st.identity_basis, st.repo_id, st.display_name, st.branch = (
                    w.identity_basis, w.repo_id, w.display_name, w.branch)
                st.is_main, st.archived, st.host = w.is_main, w.archived, w.host
                st.locator, st.path, st.last_seen_at = w.locator, w.path, at
                st.lifecycle, st.missing_since = WorktreeLifecycle.ACTIVE, None
        for key, st in self.worktrees.items():
            if key in seen or st.lifecycle is WorktreeLifecycle.REMOVED:
                continue
            if view.complete:
                st.lifecycle = WorktreeLifecycle.MISSING
                st.missing_since = st.missing_since or at
            else:
                st.lifecycle = WorktreeLifecycle.UNKNOWN
        self._git(obs, at)

    def _git(self, obs: Observation, at: datetime) -> None:
        view = obs.git
        if not view.usable:
            return
        answered = {g.repo_id for g in view.items}
        by_path = {normalize_path(g.path): g for g in view.items}
        for st in self.worktrees.values():
            if not st.path or st.repo_id not in answered:
                continue
            g = by_path.get(normalize_path(st.path))
            st.git_listed = g is not None and not g.prunable
            st.git_prunable = g.prunable if g is not None else None
            st.git_branch_matches = (g.branch == st.branch) if g is not None and g.branch and st.branch else None
            if st.lifecycle is WorktreeLifecycle.MISSING and st.git_listed is False:
                st.lifecycle = WorktreeLifecycle.REMOVED
                st.missing_since = st.missing_since or at

    def _wkey(self, locator: str) -> str:
        return self.locators.get(locator) or locator_hash(locator)

    def _terminals(self, obs: Observation, at: datetime) -> dict[str, TerminalObs]:
        view = obs.terminals
        current: dict[str, TerminalObs] = {}
        if view.usable:
            for t in view.items:
                current[t.pane_key] = t
                st = self.terminals.get(t.pane_key)
                changed = bool(st and st.incarnation_id and t.incarnation_id and st.incarnation_id != t.incarnation_id)
                if st is None:
                    st = TerminalState(t.pane_key, t.tab_id, t.leaf_id, self._wkey(t.locator), t.connected,
                                       t.orphaned, t.incarnation_id, t.agent_identity, t.exit_cause, t.liveness, at, at)
                    self.terminals[t.pane_key] = st
                st.worktree_id = self._wkey(t.locator)
                st.connected, st.orphaned, st.agent_identity = t.connected, t.orphaned, t.agent_identity
                st.exit_cause, st.liveness, st.last_seen_at, st.listed = t.exit_cause, t.liveness, at, True
                st.incarnation_changed = changed
                st.incarnation_id = t.incarnation_id
        for key, st in self.terminals.items():
            if key not in current:
                st.listed = False if view.complete else None
                st.incarnation_changed = False
        return current

    def _tabs(self, obs: Observation) -> None:
        view = obs.tabs
        seen: set[tuple[str, str]] = set()
        if view.usable:
            for tab in view.items:
                wid = self._wkey(tab.locator)
                key = (wid, tab.tab_id)
                seen.add(key)
                self.tabs[key] = TabState(tab.tab_id, wid, tab.group_id, tab.active, tab.panes, True)
        for key, st in self.tabs.items():
            if key not in seen:
                st.listed = False if view.complete else None

    # --- sessions -------------------------------------------------------------------------

    def _session_key(self, row: AgentRowObs, wid: str, terms: dict[str, TerminalObs]) -> tuple[str, IdentityBasis]:
        if row.session_id:
            return row.session_id, IdentityBasis.ORCA_SESSION_ID
        term = terms.get(row.pane_key)
        inc = term.incarnation_id if term is not None else None
        basis = IdentityBasis.PANE_INCARNATION if inc else IdentityBasis.PANE_ONLY
        return provisional_id(wid, row.pane_key, inc), basis

    def _provider(self, rows: list[AgentRowObs], places: list[Place]) -> tuple[Provider, ProviderBasis, str | None]:
        types = [r.agent_type for r in rows if r.agent_type and r.agent_type != "unknown"]
        if types:
            name = types[0].lower()
            provider = KNOWN_PROVIDERS.get(name, Provider.OTHER)
            safe = name if len(name) <= 32 and name.replace("-", "").replace("_", "").replace(".", "").isalnum() else None
            return provider, ProviderBasis.ORCA_AGENT_TYPE, safe
        for place in places:
            term = self.terminals.get(place.pane_key)
            hint = (term.agent_identity or "").lower() if term and term.listed else ""
            if hint in KNOWN_PROVIDERS:
                return KNOWN_PROVIDERS[hint], ProviderBasis.TERMINAL_AGENT_IDENTITY, hint
        return Provider.UNKNOWN, ProviderBasis.NONE, None

    def _sessions(self, obs: Observation, at: datetime, terms: dict[str, TerminalObs],
                  links: Mapping[str, WorkerLink]) -> None:
        view = obs.agents
        groups: dict[str, list[AgentRowObs]] = defaultdict(list)
        bases: dict[str, IdentityBasis] = {}
        if view.usable:
            for row in view.items:
                wid = self._wkey(row.locator)
                key, basis = self._session_key(row, wid, terms)
                groups[key].append(row)
                bases[key] = basis
        for key, rows in groups.items():
            self._seen(key, bases[key], rows, at, terms, groups)
        for key, st in self.sessions.items():
            if key in groups or st.lifecycle is Lifecycle.ENDED:
                continue
            st.previous_activity, st.activity = st.activity, Activity.UNKNOWN
            evidence = self._end_evidence(st)
            if evidence is not None:
                st.lifecycle, st.end_evidence, st.ended_at = Lifecycle.ENDED, evidence, at
            elif view.complete:
                st.lifecycle = Lifecycle.MISSING
                st.missing_since = st.missing_since or at
            else:
                st.lifecycle = Lifecycle.UNKNOWN
            st.reappeared = st.incarnation_changed = False
        for st in self.sessions.values():
            st.connection = self._connection(st, obs)
            st.worker = links.get(st.session_id)
            st.intent = decide_intent(st)

    def _seen(self, key: str, basis: IdentityBasis, rows: list[AgentRowObs], at: datetime,
              terms: dict[str, TerminalObs], groups: Mapping[str, list[AgentRowObs]]) -> None:
        rows = sorted(rows, key=lambda r: -(r.updated_at or 0))
        primary = rows[0]
        wid = self._wkey(primary.locator)
        places: list[Place] = []
        for r in rows:
            if r.kind != "terminal" or any(p.pane_key == r.pane_key for p in places):
                continue
            term = terms.get(r.pane_key)
            tab = term.tab_id if term is not None else (r.pane_key.split(":", 1)[0] or None)
            places.append(Place(r.pane_key, tab, term.incarnation_id if term is not None else None))
        provider, provider_basis, agent_type = self._provider(rows, places)
        activity = ORCA_STATES.get(primary.state, Activity.UNKNOWN)
        st = self.sessions.get(key)
        if st is None:
            self._session_arrival[wid] += 1
            st = SessionState(key, basis, primary.kind, wid, provider, provider_basis, agent_type, at, at,
                              self._session_arrival[wid])
            if basis is not IdentityBasis.ORCA_SESSION_ID:
                st.possible_predecessor = self._predecessor(wid, places, groups)
            self.sessions[key] = st
            st.previous_activity = Activity.UNKNOWN
        else:
            st.reappeared = st.lifecycle in (Lifecycle.MISSING, Lifecycle.UNKNOWN)
            old = {p.pane_key: p.incarnation_id for p in st.places}
            st.incarnation_changed = any(p.incarnation_id and old.get(p.pane_key)
                                         and old[p.pane_key] != p.incarnation_id for p in places)
            st.previous_activity = st.activity
        st.identity_basis, st.kind, st.worktree_id = basis, primary.kind, wid
        st.provider, st.provider_basis, st.agent_type = provider, provider_basis, agent_type
        st.places = places
        st.activity = st.last_activity = activity
        st.activity_updated_at = primary.updated_at
        st.lifecycle, st.missing_since, st.last_seen_at = Lifecycle.ACTIVE, None, at

    def _predecessor(self, wid: str, places: list[Place], groups: Mapping[str, list[AgentRowObs]]) -> str | None:
        panes = {p.pane_key for p in places}
        for other in self.sessions.values():
            if (other.worktree_id == wid and other.session_id not in groups and other.lifecycle is not Lifecycle.ENDED
                    and any(p.pane_key in panes for p in other.places)):
                return other.session_id
        return None

    def _end_evidence(self, st: SessionState) -> EndEvidence | None:
        wt = self.worktrees.get(st.worktree_id)
        if wt is not None and wt.lifecycle is WorktreeLifecycle.REMOVED:
            return EndEvidence.WORKTREE_REMOVED
        if not st.places:
            return None
        for place in st.places:
            term = self.terminals.get(place.pane_key)
            if term is None or term.listed is not True or term.exit_cause is None:
                return None
            if place.incarnation_id and term.incarnation_id and place.incarnation_id != term.incarnation_id:
                return None
        return EndEvidence.TERMINAL_EXIT

    def _connection(self, st: SessionState, obs: Observation) -> Connection:
        if obs.runtime.health == "disconnected":
            return Connection.DISCONNECTED
        if not obs.runtime.connected or st.lifecycle is not Lifecycle.ACTIVE:
            return Connection.UNKNOWN
        if st.kind == "chat":
            return Connection.CONNECTED
        listed = [self.terminals[p.pane_key] for p in st.places
                  if p.pane_key in self.terminals and self.terminals[p.pane_key].listed is True]
        if any(t.connected for t in listed):
            return Connection.CONNECTED
        if listed and len(listed) == len(st.places) and all(t.connected is False for t in listed):
            return Connection.DISCONNECTED
        return Connection.UNKNOWN

    def _expire(self, at: datetime) -> None:
        for key in [k for k, s in self.sessions.items()
                    if s.lifecycle is Lifecycle.ENDED and s.ended_at and at - s.ended_at > self.ended_retention]:
            del self.sessions[key]
        for key in [k for k, w in self.worktrees.items()
                    if w.lifecycle is WorktreeLifecycle.REMOVED and w.missing_since
                    and at - w.missing_since > self.ended_retention
                    and not any(s.worktree_id == k for s in self.sessions.values())]:
            del self.worktrees[key]

    # --- output ---------------------------------------------------------------------------

    def _topology(self, obs: Observation, diagnostics: list[dict],
                  worker_links: Mapping[str, WorkerLink] | None) -> Topology:
        order = {k: w.arrival for k, w in self.worktrees.items()}
        far = len(order) + 1
        sessions = sorted(self.sessions.values(), key=lambda s: (order.get(s.worktree_id, far), s.worktree_id, s.arrival))
        terminals = sorted(self.terminals.values(), key=lambda t: (order.get(t.worktree_id, far), t.worktree_id,
                                                                   t.first_seen_at, t.pane_key))
        tabs = sorted(self.tabs.values(), key=lambda t: (order.get(t.worktree_id, far), t.worktree_id, t.tab_id))
        for t in terminals:
            t.session_ids = tuple(s.session_id for s in sessions
                                  if s.lifecycle is not Lifecycle.ENDED and any(p.pane_key == t.pane_key for p in s.places))
        snap = copy.deepcopy
        asked = [name for name, view in self._source_views(obs).items()
                 if view.availability not in (Availability.UNKNOWN, Availability.NOT_CONFIGURED)]
        return Topology(
            mode=self.mode, demo=snap(self.demo), observed_at=obs.at, sequence=self.sequence, runtime=obs.runtime,
            sources=self._source_views(obs),
            repositories=tuple(snap(r) for r in sorted(self.repos.values(), key=lambda r: (r.first_seen_at, r.repo_id))),
            worktrees=tuple(snap(w) for w in sorted(self.worktrees.values(), key=lambda w: w.arrival)),
            tabs=tuple(snap(t) for t in tabs),
            terminals=tuple(snap(t) for t in terminals),
            sessions=tuple(snap(s) for s in sessions),
            worker_links=Availability.AVAILABLE if worker_links is not None else Availability.NOT_IMPLEMENTED,
            diagnostics=tuple(diagnostics),
            provenance={"answered": asked},
        )
