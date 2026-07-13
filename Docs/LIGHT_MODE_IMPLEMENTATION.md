# Light Mode Implementation — Full Reference

_Last updated: 2026-07-08 (v2 architecture — freeze SOLVED, system theme fixed, light-mode sidebars fixed, palette updated to VS Code/Excel cool-gray + green accent)_

## Overview

Full dark/light/system theme switching system wired end-to-end across the entire IDE: Python backend → QSS styling → PyQt6 widget custom paint → WebEngine HTML/CSS/JS settings UI.

> **⚠️ ARCHITECTURE v2 (2026-07-08, supersedes the "Freeze Prevention System" section below):**
> Runtime theme switches **no longer call `QApplication.setStyleSheet()` at all**. The
> `setUpdatesEnabled()` wrapping described later in this doc reduced but did not eliminate the
> freeze — see the changelog below for the final fix. The full QSS is applied exactly once, at
> startup. Do not reintroduce app-wide `setStyleSheet()` on the runtime path.

---

## Changelog — Bugs Hit & Fixed During Light Mode Work (2026-07-08)

### Bug 1: Theme switch froze the entire IDE for 75+ seconds (CRITICAL) — FIXED

**Symptom:** Clicking Dark/Light in Settings → Appearance froze the whole IDE; on a
RAM-constrained machine (7.8GB @ ~90% baseline) the freeze lasted 75+ real seconds and the app
often had to be killed.

**Investigation trail (what did NOT fix it):**
1. Removed `gc.collect()` from the stability-engine background thread (it held the GIL every
   5s tick). Necessary fix, but freeze persisted.
2. Fixed the dead freeze/thaw guard in `theme_manager.apply()` — it checked
   `isinstance(target, QWidget)` on the QApplication (always False), so
   `setUpdatesEnabled(False)` never actually ran. Fixed via a `freeze_widget` param. Freeze persisted.
3. Cut chat widget count 126 → 37 (INITIAL_LOAD 50 → 12, lazy-load on scroll-up). Confirmed in
   logs, freeze **still** persisted — proof widget count was not the dominant cost.

**Root cause (confirmed):** `QApplication.setStyleSheet()` forces Qt to re-polish every widget
in the app, **including 4 embedded QWebEngineView (Chromium) panels** — sidebar, editor/chat,
terminal, memory manager. Re-polishing Chromium containers under heavy RAM pressure/page-file
swapping is what cost the 75+ seconds.

**Final fix:** runtime switches call the new `ThemeManager.set_active_no_qss(theme)` — updates
`current`/`is_dark` state and emits `theme_changed`, **never touches setStyleSheet**. Each panel
re-themes itself independently (JS `data-theme` pushes, per-widget QTextBrowser restyle). The
full QSS applies once at startup in `_apply_initial_theme()`, before the window is shown.

**Measured result:** theme switch total went from 75,000ms+ → ~240-500ms
(`[THEME-AUDIT] TOTAL_THEME_SWITCH=244.7ms ✓ ACCEPTABLE` in cortex.log).

**Accepted trade-off:** native Qt chrome (menu bar, toolbars, splitters, status bar) keeps
old-theme colors until the next restart.

### Bug 2: "System" theme forced dark even when Windows was in light mode — FIXED

Two compounding defects:
1. `MemoryManagerBridge.setTheme()` whitelisted only `("dark", "light")` — clicking **System**
   silently coerced to `"dark"` before reaching any theme logic. Now accepts `"system"`.
2. The literal string `"system"` was written into the `data-theme` HTML attribute. That matches
   **no CSS rule** (only `[data-theme="dark"]`/`[data-theme="light"]` exist), so the page fell
   back to the default look regardless of OS preference. Fixed by adding
   `getResolvedTheme()` (`@pyqtSlot(result=str)`) which always returns real `"dark"`/`"light"`
   resolved via the OS registry check; `memory_management.js` and
   `_defer_memory_manager_sync()` in main_window.py now push only resolved values.
   `getTheme()` (raw setting) is kept solely for highlighting the active picker button.

**Rule going forward:** "system" is a *setting*, never a CSS state — every `data-theme` write
must pass through resolution.

### Bug 3: Settings-page sidebar unreadable in light mode — FIXED

`memory_management.css` defined only the dark palette in `:root` (`--text: #edf2ff`,
`--muted: #9ba6bf`). In light mode the left nav (General / Appearance / Profile / …) rendered
light-hash text on light backgrounds. Added a `[data-theme="light"]` block at the top of the
file: `--text: #1a1a1a` (near-black), `--muted: #555555`, surfaces `#f5f5f5`/`#efefef`/`#e8e8e8`,
borders `#d0d0d0`, accent `#0078d4`.

### Bug 4: File-explorer sidebar had no light mode at all — FIXED

`sidebar.html` had only dark CSS variables and `sidebar.py`'s `set_theme()` was literally
`pass`. Added `[data-theme="light"]` variable overrides to sidebar.html (dark text `#1e1e1e`,
light surfaces `#f3f3f3`/`#ebebeb`) and implemented `set_theme()` to push the `data-theme`
attribute via `page().runJavaScript()`.

### Bug 5: Startup stutter right after chat history loads — FIXED (measured 1,227x)

Not theme code at all: `semantic_search.index_project()` called `Path.rglob()` **once per
extension (11 full recursive tree walks)**, each descending into `venv/`, `node_modules/`,
`.git/` before filtering. Measured on this project: **11.4 seconds** of background-thread CPU/IO
that starved the GUI thread (a 5s QTimer fired 9s late — the felt "small freeze"). Replaced with
a single `os.walk()` pruning excluded dirs in-place (`dirnames[:] = ...`): **9.3ms, identical
687-file result**. Indexing kickoff additionally delayed 4s after project open so it never
competes with startup chat-restore timers.

### Bug 6: Settings pollution from the theme picker — FIXED

`memory_management.js` called both `bridge.setSetting("theme", …)` (writing an orphaned
`ui.theme` key) and `bridge.setTheme(…)` (the real path). The redundant `setSetting` call was
removed; `setTheme()` alone persists.

### Bug 7 (evening sweep): Dark leakage everywhere in light mode — FIXED

User report: light mode showed dark leaks all over — toolbar Run/Hide-panel
icons invisible (light-gray on light), menu bar/status bar stayed dark, chat
panel fully dark, Monaco editor fully dark, native title bar black.
Seven distinct root causes, all fixed:

| # | Leak | Root cause | Fix |
|---|------|-----------|-----|
| 7a | Whole chat panel dark | `tokens.set_theme()` was a stub — "only dark supported", always returned DARK | Real `LIGHT` palette (134-key parity, GitHub-Light derived) + `set_theme` honors mode |
| 7b | Chat/tool-cards/spinners still dark even after 7a | Seven modules did `from tokens import DARK as T` — frozen at import time | New live `_TokenProxy` (`TOKENS`); all 7 imports repointed |
| 7c | Widgets built dark on light startup | Tokens/theme state set AFTER `_build_ui()` | `set_active_no_qss(saved)` + `tokens.set_theme()` now run BEFORE UI build in `__init__` |
| 7d | Monaco editor always dark | `editor.html setTheme()` hardcoded `'cortex-dark'` ("dark mode only") | `cortex-light` theme + `body.light-theme` CSS for tab/path bars; Monaco `create()` reads body class (pre-load race) |
| 7e | Status bar dark forever (even after restart!) | Hardcoded dark widget stylesheet — widget styles override app QSS | `_restyle_status_bar(is_dark)` palette-driven |
| 7f | Toolbar icons invisible in light | `icon_color = "#c8c8c8"` hardcoded; white hover tint | Theme-picked color (`#3c3c3c` light) + `_toolbar_icon_refreshers` re-tint on switch |
| 7g | Menu bar dark after live switch | Only styled by startup QSS | `_restyle_menu_bar(is_dark)` scoped widget QSS |
| 7h | Native title bar always black | DWM dark-mode flag hardcoded `1`, run-once guard | `_apply_title_bar_theme(is_dark)` — flag `1/0`, re-appliable |
| 7i | File-explorer sidebar dark on light startup | Theme JS pushed before sidebar.html loaded → silently lost | `_pending_is_dark` re-pushed in `_on_page_loaded` |
| 7j | Unstyled surfaces (tooltips, popups) dark | `main.py` forced a global dark `QPalette` unconditionally | Palette picked from saved theme (resolves "system") before window creation |
| 7k | Chat transcript area stayed dark | Viewport + container hardcoded `background: #1e1e1e` ("prevent white flash"); header/footer/new-chat button hardcoded navy | All token-driven (`T['bg']`, `T['border']`, `T['btn_*']`); `ChatPanel.set_theme` re-applies them on live switch |
| 7l | Restored chat text white-on-light (unreadable) | Saved message HTML carries the SAVING session's theme colors as inline styles — `setDefaultStyleSheet` can't override inline styles | `_adapt_restored_html_to_theme()` remaps known token colors (prose + cached code highlights) to the active theme at restore |

All chrome restyles are scoped widget-level calls wired into
`_apply_chrome_theme(is_dark)` — called from `_apply_initial_theme()` (startup)
and BATCH 3 of `_set_theme()` (live switch). Still ZERO runtime
`QApplication.setStyleSheet()` calls.

### Regression guards

All of the above are locked in by `tests/test_release_suite.py` (45 tests), including:
`test_tokens_light_theme_is_real_and_live`,
`test_no_module_freezes_dark_tokens_at_import`,
`test_editor_html_supports_light_theme`,
`test_main_window_chrome_is_theme_aware`,
`test_sidebar_repushes_theme_after_page_load`,
`test_runtime_theme_switch_never_calls_qapplication_setstylesheet`,
`test_memory_manager_system_theme_not_forced_to_dark`,
`test_system_theme_never_written_literally_to_data_theme`,
`test_sidebar_supports_light_mode_colors`,
`test_semantic_indexer_single_tree_walk`,
`test_background_indexing_starts_delayed_not_at_startup`.

---

## Files Changed (9 total — 7 core + 2 freeze-fix)

### 1. `src/config/theme_manager.py` — Theme Engine

**69 lines | Created (replaced old 63-line dark-only stub)**

Central singleton that manages QSS theme application. Loads QSS files from `src/ui/themes/` and applies them via `QApplication.setStyleSheet()`.

| Member | What it does |
|--------|-------------|
| `VALID_THEMES = ("dark", "light")` | Allowed theme names |
| `THEME_FILES = {"dark": "dark.qss", "light": "light.qss"}` | Maps theme → QSS filename |
| `apply(theme_name, app)` | Loads QSS file from disk, calls `app.setStyleSheet()`, emits `theme_changed` |
| `toggle(app)` | Switches dark↔light, returns new theme name |
| `current` (property) | Returns `"dark"` or `"light"` |
| `is_dark` (property) | Returns `True`/`False` |
| `get_theme_manager()` | Module-level singleton accessor |

**Key behavior:** No inline QSS — reads from disk files for both themes. This keeps themes editable without touching Python code.

---

### 2. `src/ui/themes/light.qss` — Light QSS Stylesheet

**398 lines | Updated 2026-07-08 with VS Code/Excel-inspired palette**

Complete Qt stylesheet for light mode. Color palette:

| Role | Color | Notes |
|------|-------|-------|
| Main window bg | `#F5F5F5` | Very Light Gray |
| Ribbon / raised surfaces | `#E0E0E0` | Light Gray (menu bar, tool bar, status bar) |
| Inactive tabs, borders | `#D0D0D0` / `#B0B0B0` | Medium Gray |
| Active tab / inputs | `#FFFFFF` | White |
| Text primary | `#000000` | Black |
| Text muted | `#6D6D6D` | Gray (inactive tab labels) |
| Accent (pressed, focus, active) | `#4CAF50` | Green — replaces old blue `#0078D4` |
| Hover accent | `#43A047` | Darker Green |
| Selection highlight | `#E8F5E9` | Light green tint — replaces old blue `#CCE4F7` |
| Close button | `#F44336` | Red (tab close hover) |
| Custom title bar minimize | `#FFC107` | Yellow (reference only, in comments) |
| Custom title bar maximize | `#4CAF50` | Green (reference only, in comments) |
| Notification badge | `#FF4081` | Pink (reference only, in comments) |
| Search / info icon | `#2196F3` | Blue (reference only, in comments) |

**Palette change (2026-07-08):** Switched from Anthropic warm-beige / Windows-blue to cool-gray with green accent. The Anthropic palette (`#ECE9E0` warm beige, `#C96A3E` terracotta accent) is preserved as a design reference in `Docs/cortex-light-mode.md`.

**What's styled:** QMainWindow, QMenuBar, QMenu, QToolBar, QTabWidget, QTabBar, QTreeView, QListWidget, QPushButton, QLineEdit, QTextEdit, QComboBox, QScrollBar, QSlider, QStatusBar, QLabel, QGroupBox, QCheckBox, QHeaderView, plus custom `#user_bubble`, `#ai_bubble`, `#sidebar_separator`, `#icon_strip`.

---

### 3. `src/main_window.py` — Main Window Integration

**7594 lines | Modified (3 functions + 1 class)**

#### `CleanTabBar` class (line 141)

Custom QTabBar with hand-painted close button. **Added light palette**:

- `__init__` now reads initial theme from `get_theme_manager().is_dark`
- `set_dark(bool)` actually sets `self._is_dark` and calls `update()`
- `paintEvent` has dual color arrays:
  - **Dark:** `#181818`/`#1f1f1f`/`#141414` backgrounds, `#228df2` accent
  - **Light:** `#FFFFFF`/`#E0E0E0`/`#F5F5F5` backgrounds, `#4CAF50` accent

#### `_set_theme(theme)` (line 1108)

Called from memory manager when user picks a theme in Settings → Appearance. Propagates theme to ALL panels:

```
theme_manager.apply() → QSS change
  → _ai_chat.set_theme(is_dark)
  → _sidebar.set_theme(is_dark)
  → _webview_panel.set_theme(is_dark)
  → _editor_tabs.update_theme(is_dark)
  → _update_terminal_theme(is_dark)
  → ALL XTermWidget instances: term.set_theme(is_dark)
  → _push_theme_to_memory_manager(theme)
```

#### `_apply_initial_theme()` (line 1847)

Called at startup. Reads saved theme from `self._settings.theme`, applies it, then propagates to all panels (same propagation list as `_set_theme`).

#### `_update_terminal_theme(is_dark)` (line 1887)

Updates terminal/editor tab pane coloring:
- Dark: `#1e1e1e` bg / `#3e3e42` border
- Light: `#ECE9E0` bg / `#CCC9C0` border

#### `_push_theme_to_memory_manager(theme)` (line 1900)

When the memory manager dialog is open, pushes the theme change into the web view via `runJavaScript()` so the Settings UI updates its `data-theme` attribute without needing a page reload.

---

### 4. `src/ui/dialogs/memory_manager.py` — Memory Manager Bridge

**1571 lines | Modified (2 new slots)**

#### `setTheme(theme)` — `@pyqtSlot(str)` (line 855)

Called from JS when user clicks a theme button in Settings → Appearance:

1. Validates theme is `"dark"` or `"light"`
2. Persists to `self._settings.theme`
3. Walks top-level widgets to find main window (has `_set_theme`), calls it
4. This triggers full IDE theme switch

#### `getTheme()` — `@pyqtSlot(result=str)` (line 881)

Called from JS on page load to determine which theme is active. Returns `get_theme_manager().current`.

---

### 5. `src/ui/html/memory_manager/memory_management.html` — Settings Page HTML

**Modified — Appearance section**

- Line 2: `<html lang="en" data-theme="dark">` — initial state, overridden by JS on load
- Line 33: Added nav button: `<button class="nav-item" data-section="appearance">...Appearance</button>`
- Lines 116–127: Added Appearance content panel with two theme buttons:
  - `<button class="theme-option" data-theme="dark" id="themeDark">Dark</button>`
  - `<button class="theme-option" data-theme="light" id="themeLight">Light</button>`

---

### 6. `src/ui/html/memory_manager/memory_management.css` — Settings Page CSS

**Modified — ~70 `[data-theme="light"]` selector blocks added (line 2386+)**

Key overrides:

| Selector | Dark value | Light value |
|----------|-----------|-------------|
| `:root` / `body` bg | `#0e1116` | `#f0f2f5` |
| `.settings-nav` bg | `#141923` | `#e8eaed` |
| `.settings-card` bg | `#1a2130` | `#ffffff` |
| `.settings-card` border | `#293247` | `#dde0e4` |
| text color | `#edf2ff` | `#1a1a2e` |
| muted text | `#9ba6bf` | `#666d7a` |
| accent | `#4da3ff` | `#0052cc` |
| scrollbar thumb | `#3d4a67` | `#c0c6cf` |

Also styled: `.nav-item`, `.nav-search input`, `.stat-card`, `.setting-btn`, `.setting-select`, `.switch-slider`, `.modal-overlay`, `.theme-option.active`, `.profile-name`, `.insight-item`, `.toast-host`, `.md-code-block`, `.md-inline-code`, `.activity-header`, `.heatmap-legend`, `.back-btn:hover`.

---

### 7. `src/ui/html/memory_manager/memory_management.js` — Settings Page JS

**Modified — Theme initialization + click handler**

#### Initialization (line 311)

On bridge ready, calls `bridge.getTheme()` to get current theme from Python, then:
- Sets `document.documentElement.setAttribute("data-theme", theme)`
- Highlights the matching `.theme-option` button

#### Click handler (line 380)

When user clicks a `.theme-option` button:
1. Reads `btn.dataset.theme`
2. Updates active class on buttons
3. Sets `data-theme` attribute on `<html>` (immediate CSS toggle)
4. Calls `bridge.setSetting("theme", theme)` (persist)
5. Calls `bridge.setTheme(theme)` (triggers full IDE switch via Python)

---

## Freeze Prevention System

### Problem: Repaint Storm Under Memory Pressure

When RAM is elevated (>80%) or critical (>90%), theme switching causes a multi-second UI freeze:

| Stage | What happens |
|-------|-------------|
| 1 | `app.setStyleSheet(new_qss)` triggers Qt to **repaint every widget** in the application |
| 2 | Chat panel's `set_theme()` iterates ALL ThoughtsBlock children — each calls `_apply_theme_styles()` + `_refresh_body_style()` (5 `setStyleSheet()` calls per block) |
| 3 | With 71+ blocks visible, that's **355+ synchronous repaints** while system is memory-starved |
| 4 | OS swaps to disk → multi-second blocking → UI appears frozen |

### Three-Layer Fix (2026-07-08)

#### Layer 1: `theme_manager.py` — Suppress Qt Repaint Storm

```python
# Before (causes repaint storm):
app.setStyleSheet(qss)

# After (suppresses event dispatch during swap):
app.setUpdatesEnabled(False)    # Qt queues paint events instead of dispatching
app.setStyleSheet(qss)          # QSS swap happens instantly (no repaints yet)
app.setUpdatesEnabled(True)     # ONE combined repaint for all queued events
```

| Before | After |
|--------|-------|
| N paint events dispatched synchronously | 1 combined repaint after swap |
| Each widget repaints independently | All queued, dispatched as one batch |
| Multi-second freeze under memory pressure | Near-instant swap |

#### Layer 2: `chat_panel.py` — Batch ThoughtsBlock Re-Theming

```python
# Before (synchronous tight loop):
def set_theme(self, is_dark):
    for block in thoughts_container.findChildren(ThoughtsBlock):
        block._apply_theme_styles()   # 5× setStyleSheet
        block._refresh_body_style()   # 5× setStyleSheet
    # → 71 blocks × 10 setStyleSheet = 710 synchronous repaints

# After (batched with event-loop yielding):
def set_theme(self, is_dark):
    blocks = thoughts_container.findChildren(ThoughtsBlock)
    BATCH = 12  # process 12 blocks per tick
    def _process_batch(start):
        for block in blocks[start:start + BATCH]:
            block._apply_theme_styles()
            block._refresh_body_style()
        if start + BATCH < len(blocks):
            QTimer.singleShot(0, lambda: _process_batch(start + BATCH))
    _process_batch(0)
    # → 6 batches × 12 blocks, yields between each
```

| Before | After |
|--------|-------|
| All blocks re-themed in one loop tick | 12 blocks per event-loop tick |
| Single long blocking call | 6 yields between batches |
| UI frozen for entire duration | UI stays responsive during batch |

#### Layer 3: `chat_panel.py` — Smart Viewport Fallback

```python
# Before (refit ALL visible during restore):
for block in visible_blocks:
    block._refit_body()  # → 71 blocks refitted

# After (cap at 24 most recent):
MAX_FALLBACK_REFIT = 24
for block in visible_blocks[-MAX_FALLBACK_REFIT:]:
    block._refit_body()  # → max 24 blocks refitted
```

During session restore, `mapTo()` fails for every widget (scroll area not yet laid out), so the viewport fallback fires for ALL blocks. The cap prevents this from becoming a 71-block synchronous loop.

---

## Data Flow: Theme Switch End-to-End (v2 — current)

```
User clicks "Light" (or "Dark"/"System") in Settings → Appearance
  │
  ├─ JS (memory_management.js):
  │   document.documentElement.setAttribute("data-theme", <resolved>)
  │   → CSS [data-theme="light"] selectors activate immediately
  │   → bridge.setTheme("light")                  // notify Python (ONLY call — no setSetting)
  │
  ├─ Python (memory_manager.py):
  │   setTheme("light")                            // accepts "dark"|"light"|"system"
  │   → self._settings.theme = "light"            // persist raw setting to disk
  │   → finds MainWindow, calls ._set_theme("light")
  │
  ├─ Python (main_window.py) — _set_theme(), 4 deferred batches:
  │   BATCH 1 (0ms):  theme_manager.set_active_no_qss("light")
  │   │                 // state + theme_changed signal ONLY.
  │   │                 // *** NO QApplication.setStyleSheet() — EVER ***
  │   BATCH 2 (30ms): ai_chat.set_theme(False)     // batched QTextBrowser restyle
  │   │               sidebar.set_theme(False)     // JS data-theme push
  │   │               webview_panel.set_theme(False)
  │   BATCH 3 (60ms): editor_tabs.update_theme(False)
  │   │               _update_terminal_theme(False), all XTermWidget.set_theme(False)
  │   BATCH 4 (90ms): resolved = "dark" if theme_manager.is_dark else "light"
  │                   _push_theme_to_memory_manager(resolved)   // NEVER raw "system"
  │
  └─ Native Qt chrome (menus/toolbars/status bar):
      keeps previous QSS until restart — this is intentional (see Bug 1)
```

## On Restart: Theme Persistence

```
IDE starts
  │
  ├─ main_window.py:
  │   _apply_initial_theme()
  │   → reads self._settings.theme                  // "dark" | "light" | "system"
  │   → theme_manager.apply(saved, app, freeze_widget=self)
  │       // THE ONLY QApplication.setStyleSheet() call in the app's lifetime.
  │       // Runs before the window is shown — cost delays first paint,
  │       // can never freeze an active session.
  │   → propagates to all panels
  │
  └─ When user opens Settings:
      memory_management.js
      → bridge.getTheme()          // RAW setting — highlights the active picker button
      → bridge.getResolvedTheme()  // real dark/light — sets data-theme attribute
```

## Testing Checklist

### Core Theme Switching
- [x] `theme_manager.py` compiles clean (no syntax errors)
- [x] `main_window.py` compiles clean (no syntax errors)
- [x] `memory_manager.py` compiles clean (no syntax errors)
- [x] `light.qss` is valid CSS/QSS (543 lines)
- [x] Switch to light mode via Settings → Appearance → Light (log-verified 2026-07-08)
- [x] Switch back to dark mode via Settings → Appearance → Dark (log-verified 2026-07-08)
- [ ] Tabs (editor + terminal) repaint correctly in both themes
- [x] Settings page itself switches colors immediately
- [x] Settings sidebar nav text readable in light mode (Bug 3 fix)
- [ ] Theme persists across IDE restart
- [ ] All panels (chat, sidebar, webview, terminal) update correctly
- [ ] "System" follows actual Windows light/dark preference (Bug 2 fix — needs user confirmation)

### Freeze Prevention (v2 — no runtime setStyleSheet)
- [x] Theme switch at RAM >90% — no freeze (log: TOTAL_THEME_SWITCH=244.7ms at RAM=94.8%)
- [x] Chat panel re-theme batched, UI responsive (log: 18 batches × ~60ms, event loop yields between)
- [x] Startup: no stutter after chat history load (Bug 5 fix — indexer 1,227x faster + 4s deferral)
- [x] Release suite green: 38/38 tests

### Regression
- [ ] New chat creates block correctly after theme switch
- [ ] Tool output cards render in both themes
- [ ] Thinking blocks expand/collapse in both themes
- [ ] Terminal xterm theme matches IDE theme
