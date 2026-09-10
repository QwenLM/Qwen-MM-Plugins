"""Tests for the core media tools and the MCP server protocol.

Run with:  python3 -m pytest tests/test_tools.py
(or:       uv run --with pytest --with "qwen-mm-plugins[all] @ ." pytest tests/test_tools.py)
"""

import base64
import io

from conftest import mcp_call

from qwen_mm_plugins_core import get_handler, list_tools
from qwen_mm_plugins_core.producers import crop, draw_bbox
from qwen_mm_plugins_core.readers import image as image_reader
from qwen_mm_plugins_core.readers import media_info
from qwen_mm_plugins_core.readers import video as video_reader
from qwen_mm_plugins_core.visualizers import visualize

# Stable core tools that must always be discoverable. We assert this set is a
# subset of what the core server discovers rather than pinning an exact list, so
# adding a core tool later doesn't break this check. Core is local file I/O only —
# cloud API tools live in the qwen-mm-plugins-api / qwen-mm-plugins-search capabilities.
CORE_TOOLS = {
    "read_image",
    "read_video",
    "media_info",
    "visualize",
    "crop",
    "draw_bbox",
    "save_view",
}


# ── helpers ──────────────────────────────────────────────────────────


def _blocks_by_type(content, kind):
    return [b for b in content if b.get("type") == kind]


def _is_error(content):
    return any(b.get("type") == "text" and b["text"].startswith("Error") for b in content)


def _decode_image(block):
    """Decode an MCP image block to a PIL image (validates the base64 + format)."""
    from PIL import Image

    raw = base64.b64decode(block["data"])
    return Image.open(io.BytesIO(raw))


# ── discovery / registry ─────────────────────────────────────────────


def test_all_tools_discovered():
    names = {t["name"] for t in list_tools()}
    missing = CORE_TOOLS - names
    assert not missing, f"core tools not discovered: {missing}"


def test_core_excludes_cloud_tools():
    # Cloud API tools moved to the api/search capabilities — core must not re-expose them.
    names = {t["name"] for t in list_tools()}
    moved = {
        "vision_chat", "ocr", "grounding", "segmentation", "transcribe_audio",
        "web_search", "web_extractor", "image_search",
    }
    assert not (names & moved), f"core unexpectedly exposes cloud tools: {names & moved}"


def test_every_tool_has_schema_and_handler():
    for tool in list_tools():
        assert tool.get("name")
        assert tool["inputSchema"]["type"] == "object"
        assert callable(get_handler(tool["name"]))


def test_unknown_tool_has_no_handler():
    assert get_handler("does_not_exist") is None


# ── read_image ───────────────────────────────────────────────────────


def test_read_image_returns_text_and_image(sample_image):
    content = image_reader.handle({"image_path": sample_image})
    assert not _is_error(content)
    assert len(_blocks_by_type(content, "text")) == 1
    images = _blocks_by_type(content, "image")
    assert len(images) == 1
    assert images[0]["mimeType"] in ("image/png", "image/jpeg")
    # base64 must decode to a real image
    assert _decode_image(images[0]).size[0] > 0


def test_read_image_budget_changes_resolution(sample_image):
    small = image_reader.handle({"image_path": sample_image, "budget": "small"})
    large = image_reader.handle({"image_path": sample_image, "budget": "large"})
    small_px = _decode_image(_blocks_by_type(small, "image")[0]).size
    large_px = _decode_image(_blocks_by_type(large, "image")[0]).size
    assert large_px[0] * large_px[1] >= small_px[0] * small_px[1]


def test_read_image_missing_file():
    content = image_reader.handle({"image_path": "/no/such/file.png"})
    assert _is_error(content)


def _white_bbox(image):
    return image.convert("L").point(lambda p: 255 if p > 128 else 0).getbbox()


def test_read_image_uses_display_orientation(rotated_image):
    # Stored 320x120 + EXIF Orientation 6. Every viewer shows a 120x320 portrait frame; the model
    # has to see that frame too, not the sideways stored pixels (same contract as rotated video).
    content = image_reader.handle({"image_path": rotated_image, "budget": "small"})
    assert not _is_error(content)
    assert "320x120" in content[0]["text"], f"summary reports the stored size: {content[0]['text']}"
    frame = _decode_image(_blocks_by_type(content, "image")[0])
    assert frame.size[1] > frame.size[0], f"portrait photo returned as a {frame.size} landscape frame"
    box = _white_bbox(frame)
    assert box, "white square not found in the returned image"
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    assert cx > frame.width / 2 and cy < frame.height / 2, f"square landed at {box}, expected top-right"


def test_crop_uses_display_orientation(rotated_image, tmp_path):
    # 0-1000 coordinates address the frame read_image showed the model. The subject sits in the
    # top-right of the displayed photo; cropping there must return it, not stored-frame pixels.
    from PIL import Image

    out = tmp_path / "top_right.png"
    content = crop.handle({"image_path": rotated_image, "box": [500, 0, 1000, 300], "output_path": str(out)})
    assert not _is_error(content)
    cropped = Image.open(out)
    assert cropped.size == (60, 96), f"cropped the stored frame instead of the display frame: {cropped.size}"
    assert _white_bbox(cropped), "crop of the displayed top-right corner misses the subject"


def test_draw_bbox_uses_display_orientation(rotated_image, tmp_path):
    from PIL import Image

    out = tmp_path / "annotated.png"
    content = draw_bbox.handle(
        {"image_path": rotated_image, "bboxes": [{"bbox": [500, 0, 1000, 300]}], "output_path": str(out)}
    )
    assert not _is_error(content)
    assert Image.open(out).size == (120, 320), "annotated the stored (sideways) frame"


def test_read_image_tolerates_malformed_exif(tmp_path):
    # An APP1 segment that announces EXIF but does not parse must not fail the read: the photo opens
    # in its stored frame, exactly as a bare Image.open does. (JFIF density is set so PIL itself has
    # no reason to touch the EXIF block at open time - the tag lookup here is the first parse.)
    import struct

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (30, 20), (0, 0, 0)).save(buf, format="JPEG", dpi=(72, 72))
    payload = b"Exif\x00\x00GARBAGE!" + b"\x00" * 20
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    path = tmp_path / "malformed_exif.jpg"
    path.write_bytes(buf.getvalue()[:2] + app1 + buf.getvalue()[2:])

    content = image_reader.handle({"image_path": str(path), "budget": "small"})
    assert not _is_error(content), content[0]["text"]
    assert "20x30" in content[0]["text"], content[0]["text"]


# ── read_video ───────────────────────────────────────────────────────


def test_read_video_returns_frames(sample_video):
    content = video_reader.handle({"video_path": sample_video, "budget": "small"})
    assert not _is_error(content)
    images = _blocks_by_type(content, "image")
    assert len(images) >= 1
    assert _decode_image(images[0]).size[0] > 0
    # first text block is the summary
    assert content[0]["type"] == "text"
    assert "frame" in content[0]["text"].lower()


def test_get_video_info_reports_rotation(sample_video):
    from shared.video import get_video_info

    info = get_video_info(sample_video)
    # rotation is always present (read_video's summary reads info["rotation"]); a normal clip is unrotated.
    assert "rotation" in info and info["rotation"] == 0
    assert info["native_fps"] > 0


def test_get_video_info_swaps_rotated_dimensions(rotated_video):
    from shared.video import get_video_info

    info = get_video_info(rotated_video)
    # Stored 320×120 + rotation 90. ffmpeg autorotates on decode, so the pair callers scale to has to
    # be the display orientation; scaling the upright frame to the stored one distorts it.
    assert info["rotation"] in (90, -90)
    assert (info["width"], info["height"]) == (120, 320)


def test_read_video_preserves_rotated_aspect(rotated_video):
    # A square in a rotation-tagged source must stay square in the extracted frames (regression
    # against a scale target taken from the stored, pre-rotation dimensions).
    content = video_reader.handle({"video_path": rotated_video, "budget": "small", "max_frames": 2})
    assert not _is_error(content)
    frame = _decode_image(_blocks_by_type(content, "image")[0])
    assert frame.size[1] > frame.size[0], f"portrait source produced a {frame.size} landscape frame"
    box = frame.convert("L").point(lambda p: 255 if p > 128 else 0).getbbox()
    assert box, "white square not found in the extracted frame"
    w, h = box[2] - box[0], box[3] - box[1]
    # Only patch-grid rounding may move the aspect ratio; the pre-fix stretch was ~3x.
    assert 0.8 < w / h < 1.25, f"square rendered as {w}x{h} (aspect {w / h:.2f})"


def test_read_video_respects_max_frames(sample_video):
    # 6s @ ~2fps auto → ~12 frames; cap to 3 (above the 2-frame floor) must hold.
    content = video_reader.handle({"video_path": sample_video, "budget": "small", "max_frames": 3})
    assert len(_blocks_by_type(content, "image")) <= 3


def test_read_video_missing_file():
    content = video_reader.handle({"video_path": "/no/such/file.mp4"})
    assert _is_error(content)


def test_read_video_subsecond_timestamps(sample_video):
    # High fps over a short window: frames <1s apart must get distinct,
    # one-decimal timestamps (regression against integer-second truncation).
    content = video_reader.handle(
        {"video_path": sample_video, "budget": "small", "fps": 10, "start_time": 1.0, "end_time": 2.0}
    )
    assert not _is_error(content)
    stamps = [b["text"] for b in content if b["type"] == "text" and b["text"].startswith("<")]
    assert len(stamps) >= 4
    assert len(set(stamps)) == len(stamps), f"timestamps collided: {stamps}"
    # one-decimal precision, pasteable back through parse_time
    from shared.video import parse_time

    for s in stamps:
        assert "." in s
        assert parse_time(s.strip("<>")) is not None


# ── media_info ────────────────────────────────────────────────────────────────


def test_media_info_reports_all_streams(sample_media_av):
    content = media_info.handle({"path": sample_media_av})
    assert not _is_error(content)
    report = content[0]["text"]
    # container + video stream + audio stream must all be summarized
    assert "Container:" in report
    assert "160x120" in report
    assert "h264" in report
    assert "10.00 fps" in report
    assert "Audio stream" in report and "aac" in report
    assert "44100 Hz" in report or "48000 Hz" in report
    assert "no audio stream" not in report


def test_media_info_flags_missing_audio(sample_video):
    content = media_info.handle({"path": sample_video})
    assert not _is_error(content)
    assert "Audio: none (no audio stream)" in content[0]["text"]


def test_media_info_raw_json(sample_video):
    import json

    content = media_info.handle({"path": sample_video, "raw": True})
    assert not _is_error(content)
    raw_blocks = [b for b in content if b["type"] == "text" and b["text"].startswith("Raw ffprobe JSON:")]
    assert len(raw_blocks) == 1
    probe = json.loads(raw_blocks[0]["text"].split("\n", 1)[1])
    assert probe["streams"] and probe["format"]["format_name"]


def test_media_info_missing_file():
    assert _is_error(media_info.handle({"path": "/no/such/file.mp4"}))


# ── visualize dispatch ───────────────────────────────────────────────


def test_visualize_dispatches_image(sample_image):
    content = visualize.handle({"file_path": sample_image})
    assert not _is_error(content)
    assert len(_blocks_by_type(content, "image")) == 1


def test_visualize_dispatches_video(sample_video):
    content = visualize.handle({"file_path": sample_video, "budget": "small"})
    assert not _is_error(content)
    assert len(_blocks_by_type(content, "image")) >= 1


def test_visualize_unsupported_extension(tmp_path):
    p = tmp_path / "data.xyz"
    p.write_text("nope")
    content = visualize.handle({"file_path": str(p)})
    assert _is_error(content)


def test_visualize_dispatches_code(tmp_path):
    # every language the code renderer highlights must be visualizable (E3 regression)
    p = tmp_path / "hello.py"
    p.write_text("x = 1\nprint(x)\n")
    content = visualize.handle({"file_path": str(p)})
    assert not _is_error(content)
    assert any(b.get("type") == "text" and "python" in b["text"].lower() for b in content)


def test_visualize_missing_file():
    assert _is_error(visualize.handle({"file_path": "/no/such/file.pdf"}))


# ── draw_bbox ────────────────────────────────────────────────────────


def test_draw_bbox_accepts_reversed_corners(sample_image, tmp_path):
    from PIL import Image

    from qwen_mm_plugins_core.producers import draw_bbox

    out = tmp_path / "annotated.png"
    content = draw_bbox.handle(
        {
            "image_path": sample_image,
            "bboxes": [{"bbox": [800, 100, 200, 400], "color": "#00FF00"}],
            "output_path": str(out),
        }
    )
    assert not _is_error(content)
    with Image.open(out) as annotated:
        assert annotated.size == (96, 64)
        # Pixel (60, 6) lies on the normalized box's top edge.
        assert annotated.getpixel((60, 6)) == (0, 255, 0), "box was not drawn where the corners describe"


# ── server protocol (real MCP client over stdio) ─────────────────────
# Drives the installed/checked-out server binary through the official MCP SDK
# client: full initialize handshake, tools/list, tools/call. This is the
# end-to-end guard that the SDK bridge (list_tools/call_tool), the entry point,
# and the streaming stdio transport all work together. asyncio.run() drives the
# client so no pytest-asyncio/anyio plugin is required.


def test_server_tools_list(server_dir):
    result = mcp_call(server_dir, lambda s: s.list_tools())
    names = {t.name for t in result.tools}
    # The server must expose exactly what in-process discovery finds (bridge is
    # a faithful pass-through), and that must include the stable core set.
    assert names == {t["name"] for t in list_tools()}
    assert CORE_TOOLS <= names


def test_server_read_image_call(server_dir, sample_image):
    result = mcp_call(server_dir, lambda s: s.call_tool("read_image", {"image_path": sample_image}))
    assert not result.isError
    assert any(getattr(b, "type", None) == "image" for b in result.content)


def test_server_unknown_tool(server_dir):
    # Under the SDK, an unregistered tool surfaces as an isError tool result
    # (the handler raises ValueError), not a JSON-RPC method-not-found error.
    result = mcp_call(server_dir, lambda s: s.call_tool("nope", {}))
    assert result.isError


def test_visualize_plaintext(tmp_path):
    # .txt / .log used to be "unsupported file type"; now they render as a fenced text block.
    p = tmp_path / "notes.txt"
    p.write_text("hello world\nsecond line\n", encoding="utf-8")
    out = visualize.handle({"file_path": str(p)})
    assert out and out[0]["type"] == "text" and "hello world" in out[0]["text"]

    lg = tmp_path / "run.log"
    lg.write_text("LOG LINE ONE", encoding="utf-8")
    out2 = visualize.handle({"file_path": str(lg)})
    assert out2 and out2[0]["type"] == "text" and "LOG LINE ONE" in out2[0]["text"]
