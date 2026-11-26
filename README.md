# Orca Orchestrator

PRD의 요구사항을 검증 가능한 개발 작업으로 나누고, Orca를 통해 Claude Code와 Codex에 배정하기 위한 Python 프로젝트입니다. 작업 결과와 테스트를 요구사항에 연결하고, 최종 변경은 사용자가 승인하는 흐름을 목표로 합니다.

> **현재 Worker 작업 전송은 보류 상태입니다.** 이 버전은 Orca 상태를 읽어 보여주기만 하며, Worker를 시작하거나 모델에 작업을 보내지 않습니다.

## 한눈에 보기

- **Orca 터미널에서 보이는 것:** Orca 터미널 탭에서 `python -m orca_orchestrator`를 실행하면 Runtime 연결·버전, Coordinator 바인딩, 현재 Run, 작업 전송 보류 이유, 사용량(`미확인`)이 한 화면에 나옵니다. `--tasks`로 작업 정의 파일을 지정하면 작업별 상태, 요구사항 충족 여부, 다음 작업, 흐름이 멈춘 이유, 최종 승인 상태도 함께 나옵니다. 화면은 읽기 전용입니다.
- **로컬 검증 흐름:** `python -m orca_orchestrator.flow`가 작업 계획에서 검증할 작업을 계획 순서대로 골라 단위 테스트를 한 번씩 실행하고 결과를 기록합니다. 실패하면 멈추고, 다시 실행하면 저장된 결과에서 이어갑니다. [처음부터 따라 하기](#로컬-검증-흐름)
- **Orca 없이 데모 열기:** 저장소 루트의 PowerShell에서 아래 두 줄을 실행합니다. 포함된 응답 픽스처만 재생하므로 Orca가 꺼져 있어도 열리고 실제 CLI는 호출하지 않습니다.

```powershell
$env:PYTHONPATH = "src;tests"
python -m orca_orchestrator --demo
```

데모 화면(`--demo --once` 출력):

```text
Orca Orchestrator · 읽기 전용 상태 · 데모 (connected)
조회 시각 2026-11-26 20:20:43 · 실제 CLI를 호출하지 않음
────────────────────────────────────────────────────────────────────────
Runtime     연결됨 · Orca 1.4.264 (검증 버전)
바인딩      Coordinator 터미널 바인딩 있음
현재 Run    run_0123456789ab · Example objective
작업 전송   보류 · 현재 빌드는 Worker에 작업을 보내지 않음
  보류 이유
  · 실제 작업 전송이 기본값으로 꺼져 있음
  · Worker 실행에 대한 사용자 승인 없음
  · Experiment 4-A 통과 전 (현재 CONDITIONAL)
  · Worker 구독 인증 미확인
  · 구독 포함량만 쓰는 과금 경로 미확인
  · 추가 사용량·크레딧 사용 꺼짐 미확인
  · 자동 충전 꺼짐 미확인
  · worker-start 계약 미검증 (현재 빌드는 전송하지 않음)
사용량      Claude 미확인 · Codex 미확인
작업 목록   2개 · 완료 1 · 준비 1 · 데모 계획 · 선행 작업 검증 통과
  T-1  완료       작업 계약 검증 · FR-01 · 검증 1/1 통과
  T-2  준비       요구사항 추적 표시 · FR-07 · 선행 T-1 완료
요구사항    FR-01 충족 · FR-07 미충족 (T-2 미완료)
다음 작업   없음 · T-2: 자동 실행할 수 없는 검증 조건 screen-tests
최종 승인   대기 · 로컬 검증 완료 1/2 작업 · 사용자 승인은 모든 검증 후
────────────────────────────────────────────────────────────────────────
r 새로고침 · q 종료
```

데모 작업 목록은 의존 관계가 있는 두 작업을 보여줍니다. `T-2`는 `T-1`의 검증이 통과해야 `준비`가 되며, `T-2`의 `screen-tests`는 자동 실행 대상이 아니어서 다음 작업이 없다고 표시됩니다. 내용이 창보다 길면 `↑`·`↓`로 이동합니다. `r`은 다시 조회, `q`는 화면 종료입니다. 데모 장면과 실제 실행 방법은 [상태 화면](#상태-화면)과 [작업 목록](#작업-목록)에 있습니다.

## 지금 써볼 수 있는 범위

`Python 제어 코드 → Orca CLI 어댑터 → Orca Runtime`

- **Orca 어댑터**는 Runtime 상태와 현재 Run을 조회하고, Orca 터미널 바인딩이 있는 경우 Run을 생성할 수 있습니다. Experiment 1은 Orca `1.4.264`에서 `COORDINATOR`의 Run 생성과 바인딩을 확인했습니다. Run은 실행 컨텍스트이며 생성만으로 Worker가 시작되지는 않습니다.
- **읽기 전용 상태 화면**은 Runtime 연결·버전, Coordinator 바인딩, 현재 Run, 작업 전송 보류 이유를 한 화면에 보여줍니다. Orca 상태를 바꾸지 않습니다.
- **작업 목록**은 직접 정의한 작업에 요구사항 ID, 작업 ID, 선행 작업, 검증 조건을 연결합니다. 중복 ID, 없는 선행 작업, 순환 의존성을 오류로 표시하고, 입력된 검증 결과로 작업 상태와 요구사항 충족 여부를 판정합니다.
- **로컬 검증**은 사용자가 직접 호출할 때 `unit-tests` 검증 조건으로 저장소 단위 테스트를 한 번 실행하고, 종료 코드로 통과·실패를 결과 파일에 기록합니다.
- **로컬 검증 흐름**은 다음 작업을 결정적으로 골라 하나씩 검증하고, 실패·미완료·기록 실패에서 멈추며, 저장된 결과에서 이어갑니다. 요구사항 → 작업 → 검증 조건 → 실행 결과를 추적해 보여줍니다.
- **작업 전송 게이트**는 연결·버전·바인딩·승인·과금 조건과 사용량 대기 상태를 평가합니다. 현재 빌드는 Worker를 시작하거나 작업을 보내지 않습니다.
- **개발 예정**은 실제 Worker 기동·상태 제어, 사용량 연동, PRD 문서에서 작업 추출, 단위 테스트 외 검증과 보호 파일 검사, 변경·리뷰 증거 연결, 최종 승인 기록입니다.

## 로컬 시작

Python 3.11 이상이 필요합니다. Windows PowerShell에서 저장소 루트 기준으로 소스를 불러올 수 있습니다. 이 방식은 패키지 설치나 Orca 연결 없이 실행됩니다. 런타임 Python 의존성은 없습니다.

```powershell
$env:PYTHONPATH = "src;tests"
python -c "from orca_orchestrator.orca import OrcaCliAdapter, WorkDispatcher"
```

로컬 테스트도 같은 셸에서 실행합니다.

```powershell
python -m unittest discover -s tests -v
```

테스트는 가짜 명령 실행기와 응답 픽스처로 argv 구성, 응답 파싱, 바인딩 누락, 타임아웃 재실행 차단, 전송 차단과 한도 대기, 작업 계약 오류와 선행 작업 검증 결과에 따른 상태 판정, 로컬 검증 실행과 결과 기록, 흐름의 작업 선택·중단·재개와 재확인 판정, 상태 화면의 상황별 표시를 확인합니다. 실행기 자체 테스트에는 로컬 Python 서브프로세스를 사용합니다. Orca나 모델은 호출하지 않습니다.

## 상태 화면

같은 셸에서 데모를 열 수 있습니다. 데모는 패키지에 포함된 응답 픽스처를 재생하며 Orca가 꺼져 있어도 동작합니다. 실제 CLI는 호출하지 않고, 화면 제목에 `데모`가 표시됩니다.

```powershell
python -m orca_orchestrator --demo
python -m orca_orchestrator --demo disconnected --once
```

`--demo` 뒤에 `connected`(기본), `disconnected`, `unbound`, `invalid` 중 하나를 붙이면 연결, 연결 끊김, 바인딩 없음, 응답 오류 장면을 볼 수 있습니다. `--once`는 화면을 한 번 출력하고 종료합니다.

실제 상태는 Orca 터미널 탭에서 옵션 없이 실행합니다. CLI는 `ORCA_CLI_COMMAND` 또는 Windows 기본 설치 경로에서 찾습니다.

```powershell
python -m orca_orchestrator
```

| 키 | 동작 |
| --- | --- |
| `r` | 다시 조회해 화면을 새로 그립니다. 자동 갱신은 없습니다. |
| `q` | 화면을 닫습니다. Ctrl+C도 같습니다. |
| `↑`·`↓`, PgUp·PgDn, Home·End | 내용이 창보다 길면 제목과 조작키는 고정한 채 나머지를 이동합니다. `j`·`k`, `g`·`G`도 됩니다. |

- 화면은 `status --json`과 바인딩된 터미널의 `orchestration run-current --json`만 실행합니다. 그 밖의 명령은 실행 전에 거부합니다.
- Runtime이 꺼졌거나 바인딩이 없으면 하단에 한 줄 안내가 나옵니다. 해석할 수 없는 응답은 `조회 불가`로 표시합니다.
- 사용량은 아직 연동되지 않아 `미확인`으로 표시합니다. 작업 전송은 항상 보류로 표시됩니다.
- 화면을 닫으면 이 터미널만 원래 화면으로 돌아갑니다. Orca Runtime, Run, 사용자 세션에는 영향이 없습니다.

## 작업 목록

데모는 포함된 작업 정의와 검증 결과를 읽습니다. 선행 작업 `T-1`의 검증 결과를 바꿔 `T-2`의 상태를 볼 수 있습니다.

```powershell
python -m orca_orchestrator --demo --once
python -m orca_orchestrator --demo --demo-results failed --once
python -m orca_orchestrator --demo --demo-results none --once
```

`--demo-results`는 `passed`(기본), `failed`, `none` 중 하나입니다. `failed`는 흐름이 `T-1` 검증 실패로 멈춘 장면이며 작업 목록은 다음과 같습니다.

```text
작업 목록   2개 · 검증 실패 1 · 대기 1 · 데모 계획 · 선행 작업 검증 실패
  T-1  검증 실패  작업 계약 검증 · FR-01 · 검증 실패: unit-tests
  T-2  대기       요구사항 추적 표시 · FR-07 · 선행 T-1
                  대기 사유: 선행 작업 T-1 검증 실패
요구사항    FR-01 미충족 (T-1 미완료) · FR-07 미충족 (T-2 미완료)
다음 작업   없음 · T-1: unit-tests 검증 실패 (외 1건)
흐름        중단 · T-1/unit-tests · 11-26 20:00:05 · 검증 실패 (종료 코드 1) · …
최종 승인   대기 · 로컬 검증 완료 0/2 작업 · 사용자 승인은 모든 검증 후
```

직접 정의한 작업은 `--tasks`로, 기록된 검증 결과는 `--results`로 지정합니다. 화면에는 파일 이름만 표시하고, `r`을 누르면 파일을 다시 읽습니다.

```powershell
python -m orca_orchestrator --tasks plan.json --results results.json
```

파일 형식은 [`demo_task_plan.json`](src/orca_orchestrator/fixtures/demo_task_plan.json)과 [`demo_results_failed.json`](src/orca_orchestrator/fixtures/demo_results_failed.json)을 참고하세요. 작업 정의에는 요구사항(`id`, `title`, `acceptance`)과 작업(`id`, `title`, `requirements`, `depends_on`, `checks`)이 들어갑니다. 검증 결과는 작업·검증 조건별 `passed` 또는 `failed`입니다.

- 작업은 모든 검증 조건이 현재 계획에 맞는 `passed`일 때만 `완료`입니다. 하나라도 `failed`면 `검증 실패`이며 완료되지 않습니다.
- 결과에는 검증 당시 검증 조건의 지문(`check_digest`)이 함께 기록됩니다. 그 뒤 계획에서 해당 검증 조건(ID·설명·`command` 문구)이 바뀌었거나 지문이 없는 결과는 적용 여부를 판단할 수 없으므로 `재확인`으로 표시하고 완료로 세지 않습니다.
- `다음 작업`은 흐름이 고를 작업과 이유, `흐름`은 마지막 흐름 실행이 멈춘 이유, `최종 승인`은 로컬 검증과 별개인 사용자 승인 상태입니다.
- 직접 선행 작업이 모두 완료되면 `준비`, 아니면 `대기`입니다. `대기`에는 실패하거나 아직 검증되지 않은 선행 작업이 사유로 표시됩니다.
- 요구사항은 연결된 작업이 모두 완료되면 `충족`입니다. 연결된 작업이 없거나 미완료 작업이 있으면 `미충족`입니다.
- 중복 ID, 정의되지 않은 요구사항·선행 작업, 순환 의존성, 검증 조건이 없는 작업, 맞지 않는 검증 결과는 오류로 표시하고 상태를 판정하지 않습니다.
- 화면은 검증 명령을 실행하거나 작업을 Worker에 보내지 않습니다. 작업 정의의 `command`는 설명용이며 어디에서도 실행되지 않습니다.

### 로컬 검증 실행

검증은 사용자가 명령으로 직접 실행합니다. 첫 버전은 `unit-tests` 검증 조건 하나만 지원하며, 프로그램에 정해 둔 `python -m unittest discover -s tests`를 저장소 루트에서 셸 없이 한 번 실행합니다. 아래는 같은 셸에서 데모 작업 정의로 확인한 명령입니다.

```powershell
python -m orca_orchestrator.verify --tasks src/orca_orchestrator/fixtures/demo_task_plan.json --results "$env:TEMP\orca-demo-results.json" --task T-1 --check unit-tests
```

```text
실행: T-1/unit-tests → python -m unittest discover -s tests (한 번, 제한 600초)
검증 통과 · T-1/unit-tests · 종료 코드 0 · Ran 187 tests · OK
기록: orca-demo-results.json (다른 결과 0건 보존)
```

결과는 같은 결과 파일을 `--results`로 지정해 상태 화면에서 확인합니다. 화면을 열어 둔 채 검증을 다시 실행했다면 `r`을 눌러 파일을 다시 읽습니다.

```powershell
python -m orca_orchestrator --tasks src/orca_orchestrator/fixtures/demo_task_plan.json --results "$env:TEMP\orca-demo-results.json"
```

```text
작업 목록   2개 · 완료 1 · 준비 1 · demo_task_plan.json · orca-demo-results.json
  T-1  완료       작업 계약 검증 · FR-01 · 검증 1/1 통과
  T-2  준비       요구사항 추적 표시 · FR-07 · 선행 T-1 완료
```

- 지정한 작업과 검증 조건이 작업 정의에 있고, 작업 정의와 기존 결과 파일에 오류가 없을 때만 실행합니다. 그렇지 않으면 아무것도 실행하거나 바꾸지 않습니다.
- 종료 코드 0이면 `passed`, 그 밖이면 `failed`로 기록합니다. 같은 작업·검증 조건의 이전 결과만 바꾸고 다른 결과는 보존합니다.
- 테스트 중에는 결과 파일을 잠그지 않습니다. 저장할 때만 옆의 `<결과 파일>.lock`으로 한 번에 한 프로세스가 최신 파일을 다시 읽고 병합해 교체하므로, 여러 작업을 동시에 검증해도 결과가 모두 남습니다. 최신 파일을 해석할 수 없거나 저장에 실패하면 기존 파일을 그대로 두고 알립니다.
- 제한 시간(`--timeout`, 기본 600초)을 넘기거나, 실행 자체가 실패하거나, 실행된 테스트가 0개이면 결과를 기록하지 않고 원인을 알립니다. 자동으로 다시 실행하지 않습니다.
- 테스트 프로세스에는 Claude·Codex 인증 환경변수와 Orca 터미널 바인딩을 넘기지 않습니다.
- 종료 코드는 통과 `0`, 실패 `1`, 실행 전 거부 `2`, 시간 초과·실행 불가 `3`입니다.

## 로컬 검증 흐름

작업 계획과 결과 파일을 지정해 한 번 실행하면, 검증할 수 있는 작업을 골라 하나씩 검증하고 결과를 기록한 뒤 다음 작업을 다시 판단합니다. 아래는 저장소 루트의 같은 PowerShell에서 [`examples/flow_plan.json`](examples/flow_plan.json)으로 처음부터 끝까지 확인한 순서입니다. Orca나 모델은 호출하지 않습니다. 시각과 테스트 수는 실행할 때마다 다릅니다.

1. 무엇을 할지 미리 봅니다. 아무것도 실행하거나 기록하지 않습니다.

```powershell
$env:PYTHONPATH = "src;tests"
python -m orca_orchestrator.flow --tasks examples/flow_plan.json --results "$env:TEMP\orca-flow-results.json" --dry-run
```

```text
다음 작업: T-1 · 선행 작업 없음 · unit-tests 결과 없음
진행할 수 없는 작업:
  T-2: 선행 작업 T-1 검증 결과 없음
  T-3: 선행 작업 T-2 검증 결과 없음
```

2. 작업 하나만 검증하고 멈춥니다. 중간에 멈춘 상황을 재현하는 단계이며, 종료 코드는 `5`입니다.

```powershell
python -m orca_orchestrator.flow --tasks examples/flow_plan.json --results "$env:TEMP\orca-flow-results.json" --max-tasks 1
```

```text
[1] 선택 T-1 · 선행 작업 없음 · unit-tests 결과 없음
    실행 T-1/unit-tests → python -m unittest discover -s tests (한 번, 제한 600초)
    통과 · 종료 코드 0 · Ran 187 tests · OK · 기록 orca-flow-results.json
흐름 멈춤 · T-2 · 지정한 작업 수(1)만큼 실행 · 다음 작업 T-2
이번 실행에서 완료한 작업: T-1
```

3. 같은 명령을 옵션 없이 다시 실행하면 저장된 결과에서 이어갑니다. 이미 통과한 `T-1`은 다시 실행하지 않습니다.

```powershell
python -m orca_orchestrator.flow --tasks examples/flow_plan.json --results "$env:TEMP\orca-flow-results.json"
```

```text
[1] 선택 T-2 · 선행 T-1 완료 · unit-tests 결과 없음
    실행 T-2/unit-tests → python -m unittest discover -s tests (한 번, 제한 600초)
    통과 · 종료 코드 0 · Ran 187 tests · OK · 기록 orca-flow-results.json
[2] 선택 T-3 · 선행 T-2 완료 · unit-tests 결과 없음
    실행 T-3/unit-tests → python -m unittest discover -s tests (한 번, 제한 600초)
    통과 · 종료 코드 0 · Ran 187 tests · OK · 기록 orca-flow-results.json
흐름 끝남 · 모든 작업의 로컬 검증 통과 · 최종 사용자 승인 대기
이번 실행에서 완료한 작업: T-2, T-3
```

끝에는 요구사항부터 실행 결과까지의 추적이 나옵니다.

```text
추적 (요구사항 → 작업 → 검증 조건 → 결과):
  FR-01 충족 · PRD 입력 및 작업 계약
    T-1 완료 · 작업 계약과 상태 판정
      unit-tests 통과 · 종료 코드 0 · python -m unittest discover -s tests · 2026-11-26T11:20:44Z · Ran 187 tests · OK
  FR-06 충족 · 결정론적 검증
    T-2 완료 · 로컬 검증 실행과 결과 기록
      unit-tests 통과 · 종료 코드 0 · python -m unittest discover -s tests · 2026-11-26T11:20:51Z · Ran 187 tests · OK
  FR-07 충족 · 요구사항 추적
    T-3 완료 · 요구사항 추적과 흐름 표시
      unit-tests 통과 · 종료 코드 0 · python -m unittest discover -s tests · 2026-11-26T11:20:57Z · Ran 187 tests · OK
최종 승인: 대기 · 로컬 검증 결과와 별개로 사용자가 직접 승인합니다 (자동 승인 없음)
```

4. 상태 화면에서 같은 파일을 읽습니다. Orca가 꺼져 있거나 Orca 터미널이 아니면 Runtime·바인딩 줄과 하단 안내가 달라지지만 작업 영역은 같습니다. 흐름을 실행하는 동안 화면을 열어 두었다면 `r`을 눌러 파일을 다시 읽습니다.

```powershell
python -m orca_orchestrator --tasks examples/flow_plan.json --results "$env:TEMP\orca-flow-results.json"
```

```text
작업 목록   3개 · 완료 3 · flow_plan.json · orca-flow-results.json
  T-1  완료       작업 계약과 상태 판정 · FR-01 · 검증 1/1 통과
  T-2  완료       로컬 검증 실행과 결과 기록 · FR-06 · 검증 1/1 통과
  T-3  완료       요구사항 추적과 흐름 표시 · FR-07 · 검증 1/1 통과
요구사항    FR-01 충족 · FR-06 충족 · FR-07 충족
다음 작업   없음 · 모든 작업 로컬 검증 완료
흐름        끝남 · 11-26 20:21:04 · 모든 작업의 로컬 검증 통과 · 최종 사용자 승…
최종 승인   대기 · 로컬 검증 모두 통과 · 사용자 최종 승인 필요 (자동 승인 없음)
```

5. 모두 끝난 뒤 다시 실행하면 아무것도 실행하지 않고 끝납니다.

```text
흐름 끝남 · 모든 작업의 로컬 검증 통과 · 최종 사용자 승인 대기
```

- **선택 규칙:** 계획 순서에서 선행 작업이 모두 완료됐고, 자동 실행할 수 있는 검증 조건(`unit-tests`) 중 결과가 없거나 재확인이 필요한 통과 결과가 남은 첫 작업을 고릅니다. 같은 파일이면 항상 같은 작업을 고릅니다.
- **멈추는 경우:** 검증 실패(`1`), 실행 전 거부(`2`), 시간 초과·실행 불가·실행된 테스트 0개·결과 기록 실패(`3`), 더 고를 작업 없음(`4`), `--max-tasks` 도달(`5`)에서 멈춥니다. 모든 작업이 끝나면 `0`입니다. 멈춘 작업과 이유를 출력하고 `<결과 파일 이름>.flow.json`에도 남겨 상태 화면에 보여줍니다.
- **재실행하지 않는 것:** 흐름은 실패한 검증을 자동으로 다시 실행하지 않습니다. 원인을 고친 뒤 `python -m orca_orchestrator.verify`로 그 검증을 직접 실행하고, 흐름을 다시 실행해 이어갑니다. 작업 정의의 `command` 문자열은 실행하지 않습니다.
- **재확인:** 계획의 검증 조건이 바뀌어 `재확인`이 된 통과 결과는 흐름이 다시 검증합니다. 재확인이 필요한 실패 결과와 자동 실행할 수 없는 검증 조건은 대기 이유로 표시합니다.
- **파일:** 검증 결과는 기존 `--results` 형식으로 저장하며 다른 작업의 결과를 보존합니다. 저장할 때만 `<결과 파일>.lock`으로 잠급니다.
- **최종 승인:** 모든 로컬 검증이 통과해도 최종 승인은 `대기`입니다. 프로그램이 승인을 대신하지 않습니다.
- 흐름이 검증 실패로 멈춘 장면은 `python -m orca_orchestrator --demo --demo-results failed`로 볼 수 있습니다.

## 구독과 과금

실제 실행을 승인하기 전, 사용자가 공식 Claude Code·Codex CLI에서 자기 구독으로 로그인하고 해당 경로의 **추가 사용과 자동 충전을 꺼 둡니다**. 기본 정책은 구독에 포함된 사용량만 쓰는 것입니다. 프로그램에 한도 도달이 기록되면 리셋을 기다립니다. 초기화 시각이 불명확하면 대기를 유지하고, 시각이 지나면 새 사용량 관측으로 여유를 확인한 후 대기를 해제합니다. API 키로 자동 전환하거나 추가 크레딧을 구매하지 않습니다.

Worker 작업 전송에는 실제 실행 경로의 구독 인증, 구독 외 과금 배제, 해당 자동화 방식에 적용되는 공식 정책 근거 및 별도 사용자 승인이 필요합니다. 현재 Step 0의 4-A는 `CONDITIONAL`이며 4-B·4-C와 Feasibility Gate는 보류 상태입니다.

## 개발 현황

[PRD v0.2](docs/PRD.md)에 현재 구현 경계와 인수 조건을 정리했습니다. `COORDINATOR` 연동을 제품 코드에서 개발하고 로컬 테스트로 검증합니다. 실제 Worker 실행 예시는 해당 경로를 검증하고 별도 승인을 받은 뒤 추가합니다.

## 라이선스

[MIT License](LICENSE)로 배포합니다.
