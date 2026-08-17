"""Windows regression reproducer for GitHub issue #31.

Launch the core server through the same uvx + stdio path reported by the user,
then require a small STL visualization call to finish promptly.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows stdio regression")


def test_visualize_stl_through_uvx_stdio() -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    repo_root = Path(__file__).resolve().parents[1]
    sample_stl = repo_root / "tests" / "assets" / "sample.stl"
    env = dict(os.environ)
    env["MPLBACKEND"] = "Agg"

    async def run_call():
        params = StdioServerParameters(
            command="uvx",
            args=[
                "--python",
                "3.12",
                "--from",
                ".[core]",
                "qwen-mm-plugins-core",
            ],
            cwd=str(repo_root),
            env=env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=120)
                return await asyncio.wait_for(
                    session.call_tool(
                        "visualize",
                        {
                            "file_path": str(sample_stl),
                            "max_pages": 1,
                            "budget": "small",
                        },
                    ),
                    timeout=150,
                )

    result = asyncio.run(run_call())
    errors = [getattr(block, "text", "") for block in result.content if getattr(block, "type", None) == "text"]
    assert not result.isError, "\n".join(errors)
    assert any(getattr(block, "type", None) == "image" for block in result.content)
