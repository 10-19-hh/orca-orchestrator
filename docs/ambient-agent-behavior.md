# Ambient Agent Behavior — 관측에서 펫 행동까지

Orca Studio의 펫은 사무실에서 산다. 일이 없으면 탕비실·라운지에 있고, 작업이 관측되면 자기 책상으로 간다. 종료가 확인되면 출입구로 나간다. 이 문서는 그 행동이 **무엇을 근거로** 정해지는지 정한다. 애니메이션·좌표·타이밍은 [BEHAVIOR_SPEC](../design/orca-studio/living-workspace/BEHAVIOR_SPEC.md)에 있다.

## 1. 세 층

```text
topology@1 (Python 판정)          Visual State Adapter (Phase 3, TS)       Mascot Behavior (Phase 4, TS)
agent_sessions[].lifecycle   ─►   presentation.intent를 그대로 받음   ─►   행동 상태 머신 + 길찾기 + 예약
                activity                (다시 판정하지 않음)                 ambient 선택은 결정적 시드
                connection
                worker (연결 근거가 있을 때)
```

- **판정은 Python이 한다.** `presentation.intent`는 [계약](workspace-topology-v1.md#장면-의도-presentationintent--위에서부터-처음-맞는-것)의 7단계 우선순위로 정해진다. 화면은 이 값을 바꾸지 않는다.
- **Ambient 행동은 연출이다.** 펫이 커피를 마셔도 그 AI가 쉬고 있다는 뜻이 아니다. 두 펫이 회의 테이블에 앉아도 서로 대화한다는 뜻이 아니다. 화면 도움말에 이 점을 적는다.
- **작업 세부 동작은 근거가 있을 때만 쓴다.** `activity_detail`이 생기기 전까지 Typing·Thinking·Building은 재생하지 않는다. 일하는 펫은 일반 Working 자세를 쓴다.

## 2. 행동 상태 머신

논리 상태(`intent`)와 시각 상태를 나눈다. 같은 `intent`라도 펫이 어디에 있었는지에 따라 이동이 끼어든다.

```text
            (presentation.new)
 SPAWN ──► WALK_TO_TARGET ──┬──► AMBIENT ◄────────────────────────────┐
                            │      │  intent work                    │
                            │      ▼                                 │
                            └──► WALK_TO_DESK ──► SIT_DOWN ──► WORKING
                                                    │            │ intent work_done
                                                    │            ▼
                                                    │        STAND_UP ──► WALK_TO_TARGET
                                                    │
 WORKING ──► WAITING_FOR_INPUT   (intent attention: 자기 책상, '!')
 WORKING ──► FINISHING           (intent finishing: 자리에서 '...')
 ANY ──────► OBSERVATION_UNKNOWN (intent unobservable: 지금 자리에서 멈춤, 흐리게, '?')
 ANY ──────► WALK_TO_EXIT ──► DESPAWN   (intent exit: 종료 확인 근거가 있을 때만)
```

| 시각 상태 | 들어가는 조건 | 하는 일 | 나가는 조건 |
| --- | --- | --- | --- |
| `SPAWN` | 세션이 처음 관측됨(`presentation.new`) | 출입구에서 등장 | 즉시 목표로 이동 |
| `AMBIENT` | `ambient` | 공용 공간의 상호작용 지점 하나를 예약해 사용 (§4) | 다른 intent |
| `WALK_TO_DESK` → `SIT_DOWN` → `WORKING` | `work`, `attention`, `finishing` | 사용 중인 지점의 예약을 풀고 자기 책상으로 이동해 앉음 | `work_done`, `ambient` |
| `WAITING_FOR_INPUT` | `attention` | 자기 책상에서 `!`. 사용자가 그 에이전트의 터미널에서 답해야 함 | 다른 intent |
| `FINISHING` | `finishing` | 자리에서 `...`. 터미널 정리를 기다림 | 다른 intent |
| `STAND_UP` | `work_done` | 일어나서 짧게 기뻐함 | 끝나면 `AMBIENT` 목표로 이동 |
| `OBSERVATION_UNKNOWN` | `unobservable` | 그 자리에서 멈추고 흐리게 표시. 자리 예약 유지 | 다시 관측됨 |
| `WALK_TO_EXIT` → `DESPAWN` | `exit` | 예약을 모두 풀고 출입구로 감 | 사라짐(자리는 비게 됨) |

## 3. 우선순위와 중단

행동 우선순위는 계약의 intent 순서와 같다.

1. 확인된 종료
2. 사용자 확인
3. 관측 불가·연결 끊김
4. Worker 정리 중
5. 작업
6. 작업 완료
7. Ambient

- 새 문서의 intent가 지금 행동보다 우선하면 이동·애니메이션을 안전하게 끊는다. 지금 프레임을 마치고 상호작용 예약을 푼 뒤 새 목표로 간다.
- `work`가 오면 커피·산책·낮잠은 바로 중단한다. 펫은 자기 책상으로 간다.
- `unobservable`은 대기(`ambient`)가 아니다.
  - 펫을 휴식 공간으로 보내지 않는다.
  - 작업 중이었다고 표현하지도 않는다.
  - 그 자리에서 중립 자세로 `?`를 표시한다.
- **연결 끊김과 종료를 구분한다.** 끊김은 `unobservable`이고 자리와 펫을 유지한다. 퇴장은 `exit`(명시적 종료 근거)일 때만 한다.
- 목록에서 잠시 빠진 세션(`missing`)도 `unobservable`이다. 같은 ID로 돌아오면(`reappeared`) 같은 펫이 같은 자리를 이어 쓴다.
- ID 없는 세션이 재연결 뒤 새 임시 ID로 보이면(`possible_predecessor`), 새 펫을 만들고 이전 펫은 `?`로 남긴다. 둘을 합치지 않는다. 사용자가 Inspector에서 관계를 볼 수 있다.

## 4. 공용 공간과 예약

- **자리 예약 (Seat Reservation)**
  - 세션은 처음 관측된 순서(`arrival`)대로 가장 낮은 빈 슬롯을 받는다.
  - 받은 자리는 세션이 끝날 때까지 그 세션의 것이다.
  - 펫이 탕비실에 있어도 책상은 비어 있는 자기 자리로 남는다. 모니터는 꺼지고 의자는 그대로다.
- **점유 예약 (Occupancy Reservation)**
  - 커피머신·소파 자리·회의 의자처럼 공용 상호작용 지점은 펫 하나만 쓴다.
  - 예약이 찬 지점은 고르지 않는다.
- **결정적 시드**
  - Ambient 선택은 탕비실·라운지 지점을 먼저 쓰고, 다 차면 회의·창가·책장 같은 나머지 공용 지점을 쓴다. 시작 위치는 `session_id`의 해시로 정한다.
  - 같은 Provider의 펫도 시드가 달라 다른 곳에 간다.
  - 같은 문서 열과 시드면 같은 장면이 나온다(테스트 재현).
- Ambient 행동의 지속 시간과 다음 선택도 시드와 경과 시간으로 정한다. 실제 시간 대신 주입한 시계를 쓴다.

## 5. Worker 표현

- Worker 기록(`snapshot.workers.recorded`)과 세션 펫을 잇는 Orca 필드는 1.4.264에 없다. 실제 조회에서 `agent_sessions[].worker`는 항상 `null`이다.
- 연결 근거가 없으면 Worker 상태를 펫에 붙이지 않는다. 공용 검토 보드(Review 구역)에 표시한다.
  - 정리 중: "Worker 작업 완료·터미널 정리 중"
  - 종료: "종료 확인"
- `FINISHING` 행동은 DEMO `worker-finishing`에서만 쓰인다. 그 연결이 합성(`basis = demo_synthetic`)임을 화면에 표시한다.
- Worker의 `succeeded`는 작업 검증이나 최종 승인이 아니다. 검증 장치와 승인 표시는 Snapshot의 작업·검토 상태를 따른다.

## 6. 검증 장치와 확인 요청

- **로컬 검증**은 검증 장치(Review 구역의 장치)가 수행·결과를 보여 준다. 펫이 코딩·빌드하는 것으로 바꾸지 않는다([Snapshot 규칙](snapshot-v1.md#상태별-표현-규칙-tui와-gui-공통)).
- **확인 요청**은 해당 세션의 `attention`이면 그 펫의 책상에 표시한다. Snapshot `requests`처럼 세션과 이을 근거가 없으면 상태 보드에 표시한다.

## 7. 테스트로 고정한 것 (Python)

- `tests/test_topology.py`
  - `RuleTest.test_intent_priority`: 7단계 우선순위와 경계 사례
  - `RuleTest.test_work_done_is_shown_for_one_observation`: `work` → `work_done` → `ambient`
  - `SceneMeaningTest`: 동일 Provider 분리, 재연결 미합치, 중복 하나로, 끊김 시 자리 유지, 조회 실패 후 마지막 관측
- 화면 쪽 상태 머신·길찾기·예약 테스트는 Phase 4에서 TypeScript로 만든다. 그때 같은 고정 예시와 시드를 쓴다([BEHAVIOR_SPEC](../design/orca-studio/living-workspace/BEHAVIOR_SPEC.md#phase-4-검증-시나리오)).
