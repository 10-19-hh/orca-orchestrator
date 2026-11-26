# Orca Orchestrator

PRD의 요구사항을 검증 가능한 개발 작업으로 나누고, Orca를 통해 Claude Code와 Codex에 배정하기 위한 Python 프로젝트입니다. 작업 결과와 테스트를 요구사항에 연결하고, 최종 변경은 사용자가 승인하는 흐름을 목표로 합니다.

> **현재 Worker 작업 전송은 보류 상태입니다.** 이 버전은 Orca 상태를 읽어 보여주기만 하며, Worker를 시작하거나 모델에 작업을 보내지 않습니다.

## 한눈에 보기

- **Orca 터미널에서 보이는 것:** Orca 터미널 탭에서 `python -m orca_orchestrator`를 실행하면 Runtime 연결·버전, Coordinator 바인딩, 현재 Run, 작업 전송 보류 이유, 사용량(`미확인`)이 한 화면에 나옵니다. `--tasks`로 작업 정의 파일을 지정하면 작업별 상태, 요구사항 충족 여부, 다음 작업, 흐름이 멈춘 이유와 함께 **어떤 코드를 검증했는지, 기준 버전 대비 변경 파일이 어느 작업에 속하는지, 무엇이 승인을 막는지**가 나옵니다. 화면은 읽기 전용입니다.
- **로컬 검증부터 승인까지:** `flow`가 작업을 골라 단위 테스트로 검증하고, `review`로 변경과 증거를 확인한 뒤 사용자가 `--approve`로 직접 승인합니다. 승인 후 코드나 작업 정의가 바뀌면 화면에 `재승인 필요`가 표시됩니다. 자동 승인은 없습니다. [예제로 따라 하기](#로컬-검증부터-승인까지)
- **Orca 없이 데모 열기:** 저장소 루트의 PowerShell에서 아래 두 줄을 실행합니다. 포함된 응답 픽스처만 재생하므로 Orca가 꺼져 있어도 열리고 실제 CLI는 호출하지 않습니다.

```powershell
$env:PYTHONPATH = "src;tests"
python -m orca_orchestrator --demo
```

데모 화면(`--demo --once` 출력):

```text
Orca Orchestrator · 읽기 전용 상태 · 데모 (connected)
조회 시각 2026-11-26 22:55:03 · 실제 CLI를 호출하지 않음
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
코드 기준   HEAD 3c1d5e7 + 커밋 전 변경 · 지문 5f0c1e2d · 현재 코드 결과 1/1
변경        main(8e0f2a4) 대비 4개 · T-1 2 · T-2 1 · 확인 필요 1
  README.md (수정) → 연결된 작업 없음 · 확인 필요
  src/orca_orchestrator/tasks/contract.py (수정) → T-1
  src/orca_orchestrator/tasks/progress.py (수정) → T-1
  src/orca_orchestrator/tui/render.py (수정) → T-2
보호 경로   변경 없음 · docs/PRD.md, src/orca_orchestrator/orca/dispatch.py
최종 승인   보류 · 사유 1건 · 해결 전에는 승인할 수 없음
  · T-2: screen-tests 결과 없음
────────────────────────────────────────────────────────────────────────
r 새로고침 · q 종료
```

데모 작업 목록은 의존 관계가 있는 두 작업을 보여줍니다. `T-2`는 `T-1`의 검증이 통과해야 `준비`가 되며, `T-2`의 `screen-tests`는 자동 실행 대상이 아니어서 다음 작업이 없고 최종 승인도 보류됩니다. 데모의 코드 기준과 변경 파일은 포함된 예시 값입니다. 내용이 창보다 길면 `↑`·`↓`로 이동합니다. `r`은 다시 조회, `q`는 화면 종료입니다. 데모 장면과 실제 실행 방법은 [상태 화면](#상태-화면)과 [작업 목록](#작업-목록)에 있습니다.

## 지금 써볼 수 있는 범위

`Python 제어 코드 → Orca CLI 어댑터 → Orca Runtime`

- **Orca 어댑터**는 Runtime 상태와 현재 Run을 조회하고, Orca 터미널 바인딩이 있는 경우 Run을 생성할 수 있습니다. Experiment 1은 Orca `1.4.264`에서 `COORDINATOR`의 Run 생성과 바인딩을 확인했습니다. Run은 실행 컨텍스트이며 생성만으로 Worker가 시작되지는 않습니다.
- **읽기 전용 상태 화면**은 Runtime 연결·버전, Coordinator 바인딩, 현재 Run, 작업 전송 보류 이유를 한 화면에 보여줍니다. Orca 상태를 바꾸지 않습니다.
- **작업 목록**은 직접 정의한 작업에 요구사항 ID, 작업 ID, 선행 작업, 검증 조건을 연결합니다. 중복 ID, 없는 선행 작업, 순환 의존성을 오류로 표시하고, 입력된 검증 결과로 작업 상태와 요구사항 충족 여부를 판정합니다.
- **로컬 검증**은 사용자가 직접 호출할 때 `unit-tests` 검증 조건으로 저장소 단위 테스트를 한 번 실행하고, 종료 코드로 통과·실패를 결과 파일에 기록합니다.
- **로컬 검증 흐름**은 다음 작업을 결정적으로 골라 하나씩 검증하고, 실패·미완료·기록 실패에서 멈추며, 저장된 결과에서 이어갑니다. 요구사항 → 작업 → 검증 조건 → 실행 결과를 추적해 보여줍니다.
- **변경 검토와 최종 승인**은 검증 결과가 현재 코드(커밋 전 변경 포함)에 맞는지, 작업 정의의 기준 버전 대비 어떤 파일이 바뀌었고 어느 작업에 속하는지, 보호 경로가 바뀌지 않았는지 확인합니다. 승인과 보류는 사용자가 `review` 명령으로만 기록합니다.
- **작업 전송 게이트**는 연결·버전·바인딩·승인·과금 조건과 사용량 대기 상태를 평가합니다. 현재 빌드는 Worker를 시작하거나 작업을 보내지 않습니다.
- **개발 예정**은 실제 Worker 기동·상태 제어, 사용량 연동, PRD 문서에서 작업 추출, 단위 테스트 외 검증, 교차 리뷰 증거 연결, 승인 후 병합 연동입니다.

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

테스트는 가짜 명령 실행기와 응답 픽스처로 argv 구성, 응답 파싱, 바인딩 누락, 타임아웃 재실행 차단, 전송 차단과 한도 대기, 작업 계약 오류와 선행 작업 검증 결과에 따른 상태 판정, 로컬 검증 실행과 결과 기록, 흐름의 작업 선택·중단·재개와 재확인 판정, 코드 지문과 보호 경로·변경 연결, 승인·보류·재승인 판정, 상태 화면의 상황별 표시를 확인합니다. 실행기와 변경 비교 테스트에는 로컬 Python 서브프로세스와 임시 Git 저장소를 사용합니다. Orca나 모델은 호출하지 않습니다.

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

- 화면은 Orca 명령 중 `status --json`과 바인딩된 터미널의 `orchestration run-current --json`만 실행합니다. 그 밖의 Orca 명령은 실행 전에 거부합니다.
- `--tasks`를 지정하면 `--repo`(기본: 현재 폴더)의 검증 대상 파일을 읽고, 변경 확인에 읽기 전용 Git 명령(`rev-parse`, `diff`, `ls-files`)만 실행합니다. 그 밖의 Git 명령은 실행 전에 거부하며, Git에는 모델 인증 환경변수와 Orca 터미널 바인딩을 넘기지 않습니다.
- Runtime이 꺼졌거나 바인딩이 없으면 하단에 한 줄 안내가 나옵니다. 해석할 수 없는 응답은 `조회 불가`로 표시합니다.
- 사용량은 아직 연동되지 않아 `미확인`으로 표시합니다. 작업 전송은 항상 보류로 표시됩니다.
- 화면을 닫으면 이 터미널만 원래 화면으로 돌아갑니다. Orca Runtime, Run, 사용자 세션에는 영향이 없습니다.

## 작업 목록

데모는 포함된 작업 정의와 검증 결과를 읽습니다. 선행 작업 `T-1`의 검증 결과를 바꿔 `T-2`의 상태를 볼 수 있습니다.

```powershell
python -m orca_orchestrator --demo --once
python -m orca_orchestrator --demo --demo-results failed --once
python -m orca_orchestrator --demo --demo-results approved --once
```

`--demo-results`는 `passed`(기본), `failed`, `none`, `approved`(승인됨), `changed`(승인 후 코드 변경), `protected`(보호 경로 변경) 중 하나입니다. `failed`는 흐름이 `T-1` 검증 실패로 멈춘 장면이며 작업 목록은 다음과 같습니다.

```text
작업 목록   2개 · 검증 실패 1 · 대기 1 · 데모 계획 · 선행 작업 검증 실패
  T-1  검증 실패  작업 계약 검증 · FR-01 · 검증 실패: unit-tests
  T-2  대기       요구사항 추적 표시 · FR-07 · 선행 T-1
                  대기 사유: 선행 작업 T-1 검증 실패
요구사항    FR-01 미충족 (T-1 미완료) · FR-07 미충족 (T-2 미완료)
다음 작업   없음 · T-1: unit-tests 검증 실패 (외 1건)
흐름        중단 · T-1/unit-tests · 11-26 20:00:05 · 검증 실패 (종료 코드 1) · …
코드 기준   HEAD 3c1d5e7 + 커밋 전 변경 · 지문 5f0c1e2d · 현재 코드 결과 1/1
변경        main(8e0f2a4) 대비 4개 · T-1 2 · T-2 1 · 확인 필요 1
  README.md (수정) → 연결된 작업 없음 · 확인 필요
  src/orca_orchestrator/tasks/contract.py (수정) → T-1
  src/orca_orchestrator/tasks/progress.py (수정) → T-1
  src/orca_orchestrator/tui/render.py (수정) → T-2
보호 경로   변경 없음 · docs/PRD.md, src/orca_orchestrator/orca/dispatch.py
최종 승인   보류 · 사유 2건 · 해결 전에는 승인할 수 없음
  · T-1: unit-tests 검증 실패
  · T-2: screen-tests 결과 없음
```

직접 정의한 작업은 `--tasks`로, 기록된 검증 결과는 `--results`로, 검토할 저장소는 `--repo`(기본: 현재 폴더)로 지정합니다. 화면에는 파일 이름만 표시하고, `r`을 누르면 파일과 저장소 상태를 다시 읽습니다.

```powershell
python -m orca_orchestrator --tasks plan.json --results results.json --repo .
```

파일 형식은 [`demo_task_plan.json`](src/orca_orchestrator/fixtures/demo_task_plan.json)과 [`demo_results_failed.json`](src/orca_orchestrator/fixtures/demo_results_failed.json)을 참고하세요. 작업 정의에는 요구사항(`id`, `title`, `acceptance`)과 작업(`id`, `title`, `requirements`, `depends_on`, `checks`)이 들어갑니다. 변경 검토에 쓰는 비교 기준 `base`(Git 리비전)와 작업별 `paths`(이 작업이 바꾸는 경로), `protected_paths`(바꾸면 안 되는 경로)는 선택 항목이며 경로는 저장소 루트 기준 `/` 구분 상대 경로입니다. 검증 결과는 작업·검증 조건별 `passed` 또는 `failed`입니다.

- 작업은 모든 검증 조건이 현재 계획에 맞는 `passed`일 때만 `완료`입니다. 하나라도 `failed`면 `검증 실패`이며 완료되지 않습니다.
- 결과에는 검증 당시 검증 조건의 지문(`check_digest`)과 검증한 코드의 지문(`source_digest`)이 함께 기록됩니다. 그 뒤 해당 검증 조건(ID·설명·`command` 문구)이나 검증 대상 파일이 바뀌었거나, 지문이 없는 이전 결과는 현재 상태에 맞는지 판단할 수 없으므로 `재확인`으로 표시하고 완료로 세지 않습니다.
- `다음 작업`은 흐름이 고를 작업과 이유, `흐름`은 마지막 흐름 실행이 멈춘 이유입니다. `코드 기준`, `변경`, `보호 경로`, `최종 승인`은 [로컬 검증부터 승인까지](#로컬-검증부터-승인까지)에서 설명합니다.
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
검증 통과 · T-1/unit-tests · 종료 코드 0 · Ran 212 tests · OK
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
- 실행 직전에 검증 대상 파일의 지문을 기록하고, 테스트가 도는 동안 그 파일이 바뀌면 어느 코드의 결과인지 알 수 없으므로 기록하지 않습니다.
- 테스트 중에는 결과 파일을 잠그지 않습니다. 저장할 때만 옆의 `<결과 파일>.lock`으로 한 번에 한 프로세스가 최신 파일을 다시 읽고 병합해 교체하므로, 여러 작업을 동시에 검증해도 결과가 모두 남습니다. 최신 파일을 해석할 수 없거나 저장에 실패하면 기존 파일을 그대로 두고 알립니다.
- 제한 시간(`--timeout`, 기본 600초)을 넘기거나, 실행 자체가 실패하거나, 실행된 테스트가 0개이면 결과를 기록하지 않고 원인을 알립니다. 자동으로 다시 실행하지 않습니다.
- 테스트 프로세스에는 Claude·Codex 인증 환경변수와 Orca 터미널 바인딩을 넘기지 않습니다.
- 종료 코드는 통과 `0`, 실패 `1`, 실행 전 거부 `2`, 시간 초과·실행 불가 `3`입니다.

## 로컬 검증부터 승인까지

[`examples/review_project`](examples/review_project)는 계산 함수를 구현하는 작업 `T-1`과 테스트를 쓰는 작업 `T-2`, 두 작업 모두 바꾸면 안 되는 명세 `docs/spec.md`가 있는 작은 저장소입니다. 아래는 이 예제를 임시 폴더에 복사해 **계획 선택 → 로컬 검증 → 변경과 증거 확인 → 사용자 승인 → 변경 후 재확인**을 저장소 루트의 같은 PowerShell에서 따라간 순서입니다. Git이 필요하며 Orca나 모델은 호출하지 않습니다. 시각, 커밋 ID, 지문, 테스트 수는 실행할 때마다 다릅니다.

준비: 기준 커밋 `base`에는 작업 정의와 명세만 넣습니다. `src/`와 `tests/`는 작업 결과로 새로 생긴 변경이 됩니다. `$a`는 이후 명령에 공통으로 넣는 인자입니다.

```powershell
$env:PYTHONPATH = "src;tests"
$demo = "$env:TEMP\orca-review-demo"
Copy-Item -Recurse examples/review_project $demo
git -C $demo init -q -b main
git -C $demo add .gitignore plan.json docs
git -C $demo -c user.name=example -c user.email=example@example.invalid commit -q -m base
git -C $demo tag base
$a = "--tasks", "$demo\plan.json", "--results", "$demo\results.json", "--repo", $demo
```

1. **계획 선택.** 흐름이 무엇을 할지 미리 봅니다. 아무것도 실행하거나 기록하지 않습니다.

```powershell
python -m orca_orchestrator.flow @a --dry-run
```

```text
다음 작업: T-1 · 선행 작업 없음 · unit-tests 결과 없음
진행할 수 없는 작업:
  T-2: 선행 작업 T-1 검증 결과 없음
```

2. **로컬 검증.** 선행 작업 순서대로 단위 테스트를 한 번씩 실행하고 결과와 검증한 코드 지문을 기록합니다.

```powershell
python -m orca_orchestrator.flow @a
```

```text
[1] 선택 T-1 · 선행 작업 없음 · unit-tests 결과 없음
    실행 T-1/unit-tests → python -m unittest discover -s tests (한 번, 제한 600초)
    통과 · 종료 코드 0 · Ran 2 tests · OK · 기록 results.json
[2] 선택 T-2 · 선행 T-1 완료 · unit-tests 결과 없음
    실행 T-2/unit-tests → python -m unittest discover -s tests (한 번, 제한 600초)
    통과 · 종료 코드 0 · Ran 2 tests · OK · 기록 results.json
흐름 끝남 · 모든 작업의 로컬 검증 통과 · 최종 사용자 승인 대기
이번 실행에서 완료한 작업: T-1, T-2
```

3. **변경과 증거 확인.** 기준 `base` 대비 바뀐 파일이 어느 작업에 속하는지, 각 결과가 어떤 코드를 검증했는지, 보호 경로가 그대로인지 봅니다. 아무것도 기록하지 않으며, 승인할 수 있으면 종료 코드 `0`, 보류 사유가 있으면 `1`입니다.

```powershell
python -m orca_orchestrator.review @a
```

```text
검토 · plan.json · results.json
코드 기준: HEAD fe3e420 + 커밋 전 변경 · 지문 abf0e938 (검증 대상 src/, tests/ 파일 3개)
변경: base(fe3e420) 대비 3개 · T-1 2 · T-2 1
  src/calc/__init__.py (추가) → T-1
  src/calc/ops.py (추가) → T-1
  tests/test_ops.py (추가) → T-2
보호 경로: 변경 없음 · docs/spec.md
추적 (요구사항 → 작업 → 검증 조건 → 결과 · 검증한 코드 · 변경 파일):
  R-1 충족 · 덧셈과 곱셈
    T-1 완료 · 계산 함수 구현
      unit-tests 통과 · 종료 코드 0 · python -m unittest discover -s tests · 2026-11-26T13:56:03Z · Ran 2 tests · OK · 검증한 코드 HEAD fe3e420 + 커밋 전 변경, 지문 abf0e938 (현재와 같음)
      변경 파일: src/calc/__init__.py (추가), src/calc/ops.py (추가)
  R-2 충족 · 회귀 테스트
    T-2 완료 · 계산 함수 테스트
      unit-tests 통과 · 종료 코드 0 · python -m unittest discover -s tests · 2026-11-26T13:56:03Z · Ran 2 tests · OK · 검증한 코드 HEAD fe3e420 + 커밋 전 변경, 지문 abf0e938 (현재와 같음)
      변경 파일: tests/test_ops.py (추가)
최종 승인: 승인 가능 · review --approve로 직접 승인 (자동 승인 없음)
```

4. **사용자 승인.** 사용자가 직접 승인을 기록합니다. 보류 사유가 하나라도 있으면 기록하지 않습니다.

```powershell
python -m orca_orchestrator.review @a --approve
```

```text
최종 승인: 승인됨 · 11-26 22:56:05 · 코드 abf0e938
승인 기록: results.approval.json · 이후 작업 정의·코드·변경 파일이 바뀌면 상태 화면에 재승인 필요로 표시됩니다.
```

상태 화면에서도 같은 상태를 봅니다. Orca 터미널이 아니면 위쪽 Orca 영역과 하단 안내가 달라지지만 작업 영역은 같습니다.

```powershell
python -m orca_orchestrator @a
```

```text
작업 목록   2개 · 완료 2 · plan.json · results.json
  T-1  완료       계산 함수 구현 · R-1 · 검증 1/1 통과
  T-2  완료       계산 함수 테스트 · R-2 · 검증 1/1 통과
요구사항    R-1 충족 · R-2 충족
다음 작업   없음 · 모든 작업 로컬 검증 완료
흐름        끝남 · 11-26 22:56:03 · 모든 작업의 로컬 검증 통과 · 최종 사용자 승…
코드 기준   HEAD fe3e420 + 커밋 전 변경 · 지문 abf0e938 · 현재 코드 결과 2/2
변경        base(fe3e420) 대비 3개 · T-1 2 · T-2 1
  src/calc/__init__.py (추가) → T-1
  src/calc/ops.py (추가) → T-1
  tests/test_ops.py (추가) → T-2
보호 경로   변경 없음 · docs/spec.md
최종 승인   승인됨 · 11-26 22:56:05 · 코드 abf0e938
```

5. **변경 후 재확인.** 승인 뒤 코드를 고치면 예전 검증 결과는 `재확인`, 승인은 `재승인 필요`가 됩니다. 화면을 열어 두었다면 `r`을 누릅니다.

```powershell
Add-Content "$demo\src\calc\ops.py" "# changed after approval"
python -m orca_orchestrator @a
```

```text
작업 목록   2개 · 재확인 1 · 대기 1 · plan.json · results.json
  T-1  재확인     계산 함수 구현 · R-1 · 선행 작업 없음 · 재확인 필요: unit-tes…
  T-2  대기       계산 함수 테스트 · R-2 · 선행 T-1
                  대기 사유: 선행 작업 T-1 재확인 필요
요구사항    R-1 미충족 (T-1 미완료) · R-2 미충족 (T-2 미완료)
다음 작업   T-1 · 선행 작업 없음 · unit-tests 재확인 필요 (검증 후 코드 변경)
흐름        끝남 · 11-26 22:56:03 · 모든 작업의 로컬 검증 통과 · 최종 사용자 승…
코드 기준   HEAD fe3e420 + 커밋 전 변경 · 지문 e502c7a4 · 현재 코드 결과 0/2
변경        base(fe3e420) 대비 3개 · T-1 2 · T-2 1
  src/calc/__init__.py (추가) → T-1
  src/calc/ops.py (추가) → T-1
  tests/test_ops.py (추가) → T-2
보호 경로   변경 없음 · docs/spec.md
최종 승인   재승인 필요 · 승인 후 코드 변경 · 보류 사유 2건
  · T-1: unit-tests 재확인 필요 (검증 후 코드 변경)
  · T-2: unit-tests 재확인 필요 (검증 후 코드 변경)
```

흐름을 다시 실행하면 바뀐 코드로 다시 검증합니다.

```powershell
python -m orca_orchestrator.flow @a
```

```text
[1] 선택 T-1 · 선행 작업 없음 · unit-tests 재확인 필요 (검증 후 코드 변경)
[2] 선택 T-2 · 선행 T-1 완료 · unit-tests 재확인 필요 (검증 후 코드 변경)
흐름 끝남 · 모든 작업의 로컬 검증 통과 · 최종 사용자 승인 대기
```

검증이 모두 통과해도 상태 화면의 최종 승인은 사용자가 다시 승인할 때까지 그대로입니다.

```text
최종 승인   재승인 필요 · 승인 후 코드 변경
```

```powershell
python -m orca_orchestrator.review @a --approve
```

```text
최종 승인: 승인됨 · 11-26 22:56:13 · 코드 e502c7a4
승인 기록: results.approval.json · 이후 작업 정의·코드·변경 파일이 바뀌면 상태 화면에 재승인 필요로 표시됩니다.
```

6. **보호 경로 변경.** 보호 경로가 바뀌면 검증이 모두 통과해도 승인할 수 없습니다. 종료 코드는 `1`이며 승인 기록을 바꾸지 않습니다.

```powershell
Add-Content "$demo\docs\spec.md" "- div is out of scope"
python -m orca_orchestrator.review @a --approve
```

```text
최종 승인: 재승인 필요 · 승인 후 변경 파일 달라짐 · 보류 사유 2건
  · T-1: 보호 경로 변경 docs/spec.md (수정)
  · T-2: 보호 경로 변경 docs/spec.md (수정)
승인을 기록하지 않음 · 승인할 수 없음 · 보류 사유 2건
파일을 바꾸지 않았습니다.
```

상태 화면에는 다음처럼 나옵니다.

```text
  docs/spec.md (수정) → 연결된 작업 없음 · 확인 필요 · T-1, T-2 보호 경로
보호 경로   변경 2건 · 승인 보류
```

### 판정 규칙

- **검증한 코드:** 검증 대상은 `--repo` 아래 `src/`와 `tests/`의 파일이며, 커밋하지 않은 변경도 포함합니다. 결과마다 실행 직전 파일 내용의 지문(`source_digest`)을 기록하고, 현재 파일의 지문과 같을 때만 그 결과를 씁니다. Git `HEAD`와 커밋 전 변경 여부는 표시용이며, `HEAD`가 같아도 파일이 다르면 다른 코드로 봅니다. 캐시(`__pycache__`, `*.pyc`), 숨김 파일·폴더, 작업 정의·결과 파일과 `.lock`·`.flow.json`·`.approval.json`·`.tmp` 파일은 지문에 넣지 않으므로 테스트 실행이나 결과 저장으로 결과가 만료되지 않습니다.
- **이전 결과:** 지문이 없는 이전 결과 파일도 그대로 읽습니다. 다만 어떤 코드를 검증했는지 알 수 없으므로 `재확인`(코드 기준 없는 결과)으로 표시하고, 흐름이 통과 결과를 다시 검증합니다. 이런 결과로는 승인할 수 없습니다.
- **변경 파일:** 작업 정의의 `base` 대비 커밋된 변경, 커밋 전 변경, Git이 무시하지 않는 새 파일을 모읍니다. 각 파일은 `paths`가 덮는 작업에 연결하고, 연결할 작업이 없으면 `확인 필요`로 표시합니다. 작업 정의·결과 파일과 그 부속 파일, 캐시는 목록에서 뺍니다.
- **보호 경로:** 작업의 `protected_paths` 아래 파일이 `base`와 다르면(수정·삭제·추가) 그 작업과 파일을 보여주고 승인을 보류합니다. `base`가 없거나 찾을 수 없거나, `--repo`가 Git 저장소의 최상위 폴더가 아니면 비교 불가로 보류합니다.
- **승인 조건:** 모든 검증 조건이 현재 작업 정의와 현재 코드에 맞는 `passed`이고, 모든 요구사항에 작업이 연결되어 있고, 변경 비교가 되고, 보호 경로가 그대로일 때만 `--approve`가 기록됩니다. 연결할 작업이 없는 변경이 있으면 내용을 확인한 뒤 `--confirm-unlinked`를 함께 줍니다. 기록은 `<결과 파일 이름>.approval.json`에 남습니다.
- **재승인:** 승인 뒤 작업 정의, 검증 대상 코드, 변경 파일 목록이나 내용, `base`가 가리키는 커밋이 바뀌거나 보류 사유가 다시 생기면 `재승인 필요`로 표시합니다.
- **보류:** `python -m orca_orchestrator.review @a --hold "사유"`로 사유와 함께 보류를 기록합니다.
- **자동 승인 없음:** 흐름 완료, 상태 화면의 `r`, 테스트 통과는 승인 기록을 만들거나 바꾸지 않습니다. 승인과 보류는 `review`에 `--approve` 또는 `--hold`를 줄 때만 기록됩니다.
- **종료 코드:** `review`는 승인 가능·승인 기록·보류 기록 `0`, 보류 사유가 있거나 승인을 거부하면 `1`, 작업 정의나 결과 파일을 쓸 수 없으면 `2`입니다.

### 흐름 규칙

- **선택 규칙:** 계획 순서에서 선행 작업이 모두 완료됐고, 자동 실행할 수 있는 검증 조건(`unit-tests`) 중 결과가 없거나 재확인이 필요한 통과 결과가 남은 첫 작업을 고릅니다. 같은 파일과 코드면 항상 같은 작업을 고릅니다.
- **멈추는 경우:** 검증 실패(`1`), 실행 전 거부(`2`), 시간 초과·실행 불가·실행된 테스트 0개·테스트 중 코드 변경·결과 기록 실패(`3`), 더 고를 작업 없음(`4`), `--max-tasks` 도달(`5`)에서 멈춥니다. 모든 작업이 끝나면 `0`입니다. 멈춘 작업과 이유를 출력하고 `<결과 파일 이름>.flow.json`에도 남겨 상태 화면에 보여줍니다. 같은 명령을 다시 실행하면 저장된 결과에서 이어갑니다.
- **재실행하지 않는 것:** 흐름은 실패한 검증을 자동으로 다시 실행하지 않습니다. 원인을 고친 뒤 `python -m orca_orchestrator.verify`로 그 검증을 직접 실행하고, 흐름을 다시 실행해 이어갑니다. 작업 정의의 `command` 문자열은 실행하지 않습니다.
- **파일:** 검증 결과는 기존 `--results` 형식으로 저장하며 다른 작업의 결과를 보존합니다. 저장과 승인 기록 때만 `<결과 파일>.lock`으로 잠급니다.
- 흐름이 검증 실패로 멈춘 장면은 `python -m orca_orchestrator --demo --demo-results failed`로 볼 수 있습니다.

## 구독과 과금

실제 실행을 승인하기 전, 사용자가 공식 Claude Code·Codex CLI에서 자기 구독으로 로그인하고 해당 경로의 **추가 사용과 자동 충전을 꺼 둡니다**. 기본 정책은 구독에 포함된 사용량만 쓰는 것입니다. 프로그램에 한도 도달이 기록되면 리셋을 기다립니다. 초기화 시각이 불명확하면 대기를 유지하고, 시각이 지나면 새 사용량 관측으로 여유를 확인한 후 대기를 해제합니다. API 키로 자동 전환하거나 추가 크레딧을 구매하지 않습니다.

Worker 작업 전송에는 실제 실행 경로의 구독 인증, 구독 외 과금 배제, 해당 자동화 방식에 적용되는 공식 정책 근거 및 별도 사용자 승인이 필요합니다. 현재 Step 0의 4-A는 `CONDITIONAL`이며 4-B·4-C와 Feasibility Gate는 보류 상태입니다.

## 개발 현황

[PRD v0.2](docs/PRD.md)에 현재 구현 경계와 인수 조건을 정리했습니다. `COORDINATOR` 연동을 제품 코드에서 개발하고 로컬 테스트로 검증합니다. 실제 Worker 실행 예시는 해당 경로를 검증하고 별도 승인을 받은 뒤 추가합니다.

## 라이선스

[MIT License](LICENSE)로 배포합니다.
