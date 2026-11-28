# Orca Studio — Phase 2.7 보충 인계

기록일은 2026-11-29 KST다. 이 문서는 **Cozy Isometric Developer Office** 방향, Phase 2.6 조사 결과, Windows 통합 기준선과 후속 게이트를 기록한다. 기존 인계·아트 명세와 충돌하는 Studio 결정에는 이 문서를 적용한다. Python Core의 제품 요구사항과 Snapshot v1 데이터 계약은 각각 원문을 따른다. [기존 인계](ORCA_STUDIO_HANDOFF.md) · [PRD v0.3](PRD.md) · [Snapshot v1](snapshot-v1.md)

## 1. 저장소와 검증 기준선

| 구분 | 확인 결과 |
| --- | --- |
| 작성 시작 시점 | `main`의 HEAD는 `73c20911283b4d0c0db1cd90120fe1e6d2864169`, `MERGE_HEAD`와 원격 `main`은 `998609c62a1caf2f9a9d5b9e0914c56443030321`이었다. Ahead 2, behind 4였고 staged 99개(추가 90, 수정 9), unstaged 추적 변경 0개, 미해결 충돌 0개였다. |
| 최종 Windows 체크아웃 | 2026-11-29 00:18 KST 기준 `phase-2.7-living-workspace`의 HEAD는 `242a7a6e0446cd367ce18ebfa6dfaabb333dec8d`. staged·unstaged 추적 변경은 각 0개였으며, 미추적 파일은 이 문서와 기존 `t discover -s tests`다. |
| 병합과 push | `242a7a6`은 부모가 `73c2091`과 `998609c`인 Windows 통합 병합 커밋이다. 로컬 `main`과 Git 서버의 `refs/heads/main`이 같은 해시를 가리키며 `MERGE_HEAD`는 해소됐다. 이 문서 작업에서 브랜치 전환, 커밋 또는 push를 수행하지 않았다. |
| Claude Phase 2.7 | 사용자 보고 기준 진행 중이다. 최종 관측에서 Phase 2.7 로컬 브랜치가 보였으나 통합 커밋을 가리켰고 별도 구현 diff나 Claude 소유 파일을 식별하지 못했다. 원격 조회에 나타난 브랜치는 `main`과 `console-readability`였다. 기존 README, PRD, 인계·디자인·Snapshot 문서는 그대로 보존했다. |
| 이전 검증 결과 | Integration Recovery Gate `PASS`. Windows Python 3.11에서 424개 통과, 실패·오류·스킵 각 0개. 전체 재실행도 424개 통과했고 마지막 실행은 310.352초였다. 텍스트·JSON CLI 데모 Smoke Test는 통과했다. 405개는 이전 macOS 기준선이다. |
| 이번 작업에서 확인한 범위 | Git·문서·관련 코드와 외부 참조 페이지를 읽었다. 이 문서 작업에서 테스트·Runtime 조회를 재실행하지 않았다. 이전 통합 검증에도 실제 Windows Orca 탭의 대화형 동작은 포함되지 않았다. |

### Integration Recovery에서 보존한 의미

- Console은 `observation_lines()`가 반환한 모든 줄을 표시한다. 현재 조회 실패와 마지막 확인 상태를 구분하며, 종료 중 늦게 도착한 조회로 화면을 갱신하지 않는다. Windows 테스트는 인자 안의 공백과 따옴표 규칙을 확인한다.
- `FINISHING`은 Worker 작업이 정산된 뒤 프로세스 종료 또는 터미널 해제를 기다리는 상태다. 터미널 정리 중에는 중복 실행을 막고 읽기 전용 재조회로 안내한다. Worker의 `succeeded`, 검증 통과, 사용자 승인은 서로 다른 판정이다.
- Snapshot v1의 스키마와 필드 구조를 유지했다. 기존 상태 필드로 `finishing`·`exited`를 표현한다. `workers.recorded`는 저장된 기록이며 `workers.live.availability = not_implemented`, `dispatch.sends_work = false`다. 가시화는 기록을 실제 작업 애니메이션으로 재생하지 않는다. Worker 4-B 관측과 실행·전송 승인 경계는 기존 PRD와 README를 따른다. [Snapshot 계약](snapshot-v1.md) · [Worker 실행과 복구](../README.md#worker-상태와-복구) · [Step 0 범위](PRD.md#07--step-0-기술-타당성-게이트)

## 2. 제품과 공간

**Orca Studio**는 **Living Developer Workspace**다. 실제 개발 환경이 확장되면 같은 아이소메트릭 사무실에서 Room과 작업 자리가 늘어난다.

| 개발 환경 | 사무실 표현 |
| --- | --- |
| Repository | Studio. Orca Workspace와 Repository의 대응은 식별 근거로 연결한다. |
| Worktree | 독립 Room. Worktree가 추가되면 Room을 추가한다. |
| Tab / Pane | Room에 속한 작업 환경. 원천에서 확인된 소속으로 연결한다. |
| 확인된 Agent Session | 전용 책상과 Mascot 각각 하나. 같은 Provider의 여러 세션도 개별 자리를 유지한다. |
| 일반 Terminal | 터미널 장치. 탭 제목이나 생성만으로 Agent Session을 확인할 수 없다. |

세션이 0개라고 확인된 Room에는 세션 전용 자리와 펫을 생성하지 않는다. 조회할 수 없거나 목록이 잘리면 마지막으로 확인된 자리와 세션을 유지하고 관측 불가를 표시한다. 세션 추가나 Room 확장에도 기존 자리의 식별자와 위치를 유지한다.

## 3. Cozy Isometric Developer Office

Game Dev Story를 참고한 기능적인 사무실 배치에 따뜻하고 절제된 Cutaway Diorama의 색감과 조명을 결합한다.

- **배치** — 중앙에 서로 마주 보는 책상 클러스터를 두고 2·4·6·8석 이상으로 확장한다. 출입구와 책상 사이의 통로를 확보하고 회의 테이블, 화이트보드, 수납장, 탕비실, 라운지를 연결한다.
- **재질과 조명** — 따뜻한 원목 바닥, 베이지·그레이지·저채도 모브 벽, 오크·월넛 계열의 차분한 업무용 가구를 사용한다. 방향성 있는 자연광과 부드러운 그림자로 아늑함을 표현한다.
- **피할 배치** — 침실·게임룸, 침대 중심 배치, 원색 가구, 벽면에만 붙인 책상, 과도한 빈 바닥, 차갑거나 지나치게 어두운 실내, 사무실을 가리는 상시 UI 패널. Inspector는 선택 시 연다.
- **Claude** — 작고 각진 주황색 Clawd 실루엣, 짧은 팔다리, 단순한 검은 눈을 유지한다. 감정은 소품과 행동으로 표현한다. 둥근 문어형 시안은 폐기한다.
- **Codex** — 파란 구름 머리, 어두운 터미널 얼굴, 작은 로봇 몸과 단순한 팔다리를 유지한다. 두 캐릭터의 픽셀 밀도를 맞추고 32×32 캔버스를 우선 검토한다. 실제 실루엣, pivot, 방과 좌석 치수는 시안 검토를 거친다.

기존 Phase 2 아트 명세의 **Concept A 승인** 문구와 고정 규격은 해당 시안의 기록이다. A/B/C와 Hero는 비교 자료로 남기며 최종 아트 승인은 Phase 2.8에서 받는다. A는 빈 바닥과 벽면 위주 가구, B는 차가운 실험실 분위기, C는 어두운 조명, Hero는 상시 패널 면적을 비교할 때 사용한다. 이전 문어형 Claude는 실루엣을 유지해야 하는 이유를 보여 주는 비교다. [Phase 2 명세](../design/orca-studio/art-direction/SPEC.md) · [A/B/C](../design/orca-studio/concepts/index.html) · [Hero](../design/orca-studio/hero/hero-mockup.html)

## 4. Ambient Agent Behavior

확인된 세션이 대기할 때 펫은 사무실을 산책하거나 커피·간식을 즐긴다. 소파에 앉거나 낮잠을 자고, 화분과 소품을 구경하거나 청소하며, 공용 공간에서 다른 펫과 가볍게 교류할 수 있다. Ambient 행동은 시각적 연출이다.

| 관측 | 표현 |
| --- | --- |
| 대기 확인 | Ambient 행동을 고르고 접근 가능한 가구와 상호작용한다. 전용 자리는 해당 세션에 예약된 상태로 유지한다. |
| 작업 시작 확인 | Ambient 행동을 중단하고 상호작용 점유를 해제한다. 전용 책상으로 이동해 앉는다. Thinking, Coding, Building 같은 구체적인 동작마다 별도의 관측 근거가 있어야 한다. |
| 작업 완료 확인, 세션 유지 | 책상에서 일어나 공용 공간으로 돌아간다. 펫과 전용 자리는 유지한다. |
| 관측 불가 또는 목록에서 일시 누락 | 마지막 관측을 보존하고 조회 불가를 표시한다. 대기·작업·퇴장 상태를 새로 추정하지 않는다. |
| 세션 종료 확인 | 예약을 정리하고 출입구를 통해 퇴장한다. |

Mascot Behavior State Machine이 전이를 담당하고 Ambient Behavior Scheduler는 결정적 난수 시드를 사용한다. Navigation Grid, Pathfinding, Collision Handling이 이동을 담당한다. Furniture Interaction Points는 접근 위치와 방향을 정의한다. Seat Reservation은 세션과 전용 자리의 연결을, Occupancy Reservation은 공용 좌석과 가구의 동시 사용을 관리한다. Depth Sorting과 Animation Transitions는 가림과 동작 연결을 다룬다. 재현 검증에는 동일 시드, 경로 충돌, 상호작용 도중의 작업 시작, 완료 후 복귀, 연결 끊김과 실제 종료를 포함한다.

## 5. Phase 2.6 Workspace Topology Feasibility Audit

**기존 조사 판정은 `PARTIALLY FEASIBLE`이다.** 별도의 결과 문서는 현재 체크아웃과 로컬 Git 이력에서 찾지 못했다. 기존 인계 §7은 조사 계획이며, 아래 결과는 2026-11-28 Windows 호스트의 읽기 전용 감사 보고를 저장소에 옮긴 것이다.

| 근거 | 기존 관측 | 한계 |
| --- | --- | --- |
| `orca status`, `repo list`, `project list`, `project setups`, `worktree list`, `git worktree list --porcelain` | CLI `1.4.264`, Runtime `ready`·`connected` 및 Runtime의 `appVersion=1.4.264`. Git repo 3개와 Orca Worktree 13개가 보였으며 당시 대상 체크아웃은 Git·Orca 사이에서 경로·브랜치·HEAD가 일치했다. | 한 호스트 표본이며 독립적인 Runtime 빌드 버전은 확인하지 못했다. 과거 macOS `1.4.268` 조회와 별도 근거다. |
| `terminal list --include-visual-layouts`, `agent list`, `agent show`, `worktree ps` | Worktree ID와 identity, 터미널 탭 및 pane 레이아웃, PTY `handle`, `incarnationId`, `worktreeId`, `tabId`, `leafId`를 확인했다. 기존 PTY 세션 ID 3개(Codex 2, Claude 1)가 `paneKey`로 각 pane에 연결됐다. | 전체 구조화 Chat, 모든 Provider 자동 식별, 복수 배치나 재시작·이동·재연결 뒤 세션 동일성, 실제 종료 전이는 미확인이다. |
| 폴링과 상태 | 두 번의 연속 조회에서 ID 집합이 유지됐다. 한 회 약 2.30–2.47초가 걸렸다. Orca가 보고한 `working`과 `done`을 확인했다. | 조회 간 원자적 일관성, 실시간 이벤트의 순서·누락·복구, 작업 시작·완료의 전체 전이와 Thinking·Coding·Building의 세부 상태는 확인하지 못했다. |

로컬 repo·worktree·터미널과 ID가 검증된 기존 PTY 세션은 표시할 수 있다. 외부에 내보낼 때 실제 핸들·경로·계정 식별정보는 제외한다. 폴링은 성공한 빈 목록, 잘린 결과, 실패를 구분한다. 연결이 끊기거나 목록에 항목이 잠시 없으면 그대로 관측 불가로 남긴다. [Orca v1.4.264 CLI 가이드](https://github.com/stablyai/orca/blob/v1.4.264/skill-guides/orca-cli.md) · [Worktree identity 소스](https://github.com/stablyai/orca/blob/v1.4.264/src/shared/worktree/identity.ts) · [세션 목록 소스](https://github.com/stablyai/orca/blob/v1.4.264/src/main/runtime/agent-conversations/agent-conversation-list.ts)

## 6. Visual Few-shot Reference Manifest

참고·제외 열에는 사용자 요청의 디자인 의도와 외부 에셋 사용 범위를 기록했다. 2026-11-29 링크 확인은 페이지 접근과 식별 정보 범위다. 사용자와 이전 대화에서 공유한 원본 이미지는 이 세션에서 접근할 수 없었고, 외부 이미지의 세부 구도와 재배포·재사용 권한은 검증하지 않았다. 제3자 이미지의 출처·권리자·적용 라이선스 또는 승인 범위를 확인하기 전에는 저장소에 복사하거나 제작 에셋에 사용하지 않는다. [기존 참조 기록](../design/orca-studio/references/README.md)

| ID | 참고할 요소 | 제외할 요소 | 확인 범위 및 출처 |
| --- | --- | --- | --- |
| REF-01 | 자연광, 목재, 그림자, 저채도 색감, Cutaway | 침실 배치 | 페이지 확인. [clo · Pixel Art Isometric Room](https://www.buymeacoffee.com/pixitclo/pixel-art-isometric-room-1348291) |
| REF-02 | 중앙 책상 클러스터, 업무 공간, 통로와 확장 | 강한 원색, 기존 인간 캐릭터 | 게시글 확인. [LPZone · Game Dev Story](https://www.lp.zone/t/lets-play-game-dev-story-for-android/1531) |
| REF-03 | 원목 가구, 사무용 의자, 모니터, 수납장, 생활 소품 | 원본 가구 스프라이트의 복제 | `UNVERIFIED`, 접근 응답 403. [Pinterest Pin](https://br.pinterest.com/pin/651825746062625826/) |
| REF-04 | 복수 책상 클러스터, 구역 분리, 공용 통로 | 원작의 배치와 색상 그대로 차용 | 페이지 확인. [Julian Leiss / Indicius · Pixel Office](https://dribbble.com/shots/2848682-Pixel-Office) |
| REF-05 | Clawd 실루엣, 소품 기반 행동 표현 | 문어형 재해석 | `UNVERIFIED`, 접근 응답 403. [X 게시물](https://x.com/oikon48/status/2041811775428161789) |
| REF-06 | 커피머신, 카운터, 접근 지점과 방향 | 카페 전체 배치를 사무실에 복제 | 작품 페이지 확인. [Dulcahn · Pixel Art Coffee Shop](https://www.deviantart.com/dulcahn/art/Pixel-Art-Coffee-Shop-1055619413) |
| REF-07 | 독립 가구 스프라이트와 조립 가능한 공간 구조 | 사용 권한이 확인되지 않은 에셋 사용 | 상품 페이지 확인. [Tarakan4ik · Pixel Art Office Set](https://graphicriver.net/item/pixel-art-office-set/144635) |

## 7. 기술 방향과 승인 순서

Python Core와 기존 TUI를 유지한다. Snapshot v1은 작업·검증·검토·복구의 판정 결과를 공급한다. Read-only Discovery Adapter가 Orca와 Git의 구조를 수집하고, 별도 읽기 전용 **`topology@1` 후보**에 출처와 관측 가능 여부를 담는다. 이름·스키마·배포 방식은 승인 전이다. Frontend Visual State Adapter는 두 데이터의 근거 있는 연결만 사용하고 Core의 판정을 다시 구현하지 않는다.

React·TypeScript가 탐색과 선택 시 열리는 Inspector를, PixiJS가 Room과 마스코트 렌더링을 맡는 방향이다. Dynamic Room Composer와 Station Allocator는 교체 가능한 바닥·벽·가구·조명·캐릭터 모듈 및 점유 정보를 사용한다. Mascot Behavior Engine과 Navigation Engine은 행동과 이동을 맡는다. 사무실을 고정 이미지 한 장으로 구성하는 방식은 최종 구조에서 제외한다. 기존 메타데이터 JSON은 후보 예시로 유지한다. Tauri는 패키징 단계에서 검토한다. [에셋 매니페스트 예시](../design/orca-studio/asset-specification/asset-manifest.example.json) · [배치 예시](../design/orca-studio/asset-specification/scene-layout.example.json) · [애니메이션 예시](../design/orca-studio/asset-specification/animation.example.json)

아래 단계명은 **Orca Studio** 로드맵에 해당한다. [Orchestrator PRD의 Phase 번호](PRD.md#08--구현-로드맵-및-평가)는 별도다.

| 단계 | 상태와 승인 경계 |
| --- | --- |
| 0 · Architecture Audit / 1 · Snapshot Foundation | 완료. Snapshot v1 계약 유지. |
| 2 · 초기 아트 디렉션 / 2.5 · Visual Review | 완료. 기존 시안은 최종 아트 미승인. |
| 2.6 · Workspace Topology Feasibility Audit | `PARTIALLY FEASIBLE`. 검증 범위와 미확인 조건은 §5 참조. |
| Integration Recovery | `PASS`. Windows 기준선 424개 통과. |
| Baseline Finalization | 로컬 통합 병합 `242a7a6` 완료. 원격 `main`에 같은 해시 반영 확인. |
| 2.7 · Workspace Foundation & Cozy Office Design | 사용자 보고 기준 Claude Opus 5.5에 전달되어 진행 중. 구현 결과와 검증 증거는 미확인. |
| 2.8 · Master Concept Art & Production Assets | 계획. 사용자가 Master Concept Art를 승인한 뒤 제작과 권리 검증을 진행한다. |
| 3 · React + PixiJS Dynamic Room Composer | 계획. 승인된 모듈과 fixture에서 빈 방, 2·4·6·8석 이상 확장 및 여러 Worktree 배치를 검증한다. |
| 4 · Navigation / Ambient Behavior / Game Feel | 계획. 결정적 이동·점유·애니메이션과 관측 상태 전이를 검증한다. |
| 5 · Inspector / Camera / Interaction | 계획. 선택 시 상세, Room 탐색, 키보드 접근성 및 관측 시각·미확인 표시를 검증한다. |
| 6 · Live Workspace Integration | 계획. 읽기 전용 계약 승인 후 확인된 범위부터 연결한다. 재연결과 누락 이후 전체 조회로 재동기화하는 동작을 검증한다. |
| 7 · Desktop Packaging | 계획. Tauri 포함 패키징 선택과 배포 범위를 후속 검토한다. |

## 8. 다음 작업의 읽기 순서

1. 이 문서에서 최신 배치·캐릭터·행동 요구사항과 승인 범위를 확인한다.
2. [기존 Studio 인계](ORCA_STUDIO_HANDOFF.md)에서 배경과 이전 조사 계약을 확인한다.
3. [Snapshot v1](snapshot-v1.md), [PRD](PRD.md), [README](../README.md)로 필드와 실행·과금·승인 경계를 대조한다.
4. [Phase 2 아트 명세](../design/orca-studio/art-direction/SPEC.md), [디자인 인덱스](../design/orca-studio/index.html), [기존 참조 기록](../design/orca-studio/references/README.md)을 비교 자료로 사용한다.
5. Phase 2.7 구현 결과가 전달되면 실제 변경 파일·테스트·사용자 승인 여부를 각각 기록한다. 프롬프트 전달 기록은 구현이나 시각 승인 증거로 쓰지 않는다.

## 9. End-of-Day Checkpoint (2026-11-29)

이 절이 오늘 작업의 최신 인계다. §1의 기준선 표는 병합 커밋 전에 쓴 것이며, 아래 내용이 그것을 대신한다.

### 기준선과 브랜치

- **통합 기준선:** `main` = `origin/main` = `242a7a6`
  - origin/main 병합 커밋이며, Windows Python 3.11 기준 424개 테스트가 통과했다.
- **개발 브랜치:** `phase-2.7-living-workspace`
  - 오늘 체크포인트 커밋까지 원격에 일반 push했다.
  - `main`에는 병합하지 않았다.
- **Orca 준비 worktree:** `C:/Users/lee/orca/workspaces/.orca-preparing/…`
  - `242a7a6`의 detached HEAD이며 잠겨 있고 변경이 없다.
  - 별도 아트 브랜치는 아니다. 건드리지 않았다.

### 완료된 작업

| 단계 | 내용 | 주요 경로 |
| --- | --- | --- |
| 2.7 Workspace Topology | 읽기 전용 조회(명령 5개 + Git 1개), `topology@1` 계약, 동일성·생애 주기, `TopologyTracker`, Live·Recorded·Demo, 합성 장면 18개 | `src/orca_orchestrator/topology/`, `tests/test_topology.py`, `tests/fixtures/topology_v1/`, [계약](workspace-topology-v1.md), [조사](workspace-topology-audit.md), [행동](ambient-agent-behavior.md) |
| 2.7 디자인 | 배치·캐릭터·행동 명세, 에셋 매니페스트, 레이아웃 시안 11종 (최종 아트 아님) | `design/orca-studio/living-workspace/` |
| 2.7.1 Topology Preview | 기존 픽셀 마당 부품으로 만든 실시간 사무실 화면 (아래 상세) | `topology/feed.py`, `console/office.py`, `console/office_screen.py`, `tests/test_topology_preview.py` |
| 2.8-A Art Track | 자체 생성 콘셉트 후보 5장 (미승인) | `design/orca-studio/art-track/phase-2.8-a/` |

**2.7.1 Topology Preview 상세**
- Worktree별 사무실, 세션마다 책상과 펫
- 고정 좌석 예약, 주기적 읽기 전용 조회
- 조회는 한 번에 하나만, 화면 밖 스레드에서 돌고 종료 시 정리
- 11단계 DEMO timeline

### 실행 명령

```powershell
.\.venv\Scripts\python.exe -m orca_orchestrator --topology-preview          # Live (읽기 전용)
.\.venv\Scripts\python.exe -m orca_orchestrator --topology-preview --demo   # 합성 DEMO (n: 다음 단계)
.\.venv\Scripts\python.exe -m orca_orchestrator.topology --live --text      # topology@1 요약
.\.venv\Scripts\python.exe -m unittest discover -s tests                    # 전체 회귀
```

### 검증 결과 (시점별로 구분)

| 시점 | 결과 | 출처 |
| --- | --- | --- |
| Integration Baseline (`242a7a6` 커밋 전) | 424개 통과, 297초 | 이 세션의 실행 로그 |
| Phase 2.7 최종 재실행 | 468개 통과, 스킵 0, 307.9초 | 이 세션의 실행 로그 |
| Phase 2.7 Independent Review | 468개 전체 통과 · **Gate BLOCKED** | 사용자 보고. 검수 원문은 이 저장소에 없음 |
| Phase 2.7.1 (venv 3.11.9 + Textual) | 504개 통과, 스킵 1 (Textual 없는 환경 전용 테스트), 329초 | 이 세션의 실행 로그 |
| Phase 2.7.1 (시스템 3.11.9, Textual 없음) | 504개 통과, 스킵 56 | 이 세션의 실행 로그 |
| 신규 Preview 테스트 | 35개 | `tests/test_topology_preview.py` |
| 합성 검증 시나리오 | 14개 (DEMO + 테스트) | 2.7.1 보고 |
| 실제 Live 조회 (8분, 5초 간격) | 요청 97, 시작 96, 중복 방지 1, 반영 95, 실패 0, 종료 시 진행 중 조회 폐기 | 2.7.1 보고 |
| 사용자 확인 | Live Preview에서 기존 Codex 세션과 책상 표시, DEMO 마지막 단계 화면 표시 | 사용자 보고 |
| 실제 세션 추가·작업 상태 전환의 연속 Live 검증 | **미완료** | |
| 실제 세션 종료 판정 (`exitCause`) | **미검증** | |

### 미해결 결함 — Phase 2.7-R (BLOCKED 유지)

- 독립 검수가 찾은 아래 결함은 **완료 증거가 확인될 때까지 미해결**이다.
- 테스트 통과만으로 BLOCKED 판정을 풀지 않는다.
- 각 결함에 대해 Preview에서 따로 처리한 것과 Topology Core에 남은 것을 나눴다.

| 결함 | Topology Core | Preview에서 따로 처리한 것 |
| --- | --- | --- |
| PTY 종료를 Agent Session 종료로 오판 (`exitCause`) | 미해결 | 없음 (Core 판정을 그대로 표시) |
| Worktree 제거를 세션 종료로 오판 | 미해결 | 없음 |
| 다른 호스트의 같은 세션 ID 병합 | 미해결 | 없음 |
| 부분 조회 실패 시 임시 세션 복제 | 미해결 | 없음 |
| 신선도·연결 상태 판단 | 미해결 | 조회 실패 시 마지막 문서 유지와 실패 표시는 Preview에서 따로 처리 |
| 공개 JSON·raw recording 보안 경계 (`--save-raw`) | 미해결 | — |
| 읽기 전용 어댑터 허용 목록 경계 (`OrcaCliAdapter.read`) | 미해결 | — |
| Live/Demo WorkerLink 격리 | 미해결 | — |
| 좌석 예약 유지 | Core 범위 밖 | 처리함: `SeatBook`이 missing·unknown에도 자리를 유지하고, 종료일 때만 비움 |
| 공용 공간 점유 소진 | 설계 도구(`layout.allocate`)에 남음 | Preview는 예약 없이 산책해 소진 문제가 없음. 대신 예약 자체가 미구현 |
| 미상 Provider 표현 | 계약은 `unknown`·`other` | 처리함: 회색 팔레트 교체 펫 |
| 의자에 의한 캐릭터 가림 | 레이아웃 시안 문제 | 처리함: Preview는 펫을 책상 앞에 그려 가림 없음. 시안 이미지는 그대로 |

### 디자인 결정 (미승인)

- 기본 방향은 **Concept C — Walnut Developer Studio**다.
- C v2의 개선된 공간 배치를 참고하되, C의 색과 전문적인 사무실 분위기를 유지한다.
- C v3 요구사항: [art-track README](../design/orca-studio/art-track/phase-2.8-a/README.md)
- Antigravity 이미지 생성은 실제로 성공했고, 이후 `RESOURCE_EXHAUSTED`(429)가 발생했다.
  - 텍스트 모델 할당량과 이미지 생성 가능 여부는 따로 확인한다.
  - 다음 제작은 ChatGPT 등 사용 가능한 환경에서 한다.

### 내일 작업 순서

1. Phase 2.7-R 안전성 복구 (위 결함 표의 Topology Core 항목)
2. Codex 독립 재검수
3. Live Preview에서 실제 세션 추가와 작업 전환을 연속으로 검증 (사용자가 세션 생성)
4. Concept C v3 제작과 사용자 승인
5. 모듈형 환경·가구 에셋 제작
6. Claude·Codex 캐릭터 스프라이트 제작
7. React + TypeScript + PixiJS GUI 구현
8. Navigation·Ambient Behavior Engine

기존 픽셀 마당과 Preview는 기능 검증용으로 유지한다. TUI 그래픽 개선은 우선순위에서 제외한다.
