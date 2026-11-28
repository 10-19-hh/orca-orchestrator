# Orca Studio — Living Developer Workspace 인계 계획

작성 기준: 2026-11-28 (Asia/Seoul). 이 문서는 제품 방향과 후속 단계의 승인·검증 기준을 기록한다. GUI 구현이나 Worker 실행을 승인하는 문서가 아니다. 이번 작업 범위는 이 신규 문서의 작성, 단독 커밋, 현재 브랜치로의 일반 push다.

## 1. 보고 기준과 실제 저장소 확인

과거 대화의 완료 보고와 이번 확인 결과를 구분한다. 과거 수치를 현재 재검증 결과로 인용하지 않는다.

| 항목 | 대화의 보고 기준 | 이번 문서 작성 전 확인 |
| --- | --- | --- |
| 브랜치 / HEAD | `console-readability` / `b0b035d` (Phase 0~1 보고) | `console-readability` / `092af9ae0baab784a22cbeaa810c5ea9e4b91559` |
| 원격 | 현재 상태 확인 필요 | `origin`, GitHub `10-19-hh/orca-orchestrator`; upstream `origin/console-readability`. 원격 브랜치 HEAD도 위 로컬 HEAD와 일치 |
| 테스트 | 405개 통과 보고 | 이번 문서 작업에서는 테스트를 재실행하지 않음. 405는 과거 기준선 |
| Snapshot | `orca-orchestrator/snapshot@1` | `docs/snapshot-v1.md`, 구현 파일, `tests/fixtures/snapshot_v1/`의 추적 파일 12개 존재 |
| Phase 2 디자인 | `design/orca-studio/` 콘셉트 렌더·명세 60개 | 현재 해당 경로의 추적 파일은 62개. 파일 수 차이를 디자인 승인이나 기능 완료로 해석하지 않음 |
| 미커밋 변경 | 구현·디자인 변경 다수라는 과거 보고 | staged / tracked unstaged 변경 없음. untracked `.DS_Store` 1개 존재, 보존 대상 |

현재 이력에는 `173b31c`의 Snapshot 계약과 `092af9a`의 디자인 문서가 이미 커밋되어 있다. 후자 제목의 `Concept A approved`는 이력상의 표현이다. **최신 사용자 지시에 따라 A/B/C 코드 렌더는 최종 승인된 디자인이 아니다.** 기존 명세와 이력을 수정하지 않고, 후속 작업은 이 문서의 새 방향과 미승인 상태를 기준으로 한다.

Phase 0 Architecture Audit / Phase 1 Foundation Contract / Phase 2 초기 디자인 명세 / Phase 2.5 Visual Review는 대화에서 진행·완료가 보고된 단계다. 이번 작업은 그 전체 결과를 재실행하거나 시각적 품질을 새로 승인하지 않았다. Phase 2.6은 다음 조사 단계이며 아직 완료되지 않았다.

저장소 실제 경로와 Git 상태는 로컬에서 확인했다. 공유 문서에는 개인 절대 경로, 터미널 핸들, 인증 정보, 환경변수 값, 로컬 로그 원문을 싣지 않는다.

## 2. 제품 비전

**Living Developer Workspace**: 실제 개발 환경의 구조에 따라 방과 자리, 캐릭터가 생성되는 개발 스튜디오. 고정된 방에 Codex와 Claude 두 마리를 놓는 데서 끝나지 않는다. Worktree와 확인 가능한 세션이 늘어나는 과정을 공간으로 이해하게 한다.

| 실제 개념 | 공간 표현 | 의미 제한 |
| --- | --- | --- |
| Repository | Studio | Orca Workspace와 동일 개념이라고 가정하지 않음 |
| Git Worktree | 독립 Room | 경로·브랜치·Orca 관계는 조사로 확인 |
| Orca Tab | Room 안의 구역 | 실제 소속 관계가 확인된 경우에만 연결 |
| 확인 가능한 Agent Session | 전용 Station + Mascot | 세션 존재가 작업 중이라는 뜻은 아님 |
| 일반 Terminal | 터미널 장치 | 탭 제목만으로 에이전트나 Provider를 만들지 않음 |
| Local Verification | 검증 장치 | Codex/Claude의 코딩·빌드로 표현하지 않음 |
| Review / 확인 요청 | Attention Indicator | 연결 근거가 없으면 특정 세션 대신 공용 보드에 표시 |

Codex 3개와 Claude 2개의 독립 세션이 확인되면 자리와 마스코트는 각각 5개다. Provider별 하나로 합치지 않는다. Companion은 장식·동반자 역할이며 실제 worker나 세션을 대신하지 않는다. Snapshot v1의 고정 companion 카탈로그를 live 세션 목록으로 사용하지 않는다.

## 3. 시각적 방향과 승인 경계

사용자가 선호한 방향은 **compact isometric pixel art cutaway diorama**다.

- 두 벽과 열린 앞면으로 작은 작업실을 보여 준다. 2:1 아이소메트릭 투영은 검토 후보이며 최종 수치는 시안에서 결정한다.
- 따뜻한 저채도 mauve(분홍·보라) 벽, 베이지 보조색, 원목 바닥을 쓴다.
- 창문에서 들어오는 방향성 있는 자연광, 따뜻한 실내 조명, 일관된 그림자로 깊이를 표현한다.
- 가구의 앞면·옆면·윗면과 재질이 읽히고, 소품은 작지만 정교하게 만든다.
- 불필요한 빈 바닥을 줄이고, 방 밖은 깨끗한 단색 배경으로 둔다. 넓은 직사각형 방을 단순히 확대하지 않는다.
- 기존 Codex 파란 구름 머리 로봇과 `>_` 얼굴, Claude 주황 크리터의 정체성을 유지한다. 공통 32×32 캔버스를 우선 유지하고 시점에 맞는 방향·자세를 확장한다.
- 픽셀 밀도, nearest sampling, 정수 배율 확대를 검토 기준으로 유지한다. 이전 480×270 방, 16px 타일, 96px Station, pivot `(16, 31)`은 기존 시안의 값이며 새 투영에서 무조건 고정하지 않는다.

참고 출처는 사용자가 대화 「GUI 방향 검토」에 올린 Pixel Office 관련 방 이미지와 그 선호 설명이다. 이번 문서는 이미지 사용권을 확인하지 않았으므로 이미지를 저장·복사·재배포하지 않고 분위기를 텍스트로만 기록한다. 외부 에셋 도입 시 출처, 권리자, 라이선스/사용 권한, 변형 여부를 별도로 기록한다.

A/B/C와 Hero는 코드로 그린 콘셉트 렌더다. 최종 아트나 production asset으로 승격하지 않는다. Art Direction은 시안과 에셋 품질을, Engineering은 승인된 에셋의 배치·렌더링·상태 표현을 담당한다. 개발 과정에서 방 전체를 코드로 다시 그려 최종 아트로 대체하지 않는다.

## 4. 모듈형 에셋 제작 계약 후보

방 전체를 한 이미지로 고정하지 않고 재조립 가능한 PNG + 메타데이터(JSON)를 제작한다. 원본 편집 파일의 도구·제작자는 Phase 2.7에서 결정한다.

| 모듈 | 구성 |
| --- | --- |
| Room | 바닥·벽 타일, 모서리·연결부, 창문 |
| Station | 책상, 의자, 모니터, 자리별 식별 표시 |
| Furniture | 책장, 식물, 라운지, 공용 가구 |
| Devices | 검증 장치, 상태 보드, Runtime LINK 장치 |
| Light / Shadow | 조명 overlay, 그림자 sprite |
| Characters | Codex·Claude 방향별 시트, 표정, idle·walk 프레임 |

메타데이터에는 안정적 asset ID, 이미지 크기, pivot, 투영·방향, footprint, 연결 규격, 레이어/깊이 정렬 기준, 충돌 영역, 이동 가능 영역, animation tag·프레임 시간, 허용 상태, 출처/라이선스를 둔다. 이 목록은 제작 요구사항이며 완성된 스키마가 아니다. 시트 투명도, 테두리 잘림, 픽셀 밀도, 겹침, 동일 좌표에서 교체 가능 여부를 검증한다.

## 5. 기술 방향과 책임 경계

우선순위는 **React + TypeScript + PixiJS v8**이다. React는 탐색·상태 텍스트·Inspector, PixiJS는 방·가구·마스코트·카메라를 담당하는 방향이다. Tauri는 후속 패키징 단계에서 검토한다. 이번 문서 작업에서 프로젝트 생성이나 의존성 설치를 하지 않는다.

기존 Python Core와 TUI를 유지한다. Task / Verification / Review / Recovery 판정은 Python Core의 책임이다. TypeScript는 판정 결과를 표시 방식으로 매핑하며 도메인 판정을 다시 구현하지 않는다. `scene_state()` 이식을 검토할 때도 이 경계를 지킨다.

`orca-orchestrator/snapshot@1`은 불변이다. 기존 12개 fixture를 새 세션 표현에 맞추어 변조하거나 가짜 live 데이터로 확장하지 않는다. Topology가 필요하면 **별도의 읽기 전용 계약**을 조사 결과에 따라 제안하고 승인받는다. 아직 계약 이름·버전·필드가 확정되거나 구현된 것은 아니다.

후보 흐름: Orca Runtime + Git → 읽기 전용 Discovery → Workspace Topology → Room Composer → Agent Visualizer → React/PixiJS. Snapshot은 기존 작업·검증·검토 상태를 계속 공급한다. 두 계약을 결합할 때는 근거 있는 식별자로만 연결하며, 대응이 불명확하면 연결하지 않는다.

## 6. 관측과 의미적 안전성

저장소 README와 어댑터에는 실제 Orca **1.4.268**에서 읽기 전용 상태 확인을 했다는 기록이 있고, 기존 검증 버전은 **1.4.264**다. 이 버전 차이를 지원 확인으로 뭉뚱그리지 않는다. 이번 문서 작업에서는 실행 중인 Orca 버전을 새로 조회하지 않았다. Phase 2.6에서 설치본·Runtime·CLI 버전을 다시 구분해 확인한다.

현재 `workers.live.availability = not_implemented`이며 실제 Worker dispatch도 미구현이다. 명령 구성·기록 어댑터가 존재한다고 실제 전송이나 실시간 관측이 가능한 것은 아니다. UI 문구는 **“Worker 실시간 관측 미지원”**이어야 하며 “Worker 연결 없음”이나 “Worker 0개”로 바꾸지 않는다.

| 사실 / 불확실성 | 표현 원칙 |
| --- | --- |
| Live 관측 | 출처와 관측 시각, 지원 범위를 표시 |
| Recorded | 과거 실행 기록임을 표시; 현재 활동으로 재생하지 않음 |
| DEMO | 화면 전체에 DEMO 표시; live와 별도 데이터 경로 |
| Unknown / unavailable / not_implemented | 알 수 없음·조회 실패·미지원 구분; 빈 목록과 혼동 금지 |
| Stale | 낡음은 출처와 별도 축. 마지막 확인 시각·무효화 이유를 보여 주고 성공처럼 표시하지 않음 |
| 세션 존재 | 자리 생성은 가능하지만 Coding / Thinking / Reviewing / Tool Call 추정 금지 |
| 로컬 검증 | 장치가 수행·결과를 표시; 에이전트의 작업 동작으로 전환 금지 |
| 연결 끊김 | 관측 불가 표시, 기존 자리 유지; 종료로 판정 금지 |
| 종료 미확인 | 자리와 캐릭터 정체성을 유지하고 `?` / 확인 필요 표시 |
| 종료 확인 | 근거를 표시한 뒤 퇴장·자리 정리 가능; 보존 정책은 후속 결정 |

작업 완료와 세션 종료는 다르다. 탭 선택·포커스·터미널 제목도 활동 증거가 아니다. 관측되지 않은 행동을 생성하지 않는다. Snapshot v1에서는 worker 작업 애니메이션을 항상 금지한다. Runtime LINK 표시도 worker 연결이나 작업 성공을 뜻하지 않는다.

## 7. Phase 2.6 — Workspace Topology Feasibility Audit

**목표: 읽기 전용 조사와 제안만 수행한다. 구현하지 않는다.** 현재 설치본의 CLI 도움말·문서, 저장소 코드, 안전한 CLI/Git 조회를 근거로 한다. 지원 명령을 먼저 확인하고 추측한 명령을 실행하지 않는다.

### 조사 항목

1. Orca workspace와 repository 목록, Git worktree 목록·브랜치·경로의 차이 및 대응 관계.
2. Workspace / repo / worktree / tab / terminal / session의 식별자, 안정성 범위, 소속 관계와 cardinality. 제품의 계층을 데이터에 억지로 적용하지 않는다.
3. Tab별 terminal, terminal별 session 식별 가능성. 동일 Provider 다중 세션과 중복 노출 여부.
4. Provider를 신뢰할 수 있는 필드로 식별하는지, 제목·명령명 추정만 가능한지. 알 수 없으면 unknown.
5. Session 생성·종료·재연결·이동·재시작·ID 재사용 관측 가능성. 현재 session 존재와 worker 활동 구분.
6. 활성 tab / 선택 terminal의 조회 범위 및 포커스와 작업 상태의 구분.
7. Polling 또는 events 지원, 갱신 시각·순서·누락·실패·비용, 연결 끊김과 빈 결과의 구분.
8. 개별 작업 상태를 실제로 관측할 수 있는 범위. Coding / Thinking 등을 데이터 없이 추정하지 않는다.

각 항목에 명령/문서/코드 상대 경로, 버전, 확인 시점, 비식별화한 응답 필드, 한계, 재현 방법을 남긴다. 실제 세션을 만들거나 종료하지 않고 현재 존재하는 관측만 사용한다. 변화를 관측하지 못했으면 lifecycle을 검증했다고 쓰지 않는다.

| 증거 등급 | 기준 |
| --- | --- |
| VERIFIED | 해당 버전에서 실제 읽기 전용 응답으로 범위와 의미를 확인 |
| PARTIALLY VERIFIED | 일부 필드·관계·상황만 확인; 미확인 범위를 명시 |
| DOCUMENTED ONLY | 도움말·문서·소스에 있으나 실제 응답 미확인 |
| UNVERIFIED | 증거가 부족하거나 현재 조건에서 확인하지 못함 |
| NOT AVAILABLE | 확인한 인터페이스·버전에서 제공하지 않음; 조사 범위를 명시 |

### Topology 계약 제안 조건

데이터가 충분할 때만 `repositories`, `worktrees`, `tabs`, `terminals`, `agent_sessions`, `provider`, `observation`, `lifecycle`, `capabilities`, `provenance`를 후보로 제안한다. 각 필드는 출처·nullable 조건·관측 범위가 있어야 한다. 출처가 없는 값을 채워 넣지 않는다.

- 원천 ID를 우선하고 스코프·재사용·충돌을 설명한다. 이름·목록 순서·branch만으로 ID를 만들지 않는다.
- 동일 Provider 다중 세션을 구분하고, 동일 세션의 중복 노출은 근거로만 병합한다.
- 확인된 session과 Station/Mascot의 1:1 대응 조건을 명시한다. 세션 미확인 terminal은 장치로 남긴다.
- 탭 이동·재연결에서 동일 ID와 자리를 유지한다. 재연결 시 동일성 확인이 안 되면 자동 병합하지 않는다.
- 세션 추가·삭제 시 기존 자리 전체를 재배치하지 않는다. 삭제/퇴장은 종료·worktree 삭제가 확인된 경우만 허용한다.
- 관측 불가, 종료 미확인, unknown Provider를 명시적으로 표현한다.
- DEMO 데이터는 별도 fixture로 만들되 live 계약이나 Snapshot v1의 실제 관측을 사칭하지 않는다.
- 절대 경로 등 민감한 원천 필드의 수집 필요성과 공개용 정규화를 분리한다. 보고서에는 상대 경로·별칭을 사용한다.

### 결과물과 게이트

결과물은 구조 분석, 관측 가능성 표, 데이터 출처·한계, 필요한 경우 Topology 초안, Composer 책임 설계, 1/2/4/8 세션 및 다중 Worktree 배치안, 에셋 규격, 재사용 범위, 위험·blocker, Evidence Index다.

최종 판정은 `FEASIBLE`(Topology 실현 가능), `PARTIALLY FEASIBLE`, `BLOCKED` 중 하나다. 전체 기능과 제한된 fallback의 판정을 구분한다. 세션 관측이 부족하면 repo/worktree 또는 terminal까지만 표현하는 대안을 제시하고, 가짜 세션으로 빈틈을 채우지 않는다. 사용자가 조사 결과와 다음 범위를 검토·승인한 뒤 다음 단계로 간다.

금지: Worker 실행·dispatch, 세션/탭/터미널/worktree 생성·종료, GUI 구현, 의존성 설치, Core/TUI/Snapshot 변경. 후속 조사 자체의 커밋·push는 별도 요청이 없으면 하지 않는다. 이는 **이번 인계 문서의 커밋·push 허가와 구분**한다.

## 8. 후속 로드맵과 단계별 승인·테스트 게이트

| 단계 | 범위 | 통과 기준 / 다음 단계 승인 |
| --- | --- | --- |
| 2.6 Topology Audit | 읽기 전용 조사·설계 제안 | 항목별 증거 등급, 버전 차이, 식별·lifecycle 한계, feasibility 판정 검토. 구현 금지 |
| 2.7 시안 / production assets | 새 디오라마 시안, 모듈형 에셋, 캐릭터 방향·자세 | 원본/정수 배율 및 실제 UI 크기 비교, 1/2/4/8 자리 밀도, 그림자·투영·가독성, 권리 확인, PNG/메타데이터 검증. 사용자 시각 승인과 제작 주체 확정 |
| 3 Dynamic Room Composer | 승인된 아트로 fixture 기반 동적 방 조립 | 결정적 배치, 자리 안정성, 충돌·깊이·선택 영역, 세션 추가/종료/미확인/재연결, 3 Worktree 시나리오 테스트. Node 툴체인과 구현 범위 승인 필요 |
| 4 Motion / Game Feel | idle·walk·반응, 방향 전환·이동 | 상태별 허용 동작 테스트, 금지 작업 동작 미발생, reduced motion, 끊김·중단·정지 화면의 의미 보존, 실제 CPU/FPS 측정 후 예산 승인 |
| 5 Navigation / Inspector | Studio↔Room 이동, 선택·상세, Task Board/Review Queue | ID 기반 선택 유지, 출처·신선도·unknown 가독성, 키보드/포커스, 좁은 화면, 오류·빈 상태 검증. 읽음과 해결 구분, 명령 버튼 없음 |
| 6 Live read-only | 승인된 Discovery/Topology와 Snapshot 연결 | fixture/live 분리, schema·회귀 테스트, polling/events 누락·재연결·중복·stale·종료 미확인 검증, 명령 전송 없음 확인. 실제 관측 범위만 출시 |
| 7 Packaging | Tauri 등 데스크톱 패키징 검토 | 플랫폼·보안 경계·설정·업데이트 방식 승인, 설치/시작/종료/복구 smoke test, Core/TUI 공존 및 성능 확인 |
| 별도 Command Control | 검증 실행·승인·worker 명령 등 | 별도 명시적 승인 후 권한, 대상 확인, 중복 방지, 감사 기록, 실패·재시도 계약과 테스트 설계. 읽기 전용 단계에 혼입 금지 |

각 단계는 결과와 실패·미검증 항목을 보고한 뒤 승인받는다. 문서 승인, 디자인 승인, 구현 승인, 명령 실행 승인은 서로 대체하지 않는다. Core/TUI 회귀와 Snapshot v1 fixture 불변을 후속 구현의 공통 게이트로 둔다. 테스트 수만 같다고 호환성이 보장된 것으로 보지 않는다.

### Dynamic Room Composer 책임

Isometric Tilemap은 좌표 변환, Room Module은 방 경계, Station/Furniture Module은 교체 가능한 가구 단위, Layout Allocator는 안정적 자리 할당, Mascot Factory는 검증된 session과 캐릭터 대응을 담당한다. Navigation Grid와 Collision Layer는 동선·점유를, Depth Sorting은 겹침을, Camera Controller는 전체 Studio와 개별 Room 탐색을 담당한다. 이 구성은 설계 후보이며 Phase 3 이전에 구현하지 않는다.

필수 시나리오는 Worktree 1개에 세션 1/2/4/8개, Worktree 3개에 서로 다른 세션 수, 동일 Provider 다중 세션, 생성·확인된 종료·관측 불가·재연결·탭 이동·확인된 Worktree 삭제다. 빈 Worktree는 빈 방으로, 미확인 상태는 미확인으로 표현한다. Worktree가 늘면 작은 방을 추가하며 전체 가구를 재배치하는 방식은 피한다.

## 9. 현재 한계와 열린 결정

- 실제 Workspace/Tab/Terminal/Session의 전체 목록과 관계, stable ID, 실시간 lifecycle/events 지원은 Phase 2.6 조사 전이다.
- Orca 1.4.268과 기존 검증 1.4.264의 계약 호환성은 전면 검증되지 않았다.
- Worker 실시간 관측과 실제 dispatch는 현재 미구현이다. usage-limit 실측, 이벤트 이력도 기존 Snapshot의 지원 범위 밖이다.
- Snapshot v1은 로컬 검증 진행과 다른 세션 활동의 완전한 실시간 모델이 아니다. 과거 fixture의 종료 미확인 기록을 live 실행으로 해석하지 않는다.
- 최종 아트 제작자·도구·권리, 2:1 투영과 타일/Station 크기, 캐릭터 방향 수, 조명 시간대, 장면 밀도는 열려 있다.
- 종료 후 자리 보존 기간, 탭 이동 정책, 다중 Room 탐색·축소·overflow, 성능 예산은 시안과 실측으로 결정한다.
- Topology 계약 도입 여부·버전, 통신 방식(HTTP/SSE/WebSocket 등), polling 간격, 이벤트 이력 보존은 조사 후 결정한다. 현재 연결 구현이 있다고 가정하지 않는다.
- Phase 2.7의 시각 검토와 Phase 3의 기술 승인 전에는 기존 A/B/C를 최종 아트로 사용하지 않는다.

## 10. 다음 작업 시작 프롬프트

```text
Orca Studio Phase 2.6 — Workspace Topology Feasibility Audit를 수행한다.
docs/ORCA_STUDIO_HANDOFF.md와 docs/snapshot-v1.md를 먼저 읽는다.
현재 실제 저장소/브랜치/HEAD/status와 설치 Orca/CLI/Runtime 버전을 확인하고,
과거 console-readability / b0b035d / 405개 테스트 보고와 구분한다.

읽기 전용 CLI 도움말·문서·코드·Git 조사만 수행한다.
workspace/repo/worktree/tab/terminal/session의 ID와 관계, provider,
lifecycle, 활성 tab/terminal, polling/events, 실제 작업 관측 범위를 조사한다.
지원 명령을 먼저 확인하며 새 세션·Worker·탭·worktree를 생성하지 않는다.
각 항목에 VERIFIED / PARTIALLY VERIFIED / DOCUMENTED ONLY /
UNVERIFIED / NOT AVAILABLE과 비식별화한 증거·버전·한계를 기록한다.

Snapshot v1, Python Core, TUI, 기존 변경을 보존한다.
필요할 때만 별도 읽기 전용 Topology 계약을 제안하고 출처 없는 필드는 만들지 않는다.
동일 Provider 다중 세션, 안정 ID/자리, 재연결, 종료 미확인,
1/2/4/8 세션 및 3 Worktree 배치를 설계 수준에서 검토한다.
세션 존재를 작업 중으로, 연결 끊김을 종료로 해석하지 않는다.

구현·에셋 제작·의존성 설치·실제 dispatch·명령 제어는 금지한다.
별도 요청 없이 커밋·push하지 않는다.
증거표, 계약 제안 필요성, Composer/에셋 요구사항, 재사용 범위,
위험·blocker·Evidence Index를 보고한다.
FEASIBLE / PARTIALLY FEASIBLE / BLOCKED로 판정하고 다음 단계 승인 전에 종료한다.
```

## 11. 참고 경로

모든 경로는 저장소 루트 기준이다. 아래 기존 자료의 오래된 디자인·상태 문구는 최신 방향과 구분해서 읽는다.

- `README.md`: 현재 기능·CLI·버전 검증 범위와 제한.
- `docs/PRD.md`, `docs/orca_agent_orchestrator_PRD_v0.1.md`: 기존 제품·실행 구조.
- `docs/snapshot-v1.md`, `src/orca_orchestrator/snapshot_document.py`: Snapshot v1 계약과 내보내기.
- `tests/fixtures/snapshot_v1/`: 고정 예시 12개. `tests/test_snapshot_document.py`: 계약 검증.
- `src/orca_orchestrator/orca/adapter.py`, `dispatch.py`, `workers.py`: Orca 조회·전송 제한·기록 관측 경계.
- `src/orca_orchestrator/console/`: 기존 TUI, `sprites.py` 캐릭터, `yard.py` 표현 의미.
- `design/orca-studio/index.html`: 기존 산출물 진입점.
- `design/orca-studio/art-direction/SPEC.md`: 기존 기술·아트 명세, 최종 승인본 아님.
- `design/orca-studio/concepts/`, `hero/`, `character-studies/`, `environment-studies/`, `state-studies/`, `ui-wireframes/`: 앞 경로 아래의 기존 비교 시안.
- `design/orca-studio/asset-specification/`, `palettes/`, `references/README.md`: 앞 경로 아래의 메타데이터 예시·팔레트·출처 원칙.

## 12. 이번 문서 전달의 검증 범위

이 파일만 별도 커밋한다. 기존 staged/unstaged/untracked 변경은 보존하며 `reset`, `stash`, 강제 push를 사용하지 않는다. 커밋 전 문서 diff와 `git diff --check`, 민감정보·절대 경로 포함 여부를 검토한다. 커밋 후 변경 파일 목록이 이 문서 하나인지, 기존 변경이 유지되는지 확인한다. 현재 브랜치로 일반 push하고 원격 HEAD를 확인한다. 인증·권한·충돌 문제가 있으면 강제 처리하지 않고 blocker로 보고한다. 실제 커밋 해시와 push 결과는 작업 완료 보고에서 제공한다.
