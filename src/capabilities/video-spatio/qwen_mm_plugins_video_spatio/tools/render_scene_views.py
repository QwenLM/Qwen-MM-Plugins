"""MCP tool: render_scene_views — orthographic 2D renders of the scene's instances (pure compute)."""

from __future__ import annotations

import io
from typing import Any, Optional

from pydantic import BaseModel, Field

from qwen_mm_plugins_video_spatio.tools import _scene
from shared.content import image, text, text_error


class RenderSceneViewsArgs(BaseModel):
    scene: Optional[Any] = Field(
        default=None, description="The `scene` object from build_scene (JSON object or JSON string)."
    )
    scene_file: Optional[str] = Field(
        default=None, description="Path to a JSON file holding the scene (alternative to `scene`)."
    )
    faces: list[str] = Field(
        default=["front", "top"],
        description="Which orthographic faces to render: front / top (=bev) / left / right. Default front+top.",
    )
    frame: Optional[int] = Field(
        default=None, description="Frame whose instances to render (default: aggregate/available)."
    )


TOOL: dict[str, Any] = {
    "name": "render_scene_views",
    "description": (
        "Render the scene's instances as orthographic 2D views (wireframe cuboids) from canonical "
        "faces — DEFAULT front + top(BEV); add left/right only if a task needs more angles. Lets you "
        "'look from another angle' at the reconstructed layout. Needs a `scene` from build_scene."
    ),
    "args": RenderSceneViewsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        scene = _scene.load_scene(arguments)
        recon = _scene.scene_to_recon(scene, with_images=False)
        tool = _scene.new_recon_tool()
        frame = arguments.get("frame")
        views = tool.render_scene_views(
            recon,
            frame=int(frame) if frame is not None else None,
            faces=tuple(arguments.get("faces") or ("front", "top")),
        )
    except Exception as e:  # noqa: BLE001
        return text_error(f"render_scene_views failed: {e}")

    views = views if isinstance(views, (list, tuple)) else [views]
    content: list[dict[str, Any]] = [text(f"Rendered {len(views)} scene view(s).")]
    for vf in views:
        img = getattr(vf, "image", vf)
        try:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            content.append(image(buf.getvalue(), "image/png"))
        except Exception:  # noqa: BLE001
            continue
    return content
