"""Turns raw read-only replies into one :class:`~.model.Observation`.

Live discovery, recorded captures and the synthetic demo scenes all go through
this module, so the rules below are the same for each:

* a reply whose shape is not the one observed on Orca 1.4.264 makes that source
  ``unavailable`` (``invalid_response``); nothing is guessed from it,
* an item with a missing or mistyped required field is skipped and counted; the
  source is then ``partial``,
* ``truncated: true`` or hosts named under ``omittedHostIds`` also make the
  source ``partial``: an absent item is then not evidence that it is gone,
* titles, previews, handles, PTY IDs, paths and messages are never kept except
  the paths needed to join Orca and Git, which stay private in the records.

Field shapes: Orca 1.4.264 ``repo list``, ``worktree list``,
``terminal list --include-visual-layouts`` and ``agent list`` replies (read on a
Windows host on 2026-11-29) and the v1.4.264 contract sources named in
``docs/workspace-topology-audit.md``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from .model import (
    AgentRowObs,
    Availability,
    GitWorktreeObs,
    Observation,
    PaneNode,
    RepoObs,
    RuntimeView,
    SourceError,
    SourceView,
    TabObs,
    TerminalObs,
    WorktreeObs,
)

SOURCES = ("status", "repos", "worktrees", "terminals", "agents", "git")


class Shape(Exception):
    """The reply or item does not have the observed shape."""


@dataclass(frozen=True)
class Reply:
    """One read-only answer: the ``result`` object, or why there is none.

    ``asked=False`` means the command was not issued in this collection.
    For Git, ``result`` maps repo ID to ``{"porcelain": text}`` or ``{"error": {...}}``.
    """

    result: Mapping[str, Any] | None = None
    error: SourceError | None = None
    asked: bool = True


NOT_ASKED = Reply(asked=False)


@dataclass(frozen=True)
class RawObservation:
    at: datetime
    replies: Mapping[str, Reply]

    def reply(self, name: str) -> Reply:
        return self.replies.get(name, NOT_ASKED)


# --- small validators ---------------------------------------------------------------------


def _req(obj: Mapping[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    value = obj.get(key)
    if not isinstance(value, kind) or isinstance(value, bool) and kind is not bool:
        raise Shape(key)
    if isinstance(value, str) and not value:
        raise Shape(key)
    return value


def _opt(obj: Mapping[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, kind) or isinstance(value, bool) and kind not in (bool, (bool,)):
        raise Shape(key)
    return value


def _list(result: Mapping[str, Any], key: str) -> list:
    value = result.get(key)
    if not isinstance(value, list):
        raise Shape(key)
    return value


def _scope(result: Mapping[str, Any]) -> tuple[bool, int]:
    truncated = result.get("truncated")
    if truncated is not None and not isinstance(truncated, bool):
        raise Shape("truncated")
    omitted = 0
    scope = result.get("hostScope")
    if scope is not None:
        if not isinstance(scope, Mapping):
            raise Shape("hostScope")
        hosts = scope.get("omittedHostIds", [])
        if not isinstance(hosts, list):
            raise Shape("omittedHostIds")
        omitted = len(hosts)
    return bool(truncated), omitted


def locator_hash(locator: str) -> str:
    return "loc:" + hashlib.sha256(locator.encode("utf-8")).hexdigest()[:16]


def _source(reply: Reply, parse_items: Callable[[Mapping[str, Any]], tuple[list, int, bool, int]]) -> SourceView:
    if not reply.asked:
        return SourceView(Availability.UNKNOWN)
    if reply.result is None:
        return SourceView(Availability.UNAVAILABLE, error=reply.error or SourceError("invalid_response"))
    try:
        items, invalid, truncated, omitted = parse_items(reply.result)
    except Shape as exc:
        return SourceView(Availability.UNAVAILABLE, error=SourceError("invalid_response", f"shape:{exc}"))
    partial = bool(invalid or truncated or omitted)
    return SourceView(Availability.PARTIAL if partial else Availability.AVAILABLE, tuple(items),
                      invalid_items=invalid, truncated=truncated, omitted_hosts=omitted)


def _each(rows: list, parse_one: Callable[[Mapping[str, Any]], Any]) -> tuple[list, int]:
    items, invalid = [], 0
    for row in rows:
        try:
            if not isinstance(row, Mapping):
                raise Shape("item")
            items.append(parse_one(row))
        except Shape:
            invalid += 1
    return items, invalid


# --- sources ------------------------------------------------------------------------------


def parse_runtime(reply: Reply) -> RuntimeView:
    if not reply.asked:
        return RuntimeView("unknown")
    if reply.result is None:
        error = reply.error or SourceError("invalid_response")
        health = {"cli_unavailable": "cli_missing", "outcome_unknown": "timed_out", "timeout": "timed_out",
                  "invalid_response": "unreadable"}.get(error.category, "disconnected")
        return RuntimeView(health, error=error)
    runtime = reply.result.get("runtime")
    if not isinstance(runtime, Mapping):
        return RuntimeView("unreadable", error=SourceError("invalid_response", "shape:runtime"))
    version = runtime.get("appVersion") if isinstance(runtime.get("appVersion"), str) else None
    connected = (runtime.get("reachable") is True and runtime.get("state") == "ready"
                 and runtime.get("connectionState") == "connected")
    return RuntimeView("connected" if connected else "disconnected", version, connected)


def _repo(row: Mapping[str, Any]) -> RepoObs:
    return RepoObs(_req(row, "id", str), _opt(row, "displayName", str), _opt(row, "kind", str),
                   _opt(row, "path", str))


def parse_repos(reply: Reply) -> SourceView:
    def items(result):
        rows, invalid = _each(_list(result, "repos"), _repo)
        return rows, invalid, False, 0
    return _source(reply, items)


def _worktree(row: Mapping[str, Any]) -> WorktreeObs:
    locator = _req(row, "id", str)
    identity = row.get("identity")
    key, basis = None, "locator_hash"
    if isinstance(identity, Mapping) and isinstance(identity.get("key"), str) and identity["key"]:
        key, basis = identity["key"], "orca_identity"
    git = row.get("git") if isinstance(row.get("git"), Mapping) else {}
    branch = _opt(row, "branch", str) or _opt(git, "branch", str)
    return WorktreeObs(
        key=key or locator_hash(locator), identity_basis=basis, locator=locator,
        repo_id=_opt(row, "repoId", str), display_name=_opt(row, "displayName", str),
        branch=branch.removeprefix("refs/heads/") if branch else None,  # Orca 1.4.264 sends refs/heads/<name>
        is_main=_opt(row, "isMainWorktree", bool), archived=_opt(row, "isArchived", bool),
        host=_opt(row, "hostId", str), path=_opt(row, "path", str))


def parse_worktrees(reply: Reply) -> SourceView:
    def items(result):
        rows, invalid = _each(_list(result, "worktrees"), _worktree)
        truncated, omitted = _scope(result)
        return rows, invalid, truncated, omitted
    return _source(reply, items)


def _terminal(row: Mapping[str, Any]) -> TerminalObs:
    tab, leaf = _req(row, "tabId", str), _req(row, "leafId", str)
    return TerminalObs(
        pane_key=f"{tab}:{leaf}", tab_id=tab, leaf_id=leaf, locator=_req(row, "worktreeId", str),
        connected=_req(row, "connected", bool), orphaned=_opt(row, "orphaned", bool),
        incarnation_id=_opt(row, "incarnationId", str), agent_identity=_opt(row, "agentIdentity", str),
        exit_cause=_exit_cause(row.get("exitCause")), liveness=_opt(row, "verdict", str),
        last_output_at=_opt(row, "lastOutputAt", int))


def _exit_cause(value: Any) -> str | None:
    """``exitCause`` is documented in the v1.4.264 contract; its inner shape is not observed yet.

    Any present, non-null value counts as an explicit exit; only a short kind word is kept.
    """

    if value is None:
        return None
    if isinstance(value, str):
        return value if re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value) else "reported"
    if isinstance(value, Mapping):
        kind = value.get("kind") or value.get("type") or value.get("reason")
        return kind if isinstance(kind, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", kind) else "reported"
    return "reported"


def _pane(node: Any) -> PaneNode:
    if not isinstance(node, Mapping):
        raise Shape("pane")
    kind = node.get("type")
    if kind == "pane-split":
        return PaneNode(direction=_opt(node, "direction", str), first=_pane(node.get("first")),
                        second=_pane(node.get("second")))
    if kind == "terminal":
        tab, leaf = _req(node, "tabId", str), _req(node, "leafId", str)
        return PaneNode(pane_key=f"{tab}:{leaf}", active=node.get("active") is True)
    raise Shape("pane.type")


def _layout_tabs(node: Any, locator: str, out: list[TabObs]) -> None:
    if not isinstance(node, Mapping):
        raise Shape("layout")
    kind = node.get("type")
    if kind == "split":
        _layout_tabs(node.get("first"), locator, out)
        _layout_tabs(node.get("second"), locator, out)
        return
    if kind != "group":
        raise Shape("layout.type")
    group = _opt(node, "groupId", str)
    active_tab = _opt(node, "activeTabId", str)
    tabs = node.get("tabs")
    if not isinstance(tabs, list):
        raise Shape("layout.tabs")
    for tab in tabs:
        if not isinstance(tab, Mapping):
            raise Shape("tab")
        tab_id = _req(tab, "tabId", str)
        out.append(TabObs(tab_id, locator, group, tab_id == active_tab, _pane(tab.get("panes"))))


def parse_terminals(reply: Reply) -> tuple[SourceView, SourceView]:
    """Terminal panes and, from ``visualLayouts``, the tabs. One command answers both."""

    terminals = _source(reply, lambda r: (*_each(_list(r, "terminals"), _terminal), *_scope(r)))
    if not terminals.usable:
        return terminals, SourceView(terminals.availability, error=terminals.error)
    tabs: list[TabObs] = []
    invalid = 0
    layouts = reply.result.get("visualLayouts")  # type: ignore[union-attr]
    if not isinstance(layouts, list):
        return terminals, SourceView(Availability.UNAVAILABLE, error=SourceError("invalid_response", "shape:visualLayouts"))
    for layout in layouts:
        try:
            if not isinstance(layout, Mapping):
                raise Shape("layout")
            found: list[TabObs] = []
            _layout_tabs(layout.get("root"), _req(layout, "worktreeId", str), found)
            tabs.extend(found)
        except Shape:
            invalid += 1
    availability = terminals.availability if not invalid else Availability.PARTIAL
    return terminals, SourceView(availability, tuple(tabs), invalid_items=invalid,
                                 truncated=terminals.truncated, omitted_hosts=terminals.omitted_hosts)


def _agent(row: Mapping[str, Any]) -> AgentRowObs:
    session = row.get("orcaSessionId")
    if session is not None and (not isinstance(session, str) or not session):
        raise Shape("orcaSessionId")
    kind = _req(row, "kind", str)
    if kind not in ("terminal", "chat"):
        raise Shape("kind")
    return AgentRowObs(session, kind, _opt(row, "agent", str), _req(row, "state", str),
                       _req(row, "worktreeId", str), _req(row, "paneKey", str), _opt(row, "updatedAt", int))


def parse_agents(reply: Reply) -> SourceView:
    return _source(reply, lambda r: (*_each(_list(r, "agents"), _agent), *_scope(r)))


def parse_porcelain(repo_id: str, text: str) -> list[GitWorktreeObs]:
    """``git worktree list --porcelain``: blocks separated by blank lines."""

    out: list[GitWorktreeObs] = []
    block: dict[str, str] = {}
    for line in [*text.splitlines(), ""]:
        if not line.strip():
            if "worktree" in block:
                branch = block.get("branch")
                out.append(GitWorktreeObs(repo_id, block["worktree"],
                                          branch.removeprefix("refs/heads/") if branch else None,
                                          "prunable" in block, "bare" in block))
            block = {}
            continue
        name, _, value = line.partition(" ")
        block[name] = value
    return out


def parse_git(reply: Reply) -> SourceView:
    if not reply.asked:
        return SourceView(Availability.NOT_CONFIGURED)
    if reply.result is None:
        return SourceView(Availability.UNAVAILABLE, error=reply.error or SourceError("invalid_response"))
    items: list[GitWorktreeObs] = []
    failed = 0
    for repo_id, answer in reply.result.items():
        text = answer.get("porcelain") if isinstance(answer, Mapping) else None
        if isinstance(text, str):
            items.extend(parse_porcelain(repo_id, text))
        else:
            failed += 1
    if failed and not items:
        return SourceView(Availability.UNAVAILABLE, error=SourceError("git_failed"), invalid_items=failed)
    return SourceView(Availability.PARTIAL if failed else Availability.AVAILABLE, tuple(items), invalid_items=failed)


def parse_observation(raw: RawObservation) -> Observation:
    terminals, tabs = parse_terminals(raw.reply("terminals"))
    return Observation(
        at=raw.at,
        runtime=parse_runtime(raw.reply("status")),
        repos=parse_repos(raw.reply("repos")),
        worktrees=parse_worktrees(raw.reply("worktrees")),
        terminals=terminals,
        tabs=tabs,
        agents=parse_agents(raw.reply("agents")),
        git=parse_git(raw.reply("git")),
    )
