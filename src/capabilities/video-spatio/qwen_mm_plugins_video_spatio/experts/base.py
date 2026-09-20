"""Shared helpers for the in-process spatial experts."""

import logging
from typing import Set

logger = logging.getLogger(__name__)


def ensure_image_list(images) -> list:
    if isinstance(images, (list, tuple)):
        return images
    from PIL import Image

    from qwen_mm_plugins_video_spatio.frame_image import FrameImage

    if isinstance(images, (Image.Image, FrameImage)):
        return [images]
    raise TypeError(f"Expected an image or list of images, got {type(images).__name__}.")


def _resolve_tool_prompt(cls, ablations: dict = None) -> str:
    from qwen_mm_plugins_video_spatio.prompts import resolve_section

    sections = getattr(cls, "TOOL_PROMPT_SECTIONS", None)
    if sections is None:
        return cls.TOOL_PROMPT_DESCRIPTION.strip()

    prefix = getattr(cls, "TOOL_ABLATION_PREFIX", "tool_unknown")

    if ablations and prefix in ablations.get("exclude", []):
        logger.info("[prompt ablation] EXCLUDED (whole tool): %s", prefix)
        return ""

    if ablations:
        override_path = ablations.get("override", {}).get(prefix)
        if override_path:
            logger.info("[prompt ablation] OVERRIDDEN (whole tool): %s -> %s", prefix, override_path)
            with open(override_path, "r") as f:
                return f.read().strip()

    parts = []
    for sub_name, default_content in sections.items():
        key = f"{prefix}_{sub_name}"
        resolved = resolve_section(key, default_content.strip(), ablations or {})
        if resolved:
            parts.append(resolved.strip())

    return "\n\n".join(parts)


def get_all_tool_ablation_names(*tool_classes) -> set:
    names: Set[str] = set()
    for cls in tool_classes:
        sections = getattr(cls, "TOOL_PROMPT_SECTIONS", None)
        if sections is None:
            continue
        prefix = getattr(cls, "TOOL_ABLATION_PREFIX", "tool_unknown")
        names.add(prefix)
        for sub_name in sections:
            names.add(f"{prefix}_{sub_name}")
    return names


class CPUTool:
    """Base class for CPU-only tools that run directly in-process."""

    TOOL_PROMPT_DESCRIPTION: str = ""

    @classmethod
    def get_prompt_description(cls, ablations: dict = None) -> str:
        return _resolve_tool_prompt(cls, ablations)
