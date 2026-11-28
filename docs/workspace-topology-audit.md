# Workspace Topology 조사 보고 (Phase 2.6 재확인 · Phase 2.7)

기록일: 2026-11-29 (KST). Windows 11 호스트에 설치된 Orca `1.4.264`가 대상이다. 이 문서는 `topology@1`이 실제로 무엇을 근거로 하는지와 무엇을 아직 모르는지를 기록한다. 계약은 [workspace-topology-v1.md](workspace-topology-v1.md)에 있다.

**판정: `PARTIALLY FEASIBLE`.** 저장소·Worktree·탭·패널·터미널 에이전트 세션의 목록과 관계는 실제 응답으로 확인했다. 실제 세션 종료, 재시작·재연결 뒤 동일성, 세부 작업 상태, 이벤트 스트림, Worker와 세션의 연결은 확인하지 못했다. 확인하지 못한 부분은 계약에서 `unknown`·`not_implemented`로 남긴다.

## 1. 이번에 한 일과 하지 않은 일

| 구분 | 내용 |
| --- | --- |
| 명령 지원 확인 | 설치된 CLI의 명령 정의(`resources/app.asar.unpacked/out/cli/specs/core.js`·`repo.js`·`project.js`·`agent-conversations.js`)에서 경로·허용 플래그·설명을 읽었다. |
| 응답 형태 확인 | 같은 호스트에서 읽기 전용 명령을 실행했다: `status`, `repo list`, `project list`, `worktree list`, `terminal list --include-visual-layouts`, `agent list` (모두 `--json`). 이어서 `python -m orca_orchestrator.topology --live`를 두 번 실행했다. 원본 응답은 세션 임시 폴더에만 두었고 저장소에 넣지 않았다. |
| 소스 확인 | 공개 저장소 `stablyai/orca` 태그 `v1.4.264`의 계약 파일을 읽었다(§5). |
| 하지 않음 | 세션·탭·터미널·Worktree·Worker 생성이나 종료, 터미널 입력, `terminal read`·`agent read`(대화 내용), 설정·인증 변경. 상태 변화를 일부러 만들지 않았으므로 생애 주기 전이는 우연히 본 것만 기록했다. |

## 2. 관측 결과 (값 대신 수와 형태만)

| 항목 | 결과 |
| --- | --- |
| Runtime | `status`: `state=ready`, `connectionState=connected`, `appVersion=1.4.264`. `runtime.capabilities` 320개. |
| 저장소 | `repo list` 3개(`kind=git`). `project list` 3개, 각 `sourceRepoIds`가 저장소를 가리킴. |
| Worktree | 13개, `truncated=false`, 생략된 호스트 0. 13/13이 `id == <repoId>::<path>`이고 `identity.key`에 `instanceId`가 들어 있다. `branch`는 `refs/heads/<이름>` 형태다. |
| Git 교차 확인 | 저장소 3개 모두 `git worktree list --porcelain` 응답. 이름 정규화 뒤 13/13 경로와 브랜치가 일치했다. 첫 실행에서는 `refs/heads/` 접두사 때문에 불일치로 나와 파서를 고쳤다. |
| 패널 | `terminal list` 4개, 모두 `connected=true`·`orphaned=false`. 4/4의 `worktreeId`가 Worktree 목록에 있다. `ptyId`에는 Worktree 경로가 들어 있다. |
| 탭·레이아웃 | `visualLayouts` 2개(Worktree 2곳), 탭 4개. 노드 형태는 `group`·`terminal`. `split`·`pane-split`은 소스에서 확인했다. `topologyRevisions`는 Worktree ID별 정수다. |
| 에이전트 세션 | `agent list` 3개, 모두 `kind=terminal`, 모두 `orcaSessionId` 있음. 3/3의 `paneKey == <tabId>:<leafId>`가 패널과 일치했다. `agent` 값은 `claude` 1, `codex` 2. |
| 종류 힌트 | `terminals[].agentIdentity`: `codex` 2, `claude` 1, 없음 1. 없는 패널은 일반 셸이다. Orca 안내문도 이 값은 종류일 뿐 세션 식별이 아니라고 한다. |
| 상태 | 실행 사이에 Codex 세션 하나가 `working`에서 `done`으로 바뀌었다. `blocked`·`waiting`은 이번에 나타나지 않았다. |
| 시간 | 명령별 0.56–0.84초. `--live` 전체는 Git 3회 포함 약 3.4초. |
| 내보낸 문서 | 6개 출처 모두 `available`, 진단 0. 경로·핸들·`term_`·`@@` 검색 결과 0. |

2026-11-28 조사(같은 호스트, 다른 세션)의 보고와도 맞는다. 그 조사는 Codex 2·Claude 1 세션, `working`·`done`을 보고했다. 연속 조회 두 번에서 ID 집합이 유지됐고 한 회 조회는 2.30–2.47초였다는 수치는 이번에 다시 확인하지 않았다.

## 3. 기능별 증거 등급

`topology@1` 문서의 `capabilities[]`에도 이 등급이 그대로 들어 있다.

| 기능 | 등급 | 근거와 한계 |
| --- | --- | --- |
| 저장소 목록 | VERIFIED | `repo list --json` |
| Worktree와 안정 ID | VERIFIED | `identity.key`는 경로가 없는 키(`wt2:<host>:<instanceId>`). 폴더 이동 뒤의 유지는 소스 주석으로만 확인했다. 실제 이동은 하지 않았다. |
| Git 교차 확인 | VERIFIED | 경로·브랜치 13/13 일치. 삭제(`prunable`·미등록)는 관측하지 못했다. |
| 탭·패널 레이아웃 | VERIFIED | `--include-visual-layouts`. 분할(`split`·`pane-split`)은 DOCUMENTED ONLY. |
| 패널 연결 | VERIFIED | `connected`. 끊김(`false`)은 관측하지 못했다. |
| 터미널 에이전트 세션 | VERIFIED | `agent list`, `paneKey` 연결 3/3 |
| Chat 세션 | DOCUMENTED ONLY | 계약에 `kind=chat`이 있으나 이 호스트에 없었다. |
| Provider 식별 | PARTIALLY VERIFIED | `claude`·`codex` 확인. `agent=null`과 다른 종류는 문서상 가능하다. 그때는 `unknown`·`other`로 두며 제목으로 추정하지 않는다. |
| 작업 상태 | PARTIALLY VERIFIED | `working`·`done` 관측. `blocked`·`waiting`은 계약과 UI 문구(Blocked, Waiting for input)로만 확인했다. |
| 세부 작업 (Thinking·Coding·Building) | NOT AVAILABLE | 해당 필드가 없다. |
| 세션 종료 | DOCUMENTED ONLY | `terminals[].exitCause`는 계약에 있지만 관측하지 못했다. 목록에서 빠지는 것만으로는 종료가 아니다. 문서상 실행 중 잠든 에이전트는 목록에서 빠질 수 있다. |
| 재시작·재연결 뒤 동일성 | UNVERIFIED | 재시작·재연결을 관측하지 못했다. 계약 설명상 터미널 에이전트는 `/clear` 뒤 새 ID를 받고 Chat은 ID를 유지한다. |
| 실시간 이벤트 | NOT AVAILABLE (CLI) | Runtime capability 목록에는 `agent-session.status-feed.v1`, `worktree.rows-changed-event.v1` 같은 이벤트 기능이 있다. CLI에는 이벤트 명령이 없어 이 빌드는 조회를 반복한다. |
| Worker ↔ 세션 연결 | NOT AVAILABLE | 간략 `worker-show`와 `agent list` 어디에도 Dispatch와 패널을 잇는 필드가 없다. |

## 4. 계약에 반영한 결정

- **조회 명령 5개 + Git 1개만 허용한다.** `TopologyReadOnlyRunner`가 실행 전에 그 밖의 명령을 거부한다. `terminal read`·`agent read`·`terminal show`는 출력·대화를 읽으므로 넣지 않았다. `project list`는 GitHub 소유자 같은 계정 식별이 들어 있고 방 구성에 필요하지 않아 쓰지 않는다. `worktree ps`는 `agent list`가 같은 행을 평평하게 보여 주므로 쓰지 않는다.
- **Runtime이 연결되지 않으면 목록 명령을 묻지 않는다.** 실패한 출처는 다른 출처의 조회를 막지 않는다.
- `truncated`·`omittedHostIds`가 있으면 그 출처는 `partial`이고, 빠진 항목은 `missing`이 아니라 `unknown`이다.
- **Worktree ID는 경로가 들어 있는 `id`(locator) 대신 `identity.key`를 쓴다.** locator는 비공개 연결에만 쓴다.
- **세션 펫은 agent 행에서만 생긴다.** 패널의 `agentIdentity`는 Provider 힌트로만 쓴다.
- 핸들·PTY ID·경로·제목·미리보기·대화 내용은 문서에 들어가지 않는다. `RedactionTest`가 합성 원본 응답에 넣은 표식 문자열로 확인한다.

## 5. 근거 파일 (Orca v1.4.264)

- 설치본 CLI 명령 정의: `out/cli/specs/core.js`(worktree·terminal), `repo.js`, `project.js`, `agent-conversations.js`, `worktree-listing-scope-notes.js`
- 설치본 출력 형식: `out/cli/terminals/terminal-format.js`(레이아웃 노드 `split`·`group`·`pane-split`)
- 설치본 안내문: `out/cli/skills/bundled-skill-guides.js`. `agentIdentity`는 종류일 뿐이고 제목으로 터미널을 찾지 말라고 쓰여 있다.
- 공개 소스: [`runtime-terminal-visual-layout.ts`](https://github.com/stablyai/orca/blob/v1.4.264/src/main/runtime/terminal/layout/runtime-terminal-visual-layout.ts), [`runtime-terminal-contracts.ts`](https://github.com/stablyai/orca/blob/v1.4.264/src/shared/terminal/runtime/runtime-terminal-contracts.ts)(`exitCause`, `agentIdentity`, `incarnationId`), [`agent-conversation-list.ts`](https://github.com/stablyai/orca/blob/v1.4.264/src/main/runtime/agent-conversations/agent-conversation-list.ts), [`agent-conversation-contracts.ts`](https://github.com/stablyai/orca/blob/v1.4.264/src/shared/agent-conversations/agent-conversation-contracts.ts)(`orcaSessionId`이 null일 수 있음), [`agent-status-types.ts`](https://github.com/stablyai/orca/blob/v1.4.264/src/shared/agent-status/contracts/agent-status-types.ts)(`working`·`blocked`·`waiting`·`done`), [`identity.ts`](https://github.com/stablyai/orca/blob/v1.4.264/src/shared/worktree/identity.ts)

## 6. 다시 확인하는 방법

```powershell
# 읽기 전용 한 번 조회 (텍스트 요약)
python -m orca_orchestrator.topology --live --text
# 원본 응답 저장 후 재생: 저장 파일에는 경로·제목이 들어 있으니 공유하지 않는다
python -m orca_orchestrator.topology --live --save-raw $env:TEMP\topology-raw.json
python -m orca_orchestrator.topology --recorded $env:TEMP\topology-raw.json --text
```

## 7. 남은 관측 과제 (다음 실환경 확인 순서)

1. **종료**: 사용자가 직접 에이전트를 끝낸 패널에서 `exitCause`가 나오는지, 그때 `agent list` 행이 어떻게 되는지 본다.
2. **재연결**: Orca 재시작 뒤 같은 `orcaSessionId`·`incarnationId`가 돌아오는지 본다.
3. **분할 패널**: 한 탭에 두 패널을 둘 때의 `pane-split` 노드
4. **Chat 세션**: `kind=chat`과 `paneKey` 형태
5. **확인 요청**: `blocked`·`waiting`이 실제로 나오는 시점
6. **조회 비용**: 반복 조회 간격별 응답 시간, `truncated`가 나오는 Worktree 수(`--limit` 기본값)

각 단계는 사용자가 직접 동작을 만들고, 이 도구는 읽기만 한다.
