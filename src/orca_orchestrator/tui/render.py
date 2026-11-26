"""Turns a Snapshot into plain text lines (no terminal control codes)."""

from __future__ import annotations

import unicodedata

from ..orca.dispatch import Provider, Reason
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


def render(snap: s.Snapshot, width: int = 80) -> list[str]:
    width = max(40, width)
    title = "Orca Orchestrator · 읽기 전용 상태"
    if snap.demo:
        title += f" · 데모 ({snap.demo})"
    taken = snap.taken_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    rule = "─" * min(width, 72)
    lines = [
        title,
        f"조회 시각 {taken}" + (" · 실제 CLI를 호출하지 않음" if snap.demo else ""),
        rule,
        _row("Runtime", runtime_text(snap.runtime)),
        _row("바인딩", "Coordinator 터미널 바인딩 있음" if snap.bound else "없음"),
        _row("현재 Run", run_text(snap.run)),
        *dispatch_lines(snap),
        _row("사용량", " · ".join(f"{PROVIDER_NAMES[p]} {UNKNOWN}" for p in Provider)),
        rule,
    ]
    note = hint(snap)
    if note:
        lines.append(note)
    lines.append(FOOTER)
    return [fit(line, width) for line in lines]
