"""MCP tools must be advertised with their real parameter schema.

Field report, 2026-10-23: ``MCPToolSearch`` reported
``{"type": "object", "properties": {}}`` for every chrome-devtools tool, and
calling ``navigate_page`` failed with::

    Invalid arguments for tool navigate_page: pageId: Invalid input:
    expected number, received undefined

Root cause: the installed MCP SDK renamed ``Tool.inputSchema`` to
``Tool.input_schema``. ``getattr(tool, "inputSchema", None)`` therefore
returned None for every tool, and the fallback empty schema was handed to the
model as if it were the real one. Every MCP tool was affected, not just the
ones reached through MCPToolSearch.

What must not change: a tool that genuinely declares no arguments still gets
the empty object schema, and no schema means "cannot check", not "reject".
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.mcp_manager import tool_input_schema

# The shape that made this invisible: a real parameter the model has to know.
_PAGE_ID_SCHEMA = {
    "type": "object",
    "properties": {"pageId": {"type": "number"}},
    "required": ["pageId"],
}


def test_reads_the_current_sdk_field_name():
    """The installed SDK exposes ``input_schema`` (snake request form)."""
    from mcp.types import Tool

    tool = Tool(name="navigate_page", description="go", inputSchema=_PAGE_ID_SCHEMA)
    # Constructed with the camelCase alias, stored under the new name.
    assert tool_input_schema(tool) == _PAGE_ID_SCHEMA


def test_reads_the_older_sdk_field_name():
    tool = SimpleNamespace(inputSchema=_PAGE_ID_SCHEMA)
    assert tool_input_schema(tool) == _PAGE_ID_SCHEMA


def test_returns_none_when_the_tool_declares_no_schema():
    assert tool_input_schema(SimpleNamespace()) is None
    assert tool_input_schema(SimpleNamespace(input_schema=None)) is None
    assert tool_input_schema(SimpleNamespace(inputSchema={})) is None


def test_dumps_a_pydantic_model_to_a_plain_dict():
    from pydantic import BaseModel

    class Schema(BaseModel):
        type: str = "object"
        properties: dict = {}

    out = tool_input_schema(SimpleNamespace(input_schema=Schema()))
    assert isinstance(out, dict) and out["type"] == "object"


def test_an_empty_parameter_list_is_still_a_valid_schema():
    """``list_pages`` takes nothing: an object with no properties is real."""
    empty = {"type": "object", "properties": {}}
    assert tool_input_schema(SimpleNamespace(input_schema=empty)) == empty


def test_get_tool_definitions_carries_the_parameter_names(monkeypatch):
    """The OpenAI-style definition the model receives must name pageId."""
    from mcp.types import Tool
    from src.services import mcp_manager as mm

    manager = object.__new__(mm.MCPManager)
    manager._states = {
        "chrome-devtools": SimpleNamespace(
            status="connected",
            tools=[Tool(name="navigate_page", description="go",
                        inputSchema=_PAGE_ID_SCHEMA)],
        )
    }
    monkeypatch.setattr(manager, "sync", lambda: None)

    defs = manager.get_tool_definitions()
    assert len(defs) == 1
    params = defs[0]["function"]["parameters"]
    assert "pageId" in params.get("properties", {}), (
        "the model was handed a tool definition with no parameter names"
    )
    assert params["required"] == ["pageId"]
