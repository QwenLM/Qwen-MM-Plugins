"""MCP tool modules. Each module exports TOOL and handle; discovered at startup by build_registry.

MemoryRef lives here rather than in a helper module because every tool but watch_and_answer starts
from it: the two fields are how a memory is addressed, and they mirror service.memory_dir's rules.
"""

from __future__ import annotations

from pydantic import BaseModel


class MemoryRef(BaseModel):
    """Every tool locates a memory by video path, optionally with a namespace."""

    video_path: str | None = None
    namespace: str | None = None
