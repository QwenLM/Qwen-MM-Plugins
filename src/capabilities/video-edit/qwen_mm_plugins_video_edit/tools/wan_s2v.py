"""MCP tool: digital human lip-sync video via Wan2.2-S2V."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel

from shared.api_dashscope import API_V1, poll_dashscope_task, retry_call, submit_dashscope_async
from shared.content import text_error
from shared.env import get_env


class WanS2vArgs(BaseModel):
    action: Literal["detect", "generate"]
    image_url: str
    audio_url: Optional[str] = None
    resolution: Literal["480P", "720P"] = "480P"
    poll_interval: int = 15
    poll_timeout: int = 700
    output_dir: Optional[str] = None


TOOL = {"name": "wan_s2v", "args": WanS2vArgs}


def _detect(image_url: str, api_key: str) -> list[dict[str, Any]]:
    import requests

    resp = retry_call(
        requests.post,
        f"{API_V1}/services/aigc/image2video/face-detect",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "wan2.2-s2v-detect",
            "input": {"image_url": image_url},
        },
        timeout=30,
    )
    result = resp.json()

    root_code = result.get("code")
    if not 200 <= resp.status_code < 300 or root_code:
        code = root_code or f"HTTP {resp.status_code}"
        message = result.get("message", "request failed")
        request_id = result.get("request_id") or result.get("requestId")
        suffix = f" (request_id: {request_id})" if request_id else ""
        return text_error(f"detection failed — [{code}] {message}{suffix}")

    output = result.get("output", {})
    check_pass = output.get("check_pass", False)
    humanoid = output.get("humanoid", False)

    lines = [
        f"**Detection result**: {'PASS' if check_pass else 'FAIL'}",
        f"**Human detected**: {humanoid}",
    ]
    if not check_pass:
        code = output.get("code", "")
        message = output.get("message", "")
        if code or message:
            lines.append(f"**Reason**: [{code}] {message}")
        lines.append("")
        lines.append("Common failure reasons: multiple people, side face, blurry, occluded, unsupported style.")
    else:
        lines.append("")
        lines.append("Image is suitable for digital human generation. You can proceed with 'generate' action.")

    return [{"type": "text", "text": "\n".join(lines)}]


def _generate(arguments: dict[str, Any], api_key: str) -> list[dict[str, Any]]:
    image_url = arguments["image_url"]
    audio_url = arguments.get("audio_url")
    resolution = arguments.get("resolution", "480P")
    poll_interval = max(5, arguments.get("poll_interval", 15))
    poll_timeout = arguments.get("poll_timeout", 700)
    output_dir = arguments.get("output_dir")

    if not audio_url:
        return text_error("audio_url is required for 'generate' action")

    task_id, result = submit_dashscope_async(
        "services/aigc/image2video/video-synthesis/",
        {
            "model": "wan2.2-s2v",
            "input": {"image_url": image_url, "audio_url": audio_url},
            "parameters": {"resolution": resolution},
        },
        api_key,
    )
    if not task_id:
        return text_error(f"failed to create task: {json.dumps(result, ensure_ascii=False)}")

    poll_resp = poll_dashscope_task(task_id, api_key, base_url=API_V1, interval=poll_interval, timeout=poll_timeout)
    status = poll_resp.get("output", {}).get("task_status")

    if status == "SUCCEEDED":
        video_url = poll_resp["output"].get("results", {}).get("video_url")
        if not video_url:
            video_url = poll_resp["output"].get("video_url")
        usage = poll_resp.get("usage", {})

        lines = [
            "**Status**: SUCCEEDED",
            f"**Video URL**: {video_url}",
            f"**Resolution**: {resolution}",
            f"**Task ID**: {task_id}",
        ]
        if usage.get("duration"):
            lines.append(f"**Video duration**: {usage['duration']}s")
        if usage.get("size"):
            lines.append(f"**Video size**: {usage['size']}")

        if output_dir and video_url:
            from shared.api_dashscope import save_url_to_dir

            video_file = Path(output_dir).expanduser().resolve() / f"s2v_{task_id}.mp4"
            save_url_to_dir(video_url, str(video_file), timeout=300)
            lines.append(f"**Saved to**: {video_file}")

        return [{"type": "text", "text": "\n".join(lines)}]

    if status == "FAILED":
        msg = poll_resp.get("output", {}).get("message", "unknown error")
        return text_error(f"task failed — {msg}\nTask ID: {task_id}")

    return text_error(f"task timed out after {poll_timeout}s. Task ID: {task_id} (may still be running)")


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Digital human lip-sync video generation using Wan2.2-S2V. Takes a portrait image + audio and
    generates a talking-head video with lip sync. Two actions: (1) detect — check if an image is
    suitable for digital human generation (sync and billed per successful request, regardless of
    detection result); (2) generate — submit image + audio to create lip-sync video (async, billed).
    Always run detect first before generate. Supports real humans (portrait/half-body/full-body) and
    cartoon characters. Audio max 20s, image must be single person, front-facing, clear.

    Args:
        action: Action to perform. detect: check if image meets requirements (sync and billed per
            successful request; always run first). generate: create lip-sync video from image +
            audio (async, billed, 5-10 min).
        image_url: Public URL of the portrait image. Requirements: single person, front-facing,
            clear, JPG/JPEG/PNG/BMP/WEBP, resolution 400-7000px per side. Supports: portrait, half-
            body, full-body, cartoon characters.
        audio_url: Public URL of the audio file. Required for 'generate' action only. Requirements:
            WAV or MP3 format, < 15MB, < 20 seconds, must contain clear human voice, remove
            background noise/music.
        resolution: Output video resolution. Options: '480P' (faster, cheaper) or '720P'. Default:
            '480P'.
        poll_interval: Polling interval in seconds for async generation task. Min 5, recommended 15.
            Default: 15.
        poll_timeout: Max time in seconds to wait for generation to complete. Typical generation
            time: 5-10 minutes. Default: 700.
        output_dir: Directory to save the generated video. If provided, downloads video to this
            directory. If omitted, returns the video URL only (valid 24h).
    """
    action = arguments.get("action")
    image_url = arguments.get("image_url")

    if not action:
        return text_error("action is required (detect or generate)")
    if not image_url:
        return text_error("image_url is required")

    api_key = get_env("DASHSCOPE_API_KEY")
    if not api_key:
        return text_error("DASHSCOPE_API_KEY not set")

    try:
        if action == "detect":
            return _detect(image_url, api_key)
        elif action == "generate":
            return _generate(arguments, api_key)
        else:
            return text_error(f"unknown action '{action}'. Use: detect, generate")
    except Exception as e:
        return text_error(f"{e}")
