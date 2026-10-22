"""A server-defined model's extra_body reaches requests for that model only.

A dynamic provider's extra_body is merged into every request, but the switch
that turns reasoning on differs per model and per API ("reasoning",
"chat_template_kwargs", ...), and one provider can mix thinking and
non-thinking models. The server now sends an extra_body per model; the IDE
merges it over the provider's for requests to that model alone.
"""
from __future__ import annotations

import json

from src.ai import dynamic_providers as dyn
from src.ai.providers import ChatMessage
from src.ai.providers.dynamic_provider import DynamicProvider

RAW = {
    "slug": "nvidia",
    "name": "NVIDIA",
    "base_url": "https://integrate.api.nvidia.com/v1",
    "auth_style": "none",
    "extra_body": {"top_p": 0.9, "reasoning": {"effort": "low"}},
    "models": [
        {"model_id": "deepseek-ai/deepseek-v4", "supports_thinking": True,
         "extra_body": {"reasoning": {"effort": "high"},
                        "chat_template_kwargs": {"thinking": True},
                        "messages": []}},
        {"model_id": "meta/llama-3.3-70b-instruct"},
    ],
}


class _Resp:
    ok = True
    raw = None

    def raise_for_status(self):
        pass

    def iter_lines(self, *args, **kwargs):
        yield b'data: {"choices": [{"index": 0, "delta": {"content": "ok"}}]}'
        yield b"data: [DONE]"

    def close(self):
        pass


def _sent_body(model_id: str, **kwargs) -> dict:
    """The JSON body the provider posts for one request to ``model_id``."""
    sent = {}

    def post(url, headers=None, json=None, **_k):
        sent.update(json)
        return _Resp()

    provider = DynamicProvider(dyn._clean_provider(RAW))
    provider._session = type("S", (), {"post": staticmethod(post)})()
    list(provider.chat_stream([ChatMessage(role="user", content="hi")],
                              model=f"dp/nvidia/{model_id}", **kwargs))
    return sent


def test_sync_keeps_each_models_extra_body_without_forbidden_keys():
    spec = dyn._clean_provider(RAW)
    by_id = {m["model_id"]: m for m in spec["models"]}

    assert by_id["deepseek-ai/deepseek-v4"]["extra_body"] == {
        "reasoning": {"effort": "high"}, "chat_template_kwargs": {"thinking": True}}
    assert by_id["meta/llama-3.3-70b-instruct"]["extra_body"] == {}


def test_model_extra_body_is_merged_over_the_providers():
    body = _sent_body("deepseek-ai/deepseek-v4")

    assert body["reasoning"] == {"effort": "high"}          # model wins
    assert body["chat_template_kwargs"] == {"thinking": True}
    assert body["top_p"] == 0.9                              # provider kept
    assert body["model"] == "deepseek-ai/deepseek-v4"
    assert body["messages"] != []                            # never overridden


def test_other_models_of_the_provider_do_not_get_it():
    body = _sent_body("meta/llama-3.3-70b-instruct")

    assert "chat_template_kwargs" not in body
    assert body["reasoning"] == {"effort": "low"}            # provider-level only


def test_old_server_payload_without_model_extra_body_still_works():
    raw = json.loads(json.dumps(RAW))
    for m in raw["models"]:
        m.pop("extra_body", None)
    spec = dyn._clean_provider(raw)

    assert all(m["extra_body"] == {} for m in spec["models"])
