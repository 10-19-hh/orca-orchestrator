"""Names of the files the tool keeps next to a results file.

``results.json`` has companions ``results.json.lock`` (save lock),
``results.flow.json`` (last flow run) and ``results.approval.json`` (the
person's review decision). None of them are code: they never expire a result
and are left out of the change review.
"""

from __future__ import annotations

from pathlib import Path


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


def tool_files(plan_path: Path | None, results_path: Path | None) -> tuple[Path, ...]:
    """The plan, the results file and its companions: inputs and outputs of the tool, not code."""

    files = [Path(plan_path)] if plan_path is not None else []
    if results_path is not None:
        results_path = Path(results_path)
        files += [results_path, lock_path(results_path), flow_status_path(results_path), approval_path(results_path)]
    return tuple(files)
