"""Shared strict schemas for the primary-display native profile."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from qwen_mm_plugins_cua.mode import COORDINATE_MAX_INCLUSIVE
from qwen_mm_plugins_cua.tools._actions import StrictArgs


class VisualExpectation(StrictArgs):
    condition: Literal["state_changed"] = Field(
        description="Verify that the fresh primary-display screenshot changed materially."
    )


class SnapshotArgs(StrictArgs):
    snapshot_id: str = Field(
        pattern=r"^s[0-9a-f]{8}$",
        description="Latest primary-display snapshot id. Re-observe after every action.",
    )
    expect: VisualExpectation | None = Field(default=None, description="Optional visual-change verification.")


class PointArgs(SnapshotArgs):
    x: float = Field(
        ge=0,
        le=COORDINATE_MAX_INCLUSIVE,
        description="X in the coordinate_space returned by get_desktop_state.",
    )
    y: float = Field(
        ge=0,
        le=COORDINATE_MAX_INCLUSIVE,
        description="Y in the coordinate_space returned by get_desktop_state.",
    )
