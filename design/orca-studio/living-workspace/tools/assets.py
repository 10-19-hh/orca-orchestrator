"""Writes the asset specification (what the artist produces) from the layout-study geometry.

python design/orca-studio/living-workspace/tools/assets.py

Every entry is a production requirement with status ``spec``: nothing listed here
exists as final art yet. Footprints, heights and interaction points come from
``layout.py`` so the spec and the layout fixtures cannot drift apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import layout as L

OUT = Path(__file__).resolve().parents[1] / "asset-specification"
TILE = (32, 16)
LICENSE = {"license": "project-original (to be drawn; rights held by the project owner)", "third_party": None,
           "source": "not produced yet - geometry from tools/layout.py", "status": "spec"}


def sprite_box(w: float, d: float, h: int) -> dict:
    """Native size, pivot (footprint origin on the floor) and depth anchor (footprint centre) in sprite px."""

    width = int((w + d) * TILE[0] / 2) + 2
    height = int((w + d) * TILE[1] / 2) + h + 2
    pivot = (int(d * TILE[0] / 2) + 1, h + 1)
    anchor = (int(pivot[0] + (w - d) * TILE[0] / 4), int(pivot[1] + (w + d) * TILE[1] / 4))
    return {"native_px": [width, height], "pivot_px": list(pivot), "depth_anchor_px": list(anchor),
            "bounding_box_px": [0, 0, width, height]}


FURNITURE = {
    # asset_id: (layer, orientations, notes)
    "furniture.desk_modular": ("object", ["S", "N"], "2x1 tile desk; joins side by side and back to back; "
                               "monitor, keyboard and personal props are separate sprites"),
    "furniture.desk_divider": ("object", ["EW"], "low fabric screen between facing desks"),
    "furniture.office_chair": ("object", ["S", "N", "E", "W"], "seat and back are separate sprites so a seated "
                               "pet sorts between them"),
    "furniture.pantry_counter": ("object", ["S"], "base cabinets with stone top; joins to cabinet_tall"),
    "furniture.coffee_machine": ("prop", ["S"], "sits on the counter (z 14)"),
    "furniture.cabinet_tall": ("object", ["S"], "pantry cabinet or fridge"),
    "furniture.meeting_table": ("object", ["EW"], "shared table in front of the whiteboard"),
    "furniture.stool": ("object", ["ANY"], "meeting seat"),
    "furniture.sofa": ("object", ["E"], "three-seat sofa against the left wall; sit and lie points"),
    "furniture.coffee_table": ("object", ["ANY"], "lounge table"),
    "furniture.plant_tall": ("object", ["ANY"], "pot + foliage; muted green"),
    "furniture.floor_lamp": ("object", ["ANY"], "warm light pool overlay included"),
    "furniture.bookshelf": ("object", ["S"], "storage against the back wall (wide rooms)"),
    "furniture.credenza": ("object", ["W"], "low storage, front-right corner of wide rooms"),
    "furniture.printer_cart": ("object", ["ANY"], "flex decor: removed when a desk cluster takes its spot"),
    "furniture.rug": ("floor_decal", ["ANY"], "lounge rug, under furniture"),
    "environment.doormat": ("floor_decal", ["ANY"], "entrance mat"),
    "device.verification_rack": ("object", ["S"], "local verification device; LEDs show recorded results, "
                                 "never an agent's work"),
}

ENVIRONMENT = [
    ("environment.floor_oak", "floor", [32, 16], [16, 8], "2:1 diamond, 4 plank rows, 5 tone variants"),
    ("environment.floor_edge", "floor", [32, 24], [16, 8], "front slab edge, left and right faces"),
    ("environment.wall_back", "wall", [16, 76], [0, 76], "one tile of back wall (y=0 plane), baseboard included"),
    ("environment.wall_left", "wall", [16, 76], [16, 76], "one tile of left wall (x=0 plane)"),
    ("environment.wall_corner", "wall", [8, 80], [4, 80], "inner back-left corner and the two open wall ends"),
    ("environment.window_back", "wall_decor", [36, 48], [0, 46], "2-tile window, back wall"),
    ("environment.window_left", "wall_decor", [36, 48], [36, 46], "2-tile window, left wall"),
    ("environment.door_left", "wall_decor", [24, 52], [24, 40], "entrance door, left wall; spawn and exit"),
    ("environment.whiteboard", "wall_decor", [48, 40], [0, 42], "meeting whiteboard with notes"),
    ("environment.status_board", "wall_decor", [24, 26], [0, 52], "review board above the rack; shows "
                                                                    "attention when a request is open"),
    ("light.sun_left", "light_overlay", [64, 48], [0, 0], "warm floor patch from the left window (multiply/add)"),
    ("light.sun_back", "light_overlay", [64, 48], [0, 0], "warm floor patch and air shaft from the back window"),
    ("light.lamp_pool", "light_overlay", [48, 24], [24, 12], "floor lamp light pool"),
    ("shadow.furniture_small", "shadow_overlay", [40, 20], [20, 10], "contact shadow, 1x1 footprint"),
    ("shadow.furniture_wide", "shadow_overlay", [72, 36], [36, 18], "contact shadow, 2x1 footprint"),
    ("shadow.pet", "shadow_overlay", [16, 4], [8, 2], "pet foot shadow"),
]

ANIMATIONS = [
    # name, frames, frame_ms, loop, evidence needed to play it (topology@1 intent or ambient only)
    ("idle", 4, 260, "loop", "intent ambient or unobservable (neutral variant)"),
    ("walk", 4, 140, "loop", "any move between points; 4 directions (SE, SW, NE, NW), NE/NW may mirror"),
    ("look_around", 6, 180, "once", "ambient"),
    ("sit_down", 3, 120, "once", "arriving at a seat"),
    ("stand_up", 3, 120, "once", "intent work_done, or leaving a seat"),
    ("work", 4, 220, "loop", "intent work (generic: no typing, thinking or building claim)"),
    ("typing", 4, 120, "loop", "only with a future observed typing signal; not available in topology@1"),
    ("thinking", 6, 200, "loop", "only with a future observed thinking signal; not available in topology@1"),
    ("building", 6, 160, "loop", "only with a future observed build signal; not available in topology@1"),
    ("coffee", 6, 220, "loop", "ambient at coffee or counter point"),
    ("eat", 6, 220, "loop", "ambient at counter point"),
    ("sit_sofa", 2, 600, "loop", "ambient at sofa_sit"),
    ("sleep", 4, 500, "loop", "ambient at sofa_lie (decorative nap, not a session state)"),
    ("stretch", 6, 150, "once", "ambient"),
    ("sweep", 6, 160, "loop", "ambient wander"),
    ("chat", 4, 260, "loop", "two ambient pets at meeting seats (decorative, never claims agents talk)"),
    ("happy", 6, 120, "once", "intent work_done (short, then stand_up)"),
    ("notification", 4, 180, "loop", "intent attention: '!' bubble at the own desk"),
    ("finishing", 4, 220, "loop", "intent finishing: '...' bubble, still seated"),
    ("unknown", 4, 320, "loop", "intent unobservable: dimmed, '?' bubble, seat kept"),
    ("error", 4, 200, "loop", "reserved for an observed error state; topology@1 has none"),
    ("enter", 6, 140, "once", "presentation.new: from the door to the assigned seat or an ambient point"),
    ("exit", 6, 140, "once", "intent exit only (confirmed end): walk to the door, fade at the threshold"),
]


def furniture_entries() -> list[dict]:
    rooms = [L.compose("spec", n) for n in (0, 4, 8)]
    seen: dict[str, dict] = {}
    for room in rooms:
        for m in room.modules:
            if m.asset_id in seen:
                continue
            layer, orientations, notes = FURNITURE[m.asset_id]
            points = [{"id": p.point_id, "kind": p.kind, "offset_tiles": [round(p.x - m.x, 3), round(p.y - m.y, 3)],
                       "facing": p.facing,
                       "pose_offset_tiles": None if p.pose_x is None else
                       [round(p.pose_x - m.x, 3), round(p.pose_y - m.y, 3)]}
                      for p in room.points if _near(p, m)]
            if m.asset_id == "environment.doormat":
                points = [{"id": q.point_id, "kind": q.kind, "offset_tiles": [round(q.x - m.x, 3), round(q.y - m.y, 3)],
                           "facing": q.facing, "pose_offset_tiles": None} for q in room.points if q.zone == "entrance"]
            if m.asset_id == "furniture.desk_modular":
                points = [{"id": "seat_when_facing_S", "kind": "seat", "offset_tiles": [1.0, -0.75], "facing": "S",
                           "pose_offset_tiles": [1.0, -0.75], "seat_z_px": 7},
                          {"id": "seat_when_facing_N", "kind": "seat", "offset_tiles": [1.0, 1.75], "facing": "N",
                           "pose_offset_tiles": [1.0, 1.75], "seat_z_px": 7}]
            if m.asset_id == "furniture.office_chair":
                points = [{"id": "seat", "kind": "seat", "offset_tiles": [0.25, 0.25], "facing": "as placed",
                           "pose_offset_tiles": [0.25, 0.25], "seat_z_px": 7}]
            seen[m.asset_id] = {"asset_id": m.asset_id, "category": m.asset_id.split(".")[0], "layer": layer,
                                "projection": "isometric 2:1", "tile_px": list(TILE),
                                "footprint_tiles": [m.w, m.d], "height_px": m.h,
                                "collision_footprint_tiles": [m.w, m.d] if m.blocks else None,
                                "walkable_over": not m.blocks, "orientations": orientations,
                                "interaction_points": points, "notes": notes, **sprite_box(m.w, m.d, m.h), **LICENSE}
    return sorted(seen.values(), key=lambda e: e["asset_id"])


def _near(p: L.Point, m: L.Module) -> bool:
    px, py = (p.pose_x if p.pose_x is not None else p.x), (p.pose_y if p.pose_y is not None else p.y)
    return m.x - 0.75 <= px <= m.x + m.w + 0.75 and m.y - 0.75 <= py <= m.y + m.d + 0.75 and m.blocks and \
        m.asset_id not in ("furniture.desk_modular",) and (
            (m.zone == p.zone and m.asset_id in ("furniture.pantry_counter", "furniture.sofa",
                                                 "furniture.meeting_table", "device.verification_rack")))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    environment = [{"asset_id": a, "category": a.split(".")[0], "layer": layer, "projection": "isometric 2:1",
                    "tile_px": list(TILE), "native_px": size, "pivot_px": pivot, "notes": notes, **LICENSE}
                   for a, layer, size, pivot, notes in ENVIRONMENT]
    characters = []
    for name, identity in (("claude", "orange angular critter: wide rectangular body, side nubs, four short legs "
                                      "in two pairs, two square black eyes; no tentacles, no round octopus"),
                           ("codex", "blue cloud head, dark terminal screen face with '>_', small light robot body, "
                                     "short arms and legs"),
                           ("unknown_agent", "neutral grey box-shaped pet for provider unknown or other; never "
                                             "recoloured Claude or Codex")):
        characters.append({
            "asset_id": f"character.{name}", "category": "character", "layer": "object", "identity": identity,
            "frame_px": [32, 32], "pivot_px": [16, 31], "depth_anchor_px": [16, 31], "collision_footprint_tiles":
            [0.375, 0.375], "orientations": ["SE", "SW", "NE", "NW"], "mirror_allowed": ["SE<->SW", "NE<->NW"],
            "pixel_density": "same as furniture (1 px = 1 px at integer zoom); outline: darker hue of the form",
            "animations": [{"name": n, "frames": f, "frame_ms": ms, "loop": loop, "plays_when": when}
                           for n, f, ms, loop, when in ANIMATIONS],
            "bubbles": ["attention '!'", "unknown '?'", "finishing '...'", "done '+'", "sleep 'Z'"],
            **LICENSE})
    manifest = {"schema": "orca-studio/asset-manifest@0", "note": "제작 요구사항. 모든 항목은 아직 만들어지지 않음(status spec)",
                "projection": "isometric 2:1", "tile_px": list(TILE), "zoom": "integer only, nearest neighbour",
                "pivot_convention": "footprint origin corner on the floor (x, y, z=0) in sprite pixels",
                "depth_sort": "ground point of the footprint centre; pets: their foot point; seat and chair back "
                              "split so a seated pet sorts between them",
                "environment": environment, "furniture": furniture_entries(), "characters": characters}
    (OUT / "asset-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                                             encoding="utf-8", newline="\n")
    print(f"environment {len(environment)} · furniture {len(manifest['furniture'])} · characters {len(characters)}"
          f" · animations per character {len(ANIMATIONS)}")


if __name__ == "__main__":
    main()
