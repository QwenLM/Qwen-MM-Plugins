"""MCP tool: match_entities — group sightings of an object across frames into unique entities."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from qwen_mm_plugins_video_spatio.tools import _scene
from shared.content import json_text, text_error


class MatchEntitiesArgs(BaseModel):
    scene: Optional[Any] = None
    scene_file: Optional[str] = None
    op: str = "match_across_views"
    label: Optional[str] = None
    match_dist_m: float = 0.6
    model: Optional[str] = None


TOOL: dict[str, Any] = {"name": "match_entities", "args": MatchEntitiesArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Cross-frame identity: group all sightings of an object across frames/views into unique physical
    entities ('match_across_views'), or just count distinct objects ('deduplicate'). Use to avoid
    double-counting the same object seen from multiple frames. Needs a `scene`.

    Args:
        scene: The `scene` object from build_scene (JSON object or JSON string).
        scene_file: Path to a JSON file holding the scene.
        op: 'match_across_views' (group sightings into entities) or 'deduplicate' (count distinct
            physical objects).
        label: Restrict to this label (default: all).
        match_dist_m: BEV distance (meters) under which two sightings are the same object.
        model: Override the VLM model (default: from env).
    """
    try:
        from qwen_mm_plugins_video_spatio.experts.entity_matcher import EntityMatcher
        from qwen_mm_plugins_video_spatio.tools._vlm import VLMShim

        scene = _scene.load_scene(arguments)
        recon = _scene.scene_to_recon(scene, with_images=True)
        em = EntityMatcher()
        em.set_vlm_module(VLMShim(model=arguments.get("model")))
        op = arguments.get("op", "match_across_views")
        label = arguments.get("label")
        md = float(arguments.get("match_dist_m", 0.6))
        if op == "deduplicate":
            result = em.deduplicate(recon, label=label, match_dist_m=md)
        else:
            result = em.match_across_views(
                recon, label=label, images=getattr(recon, "_input_images", None), match_dist_m=md
            )
    except Exception as e:  # noqa: BLE001
        return text_error(f"match_entities failed: {e}")
    return [json_text(result)]
