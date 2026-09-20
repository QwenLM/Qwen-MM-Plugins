#!/usr/bin/env python3
"""Portable launcher for the packaged Music2MV pipeline implementation."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import os
import sys
import types
from pathlib import Path


def _check_python_prefix() -> None:
    # Check before importing the pipeline or third-party packages, so a wrong environment
    # produces an actionable error instead of an unrelated missing-dependency traceback.
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--expected-python-prefix")
    args, remaining = parser.parse_known_args()
    if args.expected_python_prefix is not None:
        if Path(args.expected_python_prefix).resolve() != Path(sys.prefix).resolve():
            parser.exit(
                2,
                "MCP Python environment mismatch: "
                f"expected {args.expected_python_prefix!r}, running {sys.prefix!r}. "
                "Use python_executable returned by get_music2mv_runtime unchanged.\n",
            )
    sys.argv[1:] = remaining


if __name__ == "__main__":
    _check_python_prefix()

_SCRIPT = Path(__file__).resolve()
_CAPABILITY_DIR = _SCRIPT.parents[5]
_SRC_DIR = _SCRIPT.parents[7]
sys.path.insert(0, str(_CAPABILITY_DIR))
sys.path.insert(0, str(_SRC_DIR))


def _install_portable_env_fallback() -> None:
    """Provide shared.env only when a git-subdir plugin is run outside the wheel environment."""
    try:
        if importlib.util.find_spec("shared.env") is not None:
            return
    except ModuleNotFoundError:
        pass

    def get_env(name: str, default: str | None = None) -> str | None:
        value = os.environ.get(name)
        if value is not None:
            return value
        config_override = os.environ.get("QWEN_MM_CONFIG")
        config_dir = os.environ.get("QWEN_MM_CONFIG_DIR")
        config_path = (
            Path(config_override).expanduser()
            if config_override
            else Path(config_dir or "~/.qwen-mm-plugins").expanduser() / "config"
        )
        try:
            lines = config_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return default
        for raw in lines:
            line = raw.strip().removeprefix("export ").lstrip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, candidate = line.partition("=")
            if key.strip() != name:
                continue
            candidate = candidate.strip()
            if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "'\"":
                candidate = candidate[1:-1]
            return candidate
        return default

    shared_module = types.ModuleType("shared")
    shared_module.__path__ = []
    env_module = types.ModuleType("shared.env")
    env_module.get_env = get_env
    sys.modules.setdefault("shared", shared_module)
    sys.modules["shared.env"] = env_module


_install_portable_env_fallback()
main = importlib.import_module("qwen_mm_plugins_omni_chatcut.music_to_mv.pipeline").main


if __name__ == "__main__":
    main()
