"""Deterministically verify bounded predicates against an exact window."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from qwen_mm_plugins_cua.browser_tools._shared import call_driver_tool
from qwen_mm_plugins_cua.driver import DEFAULT_SESSION
from qwen_mm_plugins_cua.tools._actions import StrictArgs


class ElementSelector(StrictArgs):
    label_contains: str | None = Field(default=None, min_length=1)
    role: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_selector(self):
        if not self.label_contains and not self.role:
            raise ValueError("selector requires label_contains or role")
        return self


class ElementPredicate(StrictArgs):
    selector: ElementSelector
    exists: Literal[True] | None = None
    enabled: bool | None = None
    selected: bool | None = None
    value_equals: str | None = None


class WindowBounds(StrictArgs):
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    tolerance_px: float = Field(default=0, ge=0, le=100)


class WindowPredicate(StrictArgs):
    exists: bool | None = None
    bounds: WindowBounds | None = None


class StatePredicate(StrictArgs):
    element: ElementPredicate | None = None
    window: WindowPredicate | None = None

    @model_validator(mode="after")
    def validate_predicate(self):
        if self.element is None and self.window is None:
            raise ValueError("predicate requires element or window")
        return self


class VerifyStateArgs(StrictArgs):
    pid: int = Field(gt=0, description="Exact target process id.")
    window_id: int = Field(gt=0, description="Exact native window id.")
    expect: list[StatePredicate] = Field(min_length=1, max_length=8)
    timeout_ms: int = Field(default=5000, ge=0, le=10000)
    stable_samples: int = Field(default=2, ge=1, le=5)
    include_screenshot: bool = False
    session: str = DEFAULT_SESSION


TOOL: dict[str, Any] = {
    "name": "verify_state",
    "description": "Deterministically verify one to eight bounded predicates against an exact native window.",
    "args": VerifyStateArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return call_driver_tool("verify_state", arguments, timeout=15)
