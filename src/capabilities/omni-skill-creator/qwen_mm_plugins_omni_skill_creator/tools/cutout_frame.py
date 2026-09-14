"""MCP tool: cut an irregular region out of a video frame with a real alpha channel.

Stands in for a prompted segmentation model. The split of labour is deliberate and was
measured on a real screencast frame:

  * A VLM is good at *where* -- `grounding` returns a pixel box that lands on the target.
  * A VLM is bad at *the boundary*. Asked for polygon vertices, `qwen3.7-plus`
    returned a bottom edge of 1292, 1311, 1331, 1350, ... -- a constant ~19.4px step, i.e. a
    straight line drawn from imagination, off by 63px on average from the real curve.

So the boundary is found here instead, deterministically: flood the BACKGROUND colour inward
from caller-chosen seed points and take the complement. The edge then sits exactly where the
pixel colour stops matching -- not predicted, but the termination point of colour equality.
Measured cost on a 575x780 work area: 0.6s, no model, no network, no GPU.

Deliberately implemented on Pillow + stdlib only. numpy would be tidier, but the rollout pod
installs offline from a prebuilt wheelhouse, and pillow is already a base dependency while
numpy is not -- so using it would turn a tool addition into a harness change.

Known limit, not worked around: semi-transparent internal structure cannot be separated. A
50%-white stripe over a photo reads as background where the photo is bright and as foreground
where it is dark, so the mask comes out jagged there. `coverage` and `components` in the
result exist to make that visible rather than silent.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any, Optional

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

# Pure-Python flood + component labelling is O(pixels); a whole 4K frame would take tens of
# seconds. Callers already have a box from `grounding`, so ask for it rather than degrade.
MAX_REGION_PIXELS = 3_000_000
MIN_REGION_SIDE = 16
DEFAULT_TOLERANCE = 26
DEFAULT_OPEN_SIZE = 5
DEFAULT_MIN_COMPONENT_FRAC = 0.02
SIMPLIFY_EPSILON = 1.5
SUPERSAMPLE = 4

# Failure signatures observed while building this, both silent without a check:
#   coverage 0.957 -- seeds sampled a colour the object also has, so the flood ate the region
#   coverage ~0    -- tolerance too tight, or a seed sat on the object
# Coverage alone is too weak to be the primary detector: a seed placed ON the target returned
# the inverted mask at coverage 0.85, comfortably under any threshold worth setting. The caller
# is contracted to pass a work area with background AROUND the target, so the real tell is the
# work area's own border ending up inside the cutout.
COVERAGE_TOO_HIGH = 0.95
COVERAGE_TOO_LOW = 0.02
BORDER_INSIDE_FRACTION = 0.9
MANY_COMPONENTS = 4
# A ring per component cannot express an enclosed hole (Pillow fills rings solid), so holes
# above this share of the mask make the polygon record lossy and are called out.
HOLE_FRACTION_WORTH_WARNING = 0.01


class Region(BaseModel):
    x: int = Field(description="Left edge in pixels.")
    y: int = Field(description="Top edge in pixels.")
    w: int = Field(description="Width in pixels.")
    h: int = Field(description="Height in pixels.")


class CutoutFrameArgs(BaseModel):
    video_path: str = Field()
    timestamp: float = Field()
    label: str = Field()
    region: Optional[Region] = Field(default=None)
    background_seeds: Optional[list[list[int]]] = Field(default=None)
    polygon: Optional[list] = Field(default=None)
    tolerance: int = Field(default=DEFAULT_TOLERANCE)
    feather: int = Field(default=0)
    output_dir: str | None = Field(default=None)


TOOL: dict[str, Any] = {"name": "cutout_frame", "args": CutoutFrameArgs}


def _extract_frame(video: str, ts: float, dest: str) -> None:
    _run(
        [
            ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{ts:.3f}",
            "-i",
            video,
            "-frames:v",
            "1",
            "-y",
            dest,
        ]
    )


def flood_background(
    pixels: list[tuple[int, int, int]],
    w: int,
    h: int,
    seeds: list[tuple[int, int]],
    tolerance: int,
) -> tuple[bytearray, list[tuple[int, int, int]]]:
    """Mark object pixels (1) by flooding the background from `seeds` and inverting.

    Each seed contributes its OWN colour and the "is background" test is their union. A single
    median colour was tried first and fails whenever the background is not one shade: around
    the measured shape the canvas below was pure white while the app chrome above was
    (230, 234, 240), and the median matched neither.

    Flooding rather than thresholding globally is the whole point -- a white pixel INSIDE the
    object stays object unless it is actually connected to the outside white.
    """
    colours: list[tuple[int, int, int]] = []
    for sx, sy in seeds:
        c = pixels[sy * w + sx]
        if c not in colours:
            colours.append(c)

    close = bytearray(w * h)
    for i, p in enumerate(pixels):
        pr, pg, pb = p
        for cr, cg, cb in colours:
            if abs(pr - cr) + abs(pg - cg) + abs(pb - cb) <= tolerance:
                close[i] = 1
                break

    background = bytearray(w * h)
    queue: deque[int] = deque()
    for sx, sy in seeds:
        i = sy * w + sx
        if close[i] and not background[i]:
            background[i] = 1
            queue.append(i)
    while queue:
        i = queue.popleft()
        y, x = divmod(i, w)
        if x > 0 and close[i - 1] and not background[i - 1]:
            background[i - 1] = 1
            queue.append(i - 1)
        if x < w - 1 and close[i + 1] and not background[i + 1]:
            background[i + 1] = 1
            queue.append(i + 1)
        if y > 0 and close[i - w] and not background[i - w]:
            background[i - w] = 1
            queue.append(i - w)
        if y < h - 1 and close[i + w] and not background[i + w]:
            background[i + w] = 1
            queue.append(i + w)

    return bytearray(0 if b else 1 for b in background), colours


def open_mask(mask: bytearray, w: int, h: int, size: int) -> bytearray:
    """Binary opening (erode then dilate) — sheds structures thinner than `size`.

    Without it a 1-2px slide border line welded to the shape stretched the reported bounding
    box from 580 rows to 716.
    """
    if size < 3 or size % 2 == 0:
        return mask
    from PIL import Image, ImageFilter

    im = Image.frombytes("L", (w, h), bytes(255 if v else 0 for v in mask))
    im = im.filter(ImageFilter.MinFilter(size)).filter(ImageFilter.MaxFilter(size))
    return bytearray(1 if v else 0 for v in im.tobytes())


def keep_components(mask: bytearray, w: int, h: int, min_frac: float) -> tuple[bytearray, int]:
    """Keep every component at least `min_frac` of the biggest one.

    Keeping only the largest is wrong: one fully background-coloured channel through the object
    (a white stripe running top to bottom) severs a real part of it into its own component.
    Dropping that silently deleted the shape's leftmost 51 columns.
    """
    parts = split_components(mask, w, h, min_frac)
    out = bytearray(w * h)
    for part in parts:
        for i, v in enumerate(part):
            if v:
                out[i] = 1
    return (out, len(parts)) if parts else (mask, 0)


def split_components(mask: bytearray, w: int, h: int, min_frac: float) -> list[bytearray]:
    """Each kept component as its own mask, biggest first.

    Returned separately because a single outer contour cannot describe a severed object: the
    ring traced around one part, filled, swallows the channel that separates it from the other.
    """
    seen = bytearray(w * h)
    components: list[list[int]] = []
    for start in range(w * h):
        if not mask[start] or seen[start]:
            continue
        seen[start] = 1
        queue: deque[int] = deque([start])
        current: list[int] = []
        while queue:
            i = queue.popleft()
            current.append(i)
            y, x = divmod(i, w)
            for j, ok in ((i - 1, x > 0), (i + 1, x < w - 1), (i - w, y > 0), (i + w, y < h - 1)):
                if ok and mask[j] and not seen[j]:
                    seen[j] = 1
                    queue.append(j)
        components.append(current)

    if not components:
        return []
    components.sort(key=len, reverse=True)
    threshold = min_frac * len(components[0])
    out: list[bytearray] = []
    for c in components:
        if len(c) < threshold:
            continue
        part = bytearray(w * h)
        for i in c:
            part[i] = 1
        out.append(part)
    return out


def border_inside_fraction(mask: bytearray, w: int, h: int) -> float:
    """Share of the work area's own border that ended up inside the cutout.

    The caller is asked for a work area with background AROUND the target, so a border almost
    entirely made of "object" means the flood never found the background -- typically a seed
    landed on the target and the mask came back inverted. Coverage does not catch this: that
    case measured 0.85.
    """
    idx = set()
    for x in range(w):
        idx.add(x)
        idx.add((h - 1) * w + x)
    for y in range(h):
        idx.add(y * w)
        idx.add(y * w + w - 1)
    return sum(1 for i in idx if mask[i]) / float(len(idx)) if idx else 0.0


def enclosed_hole_pixels(mask: bytearray, w: int, h: int) -> int:
    """Background pixels the border cannot reach — i.e. holes fully enclosed by the object."""
    outside = bytearray(w * h)
    queue: deque[int] = deque()
    for x in range(w):
        for i in (x, (h - 1) * w + x):
            if not mask[i] and not outside[i]:
                outside[i] = 1
                queue.append(i)
    for y in range(h):
        for i in (y * w, y * w + w - 1):
            if not mask[i] and not outside[i]:
                outside[i] = 1
                queue.append(i)
    while queue:
        i = queue.popleft()
        y, x = divmod(i, w)
        for j, ok in ((i - 1, x > 0), (i + 1, x < w - 1), (i - w, y > 0), (i + w, y < h - 1)):
            if ok and not mask[j] and not outside[j]:
                outside[j] = 1
                queue.append(j)
    return sum(1 for i in range(w * h) if not mask[i] and not outside[i])


def trace_contour(mask: bytearray, w: int, h: int) -> list[tuple[int, int]]:
    """Moore-neighbour trace of the outer boundary, as (x, y) in mask-local coordinates."""
    start = next((i for i in range(w * h) if mask[i]), None)
    if start is None:
        return []
    neighbours = ((-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1))
    sy, sx = divmod(start, w)
    contour = [(sy, sx)]
    cur, backtrack = (sy, sx), 7
    total = sum(mask)
    for _ in range(4 * total + 10):
        moved = False
        for k in range(8):
            d = (backtrack + 1 + k) % 8
            ny, nx = cur[0] + neighbours[d][0], cur[1] + neighbours[d][1]
            if 0 <= ny < h and 0 <= nx < w and mask[ny * w + nx]:
                backtrack = (d + 5) % 8
                cur = (ny, nx)
                contour.append(cur)
                moved = True
                break
        if not moved or (len(contour) > 3 and cur == (sy, sx)):
            break
    return [(x, y) for y, x in contour]


def simplify(points: list[tuple[int, int]], epsilon: float) -> list[tuple[int, int]]:
    """Iterative Douglas-Peucker (iterative because a 5762-point contour overflows recursion)."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        ax, ay = points[lo]
        bx, by = points[hi]
        dx, dy = bx - ax, by - ay
        norm = (dx * dx + dy * dy) ** 0.5
        best, best_d = lo, -1.0
        for i in range(lo + 1, hi):
            px, py = points[i]
            if norm:
                d = abs(dx * (py - ay) - dy * (px - ax)) / norm
            else:
                d = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            if d > best_d:
                best, best_d = i, d
        if best_d > epsilon:
            keep[best] = True
            stack.append((lo, best))
            stack.append((best, hi))
    return [p for p, k in zip(points, keep) if k]


def rasterize_polygon(rings: list[list[tuple[int, int]]], w: int, h: int):
    """Antialiased L-mode alpha for one or more rings, via supersampled rasterisation.

    Takes a list of rings, not one ring: a single outline cannot describe an object severed by
    a background-coloured channel, and that is the common case on a screencast.
    """
    from PIL import Image, ImageDraw

    big = Image.new("L", (w * SUPERSAMPLE, h * SUPERSAMPLE), 0)
    draw = ImageDraw.Draw(big)
    for ring in rings:
        if len(ring) >= 3:
            draw.polygon([(x * SUPERSAMPLE, y * SUPERSAMPLE) for x, y in ring], fill=255)
    return big.resize((w, h), Image.LANCZOS)


def _coerce_points(raw: Any, name: str) -> list[tuple[int, int]]:
    if not isinstance(raw, list) or not raw:
        raise MediaOpsError(f"`{name}` must be a non-empty list of [x, y] pairs.")
    points: list[tuple[int, int]] = []
    for item in raw:
        if isinstance(item, dict):
            item = [item.get("x"), item.get("y")]
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise MediaOpsError(f"`{name}` entries must be [x, y] pairs, got {item!r}.")
        try:
            points.append((int(item[0]), int(item[1])))
        except (TypeError, ValueError) as exc:
            raise MediaOpsError(f"`{name}` entries must be numeric, got {item!r}.") from exc
    return points


def _coerce_rings(raw: Any, name: str) -> list[list[tuple[int, int]]]:
    """Accept either one ring [[x, y], ...] or several [[[x, y], ...], ...].

    Both shapes are accepted because the tool's own output is a list of rings, and a caller
    hand-writing a single outline should not have to wrap it.
    """
    if not isinstance(raw, list) or not raw:
        raise MediaOpsError(f"`{name}` must be a non-empty list of [x, y] pairs or of rings.")
    first = raw[0]
    nested = isinstance(first, list) and first and isinstance(first[0], (list, tuple, dict))
    rings = [_coerce_points(r, name) for r in raw] if nested else [_coerce_points(raw, name)]
    if not any(len(r) >= 3 for r in rings):
        raise MediaOpsError(f"`{name}` needs at least one ring with 3 or more vertices.")
    return rings


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    r"""Cut an irregularly-shaped region out of a video frame into a transparent PNG, and get its outline
    back as measured polygon vertices. Use it when a rectangle will not do — a shape with a curved or
    angled edge, an icon that must sit on any background, a UI panel isolated from its surroundings.
    `crop_frame` is cheaper and is the right call whenever a plain rectangle really is enough. Two ways
    to say what to keep. Either pass `polygon` (exact by construction), or pass a `region` from
    `grounding` plus `background_seeds` on the background around the target — the boundary is then
    measured by flooding the background colour, so it lands on the real pixel edge instead of an
    estimated one. The returned `polygon` is the measured outline in full-frame pixels, one ring per
    component (a background-coloured channel through the target severs it, and one ring could not
    describe that): record it for provenance, or feed it back in to reproduce the same cut. Check
    `coverage`, `border_inside_fraction` and `warnings` before shipping the asset — they are what tell
    you a seed sampled the wrong colour. Two triggers should bring you straight here instead of to
    `crop_frame`: the asset's value is THE SUBJECT ITSELF rather than the screen it sat on — an icon, a
    logo, a mascot, a character, an illustration — because a rectangle drags the tutorial's own
    background along with it and the result cannot be dropped onto a consumer's slide or page; or THE
    SKILL TEACHES isolating / removing a background, where a cut-out subject IS the demonstration of the
    result.

    Args:
        video_path: Path to the source video file.
        timestamp: Timestamp in seconds to extract the frame.
        label: Short descriptive slug for the cutout (e.g. 'striped-shape').
        region: Work area {x, y, w, h} in pixels — pass `grounding`'s bbox_px, padded a little so background is
            included on every side. Defaults to the whole frame, which is slower and more likely to catch
            unrelated same-coloured areas.
        background_seeds: Points [[x, y], ...] in FULL-FRAME pixel coordinates that sit on the BACKGROUND around
            the target — one per distinct background colour. Required unless `polygon` is given. Each seed's own
            colour is sampled and flooded, so a background made of two shades (a white canvas plus grey app
            chrome) needs a seed on each.
        polygon: Explicit outline in full-frame pixels — either one ring [[x, y], ...] or several [[[x, y],
            ...], ...]. Given this, no flood runs and the cutout is exact by construction: the escape hatch when
            the background is not uniform enough to flood, and the way to replay this tool's own `polygon`
            output. Rings are filled solid, so an enclosed hole cannot be expressed.
        tolerance: Max summed per-channel difference from a seed colour still counted as background (default
            26). Raise it for gradients or compression noise; too high and the flood leaks through the object's
            own edge.
        feather: Gaussian blur radius on the alpha edge. 0 keeps the hard measured edge.
        output_dir: Output directory (default: system temp).
    """
    from PIL import Image, ImageFilter

    path = validate_video_path(arguments["video_path"])
    meta = get_video_metadata(str(path))
    label = arguments.get("label") or ""
    slug = slugify(label)
    if not slug:
        raise MediaOpsError("`label` must be a short descriptive slug (e.g. 'striped-shape').")

    raw_polygon = arguments.get("polygon")
    raw_seeds = arguments.get("background_seeds")
    if not raw_polygon and not raw_seeds:
        raise MediaOpsError(
            "Pass either `polygon` (explicit outline) or `background_seeds` (points on the "
            "background, one per distinct background colour) — otherwise there is nothing to "
            "separate the target from."
        )

    ts = min(max(0.0, float(arguments["timestamp"])), max(0.0, meta.duration_sec - 0.05))
    out_dir = asset_dir(arguments.get("output_dir"), FRAMES_SUBDIR)
    frame_path = out_dir / f".cutout_src_{format_mmss(ts)}_{slug}.png"
    out_path = out_dir / f"{format_mmss(ts)}_{slug}-cutout.png"

    _extract_frame(str(path), ts, str(frame_path))
    try:
        with Image.open(frame_path) as opened:
            frame = opened.convert("RGB")
    finally:
        frame_path.unlink(missing_ok=True)
    fw, fh = frame.size

    region = arguments.get("region")
    if region is None:
        rx, ry, rw, rh = 0, 0, fw, fh
    else:
        if not isinstance(region, dict):
            region = region.model_dump()
        rx, ry = int(region["x"]), int(region["y"])
        rw, rh = int(region["w"]), int(region["h"])
    rx, ry = max(0, rx), max(0, ry)
    rw, rh = min(rw, fw - rx), min(rh, fh - ry)
    if rw < MIN_REGION_SIDE or rh < MIN_REGION_SIDE:
        raise MediaOpsError(f"Work area {rw}x{rh} is smaller than {MIN_REGION_SIDE}px a side.")
    if rw * rh > MAX_REGION_PIXELS:
        raise MediaOpsError(
            f"Work area {rw}x{rh} = {rw * rh} pixels exceeds the {MAX_REGION_PIXELS} limit. "
            "Pass a tighter `region` (grounding's bbox_px padded by ~20px is usually right)."
        )

    warnings: list[str] = []
    result: dict[str, Any] = {
        "path": str(out_path),
        "seconds": round(ts, 3),
        "label": label,
        "frame_size": {"w": fw, "h": fh},
        "region": {"x": rx, "y": ry, "w": rw, "h": rh},
    }

    if raw_polygon:
        rings = _coerce_rings(raw_polygon, "polygon")
        alpha_full = rasterize_polygon(rings, fw, fh)
        result["mode"] = "polygon"
        result["components"] = sum(1 for r in rings if len(r) >= 3)
    else:
        seeds_abs = _coerce_points(raw_seeds, "background_seeds")
        seeds_local = [(x - rx, y - ry) for x, y in seeds_abs if rx <= x < rx + rw and ry <= y < ry + rh]
        if not seeds_local:
            raise MediaOpsError(
                f"No `background_seeds` fall inside the work area "
                f"{rx},{ry} {rw}x{rh}. Seeds are FULL-FRAME pixel coordinates."
            )
        if len(seeds_local) < len(seeds_abs):
            warnings.append(
                f"{len(seeds_abs) - len(seeds_local)} of {len(seeds_abs)} seeds fell outside the "
                "work area and were ignored."
            )

        sub = frame.crop((rx, ry, rx + rw, ry + rh))
        raw = sub.tobytes()
        pixels = list(zip(raw[0::3], raw[1::3], raw[2::3]))
        tolerance = max(0, int(arguments.get("tolerance", DEFAULT_TOLERANCE)))
        mask, colours = flood_background(pixels, rw, rh, seeds_local, tolerance)
        mask = open_mask(mask, rw, rh, DEFAULT_OPEN_SIZE)
        parts = split_components(mask, rw, rh, DEFAULT_MIN_COMPONENT_FRAC)
        mask = bytearray(rw * rh)
        for part in parts:
            for i, v in enumerate(part):
                if v:
                    mask[i] = 1
        components = len(parts)

        area = sum(mask)
        coverage = area / float(rw * rh)
        if area == 0:
            raise MediaOpsError(
                f"Nothing left after flooding: every pixel in the work area is within "
                f"tolerance {tolerance} of a seed colour. Lower `tolerance`, or check the seeds "
                "are on the background and not on the target."
            )

        border = border_inside_fraction(mask, rw, rh)
        if border >= BORDER_INSIDE_FRACTION:
            warnings.append(
                f"{border:.0%} of the work area's own border is inside the cutout, so the "
                "background was never found — a seed almost certainly sits on the target and the "
                "mask came back inverted. Move the seeds onto background around the target."
            )
        if coverage >= COVERAGE_TOO_HIGH:
            warnings.append(
                f"coverage {coverage:.3f} — the cutout is almost the whole work area, which is "
                "what a seed sampled from the wrong colour looks like. Verify each seed sits on "
                "background, or raise `tolerance`."
            )
        if coverage <= COVERAGE_TOO_LOW:
            warnings.append(
                f"coverage {coverage:.3f} — almost nothing was kept. `tolerance` is probably too "
                "high, so the flood leaked through the target's edge."
            )
        if components > MANY_COMPONENTS:
            warnings.append(
                f"{components} separate components kept. Expect this when the target contains "
                "semi-transparent structure: it reads as background over bright areas and as "
                "foreground over dark ones, which this method cannot separate."
            )

        holes = enclosed_hole_pixels(mask, rw, rh)
        if holes > HOLE_FRACTION_WORTH_WARNING * area:
            warnings.append(
                f"{holes} pixels form holes fully enclosed by the target. The PNG's alpha keeps "
                "them transparent, but `polygon` is a list of filled rings and cannot express a "
                "hole — replaying it would fill them in."
            )

        contour_total = 0
        polygon_rings = []
        for part in parts:
            ring_local = trace_contour(part, rw, rh)
            contour_total += len(ring_local)
            simplified = simplify(ring_local, SIMPLIFY_EPSILON)
            if len(simplified) >= 3:
                polygon_rings.append([(x + rx, y + ry) for x, y in simplified])

        alpha_full = Image.new("L", (fw, fh), 0)
        alpha_full.paste(Image.frombytes("L", (rw, rh), bytes(255 if v else 0 for v in mask)), (rx, ry))
        result["mode"] = "flood"
        result["components"] = components
        result["coverage"] = round(coverage, 4)
        result["border_inside_fraction"] = round(border, 4)
        result["enclosed_hole_pixels"] = holes
        result["tolerance"] = tolerance
        result["background_colors"] = [list(c) for c in colours]
        result["contour_pixels"] = contour_total
        rings = polygon_rings

    feather = max(0, int(arguments.get("feather", 0)))
    if feather:
        alpha_full = alpha_full.filter(ImageFilter.GaussianBlur(feather))

    bbox = alpha_full.getbbox()
    if bbox is None:
        raise MediaOpsError("The resulting cutout is fully transparent — nothing was selected.")

    cutout = frame.convert("RGBA")
    cutout.putalpha(alpha_full)
    cutout = cutout.crop(bbox)
    cutout.save(out_path)
    if not out_path.is_file():
        raise MediaOpsError("Cutout PNG was not produced.")

    opaque = sum(1 for v in cutout.getchannel("A").tobytes() if v > 127)
    result["size"] = {"w": cutout.width, "h": cutout.height}
    result["crop_offset"] = {"x": bbox[0], "y": bbox[1]}
    result["opaque_pixels"] = opaque
    result["opaque_fraction"] = round(opaque / float(cutout.width * cutout.height), 4)
    result["polygon"] = [[[x, y] for x, y in ring] for ring in rings]
    result["polygon_vertices"] = sum(len(ring) for ring in rings)
    result["hint"] = (
        "polygon is the measured outline in full-frame pixels, one ring per component — record "
        "it in the asset manifest for provenance, or pass it straight back as `polygon` to "
        "reproduce this exact cut."
    )
    if warnings:
        result["warnings"] = warnings
    return [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}]
