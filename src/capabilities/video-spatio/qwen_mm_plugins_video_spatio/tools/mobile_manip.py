"""MCP tool: mobile_manip — embodied / mobile-manipulation reasoning + action planning."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from qwen_mm_plugins_video_spatio.tools import _scene
from shared.content import json_text, text_error

# All supported ops → dispatched below.
_OPS = {
    # Observation / grounding ops (need a target)
    "track_object_trajectory",  # (recon, target)                       — cross-frame BEV path of a target
    "check_object_in_view",  # (recon, target, frame?)               — is target visible in the frame?
    "suggest_approach",  # (recon, target, frame?)               — pre-contact staging (score 4 sides)
    "search_object_across_frames",  # (recon, target)                       — where does target appear across frames?
    "reachability",  # (recon, target?, frame?, max_reach_m?)— within reach?
    # NEW: action-planning ops (turn/forward sequences — no VLM, pure geometry)
    "plan_navigation",  # (recon, target, frame?)               — single-leg turn + go_forward to target
    "plan_movement",  # (recon, target, frame?, max_reach_m?) — nav only if needed (reachability gate)
    "plan_active_search",  # (recon, frames_ignored, target, max_moves?) — target NOT in scene → next scan move
}


class MobileManipArgs(BaseModel):
    scene: Optional[Any] = Field(default=None, description="The `scene` object from build_scene.")
    scene_file: Optional[str] = Field(default=None, description="Path to a JSON file holding the scene.")
    op: str = Field(
        default="plan_navigation",
        description=(
            "Which routine to run. Observation/grounding: "
            "`track_object_trajectory` | `check_object_in_view` | `suggest_approach` | "
            "`search_object_across_frames` | `reachability`. "
            "Action planning: `plan_navigation` (single-leg turn+forward), "
            "`plan_movement` (nav only if not reachable), "
            "`plan_active_search` (target NOT in scene → suggest next scan move)."
        ),
    )
    target: str = Field(description="The object to reason about (label or instance id). Required for all ops.")
    frame: Optional[int] = Field(default=None, description="Frame index for ops that accept one (default: first).")
    max_reach_m: Optional[float] = Field(
        default=None, description="Reachability threshold in meters (reachability / plan_movement)."
    )
    max_moves: Optional[int] = Field(default=None, description="Max steps to output (plan_active_search, default 3).")
    model: Optional[str] = Field(
        default=None, description="Override VLM model (default: from env). Only affects VLM-backed ops."
    )


TOOL: dict[str, Any] = {
    "name": "mobile_manip",
    "description": (
        "Embodied / mobile-manipulation over the scene. Two families of ops:\n"
        "  Observation: `track_object_trajectory` (cross-frame BEV path), `check_object_in_view`, "
        "`suggest_approach` (staging), `search_object_across_frames`, `reachability`.\n"
        "  Action-planning (pure geometry, no VLM): `plan_navigation` gives a single-leg turn+forward "
        "sequence to a visible target; `plan_movement` gates that behind a reachability check; "
        "`plan_active_search` handles the target-not-visible case — from the camera trajectory, decide "
        "the next locomotion move (turn to an unobserved side, or advance). Needs a `scene` from build_scene."
    ),
    "args": MobileManipArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    op = arguments.get("op", "plan_navigation")
    if op not in _OPS:
        return text_error(f"unsupported op '{op}'. Supported: {sorted(_OPS)}")

    try:
        from qwen_mm_plugins_video_spatio.experts.mobile_expert import MobileManipulationExpert
        from qwen_mm_plugins_video_spatio.tools._vlm import VLMShim

        scene = _scene.load_scene(arguments)
        recon = _scene.scene_to_recon(scene, with_images=True)
        me = MobileManipulationExpert()
        me.set_vlm_module(VLMShim(model=arguments.get("model")))

        target = arguments["target"]
        frame = arguments.get("frame")
        max_reach = arguments.get("max_reach_m")
        max_moves = arguments.get("max_moves")

        # Per-op kwarg mapping (mirrors the concrete signatures in mobile_expert.py)
        if op == "plan_navigation":
            kwargs = {"target": target}
            if frame is not None:
                kwargs["frame"] = int(frame)
            result = me.plan_navigation(recon, **kwargs)
        elif op == "plan_movement":
            kwargs = {"target": target}
            if frame is not None:
                kwargs["frame"] = int(frame)
            if max_reach is not None:
                kwargs["max_reach_m"] = float(max_reach)
            result = me.plan_movement(recon, **kwargs)
        elif op == "plan_active_search":
            # signature: plan_active_search(recon, frames, target, max_moves=3)
            frames_arg = getattr(recon, "_input_images", None) or []
            kwargs = {"target": target}
            if max_moves is not None:
                kwargs["max_moves"] = int(max_moves)
            result = me.plan_active_search(recon, frames_arg, **kwargs)
        elif op == "reachability":
            kwargs = {}
            # reachability(recon, frame=None, objects=None, max_reach_m=DEFAULT)
            # We treat `target` as the single object of interest (list-of-1)
            kwargs["objects"] = [target]
            if frame is not None:
                kwargs["frame"] = int(frame)
            if max_reach is not None:
                kwargs["max_reach_m"] = float(max_reach)
            try:
                result = me.reachability(recon, **kwargs)
            except TypeError:
                # older signatures may want (recon, target=...) — fall back
                result = me.reachability(recon, target=target)
        else:
            # Original target-centric ops kept unchanged
            fn = getattr(me, op)
            try:
                result = fn(recon, target, frame=int(frame)) if frame is not None else fn(recon, target)
            except TypeError:
                result = fn(recon, target)
    except Exception as e:  # noqa: BLE001
        return text_error(f"mobile_manip failed: {e}")
    return [json_text(result)]
