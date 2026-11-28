# Behavior Spec — 행동 상태, 이동, 상호작용

판정 규칙(무엇을 근거로 어떤 의도가 되는가)은 [Ambient Agent Behavior](../../../docs/ambient-agent-behavior.md)에 있다. 이 문서는 그 의도를 **어떻게 보여 줄지**를 정한다. 구현은 Phase 4(TypeScript)다. Python은 의도까지만 판정한다.

## 1. 의도 → 행동

| `intent` | 목표 위치 | 도착 후 | 끼어들기 |
| --- | --- | --- | --- |
| `exit` | `exit` 지점 | 문턱에서 6프레임 퇴장, 자리 반납 | 다른 모든 행동을 즉시 중단 |
| `attention` | 자기 자리 | 앉아서 `notification` 루프, `!` | `unobservable`·`finishing`·`work`·ambient보다 우선 |
| `unobservable` | **지금 위치** (이동 중이면 가장 가까운 걸을 수 있는 칸) | `unknown` 루프, 흐리게, `?` | 이동을 멈춤. 휴식 공간으로 보내지 않음 |
| `finishing` | 자기 자리 | 앉아서 `finishing` 루프, `...` | |
| `work` | 자기 자리 | `sit_down` → `work` 루프 (일반 자세) | ambient 지점 예약 해제 |
| `work_done` | 자리 옆 통로 칸 | `stand_up` → `happy`(1회) → ambient 선택 | |
| `ambient` | 시드로 고른 공용 지점 (§3) | 지점 종류별 애니메이션 | 체류 시간이 끝나면 다음 지점 |
| `new` (표시) | `spawn`에서 등장 | 바로 목표로 이동 | |

- 같은 의도가 계속 오면 행동을 다시 시작하지 않는다.
- 의도가 바뀌면 지금 프레임을 끝내고 전환한다. 최대 1프레임(≈140 ms)을 기다린다.

## 2. 이동

- **격자**: 1/4 타일(`layout-fixtures/*.json`의 `nav_grid`)
  - `#`: 가구 footprint
  - 의자·스툴·러그·문 매트는 걸을 수 있다.
- **길찾기**: 4방향 BFS가 기준이다(모든 고정 예시에서 도달 가능 확인). Phase 4에서 A*와 경로 다듬기로 바꿔도 결과 칸 집합은 같아야 한다.
- **속도**: 1.5 타일/초(걷기 4프레임 × 140 ms). 화면 밖으로 나가지 않는다.
- **충돌**: 펫끼리는 서로를 통과하지 않고, 마주치면 한쪽이 0.5초 기다린다. 기다림이 3번 반복되면 경로를 다시 찾는다.
- **취소**: 새 의도가 오면 지금 칸에서 새 경로를 찾는다. 상호작용 지점 예약은 도착 전에 풀 수 있다.
- **깊이 정렬**: 발 위치의 `x + y`로 정렬한다. 가구는 footprint 중심으로 정렬한다.
  - 소파는 앉은 펫이 그 위에 그려지도록 모서리 기준이다.
  - 의자는 좌석과 등판을 나눠, 앉은 펫이 둘 사이에 그려진다.

## 3. 공용 공간 사용 (Ambient)

| 지점 종류 | 애니메이션 | 체류 | 정원 |
| --- | --- | --- | --- |
| `coffee` · `counter` | `coffee` · `eat` | 8–20초 | 1 |
| `sofa_sit` | `sit_sofa` | 15–40초 | 1 |
| `sofa_lie` | `sleep` (`Z`) | 20–60초 | 1 (소파 앉기 2자리와 동시 사용 불가) |
| `meeting_seat` | 앉기, 둘이면 `chat` | 15–30초 | 1 |
| `window-view` · `bookshelf-view` · `review_view` | `look_around` | 6–12초 | 1 |
| 산책 (빈 통로 칸) | `walk` + `stretch` / `sweep` | 6–15초 | — |

- **선택** (`tools/layout.py`의 `allocate`)
  - 탕비실·라운지 지점(`coffee, sofa-1, sofa-lie, counter, sofa-2`)을 먼저 쓴다. 이 목록 안의 시작점을 `sha256(session_id)`로 정한다.
  - 이 지점들이 다 차면 나머지 공용 지점(`meeting-1, window-view, meeting-3, review-view, bookshelf-view, meeting-2, meeting-4`)을 차례로 쓴다.
  - 예약되지 않은 첫 지점을 고른다. 낮잠(`sofa-lie`)은 소파 전체를 차지한다.
- **체류 시간과 다음 지점**: `seed + 방문 횟수`로 정한다. 같은 문서 열과 주입한 시계에서 같은 장면이 나와야 한다.
- 탕비실·라운지 지점이 모두 찼으면 산책한다.
- `review_view`에서는 펫이 상태 보드를 바라본다. 검증을 수행하는 것이 아니다.

## 4. 그리기 층

```text
배경 → 바닥(마루·두께띠) → 바닥 장식(러그·문 매트) → 햇빛 틴트 → 접촉 그림자
→ 벽·벽 장식(창문·화이트보드·상태 보드·문) → 공기 빛기둥
→ 깊이 정렬된 가구·의자·펫 → 말풍선 → UI(상단 바·Inspector)
```

## 5. UX — Inspector

- 기본 화면에서는 사무실이 주인공이다. 상단 바 한 줄에 다음만 둔다(`mockups/ux-inspector-closed`).
  - 저장소/Worktree 선택
  - 연결 상태
  - 세션 수, 작업 중, 확인 필요, 관측 불가
- **펫 클릭**: Agent Inspector를 오른쪽 패널로 연다(`mockups/ux-inspector-open`).
  - Provider, 세션 ID와 동일성 근거, Orca 상태와 출처, 관측 시각과 신선도
  - 자리, 세부 작업(관측 불가), Worker 연결 여부
  - 확인이 필요하면 "그 에이전트의 터미널에서 답하기" 안내. 이 화면에서 대신 답하거나 명령을 보내는 버튼은 없다.
- **책상 클릭**: 그 자리의 세션 상세를 연다. 빈자리는 "빈자리 — 새 세션이 오면 배정"을 보여 준다.
- **Worktree 선택**: 카메라가 그 방으로 이동한다.
- **상태 보드·리뷰 알림 클릭**: 관련 작업과 검토(Snapshot `requests`, `review`)를 연다.
- **연결 표시 클릭**: 관측 진단을 연다(`topology@1` `sources`·`diagnostics`, Snapshot `runtime`).
- 닫기는 `Esc`, 패널의 `X`, 빈 바닥 클릭이다. Inspector가 열려도 사무실 움직임은 계속된다.
- 후속 검토 항목: 미니 모드, 항상 위, 작은 사무실 보기, 여러 모니터, Tauri 패키징

## Phase 4 검증 시나리오

같은 고정 예시(`tests/fixtures/topology_v1/`)와 시드로 다음을 확인한다.

1. 같은 시드 → 같은 경로·같은 지점·같은 프레임 순서
2. 두 펫이 같은 지점을 고르려 할 때 하나만 예약
3. Ambient 도중 `work` → 즉시 중단, 예약 해제, 자기 책상 도착 후 앉기
4. `work` → `work_done` → ambient 복귀, 자리는 유지
5. `disconnected`: 모든 펫이 제자리에서 `?`, 아무도 퇴장하지 않음
6. `session-ended`: 그 펫만 문으로 퇴장, 자리 반납
7. `reconnect-unconfirmed`: 이전 임시 세션 펫은 `?`로 남고 새 펫이 문에서 등장
8. `duplicate-entity`: 펫 하나
9. `eight-sessions`: 경로 막힘 없음, 걷기 충돌 해소
10. reduced motion: 이동을 순간이동과 페이드로 바꿔도 의도와 자리는 같음
