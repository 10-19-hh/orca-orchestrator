# Orca Orchestrator

PRD의 요구사항을 검증 가능한 개발 작업으로 나누고, Orca를 통해 Claude Code와 Codex에 배정하기 위한 Python 프로젝트입니다. 작업 결과와 테스트를 요구사항에 연결하고, 최종 변경은 사용자가 승인하는 흐름을 목표로 합니다.

> **현재 Worker 작업 전송은 보류 상태입니다.** 이 버전은 Orca 상태를 읽어 보여주기만 하며, Worker를 시작하거나 모델에 작업을 보내지 않습니다.

## 한눈에 보기

- **Orca 터미널에서 보이는 것:** Orca 터미널 탭에서 `python -m orca_orchestrator`를 실행하면 Runtime 연결·버전, Coordinator 바인딩, 현재 Run, 작업 전송 보류 이유, 사용량(`미확인`)이 한 화면에 나옵니다. 화면은 읽기 전용입니다.
- **Orca 없이 데모 열기:** 저장소 루트의 PowerShell에서 아래 두 줄을 실행합니다. 포함된 응답 픽스처만 재생하므로 Orca가 꺼져 있어도 열리고 실제 CLI는 호출하지 않습니다.

```powershell
$env:PYTHONPATH = "src;tests"
python -m orca_orchestrator --demo
```

데모 화면(`--demo --once` 출력):

```text
Orca Orchestrator · 읽기 전용 상태 · 데모 (connected)
조회 시각 2026-11-26 16:38:43 · 실제 CLI를 호출하지 않음
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
────────────────────────────────────────────────────────────────────────
r 새로고침 · q 종료
```

`r`은 다시 조회, `q`는 화면 종료입니다. 데모 장면과 실제 실행 방법은 [상태 화면](#상태-화면)에 있습니다.

## 지금 써볼 수 있는 범위

`Python 제어 코드 → Orca CLI 어댑터 → Orca Runtime`

- **Orca 어댑터**는 Runtime 상태와 현재 Run을 조회하고, Orca 터미널 바인딩이 있는 경우 Run을 생성할 수 있습니다. Experiment 1은 Orca `1.4.264`에서 `COORDINATOR`의 Run 생성과 바인딩을 확인했습니다. Run은 실행 컨텍스트이며 생성만으로 Worker가 시작되지는 않습니다.
- **읽기 전용 상태 화면**은 Runtime 연결·버전, Coordinator 바인딩, 현재 Run, 작업 전송 보류 이유를 한 화면에 보여줍니다. Orca 상태를 바꾸지 않습니다.
- **작업 전송 게이트**는 연결·버전·바인딩·승인·과금 조건과 사용량 대기 상태를 평가합니다. 현재 빌드는 Worker를 시작하거나 작업을 보내지 않습니다.
- **개발 예정**은 실제 Worker 기동·상태 제어, 사용량 연동, PRD 작업 그래프, 실행 복구, 요구사항별 검증과 승인입니다.

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

테스트는 가짜 명령 실행기와 응답 픽스처로 argv 구성, 응답 파싱, 바인딩 누락, 타임아웃 재실행 차단, 전송 차단과 한도 대기, 상태 화면의 상황별 표시를 확인합니다. 실행기 자체 테스트에는 로컬 Python 서브프로세스를 사용합니다. Orca나 모델은 호출하지 않습니다.

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

- 화면은 `status --json`과 바인딩된 터미널의 `orchestration run-current --json`만 실행합니다. 그 밖의 명령은 실행 전에 거부합니다.
- Runtime이 꺼졌거나 바인딩이 없으면 하단에 한 줄 안내가 나옵니다. 해석할 수 없는 응답은 `조회 불가`로 표시합니다.
- 사용량은 아직 연동되지 않아 `미확인`으로 표시합니다. 작업 전송은 항상 보류로 표시됩니다.
- 화면을 닫으면 이 터미널만 원래 화면으로 돌아갑니다. Orca Runtime, Run, 사용자 세션에는 영향이 없습니다.

## 구독과 과금

실제 실행을 승인하기 전, 사용자가 공식 Claude Code·Codex CLI에서 자기 구독으로 로그인하고 해당 경로의 **추가 사용과 자동 충전을 꺼 둡니다**. 기본 정책은 구독에 포함된 사용량만 쓰는 것입니다. 프로그램에 한도 도달이 기록되면 리셋을 기다립니다. 초기화 시각이 불명확하면 대기를 유지하고, 시각이 지나면 새 사용량 관측으로 여유를 확인한 후 대기를 해제합니다. API 키로 자동 전환하거나 추가 크레딧을 구매하지 않습니다.

Worker 작업 전송에는 실제 실행 경로의 구독 인증, 구독 외 과금 배제, 해당 자동화 방식에 적용되는 공식 정책 근거 및 별도 사용자 승인이 필요합니다. 현재 Step 0의 4-A는 `CONDITIONAL`이며 4-B·4-C와 Feasibility Gate는 보류 상태입니다.

## 개발 현황

[PRD v0.2](docs/PRD.md)에 현재 구현 경계와 인수 조건을 정리했습니다. `COORDINATOR` 연동을 제품 코드에서 개발하고 로컬 테스트로 검증합니다. 실제 Worker 실행 예시는 해당 경로를 검증하고 별도 승인을 받은 뒤 추가합니다.

## 라이선스

[MIT License](LICENSE)로 배포합니다.
