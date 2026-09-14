"""Self-contained image-annotation primitives (not an MCP tool — no TOOL export).

Pure-PIL drawing helpers used by image_annotate. Kept local to omni-skill-creator so the
plugin stays self-contained (no dependency on the shared library). PIL is imported inside
functions so importing this module is cheap and import-safe at startup.

Coordinates come in one of two spaces, selected per call: 0-1000 normalized (the
convention shared with grounding/draw_bbox elsewhere in the repo) or raw source pixels.
Pixels exist because normalization is per-axis: on a 2880x900 frame one x unit is 2.88 px
while one y unit is 0.9 px, and estimating in that space is where box placement drifts.
"""

from __future__ import annotations

import base64
import glob
import io
import math
import os
import sys
from typing import Any

# Distinct palette for cycling across multiple annotations (same spirit as shared.image.COLORS).
COLORS = [
    (255, 59, 48),  # red
    (52, 199, 89),  # green
    (0, 122, 255),  # blue
    (255, 149, 0),  # orange
    (175, 82, 222),  # purple
    (255, 204, 0),  # yellow
    (90, 200, 250),  # cyan
    (255, 45, 146),  # magenta
]

WHITE = (255, 255, 255)
RED = (230, 30, 30)  # single default mark color — annotations stay simple, not flashy


def find_font_file() -> str | None:
    """Absolute path to a usable TrueType font (CJK-capable if available), or None.

    Includes ~/.local/share/fonts — where headless harnesses may unpack their
    CJK font bundle — so ffmpeg `drawtext` and PIL can resolve a real font by path instead of
    relying on fontconfig's default family (which fails silently on lean containers) or falling
    back to a tiny bitmap default.
    """
    patterns = [
        os.path.expanduser("~/.local/share/fonts/**/*.ttc"),
        os.path.expanduser("~/.local/share/fonts/**/*.ttf"),
        "/usr/share/fonts/**/Noto*CJK*.ttc",
        "/usr/share/fonts/**/wqy*.ttc",
        "/usr/share/fonts/**/Noto*CJK*.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/**/*DejaVu*Bold*.ttf",
        "/usr/share/fonts/**/*DejaVu*.ttf",
    ]
    if sys.platform == "win32":
        windir = os.environ.get("SYSTEMROOT", r"C:\Windows")
        fonts = os.path.join(windir, "Fonts")
        patterns = [
            os.path.join(fonts, "msyh*.ttc"),
            os.path.join(fonts, "simhei.ttf"),
            os.path.join(fonts, "simsun.ttc"),
            os.path.join(fonts, "arial*.ttf"),
        ] + patterns
    for pattern in patterns:
        for path in glob.glob(pattern, recursive=True):
            if os.path.isfile(path):
                return path
    return None


def find_font(size: int):
    """A truetype font supporting CJK, falling back to any available font."""
    from PIL import ImageFont

    path = find_font_file()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            pass
    return ImageFont.load_default()


def hex_to_rgb(hex_str: str) -> tuple[int, int, int] | None:
    """Parse '#RRGGBB' (or 'RRGGBB') into an (r, g, b) tuple; None if malformed."""
    h = (hex_str or "").lstrip("#")
    if len(h) != 6:
        return None
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return None


def _scale(space: str, w: int, h: int) -> tuple[float, float]:
    """Per-axis multiplier taking incoming coordinates to pixels."""
    return (1.0, 1.0) if space == "pixel" else (w / 1000, h / 1000)


def _pt(pt: list[int], w: int, h: int, space: str = "norm") -> tuple[int, int]:
    """`[x, y]` in `space` -> pixel point."""
    fx, fy = _scale(space, w, h)
    return (round(pt[0] * fx), round(pt[1] * fy))


def _box(box: list[int], w: int, h: int, space: str = "norm") -> list[int]:
    """`[x1, y1, x2, y2]` in `space` -> pixel box, corners ordered."""
    x1, y1, x2, y2 = box
    fx, fy = _scale(space, w, h)
    px1, px2 = sorted((round(x1 * fx), round(x2 * fx)))
    py1, py2 = sorted((round(y1 * fy), round(y2 * fy)))
    return [px1, py1, px2, py2]


def _halo(width: int) -> int:
    """White-outline thickness that pairs with a colored line of `width` px."""
    return max(2, width // 2)


def draw_text_label(draw, xy: tuple[int, int], text: str, color, font) -> None:
    """Draw `text` as outlined glyphs (colored fill + thin white stroke) at `xy`.

    No filled plate: a thin white outline keeps the text legible on any
    background — for humans and OCR — without a colored box that would occlude
    the region behind it.
    """
    if not text:
        return
    x, y = xy
    tb = draw.textbbox((0, 0), text, font=font)
    size = tb[3] - tb[1]
    stroke = max(2, round(size / 8))
    x = max(stroke, x)
    y = max(stroke, y)
    draw.text(
        (x - tb[0], y - tb[1]),
        text,
        fill=color,
        font=font,
        stroke_width=stroke,
        stroke_fill=WHITE,
    )


def draw_arrow(draw, start: tuple[int, int], end: tuple[int, int], color, width: int) -> None:
    """A straight shaft with a triangular head at `end`, haloed in white.

    Colored line + head drawn over a slightly wider white underlay — the same
    white-outlined style as the labels and number badges.
    """
    hw = max(1, width // 4)  # thinner white edge on the arrow than the box/ring halo
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    head_len = max(14, width * 6)
    spread = math.radians(26)
    left = (end[0] - head_len * math.cos(angle - spread), end[1] - head_len * math.sin(angle - spread))
    right = (end[0] - head_len * math.cos(angle + spread), end[1] - head_len * math.sin(angle + spread))
    draw.line([start, end], fill=WHITE, width=width + 2 * hw)
    draw.line([start, end], fill=color, width=width)
    draw.polygon([end, left, right], fill=color, outline=WHITE, width=hw)


def draw_number_badge(draw, center: tuple[int, int], index: int, color, font) -> None:
    """A hollow circle enclosing the numeral, in the same white-outlined red style.

    Same visual language as the text labels: a red mark haloed by a thin white
    outline for legibility on any background, no solid fill. The ring is a red
    circle edged in white (white just outside and inside it), with a
    white-stroked red numeral centered inside.
    """
    cx, cy = center
    text = str(index)
    tb = draw.textbbox((0, 0), text, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    r = max(tw, th) // 2 + max(6, th // 3)
    ring = max(2, r // 12)
    halo = max(1, ring // 2)
    # White halo first (a wider white ring), then the red ring centered on it —
    # so the red circle is edged in white on both sides, matching the text stroke.
    draw.ellipse(
        [cx - r - halo, cy - r - halo, cx + r + halo, cy + r + halo],
        outline=WHITE,
        width=ring + 2 * halo,
    )
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=ring)
    stroke = max(2, round(th / 8))
    draw.text(
        (cx - tw / 2 - tb[0], cy - th / 2 - tb[1]),
        text,
        fill=color,
        font=font,
        stroke_width=stroke,
        stroke_fill=WHITE,
    )


def draw_annotations(img, annotations: list[dict[str, Any]], coord_space: str = "norm"):
    """Draw a list of highlight annotations on a copy of `img`; return the annotated image.

    Each annotation is a dict with a ``type`` and coordinates in `coord_space`
    (``"norm"`` = 0-1000, ``"pixel"`` = source pixels):
      - ``box``    / ``circle``: ``bbox=[x1,y1,x2,y2]`` (+ optional ``label``)
      - ``arrow``:               ``start=[x,y]``, ``end=[x,y]`` (+ optional ``label``)
      - ``number``:              ``at=[x,y]``, ``index=int``
      - ``text``:                ``at=[x,y]``, ``text=str``
    Optional ``color`` (hex); otherwise the palette cycles so multiple marks stay distinct.
    """
    from PIL import ImageDraw

    annotated = img.convert("RGB").copy()
    draw = ImageDraw.Draw(annotated)
    w, h = annotated.size

    short_edge = min(w, h)
    line_width = max(2, min(6, round(short_edge / 220)))
    label_font = find_font(max(20, min(46, round(short_edge / 22))))
    badge_font = find_font(max(24, min(60, round(short_edge / 16))))

    for i, ann in enumerate(annotations):
        atype = (ann.get("type") or "box").lower()
        # Default to a single red so marks stay simple and non-distracting;
        # an explicit per-annotation hex `color` still overrides.
        color = (hex_to_rgb(ann["color"]) if ann.get("color") else None) or RED

        if atype in ("box", "rect", "rectangle"):
            x1, y1, x2, y2 = _box(ann["bbox"], w, h, coord_space)
            hw = _halo(line_width)
            draw.rectangle([x1 - hw, y1 - hw, x2 + hw, y2 + hw], outline=WHITE, width=line_width + 2 * hw)
            draw.rectangle([x1, y1, x2, y2], outline=color, width=line_width)
            draw_text_label(draw, (x1, y1), ann.get("label") or "", color, label_font)

        elif atype in ("circle", "ellipse"):
            x1, y1, x2, y2 = _box(ann["bbox"], w, h, coord_space)
            hw = _halo(line_width)
            draw.ellipse([x1 - hw, y1 - hw, x2 + hw, y2 + hw], outline=WHITE, width=line_width + 2 * hw)
            draw.ellipse([x1, y1, x2, y2], outline=color, width=line_width)
            draw_text_label(draw, (x1, y1), ann.get("label") or "", color, label_font)

        elif atype == "arrow":
            start = _pt(ann["start"], w, h, coord_space)
            end = _pt(ann["end"], w, h, coord_space)
            draw_arrow(draw, start, end, color, max(4, line_width * 2))
            draw_text_label(draw, (start[0], start[1]), ann.get("label") or "", color, label_font)

        elif atype in ("number", "badge"):
            center = _pt(ann["at"], w, h, coord_space)
            draw_number_badge(draw, center, int(ann.get("index", i + 1)), color, badge_font)

        elif atype == "text":
            anchor = _pt(ann["at"], w, h, coord_space)
            draw_text_label(draw, anchor, ann.get("text") or ann.get("label") or "", color, label_font)

        else:
            raise ValueError(f"unknown annotation type: {atype!r}")

    return annotated


def marks_extent(img, annotations: list[dict[str, Any]], coord_space: str = "norm") -> list[int] | None:
    """Pixel bounds of the box/circle marks, or None if there are none.

    Only the shapes that must sit *tightly* on a target count: an arrow tail or a text
    callout is deliberately placed in empty space, so including them would blow the
    region up and defeat the close-up.
    """
    w, h = img.size
    boxes = [
        _box(ann["bbox"], w, h, coord_space)
        for ann in annotations
        if (ann.get("type") or "box").lower() in ("box", "rect", "rectangle", "circle", "ellipse") and ann.get("bbox")
    ]
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def closeup_block(img, extent: list[int], pad_ratio: float = 0.15, max_edge: int = 1024):
    """A full-resolution crop around `extent`, or None when it covers most of the image.

    The whole-image preview is downscaled, so on a wide ribbon a mark that missed its
    target by 200 px is invisible in it. This is the view that shows whether the box
    actually landed. Skipped when the marks already span most of the frame — then the
    plain preview shows the same thing.
    """
    w, h = img.size
    x1, y1, x2, y2 = extent
    if (max(1, x2 - x1) * max(1, y2 - y1)) / float(w * h) > 0.4:
        return None
    pad_x = max(24, round((x2 - x1) * pad_ratio))
    pad_y = max(24, round((y2 - y1) * pad_ratio))
    crop = img.crop((max(0, x1 - pad_x), max(0, y1 - pad_y), min(w, x2 + pad_x), min(h, y2 + pad_y)))
    return image_to_preview_block(crop, max_edge=max_edge), crop.size


def save_png(img, path: str) -> None:
    """Save `img` as PNG (RGB)."""
    img.convert("RGB").save(path, format="PNG")


def image_to_preview_block(img, max_edge: int = 1024) -> dict[str, str]:
    """Downscale to a preview and return an MCP image content block (base64 PNG).

    1024 rather than 768: at 768 a 1920x174 ribbon comes back 70 px tall, where the
    button labels are illegible and a misplaced mark cannot be spotted at all.
    """
    from PIL import Image

    im = img.convert("RGB")
    w, h = im.size
    scale = min(1.0, max_edge / max(w, h))
    if scale < 1.0:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return {"type": "image", "data": base64.b64encode(buf.getvalue()).decode(), "mimeType": "image/png"}
