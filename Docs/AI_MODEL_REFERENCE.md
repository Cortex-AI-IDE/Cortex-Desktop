# AI Model Reference — Cortex Engine

Complete inventory of all AI models, providers, API endpoints, and configurations.

---

## Provider Registry

8 registered providers in `src/ai/providers/__init__.py`:

| Provider | Enum | API Key Env | Base URL |
|---|---|---|---|
| DeepSeek V4 | `DEEPSEEK` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/v1` |
| Xiaomi MiMo | `MIMO` | `MIMO_API_KEY` | `https://api.xiaomimimo.com/v1` (sk-) or `https://token-plan-sgp.xiaomimimo.com/v1` (tp-) |
| OpenAI GPT | `OPENAI` | `OPENAI_API_KEY` | `https://api.openai.com/v1` |
| Mistral AI | `MISTRAL` | `MISTRAL_API_KEY` | `https://api.mistral.ai/v1` |
| OpenRouter | `OPENROUTER` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` |
| Alibaba DashScope | `ALIBABA` | `DASHSCOPE_API_KEY` / `QWEN_API_KEY` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` |
| Kimi/Moonshot | `KIMI` | `MOONSHOT_API_KEY` | `https://api.moonshot.ai/v1` |
| SiliconFlow | `SILICONFLOW` | `SILICONFLOW_API_KEY` | `https://api.siliconflow.com/v1` |

All endpoints use `/chat/completions` suffix. SiliconFlow also uses `/embeddings`.

---

## Chat/Completion Models

### DeepSeek (`src/ai/providers/deepseek_provider.py`)

| Model ID | Display Name | Context | Max Output |
|---|---|---|---|
| `deepseek-v4-pro` | DeepSeek V4 Pro | 1,000,000 | 131,072 |
| `deepseek-v4-flash` | DeepSeek V4 Flash | 1,000,000 | 131,072 |

- Default: `deepseek-v4-flash`

### MiMo / Xiaomi (`src/ai/providers/mimo_provider.py`)

| Model ID | Display Name | Context | Max Output |
|---|---|---|---|
| `mimo-v2.5-pro` | MiMo V2.5 Pro (Agentic) | 1,048,576 | 131,072 |
| `mimo-v2.5` | MiMo V2.5 (Full-Modal) | 1,048,576 | 131,072 |

- Default: `mimo-v2.5-pro`
- Dual-host: `tp-*` keys → `token-plan-sgp.xiaomimimo.com`, `sk-*` keys → `api.xiaomimimo.com`
- Flash only available for `sk-*` keys
- Native web search tool built-in

### OpenAI (`src/ai/providers/openai_provider.py`)

| Model ID | Display Name | Context | Max Output |
|---|---|---|---|
| `gpt-5.5` | GPT-5.5 | 1,050,000 | 128,000 |
| `gpt-5.4` | GPT-5.4 | 1,050,000 | 128,000 |

- GPT-5.4 uses `reasoning_effort` param (env: `CORTEX_OPENAI_REASONING_EFFORT`, default "medium")
- GPT-5.5 uses `reasoning_effort` param (env: `CORTEX_OPENAI_REASONING_EFFORT`, default "medium")
- GPT-5.x uses `max_completion_tokens` instead of `max_tokens`

### Mistral (`src/ai/providers/mistral_provider.py`)

| Model ID | Display Name | Context | Max Output |
|---|---|---|---|
| `mistral-large-latest` | Mistral Large | 128,000 | 8,192 |

- Used as OCR/vision fallback
- Default provider in settings (`src/config/settings.py` line 49)

### OpenRouter (`src/ai/providers/openrouter_provider.py`)

Access to 300+ models via single API key:

| Model ID | Display Name |
|---|---|
| `anthropic/claude-opus-4-8` | Claude Opus 4.8 |
| `anthropic/claude-opus-4-5` | Claude Opus 4.5 |
| `anthropic/claude-sonnet-4-5` | Claude Sonnet 4.5 |
| `anthropic/claude-haiku-4-5` | Claude Haiku 4.5 |
| `openai/gpt-5.1-codex-max` | GPT-5.1 Codex Max |
| `openai/gpt-5.1-codex` | GPT-5.1 Codex |
| `openai/gpt-4o` | GPT-4o |
| `openai/gpt-4o-mini` | GPT-4o Mini |
| `openai/o3` | OpenAI o3 |
| `deepseek/deepseek-chat-v3.1` | DeepSeek V3.1 |
| `deepseek/deepseek-r1` | DeepSeek R1 |
| `deepseek/deepseek-v4-pro` | DeepSeek V4 Pro |
| `deepseek/deepseek-v4-flash` | DeepSeek V4 Flash |
| `deepseek/deepseek-v4-flash:free` | DeepSeek V4 Flash (Free) |
| `xiaomi/mimo-v2.5-pro` | MiMo V2.5 Pro |
| `xiaomi/mimo-v2.5-flash` | MiMo V2.5 Flash |
| `xiaomi/mimo-v2.5-flash:free` | MiMo V2.5 Flash (Free) |
| `qwen/qwen3-coder` | Qwen3 Coder (Free) |
| `qwen/qwen3.7-plus` | Qwen 3.7 Plus |
| `qwen/qwen3.7-max` | Qwen 3.7 Max |
| `google/gemini-2.5-pro` | Gemini 2.5 Pro |
| `google/gemini-2.5-flash` | Gemini 2.5 Flash |
| `z-ai/glm-5.1` | GLM-5.1 |
| `z-ai/glm-5` | GLM-5 |
| `z-ai/glm-5-turbo` | GLM-5 Turbo |
| `z-ai/glm-4.5-air:free` | GLM-4.5 Air (Free) |
| `nvidia/nemotron-3-ultra-550b-a55b` | Nemotron 3 Ultra |
| `minimax/minimax-m3` | MiniMax M3 |

- Default: `anthropic/claude-haiku-4-5`

### Alibaba / DashScope — Qwen (`src/ai/providers/alibaba_provider.py`)

| Model ID | Display Name | Context | Max Output |
|---|---|---|---|
| `qwen3.7-plus` | Qwen 3.7 Plus (Agentic Flagship) | 1,000,000 | 32,768 |
| `qwen3.6-plus` | Qwen 3.6 Plus (Agentic) | 1,000,000 | 32,768 |
| `qwen3-coder-plus` | Qwen3 Coder Plus | 1,000,000 | 65,536 |
| `qwen-flash` | Qwen Flash | 1,000,000 | 32,768 |
| `qwen-turbo` | Qwen Turbo | 1,000,000 | 8,192 |

- Configurable base URL via `DASHSCOPE_BASE_URL` env var
- Thinking support: `enable_thinking` + `thinking_budget` (env: `CORTEX_QWEN_THINKING_BUDGET`, default 4096)

### Kimi / Moonshot (`src/ai/providers/kimi_provider.py`)

| Model ID | Display Name | Context | Max Output |
|---|---|---|---|
| `kimi-k2.6` | Kimi K2.6 (Multimodal) | 262,144 | 32,768 |

- Multimodal: text/image/video, thinking mode
- Temperature fixed at 1.0

### SiliconFlow — Vision (`src/ai/providers/siliconflow_provider.py`)

| Model ID | Display Name | Context | Max Output |
|---|---|---|---|
| `Qwen/Qwen3-VL-32B-Instruct` | Qwen3-VL-32B (Vision) | 32,000 | 4,000 |
| `Qwen/Qwen3-VL-8B-Instruct` | Qwen3-VL-8B (Vision, Fast) | 32,000 | 4,000 |
| `Qwen/Qwen2.5-VL-72B-Instruct` | Qwen2.5-VL-72B (Vision) | 32,000 | 4,000 |

---

## Embedding Models

### SiliconFlow Embeddings (`src/core/siliconflow_embeddings.py`)

| Model ID | Dimensions | Quality |
|---|---|---|
| `Qwen/Qwen3-Embedding-0.6B` | 1,024 | Fast |
| `Qwen/Qwen3-Embedding-4B` | 2,560 | Balanced (default) |
| `Qwen/Qwen3-Embedding-8B` | 4,096 | Best |

- Endpoint: `https://api.siliconflow.com/v1/embeddings`
- Purpose: Semantic search / code embeddings

### Local Embeddings (`src/core/embeddings.py`)

| Model ID | Dimensions | Backend |
|---|---|---|
| `all-MiniLM-L6-v2` | 384 | sentence-transformers (local) |
| `all-mpnet-base-v2` | 768 | sentence-transformers (local) |
| `hash-fallback` | 384 | Deterministic hash (no deps) |

Priority: SiliconFlow API → sentence-transformers → hash fallback.

---

## Model Limits Registry (`src/ai/model_limits.py`)

| Pattern | Context Window | Max Output |
|---|---|---|
| `deepseek-v4-pro` | 1,000,000 | 131,072 |
| `deepseek` (catch-all) | 1,000,000 | 131,072 |
| `gpt-5.5` | 1,050,000 | 128,000 |
| `gpt-5.4` | 1,050,000 | 128,000 |
| `anthropic/claude-opus-4-8` | 1,000,000 | 65,536 |
| `anthropic/claude-opus-4-5` | 1,000,000 | 65,536 |
| `anthropic/claude-sonnet-4-5` | 1,000,000 | 65,536 |
| `anthropic/claude-haiku-4-5` | 1,000,000 | 65,536 |
| `anthropic/claude` (catch-all) | 200,000 | 16,384 |
| `qwen3-coder` | 1,000,000 | 65,536 |
| `qwen3.7-plus` | 1,000,000 | 32,768 |
| `qwen3.6-plus` | 1,000,000 | 32,768 |
| `qwen-flash` | 1,000,000 | 32,768 |
| `qwen-vl-max` | 32,768 | 8,192 |
| `qwen-turbo` | 1,000,000 | 8,192 |
| `qwen` (catch-all) | 32,000 | 8,192 |
| `google/gemini-2.5-pro` | 1,000,000 | 65,536 |
| `google/gemini-2.5-flash` | 1,000,000 | 32,768 |
| `gemini` (catch-all) | 1,000,000 | 65,536 |
| `mistral-large` | 128,000 | 32,768 |
| `mistral` (catch-all) | 128,000 | 32,768 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 1,000,000 | 65,536 |
| `kimi-k2.6` | 262,144 | 32,768 |
| `mimo-v2.5-pro` | 1,048,576 | 131,072 |
| `mimo-v2.5` | 1,048,576 | 131,072 |
| `mimo` (catch-all) | 1,048,576 | 65,536 |
| **DEFAULT** | **500,000** | **8,192** |

---

## Model Registry — UI Dropdown (`src/ai/model_registry.py`)

Models shown in the chat model selector:

| Model ID | Display Name | Color |
|---|---|---|
| `auto` | Auto (Smart routing) | `#2196f3` |
| `deepseek-v4-pro` | DeepSeek V4 Pro | `#a78bfa` |
| `mimo-v2.5-pro` | MiMo V2.5 Pro | `#ff6900` |
| `mimo-v2.5` | MiMo V2.5 | `#ff6900` |
| `gpt-5.5` | GPT-5.5 | `#10a37f` |
| `gpt-5.4` | GPT-5.4 | `#10a37f` |
| `anthropic/claude-opus-4-8` | Claude Opus 4.8 | `#d77b4a` |
| `anthropic/claude-opus-4-5` | Claude Opus 4.5 | `#d77b4a` |
| `anthropic/claude-sonnet-4-5` | Claude Sonnet 4.5 | `#d77b4a` |
| `anthropic/claude-haiku-4-5` | Claude Haiku 4.5 | `#d77b4a` |
| `google/gemini-2.5-pro` | Gemini 2.5 Pro | `#4285f4` |
| `google/gemini-2.5-flash` | Gemini 2.5 Flash | `#4285f4` |
| `nvidia/nemotron-3-ultra-550b-a55b` | Nemotron 3 Ultra | `#76b900` |
| `qwen3.7-plus` | Qwen 3.7 Plus | `#f59e0b` |
| `qwen3.6-plus` | Qwen 3.6 Plus | `#f59e0b` |
| `qwen3-coder-plus` | Qwen3 Coder Plus | `#f59e0b` |
| `qwen-flash` | Qwen Flash | `#f59e0b` |
| `qwen-turbo` | Qwen Turbo | `#f59e0b` |

---

## Multi-Agent Orchestrator (`src/ai/maf_multi_agent.py`)

Endpoints used for multi-agent fallback routing:

| Provider | Base URL | Env Key |
|---|---|---|
| kimi | `https://api.moonshot.ai/v1` | `MOONSHOT_API_KEY` |
| deepseek | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| mistral | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` |
| codestral | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` |
| openai | `https://api.openai.com/v1` | `OPENAI_API_KEY` |

---

## Key Storage (`src/core/key_manager.py`)

Priority order:
1. Environment variables
2. Windows Credential Manager
3. Encrypted file (`~/.cortex/keys.enc`)

Supported providers for env lookup: `openai`, `anthropic`, `deepseek`, `google`, `openrouter`

---

## Dependencies (`requirements.txt`)

| Package | Version | Purpose |
|---|---|---|
| `openai` | 2.38.0 | OpenAI SDK |
| `mistralai` | 2.4.8 | Mistral SDK |
| `anthropic` | 0.80.0 | Anthropic SDK |
| `litellm` | 1.80.10 | Multi-provider LLM gateway |
| `tiktoken` | 0.12.0 | Token counting |
| `numpy` | 2.4.6 | Embedding math |
| `huggingface_hub` | 1.16.1 | Model downloads |

---

## Defaults

- **Default provider:** Mistral (`mistral-large-latest`) — `src/config/settings.py:49`
- **Default embedding:** `Qwen/Qwen3-Embedding-4B` via SiliconFlow
- **Fallback embedding:** `all-MiniLM-L6-v2` (local) → hash fallback
- **Default context window:** 500,000 tokens (unknown models)
- **Default max output:** 8,192 tokens (unknown models)

---

## Total Count

- **8 providers** (DeepSeek, MiMo, OpenAI, Mistral, OpenRouter, Alibaba/Qwen, Kimi, SiliconFlow)
- **45 unique chat model IDs** across all providers
- **6 embedding model IDs** (3 cloud + 3 local)
- **10 API endpoints** (chat + embeddings)
