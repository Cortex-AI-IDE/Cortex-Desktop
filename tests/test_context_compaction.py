"""Compaction must report honestly whether it did anything.

Why this file exists. ``_compact_messages`` returns the message list untouched
when every message still fits inside ``keep_tail``. Callers in ``_call_llm``
used to treat *any* return from it as a successful compaction and latch
``_compacted_once = True``. That latch disables the periodic compaction path
for the rest of the request, so a compaction that dropped nothing silently
removed the only mechanism left to catch a growing context.

The contract guarded here is object identity, because that is what the caller
now relies on:

  * nothing to drop  -> the SAME list object comes back
  * real compaction  -> a NEW, shorter list comes back

``keep_tail`` also scales with the model window, so the same message count can
compact on a small model and be a no-op on a 1M model. Both directions are
pinned below.
"""
from __future__ import annotations

import os
import sys
from typing import Any, List
from unittest import mock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ai import agent_bridge as agent_bridge_module  # noqa: E402
from src.ai.agent_bridge import ChatMessage, CortexAgentBridge  # noqa: E402

WINDOW_1M = 1_000_000   # keep_tail = 50
WINDOW_200K = 200_000   # keep_tail = 30


class _Limits:
    """Minimal stand-in for ModelLimits; only context_window is read."""

    def __init__(self, context_window: int) -> None:
        self.context_window = context_window


def _messages(count: int) -> List[ChatMessage]:
    """A system prompt plus alternating user/assistant turns."""
    msgs = [ChatMessage(role='system', content='sys')]
    for i in range(count - 1):
        role = 'user' if i % 2 == 0 else 'assistant'
        msgs.append(ChatMessage(role=role, content=f'm{i}'))
    return msgs


@pytest.fixture
def bridge() -> CortexAgentBridge:
    b = CortexAgentBridge()
    b._tool_ctx._model_limits = _Limits(WINDOW_1M)
    # Checkpoint writing touches .cortex/memory; not what this file tests.
    b._create_context_checkpoint = lambda _msgs: 'CHECKPOINT-TEXT'
    return b


def test_noop_compaction_returns_the_same_object(bridge: CortexAgentBridge) -> None:
    """Every message fits in keep_tail, so the list must come back by identity."""
    msgs = _messages(10)
    assert len(msgs) <= 50 + 2

    result = bridge._compact_messages_inner(msgs, ChatMessage)

    assert result is msgs, 'a no-op must return the same list so callers can tell'


def test_wrapper_preserves_identity_for_the_latch(bridge: CortexAgentBridge) -> None:
    """_call_llm compares before/after identity; the wrapper must not copy."""
    msgs = _messages(10)

    result = bridge._compact_messages(msgs, ChatMessage)

    assert result is msgs


def test_real_compaction_returns_a_new_shorter_list(bridge: CortexAgentBridge) -> None:
    """Above keep_tail the list must be replaced, not mutated in place."""
    msgs = _messages(60)

    result = bridge._compact_messages_inner(msgs, ChatMessage)

    assert result is not msgs, 'real compaction must not be mistaken for a no-op'
    assert len(result) < len(msgs)
    assert len(result) == 2 + 50, 'system + summary + keep_tail(50)'
    assert result[0] is msgs[0], 'the system prompt must survive'
    assert result[-1].content == 'm58', 'the newest turn must survive'


def test_keep_tail_scales_with_the_window(bridge: CortexAgentBridge) -> None:
    """35 messages compact on a 200K model and are a no-op on a 1M model."""
    msgs = _messages(35)

    bridge._tool_ctx._model_limits = _Limits(WINDOW_200K)
    small = bridge._compact_messages_inner(msgs, ChatMessage)
    assert small is not msgs, 'keep_tail=30 should drop messages here'

    bridge._tool_ctx._model_limits = _Limits(WINDOW_1M)
    large = bridge._compact_messages_inner(msgs, ChatMessage)
    assert large is msgs, 'keep_tail=50 should keep all 35'


def test_reported_count_is_accurate(bridge: CortexAgentBridge) -> None:
    """Summary line must report the real before/after sizes."""
    msgs = _messages(60)

    with mock.patch.object(agent_bridge_module, 'log') as log:
        result = bridge._compact_messages_inner(msgs, ChatMessage)

    lines = [str(c.args[0]) for c in log.info.call_args_list]
    summary = [m for m in lines if 'Context compacted' in m]
    assert summary, lines
    assert f'{len(msgs)} \u2192 {len(result)} messages' in summary[0], summary[0]


def test_skip_is_logged_not_silent(bridge: CortexAgentBridge) -> None:
    """A no-op used to be invisible in the log; that hid the latch bug."""
    msgs = _messages(10)

    with mock.patch.object(agent_bridge_module, 'log') as log:
        bridge._compact_messages_inner(msgs, ChatMessage)

    messages = [str(c.args[0]) for c in log.info.call_args_list]
    assert any('Compact skipped' in m for m in messages), messages
    assert any('nothing to drop' in m for m in messages), messages


def test_checkpoint_is_not_written_on_a_noop(bridge: CortexAgentBridge) -> None:
    """A no-op must not touch disk, otherwise every turn re-saves a checkpoint."""
    msgs = _messages(10)
    called: List[Any] = []
    bridge._create_context_checkpoint = lambda m: called.append(m) or 'x'

    bridge._compact_messages_inner(msgs, ChatMessage)

    assert called == [], 'no checkpoint should be written when nothing is dropped'
