"""Turns a Snapshot into plain text lines (no terminal control codes)."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from ..orca.dispatch import Provider, Reason
from ..tasks import BoardState, IssueCode, TaskBoard, TaskState, WaitKind
from . import snapshot as s

UNKNOWN = "미확인"
UNREADABLE = "조회 불가"
TIMED_OUT = "조회 시간 초과"
NO_RUN = "없음 (바인딩된 Run 없음)"
LABEL_WIDTH = 12

PROVIDER_NAMES = {Provider.CLAUDE: "Claude", Provider.CODEX: "Codex"}

REASON_LABELS: dict[Reason, str] = {
    Reason.REAL_DISPATCH_DISABLED: "실제 작업 전송이 기본값으로 꺼져 있음",
    Reason.EXECUTION_APPROVAL_MISSING: "Worker 실행에 대한 사용자 승인 없음",
    Reason.EXPERIMENT_4A_NOT_PASSED: "Experiment 4-A 통과 전",
    Reason.SUBSCRIPTION_AUTH_UNVERIFIED: "Worker 구독 인증 미확인",
    Reason.BILLING_PATH_UNVERIFIED: "구독 포함량만 쓰는 과금 경로 미확인",
    Reason.EXTRA_USAGE_NOT_CONFIRMED_OFF: "추가 사용량·크레딧 사용 꺼짐 미확인",
    Reason.AUTO_RECHARGE_NOT_CONFIRMED_OFF: "자동 충전 꺼짐 미확인",
    Reason.API_KEY_BILLING_FORBIDDEN: "API 키 과금 경로는 허용하지 않음",
    Reason.API_KEY_ENVIRONMENT_PRESENT: "대체 인증 환경변수 설정됨",
    Reason.AUTOMATIC_RETRY_FORBIDDEN: "자동 재시도는 허용하지 않음",
    Reason.COORDINATOR_BINDING_MISSING: "Coordinator 바인딩 없음",
    Reason.RUNTIME_UNAVAILABLE: "Runtime 연결 안 됨",
    Reason.RUNTIME_STATUS_INVALID: "Runtime 상태 조회 불가",
    Reason.ORCA_VERSION_UNPINNED: "검증되지 않은 Orca 버전",
    Reason.USAGE_LIMIT_WAITING_FOR_RESET: "사용 한도 도달, 리셋 대기",
    Reason.USAGE_RECHECK_REQUIRED: "리셋 이후 사용량 재확인 필요",
    Reason.WORKER_DISPATCH_CONTRACT_UNVERIFIED: "worker-start 계약 미검증 (현재 빌드는 전송하지 않음)",
}

HINT_CLI_MISSING = "안내: Orca CLI를 찾지 못했습니다. ORCA_CLI_COMMAND에 경로를 지정하세요."
HINT_RUNTIME_DOWN = "안내: Orca Runtime에 연결되지 않았습니다. Orca 앱을 연 뒤 r을 누르세요."
HINT_UNBOUND = "안내: Coordinator 바인딩이 없습니다. Orca 터미널 탭에서 실행하세요."
HINT_BINDING_INVALID = "안내: 터미널 바인딩이 유효하지 않습니다. 새 Orca 터미널 탭에서 실행하세요."
HINT_UNREADABLE = "안내: 응답을 해석할 수 없습니다. Orca 버전과 CLI 경로를 확인하세요."
HINT_TIMED_OUT = "안내: Orca CLI가 제한 시간 안에 응답하지 않았습니다. r로 다시 조회하세요."

FOOTER = "r 새로고침 · q 종료"

TASKS_NOT_CONFIGURED = "지정 안 함 (--tasks로 작업 정의 파일 지정)"
MAX_ISSUE_LINES = 4

TASK_STATE_LABELS: dict[TaskState, str] = {
    TaskState.VERIFIED: "완료",
    TaskState.FAILED: "검증 실패",
    TaskState.READY: "준비",
    TaskState.WAITING: "대기",
}
STATE_COLUMN = max(len(label) * 2 for label in TASK_STATE_LABELS.values()) + 1  # Hangul is 2 wide

ISSUE_LABELS: dict[IssueCode, str] = {
    IssueCode.DUPLICATE_REQUIREMENT_ID: "중복 요구사항 ID: {0}",
    IssueCode.DUPLICATE_TASK_ID: "중복 작업 ID: {0}",
    IssueCode.DUPLICATE_CHECK_ID: "{0}의 중복 검증 조건 ID: {1}",
    IssueCode.TASK_WITHOUT_REQUIREMENT: "{0}에 연결된 요구사항 없음",
    IssueCode.UNKNOWN_REQUIREMENT: "{0}의 요구사항 {1}이(가) 정의되지 않음",
    IssueCode.TASK_WITHOUT_CHECKS: "{0}에 검증 조건 없음",
    IssueCode.UNKNOWN_DEPENDENCY: "{0}의 선행 작업 {1}이(가) 정의되지 않음",
    IssueCode.DEPENDENCY_CYCLE: "순환 의존성: {cycle}",
    IssueCode.RESULT_UNKNOWN_TASK: "검증 결과의 작업 {0}이(가) 정의되지 않음",
    IssueCode.RESULT_UNKNOWN_CHECK: "검증 결과의 조건 {0}/{1}이(가) 정의되지 않음",
    IssueCode.DUPLICATE_RESULT: "{0}/{1} 검증 결과가 중복됨",
    IssueCode.RESULT_EXIT_CODE_MISMATCH: "{0}/{1}: 통과로 기록됐지만 종료 코드 {2}",
}


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def fit(text: str, width: int) -> str:
    if display_width(text) <= width:
        return text
    out, used = [], 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + w > width - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def _row(label: str, value: str) -> str:
    return label + " " * max(1, LABEL_WIDTH - display_width(label)) + value


def runtime_text(view: s.RuntimeView) -> str:
    if view.health == s.CONNECTED and view.status is not None:
        version = view.status.app_version or UNKNOWN
        pinned = "검증 버전" if view.status.version_pinned else "미검증 버전"
        return f"연결됨 · Orca {version} ({pinned})"
    if view.health == s.DISCONNECTED:
        state = view.status.runtime_state if view.status and view.status.runtime_state else None
        return "연결 안 됨" + (f" · 상태 {state}" if state else "") + f" · 버전 {UNKNOWN}"
    if view.health == s.CLI_MISSING:
        return "Orca CLI 없음"
    if view.health == s.TIMED_OUT:
        return TIMED_OUT
    return UNREADABLE


def run_text(view: s.RunView) -> str:
    if view.state == s.RUN_OK and view.run is not None:
        objective = f" · {view.run.objective}" if view.run.objective else ""
        return f"{view.run.run_id}{objective}"
    if view.state == s.RUN_NONE:
        return NO_RUN
    if view.state == s.RUN_SKIPPED_UNBOUND:
        return "조회 안 함 (바인딩 없음)"
    if view.state == s.RUN_SKIPPED_RUNTIME:
        return "조회 안 함 (Runtime 연결 안 됨)"
    if view.state == s.RUN_TIMED_OUT:
        return TIMED_OUT
    if view.state == s.RUN_BINDING_INVALID:
        return f"{UNREADABLE} · 바인딩 무효" + (f" ({view.error_code})" if view.error_code else "")
    return UNREADABLE + (f" ({view.error_code})" if view.error_code else "")


def hint(snap: s.Snapshot) -> str | None:
    """At most one guidance line, most fundamental problem first."""
    if snap.runtime.health == s.CLI_MISSING:
        return HINT_CLI_MISSING
    if snap.runtime.health == s.DISCONNECTED:
        return HINT_RUNTIME_DOWN
    if not snap.bound:
        return HINT_UNBOUND
    if snap.run.state == s.RUN_BINDING_INVALID:
        return HINT_BINDING_INVALID
    if snap.runtime.health == s.TIMED_OUT or snap.run.state == s.RUN_TIMED_OUT:
        return HINT_TIMED_OUT
    if snap.runtime.health == s.UNREADABLE or snap.run.state == s.RUN_UNREADABLE:
        return HINT_UNREADABLE
    return None


def _reason_label(snap: s.Snapshot, code: Reason, provider: Provider | None) -> str:
    label = REASON_LABELS[code]
    if code is Reason.EXPERIMENT_4A_NOT_PASSED:
        label += f" (현재 {snap.experiment_4a_status})"
    if code is Reason.API_KEY_ENVIRONMENT_PRESENT and provider is not None:
        names = snap.auth_env_present.get(provider, ())
        if names:
            label += f": {', '.join(names)}"
    return label


def dispatch_lines(snap: s.Snapshot) -> list[str]:
    providers = list(snap.decisions)
    codes_by_provider = {p: snap.decisions[p].reason_codes for p in providers}
    ordered: list[Reason] = []
    for p in providers:
        for code in codes_by_provider[p]:
            if code not in ordered:
                ordered.append(code)
    waiting = [p for p in providers if snap.decisions[p].outcome.value == "WAITING_FOR_RESET"]
    status = "보류 · 현재 빌드는 Worker에 작업을 보내지 않음"
    if waiting:
        status += " · 리셋 대기: " + ", ".join(PROVIDER_NAMES[p] for p in waiting)
    lines = [_row("작업 전송", status), "  보류 이유"]
    for code in ordered:
        having = [p for p in providers if code in codes_by_provider[p]]
        if len(having) == len(providers):
            lines.append(f"  · {_reason_label(snap, code, None)}")
        else:
            for p in having:
                lines.append(f"  · {PROVIDER_NAMES[p]}: {_reason_label(snap, code, p)}")
    return lines


def issue_text(issue) -> str:
    return ISSUE_LABELS[issue.code].format(*issue.subject, cycle=" → ".join(issue.subject))


def wait_reason_text(reason) -> str:
    if reason.kind is WaitKind.PREREQUISITE_FAILED:
        return f"선행 작업 {reason.task_id} 검증 실패"
    if reason.passed == 0:
        return f"선행 작업 {reason.task_id} 검증 결과 없음"
    return f"선행 작업 {reason.task_id} 검증 미완료 ({reason.passed}/{reason.total} 통과)"


def _task_detail(tp) -> str:
    deps = ", ".join(tp.task.dependencies)
    if tp.state is TaskState.VERIFIED:
        return f"검증 {tp.passed}/{tp.total} 통과"
    if tp.state is TaskState.FAILED:
        return f"검증 실패: {', '.join(tp.failed_checks)}"
    if tp.state is TaskState.READY:
        text = f"선행 {deps} 완료" if deps else "선행 작업 없음"
        return text + (f" · 검증 {tp.passed}/{tp.total} 통과" if tp.passed else "")
    return f"선행 {deps}"


def task_lines(board: TaskBoard) -> list[str]:
    if board.state is BoardState.NOT_CONFIGURED:
        return [_row("작업 목록", TASKS_NOT_CONFIGURED)]
    if board.state is BoardState.UNREADABLE:
        return [_row("작업 목록", f"{UNREADABLE} · {board.error}"), f"  출처 {board.label}"]
    if board.state is BoardState.INVALID:
        lines = [_row("작업 목록", f"오류 {len(board.issues)}건 · 상태를 판정하지 않음"),
                 f"  출처 {board.label}"]
        shown = board.issues[:MAX_ISSUE_LINES]
        lines += [f"  · {issue_text(issue)}" for issue in shown]
        if len(board.issues) > len(shown):
            lines.append(f"  · … 외 {len(board.issues) - len(shown)}건")
        return lines

    progress = board.progress
    counts = [f"{TASK_STATE_LABELS[st]} {progress.count(st)}" for st in TaskState if progress.count(st)]
    lines = [_row("작업 목록", " · ".join([f"{len(progress.tasks)}개", *counts, board.label]))]
    id_width = max((display_width(tp.task.task_id) for tp in progress.tasks), default=0)
    for tp in progress.tasks:
        task_id = tp.task.task_id + " " * (id_width - display_width(tp.task.task_id))
        state = TASK_STATE_LABELS[tp.state]
        state += " " * (STATE_COLUMN - display_width(state))
        reqs = ", ".join(tp.task.requirement_ids)
        lines.append(f"  {task_id}  {state}{tp.task.title} · {reqs} · {_task_detail(tp)}")
        for reason in tp.waiting_on:
            lines.append(" " * (4 + id_width + STATE_COLUMN) + f"대기 사유: {wait_reason_text(reason)}")
    reqs = []
    for rp in progress.requirements:
        rid = rp.requirement.requirement_id
        if rp.satisfied:
            reqs.append(f"{rid} 충족")
        elif not rp.task_ids:
            reqs.append(f"{rid} 미충족 (연결된 작업 없음)")
        else:
            reqs.append(f"{rid} 미충족 ({', '.join(rp.open_task_ids)} 미완료)")
    lines.append(_row("요구사항", " · ".join(reqs) if reqs else "정의 없음"))
    return lines


HEADER_LINES = 3  # title, query time, rule


def render_parts(snap: s.Snapshot, width: int = 80) -> tuple[list[str], list[str]]:
    """(fixed header, scrollable body). The footer key line is added by the caller."""

    width = max(40, width)
    title = "Orca Orchestrator · 읽기 전용 상태"
    if snap.demo:
        title += f" · 데모 ({snap.demo})"
    taken = snap.taken_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    rule = "─" * min(width, 72)
    header = [
        title,
        f"조회 시각 {taken}" + (" · 실제 CLI를 호출하지 않음" if snap.demo else ""),
        rule,
    ]
    body = [
        _row("Runtime", runtime_text(snap.runtime)),
        _row("바인딩", "Coordinator 터미널 바인딩 있음" if snap.bound else "없음"),
        _row("현재 Run", run_text(snap.run)),
        *dispatch_lines(snap),
        _row("사용량", " · ".join(f"{PROVIDER_NAMES[p]} {UNKNOWN}" for p in Provider)),
        *task_lines(snap.tasks),
        rule,
    ]
    note = hint(snap)
    if note:
        body.append(note)
    return [fit(line, width) for line in header], [fit(line, width) for line in body]


def render(snap: s.Snapshot, width: int = 80) -> list[str]:
    """Every line at once (``--once`` and non-interactive output)."""

    header, body = render_parts(snap, width)
    return header + body + [fit(FOOTER, max(40, width))]


@dataclass(frozen=True)
class Frame:
    lines: list[str]
    offset: int  # first body line shown
    max_offset: int  # 0 when the whole body fits
    body_rows: int  # body lines visible at once


TOO_LOW = "창이 낮아 {n}줄을 표시하지 못함"


def scroll_footer(start: int, end: int, total: int) -> str:
    """Key line while the body is clipped; ``start``/``end`` are 0-based, end exclusive."""

    above, below = start, total - end
    more = []
    if above:
        more.append(f"위 {above}줄")
    if below:
        more.append(f"아래 {below}줄")
    return f"{FOOTER} · ↑↓ 이동 · {start + 1}–{end}/{total}줄 · " + "·".join(more) + " 더 있음"


def layout(snap: s.Snapshot, width: int, height: int, offset: int = 0) -> Frame:
    """Lines for a ``width`` x ``height`` terminal.

    The title (first line) and the key line (last line) are always kept. When the
    body does not fit, a window of it is shown and the key line says how much is
    above and below. Never returns more than ``max(height, 2)`` lines.
    """

    header, body = render_parts(snap, width)
    footer = fit(FOOTER, max(40, width))
    if len(header) + len(body) + 1 <= height:
        return Frame(header + body + [footer], 0, 0, len(body))
    if height < len(header) + 2:
        header = header[:1]  # very low terminal: keep only the title
    rows = max(0, height - len(header) - 1)
    if rows == 0:
        status = fit(f"{FOOTER} · {TOO_LOW.format(n=len(body))}", max(40, width))
        return Frame(header + [status], 0, 0, 0)
    max_offset = max(0, len(body) - rows)
    offset = min(max(0, offset), max_offset)
    window = body[offset:offset + rows]
    status = fit(scroll_footer(offset, offset + len(window), len(body)), max(40, width))
    return Frame(header + window + [status], offset, max_offset, rows)
