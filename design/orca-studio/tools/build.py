"""Builds every Phase 2 design deliverable from the concept kit and the snapshot@1 fixtures.

    python3 design/orca-studio/tools/build.py

Standard library only; reads tests/fixtures/snapshot_v1/*.json and writes PNG and HTML under design/orca-studio/.
Nothing here is imported by the application.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from characters import CLAUDE, CODEX, EXPRESSIONS, claude, codex
from pixel import Canvas, hexc, text
import rooms
from rooms import (PALETTES, W, H, armchair, block, bookshelf, chair, desk, floor_lamp, link_device, pal, plant,
                   render, round_table, sofa, status_board, stool, verification_station)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "snapshot_v1"

STATE_ORDER = [
    ("connected-no-tasks", "Idle / 작업 미설정"), ("connected-none", "Ready / 검증 준비"),
    ("connected-passed", "부분 검증 통과"), ("connected-failed", "Failed / 검증 실패"),
    ("connected-protected", "Review required / 보호 경로 변경"), ("connected-approved", "Approved / 사용자 승인"),
    ("connected-changed", "승인 후 변경 · stale"), ("connected-interrupted", "End unknown / 로컬 실행 중단"),
    ("connected-worker", "End unknown / 기록된 Worker"), ("disconnected-passed", "Disconnected / Runtime 끊김"),
    ("unbound-passed", "바인딩 없음"), ("invalid-passed", "Run 응답 조회 불가"),
]

GLYPH = {"verified": "✓", "failed": "✗", "ready": "▸", "waiting": "…", "recheck": "↻"}


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def scene_state(doc: dict) -> dict:
    """Fixture -> what the room shows. Only fields that exist in snapshot@1; nothing is invented."""

    tasks = doc["tasks"]["tasks"]
    stale = [t["task_id"] for t in tasks if any(c["state"] == "stale" for c in t["checks"])]
    verified = sum(1 for t in tasks if t["state"] == "verified")
    mood = doc["presentation"]["mood"]
    held = [r for r in doc["recovery"].get("tasks", []) if r["state"] == "check_run" and r.get("attempt")]
    local_unknown = any(r["attempt"]["executor_kind"] == "local" for r in held)
    worker_unknown = any(r["attempt"]["executor_kind"] == "worker" for r in held)
    if mood == "end_unknown" and not local_unknown:
        pass  # a recorded Worker run: the verification station keeps showing recorded check results
    if any(t["state"] == "failed" for t in tasks):
        station, label = "fail", "FAIL"
    elif mood == "end_unknown" and local_unknown:
        station, label = "unknown", "?"
    elif tasks and verified == len(tasks):
        station, label = "pass", "PASS"
    elif verified:
        station, label = "partial", f"{verified}/{len(tasks)}"
    else:
        station, label = "idle", "--"
    kinds = [r["kind"] for r in doc["requests"]]
    attention = ("end_unknown" if "end_unknown" in kinds else "attention") if kinds else None
    return dict(mood=mood, tasks=[(t["task_id"], t["state"]) for t in tasks], stale_tasks=stale,
                health=doc["runtime"]["health"], station=station, station_label=label, attention=attention,
                approved=doc["review"].get("state") == "approved", unknown_at="board" if worker_unknown else "station")


def save(cv: Canvas, rel: str, scales=(1,)) -> list[str]:
    out = []
    for s in scales:
        path = ROOT / (rel if s == 1 else rel.replace(".png", f"@{s}x.png"))
        path.parent.mkdir(parents=True, exist_ok=True)
        cv.save(path, s)
        out.append(str(path.relative_to(ROOT)))
    return out


def img(src: str, w: int, h: int, k: int, alt: str, cls: str = "") -> str:
    return (f'<img class="px {cls}" src="{src}" width="{w * k}" height="{h * k}" alt="{html.escape(alt)}" '
            f'style="width:{w * k}px;height:{h * k}px">')


CSS = """
:root { --bg:#f3efe7; --ink:#2b2622; --muted:#7a7066; --line:#d9d1c4; --card:#fbf8f2; --accent:#b4562f; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI",
  "Apple SD Gothic Neo", "Noto Sans KR", system-ui, sans-serif; }
main { max-width: 1480px; margin: 0 auto; padding: 32px 24px 80px; }
h1 { font-size: 28px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 19px; margin: 40px 0 10px; border-bottom: 1px solid var(--line); padding-bottom: 6px; }
h3 { font-size: 15px; margin: 18px 0 6px; }
p, li { max-width: 78ch; }
.sub { color: var(--muted); margin: 0 0 20px; }
.px { image-rendering: pixelated; image-rendering: crisp-edges; display:block; }
.row { display:flex; gap:24px; flex-wrap:wrap; align-items:flex-start; }
.card { background:var(--card); border:1px solid var(--line); border-radius:6px; padding:14px 16px; }
.tag { display:inline-block; font-size:12px; padding:1px 7px; border:1px solid var(--line); border-radius:4px;
  color:var(--muted); margin-right:6px; }
.warn { border-color:#e0a63a; color:#8a5a00; background:#fff6e0; }
.concept-only { border:1px dashed #b4562f; color:#b4562f; padding:2px 8px; font-size:12px; font-weight:600; }
code, .mono { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 13px; }
table { border-collapse: collapse; font-size: 14px; }
td, th { border-bottom:1px solid var(--line); padding:5px 10px; text-align:left; vertical-align:top; }
.sw { display:inline-flex; flex-direction:column; width:66px; margin:0 6px 8px 0; font-size:11px; color:var(--muted); }
.sw i { display:block; height:30px; border-radius:3px; border:1px solid rgba(0,0,0,.08); margin-bottom:3px; }
nav a { margin-right: 14px; color: var(--accent); }
.demo-banner { background:#fff3d6; border:1px solid #e3b65b; color:#6b4a00; padding:6px 12px; border-radius:4px;
  display:inline-block; font-weight:600; font-size:13px; margin-bottom:12px; }
"""


def page(title: str, body: str, nav: bool = True) -> str:
    links = ('<nav><a href="../index.html">← 목록</a></nav>' if nav else "")
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>'
            f"<style>{CSS}</style></head><body><main>{links}{body}</main></body></html>")


# --- concepts ---------------------------------------------------------------------------------

CONCEPTS = {
    "A": ("Cozy Developer Studio", "따뜻한 원목·세이지·저녁 램프. 캐릭터별 개인 책상, 가운데 검증 캐비닛과 코르크 상태 보드, 앞쪽 라운지.",
          ["장시간 띄워 두기 편한 중간 명도", "가구와 캐릭터가 같은 따뜻한 세계에 속함", "보드·검증 장치가 생활 소품처럼 자연스러움"],
          ["에이전트가 늘면 방을 넓히거나 책상을 재배치해야 함", "소품이 많아 에셋 수가 가장 많음"]),
    "B": ("Modern Pixel Lab", "밝은 중성 타일, 모듈형 흰 책상(Pod)과 빈 Dock, 가운데 유리 검증 챔버, 벽면 상태 디스플레이.",
          ["다중 에이전트 확장에 가장 유리 (Dock 슬롯)", "상태 대비가 가장 명확", "에셋이 단순해 제작이 빠름"],
          ["밝고 차가워 장시간 앰비언트로는 피로·무미건조", "캐릭터의 따뜻함이 공간과 겉돎"]),
    "C": ("Midnight Workshop", "어두운 자두색 작업실, 큰 야경 창, 긴 작업대와 클램프 램프의 따뜻한 빛 웅덩이, 오실로스코프 검증 카트, 작은 네온 하나.",
          ["분위기와 몰입감이 가장 강함", "빛 웅덩이로 시선 유도가 쉬움"],
          ["어두워 상태 색과 텍스트 대비 확보가 어렵다", "조명 표현 비용이 가장 높음", "TUI의 '너무 어두움' 문제를 반복할 위험"]),
}

SCORES = {  # self-check only (see the spec): not an approval
    "A": [12, 12, 12, 8, 8, 12, 9, 5, 4],
    "B": [11, 12, 10, 5, 7, 13, 9, 3, 5],
    "C": [11, 11, 12, 9, 7, 9, 8, 3, 3],
}
SCORE_ITEMS = [("공간 구성·시각적 위계", 15), ("픽셀 아트 일관성", 15), ("캐릭터 매력·정체성", 15), ("조명·분위기", 10),
               ("가구·소품 완성도", 10), ("개발 도구 UI 가독성", 15), ("상태 표현 정확성", 10), ("장시간 사용 적합성", 5),
               ("확장·구현 가능성", 5)]


def build_concepts() -> None:
    doc = load("connected-passed")
    st = scene_state(doc)
    parts = ['<h1>Art Direction 후보 3종</h1><p class="sub">같은 상태(<code>connected-passed</code>: T-1 검증 통과, '
             'T-2 준비, 확인 요청 있음)를 세 방향으로 그렸다. 실제 크기 ×2 (480×270 → 960×540).</p>'
             '<div class="demo-banner">DEMO · 모의 데이터 — 실제 CLI·실행·기록 없음</div>']
    for key, (name, desc, pros, cons) in CONCEPTS.items():
        cv, _ = render(key, st)
        save(cv, f"concepts/concept-{key}.png", (1, 3))
        swatches = "".join(f'<span class="sw"><i style="background:{v}"></i>{k}</span>'
                           for k, v in PALETTES[key].items() if isinstance(v, str))
        total = sum(SCORES[key])
        parts.append(f'<h2>Concept {key} — {name}{" (추천)" if key == "A" else ""}</h2>'
                     f'<div class="row">{img(f"concept-{key}.png", W, H, 2, name)}'
                     f'<div class="card" style="max-width:440px"><p>{desc}</p><h3>장점</h3><ul>'
                     + "".join(f"<li>{p}</li>" for p in pros) + "</ul><h3>단점</h3><ul>"
                     + "".join(f"<li>{c}</li>" for c in cons) + f"</ul><p><b>자체 점검 점수 {total}/100</b> "
                     f'(승인 아님)</p><p><a href="concept-{key}@3x.png">×3 원본 PNG</a></p></div></div>'
                     f'<h3>팔레트</h3><div>{swatches}</div>')
    rows = "".join(f"<tr><td>{n}</td><td>{m}</td>" + "".join(f"<td>{SCORES[k][i]}</td>" for k in "ABC") + "</tr>"
                   for i, (n, m) in enumerate(SCORE_ITEMS))
    rows += "<tr><th>합계</th><th>100</th>" + "".join(f"<th>{sum(SCORES[k])}</th>" for k in "ABC") + "</tr>"
    parts.append(f'<h2>Visual Quality 자체 점검</h2><table><tr><th>항목</th><th>배점</th><th>A</th><th>B</th>'
                 f'<th>C</th></tr>{rows}</table><p class="sub">자기 점검용이며 디자인 승인이 아니다. 근거는 '
                 f'<code>art-direction/SPEC.md</code> §15.</p>')
    (ROOT / "concepts" / "index.html").write_text(page("Orca Studio · Concepts", "".join(parts)), encoding="utf-8")


# --- states -----------------------------------------------------------------------------------

def world_notes(st: dict, doc: dict) -> list[str]:
    notes = [f"검증 장치: {st['station_label']} ({st['station']})", f"LINK: {doc['runtime']['health']}"]
    notes.append("보드: " + (", ".join(f"{t} {GLYPH.get(s, '?')}" for t, s in st["tasks"]) or "NO PLAN"))
    if st["stale_tasks"]:
        notes.append("재확인 카드(↻): " + ", ".join(st["stale_tasks"]))
    if st["attention"]:
        notes.append("보드 깃발: " + ("?" if st["attention"] == "end_unknown" else "!"))
    if st["approved"]:
        notes.append("보드 APPR 표식")
    return notes


def build_states() -> None:
    parts = ['<h1>State Design Sheet — Concept A</h1><p class="sub">12개 <code>snapshot@1</code> 고정 예시를 그대로 '
             '읽어 그린 장면. 장면 ×2 + 같은 문서에서 UI가 보여 줄 텍스트. 펫의 표정은 반응일 뿐 작업이 아니다.</p>'
             '<div class="demo-banner">DEMO · 모든 장면은 모의 데이터</div>']
    for name, title in STATE_ORDER:
        doc = load(name)
        st = scene_state(doc)
        cv, _ = render("A", st)
        save(cv, f"state-studies/state-{name}.png", (1, 2))
        pr = doc["presentation"]
        reqs = "".join(f"<li>{html.escape(r['label'])} · {html.escape(r['summary'])}</li>" for r in doc["requests"])
        rt = doc["runtime"]
        ui = [f"Runtime: <b>{rt['health']}</b> ({rt['availability']})",
              f"바인딩: {'있음' if doc['binding']['terminal_handle_present'] else '없음'}",
              f"Run: {doc['run']['state']} ({doc['run']['availability']})",
              f"Worker(live): {doc['workers']['live']['availability']} · 기록 {len(doc['workers']['recorded']['items'])}건"]
        parts.append(
            f'<h2>{html.escape(title)} <span class="tag">{name}.json</span></h2><div class="row">'
            f'{img(f"state-{name}.png", W, H, 2, title)}<div class="card" style="max-width:430px">'
            f'<p><b class="mono">{html.escape(pr["mark"])} {html.escape(pr["label"])}</b><br>'
            f'<span class="tag">mood {pr["mood"]}</span><span class="tag">source {pr["source"]}</span></p>'
            f'<h3>방에서</h3><ul>' + "".join(f"<li>{html.escape(n)}</li>" for n in world_notes(st, doc)) +
            f'</ul><h3>UI에서</h3><ul>' + "".join(f"<li>{u}</li>" for u in ui) + "</ul>"
            + (f"<h3>확인 요청</h3><ul>{reqs}</ul>" if reqs else "<h3>확인 요청</h3><p>없음</p>") + "</div></div>")
    (ROOT / "state-studies" / "index.html").write_text(page("Orca Studio · States", "".join(parts)), encoding="utf-8")


# --- characters -------------------------------------------------------------------------------

def build_characters() -> None:
    bg = hexc("#e9e2d4")
    # 1) size comparison: 16 / 32 / 48, both mascots, with anchor marks
    cmp = Canvas(224, 60, bg)
    x = 4
    for size in (16, 32, 48):
        for fn in (codex, claude):
            sp = fn(size)
            cmp.blit(sp, x, 54 - size)
            cmp.set(x + size // 2, 55, hexc("#b4562f"))  # anchor: bottom centre
            x += size + 4
    save(cmp, "character-studies/size-compare.png")
    # 2) expressions at 32
    ex = Canvas(8 + 36 * len(EXPRESSIONS), 76, bg)
    for i, e in enumerate(EXPRESSIONS):
        ex.blit(codex(32, e), 4 + i * 36, 2)
        ex.blit(claude(32, e), 4 + i * 36, 40)
    save(ex, "character-studies/expressions.png")
    # 3) views and frames: front, side, breath, walk
    vw = Canvas(8 + 36 * 6, 76, bg)
    frames = [dict(), dict(breath=1), dict(view="side"), dict(view="side", step=1), dict(view="side", breath=1),
              dict(expression="blink")]
    for i, k in enumerate(frames):
        vw.blit(codex(32, **k), 4 + i * 36, 2)
        vw.blit(claude(32, **k), 4 + i * 36, 40)
    save(vw, "character-studies/views-frames.png")
    # 4) canvas grid study: 32x32 with body box and anchor
    for name, fn in (("codex", codex), ("claude", claude)):
        g = Canvas(32, 32, hexc("#f6f1e7"))
        for yy in range(32):
            for xx in range(32):
                if (xx // 8 + yy // 8) % 2:
                    g.set(xx, yy, hexc("#ece5d8"))
        g.blit(fn(32), 0, 0)
        g.set(16, 31, hexc("#b4562f"))
        g.set(15, 31, hexc("#b4562f"))
        save(g, f"character-studies/{name}-canvas32.png")
    # 5) in a room, at GUI size: crop of concept A
    cv, _ = render("A", dict(mood="ready", tasks=[("T-1", "verified"), ("T-2", "ready")], station="partial"),
                   poses=[dict(name="codex", at=(200, 150), expr="neutral"),
                          dict(name="claude", at=(240, 152), expr="blink")], select="codex")
    crop = Canvas(120, 68)
    for yy in range(68):
        for xx in range(120):
            crop.px[yy][xx] = cv.px[100 + yy][160 + xx]
    save(crop, "character-studies/in-room-crop.png")
    sw = lambda d: "".join(f'<span class="sw"><i style="background:rgb{c}"></i>{k}</span>' for k, c in d.items())
    body = f"""
<h1>Character Design Sheet</h1><p class="sub">32×32 캔버스(제작 크기), 발 사이 바닥 중앙이 기준점(주황 점).
모든 크기는 같은 형태 계획에서 다시 그렸다(확대·축소 아님). 왼쪽 위 광원, 검정 외곽선 대신 형태 색의 어두운 색조.</p>
<h2>크기 비교 — 16 / 32 / 48 (×4)</h2>{img("size-compare.png", 224, 60, 4, "size compare")}
<p>16은 표정이 2픽셀로 줄어 상태 반응을 읽기 어렵다. 48은 방(480×270) 안에서 가구 대비 너무 크다. <b>32를 채택</b>하고, 48은 Inspector 초상용 후보로만 둔다.</p>
<h2>실제 GUI 표시 크기 (방 안, ×2 = 화면 100%)</h2>{img("in-room-crop.png", 120, 68, 2, "in room")}
<p>선택 표시는 발밑 점선 고리 하나(과한 테두리·화살표 없음).</p>
<h2>표정 (×5): {", ".join(EXPRESSIONS)}</h2>{img("expressions.png", 8 + 36 * len(EXPRESSIONS), 76, 5, "expressions")}
<p>Codex는 얼굴 화면의 기호, Claude는 눈 모양·팔 자세로 표현한다. <code>fail</code>은 검증 장치를 보고 놀라는 반응용이며
"이 펫이 실패했다"는 뜻으로 쓰지 않는다(실패의 주체는 검증 장치). <code>rest</code>는 휴식 장식.</p>
<h2>방향·프레임 (×5): 정면 · 호흡 · 측면 · 걷기 · 측면 호흡 · 깜빡임</h2>{img("views-frames.png", 8 + 36 * 6, 76, 5, "views")}
<h2>32×32 캔버스 (×10)</h2><div class="row">{img("codex-canvas32.png", 32, 32, 10, "codex canvas")}{img("claude-canvas32.png", 32, 32, 10, "claude canvas")}</div>
<h2>팔레트</h2><h3>Codex</h3><div>{sw(CODEX)}</div><h3>Claude</h3><div>{sw(CLAUDE)}</div>
<h2>유지·변경</h2><table><tr><th></th><th>유지 (TUI 정체성)</th><th>바뀐 것</th></tr>
<tr><td>Codex</td><td>파란 구름 머리, 남색 화면 얼굴, <code>&gt;_</code></td><td>12×12 → 32×32, 3단 명암, 흰 몸통과 가슴 램프, 화면 기호로 표정</td></tr>
<tr><td>Claude</td><td>주황 크리터, 옆으로 뻗은 팔, 짧은 다리</td><td>14×7 → 32×32(실제 몸 26×19), 둥근 모서리, 세로 눈, 볼 터치, 팔 자세로 감정</td></tr></table>
"""
    (ROOT / "character-studies" / "index.html").write_text(page("Orca Studio · Characters", body), encoding="utf-8")


# --- environment ------------------------------------------------------------------------------

def build_environment() -> None:
    P = pal("A")
    bg = P["floor"]
    st = dict(station="partial", station_label="1/2", tasks=[("T-1", "verified"), ("T-2", "ready")],
              attention="attention")
    kit = Canvas(400, 150, bg)
    desk(kit, 8, 26, 84, P, glow=rooms.CYAN)
    desk(kit, 104, 26, 84, P, monitor=False, lamp=True)
    verification_station(kit, 206, 12, P, st)
    status_board(kit, 268, 6, 88, 36, P, st)
    link_device(kit, 362, 14, P, "connected")
    link_device(kit, 362, 34, P, "disconnected")
    chair(kit, 18, 76, P)
    stool(kit, 46, 82, P)
    armchair(kit, 66, 74, P)
    floor_lamp(kit, 110, 110, P)
    round_table(kit, 150, 90, P)
    sofa(kit, 190, 78, 80, P)
    bookshelf(kit, 288, 56, 42, 52, P)
    plant(kit, 340, 90, P, big=True)
    plant(kit, 370, 96, P)
    save(kit, "environment-studies/object-kit.png")
    # station states side by side
    sts = Canvas(240, 70, bg)
    for i, (s, lab) in enumerate((("idle", "--"), ("partial", "1/2"), ("pass", "PASS"), ("fail", "FAIL"),
                                  ("unknown", "?"))):
        verification_station(sts, 6 + i * 47, 16, P, dict(station=s, station_label=lab))
    save(sts, "environment-studies/verification-states.png")
    # zones / layering overlay
    cv, rects = render("A", dict(mood="ready", tasks=[("T-1", "verified"), ("T-2", "ready")], station="partial",
                                 station_label="1/2", attention="attention"))
    colors = {"board": "#e05a8a", "verification": "#3fb5a8", "codex_station": "#4f8fe0", "claude_station": "#e08a3f",
              "shared": "#9a6be0", "link": "#5fd38a", "codex": "#4f8fe0", "claude": "#e08a3f"}
    for k, (x, y, w, h) in rects.items():
        c = hexc(colors.get(k, "#ffffff"))
        for xx in range(x, x + w):
            cv.set(xx, y, c)
            cv.set(xx, y + h - 1, c)
        for yy in range(y, y + h):
            cv.set(x, yy, c)
            cv.set(x + w - 1, yy, c)
    for y in (rooms.FLOOR_TOP, rooms.FLOOR_BOTTOM):
        for x in range(0, W, 4):
            cv.set(x, y, hexc("#ffffff"))
    text(cv, 380, 248, "LOUNGE", hexc("#ffffff"))
    text(cv, 40, 210, "NOOK", hexc("#ffffff"))
    save(cv, "environment-studies/zones.png", (1, 2))
    body = f"""
<h1>Environment Design Sheet — Concept A</h1><p class="sub">16px 그리드, 바닥 y=84~258, 벽 높이 84px. 모든 가구는 같은 3/4 시점(윗면 + 앞면),
왼쪽 위 광원, 그림자는 오른쪽 아래로 3px.</p>
<h2>오브젝트 키트 (×3)</h2>{img("object-kit.png", 400, 150, 3, "kit")}
<p>책상(모니터형·스탠드형), 검증 캐비닛, 상태 보드, LINK 장치(연결/끊김), 의자, 스툴, 안락의자, 플로어 램프, 원형 공용 테이블, 소파, 책장, 화분.</p>
<h2>검증 장치 상태 (×3): 없음 · 부분 · 통과 · 실패 · 종료 미확인</h2>{img("verification-states.png", 240, 70, 3, "station states")}
<p>상태는 화면 글자 + 켜진 램프 위치(위 통과 / 가운데 확인 필요 / 아래 실패) + 기호로 표시한다. 색만으로 구분하지 않는다.
이 장치는 <b>기록된 결과</b>를 보여 준다. 실행 중 진행 표시는 snapshot@1에 없어서 그리지 않는다.</p>
<h2>공간 구역·가림 (×2)</h2>{img("zones.png", W, H, 2, "zones")}
<table><tr><th>구역</th><th>목적</th><th>상태 연결</th></tr>
<tr><td style="color:#4f8fe0">Codex Station</td><td>Codex 펫의 정체성 공간 (모니터, <code>&gt;_</code> 포스터)</td><td>없음 — 존재 자체가 Worker 작업이 아님</td></tr>
<tr><td style="color:#e08a3f">Claude Station</td><td>Claude 펫의 정체성 공간 (스탠드, 책, 선반)</td><td>없음</td></tr>
<tr><td style="color:#3fb5a8">Verification Station</td><td>검증 기록의 환경 오브젝트</td><td><code>tasks[].checks[].state</code>, mood failed / end_unknown</td></tr>
<tr><td style="color:#e05a8a">Status Board</td><td>작업 카드·확인 요청 깃발·승인 표식</td><td><code>tasks</code>, <code>requests</code>, <code>review.state</code></td></tr>
<tr><td style="color:#5fd38a">LINK</td><td>Runtime 연결만</td><td><code>runtime.health</code></td></tr>
<tr><td style="color:#9a6be0">Shared table</td><td>두 펫이 만나는 공용 공간 (장식)</td><td>없음</td></tr>
<tr><td>Lounge / Nook</td><td>대기·휴식 동선의 끝</td><td>mood idle·ready에서 휴식</td></tr></table>
<p>깊이 정렬: 모든 오브젝트와 펫은 바닥선(발 위치 / 가구 앞면 아래)이 작을수록 먼저 그린다. 벽 부착물(창·보드·포스터·LINK)은 항상 바닥 오브젝트보다 뒤.</p>
"""
    (ROOT / "environment-studies" / "index.html").write_text(page("Orca Studio · Environment", body), encoding="utf-8")


# --- hero mockup ------------------------------------------------------------------------------

HERO_CSS = """
:root { --bg:#1c1a19; --panel:#24211f; --panel2:#2b2825; --line:#3a3531; --ink:#ede6db; --muted:#a2988b;
  --dim:#776e63; --ok:#6fd49a; --bad:#ff7d6e; --warn:#ffc65c; --accent:#e8a36b; }
* { box-sizing:border-box; }
html, body { margin:0; background:#111; }
body { font:13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", system-ui, sans-serif; color:var(--ink); }
.app { width:1440px; height:900px; margin:0 auto; background:var(--bg); display:grid;
  grid-template-columns: 1fr 400px; grid-template-rows: 46px 1fr 28px; overflow:hidden; }
.top { grid-column: 1 / 3; display:flex; align-items:center; gap:18px; padding:0 16px; border-bottom:1px solid var(--line); background:var(--panel); }
.brand { font-weight:650; letter-spacing:.01em; }
.brand small { color:var(--muted); font-weight:400; margin-left:8px; }
.pill { display:inline-flex; align-items:center; gap:6px; padding:2px 9px; border:1px solid var(--line); border-radius:4px; color:var(--muted); white-space:nowrap; }
.pill b { color:var(--ink); font-weight:500; }
.demo { border-color:#8a6a2a; color:var(--warn); background:#2e2616; font-weight:600; }
.dot { width:7px; height:7px; border-radius:50%; display:inline-block; }
.spacer { flex:1; }
.mainc { padding:16px 20px 0; display:flex; flex-direction:column; gap:14px; min-width:0; }
.stage { position:relative; width:960px; height:540px; border:1px solid var(--line); }
.stage img { display:block; image-rendering:pixelated; }
.hover { position:absolute; background:rgba(28,26,25,.94); border:1px solid var(--line); padding:4px 8px; border-radius:4px; font-size:12px; white-space:nowrap; }
.hover span { color:var(--muted); }
.stage .zoom { position:absolute; right:8px; bottom:8px; font-size:11px; color:var(--muted); background:rgba(28,26,25,.8); padding:2px 6px; border-radius:3px; }
.lower { display:grid; grid-template-columns: 1.15fr 1fr; gap:14px; width:960px; flex:1; margin-bottom:14px; min-height:0; }
.panel { background:var(--panel); border:1px solid var(--line); border-radius:4px; overflow:hidden; }
.panel h4 { margin:0; padding:8px 12px; font-size:12px; font-weight:600; color:var(--muted); letter-spacing:.04em; text-transform:uppercase; border-bottom:1px solid var(--line); display:flex; gap:8px; align-items:center; }
.panel h4 .count { color:var(--ink); }
.list { padding:4px 0; }
.item { display:grid; grid-template-columns: 22px 1fr auto; gap:8px; padding:6px 12px; align-items:start; }
.item + .item { border-top:1px solid #2f2b28; }
.item .g { font-family:ui-monospace,"SF Mono",Menlo,monospace; font-weight:700; text-align:center; }
.item .t { font-weight:500; }
.item .s { color:var(--muted); font-size:12px; }
.item .r { color:var(--dim); font-size:12px; white-space:nowrap; }
.insp { border-left:1px solid var(--line); background:var(--panel); padding:16px 18px; overflow:hidden; display:flex; flex-direction:column; gap:14px; }
.insp header { display:flex; gap:12px; align-items:center; }
.portrait { width:64px; height:64px; background:#2f2b27; border:1px solid var(--line); border-radius:4px; display:grid; place-items:center; }
.portrait img { image-rendering:pixelated; }
.insp h2 { font-size:17px; margin:0; }
.insp .kind { color:var(--muted); font-size:12px; }
.kv { display:grid; grid-template-columns: 112px 1fr; gap:5px 10px; font-size:12.5px; }
.kv dt { color:var(--muted); }
.kv dd { margin:0; }
.sec { border-top:1px solid var(--line); padding-top:12px; }
.sec h5 { margin:0 0 8px; font-size:11px; font-weight:600; color:var(--muted); letter-spacing:.05em; text-transform:uppercase; }
.note { color:var(--muted); font-size:12px; }
.cli { font-family:ui-monospace,"SF Mono",Menlo,monospace; font-size:11.5px; background:#191716; border:1px dashed #4a433d; padding:7px 9px; border-radius:3px; color:#cfc6b8; }
.status { grid-column:1 / 3; display:flex; gap:18px; align-items:center; padding:0 16px; border-top:1px solid var(--line); background:var(--panel); color:var(--muted); font-size:12px; }
.ok { color:var(--ok); } .bad { color:var(--bad); } .warn { color:var(--warn); } .muted { color:var(--muted); }
"""


def build_hero() -> None:
    name = "connected-protected"
    doc = load(name)
    st = scene_state(doc)
    cv, rects = render("A", st, select="codex")
    save(cv, "hero/hero-scene.png", (1, 2))
    save(codex(48), "hero/portrait-codex48.png")
    rt, run, pr, rv = doc["runtime"], doc["run"], doc["presentation"], doc["review"]
    e = html.escape
    tasks_html = ""
    for t in doc["tasks"]["tasks"]:
        g = GLYPH.get(t["state"], "?")
        cls = {"verified": "ok", "failed": "bad", "ready": "warn"}.get(t["state"], "muted")
        checks = " · ".join(f'{c["check_id"]} {c["state"]}' for c in t["checks"])
        tasks_html += (f'<div class="item"><div class="g {cls}">{g}</div><div><div class="t">{e(t["task_id"])} '
                       f'{e(t["title"])}</div><div class="s">{e(checks)} · 요구사항 {", ".join(t["requirement_ids"])}'
                       f'</div></div><div class="r {cls}">{e(t["state_label"])} {t["passed"]}/{t["total"]}</div></div>')
    req_html = ""
    for r in doc["requests"]:
        g = "?" if r["kind"] == "end_unknown" else "!"
        req_html += (f'<div class="item"><div class="g warn">{g}</div><div><div class="t">{e(r["label"])}</div>'
                     f'<div class="s">{e(r["summary"])}</div></div><div class="r">{e(r["tab"].replace("tab-", ""))}</div></div>')
    blockers = "".join(f"<li>{e(b['text'])}</li>" for b in rv.get("blockers", []))
    cx, cy, cw, ch = rects["claude"]
    hover = (f'<div class="hover" style="left:{(cx + cw) * 2 + 6}px;top:{cy * 2 - 6}px">Claude 펫 '
             f'<span>· 마스코트 · 휴식 중</span></div>')
    body = f"""<div class="app">
<div class="top"><span class="brand">Orca Studio<small>{e(doc["tasks"]["label"])}</small></span>
<span class="pill demo">DEMO · 모의 데이터 — 실제 CLI·실행·기록 없음</span><span class="spacer"></span>
<span class="pill"><span class="dot" style="background:var(--ok)"></span>Runtime <b>연결됨</b> · Orca {e(rt["status"]["app_version"])} 검증 버전</span>
<span class="pill">바인딩 <b>{"있음" if doc["binding"]["terminal_handle_present"] else "없음"}</b></span>
<span class="pill">Run <b class="mono">{e(run["run_id"] or "—")}</b></span>
<span class="pill">Worker 전송 <b>차단</b></span></div>
<div class="mainc">
<div class="stage"><img src="hero-scene@2x.png" width="960" height="540" alt="Concept A workspace">{hover}<span class="zoom">×2 · 480×270</span></div>
<div class="lower">
<div class="panel"><h4>Task Board <span class="count">{len(doc["tasks"]["tasks"])}</span><span class="spacer"></span><span>검증 기록 기준</span></h4><div class="list">{tasks_html}</div></div>
<div class="panel"><h4>Review Queue <span class="count">{len(doc["requests"])}</span></h4><div class="list">{req_html}</div></div>
</div></div>
<aside class="insp"><header><div class="portrait"><img src="portrait-codex48.png" width="48" height="48" alt=""></div>
<div><h2>Codex 펫</h2><div class="kind">마스코트 · 동반자 (companion)</div></div></header>
<dl class="kv"><dt>Worker 연결</dt><dd>없음 <span class="muted">— Worker를 실시간 관측하지 않음 (not_implemented)</span></dd>
<dt>작업 동작</dt><dd>허용 안 됨 <span class="muted">(worker_actions_allowed = false)</span></dd>
<dt>지금 보이는 것</dt><dd>보드 아래에서 <b>!</b> 반응 — 확인 요청이 있어서 (표현일 뿐 작업 아님)</dd></dl>
<div class="sec"><h5>현재 상태 (presentation)</h5><div class="mono" style="font-size:13px">{e(pr["label"])}</div>
<div class="note">출처 {e(pr["source"])} · 판단 {e(pr["judgment"])} · 마지막 조회 {e(doc["taken_at"])}</div></div>
<div class="sec"><h5>변경 검토 · 승인 상태 {e(rv["state"])}</h5><ul style="margin:0;padding-left:18px">{blockers}</ul>
<div class="note">보호 경로 변경 {sum(len(t["protected"]) for t in rv.get("tasks", []))}건 · 연결 없는 변경 {len(rv.get("unlinked", []))}건</div></div>
<div class="sec"><h5>읽기 전용 화면</h5><p class="note" style="margin:0 0 8px">이 앱은 실행·승인·재시도·전송을 하지 않는다. 검토와 결정은 터미널에서:</p>
<div class="cli">python -m orca_orchestrator.review --tasks … --results …</div></div>
<div class="sec note" style="margin-top:auto">Tab 대상 이동 · Enter 상세 · Esc 선택 해제 · M 움직임 · +/− 확대(정수배)</div>
</aside>
<div class="status"><span>snapshot@1 · {e(doc["mode"])} · {e(name)}.json</span><span>Runtime 출처 {e(rt["source"])} · 작업 출처 {e(doc["tasks"]["source"])}</span>
<span class="spacer" style="flex:1"></span><span>움직임: 보통</span><span>확대 ×2</span><span>로컬 실행·다른 세션 진행은 이 문서에 없음</span></div>
</div>"""
    (ROOT / "hero" / "hero-mockup.html").write_text(
        f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>Orca Studio · Hero mockup</title>'
        f'<style>{HERO_CSS}</style></head><body>{body}</body></html>', encoding="utf-8")


# --- wireframes ------------------------------------------------------------------------------

WF_CSS = """
.wf { position:relative; background:#fff; border:1px solid #bbb; font-size:11px; color:#555; }
.wf div { position:absolute; border:1px solid #9a9a9a; background:#f2f2f2; display:flex; align-items:center; justify-content:center; text-align:center; padding:2px; }
.wf .scene { background:repeating-linear-gradient(45deg,#e9e3d6,#e9e3d6 6px,#e2dbcc 6px,#e2dbcc 12px); }
.wf .strong { background:#ddd; font-weight:600; color:#333; }
.wfc { display:inline-block; margin:0 24px 28px 0; vertical-align:top; }
.wfc p { margin:6px 0 0; max-width:none; font-size:13px; }
"""


def wf(w, h, boxes) -> str:
    inner = "".join(f'<div class="{c}" style="left:{x}px;top:{y}px;width:{bw}px;height:{bh}px">{t}</div>'
                    for x, y, bw, bh, t, c in boxes)
    return f'<div class="wf" style="width:{w}px;height:{h}px">{inner}</div>'


def build_wireframes() -> None:
    s = 0.5
    main = [(0, 0, 720, 23, "Top Bar · 워크스페이스 · DEMO · Runtime · 바인딩 · Run", "strong"),
            (10, 31, 480, 270, "Workspace 480×270 (×2 → 960×540)", "scene"),
            (10, 309, 260, 116, "Task Board", ""), (278, 309, 212, 116, "Review Queue", ""),
            (500, 23, 220, 413, "Inspector (접기 가능)", ""), (0, 436, 720, 14, "Status Bar", "strong")]
    closed = [(0, 0, 720, 23, "Top Bar", "strong"), (10, 31, 640, 360, "Workspace (×3 가능 시 확대)", "scene"),
              (660, 31, 50, 360, "요청 3 ▸", ""), (10, 399, 640, 30, "Task strip", ""), (0, 436, 720, 14, "Status Bar", "strong")]
    review = [(0, 0, 720, 23, "Top Bar", "strong"), (10, 31, 480, 270, "Workspace", "scene"),
              (10, 309, 480, 116, "Review Queue (확장) · 요청별 근거 탭 링크", "strong"),
              (500, 23, 220, 413, "Inspector: 선택한 요청 근거 · CLI 명령(텍스트)", ""), (0, 436, 720, 14, "Status Bar", "strong")]
    narrow = [(0, 0, 390, 23, "Top Bar (DEMO 유지)", "strong"), (5, 29, 380, 214, "Workspace ×1 → 전체 폭 맞춤(정수배)", "scene"),
              (5, 249, 380, 26, "상태 한 줄 · ✓/✗/?/! 기호", "strong"), (5, 281, 380, 90, "Review Queue", ""),
              (5, 377, 380, 60, "Task Board (접힘)", ""), (0, 443, 390, 14, "Status", "strong")]
    mini = [(0, 0, 300, 80, "", ""), (6, 6, 120, 68, "방 축소 크롭 240×135 ×0.5 (펫·보드·검증 장치만)", "scene"),
            (132, 6, 162, 18, "DEMO · Runtime ●", "strong"), (132, 28, 162, 22, "✓ 검증 통과 · 검토 필요", ""),
            (132, 54, 162, 20, "요청 2 · 클릭 → 앱 열기", "")]
    items = [("메인 (Layout A: 우측 Inspector)", wf(720, 450, main), "기본. 장면 960×540 고정, Inspector 400px."),
             ("Inspector 닫힘", wf(720, 450, closed), "선택 해제(Esc) 또는 접기. 장면을 ×3으로 키울 수 있으면 키운다."),
             ("Review Queue 확장 (Layout B 요소)", wf(720, 450, review), "하단 패널에 요청 근거. 명령은 텍스트로만."),
             ("좁은 화면 (≤ 900px)", wf(390, 457, narrow), "장면은 정수 배율만. 정보는 세로로."),
             ("Mini Mode 개념", wf(300, 80, mini), "항상 위 작은 창. 핵심 상태 한 줄 + 요청 수. 애니메이션 기본 정지.")]
    body = ['<h1>UI Wireframes</h1><p class="sub">회색 박스 = 앱 UI, 빗금 = 픽셀 장면. 실제 비율의 50%.</p>'
            '<h2>레이아웃 후보 비교</h2><table><tr><th></th><th>정보 밀도</th><th>장시간 사용</th><th>판단</th></tr>'
            '<tr><td>A. 장면 + 접히는 우측 Inspector</td><td>중간·조절 가능</td><td>장면이 항상 같은 크기로 보여 안정적</td><td><b>채택</b></td></tr>'
            '<tr><td>B. 장면 + 하단 Activity/Review</td><td>높음</td><td>세로 공간을 먹어 장면이 작아짐</td><td>Review 확장 상태로만 차용</td></tr>'
            '<tr><td>C. 장면 + 도구 패널 전환</td><td>낮음</td><td>전환 비용, 상태를 놓치기 쉬움</td><td>미채택</td></tr></table>']
    body += [f'<div class="wfc"><h3>{t}</h3>{b}<p>{n}</p></div>' for t, b, n in items]
    (ROOT / "ui-wireframes" / "index.html").write_text(
        page("Orca Studio · Wireframes", f"<style>{WF_CSS}</style>" + "".join(body)), encoding="utf-8")


def build_index() -> None:
    entries = [("hero/hero-mockup.html", "Hero Mockup (추천안 A, 1440×900)", "hero/hero-scene.png"),
               ("concepts/index.html", "Art Direction 후보 3종 + 자체 점검", "concepts/concept-B.png"),
               ("character-studies/index.html", "Character Design Sheet", None),
               ("environment-studies/index.html", "Environment Design Sheet", "environment-studies/zones.png"),
               ("state-studies/index.html", "State Design Sheet (12 fixtures)", "state-studies/state-connected-failed.png"),
               ("ui-wireframes/index.html", "UI Wireframes", None),
               ("art-direction/SPEC.md", "Design Specification (문서)", None)]
    cards = "".join(f'<div class="card" style="width:330px"><h3><a href="{h}">{t}</a></h3>'
                    + (f'<img class="px" src="{p}" style="width:300px;height:169px">' if p else "") + "</div>"
                    for h, t, p in entries)
    body = (f'<h1>Orca Studio — Phase 2 Art Direction</h1><p class="sub">디자인 검토용 산출물. 구현이 아니다. '
            f'모든 장면은 <code>tests/fixtures/snapshot_v1</code>의 모의 데이터로 그렸다.</p>'
            f'<div class="demo-banner">DEMO · 모의 데이터</div><div class="row">{cards}</div>'
            f'<p class="sub">다시 만들기: <code>python3 design/orca-studio/tools/build.py</code> (표준 라이브러리만)</p>')
    (ROOT / "index.html").write_text(page("Orca Studio · Design Review", body, nav=False), encoding="utf-8")


if __name__ == "__main__":
    for d in ("concepts", "hero", "character-studies", "environment-studies", "state-studies", "ui-wireframes"):
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    build_concepts()
    build_states()
    build_characters()
    build_environment()
    build_hero()
    build_wireframes()
    build_index()
    (ROOT / "palettes").mkdir(exist_ok=True)
    status = {"ok": "#5fd38a", "warn": "#ffc24d", "fail": "#ff6b5e", "neutral": "#8b93a3", "accent": "#7fe0f0"}
    (ROOT / "palettes" / "palettes.json").write_text(json.dumps(
        {"concepts": PALETTES, "status_in_world": status,
         "mascots": {"codex": {k: "#%02x%02x%02x" % v for k, v in CODEX.items()},
                     "claude": {k: "#%02x%02x%02x" % v for k, v in CLAUDE.items()}}},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("built", ROOT)
