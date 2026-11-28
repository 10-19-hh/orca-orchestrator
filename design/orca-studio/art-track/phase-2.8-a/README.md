# Phase 2.8-A — Master Concept Art 후보 (미승인)

2026-11-29에 Antigravity의 이미지 생성으로 만든 **자체 생성 콘셉트 후보**다. 제3자 참고 이미지는 이 폴더에 없다. 참고 이미지의 출처·권리 상태는 [ART_DIRECTION §6](../../living-workspace/ART_DIRECTION.md#6-참고-이미지-visual-few-shot-reference-manifest)에 있다.

- 각 JPG에 Google 이미지 생성의 C2PA 콘텐츠 자격 증명이 들어 있다. AI 생성물이라는 출처 표시이므로 지우지 않는다. 계정·경로 같은 개인 정보는 없다(확인함).
- 생성 프롬프트 원문은 이 저장소에 보존되지 않았다.
- **어느 후보도 최종 승인되지 않았다.** 제작 에셋으로 쓰지 않는다.

| 파일 | 후보 | 메모 |
| --- | --- | --- |
| `isometric_pixel_office_*.jpg` | 최초 생성 테스트 | 도구 동작 확인용 |
| `concept_a_warm_oak_*.jpg` | Concept A — Warm Oak | |
| `concept_b_soft_mauve_*.jpg` | Concept B — Soft Mauve | |
| `concept_c_walnut_*.jpg` | Concept C — Walnut Developer Studio | **기본 방향.** Claude가 각진 정육면체로 그려져 있어 Clawd 실루엣과 다르다 |
| `concept_c_v2_refined_walnut_*.jpg` | Concept C v2 — Refined Walnut | 공간 배치 개선은 참고하되 색과 분위기는 C를 유지 |

## C v3 요구사항 (다음 제작)

- **공간:**
  - Warm greige 벽, 짙은 월넛 책상·수납장, 따뜻한 오크 바닥
  - 중앙에 4인용 책상 클러스터
  - 회의 테이블 1, 소파 1, 탕비실, 명확한 출입구
  - 자연광과 가구의 입체감
- **캐릭터:**
  - Codex: 파란 구름형 머리와 디지털 터미널 얼굴
  - Claude: 기존 TUI의 납작하고 각진 Clawd 실루엣. 정육면체나 문어형은 쓰지 않는다.
- **전체:** 일관된 픽셀 밀도

## 생성 환경 메모

- Antigravity 이미지 생성은 실제로 성공했다. 이후 `RESOURCE_EXHAUSTED`(429)가 발생했다.
- 일반 Gemini·Claude·GPT 모델 할당량과 이미지 생성 가능 여부는 따로 확인해야 한다. 텍스트 모델이 되더라도 이미지 생성이 막혀 있을 수 있다.
- 다음 제작은 ChatGPT 등 사용 가능한 이미지 생성 환경에서 한다. 생성 뒤 프롬프트와 도구·날짜를 이 표에 같이 기록한다.
