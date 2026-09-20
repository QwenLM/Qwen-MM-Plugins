"""MCP tool: video generation via Wan (wan2.7-t2v / wan2.7-i2v)."""

from __future__ import annotations

import hashlib
from http import HTTPStatus
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel

from shared.content import text_error
from shared.env import get_env


class WanT2vArgs(BaseModel):
    mode: Literal["text_to_video", "first_frame", "first_last_frame"] = "text_to_video"
    prompt: str
    first_frame_url: Optional[str] = None
    last_frame_url: Optional[str] = None
    size: str = "1280*720"
    duration: int = 5
    negative_prompt: str = ""
    prompt_extend: bool = True
    seed: int = 12345
    output_dir: Optional[str] = None


TOOL = {"name": "wan_t2v", "args": WanT2vArgs}


# Keep the public MCP `size` argument stable while translating it to Wan 2.7's
# resolution/ratio request fields. The third tuple item is the T2V output size.
_WAN27_SIZE_PRESETS = {
    "1280*720": ("720P", "16:9", "1280*720"),
    "720*1280": ("720P", "9:16", "720*1280"),
    "960*960": ("720P", "1:1", "960*960"),
    "1024*1024": ("720P", "1:1", "960*960"),
    "1920*1080": ("1080P", "16:9", "1920*1080"),
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Video generation using Wan models (wan2.7 series). Supports multiple generation modes: (1)
    text_to_video — generate video from text prompt only (wan2.7-t2v); (2) first_frame — generate
    video from a first-frame image + text prompt (wan2.7-i2v); (3) first_last_frame — generate video
    from first-frame + last-frame images + text prompt (wan2.7-i2v). Synchronous call via DashScope
    SDK — blocks until video is ready. Typical generation time: 30s-3min depending on duration and
    resolution.

    Args:
        mode: Generation mode. text_to_video: pure text prompt → video (wan2.7-t2v). first_frame:
            image + text prompt → video starting from that image (wan2.7-i2v). first_last_frame: two
            images + text prompt → video interpolating between them (wan2.7-i2v).
        prompt: Video description prompt. Use cinematic language: camera movements (pan, zoom,
            tracking), temporal dynamics (gradually, suddenly, slowly), style keywords (cinematic,
            documentary, vintage). Avoid: multi-person interactions, complex gestures, text content.
            Required for all modes.
        first_frame_url: URL of the first frame image. Required for first_frame and first_last_frame
            modes. Must be a publicly accessible HTTP/HTTPS URL.
        last_frame_url: URL of the last frame image. Required for first_last_frame mode only. Must
            be a publicly accessible HTTP/HTTPS URL.
        size: Video size preset. For text_to_video this selects the output size; for image-to-video
            it selects only the resolution tier, while aspect ratio and exact dimensions follow the
            first frame. Options: '1280*720' (16:9 landscape), '720*1280' (9:16 portrait), '960*960'
            (square), and '1920*1080' (full HD). The legacy '1024*1024' value is accepted and maps
            to Wan 2.7's 960*960 output. Default: '1280*720'.
        duration: Video duration in seconds. Wan 2.7 accepts 2-15 seconds. Default: 5. Cost scales
            linearly with duration.
        negative_prompt: Things to avoid in the video. Example: 'blurry, low quality, distorted
            faces, watermark'. Default: empty.
        prompt_extend: Enable intelligent prompt rewriting for better video results. Default: true.
        seed: Random seed for reproducibility [0, 2147483647]. Fix seed to compare prompt
            variations. Default: 12345.
        output_dir: Directory to save the generated video. If provided, downloads video to this
            directory. If omitted, returns the video URL only (valid 24h).
    """
    prompt = arguments.get("prompt", "")
    if not prompt:
        return text_error("prompt is required")

    mode = arguments.get("mode", "text_to_video")
    first_frame_url = arguments.get("first_frame_url")
    last_frame_url = arguments.get("last_frame_url")
    size = arguments.get("size", "1280*720")
    preset = _WAN27_SIZE_PRESETS.get(size)
    if preset is None:
        supported = ", ".join(_WAN27_SIZE_PRESETS)
        return text_error(f"unsupported size '{size}'. Use one of: {supported}")
    resolution, ratio, output_size = preset
    duration = int(arguments.get("duration", 5))
    negative_prompt = arguments.get("negative_prompt", "")
    prompt_extend = arguments.get("prompt_extend", True)
    seed = arguments.get("seed", 12345)
    output_dir = arguments.get("output_dir")

    api_key = get_env("DASHSCOPE_API_KEY")
    if not api_key:
        return text_error("DASHSCOPE_API_KEY not set")

    if mode == "first_frame" and not first_frame_url:
        return text_error("first_frame_url is required for first_frame mode")
    if mode == "first_last_frame" and (not first_frame_url or not last_frame_url):
        return text_error("both first_frame_url and last_frame_url are required for first_last_frame mode")

    try:
        import dashscope
        from dashscope import VideoSynthesis
    except ImportError:
        return text_error("missing dependency. Install with: pip install dashscope")

    from shared.api_dashscope import API_V1, retry_call

    try:
        dashscope.base_http_api_url = API_V1

        if mode == "text_to_video":
            model = "wan2.7-t2v"
            rsp = retry_call(
                VideoSynthesis.call,
                api_key=api_key,
                model=model,
                prompt=prompt,
                resolution=resolution,
                ratio=ratio,
                duration=duration,
                negative_prompt=negative_prompt,
                prompt_extend=prompt_extend,
                watermark=False,
                seed=seed,
            )
        elif mode == "first_frame":
            model = "wan2.7-i2v"
            media = [{"type": "first_frame", "url": first_frame_url}]
            rsp = retry_call(
                VideoSynthesis.call,
                api_key=api_key,
                model=model,
                prompt=prompt,
                media=media,
                resolution=resolution,
                duration=duration,
                negative_prompt=negative_prompt,
                prompt_extend=prompt_extend,
                watermark=False,
                seed=seed,
            )
        elif mode == "first_last_frame":
            model = "wan2.7-i2v"
            media = [
                {"type": "first_frame", "url": first_frame_url},
                {"type": "last_frame", "url": last_frame_url},
            ]
            rsp = retry_call(
                VideoSynthesis.call,
                api_key=api_key,
                model=model,
                prompt=prompt,
                media=media,
                resolution=resolution,
                duration=duration,
                negative_prompt=negative_prompt,
                prompt_extend=prompt_extend,
                watermark=False,
                seed=seed,
            )
        else:
            return text_error(f"unknown mode '{mode}'. Use: text_to_video, first_frame, first_last_frame")

        if rsp.status_code != HTTPStatus.OK:
            return text_error(f"[{rsp.status_code}] {rsp.code} — {rsp.message}")

        task_status = getattr(rsp.output, "task_status", None)
        if task_status == "FAILED":
            code = getattr(rsp.output, "code", "unknown")
            msg = getattr(rsp.output, "message", "unknown error")
            return text_error(f"task failed — [{code}] {msg}")

        video_url = rsp.output.video_url

        lines = [
            f"**Mode**: {mode}",
            f"**Model**: {model}",
            f"**Video URL**: {video_url}",
            f"**Duration**: {duration}s",
            f"**Seed**: {seed}",
        ]
        if mode == "text_to_video":
            lines.insert(3, f"**Size**: {output_size}")
        else:
            lines.insert(3, f"**Resolution tier**: {resolution} (aspect ratio follows first frame)")
        if first_frame_url:
            lines.append(f"**First frame**: {first_frame_url}")
        if last_frame_url:
            lines.append(f"**Last frame**: {last_frame_url}")

        if output_dir:
            from shared.api_dashscope import save_url_to_dir

            out_path = Path(output_dir).expanduser().resolve()

            cache_key = hashlib.sha256(
                f"{mode}|{prompt}|{size}|{duration}|{negative_prompt}|{seed}|{first_frame_url}|{last_frame_url}".encode()
            ).hexdigest()[:16]
            video_file = out_path / f"wan_{mode}_{cache_key}.mp4"
            save_url_to_dir(video_url, str(video_file), timeout=300)
            lines.append(f"**Saved to**: {video_file}")

        return [{"type": "text", "text": "\n".join(lines)}]

    except Exception as e:
        return text_error(f"{e}")
