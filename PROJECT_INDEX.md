# Cortex Desktop — Project Index

_Regenerated: 2026-07-09_ (previous: 2026-07-08)

## Overview

**Cortex Desktop** is an AI-native IDE built with **PyQt6 + Qt WebEngine** that wraps a multi-provider LLM agent with 24+ coding tools inside a native chat UI + Monaco editor shell. It supports autonomous coding, streaming responses, crash persistence, semantic code search, plugin extensibility, and a full profile/usage tracking system.

---

## Key Metrics

| Metric | Value | vs. Jul 8 |
|--------|-------|-----------|
| Python files (\src/\, \plugins/\) | 639 | +3 |
| Total Python lines | 169,935 | +3,211 |
| HTML files (\src/\) | 9 | +3 |
| JS files (\src/\, excl. \
ode_modules\) | 7 | — |
| CSS files | 2 | — |
| QSS files | 2 | — |
| JSON files | 5 | — |
| Markdown files | 25 | — |
| Agent tools | 24 | — |
| LLM providers | 7 | — |
| Icon assets | 1,246 SVG files | +1 |
| Total source (py+html+js+css) lines | 191,546 | — |
| Main entry | \src/main.py\ (812 lines) | |
| Main window | \src/main_window.py\ (7,906 lines) | +383 |
| Agent bridge | \src/ai/agent_bridge.py\ (13,147 lines) | +88 |
| Chat panel | \src/ui/chat_panel.py\ (9,122 lines) | +697 |

---

## Top 20 Largest Files

| # | File | Lines | Location |
|---|------|-------|----------|
| 1 | agent_bridge.py | 13,147 | \src/ai/\ |
| 2 | chat_panel.py | 9,122 | \src/ui/\ |
| 3 | main_window.py | 7,906 | \src/\ |
| 4 | bashPermissions.py | 2,410 | \src/agent/src/tools/BashTool/\ |
| 5 | editor.py | 2,328 | \src/ui/components/\ |
| 6 | FileReadTool.py | 2,057 | \src/agent/src/tools/FileReadTool/\ |
| 7 | compact.py | 2,044 | \src/agent/src/services/compact/\ |
| 8 | database.py | 1,747 | \src/core/\ |
| 9 | sidebar_bridge.py | 1,609 | \src/ui/components/\ |
| 10 | memory_manager.py | 1,595 | \src/ui/dialogs/\ |
| 11 | config.py (mcp) | 1,536 | \src/agent/src/services/mcp/\ |
| 12 | yoloClassifier.py | 1,525 | \src/agent/src/utils/permissions/\ |
| 13 | bashSecurity.py | 1,522 | \src/agent/src/tools/BashTool/\ |
| 14 | permissions.py | 1,420 | \src/agent/src/utils/permissions/\ |
| 15 | pathValidation.py | 1,404 | \src/agent/src/tools/BashTool/\ |
| 16 | icons.py | 1,389 | \src/utils/\ |
| 17 | installedPluginsManager.py | 1,379 | \src/agent/src/utils/plugins/\ |
| 18 | config.py (agent) | 1,378 | \src/agent/src/utils/\ |
| 19 | language_detector.py | 1,319 | \src/utils/\ |
| 20 | GrepTool.py | 1,318 | \src/agent/src/tools/GrepTool/\ |

---

## Architecture

`
main.py
  └── main_window.py — IDE Shell
       ├── chat_panel.py — Native Chat UI (lazy loading, scroll restore)
       ├── editor.py — Monaco Editor
       ├── sidebar_bridge.py — HTML Sidebar
       ├── xterm_terminal.py — Terminal
       ├── webview_panel.py — Webview Tabs
       ├── diff_viewer.py — Diff Dialog
       └── memory_manager.py — Settings/Profile UI
            └── agent_bridge.py — Core AI Brain
                 ├── 7 LLM Providers (providers/)
                 ├── 24 Agent Tools (agent/src/tools/)
                 ├── Context Compaction (conversation_compactor.py)
                 ├── Memory System (semantic_memory.py, embeddings.py)
                 ├── streaming.py — Emitter
                 ├── usage_tracker.py — Profile/Usage
                 └── agent_safety.py — Tool budget, doom-loop detection
`

---

## Directory Structure

`
src/
├── main.py                      # Entry point (812 lines)
├── main_window.py               # IDE shell, menu bar, signals (7,906 lines)
│
├── ai/                           # AI agent core (top-level bridge)
│   ├── agent_bridge.py          # Central brain — tool loop, streaming (13,147 lines)
│   ├── agent_safety.py          # Tool budget, doom-loop, read-before-edit
│   ├── usage_tracker.py         # Token/request/tool tracking
│   ├── streaming.py             # SSE event emitter
│   ├── conversation_compactor.py # Context window compaction
│   ├── tool_executor.py         # Tool execution engine
│   ├── tool_result_storage.py   # Tool output storage
│   ├── model_limits.py          # Per-model context limits
│   ├── model_registry.py        # Model metadata registry
│   ├── project_context.py       # Project file context
│   ├── cortex_project_context.py # Project indexing
│   ├── file_skeleton.py         # File structure extraction
│   ├── circuit_breaker.py       # API failure protection
│   ├── session_task.py          # Session management
│   ├── stub_agent.py            # Fallback agent
│   ├── changes/                 # Change tracking
│   └── providers/               # LLM provider implementations
│       ├── openai_provider.py / deepseek_provider.py / mistral_provider.py
│       └── alibaba_provider.py / mimo_provider.py / openrouter_provider.py / siliconflow_provider.py
│
├── agent/src/                   # Internal agent framework
│   ├── tools/                   # 24 tools, 90 files
│   ├── services/                # compact, mcp, oauth, AgentSummary, MagicDocs, PromptSuggestion, SessionMemory, extractMemories, context_collapse, analytics, api
│   ├── utils/                   # permissions, plugins, config, bash, git, diff, memory, model, sandbox, settings, shell, suggestions, swarm, task, todo, ultraplan, computerUse, powershell
│   ├── tasks/                   # DreamTask, LocalAgentTask, MonitorMcpTask
│   ├── hooks/, hooks/toolPermission/
│   ├── bridge/, bootstrap/, entrypoints/, bun/, shell/, bash/
│   ├── skills/ (+ bundled/), voice/, mdm/, memdir/, model/, proactive/, query/
│   ├── coordinator/, state/, task/, settings/, agent_types/, api/, analytics/
│   ├── sessionTranscript/, DelFiles/
│   └── ui/components/
│
├── ui/                           # UI components
│   ├── chat_panel.py             # Main chat UI — lazy load, scroll restore (9,122 lines)
│   ├── chat_store.py / chat_text.py / tokens.py / native_chat_bridge.py
│   ├── tool_cards.py / syntax_highlight.py / table_normalize.py
│   ├── spinner.py / spinner_overlay.py / edit_state_manager.py / secondary_ui.py
│   ├── icons.py / agent_signals.py / cursor_split_handle.py
│   ├── components/               # editor.py, webview_panel.py, sidebar.py, sidebar_bridge.py,
│   │                             #   terminal.html, xterm_terminal.py, problems_panel.py,
│   │                             #   windows_terminal.py, chat_enhanced/, permission/
│   ├── dialogs/                  # diff_viewer.py, memory_manager.py
│   ├── tools/                    # UI-side tool rendering helpers
│   ├── html/                     # sidebar.html, ai_chat/, memory_manager/, icons/ (1,246 SVGs)
│   └── themes/                   # dark.qss, light.qss
│
├── core/                         # Core systems
│   ├── crash_persistence.py / database.py (1,747 lines) / chat_history.py
│   ├── semantic_memory.py / embeddings.py / siliconflow_embeddings.py
│   ├── stability_engine.py / file_manager.py / git_manager.py / project_manager.py
│   ├── session_manager.py / agent_session_manager.py / autonomy_manager.py
│   ├── codebase_index.py / code_chunker.py / memory_storage.py / memory_types.py
│   ├── event_bus.py / background_worker.py / change_orchestrator.py / debug_loop.py
│   ├── key_manager.py / live_server.py / task_graph.py / sandbox_manager.py
│   ├── worker_entrypoint.py / auth_manager.py / cortex_api.py
│   └── security.py / security_audit.py / secure_http.py / secure_transmission.py
│
├── config/                       # settings.py, theme_manager.py, points_manager.py
├── coordinator/                  # coordinator_prompt.py, coordinator_system.py, agent_context.py
├── plugin/                       # plugin_manager.py
├── services/                     # usage_tracker.py, errors.py, update_checker.py
├── utils/                        # helpers, logger, icons, language_detector (1,319 lines),
│                                 #   image_processing, git_utils, diff/, notifications,
│                                 #   timeout_strategy, safe_delete, startup_profiler,
│                                 #   pyinstaller_hooks/, runtime_hook_*.py
└── assets/                       # editor.html, logo/

plugins/
└── symbol_indexer/               # plugin.json + plugin.py (external plugin example)
`

---

## Agent Tools (24)

| Tool | Purpose |
|------|---------|
| AgentTool | Sub-agent spawning for parallel work |
| AskUserQuestionTool | Multi-choice questions to the user |
| BashTool | Shell command execution (bash/git-bash/wsl) |
| EnterPlanModeTool | Structured planning mode entry |
| ExitPlanModeTool | Exit planning mode |
| FileEditTool | Exact string replacement in files |
| FileReadTool | File reading with offset/limit support |
| FileWriteTool | Create/overwrite files |
| GlobTool | File pattern matching |
| GrepTool | Regex content search |
| ListMcpResourcesTool | MCP resource listing |
| NotebookEditTool | Jupyter notebook editing |
| PlanBuildTool | Build plan creation/management |
| PowerShellTool | PowerShell command execution |
| REPLTool | Interactive REPL execution |
| SementicSearch | Natural language code search |
| SendMessageTool | Message sending |
| SkillTool | Skill execution |
| SleepTool | Pause execution |
| TodoWriteTool | Todo list management |
| ToolSearchTool | Tool discovery/search |
| VisionAgentTool | Image/vision processing |
| WebFetchTool | URL content fetching |
| WebSearchTool | Web search |

---

## LLM Providers (7)

| Provider | API |
|----------|-----|
| OpenAI | OpenAI GPT models |
| DeepSeek | DeepSeek models (V4) |
| Mistral | Mistral AI models |
| Alibaba | Alibaba Cloud models |
| MiMo | MiMo V2.5 models |
| OpenRouter | OpenRouter API gateway |
| SiliconFlow | SiliconFlow models |

---

## Environment Variables (\.env.example\, 13 keys)

| Variable | Purpose |
|----------|---------|
| \OPENAI_API_KEY\ | OpenAI provider auth |
| \DEEPSEEK_API_KEY\ | DeepSeek provider auth |
| \MISTRAL_API_KEY\ | Mistral provider auth |
| \DASHSCOPE_API_KEY\ / \DASHSCOPE_BASE_URL\ | Alibaba DashScope (Qwen) provider auth |
| \MIMO_API_KEY\ | MiMo provider auth |
| \OPENROUTER_API_KEY\ | OpenRouter gateway auth |
| \SILICONFLOW_API_KEY\ | SiliconFlow provider + embeddings auth |
| \MOONSHOT_API_KEY\ | Moonshot/Kimi provider auth |
| \SERPAPI_API_KEY\ | Web search tool backend |
| \CORTEX_DEEPSEEK_MAX_OUTPUT_TOKENS\ | DeepSeek output cap override |
| \CORTEX_MIMO_READ_TIMEOUT_SEC\ / \CORTEX_MIMO_TOOL_READ_TIMEOUT_SEC\ | MiMo timeout tuning |

Note: \.env.example2\ also exists alongside \.env.example\ — worth reconciling into one file if the second is a newer draft.

---

## Key Systems

### Chat History (Lazy Loading + Scroll Restore)
- **\chat_panel.py\** — \load_timeline_async()\ loads last 50 messages as complete turns
- Scroll-up pagination — loads 30 more messages per scroll-up, complete turns only
- Scroll position save/restore — per-conversation scroll memory
- Viewport-aware refit — only refits visible QTextBrowser widgets
- **\chat_store.py\** — timeline JSON serialization in SQLite
- **\crash_persistence.py\** — immediate SQLite writes on every message

### Agent Safety (\gent_safety.py\)
- Tool budget: unlimited (soft reminder every 50 calls)
- Doom-loop detection: same tool + same args 5x → stop
- Read-before-edit enforcement, stale-read detection
- Error recovery budget (3 retries)

### Thinking Budget (\gent_bridge.py\)
- Default: 32,000 tokens (configurable via \CORTEX_THINKING_BUDGET_TOKENS\)
- Exceeded → close thought card, drop further thinking chunks; tool calls still processed normally

### Stability Engine (\stability_engine.py\)
- Monitors RAM/CPU every 5 seconds; pressure levels normal → elevated → high → critical
- Emergency save throttled to once per 30 seconds at critical; GC triggered at high pressure

### Design Tokens (\	okens.py\)
- Dark theme + Light theme support — colors from \DARK\ / \LIGHT\ dicts
- \uild_markdown_css()\, \uild_qss()\ generate CSS/QSS from the same source of truth

### Authentication & API (\uth_manager.py\, \cortex_api.py\)
- User auth/session management, Cortex cloud API integration, secure key storage, license/subscription checks

### Multi-Tab Terminal (\xterm_terminal.py\, \	erminal.html\)
- QTabWidget with CleanTabBar; per-terminal XTermWidget instances with xterm.js
- Python↔JS bridge via QWebChannel for header button clicks (+New / Kill / Clear / Restart)

### Memory System (\.cortex/memory/\)
- \MEMORY.md\ index with session summaries; auto-compaction checkpoints in separate \checkpoint_*.md\ files
- Two write paths (\_write_memory_summary\, \_update_memory_md\) unified to avoid overwrite conflicts
- Relevant past-session retrieval via semantic search

---

## Build System

| File | Purpose |
|------|---------|
| \cortex.spec\ | PyInstaller spec — bundles Python + assets |
| \uild.ps1\ | PowerShell build script |
| \cortex_setup.iss\ | Inno Setup installer script |
| \uild_installer.bat\ | Batch installer builder |

Build artifacts present in the tree: \uild/cortex/\ (PyInstaller intermediates + \Cortex.exe\), \dist/Cortex/\ (final bundle), \installer_output/\. These are generated outputs, not source — safe to exclude from any future indexing pass and worth double-checking they're in \.gitignore\.

---

## Housekeeping Notes

A few things spotted while indexing that may be worth a look:
- Two build logs (\uild_error.log\, \uild_output.log\) and a top-level \error.log\ / empty \error.txt\ are sitting at repo root — likely fine to \.gitignore\ if not already.
- \.env.example\ and \.env.example2\ both exist; consider consolidating.
- Two dated screenshots and several demo HTML files (\demo_heatmap.html\, \diff_card_demo.html\, \older_icons_demo.html\) live at repo root rather than in \Docs/\ or a \demos/\ folder.
