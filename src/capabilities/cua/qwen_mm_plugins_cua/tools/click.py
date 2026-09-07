"""MCP tool: click one semantic element or screenshot point."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from qwen_mm_plugins_cua.mode import COORDINATE_MAX_INCLUSIVE
from qwen_mm_plugins_cua.tools._actions import DeliveredActionArgs, execute


class ClickArgs(DeliveredActionArgs):
    element_token: str | None = Field(default=None, description="Preferred exact handle from the current state.")
    x: float | None = Field(
        default=None,
        ge=0,
        le=COORDINATE_MAX_INCLUSIVE,
        description="X in the current state's coordinate_space.",
    )
    y: float | None = Field(
        default=None,
        ge=0,
        le=COORDINATE_MAX_INCLUSIVE,
        description="Y in the current state's coordinate_space.",
    )
    button: Literal["left", "right", "middle"] = Field(default="left", description="Pointer button.")
    count: Literal[1, 2] = Field(default=1, description="Single or double click.")
    semantic_action: Literal["press", "show_menu", "pick", "confirm", "cancel", "open"] | None = Field(
        default=None, description="Optional explicit AX action for an element-token click."
    )
    modifiers: list[str] | None = Field(default=None, description="Modifier keys held during a single click.")
    retry_if_unverified: bool = Field(
        default=False,
        description=(
            "Assert this single click is safe to retry, enabling background element -> background pixel -> "
            "foreground pixel escalation when verification fails."
        ),
    )
    allow_global_pointer_fallback: bool = Field(
        default=True,
        description=(
            "When safe retry is enabled, permit one final real OS-pointer click after every ordinary retry is "
            "proven unchanged. Defaults on for safe retries; set false per click to disable it."
        ),
    )

    @model_validator(mode="after")
    def validate_click(self):
        if self.element_token and (self.x is not None or self.y is not None):
            raise ValueError("use element_token or x/y, not both")
        if not self.element_token and (self.x is None or self.y is None):
            raise ValueError("click requires element_token or both x and y")
        if self.count == 2 and (
            self.button != "left" or self.semantic_action is not None or self.modifiers or self.retry_if_unverified
        ):
            raise ValueError("double click supports only an unmodified left click without automatic retry")
        if self.semantic_action is not None and self.element_token is None:
            raise ValueError("semantic_action requires element_token")
        if (
            self.retry_if_unverified
            and self.allow_global_pointer_fallback
            and (self.delivery != "auto" or self.count != 1 or self.button != "left" or self.modifiers)
        ):
            raise ValueError(
                "global pointer fallback requires delivery=auto, retry_if_unverified=true, "
                "and one unmodified left click"
            )
        return self


TOOL: dict[str, Any] = {
    "name": "click",
    "description": (
        "Click one current-snapshot element or screenshot point, then return fresh state. Prefer element_token. "
        "Coordinates fail closed unless the bound PNG frame is proven. A final real OS-pointer fallback is "
        "enabled by default only after the caller explicitly marks a click safe to retry."
    ),
    "args": ClickArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return execute("click", arguments)
