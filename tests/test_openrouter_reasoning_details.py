"""OpenRouter reasoning must reach the thought card from reasoning_details.

OpenRouter documents streamed reasoning as ``delta.reasoning_details`` items.
The stream reader only looked at ``delta.reasoning``, so a model that sends
its thinking only in the details list (Gemini through OpenRouter) showed no
thought card while DeepSeek, which fills ``delta.reasoning``, did.
"""
from __future__ import annotations

import json

import pytest

from src.ai.providers import openrouter_provider as orp
from src.ai.providers.openrouter_provider import _reasoning_text_from_details


def test_text_and_summary_items_are_readable():
    details = [
        {"type": "reasoning.text", "text": "Reading the file", "format": "google-gemini-v1"},
        {"type": "reasoning.summary", "summary": " then checking callers."},
    ]

    assert _reasoning_text_from_details(details) == "Reading the file then checking callers."


def test_encrypted_and_redacted_items_show_nothing():
    details = [
        {"type": "reasoning.encrypted", "data": "gAAAAB..."},
        {"type": "reasoning.text", "text": "[REDACTED]"},
    ]

    assert _reasoning_text_from_details(details) == ""


@pytest.mark.parametrize("bad", [None, "text", {"type": "reasoning.text"}, [None, 3]])
def test_malformed_details_are_ignored(bad):
    assert _reasoning_text_from_details(bad) == ""


class _FakeResponse:
    ok = True
    raw = None

    def __init__(self, chunks):
        self._lines = [f"data: {json.dumps(c)}".encode() for c in chunks] + [b"data: [DONE]"]

    def raise_for_status(self):
        pass

    def iter_lines(self, *args, **kwargs):
        yield from self._lines

    def close(self):
        pass


def _provider(monkeypatch, chunks):
    """A real OpenRouterProvider whose HTTP session replays ``chunks`` as SSE."""
    provider = orp.OpenRouterProvider.__new__(orp.OpenRouterProvider)
    try:
        orp.OpenRouterProvider.__init__(provider)
    except Exception:
        pass  # no API key in tests; the stream path does not need one
    session = type("S", (), {"post": lambda *_a, **_k: _FakeResponse(chunks)})()
    monkeypatch.setattr(provider, "_session", session, raising=False)
    return provider


def _delta(**fields):
    return {"choices": [{"index": 0, "delta": fields}]}


def test_stream_yields_reasoning_from_details_only(monkeypatch):
    provider = _provider(monkeypatch, [
        _delta(reasoning_details=[{"type": "reasoning.text", "text": "Plan: read it."}]),
        _delta(content="Done."),
    ])

    out = list(provider.chat_stream([orp.ChatMessage(role="user", content="hi")], model="google/gemini-4-argon"))

    assert "__REASONING_DELTA__:Plan: read it." in out
    assert "Done." in out


def test_plain_reasoning_field_is_not_duplicated(monkeypatch):
    provider = _provider(monkeypatch, [
        _delta(reasoning="Think.", reasoning_details=[{"type": "reasoning.text", "text": "Think."}]),
    ])

    out = list(provider.chat_stream([orp.ChatMessage(role="user", content="hi")], model="deepseek/deepseek-r1"))

    assert out.count("__REASONING_DELTA__:Think.") == 1
