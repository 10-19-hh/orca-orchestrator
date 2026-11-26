"""Runs one program-defined local check for one task and records the outcome (FR-06).

Only checks listed in :data:`SUPPORTED_CHECKS` can run. A plan links a task to a
check by its ID; the ``command`` text in the plan JSON is never executed. The
command runs once, without a shell, and its exit code decides ``passed`` (0) or
``failed`` (anything else). Nothing is recorded when the command times out,
cannot start, or runs no tests.

Results are stored in the ``verification-results@1`` format that the status
screen reads with ``--results``. The file is not locked while tests run. Just
before saving, one process at a time re-reads the latest plan and results,
validates them, merges the new entry (replacing only the same task and check)
and replaces the file in one step. If the latest file cannot be used or the
save fails, the existing file is left as it was.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Mapping

from ..orca.dispatch import AUTH_ENV_NAMES
from ..orca.runner import CommandLaunchError, CommandResult, CommandRunner, CommandTimeout, SubprocessRunner
from .contract import (
    RESULTS_SCHEMA,
    Outcome,
    PlanFormatError,
    PlanIssue,
    TaskPlan,
    VerificationResult,
    parse_plan,
    parse_results,
    validate,
)

DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
LOCK_POLL_SECONDS = 0.05
REPLACE_ATTEMPTS = 5  # a reader holding the file open can briefly block os.replace on Windows
# Removed from the child environment: model credentials and the Orca terminal binding.
STRIPPED_ENV_NAMES = frozenset(
    {name for names in AUTH_ENV_NAMES.values() for name in names} | {"ORCA_TERMINAL_HANDLE"}
)


@dataclass(frozen=True)
class CheckCommand:
    check_id: str
    label: str
    python_args: tuple[str, ...]  # appended to the current Python interpreter

    def argv(self, python: str) -> list[str]:
        return [python, *self.python_args]

    @property
    def display(self) -> str:
        return " ".join(("python", *self.python_args))


SUPPORTED_CHECKS: dict[str, CheckCommand] = {
    "unit-tests": CheckCommand(
        "unit-tests",
        "저장소 단위 테스트",
        ("-m", "unittest", "discover", "-s", "tests"),
    ),
}


class VerificationSetupError(Exception):
    """Refused before anything ran; no file was changed."""

    def __init__(self, message: str, issues: tuple[PlanIssue, ...] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.issues = issues


class VerificationNotCompleted(Exception):
    """No outcome to record (timeout, launch failure, no tests ran); no file was changed."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class VerificationNotRecorded(VerificationNotCompleted):
    """The command finished but its outcome could not be saved; the file was left as it was."""

    def __init__(self, message: str, result: VerificationResult, issues: tuple[PlanIssue, ...] = ()) -> None:
        super().__init__(message)
        self.result = result
        self.issues = issues


@dataclass(frozen=True)
class VerificationRun:
    result: VerificationResult
    command: CheckCommand
    output: CommandResult
    summary: str
    kept: int  # entries for other task/check pairs that were preserved
    replaced: bool  # an earlier entry for the same pair was replaced


# --- files -------------------------------------------------------------------


def _read_json(path: Path, what: str) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise VerificationSetupError(f"{what} 파일을 읽을 수 없음 ({path.name})") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise VerificationSetupError(f"{what} 파일의 JSON 형식 오류 ({path.name} {exc.lineno}행)") from None


def load_plan(path: Path) -> TaskPlan:
    try:
        return parse_plan(_read_json(path, "작업 정의"))
    except PlanFormatError as exc:
        raise VerificationSetupError(f"작업 정의 형식 오류 ({path.name}): {exc}") from None


def load_results(path: Path) -> tuple[VerificationResult, ...]:
    """A missing file means no results yet; any other problem refuses to overwrite it."""

    if not path.exists():
        return ()
    try:
        return parse_results(_read_json(path, "검증 결과"))
    except PlanFormatError as exc:
        raise VerificationSetupError(f"검증 결과 형식 오류 ({path.name}): {exc}") from None


def results_document(results: tuple[VerificationResult, ...]) -> dict:
    entries = []
    for r in results:
        entry: dict[str, object] = {"task": r.task_id, "check": r.check_id, "outcome": r.outcome.value}
        if r.exit_code is not None:
            entry["exit_code"] = r.exit_code
        if r.evidence is not None:
            entry["evidence"] = r.evidence
        entries.append(entry)
    return {"schema": RESULTS_SCHEMA, "results": entries}


def write_results(path: Path, results: tuple[VerificationResult, ...]) -> None:
    """Replaces the file in one step so a reader never sees a half-written file.

    On any failure the temporary file is removed and the original stays as it was.
    """

    text = json.dumps(results_document(results), ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.1)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def lock_path(results_path: Path) -> Path:
    return results_path.with_name(results_path.name + ".lock")


class LockTimeout(Exception):
    pass


@contextmanager
def results_lock(results_path: Path, timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Exclusive lock shared by every process saving to ``results_path``.

    Uses an OS lock on a companion ``<name>.lock`` file, so a crashed holder
    releases it automatically. The companion file is left in place.
    """

    fd = os.open(lock_path(results_path), os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + timeout
    try:
        if os.name == "nt":
            import msvcrt

            def try_lock() -> bool:
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    return True
                except OSError:
                    return False

            def unlock() -> None:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def try_lock() -> bool:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return True
                except OSError:
                    return False

            def unlock() -> None:
                fcntl.flock(fd, fcntl.LOCK_UN)

        while not try_lock():
            if time.monotonic() >= deadline:
                raise LockTimeout
            time.sleep(LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            unlock()
    finally:
        os.close(fd)


def merge_results(
    existing: tuple[VerificationResult, ...], new: VerificationResult
) -> tuple[tuple[VerificationResult, ...], bool]:
    key = (new.task_id, new.check_id)
    merged, replaced = [], False
    for r in existing:
        if (r.task_id, r.check_id) == key:
            if not replaced:
                merged.append(new)
                replaced = True
            continue
        merged.append(r)
    if not replaced:
        merged.append(new)
    return tuple(merged), replaced


def save_result(
    plan_path: Path,
    results_path: Path,
    result: VerificationResult,
    *,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> tuple[int, bool]:
    """Read latest, validate, merge and replace, one process at a time.

    Returns (entries kept for other pairs, whether the same pair was replaced).
    Raises :class:`VerificationNotRecorded` and leaves the file unchanged otherwise.
    """

    name = results_path.name
    try:
        with results_lock(results_path, lock_timeout):
            try:
                plan = load_plan(plan_path)
                latest = load_results(results_path)
            except VerificationSetupError as exc:
                raise VerificationNotRecorded(f"저장 직전 확인 실패: {exc.message}", result) from None
            issues = validate(plan, latest)
            if issues:
                raise VerificationNotRecorded(f"최신 검증 결과에 오류가 있어 덮어쓰지 않음 ({name})", result, issues)
            merged, replaced = merge_results(latest, result)
            issues = validate(plan, merged)
            if issues:
                raise VerificationNotRecorded(f"최신 작업 정의와 결과가 맞지 않음 ({plan_path.name})", result, issues)
            try:
                write_results(results_path, merged)
            except OSError as exc:
                raise VerificationNotRecorded(
                    f"결과 파일을 저장하지 못함 ({name}: {type(exc).__name__})", result
                ) from None
            return len(merged) - 1, replaced
    except LockTimeout:
        raise VerificationNotRecorded(
            f"다른 저장이 {lock_timeout:g}초 안에 끝나지 않아 저장하지 못함 ({lock_path(results_path).name})", result
        ) from None
    except OSError as exc:  # the lock file itself could not be opened
        raise VerificationNotRecorded(f"잠금 파일을 열 수 없음 ({type(exc).__name__})", result) from None


# --- running -----------------------------------------------------------------


def child_environment(repo: Path, environ: Mapping[str, str]) -> dict[str, str]:
    env = {k: v for k, v in environ.items() if k not in STRIPPED_ENV_NAMES}
    env["PYTHONPATH"] = os.pathsep.join([str(repo / "src"), str(repo / "tests")])
    return env


_RAN = re.compile(r"^Ran (\d+) tests? in ")


@dataclass(frozen=True)
class TestSummary:
    tests_run: int | None  # None when the output has no "Ran N tests" line
    no_tests: bool  # unittest said NO TESTS RAN
    text: str


def summarize(output: CommandResult) -> TestSummary:
    lines = [line.strip() for line in (output.stderr + "\n" + output.stdout).splitlines() if line.strip()]
    ran_line = next((line for line in lines if _RAN.match(line)), None)
    tests_run = int(_RAN.match(ran_line).group(1)) if ran_line else None
    final = next(
        (line for line in reversed(lines) if line.startswith(("OK", "FAILED", "NO TESTS RAN"))), None
    )
    parts = [p for p in (ran_line.split(" in ")[0] if ran_line else None, final) if p]
    return TestSummary(
        tests_run,
        bool(final and final.startswith("NO TESTS RAN")),
        " · ".join(parts) if parts else "테스트 요약 없음",
    )


def prepare(plan_path: Path, results_path: Path, task_id: str, check_id: str, repo: Path):
    plan = load_plan(plan_path)
    issues = validate(plan)
    if issues:
        raise VerificationSetupError("작업 정의에 오류가 있어 검증하지 않음", issues)
    task = next((t for t in plan.tasks if t.task_id == task_id), None)
    if task is None:
        raise VerificationSetupError(f"작업 {task_id}이(가) 작업 정의에 없음")
    if check_id not in {c.check_id for c in task.checks}:
        raise VerificationSetupError(f"작업 {task_id}에 검증 조건 {check_id}이(가) 없음")
    command = SUPPORTED_CHECKS.get(check_id)
    if command is None:
        supported = ", ".join(SUPPORTED_CHECKS)
        raise VerificationSetupError(f"검증 조건 {check_id}은(는) 실행을 지원하지 않음 (지원: {supported})")
    if not results_path.parent.is_dir():
        raise VerificationSetupError(f"검증 결과를 저장할 폴더가 없음 ({results_path.name})")
    existing = load_results(results_path)  # early check only; the save re-reads the latest file
    issues = validate(plan, existing)
    if issues:
        raise VerificationSetupError(f"기존 검증 결과에 오류가 있어 덮어쓰지 않음 ({results_path.name})", issues)
    if not (repo / "tests").is_dir():
        raise VerificationSetupError("저장소 루트에 tests 폴더가 없음 (--repo 확인)")
    return plan, command


def verify(
    plan_path: str | Path,
    results_path: str | Path,
    task_id: str,
    check_id: str,
    *,
    repo: str | Path = ".",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    environ: Mapping[str, str] | None = None,
    runner_factory: Callable[[dict[str, str]], CommandRunner] = SubprocessRunner,
    python: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> VerificationRun:
    plan_path, results_path, repo = Path(plan_path), Path(results_path), Path(repo).resolve()
    _, command = prepare(plan_path, results_path, task_id, check_id, repo)

    python = python if python is not None else sys.executable
    if not python:
        raise VerificationNotCompleted("Python 실행 파일 경로를 알 수 없어 실행하지 못함")
    runner = runner_factory(child_environment(repo, os.environ if environ is None else environ))
    started = now()
    try:  # no lock is held while the tests run
        output = runner.run(command.argv(python), timeout=timeout, cwd=str(repo))
    except CommandTimeout:
        raise VerificationNotCompleted(f"제한 시간 {timeout:g}초 안에 끝나지 않음") from None
    except CommandLaunchError as exc:
        raise VerificationNotCompleted(f"실행하지 못함 ({exc})") from None

    summary = summarize(output)
    if summary.no_tests or summary.tests_run == 0:
        raise VerificationNotCompleted(f"실행된 테스트가 없음 (tests 폴더 확인, 종료 코드 {output.exit_code})")
    if summary.tests_run is None and output.exit_code == 0:
        raise VerificationNotCompleted("실행된 테스트 수를 확인할 수 없어 통과로 기록하지 않음")

    outcome = Outcome.PASSED if output.exit_code == 0 else Outcome.FAILED
    stamp = started.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = VerificationResult(
        task_id, check_id, outcome, output.exit_code, f"{command.display} · {stamp} · {summary.text}"
    )
    kept, replaced = save_result(plan_path, results_path, result, lock_timeout=lock_timeout)
    return VerificationRun(result, command, output, summary.text, kept, replaced)
