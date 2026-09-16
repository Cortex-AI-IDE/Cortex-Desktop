# -*- coding: utf-8 -*-
"""Generate Docs/MODEL_INVENTORY.md — every hardcoded model fact in one table."""
import io
import os
import sys

# Repo root is the parent of tools/ — never hardcode a path, this runs on
# any checkout and on Linux builds too.
DESKTOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DESKTOP)
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from src.ai.model_registry import MODEL_GROUPS
from src.ai.model_limits import get_model_limits, _DEFAULT_LIMITS
from src.agent.src.utils.thinking import (
    model_supports_thinking, THINKING_SUPPORTED_MODELS, ADAPTIVE_THINKING_MODELS,
)
from src.agent.src.utils.model.aliases import ALIAS_MAP

try:
    from src.core.loop_engine.model_pricing import _RATES, _DEFAULT
except Exception:
    _RATES, _DEFAULT = {}, (3.0, 15.0)
try:
    from src.ai.providers.openrouter_provider import openrouter_supports_vision
except Exception:
    openrouter_supports_vision = None


def thinks(model_id, provider):
    try:
        if "/" in model_id:
            v, _, n = model_id.partition("/")
            return bool(model_supports_thinking(v, n))
        return bool(model_supports_thinking(provider, model_id))
    except Exception:
        return False


def vision(model_id, provider):
    if provider == "openrouter" and openrouter_supports_vision:
        return bool(openrouter_supports_vision(model_id))
    return None  # decided at runtime per provider/key


# reverse alias map: model_id -> [aliases]
alias_rev = {}
for alias, target in ALIAS_MAP.items():
    alias_rev.setdefault(target, []).append(alias)

L = []
w = L.append

w("# Cortex Desktop — hardcoded model inventory")
w("")
w("Auto-generated snapshot of every model fact compiled into the desktop build,")
w("gathered from the five places that each held part of the picture:")
w("")
w("| Source file | What it holds |")
w("|---|---|")
w("| `src/ai/model_registry.py` | `MODEL_GROUPS` — what the dropdown shows |")
w("| `src/ai/model_limits.py` | context window + max output tokens |")
w("| `src/core/loop_engine/model_pricing.py` | USD per 1M tokens |")
w("| `src/agent/src/utils/thinking.py` | which models produce extended reasoning |")
w("| `src/agent/src/utils/model/aliases.py` | shorthand names users can type |")
w("")
w("Adding one model used to mean editing up to four of these, then rebuilding and")
w("shipping a release. These same facts are now publishable from the server —")
w("see [`MODEL_CONFIG_ARCHITECTURE.md`](MODEL_CONFIG_ARCHITECTURE.md).")
w("")
w("For the provider layer around these models — base URLs, MiMo `tp-` vs `sk-` key")
w("routing, embeddings, orchestrator fallbacks, key storage — see")
w("[`AI_MODEL_REFERENCE.md`](AI_MODEL_REFERENCE.md). Its own model tables are stale;")
w("this file supersedes them.")
w("")
w("> Regenerate with `tools/gen_model_inventory.py`. Do not hand-edit — every number")
w("> below is read out of the running code.")
w("")

total = sum(len(items) for _, items, _, _ in MODEL_GROUPS)
groups = len(MODEL_GROUPS)
thinking_n = 0
priced_n = 0

w(f"**Totals:** {total} models across {groups} dropdown groups.")
w("")
w("---")
w("")

for label, items, tier, provider in MODEL_GROUPS:
    w(f"## {label or 'Auto'}")
    w("")
    w(f"`provider: {provider}` · `tier: {tier}`")
    w("")
    w("| Model ID | Display | Context | Max out | Thinking | $/1M in | $/1M out | Aliases |")
    w("|---|---|--:|--:|:--:|--:|--:|---|")
    for model_id, name, desc, color in items:
        lim = get_model_limits(model_id)
        ctx = f"{lim.context_window:,}"
        out = f"{lim.max_output_tokens:,}"
        th = thinks(model_id, provider)
        if th:
            thinking_n += 1
        rate = _RATES.get(model_id.lower())
        if rate:
            priced_n += 1
            pin, pout = f"{rate[0]:.2f}", f"{rate[1]:.2f}"
        else:
            pin = pout = "—"
        al = ", ".join(f"`{a}`" for a in sorted(alias_rev.get(model_id, []))) or "—"
        w(f"| `{model_id}` | {name} | {ctx} | {out} | {'yes' if th else '—'} | {pin} | {pout} | {al} |")
    w("")

w("---")
w("")
w("## Defaults for anything not listed")
w("")
w("| Fact | Fallback | Where |")
w("|---|---|---|")
w(f"| Context window | {_DEFAULT_LIMITS.context_window:,} | `model_limits._DEFAULT_LIMITS` |")
w(f"| Max output | {_DEFAULT_LIMITS.max_output_tokens:,} | `model_limits._DEFAULT_LIMITS` |")
w(f"| Price in / out | ${_DEFAULT[0]:.2f} / ${_DEFAULT[1]:.2f} per 1M | `model_pricing._DEFAULT` |")
w("| Thinking | `False` | `thinking.model_supports_thinking` |")
w("| Vision | decided per provider at runtime | `agent_bridge._current_model_supports_vision` |")
w("")
w("The pricing fallback is deliberately pessimistic. A cheap model that is missing")
w("from the table is billed in the usage panel as if it cost $3/$15 — roughly 26x")
w("over-reported for DeepSeek. That is the strongest argument for publishing real")
w("rates with each model.")
w("")
w(f"**Thinking-capable:** {thinking_n} of {total}.  ")
w(f"**Priced explicitly:** {priced_n} of {total} (the rest fall back to $3/$15).")
w("")

w("---")
w("")
w("## Provider routing (how a model id picks its API)")
w("")
w("`main_window._on_model_changed`, in order:")
w("")
w("1. **Server config** — a published model states its provider; that wins.")
w("2. `mistral-` / `codestral-` → `mistral`")
w("3. **contains `/`** → `inferencehub` if it starts `ih/`, else `openrouter`")
w("4. `deepseek` → `deepseek`  ·  `mimo-` → `mimo`")
w("5. `gpt-` / `o1` / `o3` / `codex` → `openai_responses` or `openai`")
w("6. `gemini` → `google`  ·  `qwen` / `qwq` → `alibaba`")
w("7. fallback → `deepseek`")
w("")
w("> Step 3 must stay above step 4. It used to sit below, so")
w("> `deepseek/deepseek-v4-pro` matched `startswith('deepseek')` and was sent to")
w("> the direct DeepSeek API, which rejects OpenRouter-style ids. Fixed 2026-09.")
w("")

w("## Thinking tables")
w("")
w("`THINKING_SUPPORTED_MODELS` is keyed by **vendor** for OpenRouter ids")
w("(`anthropic`, `x-ai`, `z-ai`, `moonshotai`) and by **routing provider** for")
w("direct ones (`openai`, `mimo`, `deepseek`, `alibaba`, `inferencehub`), because")
w("the caller splits `vendor/name` before asking.")
w("")
for key in sorted(THINKING_SUPPORTED_MODELS):
    vals = ", ".join(f"`{v}`" for v in sorted(THINKING_SUPPORTED_MODELS[key]))
    w(f"- **{key}** — {vals}")
w("")
w("Adaptive thinking (model decides when to think):")
w("")
for key in sorted(ADAPTIVE_THINKING_MODELS):
    vals = ", ".join(f"`{v}`" for v in sorted(ADAPTIVE_THINKING_MODELS[key]))
    w(f"- **{key}** — {vals}")
w("")

w("## Aliases")
w("")
w(f"{len(ALIAS_MAP)} shorthands. These are a typing convenience only — an alias")
w("never gates a model, so a server-published model works without one.")
w("")
w("| Alias | Resolves to |")
w("|---|---|")
for alias in sorted(ALIAS_MAP):
    w(f"| `{alias}` | `{ALIAS_MAP[alias]}` |")
w("")

path = os.path.join(DESKTOP, "Docs", "MODEL_INVENTORY.md")
os.makedirs(os.path.dirname(path), exist_ok=True)
io.open(path, "w", encoding="utf-8").write("\n".join(L))
print("wrote %s (%d lines, %d models)" % (path, len(L), total))
print("thinking: %d, priced: %d, aliases: %d" % (thinking_n, priced_n, len(ALIAS_MAP)))
