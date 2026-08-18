"""Temporary cross-platform stdio regression for isolated renderers."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path


def test_isolated_renderers_through_uvx_stdio() -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["MPLBACKEND"] = "Agg"

    async def run_calls():
        params = StdioServerParameters(
            command="uvx",
            args=["--python", "3.12", "--from", ".[core]", "qwen-mm-plugins-core"],
            cwd=str(repo_root),
            env=env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=120)
                results = []
                for relative_path in ("tests/assets/sample.stl", "tests/assets/sample.html"):
                    results.append(
                        await asyncio.wait_for(
                            session.call_tool(
                                "visualize",
                                {
                                    "file_path": str(repo_root / relative_path),
                                    "max_pages": 1,
                                    "budget": "small",
                                },
                            ),
                            timeout=90,
                        )
                    )
                return results

    for result in asyncio.run(run_calls()):
        errors = [getattr(block, "text", "") for block in result.content if getattr(block, "type", None) == "text"]
        assert not result.isError, "\n".join(errors)
        assert any(getattr(block, "type", None) == "image" for block in result.content)
