# Cortex AI Agent — Changelog & Implementation Guide

**Date:** June 28, 2026  
**Version:** Latest Build  

---

## Table of Contents

1. [Grep Tool Fixes](#1-grep-tool-fixes)
2. [Tool Card UI Fixes](#2-tool-card-ui-fixes)
3. [Sidebar Refresh Fix](#3-sidebar-refresh-fix)
4. [Model Registry & Provider System](#4-model-registry--provider-system)
5. [Ollama Provider (Local Models)](#5-ollama-provider-local-models)
6. [API Key Management (Encrypted)](#6-api-key-management-encrypted)
7. [Settings Page Updates](#7-settings-page-updates)

---

## 1. Grep Tool Fixes

### Problem
- Grep found files but didn't show matching lines
- GrepCard showed "0 matches" even when results existed

### Root Cause
- `GrepTool.py` default `output_mode` was `"files_with_matches"` (only filenames)
- `_parse_grep_matches()` in `agent_bridge.py` didn't extract the `text` field

### Files Changed

#### `src/agent/src/tools/GrepTool/GrepTool.py`
```python
# Before:
output_mode = inp.get("output_mode", "files_with_matches")

# After:
output_mode = inp.get("output_mode", "content")
```

#### `src/ai/agent_bridge.py` — `_parse_grep_matches()`
- Now extracts `text` field from `path:line:text` format
- Handles structured match objects with `file/line/text` keys
- Returns `{"file": ..., "line": ..., "path": ..., "text": ...}`

---

## 2. Tool Card UI Fixes

### Problem
- Terminal/Bash/Grep/Glob cards showed border on hover after IDE restart
- Live cards looked different from restored cards

### Root Cause
- `ToolRow.enterEvent()` added `border:1px solid` on hover
- Restored cards in `_update_rich_card()` had `border:1px solid` on command label

### Files Changed

#### `src/ui/chat_panel.py` — `ToolRow` class
```python
# Before:
def enterEvent(self, event):
    self.setStyleSheet(f"border:1px solid {T.get('border_hover', '#444')};border-radius:4px;")

# After:
def enterEvent(self, event):
    self.setStyleSheet(f"background:{T['bg_hover']};border:none;border-radius:4px;")
```

#### `src/ui/chat_panel.py` — `_update_rich_card()` method
```python
# Before:
cmd_lbl.setStyleSheet(
    f"...border:1px solid {T['border']};"
    f"border-radius:2px;"
)

# After:
cmd_lbl.setStyleSheet(
    f"...border:none;"
)
```

---

## 3. Sidebar Refresh Fix

### Problem
- Sidebar refreshed on every tool call (Bash, Edit, etc.)
- Caused UI flicker and performance issues

### Root Cause
- `main_window.py` set `self._sidebar._ai_active = True` on SidebarWidget
- `sidebar_bridge.py` only checked `self._ai_active` on SidebarBridge (not the parent)

### Files Changed

#### `src/ui/components/sidebar_bridge.py` — `_on_file_tree_refresh_needed()`
```python
# Before:
if self._suppress_refresh or self._ai_active or ...:

# After:
_parent = self.parent()
_parent_ai_active = getattr(_parent, '_ai_active', False) if _parent else False
if self._suppress_refresh or self._ai_active or _parent_ai_active or ...:
```

---

## 4. Model Registry & Provider System

### Provider Types (`src/ai/providers/__init__.py`)

```python
class ProviderType(Enum):
    MISTRAL = "mistral"
    SILICONFLOW = "siliconflow"
    DEEPSEEK = "deepseek"
    KIMI = "kimi"
    MIMO = "mimo"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    ALIBABA = "alibaba"
    OLLAMA = "ollama"       # NEW
```

### Model Groups (`src/ai/model_registry.py`)

| Tier | Providers |
|------|-----------|
| **Subscription** | Auto, DeepSeek V4, Xiaomi MiMo |
| **BYOK** | OpenAI, OpenRouter (Anthropic/Google/NVIDIA/GLM), Alibaba Qwen, Ollama |
| **Coming Soon** | Mistral AI, Kimi (Moonshot) |

### Model Routing (`src/ai/agent_bridge.py`)

```python
if model_lower.startswith("ollama/"):
    provider_type = ProviderType.OLLAMA
    model_id = model_lower.split("/", 1)[1]  # Strip "ollama/" prefix
elif "/" in model_lower:
    provider_type = ProviderType.OPENROUTER
# ... etc
```

---

## 5. Ollama Provider (Local Models)

### New File: `src/ai/providers/ollama_provider.py`

#### Features
- Runs models locally via Ollama (no API key needed)
- Auto-detects pulled models via `/api/tags`
- Uses OpenAI-compatible endpoint (`/v1/chat/completions`)
- Streaming support
- Connection health check
- Loads URL from settings (`ai.ollama_url`)

#### Supported Models
| Model | Size | Use Case |
|-------|------|----------|
| llama3.1:8b | 8B | General purpose |
| llama3.1:70b | 70B | Complex reasoning |
| phi3:mini | 3.8B | Fast inference |
| qwen2.5:7b/14b | 7B/14B | Coding |
| codellama:7b | 7B | Code generation |
| deepseek-coder-v2:16b | 16B | Advanced coding |
| mistral:7b | 7B | General purpose |

#### Usage
```bash
# Install Ollama
ollama pull llama3.1:8b
ollama serve

# Select in model selector
ollama/llama3.1:8b
```

#### Configuration
- Default URL: `http://localhost:11434`
- Settings: `ai.ollama_url`
- Environment: `OLLAMA_HOST`

---

## 6. API Key Management (Encrypted)

### Architecture

```
Settings Page → JavaScript → Python Bridge → KeyManager → Encrypted Storage
```

### Storage Backends (`src/core/key_manager.py`)

| Backend | OS | Description |
|---------|-----|-------------|
| Windows Credential Manager | Windows | OS-level encrypted storage |
| Encrypted File | All | `~/.cortex/keys.enc` with Fernet encryption |
| Environment Variables | All | Fallback: `OPENAI_API_KEY`, etc. |

### Encryption Details
```python
# Key derivation
system_data = f"{USERNAME}_{COMPUTERNAME}"
kdf = PBKDF2HMAC(
    algorithm=SHA256,
    length=32,
    salt=b'cortex_salt_v1',
    iterations=100000,
)
key = base64.urlsafe_b64encode(kdf.derive(system_data.encode()))
cipher = Fernet(key)
```

### Key Operations

| Operation | Method | Description |
|-----------|--------|-------------|
| Save | `store_key(provider, api_key)` | Encrypts → stores in backend |
| Load | `get_key(provider)` | Decrypts from backend |
| Delete | `delete_key(provider)` | Removes from all backends |
| Validate | `validate_key(provider, api_key)` | Format validation |

### Provider Key Mapping

| Provider | Env Var | KeyManager Name |
|----------|---------|-----------------|
| OpenAI | `OPENAI_API_KEY` | `openai` |
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek` |
| Mistral | `MISTRAL_API_KEY` | `mistral` |
| Kimi | `MOONSHOT_API_KEY` | `kimi` |
| MiMo | `MIMO_API_KEY` | `mimo` |
| OpenRouter | `OPENROUTER_API_KEY` | `openrouter` |
| Alibaba | `DASHSCOPE_API_KEY` | `alibaba` |
| Ollama | N/A | N/A (no key needed) |

### Flow: Save API Key

```
1. User types key in Settings Page
2. JS calls bridge.setSetting("ai.openai_key", "sk-...")
3. Python memory_manager.setSetting() detects API key field
4. Calls _store_api_key("openai", "sk-...")
5. KeyManager.store_key() encrypts and stores
6. _reload_provider_key() hot-reloads live provider
```

### Flow: Load API Key

```
1. Provider.__init__() calls super().__init__()
2. BaseProvider._load_api_key() runs automatically
3. Tries KeyManager.get_key(provider_name)
4. Falls back to env var if KeyManager fails
5. Sets self._api_key
```

---

## 7. Settings Page Updates

### File: `src/ui/html/memory_manager/memory_management.html`

#### Sections

**Cortex Subscription** (no API key)
- DeepSeek V4 Pro — "Included"
- Xiaomi MiMo — "Included"

**Provider API Keys** (your API key)
- OpenAI (GPT-5.5, GPT-5.4)
- OpenRouter (Anthropic, Google, NVIDIA, GLM)
- Alibaba Qwen (Qwen 3.7 Plus, Flash, Coder)

**Local Models** (no API key)
- Ollama — URL input for local server

**Coming Soon** (disabled)
- Google AI (Gemini)
- Anthropic (Claude)
- xAI (Grok)
- Mistral AI
- Kimi (Moonshot)

### File: `src/ui/html/memory_manager/memory_management.js`

#### Settings Map
```javascript
const SETTINGS_MAP = {
    openaiKey: "ai.openai_key",
    openrouterKey: "ai.openrouter_key",
    alibabaKey: "ai.alibaba_key",
    ollamaUrl: "ai.ollama_url",
    // ... etc
};
```

---

## Testing

### Test Grep
```python
# Should show matching lines with line numbers
grep("permission", path="src/ui/chat_panel.py")
```

### Test Ollama
```bash
# Start Ollama
ollama serve

# Pull a model
ollama pull llama3.1:8b

# Select in Cortex model selector
# ollama/llama3.1:8b
```

### Test API Key Storage
1. Open Settings → Models & Providers
2. Enter OpenAI API key
3. Key should be encrypted in `~/.cortex/keys.enc`
4. Provider should work without restart (hot-reload)

---

## File Summary

| File | Changes |
|------|---------|
| `src/agent/src/tools/GrepTool/GrepTool.py` | Default output_mode → "content" |
| `src/ai/agent_bridge.py` | _parse_grep_matches() extracts text; Ollama routing |
| `src/ui/chat_panel.py` | ToolRow hover border fix; restored card border fix |
| `src/ui/components/sidebar_bridge.py` | Check parent _ai_active flag |
| `src/ai/providers/__init__.py` | Added OLLAMA enum; BaseProvider._load_api_key() |
| `src/ai/providers/ollama_provider.py` | NEW — Full Ollama provider |
| `src/ai/providers/openai_provider.py` | Use BaseProvider key loading |
| `src/ai/providers/alibaba_provider.py` | Use BaseProvider key loading |
| `src/ai/providers/openrouter_provider.py` | Use BaseProvider key loading |
| `src/ai/providers/deepseek_provider.py` | Use BaseProvider key loading |
| `src/ai/providers/mistral_provider.py` | Use BaseProvider key loading |
| `src/ai/providers/kimi_provider.py` | Use BaseProvider key loading |
| `src/ai/model_registry.py` | Added Ollama models; Coming Soon section |
| `src/ui/dialogs/memory_manager.py` | API keys → KeyManager (encrypted) |
| `src/ui/html/memory_manager/memory_management.html` | Updated provider sections |
