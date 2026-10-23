# CLAUDE.md

Cortex AI IDE: a desktop IDE built around an autonomous agent. Native PyQt6 shell;
Qt WebEngine hosts the sidebar (`src/ui/html/sidebar.html`), Monaco editor
(`src/assets/editor.html`), xterm.js terminal (`src/ui/components/assets/xterm`)
and the chat / memory manager / live preview panels. See README.md for the full
user-facing docs.

## Commands

```powershell
.\.venv\Scripts\Activate.ps1
python src/main.py          # run from source (no hot reload: restart after changes)
python -m pytest            # tests live in tests/; markers: gui, unit
python -m black src         # formatter
npm install                 # Monaco (pinned 0.52.x for the AMD loader) into node_modules
```

## Where things are

| File | Role |
|------|------|
| `src/main.py` | Boot sequence, single-instance guard, crash recovery |
| `src/main_window.py` | `CortexMainWindow`: tabs, splitters, menus, and the `_on_*` handlers wiring bridge signals to UI (diffs, permissions, chats, files) |
| `src/ai/agent_bridge.py` | The brain. `AgentWorker(QThread)` runs a turn; `CortexAgentBridge(QObject)` owns history, `chat()`, `_dispatch_tool`, `_TOOL_SCHEMAS`, and the Qt signals the UI listens to. Bridge-side tools (`BridgeBashTool`, `BridgeLSTool`, ...) live here too |
| `src/ui/chat_panel.py` | Transcript widgets (`CollapsibleCard`, `ThoughtsBlock`, `ToolRow`, `ToolGroup`, `DiffCard`) and the prose/markdown render pipeline |
| `src/version.py` | Single source of truth for the version |

Packages:

- `src/ai/` - providers (`providers/`), tool execution (`tool_executor.py`, `tool_args.py`), circuit breaker / reasoning-loop guards, compaction, model registry and limits, MCP context.
- `src/agent/src/` - agent runtime: tools (`tools/`), permission model (`utils/permissions/`), compaction and memory services, bundled skills (`skills/bundled/`).
- `src/core/` - platform services: SQLite (`database.py`), PTY/ConPTY (`pty_backend.py`, `terminal_session.py`), workspace trust, semantic search / codebase index, `loop_engine/` (verified iteration), `security_toolkit/`, dev server, exit hooks.
- `src/ui/` - Qt widgets, themes (`color_themes.py`), extracted pure helpers (`stream_text.py`, `chat_text.py`, `table_normalize.py`), embedded HTML in `html/`.
- `src/services/` - MCP manager, update checker. `src/coordinator/` - multi-agent. `src/engine/` - tool registry/contracts. `src/plugin/` - plugins.
- `tools/` - maintenance scripts. `bin/rg.exe` - bundled ripgrep used by Grep.

## Request flow

1. `chat_panel` emits the prompt; `CortexAgentBridge.chat()` takes it.
2. The bridge assembles context (project context, memories, rules, tool schemas, history).
3. `AgentWorker` streams from the provider; text renders live as prose, thoughts and tool cards.
4. Each tool call is permission-checked and routed through `_dispatch_tool`.
5. Results flow back to the model; file writes surface as diffs via bridge signals handled in `main_window`.
6. When the model stops calling tools the turn ends and the final prose is persisted. Rendered cards are not stored, so rendering bugs do not show in the DB.

When changing behaviour, trace the whole path (Python -> bridge signal -> widget or HTML panel) before editing.

## Rules

- Version only in `src/version.py`.
- One theme setting: new surfaces read colours from `src/ui/color_themes.py`.
- Streaming/timeout code uses `time.monotonic()`, never `time.time()`.
- No unbounded work (`re.findall`, `"".join`) on streamed content on the GUI thread: cap first, then process.
- Cleanup that must survive `os._exit` goes in `src/core/exit_hooks.py`, never `atexit`.
- Logic extracted from the big files goes into Qt-free modules so it is testable without a `QApplication` (refactor item "W1").
- Do not swallow exceptions silently; failures should be logged or surfaced (item "W3").
- Never log secrets or API keys (`tests/test_no_secret_logging.py`).

Refactor progress for the W-series items is tracked in `prompt.md`.
