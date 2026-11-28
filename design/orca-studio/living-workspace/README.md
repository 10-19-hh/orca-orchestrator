# Living Workspace — Phase 2.7 디자인 패키지

Orca Studio를 실제 개발 환경에 맞춰 커지는 작은 사무실로 그리기 위한 방향과 규격이다. Phase 2의 Concept A/B/C, Hero, 캐릭터·환경 시안(이 폴더의 상위 `design/orca-studio/`)은 기록으로 보존한다.

| 문서 | 내용 |
| --- | --- |
| [ART_DIRECTION](ART_DIRECTION.md) | Cozy Isometric Developer Office 방향, 팔레트, 참고 이미지 출처·역할·권리 상태, 검토할 열린 결정, 시안 목록 |
| [ROOM_LAYOUT](ROOM_LAYOUT.md) | 구역, 책상 클러스터, 세션 수에 따른 방 확장, 자리 배정, 여러 Worktree |
| [CHARACTER_SPEC](CHARACTER_SPEC.md) | Claude·Codex 펫 정체성, 상태 표현, 방향·프레임, 권리 주의 |
| [BEHAVIOR_SPEC](BEHAVIOR_SPEC.md) | 의도 → 행동, 이동·충돌·깊이, 공용 공간 사용, Inspector UX, Phase 4 검증 시나리오 |
| [ASSET_MANIFEST](ASSET_MANIFEST.md) | 제작할 모듈형 에셋과 메타데이터 규격 (모두 `spec`) |

판정 규칙과 데이터 계약은 아래 문서에 있다.

- [`docs/workspace-topology-v1.md`](../../../docs/workspace-topology-v1.md): 무엇이 방·자리·펫이 되는가
- [`docs/ambient-agent-behavior.md`](../../../docs/ambient-agent-behavior.md): 관측이 어떤 행동이 되는가
- [`docs/workspace-topology-audit.md`](../../../docs/workspace-topology-audit.md): 실제 Orca에서 확인한 것

## 폴더

- `mockups/`: 코드로 그린 **레이아웃 시안**(`LAYOUT MOCKUP - NOT FINAL ART`). 최종 콘셉트 아트가 아니다.
- `layout-fixtures/`: 합성 `topology@1` DEMO 문서로 계산한 방 배치(`orca-studio/layout-study@0`)
- `asset-specification/asset-manifest.json`: 에셋 제작 규격
- `tools/`: `layout.py`(Room Composer·Station Allocator 참고 구현), `iso.py`(그리기), `render.py`(시안), `assets.py`(규격). 표준 라이브러리만 쓰고 `../tools/pixel.py`를 재사용한다.

## 상태

- **방향과 규격: 사용자 검토 대기**
- **최종 아트: 없음.** Master Concept Art와 제작 에셋은 Phase 2.8에서 사용자 승인 뒤 만든다.
