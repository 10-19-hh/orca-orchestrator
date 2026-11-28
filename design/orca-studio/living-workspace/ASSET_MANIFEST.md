# Asset Manifest — 제작할 모듈형 에셋

방을 한 장짜리 PNG로 그리지 않는다. 바닥·벽·가구·조명·그림자·캐릭터를 따로 그리고, Room Composer가 배치 데이터로 조립한다. 기계가 읽는 목록은 [`asset-specification/asset-manifest.json`](asset-specification/asset-manifest.json)이다. `tools/assets.py`가 레이아웃 시안과 같은 수치로 만든다.

**현재 상태: 모든 항목이 `status: spec`이다.** 목록과 규격만 있고 최종 에셋은 하나도 없다. 레이아웃 시안의 도형은 비율 확인용이며 제작 에셋이 아니다.

## 1. 공통 규격

| 항목 | 값 |
| --- | --- |
| 투영 | 아이소메트릭 2:1, 바닥 타일 32×16 px |
| 확대 | 정수 배율, 최근접 이웃 |
| 기준점(pivot) | 가구: footprint 원점 모서리(바닥, z=0)의 스프라이트 내 픽셀. 캐릭터: 32×32 캔버스의 `(16, 31)` |
| 깊이 정렬 기준 | 가구: footprint 중심의 바닥점. 캐릭터: 발 위치. 의자는 좌석과 등판을 나눈 두 스프라이트 |
| 층(layer) | `floor` · `floor_decal` · `light_overlay` · `shadow_overlay` · `wall` · `wall_decor` · `object` · `prop` |
| 방향 | 가구는 놓이는 방향만 제작(예: 책상 S·N, 소파 E). 캐릭터는 SE·SW·NE·NW이며 반전 허용 |
| 충돌 footprint | 막는 가구는 footprint와 같고, 걸을 수 있는 가구(러그·매트)는 `null` |
| 상호작용 지점 | footprint 원점 기준 타일 오프셋, 바라보는 방향, 사용 자세 위치 |
| 애니메이션 | 이름, 프레임 수, 프레임 시간(ms), 반복(`loop`·`once`), 재생 조건 |
| 권리 | 프로젝트 자체 제작. 제3자 에셋 0. `license`·`source`·`status` 필드 필수 |

## 2. 목록

### 환경 (16)

바닥·벽·창문·문·조명·그림자를 만든다.

- 바닥:
  - `environment.floor_oak`: 판자 4줄, 톤 변주 5단계
  - `environment.floor_edge`
- 벽:
  - `environment.wall_back`·`wall_left`: 걸레받이 포함
  - `wall_corner`: 안쪽 모서리와 열린 벽 끝
- 벽 장식:
  - 창문: `window_back`·`window_left`
  - `door_left`: 출입구
  - `whiteboard`
  - `status_board`: 검토 요청이 있으면 주의 LED
- 조명 overlay:
  - `light.sun_left`·`light.sun_back`: 바닥 빛 웅덩이, 공기 빛기둥
  - `light.lamp_pool`
- 그림자 overlay: `shadow.furniture_small`·`furniture_wide`·`pet`

### 가구·장치 (18)

| 구역 | asset |
| --- | --- |
| 업무 | `furniture.desk_modular`(2×1, 옆·뒤로 이어 붙임), `desk_divider`, `office_chair`(좌석/등판 분리). 모니터·키보드·개인 소품은 prop |
| 탕비실 | `pantry_counter`, `coffee_machine`, `cabinet_tall` |
| 라운지 | `sofa`, `coffee_table`, `rug`, `floor_lamp`, `plant_tall` |
| 회의 | `meeting_table`, `stool` |
| 검증·검토 | `device.verification_rack`. LED는 기록된 검증 결과를 보여 주며 에이전트 작업이 아님 |
| 수납·장식 | `bookshelf`, `credenza`, `printer_cart`(플렉스 장식) |
| 출입구 | `environment.doormat` (`spawn`·`exit` 지점) |

### 캐릭터 (3)

- `character.claude`, `character.codex`
- `character.unknown_agent`: Provider가 `unknown`·`other`일 때 쓰는 중립 펫

각 캐릭터에 4방향과 23종 애니메이션이 있다. 정체성은 [CHARACTER_SPEC](CHARACTER_SPEC.md)에 있다.

| 묶음 | 애니메이션 |
| --- | --- |
| 이동 | `walk`, `enter`, `exit` |
| 자리 | `sit_down`, `stand_up`, `work` |
| 근거가 있을 때만 | `typing`, `thinking`, `building` (topology@1에서는 재생 안 함) |
| 공용 공간 | `idle`, `look_around`, `coffee`, `eat`, `sit_sofa`, `sleep`, `stretch`, `sweep`, `chat` |
| 상태 | `notification`(`!`), `finishing`(`...`), `unknown`(`?`), `happy`, `error`(예약) |

## 3. 제작 전 확인 (Phase 2.8 진입 조건)

1. 사용자가 Master Concept Art를 승인한다. 승인 대상은 레이아웃 시안이 아니라 아티스트가 그린 대표 장면이다.
2. 제작 도구와 원본 파일 형식을 정한다(예: Aseprite `.aseprite` → PNG 시트 + JSON).
3. 캐릭터 권리를 확인한다([CHARACTER_SPEC § 권리와 배포](CHARACTER_SPEC.md#권리와-배포)).
4. 검수 기준:
   - 시트 투명도, 테두리 잘림 없음
   - 같은 픽셀 밀도
   - 기준점이 바닥에 닿음(한 칸 위·아래 오차 없음)
   - 같은 좌표에서 교체 가능
   - 깊이 정렬 시험(의자-펫-등판, 소파-펫)
5. 외부 에셋을 쓰면 출처·권리자·라이선스·변형 여부를 항목별로 적는다. REF-07 같은 판매 에셋은 구매·라이선스 확인 전에는 쓰지 않는다.
