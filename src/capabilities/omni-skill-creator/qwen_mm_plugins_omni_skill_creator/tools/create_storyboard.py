"""MCP tool: render a tiled frame grid (storyboard) for quick visual overview."""

from __future__ import annotations

import json
import math
from typing import Any

from pydantic import BaseModel, Field

from ._draw_utils import find_font_file
from ._media_utils import (
    MAX_STORYBOARD_TILES,
    MediaOpsError,
    _run,
    ffmpeg_path,
    format_mmss,
    get_video_metadata,
    has_filter,
    parse_pts_times,
    validate_output_dir,
    validate_video_path,
)


class CreateStoryboardArgs(BaseModel):
    video_path: str = Field()
    cols: int = Field(default=4)
    rows: int = Field(default=2)
    start_sec: float = Field(default=0.0)
    end_sec: float | None = Field(default=None)
    fps: float | None = Field(default=None)
    output_dir: str | None = Field(default=None)


TOOL: dict[str, Any] = {"name": "create_storyboard", "args": CreateStoryboardArgs}


TILE_PADDING = 4
TILE_MARGIN = 4


def _format_hms(seconds: float) -> str:
    """Render an absolute timestamp as `H:MM:SS` — same reading as drawtext's `%{pts:hms}`."""
    total = max(0, int(round(seconds)))
    return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _burn_labels_pil(out_path, gc: int, gr: int, times: list[float]) -> str:
    """Burn absolute timecodes into an already-tiled storyboard with PIL. "" = success.

    The drawtext-free path. `times` are the frames' real source timestamps (from showinfo), not
    nominal sample times — deriving them from start+i*step is exactly the bug that made the
    burned labels read half a sampling interval early.

    `tile` lays cells out deterministically, so each rect is recoverable from the output size
    plus the grid/padding/margin we asked for.
    """
    try:
        from PIL import Image, ImageDraw

        from ._draw_utils import find_font

        with Image.open(out_path) as opened:
            img = opened.convert("RGB")
        width, height = img.size
        cell_w = (width - 2 * TILE_MARGIN - (gc - 1) * TILE_PADDING) / gc
        cell_h = (height - 2 * TILE_MARGIN - (gr - 1) * TILE_PADDING) / gr
        if cell_w < 8 or cell_h < 8:
            return f"unexpected storyboard geometry: {width}x{height} for {gc}x{gr} grid"

        font = find_font(max(12, min(28, int(cell_h / 9))))
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for i, t in enumerate(times[: gc * gr]):
            x0 = TILE_MARGIN + (i % gc) * (cell_w + TILE_PADDING)
            y0 = TILE_MARGIN + (i // gc) * (cell_h + TILE_PADDING)
            text = _format_hms(t)
            tb = draw.textbbox((0, 0), text, font=font)
            tx, ty = x0 + 6, y0 + 6
            draw.rectangle(
                [tx - 4, ty - 4, tx + (tb[2] - tb[0]) + 4, ty + (tb[3] - tb[1]) + 4],
                fill=(0, 0, 0, 115),  # matches drawtext's box=1:boxcolor=black@0.45
            )
            draw.text((tx - tb[0], ty - tb[1]), text, fill=(255, 255, 255, 255), font=font)
        Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB").save(out_path, format="JPEG", quality=92)
        return ""
    except Exception as exc:  # noqa: BLE001 - never let labeling sink the storyboard itself
        return f"{type(exc).__name__}: {exc}"


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    r"""Render a single tiled grid of evenly spaced frames with burned ABSOLUTE timestamps. Whole-video
    (default) gives a quick overview of the visual flow; set start_sec/end_sec to tile just a short span
    densely — the fast way to SEE how something changes over a few seconds (an animation, a value being
    entered, a transition) in ONE image, instead of many single-frame crops. Frames are downscaled, so
    it reads motion/flow well but not tiny text (use crop_frame / ocr_frames for that).

    Args:
        video_path: Path to the source video file.
        cols: Number of tile columns (1-6).
        rows: Number of tile rows (1-4).
        start_sec: Window start in seconds. Default 0 = from the beginning.
        end_sec: Window end in seconds. Default = end of the video. Set start_sec+end_sec to storyboard just a
            SHORT span densely — the fast way to see how something changes over a few seconds (an animation, a
            value being entered, a transition) in one image.
        fps: Target frames/sec sampled across the window. Optional; unset fills the grid evenly. Total tiles are
            capped for readability, so a shorter window is the real density knob.
        output_dir: Output directory (default: system temp).
    """
    raw_path = arguments["video_path"]
    cols = arguments.get("cols", 4)
    rows = arguments.get("rows", 2)
    start_sec = arguments.get("start_sec", 0.0)
    end_sec = arguments.get("end_sec")
    fps = arguments.get("fps")
    output_dir = arguments.get("output_dir")

    path = validate_video_path(raw_path)
    out_dir = validate_output_dir(output_dir)
    meta = get_video_metadata(str(path))

    # ── Window within the video ──────────────────────────────────────────────
    duration = max(meta.duration_sec, 0.5)
    start = min(max(float(start_sec or 0.0), 0.0), max(duration - 0.5, 0.0))
    end = duration if end_sec is None else float(end_sec)
    end = min(max(end, start + 0.5), duration)
    wlen = end - start

    # ── Tile budget + grid ───────────────────────────────────────────────────
    # Honor the requested grid, capped for readability; fps (if given) sets the target
    # sampling density. A shorter window is the real density knob (tiles are capped).
    cols = min(max(1, int(cols)), 6)
    rows = min(max(1, int(rows)), 4)
    grid_cap = min(cols * rows, MAX_STORYBOARD_TILES)
    target = max(1, round(wlen * float(fps))) if fps else grid_cap
    tiles = min(target, grid_cap)
    gc = min(cols, tiles)
    gr = min(4, max(1, math.ceil(tiles / gc)))
    step = wlen / tiles

    out_path = out_dir / f"storyboard_{path.stem[:24]}_{gc}x{gr}_{int(round(start))}-{int(round(end))}s.jpg"

    # Sample with `select`, NOT `fps=1/step`. `fps` RE-STAMPS the frames it emits onto a nominal
    # output timeline (frame i gets pts i*step) while actually keeping the frame from the
    # *middle* of each interval — so a drawtext placed after it burned a time half a sampling
    # interval earlier than the picture. `select` passes frames through with their source PTS
    # intact, which makes the burned label correct by construction.
    sample = f"select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,{step:.4f})"
    # showinfo reports the PTS of every frame that survives `select`, so the tile index below is
    # built from the frames actually in the image rather than from an assumed sample time.
    probe_chain = f"{sample},scale=480:-1,showinfo"
    # Pass fontfile= explicitly: on lean containers fontconfig can't resolve a default family, so
    # drawtext fails to init and the timestamps get silently dropped. find_font_file() also looks in
    # ~/.local/share/fonts, where headless harnesses unpack their font bundle.
    _font_file = find_font_file()
    _fontarg = f"fontfile='{_font_file}':" if _font_file else ""
    # Placed after scale=480 so fontsize is relative to the final tile, and after select so
    # `%{pts:hms}` reads the untouched source timestamp.
    label = (
        "drawtext=" + _fontarg + "text='%{pts\\:hms}':x=6:y=6:fontsize=22:"
        "fontcolor=white:box=1:boxcolor=black@0.45:boxborderw=4"
    )
    tile_part = f"tile={gc}x{gr}:padding={TILE_PADDING}:margin={TILE_MARGIN}"
    # -ss/-copyts/-t before -i: seek to the window and keep ABSOLUTE timestamps for the burned labels.
    seek = ["-ss", f"{start:.3f}", "-copyts", "-t", f"{wlen:.3f}"]

    def _render(chain: str) -> list[float]:
        """Render `chain` to out_path; return the source PTS of the frames it kept."""
        proc = _run(
            [
                ffmpeg_path(),
                # showinfo logs at info level, so `-loglevel error` would hide the very
                # timestamps we need to report.
                "-hide_banner",
                "-loglevel",
                "info",
                *seek,
                "-i",
                str(path),
                "-vf",
                chain,
                "-frames:v",
                "1",
                "-q:v",
                "3",
                "-y",
                str(out_path),
            ],
            timeout=180,
        )
        return parse_pts_times(proc.stderr)

    timestamps_burned = False
    timestamps_method = ""
    ts_error = ""
    sample_times: list[float] = []
    # Probe rather than learn-by-failure: FFmpeg >=7.0 only builds drawtext against libharfbuzz,
    # and the common static builds omit it, which would cost a doomed render on every single call.
    if has_filter("drawtext"):
        try:
            sample_times = _render(f"{probe_chain},{label},{tile_part}")
            timestamps_burned, timestamps_method = True, "drawtext"
        except MediaOpsError as e:
            ts_error = str(e)
    else:
        ts_error = "this ffmpeg build registers no `drawtext` filter (FFmpeg >=7.0 only builds it against libharfbuzz)"

    if not timestamps_burned:
        sample_times = _render(f"{probe_chain},{tile_part}")
        if not out_path.is_file():
            raise MediaOpsError("Storyboard image was not produced.")
        pil_error = _burn_labels_pil(out_path, gc, gr, sample_times)
        if pil_error:
            ts_error = f"{ts_error}; PIL fallback also failed: {pil_error}"
        else:
            timestamps_burned, timestamps_method = True, "pil"

    if not out_path.is_file():
        raise MediaOpsError("Storyboard image was not produced.")

    # Fall back to the nominal grid only if showinfo told us nothing (very old ffmpeg): better a
    # coarse index than none, but the real frame times are preferred wherever available.
    if not sample_times:
        sample_times = [start + i * step for i in range(tiles)]
    sample_times = sample_times[: gc * gr]

    tile_index = [
        {
            "index": i,
            "row": i // gc,
            "col": i % gc,
            "seconds": round(t, 2),
            "timecode": format_mmss(t),
        }
        for i, t in enumerate(sample_times)
    ]
    result = {
        "path": str(out_path),
        "cols": gc,
        "rows": gr,
        "window_start_sec": round(start, 2),
        "window_end_sec": round(end, 2),
        "seconds_per_tile": round(step, 2),
        "effective_fps": round(1.0 / step, 3) if step > 0 else None,
        "duration_sec": meta.duration_sec,
        "timestamps_burned": timestamps_burned,
        "timestamps_method": timestamps_method or "none",
        "tiles": tile_index,
    }
    if not timestamps_burned:
        result["timestamps_warning"] = (
            "Timestamps were NOT burned into the tiles. Don't rely on visible timecodes in the "
            "image; use the `tiles[].timecode` values below. Detail: " + (ts_error or "")[-300:]
        )
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
