"""Real MCP stdio handshake/list/call coverage for Omni Video2Note."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from conftest import mcp_call
from pydantic import BaseModel
from test_omni_video2note import CAPABILITY_DIR, make_short_av_video

if "union_format" not in inspect.signature(BaseModel.model_json_schema).parameters:
    pytest.skip(
        "installed Pydantic is incompatible with mcp_framework union_format; omni-video2note requires pydantic>=2.11",
        allow_module_level=True,
    )
pytest.importorskip("mcp", reason="mcp SDK is required for a real stdio round trip")

SERVER_DIR = CAPABILITY_DIR / "qwen_mm_plugins_omni_video2note"


def test_stdio_initialize_list_and_create_dry_run(tmp_path: Path):
    video = make_short_av_video(tmp_path / "source.mp4")
    output = tmp_path / "note.pdf"

    async def exercise(session):
        listed = await session.list_tools()
        called = await session.call_tool(
            "omni_video2note_create",
            {
                "video_path": str(video),
                "output_path": str(output),
                "dry_run": True,
            },
        )
        return listed, called

    listed, called = mcp_call(str(SERVER_DIR), exercise)
    tools = listed.tools
    assert [tool.name for tool in tools] == ["omni_video2note_create", "omni_video2note_status"]
    assert len({tool.name for tool in tools}) == 2
    for tool in tools:
        schema = tool.inputSchema
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties"), dict)
        assert "$ref" not in json.dumps(schema)
    create_schema = next(tool.inputSchema for tool in tools if tool.name == "omni_video2note_create")
    properties = create_schema["properties"]
    assert "workdir" in properties
    assert "workdir" not in create_schema.get("required", [])
    assert {"omni_model", "vl_model", "review_model"} <= properties.keys()
    assert {"api_key", "base_url", "text_model", "judge_model"}.isdisjoint(properties)

    assert not called.isError
    result = json.loads(called.content[0].text)
    assert result["exit_code"] == 0
    assert result["status"] == "dry_run"
    assert result["workdir"] == str(output.with_suffix(".pdf.work"))
    assert not output.exists()
    assert not output.with_suffix(".pdf.work").exists()
