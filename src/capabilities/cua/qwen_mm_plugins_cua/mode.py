"""CUA capability profile selected once when the MCP server starts."""

from __future__ import annotations

from typing import Literal, cast

from shared.env import get_env

CuaType = Literal["native", "ax", "full"]
CUA_TYPES = frozenset({"native", "ax", "full"})


def read_cua_type() -> CuaType:
    raw = (get_env("QWEN_MM_CUA_TYPE", "ax") or "ax").strip().lower()
    if raw not in CUA_TYPES:
        choices = ", ".join(sorted(CUA_TYPES))
        raise RuntimeError(f"invalid QWEN_MM_CUA_TYPE={raw!r}; expected one of: {choices}")
    return cast(CuaType, raw)


CUA_TYPE = read_cua_type()
