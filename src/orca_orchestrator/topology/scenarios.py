"""Synthetic DEMO scenes for the workspace topology.

Every scene is a sequence of raw replies shaped like Orca 1.4.264's
``status``/``repo list``/``worktree list``/``terminal list``/``agent list``
answers and the porcelain output of ``git worktree list``. They run through the
same parser and tracker as a live collection. IDs, names and paths are example
values (``example-…``, ``/example/…``) and no scene is evidence of live behaviour:
``evidence`` says which parts follow an observed reply shape and which
transitions were never observed on a real host.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .model import SourceError, WorkerLink
from .parse import RawObservation, Reply

START = datetime(2026, 11, 29, 3, 0, tzinfo=timezone.utc)
STEP = timedelta(seconds=30)

SHAPE_OBSERVED = "응답 형태: Orca 1.4.264 실제 응답과 같은 필드 구조"
NOT_OBSERVED = "이 전이는 실제 호스트에서 관측되지 않음 (합성)"


# --- reply builders -----------------------------------------------------------------------


def status(connected: bool = True) -> dict[str, Any]:
    if not connected:
        return {"app": {"running": False}, "runtime": {"state": "not_running", "reachable": False,
                                                       "connectionState": "disconnected", "appVersion": None},
                "graph": {"state": "unavailable"}}
    return {"app": {"running": True}, "runtime": {"state": "ready", "reachable": True, "connectionState": "connected",
                                                  "appVersion": "1.4.264"}, "graph": {"state": "ready"}}


def repo(n: int, name: str = "example-app") -> dict[str, Any]:
    return {"id": f"example-repo-{n}", "path": f"/example/{name}", "displayName": name, "kind": "git",
            "gitUsername": "example-user", "gitRemoteIdentity": {"remoteUrl": "https://example.invalid/app.git"}}


@dataclass(frozen=True)
class Wt:
    repo: int
    n: int
    branch: str
    main: bool = False

    @property
    def path(self) -> str:
        return f"/example/repo-{self.repo}" if self.main else f"/example/repo-{self.repo}-wt/{self.branch}"

    @property
    def locator(self) -> str:
        return f"example-repo-{self.repo}::{self.path}"

    @property
    def key(self) -> str:
        return f"wt2:local:example-wt-{self.repo}-{self.n}"


def worktree(w: Wt) -> dict[str, Any]:
    return {"id": w.locator, "identity": {"key": w.key, "executionHostId": "local",
                                          "instanceId": f"example-wt-{w.repo}-{w.n}"},
            "repoId": f"example-repo-{w.repo}", "hostId": "local", "path": w.path, "head": "0" * 40,
            "branch": f"refs/heads/{w.branch}", "isBare": False, "isMainWorktree": w.main, "displayName": w.branch,
            "isArchived": False, "workspaceStatus": "in-progress",
            "git": {"path": w.path, "branch": f"refs/heads/{w.branch}"}}


@dataclass(frozen=True)
class Pane:
    w: Wt
    tab: str
    leaf: str
    incarnation: str
    agent: str | None = None      # terminals[].agentIdentity
    connected: bool = True
    exit_cause: Any = None

    @property
    def key(self) -> str:
        return f"{self.tab}:{self.leaf}"


def terminal(p: Pane) -> dict[str, Any]:
    out = {"handle": f"term_example-{p.leaf}", "ptyId": f"{p.w.locator}@@{p.leaf}", "incarnationId": p.incarnation,
           "orphaned": False, "worktreeId": p.w.locator, "worktreePath": p.w.path, "branch": p.w.branch,
           "tabId": p.tab, "leafId": p.leaf, "title": "example title /example/secret", "connected": p.connected,
           "writable": True, "lastOutputAt": 1795000000000, "preview": "EXAMPLE PREVIEW TEXT",
           "executionHostId": "local"}
    if p.agent:
        out["agentIdentity"] = p.agent
    if p.exit_cause is not None:
        out["exitCause"] = p.exit_cause
    return out


def layouts(panes: list[Pane]) -> list[dict[str, Any]]:
    by_wt: dict[str, list[Pane]] = {}
    for p in panes:
        by_wt.setdefault(p.w.locator, []).append(p)
    out = []
    for locator, ps in by_wt.items():
        tabs: dict[str, list[Pane]] = {}
        for p in ps:
            tabs.setdefault(p.tab, []).append(p)
        tab_nodes = []
        for tab_id, leaves in tabs.items():
            node: dict[str, Any] = _leaf(leaves[0], True)
            for extra in leaves[1:]:
                node = {"type": "pane-split", "direction": "horizontal", "first": node, "second": _leaf(extra, False)}
            tab_nodes.append({"tabId": tab_id, "title": "example tab", "activeLeafId": leaves[0].leaf, "panes": node})
        out.append({"worktreeId": locator, "worktreePath": ps[0].w.path,
                    "root": {"type": "group", "groupId": f"group-{locator[-6:]}",
                             "activeTabId": tab_nodes[0]["tabId"], "tabs": tab_nodes}})
    return out


def _leaf(p: Pane, active: bool) -> dict[str, Any]:
    return {"type": "terminal", "handle": f"term_example-{p.leaf}", "tabId": p.tab, "leafId": p.leaf,
            "title": "example pane", "connected": p.connected, "active": active}


@dataclass(frozen=True)
class Agent:
    session: str | None
    agent: str | None
    state: str
    pane: Pane
    kind: str = "terminal"
    updated: int = 1795000000000


def agent_row(a: Agent) -> dict[str, Any]:
    return {"orcaSessionId": a.session, "kind": a.kind, "agent": a.agent, "state": a.state,
            "worktreeId": a.pane.w.locator, "workspace": a.pane.w.branch, "repo": "example-app", "hostId": "local",
            "paneKey": a.pane.key, "title": "example agent title",
            "lastAssistantMessage": "EXAMPLE ASSISTANT TEXT", "updatedAt": a.updated}


def porcelain(worktrees: list[Wt], prunable: frozenset[str] = frozenset()) -> str:
    blocks = []
    for w in worktrees:
        lines = [f"worktree {w.path}", "HEAD " + "0" * 40, f"branch refs/heads/{w.branch}"]
        if w.key in prunable:
            lines.append("prunable gitdir file points to non-existent location")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


@dataclass
class Step:
    """One observation of a scene. ``None`` for a source: as answered by ``replies`` overrides."""

    worktrees: list[Wt]
    panes: list[Pane] = field(default_factory=list)
    agents: list[Agent] = field(default_factory=list)
    connected: bool = True
    overrides: dict[str, Reply] = field(default_factory=dict)
    git_worktrees: list[Wt] | None = None
    git_prunable: frozenset[str] = frozenset()
    links: dict[str, WorkerLink] | None = None
    repos: tuple[int, ...] = (1,)


def raw(step: Step, at: datetime) -> RawObservation:
    replies: dict[str, Reply] = {"status": Reply(status(step.connected))}
    if step.connected:
        replies.update({
            "repos": Reply({"repos": [repo(n, f"example-app-{n}" if n > 1 else "example-app") for n in step.repos]}),
            "worktrees": Reply({"worktrees": [worktree(w) for w in step.worktrees], "totalCount": len(step.worktrees),
                                "truncated": False, "hostScope": {"hostIds": ["local"], "omittedHostIds": []}}),
            "terminals": Reply({"terminals": [terminal(p) for p in step.panes], "visualLayouts": layouts(step.panes),
                                "topologyRevisions": {}, "totalCount": len(step.panes), "truncated": False,
                                "hostScope": {"hostIds": ["local"], "omittedHostIds": []}}),
            "agents": Reply({"agents": [agent_row(a) for a in step.agents], "truncated": False,
                             "hostScope": {"hostIds": ["local"], "omittedHostIds": []}}),
        })
        git_wts = step.worktrees if step.git_worktrees is None else step.git_worktrees
        replies["git"] = Reply({f"example-repo-{n}": {"porcelain": porcelain([w for w in git_wts if w.repo == n],
                                                                              step.git_prunable)}
                                for n in step.repos})
    replies.update(step.overrides)
    return RawObservation(at, replies)


@dataclass(frozen=True)
class Scene:
    name: str
    title: str
    evidence: str
    steps: tuple[Step, ...]

    def observations(self) -> list[tuple[RawObservation, dict[str, WorkerLink] | None]]:
        return [(raw(s, START + STEP * i), s.links) for i, s in enumerate(self.steps)]


# --- the scenes ---------------------------------------------------------------------------

W1 = Wt(1, 1, "main", main=True)
W2 = Wt(1, 2, "feature-room")
W3 = Wt(2, 3, "docs", main=True)


def pane(w: Wt, n: int, agent: str | None = None, inc: str | None = None, **kw) -> Pane:
    return Pane(w, f"example-tab-{w.n}-{n}", f"example-leaf-{w.n}-{n}", inc or f"example-inc-{w.n}-{n}", agent, **kw)


def sessions(w: Wt, specs: list[tuple[str, str]], start: int = 1) -> tuple[list[Pane], list[Agent]]:
    """``specs``: (provider, state) per session, each in its own tab and pane."""

    panes, agents = [], []
    for i, (provider, state) in enumerate(specs, start):
        p = pane(w, i, provider)
        panes.append(p)
        agents.append(Agent(f"example-session-{w.n}-{i}", provider, state, p))
    return panes, agents


def _one(specs: list[tuple[str, str]]) -> Step:
    panes, agents = sessions(W1, specs)
    return Step([W1], panes, agents)


def _scenes() -> dict[str, Scene]:
    scenes: list[Scene] = []
    add = scenes.append

    add(Scene("empty-worktree", "저장소 1개, Worktree 1개, 세션 0개", SHAPE_OBSERVED, (Step([W1]),)))
    add(Scene("one-codex", "Codex 세션 1개 (작업 중)", SHAPE_OBSERVED, (_one([("codex", "working")]),)))
    add(Scene("one-claude", "Claude 세션 1개 (대기)", SHAPE_OBSERVED, (_one([("claude", "done")]),)))
    add(Scene("codex-and-claude", "Codex 1개 + Claude 1개", SHAPE_OBSERVED,
              (_one([("codex", "working"), ("claude", "done")]),)))
    add(Scene("same-provider-three", "같은 Provider(Codex) 세션 3개", SHAPE_OBSERVED,
              (_one([("codex", "working"), ("codex", "done"), ("codex", "blocked")]),)))
    add(Scene("four-sessions", "세션 4개 (Claude 2 + Codex 2)", SHAPE_OBSERVED,
              (_one([("claude", "working"), ("codex", "working"), ("claude", "done"), ("codex", "waiting")]),)))
    add(Scene("eight-sessions", "세션 8개 (Claude 4 + Codex 4)", SHAPE_OBSERVED,
              (_one([("claude", "working"), ("codex", "working"), ("claude", "done"), ("codex", "done"),
                     ("claude", "working"), ("codex", "blocked"), ("claude", "waiting"), ("codex", "working")]),)))

    p2, a2 = sessions(W2, [("claude", "working"), ("codex", "done")])
    p3, a3 = sessions(W3, [("codex", "working"), ("claude", "working"), ("codex", "done"), ("claude", "done"),
                           ("codex", "waiting")])
    add(Scene("three-worktrees", "Worktree 3개 (세션 0 · 2 · 5)", SHAPE_OBSERVED,
              (Step([W1, W2, W3], p2 + p3, a2 + a3, repos=(1, 2)),)))

    plain_1 = pane(W1, 1)                      # a shell: no agent identity, no agent row
    plain_2 = pane(W1, 2, "codex")             # host names an agent type, but no agent row
    agent_p = pane(W1, 3, "claude")
    add(Scene("terminals-and-agents", "일반 터미널 2개 + Agent 세션 1개",
              SHAPE_OBSERVED + ". agentIdentity만 있고 agent 행이 없는 패널은 세션으로 만들지 않음",
              (Step([W1], [plain_1, plain_2, agent_p], [Agent("example-session-1-3", "claude", "working", agent_p)]),)))

    u1, u2 = pane(W1, 1), pane(W1, 2)
    add(Scene("unknown-provider", "Provider 미상 (agent 없음 · 다른 에이전트 종류)",
              SHAPE_OBSERVED + ". agent=null 행은 문서상 가능, 실제로는 관측되지 않음",
              (Step([W1], [u1, u2], [Agent("example-session-1-1", None, "working", u1),
                                      Agent("example-session-1-2", "gemini", "done", u2)]),)))

    n_panes, n_agents = sessions(W1, [("codex", "working")])
    n2_panes, n2_agents = sessions(W1, [("codex", "working"), ("claude", "working")])
    add(Scene("new-session", "새 세션 관측 (Codex 1개 → Claude 추가)", SHAPE_OBSERVED,
              (Step([W1], n_panes, n_agents), Step([W1], n2_panes, n2_agents))))

    e_pane = pane(W1, 1, "claude")
    e_exit = pane(W1, 1, "claude", exit_cause={"kind": "exited"})
    add(Scene("session-ended", "세션 종료 확인 (패널 exitCause)",
              f"{SHAPE_OBSERVED}. exitCause는 1.4.264 계약에만 있음. {NOT_OBSERVED}",
              (Step([W1], [e_pane], [Agent("example-session-1-1", "claude", "working", e_pane)]),
               Step([W1], [e_exit], []))))

    d_panes, d_agents = sessions(W1, [("claude", "working"), ("codex", "done")])
    add(Scene("disconnected", "Runtime 연결 끊김",
              "status 미연결 응답 형태는 Step 0 기록과 같음. 이후 목록 명령은 묻지 않음",
              (Step([W1], d_panes, d_agents), Step([W1], d_panes, d_agents, connected=False))))

    m_panes, m_agents = sessions(W1, [("codex", "working"), ("codex", "working")])
    add(Scene("session-missing", "세션 일시 누락 후 재등장", f"{SHAPE_OBSERVED}. {NOT_OBSERVED}",
              (Step([W1], m_panes, m_agents),
               Step([W1], m_panes, m_agents[:1]),
               Step([W1], m_panes, [m_agents[0], Agent("example-session-1-2", "codex", "done", m_panes[1])]))))

    r1 = pane(W1, 1, "claude", inc="example-inc-a")
    r1b = pane(W1, 1, "claude", inc="example-inc-a2")
    r2 = pane(W1, 2, "codex", inc="example-inc-b")
    r2b = pane(W1, 2, "codex", inc="example-inc-b2")
    add(Scene("reconnect-unconfirmed", "재연결 후 동일성 미확인",
              f"{SHAPE_OBSERVED}. ID 없는 터미널 에이전트 행과 재연결은 {NOT_OBSERVED}",
              (Step([W1], [r1, r2], [Agent("example-session-1-1", "claude", "working", r1),
                                      Agent(None, "codex", "working", r2)]),
               Step([W1], [r1, r2], [], connected=False),
               Step([W1], [r1b, r2b], [Agent("example-session-1-1", "claude", "done", r1b),
                                        Agent(None, "codex", "working", r2b)]))))

    dup_a, dup_b = pane(W1, 1, "codex"), pane(W1, 2, "codex")
    add(Scene("duplicate-entity", "같은 세션이 두 패널에 열림 (중복 조회)",
              f"{SHAPE_OBSERVED}. 두 패널에서 재개된 세션은 계약 설명에 따름, 실제로는 관측되지 않음",
              (Step([W1], [dup_a, dup_b], [Agent("example-session-1-1", "codex", "working", dup_a, updated=1795000000500),
                                            Agent("example-session-1-1", "codex", "working", dup_b)]),)))

    f_panes, f_agents = sessions(W1, [("claude", "working"), ("codex", "working")])
    link = {"example-session-1-1": WorkerLink("example-dispatch-4b", "finishing", "demo_synthetic")}
    add(Scene("worker-finishing", "Worker 작업 완료·터미널 정리 중 (합성 연결)",
              "Worker 단계 값은 4-B 관측 순서를 따름. Worker와 세션의 연결은 Orca 필드가 없어 합성 DEMO 연결",
              (Step([W1], f_panes, f_agents, links=link),)))

    q_panes, q_agents = sessions(W1, [("claude", "working"), ("codex", "blocked")])
    add(Scene("query-failed", "agent list 조회 실패 후 마지막 관측 유지", f"{SHAPE_OBSERVED}. 실패는 합성",
              (Step([W1], q_panes, q_agents),
               Step([W1], q_panes, q_agents,
                    overrides={"agents": Reply(error=SourceError("outcome_unknown"))}))))
    return {s.name: s for s in scenes}


SCENES: dict[str, Scene] = _scenes()


# --- the live-preview timeline (python -m orca_orchestrator --topology-preview --demo) -----------------


def preview_timeline() -> Scene:
    """One synthetic sequence through the preview's checks: empty room, arrivals, working <-> done, a plain
    terminal, a temporary gap, a disconnection, reconnection without duplicates, and a confirmed end.

    Not part of ``SCENES`` (the golden set); the preview tests check its meaning step by step.
    """

    a, b = pane(W1, 1, "codex"), pane(W1, 2, "claude")
    c, d = pane(W1, 3, "codex"), pane(W1, 4, "claude")
    e = pane(W2, 1, "claude")
    shell = pane(W1, 5)                              # a plain terminal: never a pet
    a2 = pane(W1, 1, "codex", inc="example-inc-1-1b")  # same pane, new process after reconnecting
    b_gone = pane(W1, 2, "claude", exit_cause={"kind": "exited"})
    A = lambda state, p=a: Agent("example-session-1-1", "codex", state, p)  # noqa: E731
    B = lambda state, p=b: Agent("example-session-1-2", "claude", state, p)  # noqa: E731
    C = lambda state: Agent("example-session-1-3", "codex", state, c)  # noqa: E731
    D = lambda state: Agent("example-session-1-4", "claude", state, d)  # noqa: E731
    E = lambda state: Agent("example-session-2-1", "claude", state, e)  # noqa: E731
    wts = [W1, W2]
    four = [a, b, c, d, e]
    steps = (
        Step(wts),                                                          # 1 no sessions
        Step(wts, [a], [A("working")]),                                     # 2 one Codex arrives, works
        Step(wts, [a, b], [A("working"), B("working")]),                    # 3 one Claude arrives
        Step(wts, four, [A("working"), B("working"), C("done"), D("done"), E("working")]),  # 4 2+2 (+1 in W2)
        Step(wts, four, [A("done"), B("working"), C("done"), D("waiting"), E("working")]),  # 5 working -> done
        Step(wts, four, [A("working"), B("working"), C("working"), D("waiting"), E("done")]),  # 6 done -> working
        Step(wts, four + [shell], [A("working"), B("working"), C("working"), D("done"), E("done")]),  # 7 shell
        Step(wts, four + [shell], [A("working"), B("working"), C("working"), E("done")]),  # 8 D missing
        Step(wts, four + [shell], [], connected=False),                     # 9 runtime disconnected
        Step(wts, [a2, b, c, d, e, shell],                                  # 10 reconnect: same IDs, no duplicates
             [A("working", a2), B("working"), C("done"), D("done"), D("done"), E("done")]),
        Step(wts, [a2, b_gone, c, d, e, shell], [A("working", a2), C("done"), D("done"), E("done")]),  # 11 B ends
    )
    return Scene("preview-timeline", "Topology Preview 합성 진행 (11단계)",
                 f"{SHAPE_OBSERVED}. 진행 순서와 전이는 합성이며 실제 세션 관측이 아님", steps)


PREVIEW_STEP_NOTES = (
    "세션 0개: 공용 공간만",
    "Codex 1개 도착 · 작업",
    "Claude 1개 도착 · 작업",
    "Codex 2 + Claude 2 (feature-room에 Claude 1)",
    "Codex #1 working → done, Claude #4 확인 필요",
    "Codex #1 done → working",
    "일반 터미널 추가 (펫 없음)",
    "Claude #4 목록에서 일시 누락 (자리 유지)",
    "Runtime 연결 끊김 (모두 관측 불가)",
    "재연결: 같은 ID는 같은 펫, 중복 행은 하나로",
    "Claude #2 종료 확인 → 퇴장",
)
