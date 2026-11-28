"""``orca-orchestrator/topology@1``: one tracked :class:`~.model.Topology` as a JSON document.

Like ``snapshot@1`` this is a pure projection: no file, process, network or
clock access; the same topology gives the same document; JSON types only;
every field is chosen here by name.

Never exported: terminal handles (an input capability), PTY IDs, absolute
paths, Orca worktree locators (``<repoId>::<path>``; worktrees are identified
by Orca's path-free ``identity.key``, or a hash of the locator on hosts without
it), tab/pane/agent titles, terminal previews, conversation text, Git remotes,
owners and user names, and error messages or argv. ``not_included`` lists them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..orca.adapter import PINNED_ORCA_VERSIONS
from .model import (
    TOPOLOGY_SCHEMA,
    Availability,
    IdentityBasis,
    Intent,
    Lifecycle,
    PaneNode,
    SessionState,
    Topology,
    WorktreeLifecycle,
)

# feature, source that answers it in this build (None: nothing does), evidence grade (audit), basis
CAPABILITIES: tuple[tuple[str, str | None, str, str], ...] = (
    ("repositories", "repositories", "VERIFIED", "repo list --json"),
    ("worktrees", "worktrees", "VERIFIED", "worktree list --json; identity.key leaves out the path"),
    ("worktree_git_crosscheck", "git", "VERIFIED", "git worktree list --porcelain: path and branch"),
    ("tabs_and_panes", "tabs", "VERIFIED", "terminal list --include-visual-layouts"),
    ("terminal_connection", "terminals", "VERIFIED", "terminals[].connected"),
    ("agent_sessions_terminal", "agent_sessions", "VERIFIED", "agent list: kind terminal, paneKey = tabId:leafId"),
    ("agent_sessions_chat", "agent_sessions", "DOCUMENTED_ONLY", "kind chat in the 1.4.264 contract; none observed"),
    ("provider", "agent_sessions", "PARTIALLY_VERIFIED", "agent claude and codex observed; other types documented"),
    ("work_activity", "agent_sessions", "PARTIALLY_VERIFIED",
     "working and done observed; blocked and waiting documented"),
    ("activity_detail", None, "NOT_AVAILABLE", "no thinking, coding or building field"),
    ("session_end", "terminals", "DOCUMENTED_ONLY", "terminals[].exitCause documented, never observed"),
    ("identity_across_restart", None, "UNVERIFIED", "no restart or reconnection observed"),
    ("events", None, "NOT_AVAILABLE", "the CLI offers no event stream; this build polls once per call"),
    ("worker_session_link", None, "NOT_AVAILABLE", "no Orca field ties a Worker Dispatch to a pane"),
)

NOT_INCLUDED = (
    {"field": "titles", "why": "탭·패널·에이전트 제목은 자유 텍스트(경로·명령 포함 가능)이고 식별 근거가 아님"},
    {"field": "preview", "why": "터미널 출력 미리보기는 내보내지 않음"},
    {"field": "handle", "why": "터미널 핸들은 입력 전송에 쓰이는 값이라 내보내지 않음"},
    {"field": "pty_id", "why": "PTY ID에 경로가 들어 있음"},
    {"field": "paths", "why": "절대 경로와 경로가 들어 있는 Orca worktree locator는 연결에만 쓰고 내보내지 않음"},
    {"field": "last_assistant_message", "why": "대화 내용은 Topology의 범위가 아님"},
    {"field": "remote_identity", "why": "Git 원격 주소·소유자·사용자 이름·프로젝트 제공자 식별은 내보내지 않음"},
    {"field": "activity_detail", "why": "Thinking·Coding·Building을 구분하는 Orca 필드가 없음"},
    {"field": "station_and_pose", "why": "자리 배정·좌표·프레임·카메라는 화면 쪽 표현 상태 (Room Composer)"},
)

ROOM_STATE = {WorktreeLifecycle.ACTIVE: "open", WorktreeLifecycle.MISSING: "unobservable",
              WorktreeLifecycle.UNKNOWN: "unobservable", WorktreeLifecycle.REMOVED: "closing"}


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _v(member) -> Any:
    return None if member is None else member.value


def _error(error) -> dict | None:
    return None if error is None else {"category": error.category, "code": error.code}


def _pane(node: PaneNode | None) -> dict | None:
    if node is None:
        return None
    if node.pane_key is not None:
        return {"type": "pane", "pane_key": node.pane_key, "active": node.active}
    return {"type": "split", "direction": node.direction, "first": _pane(node.first), "second": _pane(node.second)}


def _session(s: SessionState, observed_at: datetime) -> dict[str, Any]:
    return {
        "session_id": s.session_id,
        "identity": {"basis": s.identity_basis.value,
                     "confirmed": s.identity_basis is IdentityBasis.ORCA_SESSION_ID},
        "kind": s.kind,
        "worktree_id": s.worktree_id,
        "provider": s.provider.value,
        "provider_basis": s.provider_basis.value,
        "agent_type": s.agent_type,
        "places": [{"pane_key": p.pane_key, "tab_id": p.tab_id, "incarnation_id": p.incarnation_id}
                   for p in s.places],
        "lifecycle": s.lifecycle.value,
        "missing_since": _iso(s.missing_since),
        "ended_at": _iso(s.ended_at),
        "end_evidence": _v(s.end_evidence),
        "activity": s.activity.value,
        "last_activity": s.last_activity.value,
        "activity_basis": "orca_agent_status",
        "activity_detail": None,
        "connection": s.connection.value,
        "freshness": {"first_seen_at": _iso(s.first_seen_at), "last_seen_at": _iso(s.last_seen_at),
                      "observed_now": s.lifecycle is Lifecycle.ACTIVE and s.last_seen_at == observed_at},
        "arrival": s.arrival,
        "reappeared": s.reappeared,
        "incarnation_changed": s.incarnation_changed,
        "possible_predecessor": s.possible_predecessor,
        "worker": None if s.worker is None else {"dispatch_ref": s.worker.dispatch_ref, "phase": s.worker.phase,
                                                 "basis": s.worker.basis},
        "presentation": {"intent": s.intent.value,
                         "new": s.first_seen_at == observed_at and s.lifecycle is Lifecycle.ACTIVE},
    }


def _summary(t: Topology) -> dict[str, int]:
    live = [s for s in t.sessions if s.lifecycle is not Lifecycle.ENDED]
    count = lambda *intents: sum(1 for s in live if s.intent in intents)
    return {
        "rooms": sum(1 for w in t.worktrees if w.lifecycle is not WorktreeLifecycle.REMOVED),
        "sessions": len(live),
        "working": count(Intent.WORK, Intent.FINISHING),
        "attention": count(Intent.ATTENTION),
        "unobservable": count(Intent.UNOBSERVABLE),
        "ended": sum(1 for s in t.sessions if s.lifecycle is Lifecycle.ENDED),
    }


def _provenance(t: Topology) -> dict[str, Any]:
    base = {"generator": "orca_orchestrator.topology", "source": t.mode, "answered": list(t.provenance["answered"]),
            "contract_basis": "Orca CLI 1.4.264 replies and v1.4.264 contract sources",
            "synthetic": t.mode == "demo", "sends_commands": False}
    if t.mode == "demo":
        base["note"] = ("합성 DEMO 장면. 응답 형태는 Orca 1.4.264를 따르지만 값과 전이는 만든 것이며 "
                        "실제 세션 관측의 증거가 아님")
    return base


def topology_document(t: Topology) -> dict[str, Any]:
    rt = t.runtime
    capabilities = []
    for feature, source, grade, basis in CAPABILITIES:
        if feature == "worker_session_link":
            availability = t.worker_links.value
        elif source is None:
            availability = Availability.NOT_IMPLEMENTED.value
        else:
            availability = t.sources[source].availability.value
        capabilities.append({"feature": feature, "evidence": grade, "availability": availability, "basis": basis})
    return {
        "schema": TOPOLOGY_SCHEMA,
        "mode": t.mode,
        "demo": t.demo,
        "observed_at": _iso(t.observed_at),
        "sequence": t.sequence,
        "runtime": {"health": rt.health, "connected": rt.connected, "app_version": rt.app_version,
                    "version_verified": rt.app_version in PINNED_ORCA_VERSIONS, "error": _error(rt.error)},
        "capabilities": capabilities,
        "sources": {name: {"availability": v.availability.value, "error": _error(v.error),
                           "truncated": v.truncated, "omitted_hosts": v.omitted_hosts,
                           "invalid_items": v.invalid_items}
                    for name, v in t.sources.items()},
        "summary": _summary(t),
        "repositories": [{"repo_id": r.repo_id, "display_name": r.display_name, "kind": r.kind, "listed": r.listed,
                          "first_seen_at": _iso(r.first_seen_at), "last_seen_at": _iso(r.last_seen_at)}
                         for r in t.repositories],
        "worktrees": [{"worktree_id": w.worktree_id, "identity_basis": w.identity_basis, "repo_id": w.repo_id,
                       "display_name": w.display_name, "branch": w.branch, "is_main": w.is_main,
                       "archived": w.archived, "host": w.host, "lifecycle": w.lifecycle.value,
                       "missing_since": _iso(w.missing_since), "arrival": w.arrival,
                       "first_seen_at": _iso(w.first_seen_at), "last_seen_at": _iso(w.last_seen_at),
                       "git": {"listed": w.git_listed, "prunable": w.git_prunable,
                               "branch_matches": w.git_branch_matches},
                       "room": {"state": ROOM_STATE[w.lifecycle]}}
                      for w in t.worktrees],
        "tabs": [{"tab_id": tab.tab_id, "worktree_id": tab.worktree_id, "group_id": tab.group_id,
                  "active": tab.active, "listed": tab.listed, "panes": _pane(tab.panes)}
                 for tab in t.tabs],
        "terminals": [{"pane_key": term.pane_key, "tab_id": term.tab_id, "leaf_id": term.leaf_id,
                       "worktree_id": term.worktree_id, "listed": term.listed, "connected": term.connected,
                       "orphaned": term.orphaned, "incarnation_id": term.incarnation_id,
                       "incarnation_changed": term.incarnation_changed, "agent_identity": term.agent_identity,
                       "exit_reported": term.exit_cause is not None, "exit_cause": term.exit_cause,
                       "liveness": term.liveness, "role": "agent_session" if term.session_ids else "terminal_device",
                       "session_ids": list(term.session_ids),
                       "first_seen_at": _iso(term.first_seen_at), "last_seen_at": _iso(term.last_seen_at)}
                      for term in t.terminals],
        "agent_sessions": [_session(s, t.observed_at) for s in t.sessions],
        "diagnostics": [dict(d) for d in t.diagnostics],
        "provenance": _provenance(t),
        "not_included": [dict(item) for item in NOT_INCLUDED],
    }
