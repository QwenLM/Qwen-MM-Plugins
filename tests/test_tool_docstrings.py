"""One docstring convention drives the MCP wire schema and Hub export."""

import asyncio
import importlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field, ValidationError, field_validator

import mcp_framework as fw


class Args(BaseModel):
    text: str
    repeat: int = Field(default=1, ge=1, le=10)

    @field_validator("text")
    @classmethod
    def nonempty(cls, value):
        if not value:
            raise ValueError("empty text")
        return value


def documented(arguments):
    """Repeat some text.

    Useful for testing descriptions. 中文也可以。

    Args:
        text: Text to repeat.
            Continued field documentation.
        repeat: Number of repetitions.

    Examples:
        {"text": "hello", "repeat": 2}
    """
    return [{"type": "text", "text": arguments["text"]}]


def spec(handle=documented, model=Args):
    return fw._spec_from_module(SimpleNamespace(TOOL={"name": "repeat", "args": model}, handle=handle))


def test_google_docstring_supplies_tool_parameters_and_examples():
    tool = spec()
    assert tool.description.startswith("Repeat some text.\n\nUseful for testing descriptions. 中文也可以。")
    assert 'Examples:\n{"text": "hello", "repeat": 2}' in tool.description
    assert "Args:" not in tool.description
    fields = tool.input_schema["properties"]
    assert fields["text"]["description"] == "Text to repeat.\nContinued field documentation."
    assert fields["repeat"]["description"] == "Number of repetitions."


def test_defaults_constraints_requiredness_and_validators_survive():
    tool = spec()
    fields = tool.input_schema["properties"]
    assert tool.input_schema["required"] == ["text"]
    assert fields["repeat"]["default"] == 1
    assert fields["repeat"]["minimum"] == 1 and fields["repeat"]["maximum"] == 10
    assert tool.args_model(text="hello").repeat == 1
    for bad in ({"text": "hello", "repeat": 11}, {"text": ""}, {"repeat": 2}):
        with pytest.raises(ValidationError):
            tool.args_model(**bad)


def test_shared_model_is_not_mutated_and_wrapper_receives_descriptions():
    tool = spec()
    assert Args.model_fields["text"].description is None
    wrapper = fw._make_wrapper(tool)
    field = inspect.signature(wrapper).parameters["text"].annotation.__metadata__[0]
    assert field.description == tool.input_schema["properties"]["text"]["description"]


def test_aliases_survive_docstring_enrichment():
    class Aliased(Args):
        text: str = Field(alias="message")

    tool = spec(model=Aliased)
    assert tool.args_model(message="hello").text == "hello"
    assert "message" in tool.input_schema["properties"]


@pytest.mark.parametrize("args", ["", "        typo: Unknown.", "        text: First.\n        text: Duplicate."])
def test_args_must_document_every_field_exactly_once(args):
    def handler(arguments):
        return []

    handler.__doc__ = "Summary.\n\n    Args:\n" + args if args else "Summary."
    with pytest.raises(ValueError, match="each model field exactly once"):
        spec(handler)


def test_missing_docstring_fails_registration():
    with pytest.raises(ValueError, match="public docstring"):
        spec(lambda arguments: [])


def test_fastmcp_advertises_the_same_docstring_descriptions():
    from mcp.server.fastmcp import FastMCP

    tool = spec()
    server = FastMCP("test-docstrings")
    server.add_tool(fw._make_wrapper(tool), name=tool.name, description=tool.description, structured_output=False)
    wire = asyncio.run(server.list_tools())[0]
    assert wire.description == tool.description
    assert (
        wire.inputSchema["properties"]["text"]["description"] == tool.input_schema["properties"]["text"]["description"]
    )


def test_all_capabilities_and_template_follow_the_same_convention():
    root = Path(__file__).resolve().parents[1]
    capabilities = [*json.loads((root / "plugin-versions.json").read_text())["plugins"], "example"]
    for cap in capabilities:
        if not (root / "src/capabilities" / cap / ".mcp.json").exists():
            continue
        module = importlib.import_module("qwen_mm_plugins_" + cap.replace("-", "_"))
        for tool in module.SPECS:
            definition = inspect.getmodule(tool.handle).TOOL
            assert set(definition) == {"name", "args"}, (cap, tool.name)
            assert all(field.description is None for field in definition["args"].model_fields.values())
            assert all(field.description for field in tool.args_model.model_fields.values())
            assert tool.description
