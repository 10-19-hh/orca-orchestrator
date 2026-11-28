"""Read-only discovery: asks Orca and Git for the workspace structure once.

Only the commands below are issued. Each was checked against the installed
Orca 1.4.264 CLI command specs (``out/cli/specs``) and answered read-only on a
Windows host; flags are the documented ones. Nothing creates, sends, starts,
stops, focuses or renames anything, and no Worker command is involved.

=============  ==================================================  ====================
source         argv after the CLI path                              answers
=============  ==================================================  ====================
status         ``status --json``                                    runtime reachability
repos          ``repo list --json``                                 repositories
worktrees      ``worktree list --json``                             worktrees + identity
terminals      ``terminal list --include-visual-layouts --json``    panes, tabs, layout
agents         ``agent list --json``                                agent sessions
git            ``git --no-optional-locks -C <repo> worktree list --porcelain``  Git cross-check
=============  ==================================================  ====================

When the runtime is not connected, the four listing commands are not asked
(their sources are ``unknown``). A failure in one source never stops the others.
Errors keep only their category and code; messages and argv are dropped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence

from ..orca.adapter import OrcaCliAdapter
from ..orca.errors import OrcaAdapterError
from ..orca.runner import CommandLaunchError, CommandResult, CommandRunner, CommandTimeout, SubprocessRunner
from ..tasks.codebase import git_environment
from .model import SourceError
from .parse import RawObservation, Reply, parse_repos

TOPOLOGY_TAILS: dict[str, tuple[str, ...]] = {
    "status": ("status", "--json"),
    "repos": ("repo", "list", "--json"),
    "worktrees": ("worktree", "list", "--json"),
    "terminals": ("terminal", "list", "--include-visual-layouts", "--json"),
    "agents": ("agent", "list", "--json"),
}
GIT_TAIL = ("worktree", "list", "--porcelain")
DEFAULT_TIMEOUT_SECONDS = 10.0
GIT_TIMEOUT_SECONDS = 10.0


class TopologyCommandRefused(PermissionError):
    """A command outside the topology allowlist was about to run. A programming error, never a state."""


class TopologyReadOnlyRunner:
    """Lets exactly the topology read commands through; anything else raises before running."""

    def __init__(self, inner: CommandRunner) -> None:
        self._inner = inner

    def run(self, argv: Sequence[str], *, timeout: float, cwd: str | None = None) -> CommandResult:
        tail = tuple(argv[1:])
        if tail not in TOPOLOGY_TAILS.values():
            raise TopologyCommandRefused(f"topology discovery refused: {' '.join(tail)}")
        return self._inner.run(argv, timeout=timeout, cwd=cwd)


def is_git_worktree_list(argv: Sequence[str]) -> bool:
    return (len(argv) == 7 and tuple(argv[:4]) == ("git", "--no-optional-locks", "-C", argv[3])
            and bool(argv[3]) and tuple(argv[4:]) == GIT_TAIL)


class GitWorktreeReader:
    """``git worktree list --porcelain`` in one repository; nothing else."""

    def __init__(self, runner: CommandRunner | None = None, timeout: float = GIT_TIMEOUT_SECONDS) -> None:
        self._runner = runner if runner is not None else SubprocessRunner(git_environment())
        self._timeout = timeout

    def read(self, repo_path: str) -> Reply:
        argv = ("git", "--no-optional-locks", "-C", repo_path, *GIT_TAIL)
        if not is_git_worktree_list(argv):
            raise TopologyCommandRefused("git argv")
        try:
            result = self._runner.run(argv, timeout=self._timeout)
        except CommandTimeout:
            return Reply(error=SourceError("timeout"))
        except CommandLaunchError:
            return Reply(error=SourceError("git_unavailable"))
        if result.exit_code != 0:
            return Reply(error=SourceError("git_failed", f"exit:{result.exit_code}"))
        return Reply({"porcelain": result.stdout})


def _error(exc: OrcaAdapterError) -> SourceError:
    return SourceError(exc.category, exc.code)


class TopologyDiscovery:
    """Collects one :class:`RawObservation` from the live Orca CLI and Git.

    ``git=False`` skips the Git cross-check (``git`` source ``not_configured``).
    """

    def __init__(self, runner: CommandRunner | None = None, *, cli_path: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS, environ: Mapping[str, str] | None = None,
                 git: GitWorktreeReader | None | bool = True,
                 clock: Callable[[], datetime] | None = None) -> None:
        inner = runner if runner is not None else SubprocessRunner()
        self._adapter = OrcaCliAdapter(TopologyReadOnlyRunner(inner), cli_path, timeout=timeout, environ=environ)
        if git is True:
            git = GitWorktreeReader()
        self._git = git or None
        self._clock = clock or (lambda: datetime.now(timezone.utc).replace(microsecond=0))

    def _ask(self, name: str) -> Reply:
        try:
            return Reply(self._adapter.read(TOPOLOGY_TAILS[name]))
        except OrcaAdapterError as exc:
            return Reply(error=_error(exc))

    def collect(self) -> RawObservation:
        at = self._clock()
        replies: dict[str, Reply] = {"status": self._ask("status")}
        status = replies["status"].result
        runtime = status.get("runtime") if isinstance(status, Mapping) else None
        connected = (isinstance(runtime, Mapping) and runtime.get("reachable") is True
                     and runtime.get("state") == "ready" and runtime.get("connectionState") == "connected")
        if connected:
            for name in ("repos", "worktrees", "terminals", "agents"):
                replies[name] = self._ask(name)
            if self._git is not None:
                replies["git"] = self._git_replies(replies["repos"])
        return RawObservation(at, replies)

    def _git_replies(self, repos: Reply) -> Reply:
        view = parse_repos(repos)
        if not view.usable:
            return Reply(error=SourceError("repos_unavailable"))
        answers: dict[str, object] = {}
        for repo in view.items:
            if repo.kind != "git" or not repo.path:
                continue
            reply = self._git.read(repo.path)  # type: ignore[union-attr]
            answers[repo.repo_id] = (dict(reply.result) if reply.result is not None
                                     else {"error": {"category": reply.error.category if reply.error else "git_failed"}})
        return Reply(answers)
