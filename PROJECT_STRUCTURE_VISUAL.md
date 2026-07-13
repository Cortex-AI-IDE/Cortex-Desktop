# Cortex AI IDE — Visual Architecture

## System Architecture Diagram

```mermaid
graph TB
    subgraph UI["🖥️ PyQt6 GUI Layer"]
        MW["main_window.py<br/>(7,523 lines)<br/>Top-level window, menus, layout"]
        CP["chat_panel.py<br/>(8,425 lines)<br/>Chat streaming, tool cards"]
        ED["editor.py<br/>(2,208 lines)<br/>Monaco editor wrapper"]
        TM["xterm_terminal.py<br/>(1,600 lines)<br/>XTerm.js terminal"]
        SB["sidebar.py<br/>(1,500 lines)<br/>File explorer"]
        SM["settings_dialog.py<br/>memory_manager.py<br/>Theme manager"]
    end

    subgraph Agent["🧠 AI Agent Core"]
        AB["agent_bridge.py<br/>(13,059 lines)<br/>60-turn agentic loop<br/>Tool dispatch<br/>Context management"]
        AS["agent_safety.py<br/>(1,800 lines)<br/>Permission gates<br/>Doom-loop detection<br/>Output validation"]
        ML["model_limits.py<br/>(480 lines)<br/>79 models x 7 providers<br/>Context windows<br/>max_output_tokens"]
        MR["model_registry.py<br/>Model metadata<br/>Pricing, capabilities"]
    end

    subgraph Providers["☁️ LLM Providers"]
        AN["Anthropic<br/>Claude 3.5+<br/>Native API"]
        OR["OpenRouter<br/>300+ models<br/>Unified"]
        DS["DeepSeek<br/>V4, R1<br/>Reasoning"]
        OA["OpenAI<br/>GPT-4o, o3<br/>Frontier"]
        MI["Mistral<br/>7B-large<br/>Lightweight"]
        AL["Alibaba<br/>Qwen 32B+<br/>Ultra-cheap"]
        MM["MiMo<br/>V2.5 Pro<br/>Proprietary"]
    end

    subgraph Tools["🛠️ 24 Coding Tools"]
        FI["File I/O<br/>ReadFile, WriteFile<br/>EditFile"]
        EX["Execution<br/>BashTool, PythonTool<br/>NodeJSTool"]
        CA["Code Analysis<br/>GrepTool<br/>SementicSearch"]
        GIT["Git Ops<br/>CommitTool<br/>DiffTool"]
        PR["Project<br/>GlobTool<br/>RenameFile"]
    end

    subgraph Core["📦 Core Services"]
        SS["semantic_search.py<br/>Embeddings-based<br/>code search<br/>(Qwen 2048-dim)"]
        CR["crash_recovery.py<br/>Auto-save chat<br/>Memory snapshots<br/>History recovery"]
        ST["stability_engine.py<br/>RAM/CPU throttling<br/>Prevents freeze"]
        CC["conversation_compactor.py<br/>Context pressure<br/>→ checkpoint"]
    end

    subgraph Data["💾 Data & Storage"]
        MEM[".cortex/memory/<br/>MEMORY.md<br/>checkpoint_*.md<br/>Agent memory snapshots"]
        LOGS[".cortex/logs/<br/>cortex.log<br/>Structured logging"]
        CACHE[".cortex/cache/<br/>Embeddings cache<br/>Semantic index"]
    end

    UI -->|Sends messages| AB
    AB -->|Routes to| Providers
    AB -->|Dispatches| Tools
    AB -->|Reads limits from| ML
    AB -->|Checks safety via| AS
    Providers -->|Stream tokens| UI
    Tools -->|Execute on project| EX
    Tools -->|Search code| SS
    Tools -->|Manage project| PR
    Tools -->|Git ops| GIT
    Core -->|Compacts context| AB
    Core -->|Recovers crashes| UI
    Core -->|Throttles on pressure| AB
    AB -->|Saves state to| Data
    CR -->|Auto-saves to| Data
    MEM -->|Feeds agent memory| AB
    SS -->|Caches to| CACHE
```

---

## Data Flow — User Message to Agent Response

```mermaid
sequenceDiagram
    actor User
    participant Chat as Chat Panel
    participant Agent as agent_bridge<br/>_call_llm()
    participant Provider as LLM Provider
    participant Tools as Tool Executor
    participant UI as UI Cards

    User->>Chat: Type message
    Chat->>Agent: send_message(model, text)
    Agent->>Agent: Reset per-request state<br/>Resolve model+provider<br/>Get limits from model_limits.py
    Agent->>Provider: Stream LLM request<br/>(system prompt + history + tools)
    Provider-->>Agent: Token stream
    Agent->>Chat: Emit text tokens (real-time)
    Chat->>UI: Display streaming text
    
    alt LLM returns tool calls
        Agent->>Agent: Parse tool calls
        Agent->>Agent: Check safety gates<br/>(permission cards, doom-loop)
        Agent->>Tools: Execute each tool
        Tools->>Tools: Read/Write/Edit/Bash/Grep/Search
        Tools-->>Agent: Return results
        Agent->>UI: Emit tool_activity cards
        Agent->>Agent: Append tool results<br/>as messages
        Agent->>Agent: Check context pressure<br/>If >70%: compact
        Agent->>Provider: Continue loop (step 6)
    else LLM returns plain text
        Agent->>Chat: Final answer
        Chat->>UI: Display message
    end
    
    Agent->>Agent: Exit loop<br/>(final answer / rejection / doom-loop / max_turns)
    Agent->>Data: Save to MEMORY.md
```

---

## Agentic Loop State Machine

```mermaid
stateDiagram-v2
    [*] --> ResetState: _call_llm() entry
    ResetState --> ResolveLLM: Get model+provider
    ResolveLLM --> GetLimits: Load from model_limits.py
    GetLimits --> TurnLoop: for turn in range(max_turns)
    
    TurnLoop --> CheckStability: Check RAM/CPU
    CheckStability -->|HIGH/CRITICAL| Throttle: Sleep 2.5-8s\nMicro-compact
    Throttle --> StreamLLM
    CheckStability -->|Normal| StreamLLM
    
    StreamLLM --> StreamLLM: Accumulate tokens\nParse tool deltas
    StreamLLM --> HasTools{Tool calls?}
    
    HasTools -->|No| FinalAnswer: Return text
    HasTools -->|Yes| CheckSafety: Permission gates\nDoom-loop check
    
    CheckSafety -->|Blocked| Break: Inject error\nBreak loop
    CheckSafety -->|OK| ExecuteTools: Dispatch tools
    
    ExecuteTools --> ExecuteTools: Read/Write/Edit/Bash/Grep\nEmit tool_activity cards
    ExecuteTools --> AppendResults: Add to messages
    
    AppendResults --> CheckPressure: Context >70%?
    CheckPressure -->|Yes| Compact: Auto-compact\n→ checkpoint
    CheckPressure -->|No| ContinueLoop
    Compact --> ContinueLoop
    
    ContinueLoop --> TurnLoop: Continue turn loop
    TurnLoop --> MaxTurns{max_turns\nexhausted?}
    
    MaxTurns -->|Yes| AutoContinue: If todos pending:\nauto-continue cycle
    MaxTurns -->|No| TurnLoop
    AutoContinue --> [*]
    
    FinalAnswer --> [*]
    Break --> [*]
```

---

## Project File Organization

```
cortex_desktop/
├── src/                              (637 Python files, ~180K lines)
│
├── src/ai/                           LLM Agent + Tool Execution
│   ├── agent_bridge.py               (13,059 lines) ⭐ Core agentic loop
│   ├── agent_safety.py               (1,800 lines)  Safety gates
│   ├── model_limits.py               (480 lines)    79 models x 7 providers
│   ├── providers/                    (7 provider implementations)
│   │   ├── anthropic_provider.py
│   │   ├── openrouter_provider.py
│   │   ├── deepseek_provider.py
│   │   ├── openai_provider.py
│   │   ├── mistral_provider.py
│   │   ├── alibaba_provider.py
│   │   └── mimo_provider.py
│   └── tool_executor.py              Dispatcher for 24 tools
│
├── src/ui/                           PyQt6 GUI Components
│   ├── main_window.py                (7,523 lines)  Top-level window
│   ├── chat_panel.py                 (8,425 lines)  Chat UI + streaming
│   ├── components/
│   │   ├── editor.py                 (2,208 lines)  Monaco editor
│   │   ├── xterm_terminal.py         (1,600 lines)  Terminal
│   │   ├── sidebar.py                (1,500 lines)  File explorer
│   │   └── ...
│   └── themes/
│       ├── dark.qss
│       ├── light.qss
│       └── theme_manager.py
│
├── src/core/                         Business Logic
│   ├── semantic_search.py            Code search (embeddings)
│   ├── embeddings.py                 Embedding model support
│   ├── siliconflow_embeddings.py     SiliconFlow backend
│   ├── crash_recovery.py             Auto-save + recovery
│   ├── git_integration.py            Git operations
│   └── ...
│
├── src/agent/src/                    Agent Sub-module (100+ files)
│   ├── tools/                        24 coding tools
│   ├── coordinator/                  Multi-turn orchestration
│   ├── permissions/                  Permission gates
│   ├── services/                     Background workers
│   └── ...
│
├── tests/                            Test Suite
│   ├── test_release_suite.py         (38 tests)
│   └── ...
│
├── Docs/                             Architecture Documentation
│   ├── agent_loop/                   Agentic loop design
│   ├── AI_MODEL_REFERENCE.md         All 50+ models
│   ├── LIGHT_MODE_IMPLEMENTATION.md
│   └── ...
│
├── .cortex/                          Runtime Data
│   ├── memory/                       MEMORY.md + checkpoint_*.md
│   ├── logs/                         cortex.log
│   └── cache/                        Embeddings cache
│
└── requirements.txt                  Python dependencies (60+)
```

---

## Key File Sizes

| File | Lines | Purpose |
|------|-------|---------|
| `agent_bridge.py` | 13,059 | ⭐ Agentic loop core |
| `chat_panel.py` | 8,425 | Chat UI + streaming |
| `main_window.py` | 7,523 | Top-level GUI window |
| `editor.py` | 2,208 | Monaco editor wrapper |
| `xterm_terminal.py` | 1,600 | Terminal emulator |
| `sidebar.py` | 1,500 | File explorer |
| `pathValidation.py` | 1,404 | Path security |
| `agent_safety.py` | 1,800 | Safety gates |
| `semantic_search.py` | 1,300 | Embeddings search |

---

## Provider + Model Summary

| Provider | Count | Best For |
|----------|-------|----------|
| **Anthropic** | 5 | Best overall (Claude 3.5 Sonnet) |
| **OpenRouter** | 20+ | Diverse + cheap |
| **DeepSeek** | 4 | Cost-effective reasoning |
| **OpenAI** | 5 | Frontier models |
| **Mistral** | 3 | Lightweight |
| **Alibaba (Qwen)** | 4 | Ultra-cheap |
| **MiMo** | 2 | Proprietary alternative |
| **SiliconFlow** | 3 | Embedding models |

**Total: 50+ models registered, all with context window + token limits verified**

---

## Safety & Permission Gates

```mermaid
graph LR
    TC["Tool Call<br/>Requested"]
    IT{Is Write/<br/>Edit/<br/>Bash?}
    
    TC --> IT
    IT -->|No<br/>Read/Grep/Search| EXEC["✅ Execute<br/>Immediately"]
    IT -->|Yes| CARD["🔒 Show Permission Card"]
    
    CARD --> WAIT["⏸ Freeze Agent Thread<br/>asyncio.to_thread<br/>event.wait(60s)"]
    
    WAIT --> USER{User<br/>Response?}
    
    USER -->|Allow Once| EXEC
    USER -->|Allow Always| SAVE["💾 Save to settings"]
    SAVE --> EXEC
    USER -->|Reject| ERROR["❌ Inject error<br/>Break loop"]
    USER -->|No click<br/>60s timeout| ERROR
    
    EXEC --> RESULT["📋 Append to messages<br/>Emit tool_activity"]
```

---

## Crash Recovery Flow

```mermaid
graph TD
    APP["IDE Running"]
    SAVE["Auto-save timers<br/>30s: chat history<br/>60s: MEMORY.md<br/>Settings every 5s"]
    CRASH["IDE Crashes or<br/>Force-closed"]
    RECOVER["Next Startup:<br/>Load from .cortex/memory/"]
    RESTORE["Restore chat + context<br/>User can load older<br/>checkpoints manually"]
    
    APP -->|Every 30-60s| SAVE
    SAVE -->|💾 JSON to .cortex/memory/| CRASH
    CRASH -->|🔄 On next launch| RECOVER
    RECOVER -->|✅| RESTORE
```

---

## How the Agent Thinks

1. **Reads user message** → adds to conversation
2. **Formats prompt** with system instructions + chat history + available tools
3. **Streams from LLM** with tool-calling enabled
4. **Parses response** in real-time (text tokens + tool deltas)
5. **For each tool call:**
   - Check safety gates (permission cards, doom-loop)
   - Execute the tool (read file, run bash, search, git push, etc.)
   - Get result
   - Append result as tool message
   - Emit UI card (Explore/File/Terminal card)
6. **Loop until:**
   - Final answer (plain text, no tool calls)
   - User rejects permission card
   - Doom-loop detected (same tool+args 5×)
   - max_turns reached (60 for 1M-ctx, 30 for 128K-ctx)
7. **Save to memory** (MEMORY.md + checkpoint files)

**Key property:** Agent is **frozen** during permission card — physically cannot bypass via alternative tool.
