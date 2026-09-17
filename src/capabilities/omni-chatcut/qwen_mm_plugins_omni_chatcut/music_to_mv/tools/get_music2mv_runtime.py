"""Expose the running MCP process's interpreter for local skill scripts."""

import json
import sys

from pydantic import BaseModel, ConfigDict

from shared.content import text


class RuntimeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


TOOL = {"name": "get_music2mv_runtime", "args": RuntimeArgs}


def handle(arguments):
    """Return this Omni ChatCut MCP process's Python executable, environment prefix and version.

    Use the executable unchanged to run local Music2MV skill scripts in the same dependency
    environment; pass the prefix as --expected-python-prefix to run_mv_pipeline.py. Read-only, with
    no network requests or credential values.
    """
    RuntimeArgs.model_validate(arguments)
    return [
        text(
            json.dumps(
                {
                    "python_executable": sys.executable,
                    "python_prefix": sys.prefix,
                    "python_version": sys.version.split()[0],
                }
            )
        )
    ]
