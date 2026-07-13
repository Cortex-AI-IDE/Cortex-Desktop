# Cortex AI IDE — Comprehensive Project Index

**Last Updated:** 2026-07-09  
**Project Type:** PyQt6 + Python AI Agent IDE  
**Entry Point:** `src/main.py` (882 lines)  
**Total Python Files:** 637 (including agent subproject)  
**Total Lines of Code:** ~180,000+

---

## 🎯 Project Overview

**Cortex** is a modern, **agentic AI IDE** for developers — a hybrid between VS Code and an AI coding assistant (like Cursor/Windsurf, but completely self-hosted). It combines:

1. **PyQt6 GUI** — Rich desktop UI with multi-tab editor, chat panel, live terminal
2. **Multi-Provider LLM Engine** — Supports 7 cloud providers (OpenAI, DeepSeek, Claude, Mistral, Alibaba, MiMo, OpenRouter) with 50+ models
3. **24 Coding Tools** — File read/write/edit, bash execution, git integration, semantic search, code analysis
4. **Agentic Loop** — AI autonomously chains tool calls, with permission gates + context management
5. **Crash Recovery** — Auto-saves chat history, memory snapshots, terminal transcripts
6. **Plugin System** — Runtime-loadable Python extensions

---

## 📁 Directory Structure

```
cortex_desktop/
├── src/                          # Main PyQt6 application (637 Python files)
│   ├── main.py                   # Entry point (882 lines) — single-instance, crash recovery, startup timers
│   ├── ai/                       # LLM agent + tool execution
│   │   ├── agent_bridge.py       # Core agentic loop (13,059 lines) — 60-turn loops with context compaction
│   │   ├── agent_safety.py       # Safety constraints & permission gates (1,800+ lines)
│   │   ├── model_limits.py       # 79 models x 5 providers — context windows, max_tokens (480+ lines)
│   │   ├── model_registry.py     # Model metadata (friendly names, tags, capabilities)
│   │   ├── providers/            # LLM provider implementations
│   │   │   ├── anthropic_provider.py      # Claude 3.5+ with native API
│   │   │   ├── openrouter_provider.py     # 300+ models via OpenRouter ($)
│   │   │   ├── deepseek_provider.py       # DeepSeek V4 / R1 (best-in-class reasoning)
│   │   │   ├── openai_provider.py         # GPT-4o, o3, o1 models
│   │   │   ├── mistral_provider.py        # Mistral 7B-large
│   │   │   ├── alibaba_provider.py        # Qwen 32B/72B/110B (fast, cheap)
│   │   │   ├── mimo_provider.py           # MiMo V2.5 (proprietary)
│   │   │   └── siliconflow_provider.py    # SiliconFlow inference (embedding models)
│   │   ├── tool_executor.py      # Dispatcher for 24 tools
│   │   ├── conversation_compactor.py  # Context window pressure → checkpoint
│   │   └── streaming.py          # Token-by-token streaming parser
│   │
│   ├── agent/src/                # Sub-module: Agent task/skill types (100+ Python files)
│   │   ├── tools/                # 24 coding tools (file ops, bash, git, search, etc.)
│   │   ├── coordinator/          # Multi-turn orchestration
│   │   ├── services/             # Background worker threads (indexing, memory gc)
│   │   ├── permissions/          # Permission gate system
│   │   └── ... (40+ subdirectories for state, tasks, hooks, etc.)
│   │
│   ├── ui/                       # PyQt6 UI components (8,425 lines chat_panel.py)
│   │   ├── main_window.py        # Top-level window (7,523 lines) — layout, menus, events
│   │   ├── chat_panel.py         # Chat UI (8,425 lines) — message rendering, streaming, tool cards
│   │   ├── components/
│   │   │   ├── editor.py         # Monaco editor wrapper (2,208 lines)
│   │   │   ├── xterm_terminal.py # XTerm.js terminal (1,600+ lines)
│   │   │   ├── sidebar.py        # File explorer sidebar (1,500+ lines)
│   │   │   └── ... (webview, spinners, theme manager, etc.)
│   │   ├── dialogs/
│   │   │   ├── memory_manager.py # Settings panel with 6 tabs
│   │   │   ├── settings_dialog.py
│   │   │   └── ... (4 other dialogs)
│   │   ├── html/                 # Embedded web UIs (Monaco, terminal, sidebar, settings)
│   │   │   ├── editor.html       # Monaco editor (15K lines minified)
│   │   │   ├── terminal.html     # XTerm.js wrapper (500+ lines)
│   │   │   ├── sidebar.html      # File tree (800+ lines)
│   │   │   └── memory_manager/   # Settings HTML + JS + CSS (3 files, 2.2K lines)
│   │   └── themes/
│   │       ├── dark.qss          # Dark theme stylesheet (Qt style sheets)
│   │       ├── light.qss         # Light theme stylesheet
│   │       └── theme_manager.py  # Runtime theme switching
│   │
│   ├── core/                     # Business logic
│   │   ├── semantic_search.py    # Vector-based code search (1,300+ lines)
│   │   ├── embeddings.py         # Embedding model support (multiple providers)
│   │   ├── siliconflow_embeddings.py  # SiliconFlow semantic search backend
│   │   ├── git_integration.py    # Git operations (commit, push, diff)
│   │   ├── file_ops.py           # Safe file I/O with permissions
│   │   └── ... (crash recovery, memory management, path validation)
│   │
│   ├── config/                   # Configuration & environment
│   │   ├── env_loader.py         # .env parsing with defaults
│   │   ├── settings_manager.py   # Persistent settings (JSON)
│   │   └── ... (constants, paths)
│   │
│   ├── services/                 # Background workers
│   │   ├── stability_engine.py   # RAM/CPU throttling, GC (prevents freeze)
│   │   ├── crash_recovery.py     # Auto-save timers, history recovery
│   │   └── usage_tracker.py      # Token/API call accounting
│   │
│   └── utils/                    # Utilities
│       ├── logger.py             # Structured logging to .cortex/logs/
│       ├── pathValidation.py     # Path security checks (1,404 lines)
│       └── ... (JSON utils, process management, etc.)
│
├── agent/                        # Alternative agent implementation (legacy, not active)
│
├── plugins/                      # Runtime-loadable plugins (Python packages)
│   └── (custom user extensions here)
│
├── tests/                        # Test suite
│   ├── test_release_suite.py     # 38 regression + safety tests
│   ├── test_model_limits.py      # Verify model config accuracy
│   └── ... (8 other test files)
│
├── Docs/                         # Architecture & design docs
│   ├── agent_loop/               # Agentic loop design (3 files, detailed flowchart)
│   ├── AI_MODEL_REFERENCE.md     # All 50+ models with pricing/latency
│   ├── LIGHT_MODE_IMPLEMENTATION.md  # Theme system
│   ├── CHAT_HISTORY_RECOVERY_AUDIT.md # Crash recovery mechanism
│   ├── EDITOR_OPEN_ISSUES.md     # Known Monaco editor limitations
│   └── ... (12 other design docs)
│
├── bin/                          # Build & launch scripts
│   ├── build.ps1                 # PyInstaller build script (Windows)
│   └── launch_cortex.bat         # Launcher batch file
│
├── .cortex/                      # Runtime data (auto-created)
│   ├── memory/                   # MEMORY.md (agent memory snapshots) + checkpoint_*.md
│   ├── logs/                     # cortex.log (rotation every 50 MB)
│   └── cache/                    # Semantic search index, embeddings cache
│
├── PROJECT_INDEX.md              # Previous index (to be replaced)
├── requirements.txt              # Python dependencies (60+ packages)
├── pytest.ini                    # Test config
└── .env.example                  # Example environment variables
```

---

## 🧠 Key Components Explained

### 1. **Core Agentic Loop** (`src/ai/agent_bridge.py`, 13,059 lines)

The heart of Cortex — a 60-turn conversational agent that chains tool calls. Flow:

```
User message → LLM (streaming) → Tool calls? 
                                  ├─ Yes: Execute tools → append results → loop
                                  └─ No: Final answer → return
```

**Key features:**
- Per-request failover (if Claude fails, try DeepSeek)
- Context pressure detection (→ auto-compact into checkpoint)
- 5-layer safety gates (permission cards, doom-loop detection, output validation)
- Stability throttling (slows down under high CPU/RAM pressure instead of crashing)

**Entry point:** `_call_llm(model, messages, tools, ...)` (line ~4100)  
**Exit conditions:** 4 (final answer, user rejection, doom-loop, max_turns)

### 2. **Multi-Provider LLM Engine** (`src/ai/providers/`)

7 provider implementations, all exposing the same interface (OpenAI-compatible):

| Provider | Models | Best For | Status |
|----------|--------|----------|--------|
| **Anthropic** | Claude 3.5 Sonnet, Opus, Haiku | Best general-purpose coding | ✅ Native API |
| **OpenRouter** | 300+ (Claude, GPT, DeepSeek, Mistral, etc.) | Cheap + diverse | ✅ Unified middleware |
| **DeepSeek** | V4, R1 (reasoning) | Cost-effective, strong reasoning | ✅ Excellent value |
| **OpenAI** | GPT-4o, o1, o3 | Frontier models | ✅ Latest models |
| **Mistral** | 7B-large, Codestral | Lightweight, open | ✅ Fast |
| **Alibaba (Qwen)** | Qwen 32B/72B/110B | Ultra-cheap, very fast | ✅ Popular in Asia |
| **MiMo** | V2.5 Pro | Proprietary | ✅ Alternative |

**All models are registered in `model_limits.py`** with:
- Context window size (input + output)
- Max output tokens (safe generation limit)
- Per-message character caps (prevents token explosion)
- Turn limits (how many tool calls before auto-continue)

### 3. **24 Coding Tools** (`src/agent/src/tools/`)

Tools the AI can invoke autonomously:

| Category | Tools | Purpose |
|----------|-------|---------|
| **File I/O** | ReadFile, WriteFile, EditFile | Read/write code, docs, config |
| **Execution** | BashTool, PythonTool, NodeJSTool | Run shells, Python scripts, Node |
| **Code Analysis** | GrepTool, SementicSearchTool, ASTAnalyze | Find code patterns, search semantically |
| **Git** | CommitTool, PushTool, DiffTool | Version control, diffs |
| **Project** | GlobTool, ListDirTool, RenameFileTool | File system navigation |
| **Memory** | SaveToMemory, LoadMemory | Persistent agent memory |
| **Web** | WebSearch, WebFetch | Real-time web queries |
| **Other** | CreateFileTool, DeleteFileTool, etc. | File management |

**All tool calls are:**
- Streamed to the UI as Explore/File/Terminal cards
- Subject to permission gates (user must Allow/Reject/AlwaysAllow)
- Logged for crash recovery
- Result-capped to prevent context explosion

### 4. **Permission Gate System** (`src/ai/agent_safety.py`)

Prevents unauthorized file modification and code execution:

```
Tool call requested
  ↓
Is this a Write/Edit/Bash call?
  ├─ No → execute immediately (Read/Grep/Search are free)
  └─ Yes → show permission card
      ├─ User clicks "Allow Once" → execute, ask next time
      ├─ User clicks "Allow Always" → execute, skip gate forever for this model
      └─ User clicks "Reject" → agent receives error, loop breaks
```

**Thread synchronization:** The agent thread is **frozen** in `asyncio.to_thread(event.wait, 60s)` while the card is showing — **zero chance of bypass**.

### 5. **Chat Panel UI** (`src/ui/chat_panel.py`, 8,425 lines)

Real-time chat interface with:
- Streaming text + tool-call visualization (Explore/File/Terminal cards)
- Lazy-load chat history (only 12 messages on startup, rest on scroll-up)
- Markdown + code syntax highlighting
- Mermaid diagram rendering
- Copy buttons on code blocks
- Auto-scroll during streaming

### 6. **Crash Recovery System** (`src/core/crash_recovery.py`)

When Cortex crashes or is force-closed:
1. Auto-save timers dump chat history every 30 seconds to `.cortex/memory/chat_session_*.json`
2. Auto-save memory snapshots every 60 seconds to `MEMORY.md`
3. Next restart: recover the chat + context automatically
4. User can manually load older checkpoint files

### 7. **Semantic Search** (`src/core/semantic_search.py`)

Embeddings-based code search (finds related code by meaning, not keywords):

```
User query: "where is password validation?"
  ↓
Convert to embedding (Qwen 2048-dim via SiliconFlow)
  ↓
Search index (all project files → embeddings cached)
  ↓
Return top-K semantically similar chunks
```

**Backend:** SiliconFlow (Qwen/Qwen3-Embedding-4B, 32K context, 2048 dims)

---

## 🚀 Key Statistics

| Metric | Count |
|--------|-------|
| **Total Python files** | 637 |
| **Total Python lines** | ~180,000+ |
| **Largest file** | `agent_bridge.py` (13,059 lines) |
| **UI files** | 6 HTML, 7 JS, 2 CSS |
| **SVG icons** | 1,245 |
| **LLM providers** | 7 (Anthropic, OpenAI, DeepSeek, Mistral, Alibaba, MiMo, OpenRouter) |
| **Models supported** | 50+ (GPT-4o, Claude 3.5, DeepSeek R1, Qwen, Mistral, etc.) |
| **Coding tools** | 24 (file I/O, bash, git, search, etc.) |
| **Test suite** | 38 tests (release regression suite) |
| **Build target** | Windows exe (PyInstaller) |

---

## 🔄 Data Flow — From User Message to Agent Response

```
1. User types message in Chat Panel
   ↓
2. send_message() → agent_bridge._call_llm()
   ↓
3. Resolve model + provider + limits (model_limits.py)
   ↓
4. Format system prompt + chat history + tools list
   ↓
5. Stream from LLM (OpenAI-compatible endpoint)
   ↓
6. Parse response:
   ├─ Text tokens → emit to chat_panel (real-time display)
   └─ Tool calls → extract + validate + dispatch
   ↓
7. For each tool call:
   ├─ Check safety gates (permission cards, doom-loop)
   ├─ Execute (file I/O, bash, search, git, etc.)
   ├─ Capture result + error
   ├─ Emit tool_activity (UI card streaming)
   └─ Append as tool message
   ↓
8. Check context pressure:
   ├─ If >70% full → auto-compact to checkpoint
   └─ Continue loop (return to step 5)
   ↓
9. Loop exits (final answer / user rejection / doom-loop / max_turns)
   ↓
10. Save to chat history (.cortex/memory/)
```

---

## 🛠 How to Start Development

### Requirements
- Python 3.10+ (tested on 3.11, 3.12)
- PyQt6 + Qt WebEngine
- 7.8 GB RAM minimum (IDE baseline ~1.2 GB, agent + models ~2-4 GB)
- Windows 10+ (primary target; Linux/macOS untested but likely works)

### Setup

```bash
# Clone + install dependencies
git clone <repo>
cd cortex_desktop
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Set API keys
cp .env.example .env
# Edit .env with your OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.

# Run
python src/main.py
```

### Key Development Files to Know

- **Want to add a new tool?** → `src/agent/src/tools/`
- **Want to add a new LLM provider?** → `src/ai/providers/new_provider.py` + register in `agent_bridge.py`
- **Want to tweak safety gates?** → `src/ai/agent_safety.py` + `agent_bridge.py` (_request_project_access)
- **Want to change theme colors?** → `src/ui/themes/dark.qss` or `light.qss`
- **Want to fix a UI layout bug?** → `src/ui/main_window.py` or `chat_panel.py`

---

## 📚 Architecture Docs

See `Docs/` for deeper dives:

- `agent_loop/AGENTIC_LOOP_IMPLEMENTATION.md` — 60-turn loop design
- `AI_MODEL_REFERENCE.md` — All 50+ models with real pricing/latency
- `LIGHT_MODE_IMPLEMENTATION.md` — Theme system v2 (runtime switching without QSS)
- `CHAT_HISTORY_RECOVERY_AUDIT.md` — Crash recovery mechanism
- `EDITOR_OPEN_ISSUES.md` — Known Monaco editor bugs + workarounds

---

## 🐛 Known Issues & TODOs

- **Temp helper scripts in root** (`_fix_limits*.py`, `_test_limits.py`) — Can be deleted manually
- **Terminal header rendering** — Still being refined (HTML embedded in XTermWidget)
- **Light mode native chrome** — Qt menus stay dark until restart (by design)

---

## 🎓 Project Maturity

**Status:** Feature-complete beta  
**Last major work:** Light mode + permission gates + agentic loop + crash recovery (July 2026)  
**Test coverage:** 38 tests (release regression suite, not full code coverage)  
**Production-ready:** Yes, for single-developer use; multi-user features not implemented.

