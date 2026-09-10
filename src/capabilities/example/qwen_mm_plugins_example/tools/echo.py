"""Example tool (TEXT return): echo a message back."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from shared.content import text


class EchoArgs(BaseModel):
    message: str
    repeat: int = 1


TOOL = {"name": "echo", "args": EchoArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Echo a message back as text. Demonstrates a text-only tool.

    Args:
        message: Text to echo back.
        repeat: How many times to repeat the message (1-10).
    """
    message = arguments.get("message", "")
    repeat = max(1, min(int(arguments.get("repeat", 1)), 10))
    return [text("\n".join([message] * repeat))]
