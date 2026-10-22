# Copyright 2026 Cortex AI IDE contributors
# SPDX-License-Identifier: Apache-2.0
"""Contract and registry behaviour for the clean-room engine."""

import asyncio

import pytest

from src.engine import contracts, registry


def test_success_shape_matches_bridge():
    out = contracts.ToolResult.success("hello", {"k": 1}).to_bridge()
    assert out == {"data": {"k": 1}, "success": True, "error": None}


def test_failure_shape_matches_bridge():
    out = contracts.ToolResult.failure("boom").to_bridge()
    assert out["success"] is False
    assert out["error"] == "boom"


def test_failure_without_message_still_carries_error():
    out = contracts.ToolResult.failure("").to_bridge()
    assert out["success"] is False
    assert out["error"]


def test_register_and_resolve():
    class FakeTool:
        name = "fake"
        description = ""
        schema = {}
        read_only = True

        async def run(self, args, ctx):
            return contracts.ToolResult.success(str(args))

    registry.clear()
    registry.register("fake", FakeTool)
    assert registry.resolve("fake") is FakeTool
    assert registry.names() == ["fake"]
    registry.clear()


def test_duplicate_registration_is_rejected():
    class A:
        pass

    class B:
        pass

    registry.clear()
    registry.register("dup", A)
    with pytest.raises(ValueError):
        registry.register("dup", B)
    registry.clear()


def test_empty_name_is_rejected():
    with pytest.raises(ValueError):
        registry.register("", object)


def test_bridge_call_returns_dict():
    class Echo:
        name = "echo"
        description = ""
        schema = {}
        read_only = True

        async def run(self, args, ctx):
            return contracts.ToolResult.success(data={"echo": args})

    result = asyncio.run(contracts.bridge_call(Echo(), {"a": 1}, None))
    assert result["success"] is True
    assert result["data"] == {"echo": {"a": 1}}


def test_bridge_call_converts_exceptions_to_failures():
    class Broken:
        name = "broken"
        description = ""
        schema = {}
        read_only = True

        async def run(self, args, ctx):
            raise RuntimeError("nope")

    result = asyncio.run(contracts.bridge_call(Broken(), {}, None))
    assert result["success"] is False
    assert "nope" in result["error"]


def test_tool_protocol_accepts_a_conforming_class():
    class Good:
        name = "good"
        description = ""
        schema = {}
        read_only = False

        async def run(self, args, ctx):
            return contracts.ToolResult.success()

    assert isinstance(Good(), contracts.Tool)
