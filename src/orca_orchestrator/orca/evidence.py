"""What Experiments 4-A and 4-B established per Provider, and what is still missing.

A status summary only: no account identifiers, values or raw observations.
Recorded 2026-11-26 (KST), Claude items updated with Experiment 4-B on
2026-11-27 (KST); 4-A stays CONDITIONAL until every item is resolved.
An answer the user received about their own account is not a basis that
applies to other users, so it never raises a policy item here.
Changing a status here does not open the dispatch gate: the gate reads
``DispatchPolicy``, which a person sets after reviewing new evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dispatch import Provider

EXPERIMENT_4A_STATUS = "CONDITIONAL"
EXPERIMENT_4B_CLAUDE_STATUS = "PARTIAL"  # start, completion and exit observed; sign-in and billing not
EVIDENCE_DATE = "2026-11-27"


@dataclass(frozen=True)
class ProviderEvidence:
    item: str  # 구독 인증 / 정책 근거 / 구독 외 과금 배제
    status: str  # VERIFIED / DOCUMENTED / UNVERIFIED / INCONCLUSIVE
    secured: str  # what is already established (reused, not re-checked)
    missing: str  # what a real Worker test still needs


PROVIDER_EVIDENCE: dict[Provider, tuple[ProviderEvidence, ...]] = {
    Provider.CLAUDE: (
        ProviderEvidence(
            "예정 Worker의 실제 구독 인증", "UNVERIFIED",
            "실험 셸의 Claude CLI가 claude.ai 구독 로그인을 표시. Orca Agents 설정은 CLI·Command와 Arguments "
            "기본값·Environment 재정의 없음·System default 프로필 (사용자 확인과 화면 관측). 4-B에서 Worker는 "
            "mode terminal로 시작·완료·종료했지만 시작 직후 실행 파일·인자·로그인 표시를 수집하지 못함",
            "Worker 시작 직후 같은 PTY의 실행 파일·인자(-p/--print 여부)·로그인 표시와, 과금을 확인한 계정과의 일치",
        ),
        ProviderEvidence(
            "해당 자동화의 공식 정책 근거", "UNVERIFIED",
            "본인이 공식 비수정 Claude Code CLI에 구독으로 직접 로그인하는 경로는 공식 문서에 있음. "
            "실험 계정으로 받은 개별 지원 답변은 그 계정의 판단 자료일 뿐 다른 사용자에게 적용되는 근거가 아님",
            "Orca가 대화형 PTY에 작업을 자동 전달하는 방식이 해당 플랜 약관의 자동화 조건에 맞는다는 공개 근거",
        ),
        ProviderEvidence(
            "구독 외 과금 배제", "INCONCLUSIVE",
            "확인한 계정에서 Extra usage 꺼짐·별도 크레딧 잔액 없음·자동 충전 꺼짐 (사용자 확인). 4-B 전후 공식 "
            "사용량 표시는 변화 없음 (표시 단위 안의 차감이나 다른 과금 경로를 배제하지 못함)",
            "Worker가 그 계정을 쓴다는 근거, 실행 전후 비교 가능한 사용량 수치, Console/API 키·클라우드 Provider·"
            "게이트웨이 경로의 배제",
        ),
    ),
    Provider.CODEX: (
        ProviderEvidence(
            "예정 Worker의 실제 구독 인증", "UNVERIFIED",
            "실험 셸의 Codex CLI가 ChatGPT 로그인을 표시. Orca 계정 목록은 시스템 기본값 OAuth. 설정은 CLI·"
            "System default (사용자 확인)",
            "Worker의 유효 인증과 Codex 바이너리 버전, 실험 셸에서 관측된 대체 인증 변수가 Runtime·Worker에 "
            "전달되지 않는다는 근거",
        ),
        ProviderEvidence(
            "해당 자동화의 공식 정책 근거", "UNVERIFIED",
            "공식 Codex CLI의 ChatGPT 구독 로그인과 비대화형 인증 조건은 공식 문서에 있음",
            "Orca의 PTY 자동 입력 또는 app-server 경로가 해당 계정·워크스페이스의 지원 범위라는 근거",
        ),
        ProviderEvidence(
            "구독 외 과금 배제", "INCONCLUSIVE",
            "확인한 계정에서 구매 크레딧 없음·잔액 없음·자동 충전 꺼짐 (사용자 확인)",
            "앱별 크레딧 사용 허용, Platform API 결제 상태, API 키·대체 Provider 경로의 배제",
        ),
    ),
}
