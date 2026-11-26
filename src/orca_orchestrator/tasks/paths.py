"""Names of the files the tool keeps next to a results file.

``results.json`` has companions:

* ``results.json.lock`` — held briefly while saving results or a decision,
* ``results.flow.json`` — what the last flow run did,
* ``results.approval.json`` — the person's review decision,
* ``results.handoff.json`` (+ ``.lock``) — each task attempt: what ran, how it
  ended and what to do next, for the next session,
* ``results.json.run-<8 hex>.lock`` — held by the session running a task.

None of them are code: they never expire a result and are left out of the
change review.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_RUN_LOCK = re.compile(r".+\.run-[0-9a-f]{8}\.lock")
TOOL_SUFFIXES = (".json.lock", ".flow.json", ".approval.json", ".handoff.json", ".tmp")


def is_tool_file_name(name: str) -> bool:
    return name.endswith(TOOL_SUFFIXES) or bool(_RUN_LOCK.fullmatch(name))


def _stem_name(results_path: Path, suffix: str) -> Path:
    results_path = Path(results_path)
    if results_path.suffix == ".json":
        return results_path.with_name(results_path.stem + suffix)
    return results_path.with_name(results_path.name + suffix)


def lock_path(results_path: Path) -> Path:
    results_path = Path(results_path)
    return results_path.with_name(results_path.name + ".lock")


def flow_status_path(results_path: Path) -> Path:
    return _stem_name(results_path, ".flow.json")


def approval_path(results_path: Path) -> Path:
    return _stem_name(results_path, ".approval.json")


def handoff_path(results_path: Path) -> Path:
    return _stem_name(results_path, ".handoff.json")


def run_lock_path(results_path: Path, task_id: str) -> Path:
    """Held by the one session working on ``task_id`` (task IDs may contain any character)."""

    results_path = Path(results_path)
    tag = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:8]
    return results_path.with_name(f"{results_path.name}.run-{tag}.lock")


def tool_files(plan_path: Path | None, results_path: Path | None) -> tuple[Path, ...]:
    """The plan, the results file and its companions: inputs and outputs of the tool, not code."""

    files = [Path(plan_path)] if plan_path is not None else []
    if results_path is not None:
        results_path = Path(results_path)
        handoff = handoff_path(results_path)
        files += [results_path, lock_path(results_path), flow_status_path(results_path), approval_path(results_path),
                  handoff, lock_path(handoff)]
    return tuple(files)
