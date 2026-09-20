"""Capability-local Omni streaming with one retry layer and cancellable deadlines."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
import time
from typing import Any

from shared.api_omni import OMNI_MAX_B64_BYTES, inline_b64_item_bytes
from shared.api_openai import expand_video_frames
from shared.dashscope_upload import OSS_RESOLVE_HEADER, contains_temporary_oss_url

from .config import PipelineConfig

_ATTEMPTS = 2
_REQUEST_TIMEOUT_SECONDS = 65.0
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class OmniCallError(RuntimeError):
    """Safe request failure; partial_text is available for local content recovery."""

    def __init__(
        self,
        message: str,
        *,
        partial_text: str = "",
        reason: str = "request_failed",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.partial_text = partial_text
        self.reason = reason
        self.status_code = status_code


class _EmptyCompletionError(RuntimeError):
    pass


def _usage_dict(usage: Any) -> dict[str, int]:
    """Retain token counts, never provider response bodies or arbitrary extra fields."""
    result = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            result[name] = value
    return result


async def _close(resource: Any) -> None:
    if resource is not None:
        with contextlib.suppress(Exception):
            await resource.close()


def _error_kind(exc: Exception, openai: Any, httpx: Any) -> tuple[str, int | None, bool]:
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = None
    if status is not None:
        return "http_error", status, status in _RETRYABLE_STATUS
    if isinstance(exc, _EmptyCompletionError):
        return "empty_response", None, True
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, openai.APITimeoutError, httpx.TimeoutException)):
        return "timeout", None, True
    if isinstance(exc, (openai.APIConnectionError, httpx.TransportError)):
        return "connection_error", None, True
    return "request_failed", None, False


async def _call_omni_text_async(
    config: PipelineConfig,
    *,
    messages: list[dict[str, Any]],
    stage: str,
    max_tokens: int,
    temperature: float,
) -> str:
    import httpx
    import openai

    prepared = expand_video_frames(messages)
    for attempt in range(1, _ATTEMPTS + 1):
        started = time.monotonic()
        remaining = config.deadline - started
        if remaining <= 0:
            config.api_calls.append(
                {"stage": stage, "attempt": 0, "elapsed_seconds": 0.0, "status": "budget_exhausted"}
            )
            raise OmniCallError(f"{stage}: model request time budget exhausted", reason="budget_exhausted")
        timeout_seconds = min(_REQUEST_TIMEOUT_SECONDS, remaining)
        parts: list[str] = []
        usage = None

        async def consume() -> None:
            nonlocal usage
            client = openai.AsyncOpenAI(
                base_url=config._base_url,
                api_key=config._api_key,
                max_retries=0,
                timeout=httpx.Timeout(
                    timeout_seconds,
                    connect=min(10.0, timeout_seconds),
                    write=min(20.0, timeout_seconds),
                    pool=min(5.0, timeout_seconds),
                ),
            )
            stream = None
            try:
                options: dict[str, Any] = {}
                if contains_temporary_oss_url(prepared):
                    options["extra_headers"] = dict(OSS_RESOLVE_HEADER)
                stream = await client.chat.completions.create(
                    model=config.omni_model,
                    messages=prepared,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                    stream_options={"include_usage": True},
                    extra_body={"modalities": ["text"]},
                    **options,
                )
                async for chunk in stream:
                    if getattr(chunk, "usage", None) is not None:
                        usage = chunk.usage
                    for choice in getattr(chunk, "choices", None) or []:
                        delta = getattr(choice, "delta", None)
                        content = getattr(delta, "content", None) if delta else None
                        if isinstance(content, str) and content:
                            parts.append(content)
                if not "".join(parts).strip():
                    raise _EmptyCompletionError()
            finally:
                try:
                    await _close(stream)
                finally:
                    await _close(client)

        error: Exception | None = None
        try:
            # A socket read timeout resets after each byte. wait_for instead cancels the whole
            # request at an absolute wall-clock limit, including slow/incomplete SSE frames.
            await asyncio.wait_for(consume(), timeout=timeout_seconds)
        except Exception as exc:  # noqa: BLE001 — report categories, never response bodies or credentials
            error = exc

        partial = "".join(parts)
        metrics: dict[str, Any] = {
            "stage": stage,
            "model": config.omni_model,
            "attempt": attempt,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "status": "success",
            "text_characters": len(partial),
        }
        counts = _usage_dict(usage)
        if counts:
            metrics["usage"] = counts
        if error is None:
            config.api_calls.append(metrics)
            return partial

        kind, status, transient = _error_kind(error, openai, httpx)
        metrics["status"] = kind
        if status is not None:
            metrics["http_status"] = status
        if partial.strip():
            metrics["partial"] = True
        config.api_calls.append(metrics)
        description = f"HTTP {status}" if status is not None else kind.replace("_", " ")
        if partial.strip():
            config.warnings.append(f"{stage}: interrupted response; partial model text was preserved.")
            raise OmniCallError(
                f"{stage}: {description}; partial text is available",
                partial_text=partial,
                reason=kind,
                status_code=status,
            ) from None
        if not transient or attempt == _ATTEMPTS:
            raise OmniCallError(f"{stage}: {description}", reason=kind, status_code=status) from None
        remaining = config.deadline - time.monotonic()
        if remaining <= 1.0:
            raise OmniCallError(f"{stage}: model request time budget exhausted", reason="budget_exhausted") from None
        config.warnings.append(f"{stage}: transient {description}; retrying once within the shared time budget.")
        await asyncio.sleep(min(0.5, remaining / 4))

    raise AssertionError("unreachable")


def call_omni_text(
    config: PipelineConfig,
    *,
    messages: list[dict[str, Any]],
    stage: str,
    max_tokens: int = 4096,
    temperature: float = 0.1,
) -> str:
    """Synchronously return Omni text with at most two cancellable HTTP attempts.

    The streaming SDK's own retries are disabled. The existing synchronous MCP/CLI interface
    also works when invoked inside an event loop: only that case uses an isolated worker loop.
    """
    if any(size > OMNI_MAX_B64_BYTES for size in inline_b64_item_bytes(messages)):
        raise OmniCallError(f"{stage}: an inline media item exceeds the upload limit", reason="payload_too_large")
    if config.deadline is None:
        config.deadline = time.monotonic() + config.time_budget_seconds
    kwargs = {"messages": messages, "stage": stage, "max_tokens": max_tokens, "temperature": temperature}
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_call_omni_text_async(config, **kwargs))

    # Synchronous MCP handlers can be invoked by a running event loop. Nesting asyncio.run()
    # there is forbidden; an independent daemon worker owns its async client and cancellation.
    outcome: concurrent.futures.Future[str] = concurrent.futures.Future()
    state: dict[str, Any] = {}

    def worker() -> None:
        loop = asyncio.new_event_loop()
        state["loop"] = loop
        task = loop.create_task(_call_omni_text_async(config, **kwargs))
        state["task"] = task
        try:
            outcome.set_result(loop.run_until_complete(task))
        except BaseException as exc:
            outcome.set_exception(exc)
        finally:
            loop.close()

    thread = threading.Thread(target=worker, name="video2note-omni", daemon=True)
    thread.start()
    try:
        # Small cleanup allowance only; no unbounded thread join or executor shutdown.
        return outcome.result(timeout=max(0.0, config.deadline - time.monotonic()) + 0.25)
    except concurrent.futures.TimeoutError:
        loop, task = state.get("loop"), state.get("task")
        if loop is not None and task is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):  # the worker may finish between this check and cancellation
                loop.call_soon_threadsafe(task.cancel)
        thread.join(timeout=0.1)
        raise OmniCallError(f"{stage}: model request time budget exhausted", reason="budget_exhausted") from None
