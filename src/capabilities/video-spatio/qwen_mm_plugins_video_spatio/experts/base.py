"""Base classes for GPU and CPU tools.

GPU tools communicate with the spatial perception GPU server via HTTP JSON API
(replacing SpatialClaw's pickle-based protocol). Server addresses are configured
via environment variables.
"""

import base64
import io
import logging
import os
import time
from typing import Set

import requests

logger = logging.getLogger(__name__)


def ensure_image_list(images) -> list:
    if isinstance(images, (list, tuple)):
        return images
    from PIL import Image

    from qwen_mm_plugins_video_spatio.frame_image import FrameImage

    if isinstance(images, (Image.Image, FrameImage)):
        return [images]
    raise TypeError(f"Expected an image or list of images, got {type(images).__name__}.")


def image_to_base64(img) -> str:
    """Encode a PIL Image to base64 JPEG string."""
    from PIL import Image

    if not isinstance(img, Image.Image):
        img = img.convert("RGB") if hasattr(img, "convert") else Image.fromarray(img)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def base64_to_numpy(obj: dict):
    """Deserialize a base64-encoded numpy array from GPU server response."""
    import numpy as np

    buf = io.BytesIO(base64.b64decode(obj["data"]))
    return np.load(buf)


_HTTP_TIMEOUT = 450


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


class GPUTool:
    """Base class for tools that communicate with the GPU server via JSON HTTP.

    Server addresses are configured via environment variables. Each subclass
    specifies its ENV_VAR_NAME and API_ENDPOINT.
    """

    TOOL_PROMPT_DESCRIPTION: str = ""
    ENV_VAR_NAME: str = ""
    API_ENDPOINT: str = ""

    def __init__(self, base_url: str = "", max_retries: int = 3):
        self._base_url = base_url
        self._max_retries = max(max_retries, 1)
        self._tracer = None  # Optional[ToolTracer], injected by ToolsModule

    def _get_base_url(self) -> str:
        url = (
            self._base_url
            or os.getenv(self.ENV_VAR_NAME, "")
            or os.getenv("GPU_SERVER_BASE_URL", "")
            or "http://localhost:8001"
        )
        return url.rstrip("/")

    @classmethod
    def get_prompt_description(cls, ablations: dict = None) -> str:
        return _resolve_tool_prompt(cls, ablations)

    def _call_api(self, endpoint: str, payload: dict) -> dict:
        """Send a JSON HTTP POST to the GPU server and return the response.

        Retries with exponential backoff on transient failures.
        """
        base_url = self._get_base_url()

        for attempt in range(self._max_retries):
            try:
                resp = requests.post(
                    f"{base_url}{endpoint}",
                    json=payload,
                    timeout=_HTTP_TIMEOUT,
                )
                resp.raise_for_status()
                return resp.json()

            except Exception as exc:
                if _is_application_error(exc):
                    raise

                if attempt < self._max_retries - 1:
                    backoff = 5 * (2**attempt)
                    logger.warning(
                        "[GPUTool] %s attempt %d/%d failed: %s. Retrying in %ds...",
                        self.__class__.__name__,
                        attempt + 1,
                        self._max_retries,
                        exc,
                        backoff,
                    )
                    time.sleep(backoff)

        raise RuntimeError(
            f"{self.__class__.__name__} is temporarily unavailable. "
            f"The GPU server at {base_url} may be restarting. Please try again."
        )

    def _traced_call(self, method_name: str, endpoint: str, payload: dict, args_summary: str = "") -> dict:
        """Wrapper around _call_api that records a ToolTrace when a tracer is attached."""
        if self._tracer is None:
            return self._call_api(endpoint, payload)

        from qwen_mm_plugins_video_spatio.tool_trace import ToolTrace

        t0 = time.time()
        error_type = None
        success = True
        result_summary = ""
        try:
            result = self._call_api(endpoint, payload)
            result_summary = self._summarize_result(result)
            return result
        except Exception as exc:
            success = False
            error_type = type(exc).__name__
            result_summary = str(exc)[:200]
            raise
        finally:
            self._tracer.record(
                ToolTrace(
                    step=self._tracer._current_step,
                    tool=self.__class__.__name__,
                    method=method_name,
                    args_summary=args_summary[:200],
                    result_summary=result_summary[:300],
                    duration_ms=(time.time() - t0) * 1000,
                    success=success,
                    error_type=error_type,
                )
            )

    def _summarize_result(self, result: dict) -> str:
        """Override in subclasses for meaningful result summaries."""
        return f"{len(result)} keys"


def _is_application_error(exc: Exception) -> bool:
    if isinstance(exc, (AssertionError, ValueError, TypeError)):
        return True
    return False


class CPUTool:
    """Base class for CPU-only tools that run directly in-process."""

    TOOL_PROMPT_DESCRIPTION: str = ""

    @classmethod
    def get_prompt_description(cls, ablations: dict = None) -> str:
        return _resolve_tool_prompt(cls, ablations)
