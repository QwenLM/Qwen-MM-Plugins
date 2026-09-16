"""MCP tool: crop a video frame to a specified pixel region."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from ._media_utils import (
    FRAMES_SUBDIR,
    MediaOpsError,
    _run,
    asset_dir,
    ffmpeg_path,
    format_mmss,
    get_video_metadata,
    slugify,
    validate_video_path,
)


class CropRegion(BaseModel):
    x: int = Field(description="Left edge in pixels.")
    y: int = Field(description="Top edge in pixels.")
    w: int = Field(description="Width in pixels (min 16).")
    h: int = Field(description="Height in pixels (min 16).")


class CropFrameArgs(BaseModel):
    video_path: str = Field()
    timestamp: float = Field()
    region: CropRegion = Field()
    label: str = Field()
    output_dir: str | None = Field(default=None)


TOOL: dict[str, Any] = {"name": "crop_frame", "args": CropFrameArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    r"""Extract one frame cropped to a pixel region, at full source resolution — a spatial zoom to read fine
    visual detail (small text, an icon, intricate structure), or to keep just the relevant area as a
    focused skill asset. The result carries the crop size and the full-frame size, so the region doubles
    as a MEASURED pixel box: annotate the crop directly with `image_annotate` (`coord_space="pixel"`),
    or mark the same spot on the full frame at `[x, y, x + w, y + h]`.

    Args:
        video_path: Path to the source video file.
        timestamp: Timestamp in seconds to extract the frame.
        region: Crop region {x, y, w, h} in pixels.
        label: Short descriptive slug for the crop (e.g. 'node-editor').
        output_dir: Output directory (default: system temp).
    """
    raw_path = arguments["video_path"]
    timestamp = arguments["timestamp"]
    region = arguments["region"]
    label = arguments["label"]
    output_dir = arguments.get("output_dir")

    path = validate_video_path(raw_path)
    meta = get_video_metadata(str(path))

    slug = slugify(label or "")
    if not slug:
        raise MediaOpsError("`label` must be a short descriptive slug (e.g. 'node-editor').")

    if isinstance(region, dict):
        x, y, w, h = int(region["x"]), int(region["y"]), int(region["w"]), int(region["h"])
    else:
        x, y, w, h = region.x, region.y, region.w, region.h

    if w < 16 or h < 16:
        raise MediaOpsError("Crop region must be at least 16x16 pixels.")
    if x < 0 or y < 0 or x + w > meta.width or y + h > meta.height:
        raise MediaOpsError(f"Crop region {x},{y} {w}x{h} exceeds the {meta.width}x{meta.height} frame.")

    ts = min(max(0.0, float(timestamp)), max(0.0, meta.duration_sec - 0.05))
    out_dir = asset_dir(output_dir, FRAMES_SUBDIR)
    out_path = out_dir / f"{format_mmss(ts)}_{slug}.png"

    _run(
        [
            ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{ts:.3f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            f"crop={w}:{h}:{x}:{y}",
            "-y",
            str(out_path),
        ]
    )
    if not out_path.is_file():
        raise MediaOpsError("Cropped frame was not produced.")

    result = {
        "path": str(out_path),
        "seconds": round(ts, 3),
        "region": {"x": x, "y": y, "w": w, "h": h},
        "size": {"w": w, "h": h},
        "frame_size": {"w": meta.width, "h": meta.height},
    }
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
