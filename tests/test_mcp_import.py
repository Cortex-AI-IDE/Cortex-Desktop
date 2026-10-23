"""Settings -> MCP -> Import accepts a single pasted server entry.

2026-10-23: pasting `"ddg-search": {...}` (one entry copied out of an
mcpServers block) failed with "Import failed: Extra data: line 1 column 13".
"""
from __future__ import annotations

import pytest

from src.services.mcp_manager import parse_import_text

_ENTRY = '"ddg-search": {\n  "command": "python.exe",\n  "args": ["ddg_search_mcp.py"]\n}'


@pytest.mark.unit
def test_a_single_entry_is_accepted():
    assert parse_import_text(_ENTRY) == {
        "ddg-search": {"command": "python.exe", "args": ["ddg_search_mcp.py"]}}


@pytest.mark.unit
def test_a_single_entry_with_its_trailing_comma_is_accepted():
    assert "ddg-search" in parse_import_text(_ENTRY + ",\n")


@pytest.mark.unit
def test_a_whole_file_still_parses_unchanged():
    text = '{"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}'
    assert parse_import_text(text)["mcpServers"]["fetch"]["command"] == "uvx"


@pytest.mark.unit
def test_broken_json_says_what_shapes_are_accepted():
    with pytest.raises(ValueError, match="mcpServers"):
        parse_import_text('{"fetch": {"command": "uvx",}')
