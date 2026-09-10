"""Low-level image primitives + resolution-budget math shared across apis/, renderers/, producers/.

Pure PIL helpers with no MCP-content-block knowledge (that lives in shared.content). PIL, pypdfium2
and resvg_py are imported inside functions so this module is import-safe at startup.
"""

from __future__ import annotations

import base64
import glob
import io
import logging
import math
import os
import sys
from typing import Any

from shared.env import DEFAULT_BUDGET, IMAGE_BUDGET_TOKENS, IMAGE_MIN_PIXELS, TOKEN_SIZE

# Distinct palette for annotating multiple boxes.
COLORS = [
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 0),
    (255, 0, 255),
    (0, 255, 255),
    (255, 128, 0),
    (128, 0, 255),
]


def norm_to_pixel(bbox: list[int], img_w: int, img_h: int) -> list[int]:
    """Convert a [0, 1000] normalized bbox to pixel coordinates."""
    nx1, ny1, nx2, ny2 = bbox
    return [
        round(nx1 / 1000 * img_w),
        round(ny1 / 1000 * img_h),
        round(nx2 / 1000 * img_w),
        round(ny2 / 1000 * img_h),
    ]


def find_font(size: int):
    """A truetype font supporting CJK, falling back to any available font."""

    from PIL import ImageFont

    patterns = [
        "/usr/share/fonts/**/Noto*CJK*.ttc",
        "/usr/share/fonts/**/wqy*.ttc",
        "/usr/share/fonts/**/Noto*CJK*.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/**/*DejaVu*Bold*.ttf",
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
            try:
                return ImageFont.truetype(path, size)
            except (OSError, IOError):
                continue
    return ImageFont.load_default()


def draw_boxes(img, detections: list[dict[str, Any]]):
    """Draw bounding boxes (+ optional labels) on a copy of `img`.

    Each detection: ``{"bbox_pixel": [x1,y1,x2,y2], "label": str, "color": (r,g,b)}``.
    ``label`` and ``color`` are optional — an empty label draws no text box, and a
    missing color cycles the shared palette (so multi-box results stay distinct).
    """
    from PIL import ImageDraw

    annotated = img.copy()
    draw = ImageDraw.Draw(annotated)

    short_edge = min(img.width, img.height)
    line_width = max(2, short_edge // 200)
    font = find_font(max(14, short_edge // 40))

    for i, det in enumerate(detections):
        color = det.get("color") or COLORS[i % len(COLORS)]
        x1, y1, x2, y2 = det["bbox_pixel"]
        # Accept either corner order; Pillow requires top-left then bottom-right.
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        draw.rectangle([x1, y1, x2, y2], outline=color, width=line_width)

        label = det.get("label") or ""
        if label:
            tb = draw.textbbox((0, 0), label, font=font)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            label_y = max(0, y1 - th - 6)
            draw.rectangle([x1, label_y, x1 + tw + 6, label_y + th + 6], fill=color)
            draw.text((x1 + 3, label_y + 2), label, fill=(255, 255, 255), font=font)

    return annotated


def open_image(path: str):
    """Open a photo in display orientation, retaining stored pixels if its EXIF is unreadable."""
    from PIL import Image, ImageOps

    img = Image.open(path)
    try:
        orientation = img.getexif().get(0x0112, 1)
    except Exception:  # noqa: BLE001 — malformed EXIF segment: keep the stored frame, as Image.open does
        return img
    # exif_transpose copies even when there is nothing to do — only pay for it when tagged.
    if orientation in range(2, 9):
        img = ImageOps.exif_transpose(img)
    return img


def render_pdf_page(page, dpi: int = 150):
    """Rasterize a pypdfium2 PdfPage to a PIL Image at `dpi` (scale = dpi / 72)."""
    return page.render(scale=dpi / 72).to_pil()


def svg_to_image(svg_string: str | None = None, svg_path: str | None = None, dpi: int = 150):
    """Rasterize an SVG (string or file path) to a PIL Image via resvg (zoom = dpi / 96).

    Composites onto an opaque white background so transparent SVGs don't show a bare alpha
    channel (which most viewers paint black).
    """
    import resvg_py
    from PIL import Image

    png = bytes(resvg_py.svg_to_bytes(svg_string=svg_string, svg_path=svg_path, zoom=dpi / 96))
    img = Image.open(io.BytesIO(png))
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    return img


# Output extension → PIL format. Anything else falls back to PNG (lossless).
_EXT_FORMAT = {
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "bmp": "BMP",
    "gif": "GIF",
    "tiff": "TIFF",
    "tif": "TIFF",
}


def save_image(img, output_path: str, quality: int = 95) -> None:
    """Save a PIL image, choosing the encoder from its extension.

    JPEG only encodes RGB/L, so palette/alpha/exotic modes are converted to RGB first (a bare
    JPEG save on mode P/LA/RGBA raises OSError).
    """
    ext = os.path.splitext(output_path)[1].lower().lstrip(".")
    fmt = _EXT_FORMAT.get(ext, "PNG")
    if fmt == "JPEG" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    save_kwargs = {"quality": quality} if fmt == "JPEG" else {}
    img.save(output_path, format=fmt, **save_kwargs)


def budget_to_pixels(budget: str, tokens_map: dict[str, int]) -> int:
    """Map a resolution preset ('small'/'normal'/'large') to a pixel budget via its token count."""
    tokens = tokens_map.get(budget, tokens_map[DEFAULT_BUDGET])
    return tokens * TOKEN_SIZE * TOKEN_SIZE


def smart_resize(
    height: int, width: int, min_pixels: int, max_pixels: int, factor: int = TOKEN_SIZE
) -> tuple[int, int]:
    """Resize (h, w) into [min_pixels, max_pixels], snapped to a multiple of `factor` (the patch grid)."""
    if min_pixels > max_pixels:
        logging.warning("min_pixels (%d) > max_pixels (%d), clamping max_pixels to min_pixels", min_pixels, max_pixels)
        max_pixels = min_pixels

    if height * width < min_pixels:
        scale = math.sqrt(min_pixels / (height * width))
        height = int(height * scale)
        width = int(width * scale)

    if height * width > max_pixels:
        scale = math.sqrt(max_pixels / (height * width))
        height = int(height * scale)
        width = int(width * scale)

    height = max(factor, round(height / factor) * factor)
    width = max(factor, round(width / factor) * factor)
    return height, width


def encode_image(img):
    """Encode at the current size; return the encodable image, base64 text, and MIME type."""
    buf = io.BytesIO()
    # JPEG only handles RGB/L — keep palette/alpha as PNG; convert exotic modes to RGB.
    if img.mode in ("RGBA", "LA", "PA", "P"):
        fmt, mime, save_kwargs = "PNG", "image/png", {}
    else:
        fmt, mime, save_kwargs = "JPEG", "image/jpeg", {"quality": 90}
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
    img.save(buf, format=fmt, **save_kwargs)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return img, b64, mime


def process_image(img, min_pixels: int, max_pixels: int):
    """Resize to the pixel budget; return (image, base64, width, height, MIME type)."""
    from PIL import Image

    target_h, target_w = smart_resize(img.height, img.width, min_pixels, max_pixels)
    resized = img if (target_w, target_h) == img.size else img.resize((target_w, target_h), Image.LANCZOS)
    resized, b64, mime = encode_image(resized)
    return resized, b64, target_w, target_h, mime


def render_image_block(img, budget: str = "large") -> tuple[dict[str, str], int, int]:
    """Resize a PIL Image to the token budget and return (image block, width, height)."""
    max_pixels = budget_to_pixels(budget, IMAGE_BUDGET_TOKENS)
    _, b64, w, h, mime = process_image(img, IMAGE_MIN_PIXELS, max_pixels)
    return {"type": "image", "data": b64, "mimeType": mime}, w, h


def image_to_content(img, budget: str = "large") -> dict[str, str]:
    """Convert a PIL Image to an MCP image content block (resized to budget)."""
    block, _w, _h = render_image_block(img, budget)
    return block
