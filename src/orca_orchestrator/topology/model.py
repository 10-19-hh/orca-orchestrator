"""Values and records of the workspace topology (``orca-orchestrator/topology@1``).

The topology says which repositories, worktrees, tabs, terminal panes and agent
sessions Orca and Git report, and how sure that is. It is separate from
``snapshot@1``, which carries the task, verification, review and recovery
judgments; the two are joined only through identifiers with evidence.

Kept apart on purpose:

* identity (``session_id``, ``worktree_id``) and the process incarnation of a pane,
* provider (what kind of agent) and how it was identified,
* freshness (when last seen), connection (is the pane/runtime reachable),
* lifecycle (is the session listed, missing or confirmed ended),
* activity (what Orca reports the agent is doing),
* availability (could this be observed at all).

Records here hold private locators (raw Orca worktree IDs, paths) for joining;
``document.topology_document`` chooses the exported fields by name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

TOPOLOGY_SCHEMA = "orca-orchestrator/topology@1"


class Availability(str, Enum):
    AVAILABLE = "available"            # asked and answered completely
    PARTIAL = "partial"                # answered, but truncated, hosts omitted or items unreadable
    UNAVAILABLE = "unavailable"        # asked, no usable answer (timeout, error, unreadable)
    UNKNOWN = "unknown"                # not asked (e.g. runtime not connected)
    NOT_CONFIGURED = "not_configured"  # not requested for this collection
    NOT_IMPLEMENTED = "not_implemented"  # this build cannot observe it


class Provider(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    OTHER = "other"      # Orca named another agent type
    UNKNOWN = "unknown"  # Orca did not name one


KNOWN_PROVIDERS = {"claude": Provider.CLAUDE, "codex": Provider.CODEX}


class Activity(str, Enum):
    """Orca's reported agent status (``agent list`` ``state``); nothing finer is observable."""

    WORKING = "working"
    BLOCKED = "blocked"   # Orca UI: "Blocked" (permission or trust menu)
    WAITING = "waiting"   # Orca UI: "Waiting for input"
    DONE = "done"         # the turn ended; the session stays
    UNKNOWN = "unknown"


ORCA_STATES = {a.value: a for a in (Activity.WORKING, Activity.BLOCKED, Activity.WAITING, Activity.DONE)}


class Lifecycle(str, Enum):
    ACTIVE = "active"    # listed in the latest complete listing
    MISSING = "missing"  # absent from a complete listing, with no end evidence (stale)
    ENDED = "ended"      # explicit end evidence
    UNKNOWN = "unknown"  # the latest listing failed, was partial or was not asked


class WorktreeLifecycle(str, Enum):
    ACTIVE = "active"
    MISSING = "missing"  # Orca stopped listing it; Git still has it or was not asked
    REMOVED = "removed"  # Orca stopped listing it and Git no longer registers it
    UNKNOWN = "unknown"


class Connection(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    UNKNOWN = "unknown"


class Intent(str, Enum):
    """What the scene may show for a session, in priority order (first match wins)."""

    EXIT = "exit"                  # 1. confirmed end: leave through the entrance
    ATTENTION = "attention"        # 2. the agent waits for the user (blocked / waiting)
    UNOBSERVABLE = "unobservable"  # 3. missing, unknown, disconnected: keep the seat, show '?'
    FINISHING = "finishing"        # 4. a linked Worker settled; its terminal is being cleaned up
    WORK = "work"                  # 5. Orca reports working: walk to the own desk and sit
    WORK_DONE = "work_done"        # 6. working -> done seen between two observations: stand up
    AMBIENT = "ambient"            # 7. done (idle): use the shared spaces


INTENT_ORDER = tuple(Intent)


class EndEvidence(str, Enum):
    TERMINAL_EXIT = "terminal_exit"        # the session's pane reports exitCause for the same incarnation
    WORKTREE_REMOVED = "worktree_removed"  # its worktree is confirmed removed (Orca and Git)


class IdentityBasis(str, Enum):
    ORCA_SESSION_ID = "orca_session_id"      # Orca's own session ID: the identity
    PANE_INCARNATION = "pane_incarnation"    # no ID yet: provisional, pane + process incarnation
    PANE_ONLY = "pane_only"                  # no ID and no terminal row: provisional, pane only


class ProviderBasis(str, Enum):
    ORCA_AGENT_TYPE = "orca_agent_type"                  # agent list ``agent``
    TERMINAL_AGENT_IDENTITY = "terminal_agent_identity"  # terminal list ``agentIdentity`` (type only)
    NONE = "none"


# --- one parsed observation ------------------------------------------------------------------


@dataclass(frozen=True)
class SourceError:
    category: str        # adapter category: timeout, cli_unavailable, invalid_response, orca_error, ...
    code: str | None = None


@dataclass(frozen=True)
class SourceView:
    availability: Availability
    items: tuple = ()
    error: SourceError | None = None
    invalid_items: int = 0
    truncated: bool = False
    omitted_hosts: int = 0

    @property
    def usable(self) -> bool:
        return self.availability in (Availability.AVAILABLE, Availability.PARTIAL)

    @property
    def complete(self) -> bool:
        return self.availability is Availability.AVAILABLE


@dataclass(frozen=True)
class RuntimeView:
    health: str               # connected | disconnected | cli_missing | timed_out | unreadable
    app_version: str | None = None
    connected: bool = False
    error: SourceError | None = None


@dataclass(frozen=True)
class RepoObs:
    repo_id: str
    display_name: str | None
    kind: str | None
    path: str | None = field(default=None, repr=False)  # private: Git cross-check only


@dataclass(frozen=True)
class WorktreeObs:
    key: str                  # identity.key (wt2:<host>:<instanceId>) or a locator hash
    identity_basis: str       # "orca_identity" | "locator_hash"
    locator: str = field(repr=False)  # private: Orca worktreeId (<repoId>::<path>), joins only
    repo_id: str | None = None
    display_name: str | None = None
    branch: str | None = None
    is_main: bool | None = None
    archived: bool | None = None
    host: str | None = None
    path: str | None = field(default=None, repr=False)  # private


@dataclass(frozen=True)
class TerminalObs:
    pane_key: str             # <tabId>:<leafId>, Orca's join key for agent rows
    tab_id: str
    leaf_id: str
    locator: str = field(repr=False)
    connected: bool | None = None
    orphaned: bool | None = None
    incarnation_id: str | None = None
    agent_identity: str | None = None  # agent type only, never a session identity
    exit_cause: str | None = None      # absent while running (documented, not yet observed)
    liveness: str | None = None        # verdict, when the host reports it
    last_output_at: int | None = None


@dataclass(frozen=True)
class PaneNode:
    """Pane layout without handles: a leaf pane or a split of two."""

    pane_key: str | None = None
    direction: str | None = None
    first: "PaneNode | None" = None
    second: "PaneNode | None" = None
    active: bool = False

    def leaves(self) -> tuple[str, ...]:
        if self.pane_key is not None:
            return (self.pane_key,)
        out: tuple[str, ...] = ()
        for child in (self.first, self.second):
            if child is not None:
                out += child.leaves()
        return out


@dataclass(frozen=True)
class TabObs:
    tab_id: str
    locator: str = field(repr=False)
    group_id: str | None = None
    active: bool = False
    panes: PaneNode | None = None


@dataclass(frozen=True)
class AgentRowObs:
    session_id: str | None    # orcaSessionId; None until the agent first reports to Orca
    kind: str                 # terminal | chat
    agent_type: str | None
    state: str
    locator: str = field(repr=False)
    pane_key: str = ""
    updated_at: int | None = None


@dataclass(frozen=True)
class GitWorktreeObs:
    repo_id: str
    path: str = field(repr=False)
    branch: str | None = None
    prunable: bool = False
    bare: bool = False


@dataclass(frozen=True)
class Observation:
    at: datetime
    runtime: RuntimeView
    repos: SourceView
    worktrees: SourceView
    terminals: SourceView
    tabs: SourceView
    agents: SourceView
    git: SourceView


# --- tracked topology (what the document is made from) ---------------------------------------


@dataclass(frozen=True)
class WorkerLink:
    """A Worker Dispatch tied to a session. No Orca field links them in 1.4.264; demo only."""

    dispatch_ref: str
    phase: str      # working | finishing | exited | unknown (orca.workers.WorkerPhase values)
    basis: str      # "demo_synthetic"


@dataclass
class RepoState:
    repo_id: str
    display_name: str | None
    kind: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    listed: bool | None = True   # None: not known in the latest observation
    path: str | None = field(default=None, repr=False)


@dataclass
class WorktreeState:
    worktree_id: str
    identity_basis: str
    repo_id: str | None
    display_name: str | None
    branch: str | None
    is_main: bool | None
    archived: bool | None
    host: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    arrival: int
    lifecycle: WorktreeLifecycle = WorktreeLifecycle.ACTIVE
    missing_since: datetime | None = None
    git_listed: bool | None = None
    git_prunable: bool | None = None
    git_branch_matches: bool | None = None
    locator: str = field(default="", repr=False)
    path: str | None = field(default=None, repr=False)


@dataclass
class TerminalState:
    pane_key: str
    tab_id: str
    leaf_id: str
    worktree_id: str
    connected: bool | None
    orphaned: bool | None
    incarnation_id: str | None
    agent_identity: str | None
    exit_cause: str | None
    liveness: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    listed: bool | None = True
    incarnation_changed: bool = False
    session_ids: tuple[str, ...] = ()


@dataclass
class TabState:
    tab_id: str
    worktree_id: str
    group_id: str | None
    active: bool
    panes: PaneNode | None
    listed: bool | None = True


@dataclass
class Place:
    pane_key: str
    tab_id: str | None
    incarnation_id: str | None


@dataclass
class SessionState:
    session_id: str
    identity_basis: IdentityBasis
    kind: str
    worktree_id: str
    provider: Provider
    provider_basis: ProviderBasis
    agent_type: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    arrival: int
    places: list[Place] = field(default_factory=list)
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    activity: Activity = Activity.UNKNOWN
    last_activity: Activity = Activity.UNKNOWN
    previous_activity: Activity = Activity.UNKNOWN
    activity_updated_at: int | None = None
    connection: Connection = Connection.UNKNOWN
    missing_since: datetime | None = None
    ended_at: datetime | None = None
    end_evidence: EndEvidence | None = None
    reappeared: bool = False
    incarnation_changed: bool = False
    possible_predecessor: str | None = None
    worker: WorkerLink | None = None
    intent: Intent = Intent.UNOBSERVABLE


@dataclass(frozen=True)
class Topology:
    mode: str                 # live | recorded | demo
    demo: dict | None
    observed_at: datetime
    sequence: int
    runtime: RuntimeView
    sources: dict[str, SourceView]
    repositories: tuple[RepoState, ...]
    worktrees: tuple[WorktreeState, ...]
    tabs: tuple[TabState, ...]
    terminals: tuple[TerminalState, ...]
    sessions: tuple[SessionState, ...]
    worker_links: Availability
    diagnostics: tuple[dict, ...]
    provenance: dict
