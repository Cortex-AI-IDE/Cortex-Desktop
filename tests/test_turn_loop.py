"""W2: the turn loop's two testable seams, at the level that can be tested.

The whole loop is ``_handle_chat``: roughly 2,300 lines that open a provider
stream, emit Qt signals, write the DB and run tools. A test for that needs a
fake provider, a fake UI and a fake database at once, and a test nobody can
read is a test nobody runs.

So these tests cover the two units the loop is actually built from, chosen
because each has a failure mode that is invisible until it costs days:

``_execute_single_tool``   a crashing tool must never take the IDE with it, and
                           the model must be told, or it retries the same call.
                           ``CancelledError`` must escape, or the Stop button
                           silently stops working.

``_await_tool_with_budget``
                           a hung tool must time out, but time a human spends
                           deciding must not count against the budget. Get that
                           second part wrong and every "may I?" question makes
                           the next tool fail.

What is deliberately NOT here: a whole-loop assertion. Per the plan's own
ordering, that needs the extraction in W1 first, because there is no boundary
to assert at yet. Both tests below fail if the behaviour they name is removed.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ai.agent_bridge import CortexAgentBridge  # noqa: E402


@dataclass
class _PCM:
    """Minimal stand-in for the provider's message constructor."""

    role: str
    content: str = ""
    tool_call_id: str = ""
    name: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@pytest.fixture()
def bridge() -> CortexAgentBridge:
    """A bridge with no window, no event loop pump and no QApplication."""
    return CortexAgentBridge()


# ── _execute_single_tool: the crash shield ────────────────────────────────


def test_a_crashing_tool_does_not_escape(bridge: CortexAgentBridge) -> None:
    """A tool that raises must not propagate out of the loop.

    The shield exists because a tool crash used to take the whole IDE with it.
    Without it this call raises ``RuntimeError`` and the chat dies mid-turn.
    """

    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("tool exploded")

    bridge._execute_single_tool_inner = _boom  # type: ignore[method-assign]
    messages: List[_PCM] = []

    asyncio.run(bridge._execute_single_tool("Read", "call-1", {}, messages, _PCM))

    assert len(messages) == 1, "the model was not told the tool failed"


def test_a_crashing_tool_is_reported_to_the_model(bridge: CortexAgentBridge) -> None:
    """The failure goes back as a tool message, not into the void.

    This is the difference between a model that tries something else and one
    that repeats the identical call, because it never learned it failed.
    """

    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("disk went away")

    bridge._execute_single_tool_inner = _boom  # type: ignore[method-assign]
    messages: List[_PCM] = []

    asyncio.run(bridge._execute_single_tool("Edit", "call-2", {}, messages, _PCM))

    assert messages[0].role == "tool"
    assert messages[0].tool_call_id == "call-2"
    assert "disk went away" in messages[0].content
    assert "RuntimeError" in messages[0].content


def test_cancellation_is_not_swallowed(bridge: CortexAgentBridge) -> None:
    """Stop must actually stop.

    ``CancelledError`` is a ``BaseException``, so a blanket ``except`` that is
    not ordered correctly eats it: the tool keeps running and the Stop button
    looks broken.
    """

    async def _cancel(*_args: Any, **_kwargs: Any) -> None:
        raise asyncio.CancelledError()

    bridge._execute_single_tool_inner = _cancel  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            bridge._execute_single_tool("Read", "call-3", {}, [], _PCM)
        )


def test_a_working_tool_produces_no_error_message(bridge: CortexAgentBridge) -> None:
    """The happy path must not append a failure (guards an over-eager shield)."""

    async def _fine(*_args: Any, **_kwargs: Any) -> None:
        return None

    bridge._execute_single_tool_inner = _fine  # type: ignore[method-assign]
    messages: List[_PCM] = []

    asyncio.run(bridge._execute_single_tool("Read", "call-4", {}, messages, _PCM))

    assert messages == []


# ── _await_tool_with_budget: timeout vs. human time ───────────────────────


def test_a_fast_tool_returns_its_result(bridge: CortexAgentBridge) -> None:
    """The budget wrapper must be transparent when the tool finishes in time."""

    async def _quick() -> str:
        return "value"

    assert asyncio.run(bridge._await_tool_with_budget(_quick(), 1.0)) == "value"


def test_a_hung_tool_times_out(bridge: CortexAgentBridge) -> None:
    """A tool that never returns must raise, not hang the turn forever."""

    async def _hang() -> None:
        await asyncio.sleep(30)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(bridge._await_tool_with_budget(_hang(), 0.1))


def test_time_spent_waiting_on_a_human_does_not_burn_the_budget(
    bridge: CortexAgentBridge,
) -> None:
    """The subtle rule, and the one that breaks in production.

    Elapsed wall time is not the budget: the seconds a human spends on a
    permission prompt are subtracted. This tool credits 10s to a human first
    (the prompt is answered), then runs for 0.35s against a 0.15s budget.
    Remove the subtraction and the first tick of the loop raises TimeoutError.
    """

    async def _slow_with_human() -> str:
        # The human answers the prompt immediately, then the tool is slow.
        bridge._human_wait_total = getattr(bridge, "_human_wait_total", 0.0) + 10.0
        await asyncio.sleep(0.35)
        return "survived"

    result = asyncio.run(bridge._await_tool_with_budget(_slow_with_human(), 0.15))
    assert result == "survived"


def test_a_human_waiting_pauses_the_clock_entirely(bridge: CortexAgentBridge) -> None:
    """While a prompt is open the countdown is paused, not merely credited.

    ``_human_wait_depth`` means "a human is deciding right now". A tool that
    outlives the budget during that window must still return normally.
    """
    bridge._human_wait_depth = 1

    async def _slow() -> str:
        await asyncio.sleep(0.2)
        return "asked and answered"

    try:
        assert asyncio.run(bridge._await_tool_with_budget(_slow(), 0.05)) == (
            "asked and answered"
        )
    finally:
        bridge._human_wait_depth = 0
