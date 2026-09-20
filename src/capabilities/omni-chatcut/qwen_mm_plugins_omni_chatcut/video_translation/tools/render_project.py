"""Render a validated translated dubbing project."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from shared.content import text, text_error

from ..rendering import render_project


class RenderProjectArgs(BaseModel):
    project_dir: str
    server: str | None = None
    reuse_existing: bool = True
    background_mode: Literal["include", "omit"] = "include"
    regenerate_segment_ids: list[str] = []


TOOL = {"name": "render_video_translation", "args": RenderProjectArgs}


def _format_user_summary(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    review_segments = result.get("review_segments") or []
    lines = [
        "翻译渲染完成",
        "",
        f"共 {result.get('segment_count', 0)} 段：{summary.get('pass_count', 0)} 段正常，"
        f"{summary.get('review_count', 0)} 段建议复核。",
    ]
    if review_segments:
        lines.extend(["", "建议优先复核："])
        for item in review_segments:
            slot = f"{float(item['slot_start_sec']):.2f}–{float(item['slot_end_sec']):.2f}s"
            reasons = "；".join(item.get("reasons") or ["需要人工听检"])
            lines.append(f"- {item['segment_id']}（{slot}）：{reasons}")
    else:
        lines.extend(["", "未发现自动风险标记，但仍建议完整顺听一遍。"])
    lines.extend(
        [
            "",
            f"完整摘要：{result['summary_path']}",
            f"详细诊断：{result['diagnostics_path']}",
            f"交付视频：{result['final_video']}",
        ]
    )
    return "\n".join(lines)


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Separate source audio, synthesize translated speech, fit timing, optionally mix the separated background, remux video, write measured QA, and return a user-facing summary plus segments that need manual listening review.

    Requires a validated translation plan: rendering stops if the plan fails validation or the source
    movie changed after the plan was authored.

    Args:
        project_dir: Existing project directory created by prepare_video_translation_project; it must hold
            a validated translation plan.
        server: Dubbing service base URL for this call. Leave unset to use the configured
            QWEN_MM_DUBBING_SERVER_URL.
        reuse_existing: Reuse the separated stems and per-segment speech whose signatures still match. Pass
            false to separate and synthesize again from scratch; the extracted source audio is refreshed on
            its own signature either way.
        background_mode: "include" mixes the separated background under the translated speech; "omit"
            delivers the dubbed voices only.
        regenerate_segment_ids: Segment ids to re-synthesize even when reuse_existing is true; every id must
            exist in the plan.
    """
    try:
        result = render_project(
            arguments["project_dir"],
            explicit_server=arguments.get("server"),
            reuse_existing=arguments.get("reuse_existing", True),
            background_mode=arguments.get("background_mode", "include"),
            regenerate_segment_ids=set(arguments.get("regenerate_segment_ids") or []),
        )
        return [text(_format_user_summary(result))]
    except Exception as exc:  # noqa: BLE001
        return text_error(str(exc))
