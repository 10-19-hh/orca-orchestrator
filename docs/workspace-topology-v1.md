# Workspace Topology 계약 v1 (`orca-orchestrator/topology@1`)

이 계약은 Orca와 Git이 보고하는 개발 환경의 구조를 읽기 전용 JSON 문서 하나로 내보낸다. 대상은 저장소, Worktree, 탭, 터미널 패널, 에이전트 세션이다. Orca Studio의 방·자리·펫이 이 문서를 근거로 생긴다.

이 계약은 작업·검증·검토·복구 판정을 다루는 [`snapshot@1`](snapshot-v1.md)과 별도다. 두 문서는 근거 있는 식별자로만 잇는다. 현재 둘을 잇는 Orca 필드는 없어서 Worker 연결은 `not_implemented`다.

- 상태: **후보 v1, Phase 2.7 구현.** 이름·필드는 사용자 검토 전이며 화면이나 콘솔은 아직 이 문서를 쓰지 않는다.
- 구현: `src/orca_orchestrator/topology/`
  - `parse.py`: 원본 응답 → 관측
  - `tracker.py`: 관측 사이의 동일성·생애 주기, 장면 의도
  - `document.py`: JSON 투영 (순수 함수)
  - `discovery.py`: 읽기 전용 조회
  - `scenarios.py`: 합성 DEMO 장면
- CLI: `python -m orca_orchestrator.topology`
  - `--demo SCENE`, `--recorded FILE`, `--live` 가운데 하나를 고른다.
  - 선택 인자: `--text`, `--step N`, `--no-git`, `--save-raw FILE`
- 고정 예시: `tests/fixtures/topology_v1/*.json` (18개 장면). 각 파일은 그 장면의 단계별 문서 배열이다.
  - 다시 만들기: `ORCA_UPDATE_GOLDEN=1 python -m unittest discover -s tests -p test_topology.py`
- 실제 Orca에서 무엇을 확인했는지는 [조사 보고](workspace-topology-audit.md)에 있다. 장면 의도를 펫 행동으로 옮기는 규칙은 [Ambient Agent Behavior](ambient-agent-behavior.md)에 있다.

## 데이터 흐름

```text
Orca CLI (status · repo list · worktree list · terminal list --include-visual-layouts · agent list)
Git (git worktree list --porcelain)
        │  topology.discovery — 허용 목록 밖 명령은 실행 전에 거부
        ▼
RawObservation (원본 응답, 비공개)
        │  topology.parse — 관측된 응답 형태만 받아들임
        ▼
Observation ──► TopologyTracker (이전 관측을 기억: 동일성, 누락, 종료 근거, 의도)
                        │
                        ▼
                 topology_document() — 허용 목록 필드만, 경로·핸들·제목·대화 없음
                        │
                        ▼
                 topology@1 JSON ──► (Phase 3) Visual State Adapter → Room Composer → PixiJS
```

`--live`는 한 번 조회해 문서 하나를 낸다. 이전 관측과 비교하는 규칙(누락, 재등장, 프로세스 교체, `work_done`)은 같은 tracker로 여러 번 관측할 때만 적용된다. 반복 조회와 로컬 API 연결은 Phase 6에서 한다.

## 문서 구조

| 필드 | 내용 |
| --- | --- |
| `schema` | `"orca-orchestrator/topology@1"` |
| `mode` | `live`(이번에 조회), `recorded`(저장한 원본 응답 재생), `demo`(합성 장면) |
| `demo` | 데모면 `{scenario, label, step, steps, evidence, note}`, 아니면 `null` |
| `observed_at` · `sequence` | 이 관측의 시각(UTC `Z`)과 tracker 안의 순번 |
| `runtime` | `health`(`connected`·`disconnected`·`cli_missing`·`timed_out`·`unreadable`·`unknown`), `connected`, `app_version`, `version_verified`(1.4.264), `error{category, code}` |
| `capabilities[]` | 기능별 `evidence` 등급(조사 보고 기준), 이번 관측의 `availability`, 근거 요약 |
| `sources{}` | `repositories`·`worktrees`·`terminals`·`tabs`·`agent_sessions`·`git`별 `availability`, `error`, `truncated`, `omitted_hosts`, `invalid_items` |
| `summary` | `rooms`, `sessions`(종료 제외), `working`(`work`+`finishing`), `attention`, `unobservable`, `ended` |
| `repositories[]` | `repo_id`, `display_name`, `kind`, `listed`, 처음·마지막 확인 시각 |
| `worktrees[]` | 아래 표 참고 |
| `tabs[]` | `tab_id`, `worktree_id`, `group_id`, `active`, `listed`, `panes`(분할 트리: `pane`/`split`) |
| `terminals[]` | 아래 표 참고 |
| `agent_sessions[]` | 아래 표 참고 |
| `diagnostics[]` | 사용 가능(`available`)이 아닌 출처의 `category`·`code`·잘림 정보, 연결 대상을 찾지 못한 참조 수 |
| `provenance` | `source`, `answered`(응답한 출처), `synthetic`, `sends_commands: false`, 계약 근거 |
| `not_included[]` | 일부러 내보내지 않는 정보와 이유 |

### `worktrees[]` — 방 하나

| 필드 | 의미 |
| --- | --- |
| `worktree_id` | Orca의 `identity.key`(`wt2:<host>:<instanceId>`). 경로가 들어 있지 않고 폴더를 옮겨도 유지된다. 이 키가 없는 호스트에서는 locator 해시 `loc:<16 hex>`를 쓴다. 이때 `identity_basis = locator_hash`이며 폴더를 옮기면 값이 바뀐다. |
| `repo_id` · `display_name` · `branch` · `is_main` · `archived` · `host` | Orca 응답 값. `branch`는 `refs/heads/`를 뗀 이름이다. |
| `lifecycle` | `active` · `missing`(Orca 목록에서 빠짐) · `removed`(Orca와 Git 모두에서 없어짐, 또는 Git이 `prunable` 표시) · `unknown` |
| `git` | `listed`, `prunable`, `branch_matches`. Git 교차 확인 결과이며 해당 저장소를 조회하지 못하면 `null`이다. |
| `arrival` | 처음 관측된 순서. 방 배치를 안정적으로 유지하는 데 쓴다. |
| `room.state` | `open` · `unobservable` · `closing`(삭제 확인) |

### `terminals[]` — 패널 하나

| 필드 | 의미 |
| --- | --- |
| `pane_key` | `<tabId>:<leafId>`. Orca가 에이전트 행과 패널을 잇는 키 |
| `listed` · `connected` · `orphaned` | 목록 포함 여부와 PTY 연결 |
| `incarnation_id` · `incarnation_changed` | 그 패널에서 실행 중인 프로세스 세대. 바뀌었으면 새 프로세스다. |
| `agent_identity` | 호스트가 본 **에이전트 종류**. 세션 식별이 아니며 같은 종류의 패널 둘이 같은 값을 갖는다. |
| `exit_reported` · `exit_cause` | Orca가 `exitCause`를 보고했는지. 1.4.264 계약에만 있고 실제로는 관측하지 못했다. |
| `role` | `agent_session`(세션이 열려 있음) 또는 `terminal_device`(일반 터미널) |
| `session_ids` | 이 패널에 열린 세션 |

### `agent_sessions[]` — 자리와 펫 하나

| 필드 | 의미 |
| --- | --- |
| `session_id` · `identity` | Orca `orcaSessionId`면 `basis = orca_session_id`, `confirmed = true`다. ID가 아직 없으면 임시 ID `prov:<16 hex>`를 쓴다. 이때 `basis`는 `pane_incarnation`(패널과 프로세스 세대) 또는 `pane_only`이고 `confirmed = false`다. |
| `kind` | `terminal` · `chat` |
| `provider` · `provider_basis` · `agent_type` | `claude`·`codex`·`other`·`unknown`과 근거. 근거는 `orca_agent_type`(agent 행) 또는 `terminal_agent_identity`(패널의 종류 힌트), 없으면 `none`. 제목은 쓰지 않는다. |
| `places[]` | 열린 패널(`pane_key`, `tab_id`, `incarnation_id`). 두 패널에서 재개된 세션은 두 곳이다. |
| `lifecycle` | `active` · `missing` · `ended` · `unknown` (아래 규칙) |
| `end_evidence` · `ended_at` | `terminal_exit` 또는 `worktree_removed`. `ended`일 때만 값이 있다. |
| `activity` · `last_activity` | Orca가 보고한 `working`·`blocked`·`waiting`·`done`. 이번에 관측하지 못하면 `unknown`이고 마지막 값은 `last_activity`에 남는다. |
| `activity_detail` | 항상 `null`. Thinking·Coding·Building을 구분하는 필드가 없다. |
| `connection` | 패널 PTY와 Runtime 연결: `connected` · `disconnected` · `unknown` |
| `freshness` | `first_seen_at`, `last_seen_at`, `observed_now` |
| `arrival` | Worktree 안에서 처음 관측된 순서. Station Allocator가 자리를 고정하는 데 쓴다. |
| `reappeared` · `incarnation_changed` · `possible_predecessor` | 누락 후 같은 ID 재등장, 같은 ID의 프로세스 교체, 동일성을 확인하지 못한 이전 임시 세션 |
| `worker` | Worker Dispatch 연결. 1.4.264에는 연결 필드가 없어 실제 조회에서는 항상 `null`이다. DEMO `worker-finishing`만 `basis = demo_synthetic`으로 채운다. |
| `presentation` | `intent`(아래 우선순위)와 `new`(이번에 처음 관측됨) |

## 동일성과 생애 주기 규칙

1. 세션의 동일성은 Orca `orcaSessionId`다. 같은 ID의 행이 여러 개면 펫 하나에 `places`가 여럿이다.
2. 같은 Provider의 세션은 Provider로 합치지 않는다. Codex 셋이면 자리와 펫이 셋이다.
3. 에이전트 행이 없는 패널은 `terminal_device`다. `agentIdentity`가 있어도 세션을 만들지 않는다. 탭·패널 제목은 내보내지도 판정에 쓰지도 않는다.
4. ID 없는 행의 임시 ID에는 패널의 프로세스 세대가 들어간다. 같은 패널에 새 세대가 오면 새 임시 세션이 된다. 이전 세션과 합치지 않고 `possible_predecessor`로만 남긴다.
5. 같은 `orcaSessionId`가 누락이나 재연결 뒤 다시 보이면 같은 세션이다(`reappeared`). 프로세스 세대가 바뀌었으면 `incarnation_changed`를 표시한다.
6. 완전한 목록에서 빠지면 `missing`, 실패하거나 잘린 목록에서 빠지면 `unknown`이다. 둘 다 종료가 아니며 자리와 펫을 유지한다.
7. `ended`에는 명시적 근거가 필요하다.
   - 세션이 열려 있던 모든 패널이 같은 프로세스 세대로 `exitCause`를 보고하거나,
   - 그 Worktree가 삭제로 확인돼야 한다(Orca와 Git 모두에서 없어지거나 Git이 `prunable` 표시).
8. Runtime 연결이 끊기면 목록 명령을 묻지 않는다. 모든 출처는 `unknown`, 모든 세션은 `unknown`·`disconnected`가 되고 자리는 유지된다.
9. 종료된 세션은 기본 10분 동안 문서에 남는다. 화면이 퇴장 연출을 놓치지 않게 하기 위해서다.
10. 이름이나 목록 순서로 ID를 만들지 않는다. 방 배치는 `arrival`로 안정적으로 유지한다.

## 장면 의도 (`presentation.intent`) — 위에서부터 처음 맞는 것

| 순위 | 의도 | 조건 | 화면 |
| --- | --- | --- | --- |
| 1 | `exit` | `lifecycle = ended` | 출입구로 퇴장 |
| 2 | `attention` | 이번 관측에서 `activity`가 `blocked`·`waiting` | 자기 책상에서 `!`, 사용자 확인 필요 |
| 3 | `unobservable` | `missing`·`unknown`, 연결 끊김, 또는 상태 `unknown` | 자리 유지, 흐리게, `?` |
| 4 | `finishing` | 연결된 Worker가 `finishing` (실제 조회에서는 없음) | 자리에서 `...` |
| 5 | `work` | `working` | 자기 책상으로 가서 앉아 일반 작업 자세 |
| 6 | `work_done` | 이전 관측 `working` → 이번 `done` | 일어서기 (한 관측 동안) |
| 7 | `ambient` | 그 밖의 `done` | 공용 공간 사용 |

`intent`는 Python이 판정하고 화면은 표현만 정한다. Typing·Thinking·Building 같은 세부 동작은 그 근거가 문서에 생길 때까지 쓰지 않는다.

## 내보내지 않는 것

- 터미널 핸들: 입력을 보내는 값이다.
- PTY ID, 절대 경로, Orca worktree locator(`<repoId>::<path>`)
- 탭·패널·에이전트 제목, 터미널 미리보기, 대화 내용(`lastAssistantMessage`)
- Git 원격 주소, 소유자, 사용자 이름
- 오류 메시지와 argv

`--save-raw`로 저장한 원본 응답에는 이 값들이 그대로 들어 있다. 공유하지 않는다.

## 고정 예시

18개 모두 합성 DEMO다. 응답 형태는 1.4.264 실제 응답을 따르고, 값과 전이는 만든 것이다. 장면별 `demo.evidence`에 관측된 부분과 관측되지 않은 전이를 적었다.

| 장면 | 확인하는 것 |
| --- | --- |
| `empty-worktree` | 방 1, 세션 0 (펫·전용 자리 없음) |
| `one-codex` · `one-claude` · `codex-and-claude` | 1·2 세션, Provider별 표현 |
| `same-provider-three` | Codex 3개가 각각 자리를 가짐 |
| `four-sessions` · `eight-sessions` | 4·8 세션 배치 |
| `three-worktrees` | 방 3개(세션 0·2·5), 저장소 2개 |
| `terminals-and-agents` | 일반 터미널(종류 힌트만 있는 패널 포함)과 세션 혼재 |
| `unknown-provider` | `agent` 없음 → `unknown`, 다른 종류 → `other` |
| `new-session` | 새 세션의 `presentation.new` |
| `session-ended` | `exitCause`로 종료 확인 → `exit` |
| `disconnected` | 연결 끊김: 자리 유지, `unknown` |
| `session-missing` | 누락(`missing`) 후 같은 ID 재등장 |
| `reconnect-unconfirmed` | 재연결 후 ID 없는 세션은 합치지 않음, ID 있는 세션은 `incarnation_changed` |
| `duplicate-entity` | 두 패널에 열린 한 세션 = 펫 하나 |
| `worker-finishing` | 합성 Worker 연결의 `finishing` |
| `query-failed` | `agent list` 실패 후 마지막 관측 유지 |

## Snapshot v1과의 관계

- `snapshot@1`의 스키마·필드·고정 예시 12개는 바꾸지 않았다.
- 상태 화면(`python -m orca_orchestrator`)과 콘솔은 topology 모듈을 불러오지 않는다. topology 조회 실패는 기존 화면에 영향을 주지 않는다(`IsolationTest`).
- 마스코트 카탈로그(`snapshot.mascots`)는 계속 표현용 동반자다. 세션 펫은 이 문서의 `agent_sessions`에서만 생긴다.
- 화면이 두 문서를 함께 쓸 때 Worker 기록(`snapshot.workers.recorded`)을 펫에 붙이려면 근거가 필요하다. Dispatch와 패널을 잇는 근거다. 1.4.264에는 없으므로 Worker 상태는 공용 검토 보드에 표시한다.
