"""CUA capability profile and screenshot coordinate mode selected at server startup."""

from __future__ import annotations

from typing import Literal, cast

from shared.env import get_env

CuaType = Literal["native", "ax", "full"]
CUA_TYPES = frozenset({"native", "ax", "full"})
CoordinateMode = Literal["absolute", "relative"]
COORDINATE_MODES = frozenset({"absolute", "relative"})


def read_cua_type() -> CuaType:
    raw = (get_env("QWEN_MM_CUA_TYPE", "ax") or "ax").strip().lower()
    if raw not in CUA_TYPES:
        choices = ", ".join(sorted(CUA_TYPES))
        raise RuntimeError(f"invalid QWEN_MM_CUA_TYPE={raw!r}; expected one of: {choices}")
    return cast(CuaType, raw)


def read_coordinate_mode() -> CoordinateMode:
    raw = (get_env("QWEN_MM_CUA_COORDINATE_MODE", "absolute") or "absolute").strip().lower()
    if raw not in COORDINATE_MODES:
        choices = ", ".join(sorted(COORDINATE_MODES))
        raise RuntimeError(f"invalid QWEN_MM_CUA_COORDINATE_MODE={raw!r}; expected one of: {choices}")
    return cast(CoordinateMode, raw)


CUA_TYPE = read_cua_type()
CUA_COORDINATE_MODE = read_coordinate_mode()
COORDINATE_MAX_INCLUSIVE = 1000 if CUA_COORDINATE_MODE == "relative" else None
