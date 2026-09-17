"""MCP tool: video generation and editing via HappyHorse."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel

from shared.api_dashscope import API_V1, submit_dashscope_async
from shared.content import text_error
from shared.env import get_env


class HappyhorseArgs(BaseModel):
    mode: Literal["text_to_video", "image_to_video", "reference_to_video", "video_edit"]
    prompt: Optional[str] = None
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    reference_image_urls: Optional[list[str]] = None
    resolution: Literal["720P", "1080P"] = "1080P"
    ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4", "4:5", "5:4", "21:9", "9:21"] = "16:9"
    duration: int = 5
    audio_setting: Literal["auto", "origin"] = "auto"
    seed: Optional[int] = None
    watermark: bool = False
    poll_interval: int = 15
    poll_timeout: int = 600
    output_dir: Optional[str] = None


TOOL = {"name": "happyhorse", "args": HappyhorseArgs}


_VIDEO_SYNTH_PATH = "services/aigc/video-generation/video-synthesis"


def _submit_t2v(arguments: dict[str, Any], api_key: str) -> tuple[str | None, dict]:
    parameters: dict[str, Any] = {
        "resolution": arguments.get("resolution", "1080P"),
        "ratio": arguments.get("ratio", "16:9"),
        "duration": int(arguments.get("duration", 5)),
        "watermark": arguments.get("watermark", False),
    }
    if arguments.get("seed") is not None:
        parameters["seed"] = arguments["seed"]
    payload = {"model": "happyhorse-1.0-t2v", "input": {"prompt": arguments["prompt"]}, "parameters": parameters}
    return submit_dashscope_async(_VIDEO_SYNTH_PATH, payload, api_key)


def _submit_video_edit(arguments: dict[str, Any], api_key: str) -> tuple[str | None, dict]:
    video_url = arguments.get("video_url")
    if not video_url:
        return None, {"error": "video_url is required for video_edit mode"}

    media = [{"type": "video", "url": video_url}]
    for img_url in (arguments.get("reference_image_urls") or [])[:5]:
        media.append({"type": "reference_image", "url": img_url})

    parameters: dict[str, Any] = {
        "resolution": arguments.get("resolution", "1080P"),
        "watermark": arguments.get("watermark", False),
        "audio_setting": arguments.get("audio_setting", "auto"),
    }
    if arguments.get("seed") is not None:
        parameters["seed"] = arguments["seed"]

    payload = {
        "model": "happyhorse-1.0-video-edit",
        "input": {"prompt": arguments["prompt"], "media": media},
        "parameters": parameters,
    }
    return submit_dashscope_async(_VIDEO_SYNTH_PATH, payload, api_key)


def _submit_i2v(arguments: dict[str, Any], api_key: str) -> tuple[str | None, dict]:
    image_url = arguments.get("image_url")
    if not image_url:
        return None, {"error": "image_url is required for image_to_video mode"}

    input_obj: dict[str, Any] = {"media": [{"type": "first_frame", "url": image_url}]}
    if arguments.get("prompt"):
        input_obj["prompt"] = arguments["prompt"]

    parameters: dict[str, Any] = {
        "resolution": arguments.get("resolution", "1080P"),
        "duration": int(arguments.get("duration", 5)),
        "watermark": arguments.get("watermark", False),
    }
    if arguments.get("seed") is not None:
        parameters["seed"] = arguments["seed"]

    payload = {"model": "happyhorse-1.0-i2v", "input": input_obj, "parameters": parameters}
    return submit_dashscope_async(_VIDEO_SYNTH_PATH, payload, api_key)


def _submit_r2v(arguments: dict[str, Any], api_key: str) -> tuple[str | None, dict]:
    ref_urls = arguments.get("reference_image_urls") or []
    if not ref_urls:
        return None, {"error": "reference_image_urls (1-9 images) is required for reference_to_video mode"}

    media = [{"type": "reference_image", "url": url} for url in ref_urls[:9]]

    parameters: dict[str, Any] = {
        "resolution": arguments.get("resolution", "1080P"),
        "ratio": arguments.get("ratio", "16:9"),
        "duration": int(arguments.get("duration", 5)),
        "watermark": arguments.get("watermark", False),
    }
    if arguments.get("seed") is not None:
        parameters["seed"] = arguments["seed"]

    payload = {
        "model": "happyhorse-1.0-r2v",
        "input": {"prompt": arguments.get("prompt", ""), "media": media},
        "parameters": parameters,
    }
    return submit_dashscope_async(_VIDEO_SYNTH_PATH, payload, api_key)


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Video generation and editing using HappyHorse models. Four modes: (1) text_to_video — generate
    video from text prompt (happyhorse-1.0-t2v); (2) image_to_video — animate a single image into
    video (happyhorse-1.0-i2v); (3) reference_to_video — generate video with 1-9 reference images
    fused as characters/objects (happyhorse-1.0-r2v, use [Image 1] etc. in prompt to reference
    them); (4) video_edit — edit an existing video with text instruction + optional reference images
    (happyhorse-1.0-video-edit). All modes are async — submit task then poll until completion (1-5
    min typical).

    Args:
        mode: Operation mode. text_to_video: generate video from text description. image_to_video:
            animate a single input image (first frame) into video. reference_to_video: generate
            video with 1-9 reference images fused into the scene (use [Image 1], [Image 2] etc. in
            prompt to reference each image). video_edit: modify an existing video based on text
            instruction, optionally guided by reference images.
        prompt: Text prompt (up to 2500 Chinese chars / 5000 non-Chinese). Required for all modes
            EXCEPT image_to_video (optional there). For reference_to_video: use [Image 1], [Image 2]
            etc. to reference images and describe what each depicts, e.g. 'the woman from [Image 1]
            runs on the beach'.
        image_url: Input image URL or base64 data for image_to_video mode. Required for
            image_to_video. This image becomes the first frame of the generated video. Requirements:
            JPEG/JPG/PNG/WEBP, width & height ≥300px, aspect ratio 1:2.5~2.5:1, ≤20MB. Output video
            aspect ratio follows the input image (ratio param is ignored).
        video_url: Input video URL for video_edit mode. Required for video_edit. Requirements:
            MP4/MOV (H.264), 3-60s duration (>15s auto-truncated to first 15s), long edge ≤4096px,
            short edge ≥360px, aspect ratio 1:2.5~2.5:1, ≤100MB, >8fps.
        reference_image_urls: Reference images for reference_to_video (1-9, required) or video_edit
            (0-5, optional). For reference_to_video: each image provides a character/object to fuse
            into the video. Prompt must use [Image 1], [Image 2] etc. to reference them by array
            order. For video_edit: style guidance or element replacement reference. Requirements:
            JPEG/JPG/PNG/WEBP, ≤20MB each. r2v: short edge ≥400px, recommend ≥720P. video_edit:
            width/height ≥300px.
        resolution: Output video resolution. Default: '1080P'.
        ratio: Aspect ratio for text_to_video and reference_to_video modes. Ignored for
            image_to_video (follows input image) and video_edit. Default: '16:9'.
        duration: Video duration in seconds for text_to_video, image_to_video, and
            reference_to_video modes. Range: 3-15. Default: 5. For video_edit: duration follows
            input video (≤15s).
        audio_setting: Audio control for video_edit mode only. 'auto': model generates/modifies
            audio. 'origin': preserve original audio from input video. Default: 'auto'.
        seed: Random seed [0, 2147483647] for reproducibility.
        watermark: Whether to add watermark. Default: false.
        poll_interval: Polling interval in seconds. Default: 15.
        poll_timeout: Max wait time in seconds. Default: 600.
        output_dir: Directory to save generated video. If provided, downloads video to this
            directory. If omitted, returns URL only (valid 24h).
    """
    mode = arguments.get("mode")
    prompt = arguments.get("prompt", "")

    if not mode:
        return text_error("mode is required (text_to_video, image_to_video, reference_to_video, video_edit)")
    if not prompt and mode != "image_to_video":
        return text_error("prompt is required for this mode")

    api_key = get_env("DASHSCOPE_API_KEY")
    if not api_key:
        return text_error("DASHSCOPE_API_KEY not set")

    try:
        if mode == "text_to_video":
            task_id, submit_result = _submit_t2v(arguments, api_key)
        elif mode == "image_to_video":
            task_id, submit_result = _submit_i2v(arguments, api_key)
        elif mode == "reference_to_video":
            task_id, submit_result = _submit_r2v(arguments, api_key)
        elif mode == "video_edit":
            task_id, submit_result = _submit_video_edit(arguments, api_key)
        else:
            return text_error(
                f"unknown mode '{mode}'. Use: text_to_video, image_to_video, reference_to_video, video_edit"
            )

        if not task_id:
            return text_error(f"failed to create task: {json.dumps(submit_result, ensure_ascii=False)}")

        poll_interval = max(5, arguments.get("poll_interval", 15))
        poll_timeout = arguments.get("poll_timeout", 600)

        from shared.api_dashscope import poll_dashscope_task

        result = poll_dashscope_task(task_id, api_key, base_url=API_V1, interval=poll_interval, timeout=poll_timeout)
        status = result.get("output", {}).get("task_status")

        if status == "SUCCEEDED":
            video_url = result["output"].get("video_url")
            usage = result.get("usage", {})

            model_suffix = {
                "text_to_video": "t2v",
                "image_to_video": "i2v",
                "reference_to_video": "r2v",
                "video_edit": "video-edit",
            }[mode]
            lines = [
                f"**Mode**: {mode}",
                f"**Model**: happyhorse-1.0-{model_suffix}",
                "**Status**: SUCCEEDED",
                f"**Video URL**: {video_url}",
                f"**Task ID**: {task_id}",
            ]
            if usage.get("output_video_duration"):
                lines.append(f"**Output duration**: {usage['output_video_duration']}s")
            if usage.get("SR"):
                lines.append(f"**Resolution**: {usage['SR']}P")
            if usage.get("ratio"):
                lines.append(f"**Ratio**: {usage['ratio']}")

            output_dir = arguments.get("output_dir")
            if output_dir and video_url:
                from shared.api_dashscope import save_url_to_dir

                video_file = Path(output_dir).expanduser().resolve() / f"happyhorse_{task_id}.mp4"
                save_url_to_dir(video_url, str(video_file), timeout=300)
                lines.append(f"**Saved to**: {video_file}")

            return [{"type": "text", "text": "\n".join(lines)}]

        elif status == "FAILED":
            msg = result.get("output", {}).get("message", "unknown error")
            return text_error(f"task failed — {msg}\nTask ID: {task_id}")

        else:
            return text_error(
                f"task timed out after {poll_timeout}s. Task ID: {task_id} (may still be running — query manually)"
            )

    except Exception as e:
        return text_error(f"{e}")
