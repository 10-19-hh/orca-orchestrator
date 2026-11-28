"""Which code a check ran against, and what changed since the plan's base (FR-06, FR-07).

Two facts are kept apart.

* The **source digest** fingerprints the files a local check reads: every file
  under ``src/`` and ``tests/`` of the repository (:data:`VERIFICATION_SCOPE`),
  plus the Python modules (``*.py``) and regular packages (folders with an
  ``__init__.py``) directly under the repository root, read from disk, so
  edits that are not committed count. The root is included because the unit
  test command runs there with ``python -m``, which puts the repository root
  first on ``sys.path``: tests can import those modules, so a change there can
  change the outcome. Folders without ``__init__.py`` (namespace packages,
  ``docs/``, ``examples/``) and other root files (``README.md``,
  ``pyproject.toml``) are not part of it; a repository without root modules
  keeps the digest it had before. Git is not consulted:
  two working trees at the same HEAD with different edits get different
  digests. Caches (``__pycache__``, ``*.pyc``), hidden files and folders, and
  the tool's own files (plan, results and their ``.lock``/``.flow.json``/
  ``.approval.json``/``.handoff.json`` companions, run locks, ``*.tmp``) are skipped, so running tests or
  saving results never expires a result.
* The **change set** lists files that differ from the plan's ``base``
  revision: tracked files changed in the working tree (committed or not) and
  untracked files Git does not ignore. It runs only read-only Git commands
  through :class:`GitReader`.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from ..orca.dispatch import AUTH_ENV_NAMES
from ..orca.runner import CommandLaunchError, CommandRunner, CommandTimeout, SubprocessRunner
from .contract import CHECK_DIGEST_LENGTH
from .paths import is_tool_file_name

VERIFICATION_SCOPE = ("src", "tests")
CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
CACHE_SUFFIXES = (".pyc", ".pyo")
GIT_TIMEOUT_SECONDS = 15.0


def _digest(payload: object) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:CHECK_DIGEST_LENGTH]


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(paths: Iterable[Path], repo: Path) -> frozenset[str]:
    out = set()
    root = repo.resolve()
    for p in paths:
        try:
            out.add(Path(p).resolve().relative_to(root).as_posix())
        except (ValueError, OSError):
            continue
    return frozenset(out)


# --- source digest -------------------------------------------------------------


@dataclass(frozen=True)
class SourceState:
    digest: str | None  # None: could not be determined (see error)
    files: int = 0
    error: str | None = None


ROOT_MODULES_TEXT = "저장소 루트의 Python 모듈·패키지"


def scope_text() -> str:
    return ", ".join([*(f"{d}/" for d in VERIFICATION_SCOPE), ROOT_MODULES_TEXT])


def _skipped_name(name: str) -> bool:
    return name.startswith(".") or name.endswith(CACHE_SUFFIXES) or is_tool_file_name(name)


def _root_targets(repo: Path) -> tuple[list[str], list[Path]]:
    """Importable code directly under the repository root: ``*.py`` files and regular package folders."""

    files, packages = [], []
    with os.scandir(repo) as it:
        listing = sorted(it, key=lambda e: e.name)
    for entry in listing:
        name = entry.name
        if name.startswith(".") or name in VERIFICATION_SCOPE or name in CACHE_DIRS:
            continue
        if entry.is_file() and name.endswith(".py") and not _skipped_name(name):
            files.append(name)
        elif entry.is_dir() and (Path(entry.path) / "__init__.py").is_file():
            packages.append(Path(entry.path))
    return files, packages


def source_state(repo: str | Path, exclude: Iterable[Path] = ()) -> SourceState:
    """Fingerprint of the verification scope as it is on disk now."""

    repo = Path(repo)
    skipped = _relative(exclude, repo)
    entries: list[tuple[str, str]] = []

    def walk(root: Path) -> None:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in CACHE_DIRS)
            rel_dir = Path(dirpath).relative_to(repo).as_posix()
            for name in sorted(filenames):
                if _skipped_name(name):
                    continue
                rel = f"{rel_dir}/{name}"
                if rel in skipped:
                    continue
                entries.append((rel, _file_hash(Path(dirpath) / name)))

    try:
        for top in VERIFICATION_SCOPE:
            root = repo / top
            if root.is_dir():
                walk(root)
        if repo.is_dir():
            modules, packages = _root_targets(repo)
            for name in modules:
                if name not in skipped:
                    entries.append((name, _file_hash(repo / name)))
            for package in packages:
                walk(package)
    except OSError as exc:
        return SourceState(None, 0, f"검증 대상 파일을 읽을 수 없음 ({type(exc).__name__})")
    entries.sort()  # an empty scope is a state too; the test run itself reports missing tests
    return SourceState(_digest(entries), len(entries))


# --- git -----------------------------------------------------------------------

GIT_READ_COMMANDS = frozenset({"rev-parse", "diff", "ls-files"})
# Not passed to git: model credentials and the Orca terminal binding.
_GIT_STRIPPED_ENV = frozenset({n for names in AUTH_ENV_NAMES.values() for n in names} | {"ORCA_TERMINAL_HANDLE"})


def git_environment() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_STRIPPED_ENV}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


class GitError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class GitReader:
    """Runs read-only Git commands in ``repo``; anything else is refused before running."""

    def __init__(self, repo: str | Path, runner: CommandRunner | None = None,
                 timeout: float = GIT_TIMEOUT_SECONDS) -> None:
        self.repo = Path(repo)
        self.runner = runner if runner is not None else SubprocessRunner(git_environment())
        self.timeout = timeout

    def run(self, *args: str) -> str:
        if not args or args[0] not in GIT_READ_COMMANDS:
            raise GitError(f"허용하지 않는 git 명령 ({args[0] if args else '없음'})")
        argv = ["git", "--no-optional-locks", "-c", "core.quotepath=off", "-C", str(self.repo), *args]
        try:
            result = self.runner.run(argv, timeout=self.timeout)
        except CommandTimeout:
            raise GitError("git 응답 시간 초과") from None
        except CommandLaunchError:
            raise GitError("git을 실행할 수 없음") from None
        if result.exit_code != 0:
            raise GitError(f"git {args[0]} 실패 (종료 코드 {result.exit_code})")
        return result.stdout


@dataclass(frozen=True)
class CodeBasis:
    """Git facts shown next to a source digest. Informational; never decides validity."""

    head: str | None = None
    dirty: bool | None = None  # uncommitted changes (tracked or untracked), tool files and caches aside

    def text(self) -> str:
        if self.head is None:
            return "Git 커밋 정보 없음"
        dirty = " + 커밋 전 변경" if self.dirty else (" (커밋 전 변경 없음)" if self.dirty is False else "")
        return f"HEAD {self.head[:7]}{dirty}"


class ChangeStatus(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


CHANGE_TEXT = {ChangeStatus.ADDED: "추가", ChangeStatus.MODIFIED: "수정", ChangeStatus.DELETED: "삭제"}


@dataclass(frozen=True)
class FileChange:
    path: str  # repository-relative, '/'-separated
    status: ChangeStatus

    def text(self) -> str:
        return f"{self.path} ({CHANGE_TEXT[self.status]})"


@dataclass(frozen=True)
class ChangeSet:
    base: str | None  # as written in the plan
    base_sha: str | None = None
    changes: tuple[FileChange, ...] = ()
    digest: str | None = None  # paths, statuses and current contents of every change
    error: str | None = None  # why the comparison could not be made

    @property
    def ok(self) -> bool:
        return self.error is None and self.base_sha is not None


def _ignored_change(path: str, skipped: frozenset[str]) -> bool:
    parts = path.split("/")
    return (path in skipped or any(p in CACHE_DIRS for p in parts[:-1])
            or parts[-1].endswith(CACHE_SUFFIXES) or is_tool_file_name(parts[-1]))


_STATUS = {"A": ChangeStatus.ADDED, "D": ChangeStatus.DELETED}


def _changed(git: GitReader, rev: str, skipped: frozenset[str]) -> dict[str, ChangeStatus]:
    tokens = git.run("diff", "--name-status", "-z", "--no-renames", rev, "--").split("\0")
    changes: dict[str, ChangeStatus] = {}
    for status, path in zip(tokens[0::2], tokens[1::2]):
        if status and path and not _ignored_change(path, skipped):
            changes[path] = _STATUS.get(status[0], ChangeStatus.MODIFIED)
    for path in git.run("ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if path and not _ignored_change(path, skipped):
            changes[path] = ChangeStatus.ADDED
    return changes


def _check_root(git: GitReader) -> None:
    try:
        prefix = git.run("rev-parse", "--show-prefix").strip()
    except GitError as exc:
        if exc.message.startswith("git rev-parse"):
            raise GitError("--repo가 Git 저장소가 아님") from None
        raise
    if prefix:
        raise GitError("--repo가 Git 저장소의 최상위 폴더가 아님")


def code_basis(repo: str | Path, exclude: Iterable[Path] = (), git: GitReader | None = None) -> CodeBasis:
    """HEAD and whether anything is uncommitted; empty when Git is unavailable or has no commit."""

    repo = Path(repo)
    git = git or GitReader(repo)
    try:
        _check_root(git)
        head = git.run("rev-parse", "--verify", "--quiet", "HEAD").strip()
    except GitError:
        return CodeBasis()
    try:
        dirty = bool(_changed(git, head, _relative(exclude, repo)))
    except GitError:
        dirty = None
    return CodeBasis(head, dirty)


def compare_changes(repo: str | Path, base: str | None, exclude: Iterable[Path] = (),
                    git: GitReader | None = None) -> ChangeSet:
    """Files that differ from ``base`` now, with a digest of their current contents."""

    if base is None:
        return ChangeSet(None, error="작업 정의에 비교 기준(base)이 없음")
    repo = Path(repo)
    git = git or GitReader(repo)
    try:
        _check_root(git)
    except GitError as exc:
        return ChangeSet(base, error=exc.message)
    try:
        sha = git.run("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").strip()
    except GitError:
        return ChangeSet(base, error=f"비교 기준 {base}을(를) 저장소에서 찾을 수 없음")
    try:
        found = _changed(git, sha, _relative(exclude, repo))
    except GitError as exc:
        return ChangeSet(base, sha, error=exc.message)
    changes = tuple(FileChange(path, found[path]) for path in sorted(found))
    entries = []
    try:
        for change in changes:
            content = "-" if change.status is ChangeStatus.DELETED else _file_hash(repo / change.path)
            entries.append((change.path, change.status.value, content))
    except OSError as exc:
        return ChangeSet(base, sha, changes, error=f"변경 파일을 읽을 수 없음 ({type(exc).__name__})")
    return ChangeSet(base, sha, changes, _digest([sha, entries]))
