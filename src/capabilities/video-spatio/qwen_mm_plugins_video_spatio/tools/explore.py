"""MCP tools: assess_coverage + plan_exploration — structural active exploration.

Purpose (contrast with `mobile_manip.plan_active_search`):
  `plan_active_search` handles the small case "haven't turned right yet → turn right".
  This module handles the STRUCTURAL case: "is the accumulated scene sufficient to answer the
  question at all, and if not, where to physically go to unlock the missing information?"
  Turns "just spin/advance" into: spin THIS room / traverse THAT gateway / cross to the room
  where the target category typically lives.

Two ops:
  assess_coverage(scene, target?)   — coverage_deg / unobserved_sectors / candidate_gateways /
                                       known_rooms / sufficient_for_target / recommendation
  plan_exploration(scene, target, budget_moves=3) — strategy in {spin_scan | traverse_gateway |
                                       go_forward} + skill_sequence

Pure geometry + label heuristics — no VLM calls. Reads only scene.cameras + scene.instances.
"""

from __future__ import annotations

import json
import math
from typing import Any, Optional

from pydantic import BaseModel

from qwen_mm_plugins_video_spatio.tools import _scene
from shared.content import json_text, text_error

# ── Room / gateway priors ────────────────────────────────────────────────────
# Label heuristics for "we're in room X because we see Y" and "Z is likely found in room W".
# Keep this small and language-neutral (lowercase substring match). Do NOT bloat — the outer
# model can override via a "prior_room_type" hint later; this is a safe minimal fallback.
_ROOM_ANCHORS: dict[str, list[str]] = {
    "kitchen": [
        "fridge",
        "refrigerator",
        "stove",
        "oven",
        "range hood",
        "microwave",
        "sink",
        "dishwasher",
        "kettle",
        "toaster",
        "cutting board",
        "kitchen",
    ],
    "bathroom": [
        "toilet",
        "bathtub",
        "shower",
        "washbasin",
        "vanity",
        "bathroom",
        "toothbrush",
        "bath towel",
        "toilet paper",
        "mirror",
    ],
    "bedroom": ["bed", "pillow", "wardrobe", "headboard", "nightstand", "dresser", "bedside"],
    "livingroom": ["sofa", "couch", "tv", "television", "coffee table", "armchair", "recliner", "living"],
    "office": [
        "desk",
        "monitor",
        "keyboard",
        "office chair",
        "printer",
        "conference",
        "meeting room",
        "whiteboard",
        "projector",
    ],
    "hallway": ["hallway", "corridor", "passage"],
    "diningroom": ["dining table", "dining chair", "buffet"],
    "laundry": ["washer", "dryer", "laundry"],
    "garage": ["car", "garage", "tool box"],
    "outdoor": ["door", "fence", "tree", "car", "outside", "yard", "sidewalk", "grass"],
}

_TARGET_ROOM_PRIOR: dict[str, list[str]] = {
    # object → likely room(s)
    "bed": ["bedroom"],
    "pillow": ["bedroom"],
    "wardrobe": ["bedroom"],
    "nightstand": ["bedroom"],
    "fridge": ["kitchen"],
    "refrigerator": ["kitchen"],
    "stove": ["kitchen"],
    "oven": ["kitchen"],
    "microwave": ["kitchen"],
    "sink": ["kitchen", "bathroom"],
    "kettle": ["kitchen"],
    "toilet": ["bathroom"],
    "bathtub": ["bathroom"],
    "shower": ["bathroom"],
    "sofa": ["livingroom"],
    "tv": ["livingroom"],
    "coffee table": ["livingroom"],
    "armchair": ["livingroom"],
    "conference": ["office"],
    "meeting room": ["office"],
    "whiteboard": ["office"],
    "projector": ["office"],
    "desk": ["office", "bedroom"],
    "monitor": ["office"],
    "keyboard": ["office"],
    "washer": ["laundry"],
    "dryer": ["laundry"],
}

_GATEWAY_LABELS = ("door", "doorway", "corridor", "hallway", "passage", "opening", "entry", "entrance")


# ── Offline suggestion helpers ───────────────────────────────────────────────
# Offline setting has NO environment step — instead, each abstract action maps to concrete MCP
# calls the agent should make to pull more information from the source video (read_video /
# save_view / select_keyframes) and then build_scene on the new frames. See _offline_hints below.


def _skill_to_env_call(skill_str: str) -> dict:
    """Turn 'turn_right()' / 'go_forward(door)' / 'spin_scan()' / 'look_for(bed)' etc. into a
    structured {action, params} stub for a future env_step MCP tool.
    """
    import re as _re

    m = _re.match(r"([a-zA-Z_]+)\s*\((.*)\)\s*$", skill_str or "")
    if not m:
        return {"action": skill_str.strip(), "params": {}}
    name, arg = m.group(1), m.group(2).strip()
    params = {}
    if arg:
        # very lightweight: single-arg 'target=...' or 'X' becomes {'target': X}
        arg = arg.strip("'\"")
        # strip label='...' style if present
        m2 = _re.match(r"[a-zA-Z_]+\s*=\s*(.+)$", arg)
        if m2:
            arg = m2.group(1).strip("'\"")
        if arg:
            params["target"] = arg
    return {"action": name, "params": params}


def _env_calls(skill_sequence: list[str]) -> list[dict]:
    return [_skill_to_env_call(s) for s in (skill_sequence or [])]


# ── Offline mapping ──────────────────────────────────────────────────────────
# In an OFFLINE test (no env_step), an agent cannot actually turn/walk; but it CAN pull more
# frames from the source video. So we translate each abstract action into concrete MCP calls
# the offline agent should make instead. These are hints (not enforced) — a fallback for the
# offline setting when the env-interface stub is not yet wired.
_OFFLINE_ACTION_MAP = {
    # abstract action  →  suggested offline MCP call(s)
    "spin_scan": [
        "select_keyframes(scene, strategy='motion', n=8)",
        "build_scene(objects_by_frame=..., frames=[new frame paths])",
    ],
    "turn_left": [
        "read_video(video_path, start_time=..., end_time=...)  # earlier/other segment where camera panned left"
    ],
    "turn_right": ["read_video(video_path, start_time=..., end_time=...)  # segment where camera panned right"],
    "turn_back": ["read_video(video_path, start_time=..., end_time=...)  # segment shot the opposite way"],
    "go_forward": [
        "read_video(video_path, start_time=<later time>, ...)   # segment where camera moved deeper into the scene",
        "save_view(video_path, times=[<candidate seconds>])       # or grab specific frames along the traversal",
    ],
    "look_for": ["build_scene(...)  # after adding the new frames, ground the target and re-check"],
    "observation": ["read_video / save_view to inspect one more frame"],
    "plan_navigation": ["mobile_manip(scene, op='plan_navigation', target=<label>)   # target already in scene"],
}


def _offline_hints(skill_sequence: list[str]) -> list[dict]:
    """Map each abstract action to concrete offline-friendly MCP calls.
    Returned as list of {action, offline_calls: [str,...]} paralleling skill_sequence.
    """
    out = []
    for s in skill_sequence or []:
        call = _skill_to_env_call(s)
        name = call.get("action", "")
        out.append(
            {
                "action": name,
                "offline_calls": _OFFLINE_ACTION_MAP.get(
                    name, ["read_video / save_view / select_keyframes  # gather more frames, then build_scene"]
                ),
            }
        )
    return out


# ── helpers ──────────────────────────────────────────────────────────────────


def _inst_world_xz(inst: dict, cam: dict) -> tuple[float, float]:
    """Map an instance's (bbox_1000 centre bearing + depth_m) into world (x,z) meters."""
    pos = cam.get("pos_bev") or [0.0, 0.0]
    yaw = math.radians(float(cam.get("yaw_deg") or 0.0))
    fov = float(cam.get("fov_deg") or 60.0)
    depth = float(inst.get("depth_m") or 0.0)
    pt = inst.get("point_1000") or [500.0, 500.0]
    bearing = math.radians((pt[0] / 1000.0 - 0.5) * fov)  # + = right of image centre
    r = depth * math.sin(bearing)
    f = depth * math.cos(bearing)
    x = float(pos[0]) + math.sin(yaw) * f + math.cos(yaw) * r
    z = float(pos[1]) - math.cos(yaw) * f + math.sin(yaw) * r
    return x, z


def _wrap180(a: float) -> float:
    return ((a + 540.0) % 360.0) - 180.0


def _cam_fov_sectors(scene: dict) -> list[tuple[float, float]]:
    """Return list of world-heading intervals (deg, wrapped to [-180,180]) that each camera has covered.
    Each element = (a_min, a_max) with a_min < a_max, possibly split into two if crossing ±180.
    """
    sectors: list[tuple[float, float]] = []
    for c in (scene.get("cameras") or {}).values():
        yaw = float(c.get("yaw_deg") or 0.0)
        fov = float(c.get("fov_deg") or 60.0)
        a0 = _wrap180(yaw - fov / 2.0)
        a1 = _wrap180(yaw + fov / 2.0)
        if a0 <= a1:
            sectors.append((a0, a1))
        else:
            sectors.append((a0, 180.0))
            sectors.append((-180.0, a1))
    return sectors


def _union_length(sectors: list[tuple[float, float]]) -> float:
    """Total covered angular extent (deg), sectors on [-180,180]."""
    if not sectors:
        return 0.0
    xs = sorted(sectors)
    merged = [list(xs[0])]
    for a, b in xs[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return sum(b - a for a, b in merged)


def _unobserved_sectors(scene: dict) -> list[list[float]]:
    """Complement of covered sectors on [-180, 180], returned as list of [a_min, a_max]."""
    sectors = sorted(_cam_fov_sectors(scene))
    if not sectors:
        return [[-180.0, 180.0]]
    merged = [list(sectors[0])]
    for a, b in sectors[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    gaps: list[list[float]] = []
    prev = -180.0
    for a, b in merged:
        if a > prev:
            gaps.append([prev, a])
        prev = b
    if prev < 180.0:
        gaps.append([prev, 180.0])
    return gaps


def _in_sector(angle_deg: float, sector: list[float]) -> bool:
    return sector[0] <= angle_deg <= sector[1]


def _room_from_labels(labels: list[str]) -> list[str]:
    """Cluster a set of seen labels into room-type hypotheses."""
    L = [lab.lower() for lab in labels]
    votes: dict[str, int] = {}
    for room, anchors in _ROOM_ANCHORS.items():
        v = sum(1 for a in anchors for lab in L if a in lab)
        if v > 0:
            votes[room] = v
    return [k for k, _ in sorted(votes.items(), key=lambda kv: -kv[1])]


def _target_prior_rooms(target: str) -> list[str]:
    if not target:
        return []
    t = target.lower().strip()
    for key, rooms in _TARGET_ROOM_PRIOR.items():
        if key in t or t in key:
            return list(rooms)
    return []


def _all_seen_labels(scene: dict) -> list[str]:
    seen: list[str] = []
    for insts in (scene.get("instances") or {}).values():
        for it in insts or []:
            lbl = it.get("label")
            if lbl:
                seen.append(str(lbl))
    return seen


def _find_target_instances(scene: dict, target: str) -> list[dict]:
    """Return all instances whose label matches target (across all frames)."""
    t = (target or "").lower().strip()
    hits = []
    for insts in (scene.get("instances") or {}).values():
        for it in insts or []:
            lbl = str(it.get("label", "")).lower()
            if not lbl:
                continue
            if t == lbl or t in lbl or lbl in t:
                hits.append(it)
    return hits


def _gateways_and_headings(scene: dict) -> list[dict]:
    """From scene.instances, find gateway-like items and compute the heading (deg) from the
    reference (first) camera pointing toward each gateway's world position."""
    cams = scene.get("cameras") or {}
    if not cams:
        return []
    # reference camera = first frame index
    fis = scene.get("frame_indices") or sorted(int(k) for k in cams)
    ref_fi = fis[0] if fis else 0
    ref = cams.get(str(ref_fi)) or list(cams.values())[0]
    rx, rz = ref.get("pos_bev") or [0.0, 0.0]

    out = []
    for fi, insts in (scene.get("instances") or {}).items():
        cam = cams.get(str(fi)) or ref
        for it in insts or []:
            lbl = str(it.get("label", "")).lower()
            if not any(g in lbl for g in _GATEWAY_LABELS):
                continue
            wx, wz = _inst_world_xz(it, cam)
            dx, dz = wx - float(rx), wz - float(rz)
            heading_deg = math.degrees(math.atan2(dx, -dz))
            out.append(
                {
                    "label": it.get("label"),
                    "frame": int(fi) if str(fi).lstrip("-").isdigit() else fi,
                    "world_xz": [round(wx, 3), round(wz, 3)],
                    "heading_from_ref_deg": round(heading_deg, 1),
                    "depth_m": float(it.get("depth_m") or 0.0),
                }
            )
    # dedupe by nearby world_xz (< 0.3 m)
    dedup = []
    for g in out:
        keep = True
        for k in dedup:
            if math.hypot(g["world_xz"][0] - k["world_xz"][0], g["world_xz"][1] - k["world_xz"][1]) < 0.3:
                keep = False
                break
        if keep:
            dedup.append(g)
    return dedup


# ── op 1: assess_coverage ─────────────────────────────────────────────────────


class AssessCoverageArgs(BaseModel):
    scene: Optional[Any] = None
    scene_file: Optional[str] = None
    target: Optional[str] = None


TOOL_ASSESS: dict[str, Any] = {"name": "assess_coverage", "args": AssessCoverageArgs}


def handle_assess(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Report how much of the world has been observed and whether that is sufficient to answer the current
    question. Pure geometry + label heuristics — no VLM. Returns: coverage_deg (total angular sweep),
    unobserved_sectors (world-heading gaps), candidate_gateways (door/corridor/opening items with
    headings from the reference camera), known_rooms (guessed from seen labels), target_hint (likely
    rooms for `target` from a small prior), and sufficient_for_target (true if target is already in
    scene, else false).

    Args:
        scene: `scene` from build_scene.
        scene_file: Path to scene JSON file.
        target: Optional: object label we want to find. If given, the tool also reports whether the
            accumulated scene is enough (target present) or hints where it likely is (via room prior).
    """
    try:
        scene = _scene.load_scene(arguments)
    except Exception as e:  # noqa: BLE001
        return text_error(f"assess_coverage: {e}")

    sectors = _cam_fov_sectors(scene)
    coverage_deg = round(_union_length(sectors), 1)
    unobs = _unobserved_sectors(scene)
    unobs = [[round(a, 1), round(b, 1)] for a, b in unobs if (b - a) > 5.0]  # ignore tiny slivers

    labels = _all_seen_labels(scene)
    known_rooms = _room_from_labels(labels)
    gateways = _gateways_and_headings(scene)
    # A gateway is "leads_to_unobserved" if EITHER
    #  (a) its heading falls in an un-observed sector (we haven't looked through it yet), OR
    #  (b) it's a physical passage (door/corridor/opening): by definition its FAR side is unobserved.
    #      (Even if we're looking AT the door, we haven't been *past* it — traversing changes the vantage.)
    # A gateway leads to unobserved space by default (its far side hasn't been reached).
    # Also record whether the gateway itself sits in an un-scanned bearing gap (stronger signal).
    for g in gateways:
        h = g["heading_from_ref_deg"]
        g["heading_in_unobserved_gap"] = any(_in_sector(h, s) for s in unobs)
        g["leads_to_unobserved"] = True  # physical passage → far side unseen unless we walked through

    target = (arguments.get("target") or "").strip() or None
    target_hint = None
    sufficient = False
    hits = []
    if target:
        hits = _find_target_instances(scene, target)
        if hits:
            sufficient = True
            target_hint = f"already in scene (×{len(hits)})"
        else:
            prior = _target_prior_rooms(target)
            in_room = [r for r in prior if r in known_rooms]
            missing_rooms = [r for r in prior if r not in known_rooms]
            if in_room:
                target_hint = f"target='{target}' typically in {prior}; those rooms appear scanned ({in_room}) but target not grounded — spin/re-scan or scan farther."
            elif missing_rooms:
                target_hint = f"target='{target}' typically in {missing_rooms}; those rooms NOT seen yet — cross a gateway toward them."
            elif prior:
                target_hint = f"target='{target}' typically in {prior}; none of those rooms observed."
            else:
                target_hint = f"no strong room prior for target='{target}' — explore broadly."

    # recommendation: one-line English hint
    if sufficient:
        rec = f"scene ALREADY covers target='{target}'; use plan_navigation."
    else:
        parts = []
        if coverage_deg < 180:
            parts.append(f"only {coverage_deg:.0f}° of 360° scanned → spin_scan first")
        elif unobs:
            gw = [g for g in gateways if g.get("leads_to_unobserved")]
            if gw:
                parts.append(
                    f"un-observed sectors {unobs} — traverse gateway {gw[0]['label']} @ heading {gw[0]['heading_from_ref_deg']}°"
                )
            else:
                parts.append(f"un-observed sectors {unobs}, no gateway → turn toward them or go_forward")
        else:
            parts.append("full 360° scanned in this room — cross a gateway if target not here")
        if target_hint:
            parts.append(target_hint)
        rec = " | ".join(parts) if parts else "explore broadly"

    result = {
        "coverage_deg": coverage_deg,
        "unobserved_sectors": unobs,
        "candidate_gateways": gateways,
        "known_rooms": known_rooms,
        "target": target,
        "target_hint": target_hint,
        "target_hits": len(hits),
        "sufficient_for_target": sufficient,
        "recommendation": rec,
    }
    return [json_text(result)]


# ── op 2: plan_exploration ────────────────────────────────────────────────────


class PlanExplorationArgs(BaseModel):
    scene: Optional[Any] = None
    scene_file: Optional[str] = None
    target: str
    budget_moves: int = 3
    spin_coverage_deg: float = 180.0


TOOL_PLAN: dict[str, Any] = {"name": "plan_exploration", "args": PlanExplorationArgs}


def handle_plan(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Given a target and the current scene, decide the next STRUCTURAL exploration move: (a) `spin_scan` —
    turn ~360° in place to raise this room's coverage; (b) `traverse_gateway` — head to a door/corridor
    pointing into an unobserved sector, then re-observe; (c) `go_forward` — no gateway, no target in
    view; advance to a new vantage; (d) `found` — target already in scene, hand off to plan_navigation.
    Pure geometry, no VLM. Complements `plan_active_search` (small local left/right) with structural
    reasoning across rooms.

    Args:
        scene: `scene` from build_scene.
        scene_file: Path to scene JSON file.
        target: Object label we want to find (e.g. 'bed', 'conference room 302').
        budget_moves: Max steps in the returned skill_sequence.
        spin_coverage_deg: If current coverage_deg < this, prefer spin_scan first.
    """
    try:
        scene = _scene.load_scene(arguments)
    except Exception as e:  # noqa: BLE001
        return text_error(f"plan_exploration: {e}")

    target = str(arguments["target"]).strip()
    budget = max(1, int(arguments.get("budget_moves") or 3))
    spin_thr = float(arguments.get("spin_coverage_deg") or 180.0)

    coverage_deg = _union_length(_cam_fov_sectors(scene))
    unobs = _unobserved_sectors(scene)
    unobs = [[a, b] for a, b in unobs if (b - a) > 5.0]
    gateways = _gateways_and_headings(scene)
    for g in gateways:
        g["heading_in_unobserved_gap"] = any(
            g["heading_from_ref_deg"] >= s[0] and g["heading_from_ref_deg"] <= s[1] for s in unobs
        )
        g["leads_to_unobserved"] = True
    known_rooms = _room_from_labels(_all_seen_labels(scene))
    prior_rooms = _target_prior_rooms(target)
    in_prior = [r for r in prior_rooms if r in known_rooms]
    gw_pref = [g for g in gateways if g.get("leads_to_unobserved")]

    # ── Offline assessment: this is the CORE offline product — a judgment about whether the current
    # (frame-based) memory is enough to answer, plus what to pull from the video if not, and a
    # calibrated fallback answer to use if forced to commit without more data.
    def _offline_assessment(strategy: str, hits: list, extra: dict | None = None) -> dict:
        base = {
            "target": target,
            "target_seen": bool(hits),
            "target_hits": len(hits),
            "coverage_deg": round(coverage_deg, 1),
            "known_rooms": known_rooms,
            "prior_rooms_for_target": prior_rooms,
            "prior_room_seen": in_prior,
            "unobserved_sectors": unobs,
            "gateways": gw_pref,
        }
        if strategy == "found":
            base["evidence_sufficiency"] = "found"
            base["recommended_action"] = "answer_now"
            base["specific_data_calls"] = []
            base["fallback_answer_hint"] = (
                f"target='{target}' is grounded — read its position/attributes off the scene."
            )
        elif strategy == "traverse_gateway":
            base["evidence_sufficiency"] = "likely_in_unseen_area"
            base["recommended_action"] = "sample_more_frames_from_video"
            gw = (extra or {}).get("gateway", {})
            base["specific_data_calls"] = [
                "read_video(video_path, start_time=<later segment>, end_time=<later>)  # segment shot beyond the gateway",
                "save_view(video_path, times=[<candidate seconds>])                     # or grab frames past the doorway",
                "select_keyframes(scene, strategy='motion', n=16)                       # widen frame set toward large-yaw shots",
                "build_scene(objects_by_frame=..., frames=[new frame paths], camera_motions=[...])  # extend the scene",
            ]
            base["fallback_answer_hint"] = (
                f"target='{target}' not in the current scene, but its typical rooms {prior_rooms} were NOT observed either. "
                f"If forced, guess: '{target}' probably in {prior_rooms or 'an un-observed room'} (via gateway '{gw.get('label', 'door')}')."
            )
        elif strategy == "spin_scan":
            base["evidence_sufficiency"] = "likely_present_but_uncovered_in_current_frames"
            base["recommended_action"] = "sample_more_frames_from_video"
            base["specific_data_calls"] = [
                "select_keyframes(scene, strategy='motion', n=16)                       # add frames with wider yaw range",
                "select_keyframes(scene, strategy='coverage', label='<target>', n=8)     # bias toward frames likely to contain target",
                "save_view(video_path, times=[<seconds spanning missed angles>])         # sample frames from un-scanned yaw windows",
                "build_scene(..., include the new frames)                                # rebuild and re-check",
            ]
            base["fallback_answer_hint"] = (
                f"'{target}' likely lives in {prior_rooms or 'the current room'} which IS partly scanned; "
                f"missing coverage {[round(a, 0) for a, b in unobs for _ in [None]] or 'small gaps'}."
                if prior_rooms and in_prior
                else f"only {coverage_deg:.0f}° of 360° scanned so far; '{target}' may lie in the uncovered sectors."
            )
        elif strategy == "go_forward":
            base["evidence_sufficiency"] = "insufficient"
            base["recommended_action"] = "sample_more_frames_from_video_or_declare_insufficient"
            base["specific_data_calls"] = [
                "read_video(video_path, start_time=<other segment>, end_time=<other>)  # try a completely different segment",
                "select_keyframes(strategy='motion', n=32)                              # widen frame diversity",
                "save_view(video_path, times=[<other candidate seconds>])",
                "build_scene(...)  # rebuild; if still not found → declare 'insufficient data'",
            ]
            base["fallback_answer_hint"] = (
                f"the video's current scene shows no '{target}', no gateway, and no strong prior — "
                f"the honest answer is 'cannot be determined from the given frames'. Only commit to a guess if the task demands one."
            )
        return base

    # ── strategy selection (same as before) ──
    hits = _find_target_instances(scene, target)
    if hits:
        seq = [f"plan_navigation(target='{target}')"]
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "strategy": "found",
                        "reason": f"target='{target}' already grounded in scene (×{len(hits)}). Hand off to plan_navigation.",
                        "skill_sequence": seq,
                        "offline_hints": _offline_hints(seq),
                        "offline_assessment": _offline_assessment("found", hits),
                    },
                    ensure_ascii=False,
                ),
            }
        ]

    # A) low coverage → spin first
    if coverage_deg < spin_thr and not gateways:
        seq = ["spin_scan()", f"look_for({target})"][:budget]
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "strategy": "spin_scan",
                        "reason": f"only {coverage_deg:.0f}° of 360° scanned in the current room; no gateway visible — spin in place first.",
                        "coverage_before_deg": round(coverage_deg, 1),
                        "skill_sequence": seq,
                        "offline_hints": _offline_hints(seq),
                        "offline_assessment": _offline_assessment("spin_scan", []),
                    },
                    ensure_ascii=False,
                ),
            }
        ]

    # B) prior says target lives in an UN-seen room, and we have gateway → traverse
    if prior_rooms and not in_prior and gw_pref:
        g = gw_pref[0]
        h = g["heading_from_ref_deg"]
        turn = "turn_right()" if h > 45 else ("turn_left()" if h < -45 else ("turn_back()" if abs(h) > 135 else None))
        seq = []
        if turn:
            seq.append(turn)
        seq += [f"go_forward({g['label']})", "spin_scan()", f"look_for({target})"]
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "strategy": "traverse_gateway",
                        "reason": f"target='{target}' likely in {prior_rooms} — none seen; gateway '{g['label']}' points into unobserved area (heading {h:.0f}°).",
                        "gateway_used": g,
                        "prior_rooms": prior_rooms,
                        "known_rooms": known_rooms,
                        "skill_sequence": seq[:budget],
                        "offline_hints": _offline_hints(seq[:budget]),
                        "offline_assessment": _offline_assessment("traverse_gateway", [], {"gateway": g}),
                    },
                    ensure_ascii=False,
                ),
            }
        ]

    # C) prior says target lives in an already-seen room but not grounded → spin again
    if prior_rooms and in_prior:
        seq = ["spin_scan()", f"look_for({target})"][:budget]
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "strategy": "spin_scan",
                        "reason": f"target='{target}' typically in {prior_rooms}; those rooms ({in_prior}) appear scanned but target not grounded — re-scan more carefully.",
                        "coverage_before_deg": round(coverage_deg, 1),
                        "prior_rooms": prior_rooms,
                        "known_rooms": known_rooms,
                        "skill_sequence": seq,
                        "offline_hints": _offline_hints(seq),
                        "offline_assessment": _offline_assessment("spin_scan", []),
                    },
                    ensure_ascii=False,
                ),
            }
        ]

    # D) any gateway to unobserved → try it
    if gw_pref:
        g = gw_pref[0]
        h = g["heading_from_ref_deg"]
        turn = "turn_right()" if h > 45 else ("turn_left()" if h < -45 else ("turn_back()" if abs(h) > 135 else None))
        seq = []
        if turn:
            seq.append(turn)
        seq += [f"go_forward({g['label']})", "spin_scan()", f"look_for({target})"]
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "strategy": "traverse_gateway",
                        "reason": f"no prior room hit; but gateway '{g['label']}' points into unobserved sector.",
                        "gateway_used": g,
                        "skill_sequence": seq[:budget],
                        "offline_hints": _offline_hints(seq[:budget]),
                        "offline_assessment": _offline_assessment("traverse_gateway", [], {"gateway": g}),
                    },
                    ensure_ascii=False,
                ),
            }
        ]

    # E) fallback: go forward to a new vantage
    seq = ["go_forward()", "spin_scan()", f"look_for({target})"][:budget]
    return [
        {
            "type": "text",
            "text": json.dumps(
                {
                    "strategy": "go_forward",
                    "reason": "no target in scene, no gateway visible, coverage cannot be improved by spinning in place — advance for a new vantage.",
                    "skill_sequence": seq,
                    "offline_hints": _offline_hints(seq),
                    "offline_assessment": _offline_assessment("go_forward", []),
                },
                ensure_ascii=False,
            ),
        }
    ]


# ── op 3: assess_reachable ─────────────────────────────────────────────────────
# The manipulation-prep case, OFFLINE. "Can the agent move to a position from which it can
# OPERATE the target?" — with no env step, so we only JUDGE + SUGGEST (never actually move).
# Deliberately narrow (per scope): we do NOT verify the manipulation itself, only whether an
# operable vantage is reachable. Pure geometry; complements mobile_manip's recon+VLM plan_movement.


class AssessReachableArgs(BaseModel):
    scene: Optional[Any] = None
    scene_file: Optional[str] = None
    target: str
    max_reach_m: float = 1.0


TOOL_REACH: dict[str, Any] = {"name": "assess_reachable", "args": AssessReachableArgs}


def _target_hits_world(scene: dict, target: str) -> list[dict]:
    """Grounded target instances with world (x,z), depth, and camera-relative bearing per frame."""
    t = (target or "").lower().strip()
    cams = scene.get("cameras") or {}
    out: list[dict] = []
    for fi, insts in (scene.get("instances") or {}).items():
        cam = cams.get(str(fi)) or cams.get(fi) or {}
        for it in insts or []:
            lbl = str(it.get("label", "")).lower()
            if not lbl:
                continue
            if not (t == lbl or t in lbl or lbl in t):
                continue
            wx, wz = _inst_world_xz(it, cam)
            pt = it.get("point_1000") or [500.0, 500.0]
            fov = float(cam.get("fov_deg") or 60.0)
            bearing = (pt[0] / 1000.0 - 0.5) * fov
            out.append(
                {
                    "frame": int(fi) if str(fi).lstrip("-").isdigit() else fi,
                    "world_xz": [round(wx, 3), round(wz, 3)],
                    "depth_m": float(it.get("depth_m") or 0.0),
                    "bearing_deg": round(bearing, 1),
                }
            )
    return out


def handle_reach(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """OFFLINE manipulation-prep judgment: given a target and the accumulated scene (past trajectory /
    memory), decide whether the agent can reach a position from which it can OPERATE the target —
    WITHOUT any env step. Pure geometry + label heuristics, no VLM. Three outcomes: `reachable_now` (an
    observed viewpoint is already within max_reach_m), `reachable_after_move` (target grounded but too
    far → single-leg turn+go_forward plan + distance to close), or `target_not_grounded` (never observed
    → defer to assess_coverage / plan_exploration to FIND it first). Scope: judges only whether an
    operable vantage is reachable, NOT whether the manipulation succeeds. Returns offline_assessment
    (evidence_sufficiency / recommended_action / specific_data_calls / fallback_answer_hint) in the same
    shape as plan_exploration.

    Args:
        scene: `scene` from build_scene.
        scene_file: Path to scene JSON file.
        target: Object to reach / operate on (e.g. 'cup', 'door handle', 'drawer').
        max_reach_m: Operable reach in meters: target counts as 'operable' if some observed viewpoint is
            within this distance.
    """
    try:
        scene = _scene.load_scene(arguments)
    except Exception as e:  # noqa: BLE001
        return text_error(f"assess_reachable: {e}")

    target = str(arguments["target"]).strip()
    max_reach = float(arguments.get("max_reach_m") or 1.0)

    hits = _target_hits_world(scene, target)
    known_rooms = _room_from_labels(_all_seen_labels(scene))
    prior_rooms = _target_prior_rooms(target)

    def _assessment(evidence: str, action: str, calls: list[str], fallback: str) -> dict:
        return {
            "target": target,
            "target_grounded": bool(hits),
            "target_hits": len(hits),
            "max_reach_m": max_reach,
            "known_rooms": known_rooms,
            "prior_rooms_for_target": prior_rooms,
            "evidence_sufficiency": evidence,
            "recommended_action": action,
            "specific_data_calls": calls,
            "fallback_answer_hint": fallback,
        }

    # Case A — never grounded → can't even localize it → hand back to the exploration ops (op1/op2).
    if not hits:
        result = {
            "target": target,
            "status": "target_not_grounded",
            "reachable": False,
            "reason": f"target='{target}' not grounded in any frame — locate it first via assess_coverage / plan_exploration.",
            "skill_sequence": [f"plan_exploration(target='{target}')"],
            "offline_assessment": _assessment(
                "target_not_grounded",
                "explore_first_then_reassess",
                [
                    f"assess_coverage(scene, target='{target}')   # is the target's likely room even observed?",
                    f"plan_exploration(scene, target='{target}')  # spin / traverse-gateway / go-forward plan to find it",
                    "build_scene(...) on the new frames, then call assess_reachable again",
                ],
                (
                    f"'{target}' has never been observed (likely in {prior_rooms or 'an un-observed room'}); "
                    f"reachability is undecidable until it is grounded — explore first."
                ),
            ),
        }
        return [json_text(result)]

    # closest observation = best proxy for "a viewpoint from which we could operate"
    best = min(hits, key=lambda h: h["depth_m"] if h["depth_m"] > 0 else 1e9)
    min_depth = best["depth_m"]

    # Case B — an observed viewpoint is already within operable reach.
    if min_depth <= max_reach:
        result = {
            "target": target,
            "status": "reachable_now",
            "reachable": True,
            "min_observed_depth_m": round(min_depth, 3),
            "operable_from_frame": best["frame"],
            "skill_sequence": [],
            "reason": f"target seen at {min_depth:.2f} m ≤ max_reach {max_reach:.2f} m — an operable vantage already exists.",
            "offline_assessment": _assessment(
                "reachable_now",
                "operate_now",
                [],
                f"target='{target}' observed at {min_depth:.2f} m ≤ reach {max_reach:.2f} m (frame {best['frame']}) — already operable from that vantage.",
            ),
        }
        return [json_text(result)]

    # Case C — grounded but too far → one turn + go_forward leg to close the gap.
    b = best["bearing_deg"]
    turn = "turn_right()" if b > 20 else ("turn_left()" if b < -20 else None)
    seq: list[str] = []
    if turn:
        seq.append(turn)
    seq.append(f"go_forward({target})")
    dist_to_close = round(min_depth - max_reach, 2)
    result = {
        "target": target,
        "status": "reachable_after_move",
        "reachable": True,
        "min_observed_depth_m": round(min_depth, 3),
        "distance_to_close_m": dist_to_close,
        "seen_from_frame": best["frame"],
        "target_bearing_deg": b,
        "skill_sequence": seq,
        "reason": f"target at {min_depth:.2f} m > reach {max_reach:.2f} m — one turn+forward leg reaches an operable pose.",
        "offline_assessment": _assessment(
            "reachable_after_move",
            "navigate_then_operate",
            [
                f"mobile_manip(scene, op='plan_movement', target='{target}', max_reach_m={max_reach})  # full turn+forward plan (recon path)",
                "read_video / save_view near the closest observation to confirm a clear approach",
            ],
            (
                f"'{target}' grounded at ~{min_depth:.2f} m (> reach {max_reach:.2f} m); move ~{dist_to_close:.2f} m "
                f"toward it ({turn or 'straight ahead'}) to reach an operable position."
            ),
        ),
    }
    return [json_text(result)]


# ── framework wiring: expose two tools out of this one file ──────────────────
# The build_registry picks up TOOL/handle pairs. To register TWO tools from one module,
# we use a dispatch shim: the module exports TOOL/handle for the first, and the framework
# also finds a second registration when it scans for TOOL_<suffix>. mcp_framework here
# only accepts a single TOOL per module, so we ship TWO files to keep it clean.
TOOL = TOOL_ASSESS
handle = handle_assess
