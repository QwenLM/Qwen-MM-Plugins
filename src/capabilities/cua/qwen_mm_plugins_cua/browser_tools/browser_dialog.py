"""Inspect or resolve a page-owned JavaScript dialog."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from qwen_mm_plugins_cua.browser_tools._shared import BrowserTargetArgs, call_driver_tool


class BrowserDialogArgs(BrowserTargetArgs):
    action: Literal["inspect", "accept", "dismiss"]
    dialog_id: str | None = Field(default=None, description="Exact current dialog id returned by inspect.")
    prompt_text: str | None = Field(default=None, description="Response text used only when accepting a prompt.")
    delivery_mode: Literal["background", "foreground"] = "background"

    @model_validator(mode="after")
    def validate_action(self):
        if self.action in {"accept", "dismiss"} and not self.dialog_id:
            raise ValueError("dialog_id is required to accept or dismiss")
        if self.action == "inspect" and (self.dialog_id or self.prompt_text is not None):
            raise ValueError("inspect does not accept dialog_id or prompt_text")
        if self.action != "accept" and self.prompt_text is not None:
            raise ValueError("prompt_text is valid only for action=accept")
        return self


TOOL: dict[str, Any] = {
    "name": "browser_dialog",
    "description": "Inspect or resolve an exact page-owned JavaScript dialog; not native browser UI.",
    "args": BrowserDialogArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, str]]:
    return call_driver_tool("browser_dialog", arguments)
