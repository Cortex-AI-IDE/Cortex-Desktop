# GPT-5 Compatibility & Multi-Architecture Wiring

**Date:** 2026-07-13
**Scope:** Multi-core topology (CUDA, XPU, NPU, WebGPU, CPU fallback), schema migration, provider registry refactor, gpt-5 testing matrix, telemetry adaptation, regressions, developer workflow.

---

## 1. Purpose

Cortex v2.8 added GPT-5 as the first model in the new three-tier intelligence hierarchy (V2.5 → GPT-5 → V2.8-Pro). This required wiring a new model family into an already complex multi-architecture runtime that supports CUDA (NVIDIA), XPU (Intel), NPU (Qualcomm), WebGPU (browser), and CPU (fallback). This document traces every layer of the change: from the GPU discovery plugin through the provider registry, the inference adapter, the database schema, the telemetry pipeline, the installer, and the developer workflow.

---

## 2. Multi-Core Topology

### 2.1 Architecture Map

| Core | Backend | Runtime | GPT-5 Status | Notes |
|------|---------|---------|--------------|-------|
| **CUDA** | NVIDIA GPU | `nvidia-smi` → `torch.cuda` | ✅ Production | Flash Attention 3, CUDA Graphs, bf16/fp16 |
| **XPU** | Intel GPU | `intel-extension-for-pytorch` → `torch.xpu` | ✅ Production | SYCL 2025, Level Zero, oneDNN |
| **NPU** | Qualcomm Hexagon | `qnn-sdk` → `torch_npu` | ⚠️ Preview | INT4 quantized only, no fp16 |
| **WebGPU** | Browser GPU | `onnxruntime-web` → WebGPU EP | ⚠️ Preview | 7B quantized only, wasm fallback |
| **CPU** | Any x86/ARM | `onnxruntime` → CPU EP | ✅ Production | INT8 quantized, slow but always works |

### 2.2 Discovery Flow

```
boot()
  ├─ detect_gpu_vendor()          # CUDA? XPU? NPU? WebGPU? none?
  │    ├─ nvidia-smi --query-gpu  # CUDA
  │    ├─ xpu-smi --query-device  # XPU
  │    ├─ qnn-net-run --check     # NPU
  │    └─ navigator.gpu?.request  # WebGPU
  ├─ resolve_backend(vendor)      # → cuda / xpu / npu / webgpu / cpu
  ├─ load_backend_plugins()       # dynamic .dll / .so from plugins/
  │    ├─ cuda_backend_v3.plugin  # loaded if CUDA detected
  │    ├─ xpu_backend_v2.plugin   # loaded if XPU detected
  │    ├─ npu_backend_v1.plugin   # loaded if NPU detected
  │    ├─ webgpu_backend_v1.plugin# loaded if WebGPU detected
  │    └─ cpu_backend_v3.plugin   # always loaded as fallback
  └─ init_inference_engine(backend, device_id)
       ├─ torch.device(backend)
       ├─ ort.InferenceSession(device=backend)
       └─ ort.InferenceSession(device='webgpu')
```

### 2.3 GPT-5 Per-Architecture Status

GPT-5 (400B MoE) requires a minimum of 2×24 GB VRAM (48 GB total) or 4×16 GB VRAM for full precision. Quantized variants (INT4/INT8) can run on smaller hardware.

| Variant | CUDA | XPU | NPU | WebGPU | CPU |
|---------|------|-----|-----|--------|-----|
| GPT-5 (fp16, 400B) | ✅ 2×24 GB | ⚠️ 2×32 GB | ❌ | ❌ | ❌ |
| GPT-5 (INT8, 400B) | ✅ 1×24 GB | ⚠️ 1×32 GB | ❌ | ❌ | ❌ |
| GPT-5-mini (fp16, 120B) | ✅ 1×24 GB | ✅ 1×32 GB | ⚠️ Preview | ❌ | ❌ |
| GPT-5-mini (INT4, 120B) | ✅ 1×12 GB | ✅ 1×16 GB | ✅ Preview | ⚠️ 7B only | ❌ |
| GPT-5-nano (INT4, 8B) | ✅ 1×8 GB | ✅ 1×8 GB | ✅ | ✅ | ⚠️ Slow |
| GPT-5-turbo (API only) | N/A | N/A | N/A | N/A | N/A |

---

## 3. Database Schema Migration

### 3.1 New Table: `model_capabilities`

```sql
CREATE TABLE model_capabilities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_key       TEXT NOT NULL UNIQUE,           -- e.g. 'gpt-5', 'gpt-5-mini', 'gpt-5-nano'
    provider        TEXT NOT NULL,                  -- 'openai', 'cortex-local'
    family          TEXT NOT NULL DEFAULT 'gpt5',   -- 'gpt5', 'mimo', 'qwen'
    tier            TEXT NOT NULL,                  -- 'v2.5', 'gpt5', 'v2.8-pro'
    context_window  INTEGER NOT NULL,               -- 1000000
    max_output      INTEGER NOT NULL,               -- 32768
    supports_tools  BOOLEAN NOT NULL DEFAULT 1,
    supports_vision BOOLEAN NOT NULL DEFAULT 0,
    supports_audio  BOOLEAN NOT NULL DEFAULT 0,
    supports_mcp    BOOLEAN NOT NULL DEFAULT 1,
    min_vram_gb     INTEGER,                        -- minimum GPU memory required
    arch_support    TEXT,                           -- JSON: {"cuda":"ok","xpu":"ok","npu":"preview","webgpu":"no","cpu":"no"}
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2 Migration Script

```sql
-- 0008_model_capabilities.sql
-- Adds model_capabilities table and populates known models

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS model_capabilities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_key       TEXT NOT NULL UNIQUE,
    provider        TEXT NOT NULL,
    family          TEXT NOT NULL DEFAULT 'gpt5',
    tier            TEXT NOT NULL,
    context_window  INTEGER NOT NULL,
    max_output      INTEGER NOT NULL,
    supports_tools  BOOLEAN NOT NULL DEFAULT 1,
    supports_vision BOOLEAN NOT NULL DEFAULT 0,
    supports_audio  BOOLEAN NOT NULL DEFAULT 0,
    supports_mcp    BOOLEAN NOT NULL DEFAULT 1,
    min_vram_gb     INTEGER,
    arch_support    TEXT,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_model_capabilities_provider ON model_capabilities(provider);
CREATE INDEX IF NOT EXISTS idx_model_capabilities_tier ON model_capabilities(tier);
CREATE INDEX IF NOT EXISTS idx_model_capabilities_family ON model_capabilities(family);

-- Seed: existing models
INSERT INTO model_capabilities (model_key, provider, family, tier, context_window, max_output, min_vram_gb, arch_support) VALUES
('mimo-v2.5-pro',     'xiaomi',   'mimo',  'v2.5',    1000000, 32768, NULL, '{"cuda":"ok","xpu":"ok","npu":"no","webgpu":"no","cpu":"no"}'),
('mimo-v2.5',         'xiaomi',   'mimo',  'v2.5',    1000000, 32768, NULL, '{"cuda":"ok","xpu":"ok","npu":"no","webgpu":"no","cpu":"no"}'),
('deepseek-v4-pro',   'deepseek', 'ds',    'v2.5',    1000000, 32768, NULL, '{"cuda":"ok","xpu":"ok","npu":"no","webgpu":"no","cpu":"no"}'),
('deepseek-v4-flash', 'deepseek', 'ds',    'v2.5',    1000000, 32768, NULL, '{"cuda":"ok","xpu":"ok","npu":"no","webgpu":"no","cpu":"no"}'),
('gpt-5',             'openai',   'gpt5',  'gpt5',    1000000, 32768, 48,   '{"cuda":"ok","xpu":"preview","npu":"no","webgpu":"no","cpu":"no"}'),
('gpt-5-mini',        'openai',   'gpt5',  'gpt5',    1000000, 32768, 24,   '{"cuda":"ok","xpu":"ok","npu":"preview","webgpu":"no","cpu":"no"}'),
('gpt-5-nano',        'openai',   'gpt5',  'gpt5',    1000000, 32768, 8,    '{"cuda":"ok","xpu":"ok","npu":"ok","webgpu":"preview","cpu":"slow"}'),
('gpt-5.5',           'openai',   'gpt5',  'v2.8-pro',1000000, 65536, 64,   '{"cuda":"ok","xpu":"preview","npu":"no","webgpu":"no","cpu":"no"}'),
('gpt-5.4',           'openai',   'gpt5',  'v2.8-pro',1000000, 65536, 64,   '{"cuda":"ok","xpu":"preview","npu":"no","webgpu":"no","cpu":"no"}'),
('qwen-3-72b',        'alibaba',  'qwen',  'v2.5',    1000000, 32768, NULL, '{"cuda":"ok","xpu":"ok","npu":"no","webgpu":"no","cpu":"no"}'),
('qwen-3-32b',        'alibaba',  'qwen',  'v2.5',    1000000, 32768, NULL, '{"cuda":"ok","xpu":"ok","npu":"no","webgpu":"no","cpu":"no"}');

-- Add FK or CHECK constraint if needed
-- model_capabilities.family IN ('gpt5', 'mimo', 'ds', 'qwen', 'claude', 'gemini', 'grok')
-- model_capabilities.tier IN ('v2.5', 'gpt5', 'v2.8-pro')

COMMIT;
```

### 3.3 User Settings Table: New Columns

```sql
-- 0009_user_model_preferences.sql
-- Adds GPT-5 specific user preferences

BEGIN TRANSACTION;

ALTER TABLE user_settings ADD COLUMN preferred_tier TEXT DEFAULT 'v2.5';
ALTER TABLE user_settings ADD COLUMN auto_upgrade_tier BOOLEAN DEFAULT 0;
ALTER TABLE user_settings ADD COLUMN fallback_chain TEXT DEFAULT '["deepseek-v4-flash","gpt-5-nano","mimo-v2.5"]';
ALTER TABLE user_settings ADD COLUMN gpt5_mini_enabled BOOLEAN DEFAULT 1;

COMMIT;
```

### 3.4 Session Model Storage

```sql
-- 0010_session_model_family.sql
-- Tracks which model family was used per session for analytics

BEGIN TRANSACTION;

ALTER TABLE sessions ADD COLUMN model_family TEXT;
ALTER TABLE sessions ADD COLUMN model_tier TEXT;
ALTER TABLE sessions ADD COLUMN backend_arch TEXT;  -- 'cuda', 'xpu', 'npu', 'webgpu', 'cpu'

COMMIT;
```

---

## 4. Provider Registry Refactor

### 4.1 Before: Flat Provider List

```python
# config/settings.py (before)
PROVIDERS = {
    "openai": {"api_key": "", "base_url": "https://api.openai.com/v1"},
    "deepseek": {"api_key": "", "base_url": "https://api.deepseek.com/v1"},
    "xiaomi": {"api_key": "", "base_url": "https://api.mimo.xiaomi.com/v1"},
    "qwen": {"api_key": "", "base_url": "https://dashscope.aliyuncs.com/v1"},
    "openrouter": {"api_key": "", "base_url": "https://openrouter.ai/api/v1"},
}
```

### 4.2 After: Tiered Provider Registry

```python
# config/settings.py (after)

TIER_HIERARCHY = {
    "v2.5":    ["mimo-v2.5", "deepseek-v4-flash", "qwen-3-32b"],
    "gpt5":    ["gpt-5-mini", "gpt-5-nano", "gpt-5"],
    "v2.8-pro":["gpt-5.5", "gpt-5.4"],
}

PROVIDER_REGISTRY = {
    # V2.5 tier
    "xiaomi": {
        "base_url": "https://api.mimo.xiaomi.com/v1",
        "models": ["mimo-v2.5-pro", "mimo-v2.5"],
        "tier": "v2.5",
        "family": "mimo",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
        "tier": "v2.5",
        "family": "ds",
    },
    "alibaba": {
        "base_url": "https://dashscope.aliyuncs.com/v1",
        "models": ["qwen-3-72b", "qwen-3-32b"],
        "tier": "v2.5",
        "family": "qwen",
    },
    # GPT-5 tier
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-5", "gpt-5-mini", "gpt-5-nano"],
        "tier": "gpt5",
        "family": "gpt5",
    },
    # V2.8-Pro tier
    "openai-pro": {
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-5.5", "gpt-5.4"],
        "tier": "v2.8-pro",
        "family": "gpt5",
    },
    # OpenRouter (multi-provider)
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["anthropic/claude-4", "google/gemini-2.5", "xai/grok-3"],
        "tier": "v2.5",
        "family": "mixed",
    },
}

# Resolve model → provider
def resolve_provider(model_key: str) -> dict:
    """Find the provider config for a given model key."""
    for provider_key, provider_cfg in PROVIDER_REGISTRY.items():
        if model_key in provider_cfg["models"]:
            return {"provider": provider_key, **provider_cfg}
    raise ValueError(f"Unknown model: {model_key}")

# Resolve tier → available models
def models_for_tier(tier: str) -> list[str]:
    """Return all model keys available in a given tier."""
    models = []
    for provider_cfg in PROVIDER_REGISTRY.values():
        if provider_cfg["tier"] == tier:
            models.extend(provider_cfg["models"])
    return models
```

### 4.3 Provider Interface (Inference Adapter)

```python
# core/providers/base.py

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

class BaseProvider(ABC):
    """Base class for all inference providers."""

    @abstractmethod
    async def chat_completion(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        stream: bool = False,
        **kwargs
    ) -> dict | AsyncIterator[dict]:
        ...

    @abstractmethod
    async def list_models(self) -> list[dict]:
        ...

    @abstractmethod
    async def validate_key(self) -> bool:
        ...


class OpenAIProvider(BaseProvider):
    """OpenAI API adapter — handles GPT-5 and GPT-5 variants."""

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.base_url = base_url

    async def chat_completion(self, messages, model, tools=None, stream=False, **kwargs):
        import httpx
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {"model": model, "messages": messages, "stream": stream}
        if tools:
            body["tools"] = tools

        async with httpx.AsyncClient() as client:
            if stream:
                return self._stream_response(client, headers, body)
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=body,
                timeout=120
            )
            return resp.json()

    async def _stream_response(self, client, headers, body):
        async with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=body,
            timeout=120
        ) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    import json
                    yield json.loads(line[6:])

    async def list_models(self):
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            return resp.json().get("data", [])

    async def validate_key(self):
        try:
            models = await self.list_models()
            return len(models) > 0
        except Exception:
            return False
```

---

## 5. GPT-5 Testing Matrix

### 5.1 Test Categories

| Category | Tests | Priority | Notes |
|----------|-------|----------|-------|
| **Smoke** | 10 | P0 | Does GPT-5 respond at all? Basic completion, no tools. |
| **Tool calling** | 25 | P0 | Does GPT-5 correctly invoke bash, file_read, grep, etc.? |
| **Context window** | 8 | P0 | Can it handle 500K+ token prompts without truncation? |
| **Streaming** | 12 | P0 | SSE streaming, chunk assembly, timeout handling. |
| **Multi-turn** | 15 | P1 | 10+ turn conversations, context accumulation, tool chaining. |
| **Error recovery** | 10 | P1 | Rate limits, 500s, timeouts, malformed responses. |
| **Cost tracking** | 8 | P1 | Token counting, USD calculation, budget enforcement. |
| **Tier fallback** | 5 | P1 | gpt-5 fails → fall back to gpt-5-mini → gpt-5-nano. |
| **Vision** | 6 | P2 | GPT-5 with image inputs (screenshots, diagrams). |
| **MCP** | 8 | P2 | GPT-5 + MCP server tools (GitHub, filesystem, custom). |
| **Streaming + tools** | 5 | P2 | Streaming responses that contain tool calls. |
| **Stress** | 3 | P2 | 100 concurrent requests, rate limit handling. |
| **Regression** | 15 | P0 | Existing MiMo/DeepSeek/Qwen tests still pass. |

**Total: 130 tests**

### 5.2 CI Pipeline

```yaml
# .github/workflows/gpt5-test.yml

name: GPT-5 Compatibility Tests
on:
  push:
    branches: [main, release/*]
  pull_request:
    paths:
      - 'src/core/providers/**'
      - 'src/core/loop_engine/**'
      - 'tests/providers/**'

jobs:
  gpt5-smoke:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -e ".[dev]"
      - run: pytest tests/providers/test_gpt5_smoke.py -v --timeout=60
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_TEST_KEY }}

  gpt5-tools:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    needs: gpt5-smoke
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -e ".[dev]"
      - run: pytest tests/providers/test_gpt5_tools.py -v --timeout=120
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_TEST_KEY }}

  gpt5-context:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    needs: gpt5-smoke
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -e ".[dev]"
      - run: pytest tests/providers/test_gpt5_context.py -v --timeout=300
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_TEST_KEY }}

  regression:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -e ".[dev]"
      - run: pytest tests/providers/ -v --timeout=60
        env:
          XIAOMI_API_KEY: ${{ secrets.XIAOMI_TEST_KEY }}
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_TEST_KEY }}
          QWEN_API_KEY: ${{ secrets.QWEN_TEST_KEY }}
```

---

## 6. Telemetry Adaptation

### 6.1 New Telemetry Events

| Event | Trigger | Payload |
|-------|---------|---------|
| `model.selected` | User picks a model | `{model, provider, tier, backend}` |
| `model.fallback` | Auto-fallback triggered | `{from_model, to_model, reason}` |
| `model.capabilities.resolved` | Capabilities loaded | `{model, capabilities_json}` |
| `inference.start` | Request sent | `{model, provider, arch, message_count, tool_count}` |
| `inference.end` | Response received | `{model, latency_ms, tokens_in, tokens_out, cost_usd, tools_called, finish_reason}` |
| `inference.error` | Request failed | `{model, error_code, error_message, retry_count}` |
| `inference.timeout` | Request timed out | `{model, timeout_s, partial_tokens}` |
| `tier.upgrade` | User upgrades tier | `{from_tier, to_tier}` |
| `tier.downgrade` | User downgrades tier | `{from_tier, to_tier}` |
| `backend.switch` | Backend changed | `{from_arch, to_arch, reason}` |

### 6.2 Cost Tracking Schema

```python
# core/telemetry/cost_tracker.py

GPT5_PRICING = {
    # per 1M tokens (USD)
    "gpt-5":      {"input": 2.50,  "output": 10.00},
    "gpt-5-mini": {"input": 0.40,  "output": 1.60},
    "gpt-5-nano": {"input": 0.05,  "output": 0.20},
    "gpt-5.5":    {"input": 5.00,  "output": 20.00},
    "gpt-5.4":    {"input": 3.00,  "output": 12.00},
}

def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate USD cost for a GPT-5 model invocation."""
    pricing = GPT5_PRICING.get(model)
    if not pricing:
        return 0.0
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
```

### 6.3 Telemetry Dashboard Integration

```python
# core/telemetry/dashboard.py

def get_model_usage_summary(user_id: str, days: int = 30) -> dict:
    """Aggregate model usage by tier for the dashboard."""
    from core.db import get_db
    db = get_db()
    rows = db.execute("""
        SELECT model_tier, model_family,
               COUNT(*) as invocations,
               SUM(tokens_in) as total_input,
               SUM(tokens_out) as total_output,
               SUM(cost_usd) as total_cost,
               AVG(latency_ms) as avg_latency
        FROM sessions
        WHERE user_id = ? AND created_at > datetime('now', ?)
        GROUP BY model_tier, model_family
        ORDER BY total_cost DESC
    """, (user_id, f'-{days} days')).fetchall()

    return {
        "period_days": days,
        "tiers": [
            {
                "tier": row["model_tier"],
                "family": row["model_family"],
                "invocations": row["invocations"],
                "total_input_tokens": row["total_input"],
                "total_output_tokens": row["total_output"],
                "total_cost_usd": round(row["total_cost"], 4),
                "avg_latency_ms": round(row["avg_latency"], 1),
            }
            for row in rows
        ],
    }
```

---

## 7. Regressions & Known Issues

### 7.1 Critical Regressions

| ID | Severity | Description | Status | Fix |
|----|----------|-------------|--------|-----|
| **REG-001** | 🔴 Critical | GPT-5 tool_calls format uses `{"type": "function", "function": {"name": ..., "arguments": ...}}` — Cortex parser expected flat `{"name": ..., "arguments": ...}` | ✅ Fixed | Updated `core/providers/openai.py` parser to handle both formats |
| **REG-002** | 🔴 Critical | GPT-5 returns `finish_reason: "tool_calls"` instead of `"stop"` when tools invoked — loop engine treated as incomplete and re-prompted infinitely | ✅ Fixed | Added `"tool_calls"` to `FINISH_REASONS` set in `core/loop_engine/loop.py` |
| **REG-003** | 🟡 High | GPT-5-mini on XPU backend crashes with SYCL memory error on prompts > 200K tokens | ⚠️ Open | Workaround: cap XPU prompts at 200K; full fix needs Intel driver update |
| **REG-004** | 🟡 High | GPT-5.5 (v2.8-pro) costs not tracked — pricing table missing entries | ✅ Fixed | Added GPT-5.5 and GPT-5.4 to `GPT5_PRICING` dict |
| **REG-005** | 🟡 High | Streaming GPT-5 responses with tool calls produces split chunks — partial JSON assembled incorrectly | ✅ Fixed | Added chunk buffering in `core/providers/streaming.py` |
| **REG-006** | 🟠 Medium | GPT-5 vision responses return `image_url` in content — Cortex chat renderer doesn't display inline images | ⚠️ Open | Needs chat UI update to render inline images |
| **REG-007** | 🟠 Medium | `model_capabilities` migration fails on SQLite < 3.35.0 (no `IF NOT EXISTS` in `CREATE TABLE`) | ✅ Fixed | Added version check, fallback to `CREATE TABLE IF NOT EXISTS` |
| **REG-008** | 🟢 Low | GPT-5-nano on CPU takes 45s+ for simple completions — user sees "thinking" spinner indefinitely | ℹ️ Known | Documented as expected; recommend CPU users use DeepSeek/MiMo instead |

### 7.2 Regression Test Fixtures

```python
# tests/providers/test_gpt5_regression.py

import pytest
from core.providers.openai import OpenAIProvider
from core.loop_engine.loop import FINISH_REASONS

class TestGPT5Regression:
    """Regression tests for GPT-5 compatibility issues."""

    def test_tool_calls_format_flat(self):
        """REG-001: Parser handles flat tool_calls format (MiMo/DeepSeek style)."""
        response = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "tool_calls": [{"name": "bash", "arguments": {"command": "ls"}}]
                },
                "finish_reason": "tool_calls"
            }]
        }
        parsed = OpenAIProvider._parse_response(response)
        assert parsed.tool_calls[0].name == "bash"

    def test_tool_calls_format_nested(self):
        """REG-001: Parser handles nested tool_calls format (GPT-5 style)."""
        response = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "tool_calls": [{
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"command": "ls"}'}
                    }]
                },
                "finish_reason": "tool_calls"
            }]
        }
        parsed = OpenAIProvider._parse_response(response)
        assert parsed.tool_calls[0].name == "bash"

    def test_finish_reason_tool_calls_stops_loop(self):
        """REG-002: finish_reason='tool_calls' is recognized as complete."""
        assert "tool_calls" in FINISH_REASONS

    def test_streaming_tool_call_chunk_buffering(self):
        """REG-005: Partial tool call JSON chunks are buffered correctly."""
        chunks = [
            '{"choices":[{"delta":{"tool_calls":[{"function":{"name":"bash","argu',
            'ments":"{\\"comm',
            'and\\":\\"ls\\"}"}}]}}]}',
        ]
        result = OpenAIProvider._buffer_stream_chunks(chunks)
        assert result.tool_calls[0].name == "bash"
        assert result.tool_calls[0].arguments == {"command": "ls"}
```

---

## 8. Developer Workflow

### 8.1 Setting Up GPT-5 Locally

```bash
# 1. Add OpenAI API key
cortex config set openai.api_key sk-...

# 2. Verify key
cortex config verify openai
# ✅ OpenAI key valid. Available models: gpt-5, gpt-5-mini, gpt-5-nano

# 3. Set default model to GPT-5-mini
cortex config set default.model gpt-5-mini

# 4. Verify hardware compatibility
cortex doctor --check-arch
# ✅ CUDA: NVIDIA RTX 4090 (24 GB)
# ✅ GPT-5-mini: supported (requires 24 GB)
# ⚠️ GPT-5: requires 48 GB — use quantized or XPU
```

### 8.2 Developing a New Provider Adapter

```bash
# 1. Create provider file
touch src/core/providers/new_provider.py

# 2. Implement BaseProvider
# See src/core/providers/base.py for interface

# 3. Register in PROVIDER_REGISTRY
# Edit config/settings.py

# 4. Add to model_capabilities
# Run: python manage.py migrate

# 5. Write tests
touch tests/providers/test_new_provider.py

# 6. Run tests
pytest tests/providers/test_new_provider.py -v

# 7. Add to CI
# Edit .github/workflows/gpt5-test.yml
```

### 8.3 Adding a New Architecture Backend

```bash
# 1. Create backend plugin
touch plugins/new_arch_backend.plugin

# 2. Implement detect() and init()
# See plugins/cuda_backend_v3.plugin for reference

# 3. Register in boot sequence
# Edit src/core/boot.py

# 4. Add to arch_support in model_capabilities
# Run migration

# 5. Test
pytest tests/arch/test_new_arch.py -v

# 6. Document
# Update this file and Docs/ARCHITECTURE.md
```

### 8.4 Debugging GPT-5 Issues

```bash
# Enable debug logging
cortex config set debug.providers true
cortex config set debug.telemetry true

# View logs
tail -f ~/.cortex/logs/provider.log

# Check model capabilities
cortex models list --tier gpt5
# gpt-5        | openai | cuda:ok xpu:preview npu:no
# gpt-5-mini   | openai | cuda:ok xpu:ok npu:preview
# gpt-5-nano   | openai | cuda:ok xpu:ok npu:ok webgpu:preview

# Check backend status
cortex status
# Backend: CUDA (NVIDIA RTX 4090, 24 GB)
# Model: gpt-5-mini
# Tier: gpt5
# Status: ready
```

---

## 9. Rollback Plan

If GPT-5 causes critical regressions:

1. **Disable GPT-5 tier**: `cortex config set tiers.gpt5.enabled false`
2. **Fall back to V2.5**: `cortex config set default.model deepseek-v4-flash`
3. **Remove model_capabilities migration**: `python manage.py migrate api 0007`
4. **Remove GPT-5 from PROVIDER_REGISTRY**: revert `config/settings.py`
5. **Tag release**: `git tag v2.8.0-hotfix-no-gpt5`

User data (sessions, keys, settings) is untouched — only model routing changes.

---

## 10. Summary

GPT-5 compatibility required changes across 7 layers:

1. **GPU Discovery Plugin** — Added GPT-5 VRAM requirements to hardware detection
2. **Provider Registry** — Refactored from flat dict to tiered registry with family/tier metadata
3. **Inference Adapter** — New `OpenAIProvider` with GPT-5-specific tool_calls parsing and streaming
4. **Database Schema** — `model_capabilities` table, user preferences, session tracking
5. **Telemetry Pipeline** — 10 new events, cost tracking, dashboard integration
6. **Installer** — GPT-5 runtime deps added to setup.py
7. **Testing** — 130-test matrix covering smoke, tools, context, streaming, regression

The multi-architecture wiring ensures GPT-5 works on CUDA (production), XPU (preview), NPU (quantized only), WebGPU (nano only), and CPU (nano, slow). The tiered provider registry makes it trivial to add future model families (Claude 4, Gemini 2.5 Pro) without touching the loop engine.
