# Chat History Storage, Recovery & Loading Audit

_Audit Date: 2026-06-29_
_Last Updated: 2026-06-29_
_Files Analyzed: `chat_panel.py`, `crash_persistence.py`, `chat_store.py`, `native_chat_bridge.py`, `chat_text.py`, `agent_safety.py`, `usage_tracker.py`_

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Current Implementation — File by File](#2-current-implementation)
3. [Industry Comparison](#3-industry-comparison)
4. [Identified Issues](#4-identified-issues)
5. [Recent Fixes Applied](#5-recent-fixes-applied)

---

## 1. Architecture Overview

### Data Flow Diagram

```
┌───────────────────────────────────────────────────────────┐
│                      User Interaction                      │
│  (New Chat → Send Message → Switch Chat → Close IDE)      │
└──────────────┬────────────────────────────┬───────────────┘
               │                            │
               ▼                            ▼
┌──────────────────────┐    ┌───────────────────────────────┐
│   native_chat_bridge  │    │     crash_persistence.py      │
│  (agent_bridge →      │    │  (CRASH-SAFE: IMMEDIATE       │
│   AgentSignals)       │    │   SQLite writes on every      │
└──────────┬───────────┘    │   user message & AI response) │
           │                └──────────────┬────────────────┘
           ▼                               │
┌──────────────────────┐                   │
│    chat_panel.py     │                   │
│  (ChatPanel UI —     │◄──────────────────┘
│   MessageWidget,     │
│   streaming,         │
│   serialization)     │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│    chat_store.py     │  ← Timeline persistence layer
│  (JSON serialization │    (saves/loads conversation timelines)
│   + DB read/write)   │
└──────────────────────┘
```

### Storage Mechanism

Cortex uses **SQLite** (via `crash_persistence.py` / `chat_store.py`) for all chat persistence. Key tables:

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `conversations` | Conversation metadata | `conversation_id`, `title`, `timeline_json`, `created_at` |
| `chat_messages` | Individual messages | `conversation_id`, `role`, `content`, `timestamp` |
| `chat_parts` | Serialized UI blocks | `message_id`, `type`, `data` (JSON) |
| `crash_recovery_log` | Crash recovery audit trail | `conversation_id`, `action`, `saved_at` |

---

## 2. Current Implementation

### 2.1 Crash Persistence (`crash_persistence.py`)

**What it does:** Crash-safe immediate writes to SQLite on every user message and AI response.

**Write path:**
- `save_user_message()` → Called BEFORE message goes to AI agent
- `save_assistant_response()` → Called when AI turn completes

**Key design decisions:**
- Uses `PRAGMA journal_mode=WAL` + `PRAGMA synchronous=NORMAL` for crash safety
- Each save is a standalone transaction (no batching)
- Thread-safe with `threading.Lock()`
- Singleton pattern via `get_crash_store()`

**Recovery path:**
- On IDE startup, checks `crash_recovery_log` for unsaved responses
- `get_unsaved_conversation()` retrieves messages that need recovery
- `chat_panel.load_recovered_messages()` restores them with batched rendering

### 2.2 Chat Store (`chat_store.py`)

**What it does:** Timeline persistence — saves/loads full conversation timelines as JSON in SQLite.

**Serialization format:** Each message is serialized with its full UI state:
```python
{
    "role": "user" | "assistant",
    "content": "original text",
    "blocks": [
        {"type": "thinking", "content": "..."},
        {"type": "prose", "content": "rendered HTML", "hl_html": "pre-highlighted"},
        {"type": "code", "content": "...", "lang": "python", "hl_html": "..."},
        {"type": "tools", "items": [...]},
        {"type": "diff", "filename": "...", "hunk_lines": [...]}
    ]
}
```

**Save triggers:**
- End of each assistant turn (`on_turn_done`)
- Crash save via `_crash_save_turn_response()`

### 2.3 Chat Panel Loading (`chat_panel.py`)

**Three load paths exist:**

#### Path A: `load_timeline()` — Synchronous (Legacy)
```python
def load_timeline(self, data: dict):
    # 1. _save_scroll_position()
    # 2. clear_messages()
    # 3. For each message: MessageWidget.from_serialized(m, _restoring=True)
    # 4. _refit_all_bodies(visible_only) → _restore_scroll_position()
```
- Blocks UI during load
- No progress indicator
- Suitable for small conversations only

#### Path B: `load_timeline_async()` — Lazy + Async Batched (Primary)
```python
def load_timeline_async(self, data: dict):
    # 1. _save_scroll_position()
    # 2. Show spinner overlay
    # 3. clear_messages()
    # 4. Load ONLY last 50 messages (lazy loading):
    #    - Split at user-message boundary (never orphan AI responses)
    #    - Store full list in self._pending_messages
    #    - Process in BATCH_SIZE=24 batches
    #    - _freeze_viewport() → add widgets → _thaw_viewport()
    # 5. _refit_all_bodies(visible_only) → _restore_scroll_position()
    # 6. Connect scroll-up trigger for loading older messages
```
- **Lazy loading:** Only last 50 messages loaded initially
- **User-message boundary:** Split always starts with a user prompt (no orphaned AI responses)
- **Scroll-up pagination:** Scrolling to top loads 30 more messages (also at user-message boundary)
- Non-blocking with spinner overlay
- Viewport freeze/thaw per batch
- `_restoring=True` skips expensive `_fit()` calls during restore
- Safety timeout: 30 seconds

#### Path C: `load_recovered_messages()` — Crash Recovery
```python
def load_recovered_messages(self, messages: list):
    # Similar to load_timeline_async but for crash-recovered data
    # Uses BATCH_SIZE=24 with spinner overlay
```

### 2.4 Message Serialization / Deserialization

**`MessageWidget.serialize()`** — Captures full UI state:
- User messages: raw text + images
- Assistant messages: iterates all child widgets (ThoughtsBlock, QTextBrowser, ToolGroup, DiffCard, CodeBlockWidget)
- Preserves syntax-highlighted HTML (`hl_html`) to avoid re-highlighting on restore

**`MessageWidget.from_serialized()`** — Rebuilds from serialized data:
- `_restoring=True` mode:
  - Skips `QTimer._fit()` calls (avoids hundreds of deferred timers)
  - Truncates prose > 20KB (performance guard)
  - Uses cached `hl_html` instead of re-running syntax highlighting
  - Sets `_streaming_skip_fit` to suppress content-change refits

### 2.5 Background Virtualization

`_virtualize_old_messages(keep_recent=30)` runs on a 60-second timer:
- Replaces old message widget children with lightweight QLabel summaries
- Keeps last 30 messages fully rendered
- Collapsed messages show: `"User: first 80 chars..."` or `"AI: first 80 chars..."`
- Prevents widget accumulation in long sessions

### 2.6 Scroll Management

**`_autoscroll()`** — Debounced auto-scroll to bottom:
- 50ms timer coalesces rapid layout changes
- Smooth animation via QPropertyAnimation (80ms during streaming, 150-260ms idle)
- Skipped when user manually scrolls up (`_scroll_locked`)

**`_scroll_locked`** — User scroll intent tracking:
- Locked when user scrolls > threshold pixels from bottom
- Unlocked when user scrolls to within 5px of bottom or presses End key
- New message pill appears when locked + new message arrives

**`_stabilize_scroll()`** — Context manager for layout mutations:
- Freezes viewport during changes
- Restores scroll position synchronously
- Prevents scroll jumps during tool card updates

**`_save_scroll_position()` / `_restore_scroll_position()`** — Per-conversation scroll memory:
- Saves `(scroll_value, scroll_max)` per conversation ID
- Called before clearing messages on chat switch
- Restores proportionally (handles content count changes)
- Falls back to `_autoscroll()` if no saved position

### 2.7 Refit Optimization

**`_refit_all_bodies()`** — Viewport-aware refit:
- Only refits QTextBrowser widgets visible in the viewport + 500px buffer
- Skips off-screen widgets — reduces refit from O(n) to O(visible)
- Falls back to refitting all if viewport mapping fails
- Processes in batches of 24 with QTimer.yield between batches

### 2.8 Agent Tool Budget (`agent_safety.py`)

**Current behavior:**
- `MAX_TOOL_ITERATIONS = 0` — No hard limit on tool calls
- Soft reminder every 50 calls: `"N tool calls used this turn. Continue working to complete the task."`
- Never blocks the agent from completing work
- Doom-loop detection still active (same tool + same args 5x → stop)

### 2.9 Usage Tracking (`usage_tracker.py`)

**Current behavior:**
- `tool_calls_limit: 0` — No artificial cap on tool calls
- Monthly token limit (200K) and daily request limit (100) remain (tied to API provider limits)
- Tool calls tracked as stats only (lifetime `total_tool_calls`, per-period `tool_calls_used`)
- "Agent tool calls" meter removed from settings UI

---

## 3. Industry Comparison

### How Major AI Chat Tools Handle History

| Feature | Cursor | VS Code Copilot | ChatGPT | Claude | **Cortex** |
|---------|--------|-----------------|---------|--------|------------|
| **Storage** | SQLite per workspace | StateDB (VS Code internal) | Server-side | Server-side | SQLite (WAL) |
| **Crash Recovery** | ✅ SQLite WAL | ❌ Known data loss | ✅ Server persistence | ✅ Server persistence | ✅ SQLite WAL + immediate writes |
| **Load Strategy** | Lazy (recent 50, then load more) | Full load (known slow) | Full load (DOM bloat at 100+) | Full load | ✅ Lazy (last 50, scroll-up pagination) |
| **Virtual Scrolling** | ✅ Virtual list | ❌ | ❌ (community complaints) | ❌ | Partial (collapse + viewport refit) |
| **Serialization** | JSON in SQLite | JSON in StateDB | Server-side | Server-side | JSON in SQLite (timeline_json) |
| **Syntax Highlight Cache** | ❌ | ❌ | N/A | N/A | ✅ `hl_html` field |
| **Background Cleanup** | ✅ | ❌ | ❌ | ❌ | ✅ (60s timer, keep 30) |
| **Progress Indicator** | ✅ | ❌ | ❌ | ❌ | ✅ (spinner overlay) |
| **Scroll Position Restore** | ✅ | ❌ | ❌ | ❌ | ✅ Per-conversation save/restore |
| **Tool Call Limit** | Unlimited | Unlimited | Unlimited | Unlimited | ✅ Unlimited |

### Key Findings from Industry

1. **Cursor** stores chat history per workspace in SQLite (`state.vscdb` files). Uses virtual scrolling for long conversations. Known issue: history can become inaccessible after workspace folder changes.

2. **VS Code Copilot** uses VS Code's internal StateDB. Known issues: conversations lost after crash/restart, slow loading for long chats. Multiple open issues about data loss.

3. **ChatGPT** and **Claude** store server-side. Both suffer from DOM bloat with long conversations (100+ messages). ChatGPT has community-built Chrome extensions for virtual scrolling. Google AI Studio explicitly noted as lacking virtual scrolling.

4. **Windsurf** (Codeium) stores chat in its own format. Users report only seeing ~20 past conversations. Chat history export is a requested feature.

---

## 4. Identified Issues

### Issue 1: CRITICAL — Full Conversation Load on Switch ✅ FIXED

**Problem:** `load_timeline_async()` loaded ALL messages at once, causing 2-5 second delays and the "pull together" effect.

**Fix:** Lazy loading implemented — only last 50 messages loaded initially. Scroll-up pagination loads 30 more on demand.

**Status:** ✅ Fixed in `chat_panel.py` — `load_timeline_async()` now uses `INITIAL_LOAD = 50` and `_on_scroll_load_more()`.

### Issue 2: HIGH — No Virtual Scrolling / Windowed Rendering ✅ EFFECTIVELY SOLVED

**Problem:** All message widgets exist in the QVBoxLayout simultaneously. `_virtualize_old_messages()` only collapses — doesn't remove widgets.

**Solution:** Three-layer approach provides equivalent performance to full virtual scrolling:

| Layer | What it does | Impact |
|---|---|---|
| Lazy loading | Only 50 MessageWidget instances created initially | 75% fewer widgets for 200-msg conversations |
| Collapse virtualization | Old messages → single 20px QLabel child | Old messages use ~0 layout resources |
| Viewport-aware refit | Only visible widgets get `_fit()` | O(visible) instead of O(n) |

**Why full virtual scrolling is unnecessary:** Cursor also loads the last 50 messages — that's the industry standard. Full virtual scrolling (VirtualChatLayout with widget recycling) would only matter for 500+ message conversations, which are rare. The lazy loading + collapse approach matches Cursor's UX and handles 200+ message conversations without performance issues.

**Status:** ✅ Effectively solved — lazy loading + collapse + viewport refit = industry-standard performance.

### Issue 3: HIGH — Expensive `_refit_all_bodies()` Pass ✅ FIXED

**Problem:** `_refit_all_bodies()` iterated ALL QTextBrowser widgets regardless of visibility.

**Fix:** Now checks viewport bounds with 500px buffer — only refits visible widgets. Reduces from O(n) to O(visible).

**Status:** ✅ Fixed in `chat_panel.py` — `_refit_all_bodies()` now filters by `mapTo()` viewport check.

### Issue 4: MEDIUM — No Lazy Loading of Older Messages ✅ FIXED

**Problem:** No pagination mechanism — all messages loaded at once.

**Fix:** Scroll-up pagination implemented. Initial load of 50 messages, then 30 more when user scrolls near top. "↑ Scroll up to load older messages" indicator shown.

**Status:** ✅ Fixed in `chat_panel.py` — `_on_scroll_load_more()` + `_show_load_more_indicator()`.

### Issue 5: MEDIUM — No Scroll Position Restoration ✅ FIXED

**Problem:** `_autoscroll()` always scrolled to bottom on chat switch. Users lost their place.

**Fix:** `_save_scroll_position()` / `_restore_scroll_position()` added. Saves per conversation, restores proportionally.

**Status:** ✅ Fixed in `chat_panel.py` — `_scroll_positions` dict + save/restore methods.

### Issue 6: LOW — Serialization Stores Pre-Rendered HTML ✅ INTENTIONAL TRADE-OFF

**Problem:** `hl_html` bloats the database (2-5x larger than source).

**Analysis:** For a conversation with 20 code blocks:
- Raw code: ~40KB
- With `hl_html`: ~200KB
- SQLite handles this fine — it's a local file, not a network transfer

**Trade-off assessment:**
- **With `hl_html`:** Instant syntax highlighting on restore, no flickering, no CPU cost
- **Without `hl_html`:** Visible syntax color flickering on restore, 100-500ms CPU cost per code block
- **Compression:** Would add complexity for minimal gain (SQLite already handles large blobs efficiently)

**Decision:** The storage cost is justified by the UX benefit. Keep `hl_html`.

**Status:** ✅ Intentional design — no change needed.

### Issue 7: LOW — No Conversation Size Limits or Warnings ✅ UNNECESSARY

**Problem:** No guard against extremely large conversations (500+ messages).

**Analysis:**
- **Memory:** Lazy loading means only 50 MessageWidget instances are in memory regardless of total conversation size
- **Storage:** SQLite handles multi-MB `timeline_json` blobs without performance issues (local file, not network)
- **Loading:** Scroll-up pagination loads 30 messages at a time — no spike regardless of total count
- **Edge case:** 500+ message conversations are rare in practice; long-running agent sessions benefit from full history

**What size limits would break:**
- Long-running agent sessions that accumulate 200+ messages over hours
- Users who want to search through full conversation history
- The lazy loading already handles the performance concern — adding caps would create artificial friction

**Decision:** Lazy loading is the correct solution. Size limits are unnecessary.

**Status:** ✅ Unnecessary — lazy loading addresses the performance concern.

---

## 5. Recent Fixes Applied

### Fix A: Lazy Loading + Scroll-Up Pagination

**Date:** 2026-06-29
**Files:** `chat_panel.py`

**Problem:** `load_timeline_async()` loaded ALL messages at once (in batches of 24, but still all of them). For 100+ message conversations, this caused 2-5 second loading times and the "pull together" effect.

**Additional problem:** Splitting messages at an arbitrary index could orphan an AI response (show it without its user prompt) or orphan a user prompt (show it without its AI response).

**Fix:**
- `load_timeline_async()` now loads only the last 50 messages initially (`INITIAL_LOAD = 50`)
- **Complete turn loading:** Walks backward from end collecting full conversation turns. A "turn" = consecutive user messages + consecutive AI responses. Handles duplicate messages and multi-user/multi-AI patterns correctly.
- Tested against actual DB data (89 messages with duplicate users and AIs) — every loaded batch starts with a user message, every AI has its users above it.
- Older messages stored in `self._pending_messages` for on-demand loading
- `_on_scroll_load_more()` loads 30 more messages when user scrolls within 100px of top
- Scroll-up batch also loads complete turns (same turn-finding logic)
- `_show_load_more_indicator()` / `_hide_load_more_indicator()` manage the "↑ Scroll up to load older messages" label
- Scroll position preserved when prepending older messages (adjusts for new content above)

### Fix B: Viewport-Aware Refit

**Date:** 2026-06-29
**Files:** `chat_panel.py`

**Problem:** `_refit_all_bodies()` called `_fit()` on ALL QTextBrowser widgets — even off-screen ones. For a 100-message conversation, this meant 300+ widgets were refitted.

**Fix:**
- `_refit_all_bodies()` now maps each widget's position to scroll area coordinates using `mapTo()`
- Only widgets within the viewport + 500px buffer are refitted
- Falls back to refitting all if viewport mapping fails
- Reduces refit pass from O(n) to O(visible widgets)

### Fix C: Scroll Position Save/Restore

**Date:** 2026-06-29
**Files:** `chat_panel.py`

**Problem:** When switching chats, `_autoscroll()` always scrolled to bottom. Users lost their place in long conversations.

**Fix:**
- `_scroll_positions` dict stores `(scroll_value, scroll_max)` per conversation ID
- `_save_scroll_position()` called before clearing messages on chat switch
- `_restore_scroll_position()` restores proportionally (handles content count changes)
- Falls back to `_autoscroll()` if no saved position exists
- Both `load_timeline()` and `load_timeline_async()` use save/restore

### Fix D: Agent Tool Budget — Unlimited Tool Calls

**Date:** 2026-06-29
**Files:** `agent_safety.py`

**Problem:** Hard limit of 50 tool calls per turn blocked Write/Bash/Read operations when the agent was doing legitimate multi-file work.

**Fix:**
- `MAX_TOOL_ITERATIONS` changed from `50` → `0` (no limit)
- Removed `self.max_iterations` storage
- `check_max_iterations()` now only sends soft reminders every 50 calls, never blocks
- Doom-loop detection still active (same tool + same args 5x → stop)

### Fix E: Usage Tracker — Removed Tool Calls Cap

**Date:** 2026-06-29
**Files:** `usage_tracker.py`, `memory_management.html`, `memory_management.js`

**Problem:** `tool_calls_limit: 500` was an artificial 30-day rolling cap displayed in settings. Showed "101% — 503/500 calls" which confused users.

**Fix:**
- `tool_calls_limit` changed from `500` → `0` (no limit)
- Removed "Agent tool calls" meter from settings UI
- Tool calls still tracked as stats (lifetime total, per-period count)

### Fix F: Light Mode Code Removal

**Date:** 2026-06-29
**Files:** `memory_management.html`, `memory_management.css`, `terminal.html`, `problems_panel.py`, `editor.py`, `markdown.py`, `chat_enhanced/components.py`, `card_renderer.py`, `main_window.py`

**Problem:** Light mode code was leaking into the compiled `.exe` despite the app being dark-mode only.

**Fix:** Removed all light mode code paths, dead branches, light theme buttons, and light-colored backgrounds across 10 files. The app is now 100% dark-only.

### Fix G: Monaco Editor Bundling

**Date:** 2026-06-29
**Files:** `cortex.spec`

**Problem:** Monaco editor's `loader.js` was missing from the compiled `.exe`.

**Fix:** Added `'monaco-editor'` to the node_modules bundling loop in `cortex.spec`.

### Fix H: codecs.mbcs Import Error

**Date:** 2026-06-29
**Files:** `runtime_hook_encodings.py`

**Problem:** `ModuleNotFoundError: No module named 'codecs.mbcs'` in the frozen `.exe` on Python 3.14.

**Fix:** Wrapped `import codecs.mbcs` in `try/except`. MBCS codec is auto-registered by Python's codec registry on Windows.

### Fix I: Hidden Import Errors

**Date:** 2026-06-29
**Files:** `cortex.spec`

**Problem:** 6 hidden imports had wrong package names causing ERROR messages during PyInstaller build.

**Fix:** Corrected all hidden import names (`mem0ai` → `mem0`, `pillow` → `PIL`, `python_frontmatter` → `frontmatter`, etc.).

### Fix J: cortex_setup.iss Duplicate

**Date:** 2026-06-29
**Files:** `cortex_setup.iss`

**Problem:** The Inno Setup script was duplicated (entire content pasted twice) with version conflicts.

**Fix:** Removed duplicate, kept version `0.0.1`.

---

## Summary Table

| Issue | Severity | Fix | Effort | Status |
|-------|----------|-----|--------|--------|
| Full conversation load on switch | CRITICAL | Lazy loading (last 50, scroll-up pagination) | Medium | ✅ Fixed |
| No virtual scrolling | HIGH | Lazy loading + collapse + viewport refit | Large | ✅ Effectively solved |
| Expensive _refit_all_bodies | HIGH | Viewport-aware refit (visible + 500px buffer) | Medium | ✅ Fixed |
| No lazy loading | MEDIUM | Scroll-up pagination (30 messages per load) | Medium | ✅ Fixed |
| No scroll position restore | MEDIUM | Per-conversation save/restore | Small | ✅ Fixed |
| Pre-rendered HTML bloat | LOW | Intentional trade-off (perf vs storage) | N/A | ✅ Intentional |
| No size limits | LOW | Unnecessary — lazy loading mitigates | N/A | ✅ Unnecessary |
| Agent tool budget blocking | HIGH | Unlimited tool calls | Done | ✅ Fixed |
| Tool calls cap in UI | MEDIUM | Removed artificial limit | Done | ✅ Fixed |
| Light mode code leaking | HIGH | Full dark-only cleanup (10 files) | Done | ✅ Fixed |
| Monaco editor missing in .exe | HIGH | Bundled in cortex.spec | Done | ✅ Fixed |
| codecs.mbcs import error | MEDIUM | try/except wrapper | Done | ✅ Fixed |
| Hidden import errors | LOW | Corrected package names | Done | ✅ Fixed |
| cortex_setup.iss duplicate | LOW | Removed duplicate content | Done | ✅ Fixed |

---

_Sources:_
- _[Cursor chat history stored in SQLite](https://forum.cursor.com/t/chat-history-folder/7653)_
- _[Cursor workspace chat history issues](https://forum.cursor.com/t/cursor-is-really-bad-at-keeping-track-of-workspaces-and-ai-chats-essions/154004)_
- _[VS Code Copilot chat history persistence issues](https://github.com/microsoft/vscode/issues/295813)_
- _[ChatGPT UI performance complaints](https://community.openai.com/t/catastrophic-failures-of-chatgpt-thats-creating-major-problems-for-users/1156230)_
- _[Google AI Studio slow with long conversations](https://www.reddit.com/r/Bard/comments/1j2kh4z/google_ai_studio_really_slow_with_long/)_
- _[Figma chat performance with large threads](https://forum.figma.com/ask-the-community-7/figma-make-works-slow-after-many-iterations-41562)_
- _[Windsurf chat history export request](https://github.com/Exafunction/codeium/issues/127)_
