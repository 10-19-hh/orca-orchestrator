"""``python -m orca_orchestrator.topology`` — the workspace topology as ``topology@1`` JSON (read-only).

* ``--demo SCENE``: a bundled synthetic scene; runs nothing. Prints every step's
  document as a JSON array (``--step N`` for one step).
* ``--recorded FILE``: replays raw replies saved with ``--live --save-raw``.
* ``--live``: asks the Orca CLI and Git once (the read commands listed in
  ``topology.discovery``) and prints one document. ``--no-git`` skips Git.
* ``--text``: a short room-by-room summary instead of JSON.

Nothing here sends input to a terminal, starts or stops a session or Worker, or
changes Orca or Git. The Snapshot screen and console do not use this module.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .document import topology_document
from .model import SourceError, WorkerLink
from .parse import RawObservation, Reply, parse_observation
from .scenarios import SCENES
from .tracker import TopologyTracker

INTENT_TEXT = {"exit": "퇴장 (종료 확인)", "attention": "사용자 확인 필요", "unobservable": "관측 불가 (자리 유지)",
               "finishing": "Worker 작업 완료·정리 중", "work": "작업 중 (책상)", "work_done": "작업 끝남 (일어섬)",
               "ambient": "대기 (공용 공간)"}
PROVIDER_TEXT = {"claude": "Claude", "codex": "Codex", "other": "기타", "unknown": "미상"}


def demo_documents(name: str) -> list[dict[str, Any]]:
    """Every step of one scene through one tracker (later steps remember earlier ones)."""

    scene = SCENES[name]
    tracker = TopologyTracker("demo")
    out = []
    for i, (raw, links) in enumerate(scene.observations(), 1):
        tracker.demo = {"scenario": name, "label": scene.title, "step": i, "steps": len(scene.steps),
                        "evidence": scene.evidence, "note": "합성 DEMO · 실제 세션 관측 아님"}
        out.append(topology_document(tracker.observe(parse_observation(raw), links)))
    return out


def _reply_from_json(value: Any) -> Reply:
    if value is None:
        return Reply(asked=False)
    error = value.get("error")
    return Reply(value.get("result"), SourceError(error["category"], error.get("code")) if error else None)


def _reply_to_json(reply: Reply) -> Any:
    if not reply.asked:
        return None
    return {"result": reply.result, "error": None if reply.error is None else
            {"category": reply.error.category, "code": reply.error.code}}


def recorded_documents(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    tracker = TopologyTracker("recorded")
    out = []
    for item in data["observations"]:
        raw = RawObservation(datetime.fromisoformat(item["at"].replace("Z", "+00:00")),
                             {k: _reply_from_json(v) for k, v in item["replies"].items()})
        out.append(topology_document(tracker.observe(parse_observation(raw))))
    return out


def live_document(git: bool = True, save_raw: Path | None = None) -> dict[str, Any]:
    from .discovery import TopologyDiscovery

    raw = TopologyDiscovery(git=git).collect()
    if save_raw is not None:
        save_raw.write_text(json.dumps({"observations": [{"at": raw.at.isoformat(), "replies": {
            k: _reply_to_json(v) for k, v in raw.replies.items()}}]}, ensure_ascii=False, indent=1), encoding="utf-8")
    return topology_document(TopologyTracker("live").observe(parse_observation(raw)))


def text_lines(doc: dict[str, Any]) -> list[str]:
    head = "DEMO · " if doc["mode"] == "demo" else ("기록 재생 · " if doc["mode"] == "recorded" else "")
    rt = doc["runtime"]
    s = doc["summary"]
    lines = [f"{head}관측 {doc['observed_at']} · Runtime {rt['health']}"
             + (f" ({rt['app_version']})" if rt["app_version"] else ""),
             f"방 {s['rooms']} · 세션 {s['sessions']} · 작업 중 {s['working']} · 확인 필요 {s['attention']}"
             f" · 관측 불가 {s['unobservable']}" + (f" · 종료 {s['ended']}" if s["ended"] else "")]
    if doc["demo"]:
        lines.insert(1, f"장면 {doc['demo']['scenario']} ({doc['demo']['step']}/{doc['demo']['steps']}) · "
                        f"{doc['demo']['label']}")
    bad = [f"{d['source']} {d['availability']}" + (f" ({d['category']})" if d["category"] else "")
           for d in doc["diagnostics"]]
    if bad:
        lines.append("조회 제한: " + ", ".join(bad))
    for w in doc["worktrees"]:
        sessions = [x for x in doc["agent_sessions"] if x["worktree_id"] == w["worktree_id"]]
        devices = [t for t in doc["terminals"] if t["worktree_id"] == w["worktree_id"] and t["role"] == "terminal_device"
                   and t["listed"] and not t["exit_reported"]]
        lines.append(f"[{w['display_name'] or w['worktree_id']}] 방 {w['room']['state']} · 세션 {len(sessions)}"
                     + (f" · 터미널 장치 {len(devices)}" if devices else ""))
        for x in sessions:
            ident = "" if x["identity"]["confirmed"] else " · 임시 ID"
            extra = []
            if x["presentation"]["new"]:
                extra.append("새로 관측")
            if x["reappeared"]:
                extra.append("다시 관측")
            if x["incarnation_changed"]:
                extra.append("프로세스 교체")
            if x["possible_predecessor"]:
                extra.append("이전 세션과 동일성 미확인")
            if x["end_evidence"]:
                extra.append(f"근거 {x['end_evidence']}")
            if len(x["places"]) > 1:
                extra.append(f"패널 {len(x['places'])}곳")
            lines.append(f"  #{x['arrival']} {PROVIDER_TEXT[x['provider']]} {x['session_id']}{ident} · "
                         f"{INTENT_TEXT[x['presentation']['intent']]} · 상태 {x['activity']}"
                         + (f" (마지막 {x['last_activity']})" if x["activity"] == "unknown"
                            and x["last_activity"] != "unknown" else "")
                         + f" · {x['lifecycle']}" + (" · " + ", ".join(extra) if extra else ""))
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m orca_orchestrator.topology",
                                     description="Workspace topology (topology@1), read-only")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--demo", choices=sorted(SCENES), metavar="SCENE",
                     help="합성 DEMO 장면: " + ", ".join(SCENES))
    src.add_argument("--recorded", type=Path, metavar="FILE", help="--save-raw로 저장한 응답을 재생")
    src.add_argument("--live", action="store_true", help="Orca CLI와 Git을 한 번 읽기 전용으로 조회")
    src.add_argument("--list", action="store_true", help="DEMO 장면 목록")
    parser.add_argument("--step", type=int, metavar="N", help="--demo/--recorded: N번째 문서만 (1부터)")
    parser.add_argument("--text", action="store_true", help="JSON 대신 짧은 요약")
    parser.add_argument("--no-git", action="store_true", help="--live: Git 교차 확인 생략")
    parser.add_argument("--save-raw", type=Path, metavar="FILE",
                        help="--live: 원본 응답을 파일로 저장 (경로·제목 등이 들어 있으니 공유하지 말 것)")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    if args.list:
        for name, scene in SCENES.items():
            print(f"{name:24} {len(scene.steps)}단계 · {scene.title}")
        return 0
    if args.save_raw and not args.live:
        parser.error("--save-raw는 --live와 함께 씁니다.")
    if args.live:
        docs = [live_document(git=not args.no_git, save_raw=args.save_raw)]
    elif args.demo:
        docs = demo_documents(args.demo)
    else:
        docs = recorded_documents(args.recorded)
    if args.step is not None:
        if not 1 <= args.step <= len(docs):
            parser.error(f"--step은 1부터 {len(docs)}까지입니다.")
        docs = [docs[args.step - 1]]
    if args.text:
        print("\n\n".join("\n".join(text_lines(d)) for d in docs))
    elif len(docs) == 1:
        print(json.dumps(docs[0], ensure_ascii=False, indent=2))
    else:
        print(json.dumps(docs, ensure_ascii=False, indent=2))
    return 0


__all__ = ["demo_documents", "live_document", "main", "recorded_documents", "text_lines", "WorkerLink"]

if __name__ == "__main__":
    sys.exit(main())
