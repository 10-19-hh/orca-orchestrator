# Art Direction — Cozy Isometric Developer Office

상태: **검토용 방향 (READY FOR REVIEW)**. 최종 콘셉트 아트와 제작 에셋은 아직 없다. `mockups/`의 이미지는 코드로 그린 **배치 시안**이다. 비율·구역·조명 방향·펫 배치를 검토하는 용도이며, 최종 아트로 승인하거나 제작 에셋으로 쓰지 않는다. 모든 이미지에 `LAYOUT MOCKUP - NOT FINAL ART`가 찍혀 있다.

이전 Phase 2의 Concept A/B/C와 Hero(`../concepts/`, `../hero/`)는 디자인 실험의 기록으로 보존한다. 최종 아트로 승인하지 않는다.

## 1. 한 문장

**먼저 실제 개발 사무실로 읽히고, 그다음에 아늑한 픽셀 디오라마로 읽힌다.** 배치는 기능적인 사무실(REF-02·REF-04)에서, 분위기는 따뜻한 컷어웨이 디오라마(REF-01)에서, 가구의 재질감은 REF-03에서 가져온다.

## 2. 우선순위

1. 사무실로 읽히는 배치: 중앙 책상 클러스터, 마주 보는 자리, 통로
2. 기능적 구역: 업무·탕비실·라운지·회의·검증/검토·출입구
3. 따뜻한 조명: 창문의 방향성 있는 햇빛, 바닥의 빛 웅덩이, 부드러운 그림자
4. 정돈: 벽면은 비워 두고, 소품은 작게 모아 둔다
5. 캐릭터의 귀여움: 실루엣과 소품으로 표현
6. 장식 디테일

## 3. 화면 문법

- **투영**: 2:1 아이소메트릭, 바닥 타일 32×16 px. 위치는 1/8 타일 단위로 정해 픽셀 격자에 맞춘다.
- **확대**: 정수 배율, 최근접 이웃 보간만 쓴다. 시안은 1×·2×·3×로 저장했다.
- **방**: 두 벽(왼쪽 벽 x=0, 뒷벽 y=0)과 열린 앞면. 바닥 아래에 월넛 두께띠가 있다. 바깥은 단색 `#223344`.
- **빛**
  - 창문 두 개(뒷벽·왼쪽 벽)에서 오후의 따뜻한 빛이 들어온다.
  - 바닥 빛 웅덩이는 `sun` 30–35% 틴트이고, 뒷창에서 공기 중 빛기둥(7%)이 떨어진다.
  - 벽 아래쪽은 살짝 어둡고(AO), 가구마다 바닥 접촉 그림자가 있다.
- **재질**
  - 바닥: 오크 마루. 판자 4줄, 이음매가 엇갈리고 톤 변주는 5단계다.
  - 업무 가구: 월넛 다리·옆판과 오크 상판
  - 칸막이·의자: 사무용 회색
  - 모니터: 어두운 슬레이트

## 4. 팔레트 (시작점, 확정 아님)

| 역할 | 값 | 비고 |
| --- | --- | --- |
| Canvas | `#223344` | 방 바깥 |
| Warm wall / Wall shadow / Wall cap | `#D2C2AF` / `#A99C90` / `#E6DCCD` | 왼쪽 벽은 그림자 쪽으로 38% 섞음 |
| Oak floor / Floor shadow / Seam | `#C5A17A` / `#967456` / `#B08B63` | |
| Walnut / Walnut dark / Oak top | `#795743` / `#5E4334` / `#C79B6B` | 가구 |
| Office gray / dark | `#B4B5B0` / `#8E8F8A` | 칸막이·소품 |
| Monitor / screen on / screen off | `#303A43` / `#9FC6CF` / `#3B4752` | 켜진 화면은 작업 관측이 있는 자리만 |
| Plant / dark / light, Pot | `#78896B` / `#5D6B53` / `#93A383`, `#A6765A` | |
| Sunlight / Lamp | `#EAD5A3` / `#F1DDA8` | 빛은 틴트로만 |
| Fabric (sofa) / Rug | `#8E7F72` / `#A8917A` | 분홍·민트 대신 따뜻한 회갈색 |
| 상태 강조 | 주의 `#C98A2E`, 미확인 `#6E8296`, 오류 `#B5544A`, 정상 LED `#8BC28C` | 색만으로 구분하지 않고 `!`·`?`·`X`·`+` 글리프를 함께 쓴다 |

배경보다 캐릭터가 먼저 보여야 한다. 배경의 채도를 낮게 두고, 캐릭터의 주황(Claude)과 하늘색(Codex)이 가장 채도 높은 색이 되게 한다.

## 5. 피할 것

- 침실·게임룸 구조, 침대 중심 배치
- 장난감 같은 원색 가구, 과한 분홍·민트·청록
- 판타지 장식
- 모든 가구를 벽에 붙인 배치, 넓고 빈 바닥
- 평면 직사각형 가구, 배경이 캐릭터보다 강한 색
- 늘 열려 있는 큰 Inspector

시안 자체 검토에서 남은 약점 두 가지(§8):
- 빈 사무실(세션 0)은 클러스터 예정 구역이 비어 보인다.
- 앞줄 의자 등판이 펫 몸을 가린다.

## 6. 참고 이미지 (Visual Few-shot Reference Manifest)

사용자가 대화에 붙인 7장을 직접 보고 비교했다. 아래 링크는 사용자가 준 출처다. 이번 작업에서 페이지의 라이선스는 확인하지 않았다(`UNVERIFIED`). 제3자 이미지를 저장소에 복사하거나 제작 에셋에 쓰지 않는다. `mockups/`의 모든 형태는 이 저장소의 코드로 새로 그렸고, 참고 이미지의 픽셀을 가져오지 않았다.

| ID | 출처 (사용자 제공) | 이미지에서 본 것 | 가져온 것 | 가져오지 않은 것 | 권리 상태 |
| --- | --- | --- | --- | --- | --- |
| REF-01 | [Pixel Art Isometric Room (pixitclo)](https://www.buymeacoffee.com/pixitclo/pixel-art-isometric-room-1348291) | 두 벽 컷어웨이, 창문 빛줄기, 모브 벽, 원목 바닥, 남색 배경 | 컷어웨이 투영, 빛 웅덩이, 바닥 두께띠, 단색 배경 | 침대·침실 배치, 줄조명 | UNVERIFIED · 참고 전용 |
| REF-02 | [Game Dev Story 플레이 게시글 (LPZone)](https://www.lp.zone/t/lets-play-game-dev-story-for-android/1531) | 마주 보는 책상 클러스터, 회의 공간, 화이트보드, 원목 마루 | 중앙 클러스터와 마주 보기, 회의 구역의 위치 | 사람 캐릭터, 흰 CRT 모니터, 원색 | UNVERIFIED · 참고 전용 (상용 게임 화면) |
| REF-03 | [Pinterest 핀](https://br.pinterest.com/pin/651825746062625826/) (원작 서명 "Coalesce") | 원목 책상, 검은 사무 의자, 커피머신과 수납, 식물, 블라인드 | 원목 + 회색 의자 조합, 커피머신 위치, 소품을 상판에 모으기 | 촘촘한 벽 질감, 녹색 기둥 장식 | UNVERIFIED · 원작자 미확인 |
| REF-04 | [Pixel Office (Dribbble)](https://dribbble.com/shots/2848682-Pixel-Office) | 긴 방에 여러 클러스터, 통로, 화분 | 복수 클러스터와 통로 규칙, 방을 옆으로 늘리기 | 밝은 외곽 패턴, 벽 글씨, 채도 높은 모니터 | UNVERIFIED · 참고 전용 |
| REF-05 | [X 게시물](https://x.com/oikon48/status/2041811775428161789) (Clawd 스티커 목록 화면) | 각진 주황 몸통, 옆 돌기, 다리 4개, 네모 눈, 소품으로 행동 표현(커피, 전구, 헤드폰) | 실루엣 규칙과 "소품으로 상태 표현" | 스티커 외곽선, 판매 이미지, 상세 묘사 | UNVERIFIED · 상표/캐릭터 권리 확인 필요 (§7) |
| REF-06 | [Pixel Art Coffee Shop (Dulcahn)](https://www.deviantart.com/dulcahn/art/Pixel-Art-Coffee-Shop-1055619413) | 카운터와 머신, 테이블 주변 의자, 문 | 카운터 앞 접근 지점, 작은 모임 공간 | 카페 전체 배치, 큰 간판, 체크무늬 바닥 | UNVERIFIED · 참고 전용 |
| REF-07 | [Pixel Art Office Set (GraphicRiver)](https://graphicriver.net/item/pixel-art-office-set/144635) | 가구·하드웨어·사람을 독립 스프라이트로 나눈 세트 | 모듈형 스프라이트와 같은 방향·footprint 규칙 | 에셋 자체, 파란 기업형 색 | 상용 판매 에셋 · 구매·라이선스 없이는 사용 불가 |

## 7. 캐릭터 권리 주의

- Claude 펫은 Anthropic의 Claude Code 마스코트(Clawd)와 같은 실루엣을 따른다.
- Codex 펫은 OpenAI 제품을 가리킨다.
- 이 저장소는 개인 도구이고 펫은 세션 종류를 알아보게 하는 표지로 쓴다. 공개 배포·패키징 전에 두 회사의 상표·브랜드 사용 지침을 확인해야 한다. 필요하면 일반화한 대체 실루엣을 준비한다.
- 이 확인은 Phase 2.8(제작 에셋)의 진입 조건이다. [CHARACTER_SPEC](CHARACTER_SPEC.md#권리와-배포)에도 같은 내용이 있다.

## 8. 검토 결과와 열린 결정 (Phase 2.8 전에 사용자가 정할 것)

| 항목 | 현재 시안 | 결정 필요 |
| --- | --- | --- |
| 빈 사무실(0 세션) | 12×10 방에 공용 구역만 있고, 클러스터 예정 구역에 점선 표시와 플렉스 장식 | 이대로 둘지, 더 작은 방(예: 9×8)에서 시작해 첫 세션 때 넓힐지 |
| 앞줄 의자 등판 | 펫 몸통을 가림 (현실적이지만 덜 보임) | 등판을 낮출지, 반투명 처리할지 |
| 시간대 | 오후 햇빛 고정 | 실제 시간 연동 여부 (기본은 고정 권장) |
| 캐릭터 크기 | Claude 16×11, Codex 13×15 (1×) | 제작 캔버스 32×32에서 같은 픽셀 밀도로 다시 그릴 때 비율 |
| 팔레트 | §4 | 확정 |

## 9. 시안 목록 (`mockups/`)

각 파일은 1× 원본과 확대본(`@3x`, 개요는 `@2x`, 포즈 표는 `@4x`)이 함께 있다.

| 파일 | 장면 | 근거 데이터 |
| --- | --- | --- |
| `office-0-empty` | 세션 0: 공용 구역만, 클러스터 예정 구역 점선 | DEMO `empty-worktree` |
| `office-2` | 세션 2: 마주 보는 한 쌍, Codex 작업·Claude 대기 | DEMO `codex-and-claude` |
| `office-4` | 세션 4: 작업 2, 대기 1, 확인 필요 1 | DEMO `four-sessions` |
| `office-8` | 세션 8: 클러스터 2개, 넓어진 방 | DEMO `eight-sessions` |
| `office-4-idle-pantry-lounge` | 모두 대기: 탕비실·라운지·회의 테이블 | `four-sessions`의 의도를 `ambient`로 바꾼 합성 |
| `office-4-active-seated` | 모두 작업: 각자 책상 | `four-sessions`의 의도를 `work`로 바꾼 합성 |
| `studio-3-worktrees` | Worktree 3개 = 방 3개(세션 0·2·5) | DEMO `three-worktrees` |
| `behaviour-sequence` | Codex 한 마리: 소파 → 걸어감 → 앉아 작업 → 일어섬 → 복귀, 종료 확인 → 퇴장 | 합성 |
| `character-poses` | Claude·Codex 포즈 13종 | — |
| `ux-inspector-closed` / `-open` | 상단 바만 있는 기본 화면 / 펫 클릭 시 Inspector | DEMO `four-sessions` |

다시 만들기 (저장소 루트에서, 표준 라이브러리만 씀):

```powershell
python design/orca-studio/living-workspace/tools/layout.py   # layout-fixtures/*.json
python design/orca-studio/living-workspace/tools/assets.py   # asset-specification/asset-manifest.json
python design/orca-studio/living-workspace/tools/render.py   # mockups/*.png
```
