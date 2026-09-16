"""MCP tool: draw highlight annotations (box / arrow / number / circle / text) on an image."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from shared.image import open_image

from . import _draw_utils
from ._media_utils import MediaOpsError, asset_dir, slugify

ANNOT_SUBDIR = "annotated"
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


class Annotation(BaseModel):
    type: str = Field(description="One of: box | arrow | number | circle | text.")
    bbox: Optional[list[int]] = Field(
        default=None,
        description="[x1,y1,x2,y2] in `coord_space`, for type=box or circle.",
    )
    start: Optional[list[int]] = Field(default=None, description="[x,y] in `coord_space`, arrow tail (type=arrow).")
    end: Optional[list[int]] = Field(default=None, description="[x,y] in `coord_space`, arrow head (type=arrow).")
    at: Optional[list[int]] = Field(default=None, description="[x,y] in `coord_space`, anchor for type=number or text.")
    index: Optional[int] = Field(default=None, description="Integer shown in the badge (type=number).")
    text: Optional[str] = Field(default=None, description="Text content (type=text).")
    label: Optional[str] = Field(default=None, description="Optional short label drawn next to a box/circle/arrow.")
    color: Optional[str] = Field(
        default=None,
        description="Optional hex color like '#FF3B30'. Cycles a default palette if omitted.",
    )


class ImageAnnotateArgs(BaseModel):
    image_path: str = Field()
    annotations: list[Annotation] = Field()
    coord_space: Literal["norm", "pixel"] = Field(default="norm")
    label: str = Field(default="annotated")
    output_dir: str | None = Field(default=None)


TOOL: dict[str, Any] = {"name": "image_annotate", "args": ImageAnnotateArgs}

# Per-type required coordinate keys, used to turn a would-be KeyError into a spec the model
# can act on: testing shows the model inventing {x, y, w, h, thickness, size} instead.
_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "box": ("bbox",),
    "rect": ("bbox",),
    "rectangle": ("bbox",),
    "circle": ("bbox",),
    "ellipse": ("bbox",),
    "arrow": ("start", "end"),
    "number": ("at",),
    "badge": ("at",),
    "text": ("at", "text"),
}

_SPEC_HELP = (
    "Expected: box/circle -> bbox=[x1,y1,x2,y2]; arrow -> start=[x,y], end=[x,y]; "
    "number -> at=[x,y], index=int; text -> at=[x,y], text=str. Optional on any: label, color "
    "(hex). There are no x/y/w/h, x2/y2, thickness or size fields — set line style is automatic, "
    "and coordinates go in the arrays above, read in `coord_space` ('norm' 0-1000 or 'pixel')."
)


def _validate_annotations(anns: list[dict[str, Any]], coord_space: str, size: tuple[int, int]) -> None:
    """Reject a malformed annotation list with a message that states the right shape."""
    for i, ann in enumerate(anns):
        atype = (ann.get("type") or "box").lower()
        required = _REQUIRED_KEYS.get(atype)
        if required is None:
            raise MediaOpsError(
                f"annotations[{i}]: unknown type {atype!r}. Use one of: box, circle, arrow, number, text. {_SPEC_HELP}"
            )
        missing = [k for k in required if ann.get(k) is None]
        if missing:
            raise MediaOpsError(
                f"annotations[{i}] (type={atype}) is missing {', '.join(missing)}; got keys {sorted(ann)}. {_SPEC_HELP}"
            )
        for key, arity in (("bbox", 4), ("start", 2), ("end", 2), ("at", 2)):
            value = ann.get(key)
            if value is None:
                continue
            if not isinstance(value, (list, tuple)) or len(value) != arity:
                raise MediaOpsError(
                    f"annotations[{i}].{key} must be a list of {arity} numbers, got {value!r}. {_SPEC_HELP}"
                )
            if coord_space == "norm" and any(abs(float(v)) > 1000 for v in value):
                raise MediaOpsError(
                    f"annotations[{i}].{key}={list(value)} exceeds the 0-1000 normalized range. "
                    f"These look like pixels of a {size[0]}x{size[1]} image — pass "
                    'coord_space="pixel" and leave them as pixels.'
                )


def _validate_image_path(raw_path: str) -> Path:
    if not raw_path or not raw_path.strip():
        raise MediaOpsError("`image_path` must be a non-empty local image file path.")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if not path.is_file():
        raise MediaOpsError(f"Image file not found: {path}")
    if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_IMAGE_EXTENSIONS))
        raise MediaOpsError(f"Unsupported image extension `{path.suffix}`. Supported: {supported}")
    return path


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    r"""Draw highlight annotations on an image — boxes, arrows, numbered badges (Set-of-Mark), circles, and
    text callouts — and save the result as a focused skill asset. Coordinates are read in `coord_space`:
    `pixel` for measured coordinates, `norm` (0-1000, the grounding convention) otherwise. Arrows,
    badges and text callouts tolerate approximate placement, so eyeballed anchors are fine for them. A
    tight box is different, and estimating one by eye misses: take its coordinates from a `crop_frame`
    region you already have, else `ocr_frames` with `locate` for any target carrying text, else
    `grounding` described in words. Returns the annotated image plus, when the marks cover a small part
    of the frame, a full-resolution close-up of the marked area: check in it that each box landed on its
    target before registering the asset.

    Args:
        image_path: Absolute path to the source image (e.g. a captured frame).
        annotations: Ordered list of highlight annotations to draw on the image.
        coord_space: How to read every coordinate below. `pixel` = pixels after applying the image's EXIF
            display orientation, matching grounding — use it whenever
            you have MEASURED coordinates (an `ocr_frames` `locate` match, a `crop_frame` region, a computed
            offset) and do not convert them yourself. `norm` = 0-1000 per axis, the `grounding` convention; note
            it scales each axis separately, so on a wide frame one x unit and one y unit are different
            distances.
        label: Short slug used in the output filename.
        output_dir: Output directory (default: system temp).
    """
    raw_path = arguments["image_path"]
    annotations = arguments.get("annotations") or []
    label = arguments.get("label") or "annotated"
    output_dir = arguments.get("output_dir")
    coord_space = arguments.get("coord_space") or "norm"
    if coord_space not in ("norm", "pixel"):
        raise MediaOpsError(f"`coord_space` must be 'norm' or 'pixel', got {coord_space!r}.")

    path = _validate_image_path(raw_path)
    if not annotations:
        raise MediaOpsError("`annotations` must not be empty.")

    # Accept either plain dicts (wire calls) or pydantic models (in-process calls).
    anns = [a if isinstance(a, dict) else a.model_dump(exclude_none=True) for a in annotations]

    slug = slugify(label) or "annotated"
    with open_image(str(path)) as img:
        _validate_annotations(anns, coord_space, img.size)
        try:
            annotated = _draw_utils.draw_annotations(img, anns, coord_space)
        except (KeyError, ValueError, TypeError) as exc:
            raise MediaOpsError(f"Invalid annotation spec: {exc}. {_SPEC_HELP}") from exc

    out_dir = asset_dir(output_dir, ANNOT_SUBDIR)
    out_path = out_dir / f"{path.stem[:24]}_{slug}.png"
    _draw_utils.save_png(annotated, str(out_path))
    if not out_path.is_file():
        raise MediaOpsError("Annotated image was not produced.")

    result = {
        "path": str(out_path),
        "size": {"width": annotated.width, "height": annotated.height},
        "coord_space": coord_space,
        "count": len(anns),
        "types": [a.get("type") for a in anns],
    }
    content: list[dict[str, Any]] = [
        {"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)},
        _draw_utils.image_to_preview_block(annotated),
    ]

    extent = _draw_utils.marks_extent(annotated, anns, coord_space)
    closeup = _draw_utils.closeup_block(annotated, extent) if extent else None
    if closeup:
        block, (cw, ch) = closeup
        content.append(
            {
                "type": "text",
                "text": (
                    f"[Close-up of the marked area, {cw}x{ch} at full resolution] "
                    "Check each box actually encloses its target here. If one missed, get "
                    "measured coordinates (ocr_frames `locate`, a crop region, grounding) "
                    "and redraw — a second estimate by eye lands no better than the first."
                ),
            }
        )
        content.append(block)
    return content
