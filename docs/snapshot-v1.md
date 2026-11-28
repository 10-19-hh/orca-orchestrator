# Snapshot 계약 v1 (`orca-orchestrator/snapshot@1`)

GUI(가칭 Orca Studio)가 기존 판정을 다시 구현하지 않고 상태를 그리도록, 읽기 전용 수집 결과 한 번을 JSON 문서 하나로 내보내는 계약이다. 쓰기·실행 명령은 이 계약에 없다.

- 구현: `src/orca_orchestrator/snapshot_document.py`의 `snapshot_document(snapshot) -> dict` (순수 함수. 파일·프로세스·네트워크·시계를 쓰지 않고 입력을 바꾸지 않으며, 같은 입력에는 같은 문서를 낸다)
- CLI: `python -m orca_orchestrator [--demo [SCENARIO] [--demo-results RESULTS] | --tasks … --results … --repo …] --once --json`
  - stdout에는 JSON 문서 하나만 나온다. 텍스트 `--once`는 그대로다.
  - `--json`은 `--once`와 함께 써야 하고, `--plain`·`--console`과는 함께 쓸 수 없다.
  - 실제 상태는 텍스트 화면과 같은 읽기 전용 수집(`status --json`, 바인딩·연결 시 `run-current`)만 한다. 데모는 아무 프로세스도 띄우지 않는다.
- 고정 예시: `tests/fixtures/snapshot_v1/*.json` (표준 JSON. React에서 바로 import할 수 있다)
  - 다시 만들기: `ORCA_UPDATE_GOLDEN=1 .venv/bin/python -m unittest discover -s tests -p test_snapshot_document.py`. 그다음 diff를 검토한다.

## 문서 구조

| 필드 | 내용 | `source` · `judgment` |
| --- | --- | --- |
| `schema` | `"orca-orchestrator/snapshot@1"`. 필드 의미가 바뀌면 버전을 올린다 | |
| `mode` | `"demo"` 또는 `"live"` | |
| `demo` | 데모면 `{scenario, note}`, 실제면 `null` | |
| `taken_at` | 수집 시각 (ISO 8601 UTC, `Z`) | |
| `runtime` | `health`(`connected`·`disconnected`·`cli_missing`·`timed_out`·`unreadable`), `status`(앱·Runtime·연결 상태, `app_version`, `version_verified`, `connected`), `error`(`category`·`code`만) | live/demo · observed |
| `binding` | `terminal_handle_present` (값은 내보내지 않음) | live/demo · observed |
| `run` | `state`(`ok`·`none`·`skipped_unbound`·`skipped_runtime`·`binding_invalid`·`timed_out`·`unreadable`), `run_id`, `objective`, `error_code` | live/demo · observed |
| `dispatch` | `sends_work: false`, `experiment_4a_status`, Provider별 `outcome`(`BLOCKED`·`WAITING_FOR_RESET`)·`reasons[{code, label}]`·`resume_after` | live/demo · inferred |
| `tasks` | `board_state`, `label`, `error`, `issues`, `tasks[]`(상태·라벨·검증 조건별 `state`·`stale_reason`·기록된 `result`), `requirements[]`, `next`, `blockers[]` | recorded/demo · inferred |
| `flow` | 마지막 흐름 실행 기록(`status`)과 읽기 오류 | recorded/demo · observed |
| `review` | 승인 상태, `approvable`, 코드 기준(digest·HEAD·dirty), 기준 대비 변경, 작업별·연결 없는 변경, 승인 차단 이유, 마지막 결정, 재승인 이유 | recorded/demo · inferred |
| `recovery` | 작업별 재개 판정(`done`·`resumable`·`recheck`·`failed`·`waiting`·`blocked`·`running`·`check_run`), 이유, 다음 행동, 마지막 시도(실행기 종류, 실행별 종료·관측) | recorded/demo · inferred |
| `workers` | `live`: 항상 `not_implemented`, 빈 목록. `recorded`: 실행 기록에 남은 Worker 실행(`agent`, `dispatch_id`, 종료, 마지막 관측) | live: none · recorded: recorded/demo |
| `mascots` | `codex`·`claude` 펫의 이름과 역할(`companion`), `worker: null`, `worker_actions_allowed: false` | 표현 카탈로그 |
| `presentation` | 마당과 같은 `mood`·`mark`·`tag`·`label`·`task_id` (`console.yard.yard_model`), `local_run: {availability: "unknown"}` | 작업 출처 · inferred |
| `requests` | 확인 요청(`console.yard.derive_requests`): `rid`(안정 ID), `kind`, `label`, `priority`(0이 가장 급함), `task_id`, `summary`, 근거가 있는 상세 `tab` | 작업 출처 · inferred |
| `not_included` | v1에 없는 정보와 그 이유 | |

열거형은 코드의 값 그대로다(`tasks.progress.TaskState`, `CheckState`, `tasks.handoff.ResumeState`, `tasks.review.ApprovalState`, `orca.dispatch.Reason` 등). GUI는 이 값을 표시 방식으로만 바꾸고 다시 판정하지 않는다.

## 출처와 관측 가능 여부

- `source`는 사실이 어디서 왔는지다.
  - `live`: 이번 수집에서 Orca CLI가 답함
  - `recorded`: 계획·결과·흐름·승인·실행 기록 파일, 읽기 전용 Git
  - `demo`: 포함된 픽스처. 아무것도 실행되지 않았음
  - `none`: 수집한 것 없음
- `judgment`는 그대로 보고한 값(`observed`)인지, 기존 판정 로직이 계산한 값(`inferred`)인지다.
- `availability`는 그 묶음을 알 수 있는지다.

  | 값 | 뜻 |
  | --- | --- |
  | `available` | 확인됨. 목록이 비어 있으면 "확인했고 없음"(Empty) |
  | `not_configured` | 지정하지 않음 (예: `--tasks` 없음, 저장소 없음) |
  | `unavailable` | 물어봤지만 정하지 못함 (CLI 없음, 파일 읽기 실패, 응답 해석 불가) |
  | `unknown` | 묻지 않았거나 이 문서로는 알 수 없음 (바인딩 없어 Run 조회 안 함, `local_run`) |
  | `not_implemented` | 이 빌드가 관측하지 못함 (실시간 Worker) |

- **낡음(stale)은 출처가 아니라 상태다.** 기록된 결과라도 현재 검증 조건·코드와 digest가 다르면 `checks[].state = "stale"`(+`stale_reason`)이고, 승인 뒤 코드가 바뀌면 `review.state = "reapprove"`다. 이런 결과를 통과로 그리면 안 된다.
- 데모 문서는 모든 묶음의 `source`가 `demo`이고 `mode = "demo"`다. GUI는 데모 문서를 그릴 때 화면 전체에 DEMO 표시를 둔다.

## Worker와 마스코트

- 화면의 Codex 펫·Claude 펫은 **마스코트**다. 펫이 있다는 것이 Worker가 있거나 연결됐다는 뜻은 아니다. `mascots[].worker`는 항상 `null`이다.
- 실시간 Worker는 이 빌드가 관측하지 않는다(`workers.live.availability = "not_implemented"`). 빈 `items`를 "Worker 없음"으로 읽으면 안 된다.
- `workers.recorded`는 실행 기록에 남은 Worker 실행뿐이다. 실제로 기록된 ID만 담고, 가짜 Worker·실행 ID를 만들지 않는다.
- 펫이 타이핑·망치질(`mascots[].worker_actions`)을 할 수 있는 경우는 그 에이전트의 Worker가 실제로 관측됐을 때뿐이다. v1에서는 언제나 금지다(`worker_actions_allowed: false`).

## 상태별 표현 규칙 (TUI와 GUI 공통)

| 실제 상황 | 문서에서 알아보는 법 | 허용 | 금지 |
| --- | --- | --- | --- |
| 대기·준비 | `mood` `idle`·`ready` | 휴식, 눈 깜빡임, 가벼운 이동 | 작업 진행 표시 (`ready`는 실행 시작이 아님) |
| 로컬 검증 | 문서에 없음 (`local_run` unknown). TUI는 자기 실행 이벤트로만 안다 | 펫이 모니터를 지켜봄 | 펫이 코딩·빌드함 |
| 다른 세션 실행 | `recovery.tasks[].state = "running"` (데모에는 없음) | 외부 실행 안내, 제자리 대기 | 실행 주체를 Codex·Claude로 추정 |
| Worker 작업 관측 | 해당 없음 (not_implemented) | (향후) 그 에이전트 펫만 작업 동작 | 관측되지 않은 펫의 작업 동작 |
| 검증 실패 | `mood` `failed`, 요청 `check_failed` | 오류·주의 표시 | 성공 표현 |
| 검증 통과 | `mood` `passed`·`review`·`approved` | 검증 결과의 성공 표시 | 프로젝트 전체 완료 표현 (`approved`만 사람의 최종 승인) |
| 종료 미확인 | `mood` `end_unknown`, 요청 `end_unknown`(우선순위 0) | `?`, 확인 필요, 대기 | 종료됐다거나 다시 실행할 수 있다고 단정 |
| 연결 끊김 | `runtime.health` ≠ `connected`, `availability` | 연결 상태, 관측 불가 표시 | 에이전트 작업 여부 단정 |
| 모의 장면 | `mode = "demo"` | 분명한 DEMO 표시 | 실제 Worker가 실행 중이라는 표현 |

## 고정 예시 (`tests/fixtures/snapshot_v1/`)

모두 데모 픽스처에서 2026-11-28T06:00:00Z에 만든 문서다(`--demo SCENARIO --demo-results RESULTS`와 같은 입력).

| 파일 | 장면 | 주요 상태 | GUI 확인 목적 |
| --- | --- | --- | --- |
| `connected-no-tasks.json` | 연결, 작업 목록 없음 | `tasks` not_configured, mood `idle` | 대기 장면, 빈 상태와 미지정 구분 |
| `connected-none.json` | 검증 결과 없음 | T-1 `ready`, 요청 `check_missing` | 준비 ≠ 실행 |
| `connected-passed.json` | 선행 작업 통과 | T-1 `verified`, T-2 `ready` | 부분 진행 |
| `connected-failed.json` | 선행 작업 실패 | mood `failed`, 요청 `check_failed`, 흐름 정지 기록 | 실패 표현 |
| `connected-protected.json` | 모두 통과 + 보호 경로 변경 | mood `review`, 승인 차단 | 검토 필요 |
| `connected-approved.json` | 사용자 승인 | mood `approved`, 요청 없음 | 최종 승인 표현 |
| `connected-changed.json` | 승인 뒤 코드 변경 | 검증 조건 `stale`, `review` `reapprove` | 낡은 결과를 성공으로 그리지 않기 |
| `connected-interrupted.json` | 로컬 실행 중 중단 | mood `end_unknown`, `recovery` `check_run` | 종료 미확인 (로컬) |
| `connected-worker.json` | Worker 실행 종료 미확인 | mood `end_unknown`, `workers.recorded` 1건 | 기록된 Worker와 마스코트 구분 |
| `disconnected-passed.json` | Runtime 연결 안 됨 | `runtime.health` `disconnected`, Run 조회 안 함 | 연결 끊김 |
| `unbound-passed.json` | 바인딩 없음 | `binding` false, `run` unknown | 알 수 없음 표시 |
| `invalid-passed.json` | Run 응답 해석 불가 | `run` `unreadable`·unavailable | 조회 불가 표시 |

"로컬 검증 실행 중"과 "다른 세션 실행 중"은 고정 예시가 없다. 로컬 실행은 실행한 콘솔의 메모리에만 있고, 데모 실행 기록은 살아 있는 세션을 만들지 않기 때문이다. 이 두 장면은 GUI의 표현 상태로만 시험하고, 가짜 Snapshot을 만들지 않는다.

## 내보내지 않는 것 (데이터 최소화)

- 필드는 이름으로 골라 담는다(허용 목록). dataclass를 통째로 직렬화하지 않는다.
- 다음은 내보내지 않는다:
  - 환경변수 값과 이름 (API 키 존재는 `dispatch` 사유 코드로만 알린다)
  - 터미널 핸들 값과 CLI 경로
  - Orca·Git 오류 원문과 argv
  - 이 호스트의 프로세스 ID(`owner_pid`)
  - 검증 출력(`evidence`)
  - 계획의 `command` 문자열
  - 확인 메모(`resolved_note`)
  - 보류 사유의 상세 문장(`detail`)
- 경로는 저장소 기준 상대 경로와 파일 이름만 쓴다. 그대로 남는 것은 실행 ID(`pid:<n>` 같은 실행기 ID, `dispatch:<id>`)와 세션 ID다. 사람이 종료를 확인하는 데 필요하기 때문이다.

## v1에 없는 정보

| 필드 | 이유 |
| --- | --- |
| `local_run` | 이 화면에서 시작한 로컬 검증은 그 콘솔 프로세스의 메모리에만 있다 |
| `action_progress` | 검증·흐름 진행 이벤트는 실행한 콘솔에만 전달된다 |
| `request_read_state` | 읽음 표시는 화면마다 다른 클라이언트 상태다 |
| `mascot_pose` | 펫 좌표·프레임·카메라·선택은 클라이언트의 표현 상태다 |
| `live_worker_activity` | 이 빌드는 Worker를 실시간으로 관측하지 않는다 |

또 없는 것:
- 사용량 한도 대기의 실제 관측. `UsageLimitTracker`는 실제 경로에서 기록되지 않는다.
- 이벤트 이력. 마지막 흐름 이벤트와 실행 기록만 있다.

## GUI에서 재사용하는 표현 모델

- `presentation`과 `requests`는 TUI 마당과 같은 함수의 결과다.
  - GUI는 `requests[].rid`로 읽음 상태를 클라이언트에 따로 둔다(TUI의 `RequestBook`과 같은 방식). 읽음은 해결이 아니다.
  - 해결 여부는 다음 문서에서 그 요청이 사라졌는지로만 판단한다.
- 펫의 위치·움직임·프레임은 GUI가 `mood`를 보고 정한다. 규칙은 위 표를 따르고, 문서에 없는 상태를 지어내지 않는다.
- 펫 이름(`Codex 펫`·`Claude 펫`)과 정체성은 `mascots`에 있다. 모습과 애니메이션은 GUI 쪽에서 자유롭게 다시 만들 수 있다.

## 향후 연결 계획 (미구현)

- 다음 단계의 GUI는 고정 예시 JSON으로 시작한다.
- 그 뒤 같은 문서를 주는 읽기 전용 로컬 API를 붙인다.
  - 형태: 127.0.0.1, 최초 전체 문서, 변경 시 전체 문서를 다시 보냄
  - 진실 공급원은 기존 파일과 Orca CLI다
  - 명령(검증·흐름·승인)은 이 계약과 분리해 별도 단계에서 설계한다
- HTTP·SSE·WebSocket은 아직 없다.
