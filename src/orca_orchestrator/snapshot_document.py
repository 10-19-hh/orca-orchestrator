"""``orca-orchestrator/snapshot@1``: one collected :class:`~tui.snapshot.Snapshot` as a JSON document.

The document is the read-only contract a separate GUI consumes instead of
re-implementing the judgments. :func:`snapshot_document` is a projection:

* it is pure: no file, process, network or clock access, and the snapshot is
  not changed; the same snapshot gives the same document,
* it returns JSON types only (``dict``, ``list``, ``str``, ``int``, ``bool``,
  ``None``); enums become their values, times ISO 8601 UTC strings,
* every field is chosen here by name (an allowlist). Environment values,
  variable names, process IDs of this host, absolute paths, error messages
  from the Orca CLI or Git, check output and plan command text are left out,
* it adds no judgment: states, reasons and requests come from ``tasks.*``,
  ``orca.dispatch`` and ``console.yard`` exactly as the screens use them.

Each section says where its facts come from (``source``) and whether they are
reported as observed or computed (``judgment``). ``source`` is ``live`` (Orca
CLI in this collection), ``recorded`` (plan, results, flow, approval and
handoff files, read-only Git), ``demo`` (bundled fixtures; nothing ran) or
``none``. ``availability`` is ``available``, ``not_configured``,
``unavailable`` (asked, could not be determined), ``unknown`` or
``not_implemented`` (this build cannot observe it). A recorded fact can still
be stale: staleness is the domain state itself (``CheckState.STALE``,
``ApprovalState.REAPPROVE``), never a source.

Not in v1, because only one console process knows it or because it is
presentation: the console's local run (``local_run``) and action progress,
which requests a person has read, pet positions, frames, camera and
selection, and live Worker activity (not observed by this build). The
document lists them under ``not_included``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .console.sprites import PET_NAMES, WORKER_ACTIONS
from .console.yard import MOOD_MARKS, YardInput, derive_requests, yard_model
from .orca.dispatch import Provider
from .tasks import BoardState, TaskBoard
from .tasks.flow import STALE_TEXT
from .tasks.handoff import EXECUTOR_LOCAL, WORKER_RUN_PREFIX
from .tasks.worker_runs import EXECUTOR_WORKER, dispatch_of
from .tui import snapshot as s
from .tui.render import REASON_LABELS, TASK_STATE_LABELS

SNAPSHOT_SCHEMA = "orca-orchestrator/snapshot@1"

LIVE, RECORDED, DEMO, NONE = "live", "recorded", "demo", "none"
OBSERVED, INFERRED = "observed", "inferred"
AVAILABLE, NOT_CONFIGURED, UNAVAILABLE, UNKNOWN, NOT_IMPLEMENTED = (
    "available", "not_configured", "unavailable", "unknown", "not_implemented")

NOT_INCLUDED = (
    {"field": "local_run", "why": "이 화면에서 시작한 로컬 검증은 그 콘솔 프로세스의 메모리에만 있음"},
    {"field": "action_progress", "why": "검증·흐름 진행 이벤트는 실행한 콘솔에만 전달됨"},
    {"field": "request_read_state", "why": "읽음 표시는 화면마다 다름 (화면 쪽 상태)"},
    {"field": "mascot_pose", "why": "펫 좌표·프레임·카메라·선택은 화면 쪽 표현 상태"},
    {"field": "live_worker_activity", "why": "이 빌드는 Worker 작업을 실시간으로 관측하지 않음"},
)


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _value(member) -> str | None:
    return None if member is None else member.value


def _section(source: str, judgment: str, availability: str = AVAILABLE, **fields: Any) -> dict[str, Any]:
    return {"source": source, "judgment": judgment, "availability": availability, **fields}


# --- Orca -------------------------------------------------------------------------------------


def _runtime(snap: s.Snapshot, source: str) -> dict[str, Any]:
    view = snap.runtime
    status = None
    if view.status is not None:
        st = view.status
        status = {
            "app_running": st.app_running,
            "runtime_state": st.runtime_state,
            "runtime_reachable": st.runtime_reachable,
            "connection_state": st.connection_state,
            "app_version": st.app_version,
            "version_verified": st.version_pinned,
            "connected": st.connected,
        }
    error = None if view.error is None else {"category": view.error.category, "code": view.error.code}
    availability = AVAILABLE if view.status is not None else UNAVAILABLE
    return _section(source, OBSERVED, availability, health=view.health, status=status, error=error)


def _run(snap: s.Snapshot, source: str) -> dict[str, Any]:
    view = snap.run
    asked = view.state not in (s.RUN_SKIPPED_UNBOUND, s.RUN_SKIPPED_RUNTIME)
    if not asked:
        availability = UNKNOWN  # not asked: no binding or no Runtime
    elif view.state in (s.RUN_OK, s.RUN_NONE):
        availability = AVAILABLE
    else:
        availability = UNAVAILABLE
    return _section(source, OBSERVED, availability, state=view.state,
                    run_id=view.run.run_id if view.run else None,
                    objective=view.run.objective if view.run else None,
                    error_code=view.error_code)


def _dispatch(snap: s.Snapshot, source: str) -> dict[str, Any]:
    providers = []
    for provider in Provider:
        decision = snap.decisions.get(provider)
        if decision is None:
            continue
        providers.append({
            "provider": provider.value,
            "outcome": decision.outcome.value,
            # codes and the screen's labels only: a reason's detail can name environment variables or carry CLI text
            "reasons": [{"code": r.code.value, "label": REASON_LABELS[r.code]} for r in decision.reasons],
            "resume_after": _iso(decision.resume_after),
        })
    return _section(source, INFERRED, AVAILABLE if providers else UNKNOWN,
                    sends_work=False,  # this build has no path that sends work (dispatch.py, worker_runs.py)
                    experiment_4a_status=snap.experiment_4a_status,
                    providers=providers)


# --- tasks ------------------------------------------------------------------------------------


def _result(r) -> dict[str, Any] | None:
    if r is None:
        return None
    return {"outcome": r.outcome.value, "exit_code": r.exit_code, "check_digest": r.check_digest,
            "source_digest": r.source_digest, "git_head": r.git_head, "git_dirty": r.git_dirty}


def _check(cp) -> dict[str, Any]:
    return {
        "check_id": cp.check.check_id,
        "description": cp.check.description,
        "state": cp.state.value,
        "stale_reason": _value(cp.stale_reason),
        "stale_text": STALE_TEXT[cp.stale_reason] if cp.stale_reason is not None else None,
        "result": _result(cp.result),
    }


def _task(tp) -> dict[str, Any]:
    t = tp.task
    return {
        "task_id": t.task_id,
        "title": t.title,
        "requirement_ids": list(t.requirement_ids),
        "dependencies": list(t.dependencies),
        "paths": list(t.paths),
        "protected_paths": list(t.protected_paths),
        "state": tp.state.value,
        "state_label": TASK_STATE_LABELS[tp.state],
        "passed": tp.passed,
        "total": tp.total,
        "failed_checks": list(tp.failed_checks),
        "waiting_on": [{"kind": w.kind.value, "task_id": w.task_id, "passed": w.passed, "total": w.total}
                       for w in tp.waiting_on],
        "checks": [_check(cp) for cp in tp.checks],
    }


def _board_availability(board: TaskBoard) -> str:
    return {BoardState.OK: AVAILABLE, BoardState.NOT_CONFIGURED: NOT_CONFIGURED}.get(board.state, UNAVAILABLE)


def _tasks(board: TaskBoard, source: str) -> dict[str, Any]:
    progress = board.progress
    view = board.view
    return _section(
        source, INFERRED, _board_availability(board),
        board_state=board.state.value,
        label=board.label,
        error=board.error,  # file names only (tasks.source never shows a directory)
        issues=[{"code": i.code.value, "subject": list(i.subject)} for i in board.issues],
        tasks=[_task(tp) for tp in progress.tasks] if progress else [],
        requirements=[{"requirement_id": rp.requirement.requirement_id, "title": rp.requirement.title,
                       "task_ids": list(rp.task_ids), "open_task_ids": list(rp.open_task_ids),
                       "satisfied": rp.satisfied} for rp in progress.requirements] if progress else [],
        next=({"task_id": view.next.task_id, "check_ids": list(view.next.check_ids), "reason": view.next.reason}
              if view is not None and view.next is not None else None),
        blockers=[{"task_id": b.task_id, "reason": b.reason, "hint": b.hint} for b in view.blockers] if view else [],
    )


def _flow(board: TaskBoard, source: str) -> dict[str, Any]:
    status = board.flow
    doc = None
    if status is not None:
        last = status.last
        doc = {
            "state": status.state,
            "updated_at": status.updated_at,
            "current": ({"task_id": status.current[0], "check_id": status.current[1],
                         "started_at": status.current[2]} if status.current else None),
            "last": ({"kind": last.kind.value, "message": last.message, "at": last.at,
                      "task_id": last.task_id, "check_id": last.check_id} if last else None),
            "verified": list(status.verified),
        }
    if board.state is BoardState.NOT_CONFIGURED:
        availability = NOT_CONFIGURED
    elif board.flow_error:
        availability = UNAVAILABLE
    else:
        availability = AVAILABLE  # no file yet: available and empty (status null)
    return _section(source, OBSERVED, availability, status=doc, error=board.flow_error)


def _change(c) -> dict[str, str]:
    return {"path": c.path, "status": c.status.value}


def _review(board: TaskBoard, source: str) -> dict[str, Any]:
    review = board.review
    if review is None:
        why = NOT_CONFIGURED if board.state in (BoardState.OK, BoardState.NOT_CONFIGURED) else UNAVAILABLE
        return _section(source, INFERRED, why, state=None)
    code = review.code
    record = review.record
    current, recorded = review.current_results
    return _section(
        source, INFERRED,
        state=review.state.value,
        approvable=review.approvable,
        plan_digest=review.plan_digest,
        code={
            "source_digest": code.source.digest,
            "source_files": code.source.files,
            "source_determined": code.source.digest is not None,  # an error's text is left out (Git/OS output)
            "git_head": code.basis.head,
            "git_dirty": code.basis.dirty,
        },
        changes={
            "base": code.changes.base,
            "base_sha": code.changes.base_sha,
            "digest": code.changes.digest,
            "determined": code.changes.ok,
            "files": [_change(c) for c in code.changes.changes],
        },
        tasks=[{"task_id": t.task_id, "changes": [_change(c) for c in t.changes],
                "protected": [_change(c) for c in t.protected]} for t in review.tasks],
        unlinked=[_change(c) for c in review.unlinked],
        blockers=[{"kind": b.kind.value, "subject": b.subject, "text": b.text} for b in review.blockers],
        results_current=current,
        results_recorded=recorded,
        record=({"decision": record.decision.value, "at": record.at, "note": record.note,
                 "confirmed_unlinked": list(record.confirmed_unlinked)} if record else None),
        stale_reasons=list(review.stale_reasons),
    )


# --- runs and Workers -------------------------------------------------------------------------


def _executor_kind(executor: str) -> str:
    if executor == EXECUTOR_LOCAL:
        return "local"
    if executor.startswith(EXECUTOR_WORKER + ":"):
        return "worker"
    return "other"


def _run_doc(r) -> dict[str, Any]:
    return {"check": r.check, "run_id": r.run_id, "started_at": r.started_at, "ended_at": r.ended_at,
            "end": _value(r.end), "outcome": r.outcome, "exit_code": r.exit_code,
            "observed_state": r.observed_state, "observed_at": r.observed_at}


def _attempt(a) -> dict[str, Any] | None:
    if a is None:
        return None
    return {
        "attempt_id": a.attempt_id,
        "executor": a.executor,
        "executor_kind": _executor_kind(a.executor),
        "session": a.session,
        "state": a.state.value,
        "started_at": a.started_at,
        "ended_at": a.ended_at,
        "runs": [_run_doc(r) for r in a.runs],
        "done": list(a.done),
        "remaining": list(a.remaining),
        "reason": a.reason,
        "resolved": a.resolved,
    }


def _recovery(board: TaskBoard, source: str) -> dict[str, Any]:
    resume = board.resume
    if resume is None:
        why = NOT_CONFIGURED if board.state in (BoardState.OK, BoardState.NOT_CONFIGURED) else UNAVAILABLE
        return _section(source, INFERRED, why, tasks=[], next_task=None, error=None)
    return _section(
        source, INFERRED, UNAVAILABLE if resume.error else AVAILABLE,
        next_task=resume.next_task,
        error=resume.error,
        tasks=[{"task_id": t.task_id, "state": t.state.value, "reason": t.reason, "next_action": t.next_action,
                "since": list(t.since), "attempt": _attempt(t.attempt)} for t in resume.tasks],
    )


def _workers(board: TaskBoard, source: str) -> dict[str, Any]:
    """Recorded Worker runs from the execution record; live Workers are not observed by this build."""

    recorded = []
    if board.resume is not None:
        for a in board.resume.attempts:
            if _executor_kind(a.executor) != "worker":
                continue
            for r in a.runs:
                if r.run_id is None or not r.run_id.startswith(WORKER_RUN_PREFIX):
                    continue
                recorded.append({
                    "task_id": a.task,
                    "attempt_id": a.attempt_id,
                    "agent": a.executor.split(":", 1)[1],
                    "dispatch_id": dispatch_of(r.run_id),
                    "end": _value(r.end),
                    "observed_state": r.observed_state,
                    "observed_at": r.observed_at,
                    "resolved": a.resolved,
                })
    return {
        # Not observed live: an empty list here does not mean that no Worker exists.
        "live": {"source": NONE, "judgment": OBSERVED, "availability": NOT_IMPLEMENTED, "items": []},
        "recorded": _section(source, OBSERVED, AVAILABLE if board.resume is not None else UNKNOWN,
                             items=recorded),
    }


def _mascots() -> list[dict[str, Any]]:
    # Presentation companions. A pet stands for no Worker, run or task unless a Worker for its agent is
    # observed; this build observes none, so no pet may show coding actions.
    return [{"id": pet, "name": name, "theme_agent": pet, "role": "companion", "worker": None,
             "worker_actions_allowed": False, "worker_actions": sorted(WORKER_ACTIONS)}
            for pet, name in PET_NAMES.items()]


# --- presentation -----------------------------------------------------------------------------


def _presentation(snap: s.Snapshot, source: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    model = yard_model(YardInput(board=snap.tasks, decisions=snap.decisions, demo=snap.demo is not None))
    requests = [{"rid": r.rid, "kind": r.kind.value, "label": r.label, "priority": r.priority,
                 "task_id": r.task_id, "summary": r.summary, "tab": r.tab} for r in derive_requests(snap.tasks)]
    presentation = _section(
        source, INFERRED,
        mood=model.mood.value,
        mark=MOOD_MARKS[model.mood],
        tag=model.tag,
        label=model.label,
        task_id=model.task_id,
        # The console shows "a local check runs here" only from its own run events, which are not in a snapshot.
        local_run={"availability": UNKNOWN, "value": None},
    )
    return presentation, requests


def snapshot_document(snapshot: s.Snapshot) -> dict[str, Any]:
    """The ``snapshot@1`` document for one collected snapshot. Pure and deterministic; see the module doc."""

    demo = snapshot.demo is not None
    orca = DEMO if demo else LIVE
    files = DEMO if demo else (RECORDED if snapshot.tasks.state is not BoardState.NOT_CONFIGURED else NONE)
    presentation, requests = _presentation(snapshot, files)
    board = snapshot.tasks
    return {
        "schema": SNAPSHOT_SCHEMA,
        "mode": "demo" if demo else "live",
        "demo": {"scenario": snapshot.demo, "note": "모의 상태 · 실제 CLI·실행·기록 없음"} if demo else None,
        "taken_at": _iso(snapshot.taken_at),
        "runtime": _runtime(snapshot, orca),
        "binding": _section(orca, OBSERVED, terminal_handle_present=snapshot.bound),
        "run": _run(snapshot, orca),
        "dispatch": _dispatch(snapshot, orca),
        "tasks": _tasks(board, files),
        "flow": _flow(board, files),
        "review": _review(board, files),
        "recovery": _recovery(board, files),
        "workers": _workers(board, files),
        "mascots": _mascots(),
        "presentation": presentation,
        "requests": requests,
        "not_included": [dict(item) for item in NOT_INCLUDED],
    }
