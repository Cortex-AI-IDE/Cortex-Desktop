# Cortex IDE — Menu Bar Status Audit

**Audited:** 2026-06-25
**Source:** `src/main_window.py` → `_build_menu()` (line 1257–1401)
**Total Actions:** 66 (60 menu items + 6 keyboard-only shortcuts)

---

## Status Legend

| Icon | Status | Meaning |
|------|--------|---------|
| ✅ | **WORKING** | Fully implemented, does real work |
| ⚠️ | **PLACEHOLDER** | Opens dialog but core functionality removed |
| ❌ | **STUB** | Only logs, shows `pass`, or displays statusbar message |

---

## File Menu

| # | Action | Shortcut | Handler | Line | Status |
|---|--------|----------|---------|------|--------|
| 1 | New Chat | Ctrl+N | `_on_new_chat` | 1006 | ✅ Creates new chat, resets agent state |
| 2 | Open File... | Ctrl+Shift+O | `_open_file_dialog` | 1938 | ✅ Opens file in editor via Monaco |
| 3 | Open Folder... | Ctrl+O | `_open_folder_dialog` | 1965 | ✅ Opens folder, loads sidebar tree |
| 4 | Save | Ctrl+S | `_save_current` | 3959 | ✅ Saves active editor file |
| 5 | Save All | Ctrl+Shift+S | `_save_all` | 4017 | ✅ Saves all open files |
| 6 | Exit | Alt+F4 | `self.close` | — | ✅ Closes window (triggers closeEvent) |

---

## Edit Menu

| # | Action | Shortcut | Handler | Line | Status |
|---|--------|----------|---------|------|--------|
| 7 | Undo | Ctrl+Z | `_undo` | 4909 | ✅ JS `editor.trigger('undo')` |
| 8 | Redo | Ctrl+Y | `_redo` | 4924 | ✅ JS `editor.trigger('redo')` |
| 9 | Cut | Ctrl+X | `_current_editor_action("cut")` | 4584 | ✅ JS clipboard cut |
| 10 | Copy | Ctrl+C | `_current_editor_action("copy")` | 4584 | ✅ JS clipboard copy |
| 11 | Paste | Ctrl+V | `_current_editor_action("paste")` | 4584 | ✅ JS clipboard paste |
| 12 | Select All | Ctrl+A | `_current_editor_action("selectAll")` | 4584 | ✅ JS select all |
| 13 | Find... | — | `_show_find` | 4667 | ✅ JS `actions.find` trigger |
| 16 | Rename... | F2 | `_rename_file` | 4703 | ✅ Triggers sidebar rename flow |
| 17 | Go to Line... | — | `_go_to_line` | 4943 | ✅ JS `editor.action.gotoLine` |
|
---

## View Menu

| # | Action | Shortcut | Handler | Line | Status |
|---|--------|----------|---------|------|--------|
| 24 | Toggle Sidebar | Ctrl+B | `_toggle_sidebar` | 4423 | ✅ Shows/hides left sidebar |
| 25 | Toggle Review Panel | Alt+Ctrl+B | `_toggle_review_panel_menu` | 4483 | ✅ Shows/hides review panel |
| 26 | Toggle Full Screen | F11 | `_toggle_fullscreen` | 4491 | ✅ Toggles fullscreen state |

---

## AI Menu

| # | Action | Shortcut | Handler | Line | Status |
|---|--------|----------|---------|------|--------|
| 27 | Explain Code | Ctrl+Shift+E | `_ai_action("explain")` | 5733 | ✅ Sends code to AI with explain prompt |
| 28 | Refactor Code | Ctrl+Shift+R | `_ai_action("refactor")` | 5733 | ✅ Sends code to AI with refactor prompt |
| 29 | Write Tests | Ctrl+Shift+U | `_ai_action("tests")` | 5733 | ✅ Sends code to AI with test prompt |
| 30 | Debug Help | Ctrl+Shift+H | `_ai_action("debug")` | 5733 | ✅ Sends code to AI with debug prompt |
| 31 | Agent Mode → Build Mode | — | `_set_agent_mode("build")` | 6836 | ✅ Calls `self._ai_agent.set_mode("build")` |
| 32 | Agent Mode → Explore Mode | — | `_set_agent_mode("explore")` | 6836 | ✅ Calls `self._ai_agent.set_mode("explore")` |
| 33 | Agent Mode → Debug Mode | — | `_set_agent_mode("debug")` | 6836 | ✅ Calls `self._ai_agent.set_mode("debug")` |
| 34 | Agent Mode → Plan Mode | — | `_set_agent_mode("plan")` | 6836 | ✅ Calls `self._ai_agent.set_mode("plan")` |
| 35 | Browse Skills... | — | `_show_skills_browser` | 6853 | ✅ Opens skills browser dialog with real skill list |
| 36 | Tasks & TODOs → View Tasks... | — | `_show_todo_manager` | 6896 | ⚠️ PLACEHOLDER — dialog says "Todos managed by AI agent via TodoWrite" |
| 37 | Tasks & TODOs → Add Task... | — | `_add_todo_task` | 6926 | ❌ STUB — statusbar "Todo manager has been removed" |
| 38 | Tasks & TODOs → Complete Task | — | `_complete_todo_task` | 6931 | ❌ STUB — statusbar "Todo manager has been removed" |
| 39 | Permission Settings... | — | `_show_permission_settings` | 6936 | ✅ Opens permission settings dialog with cache controls |
| 40 | Memory Manager... | Ctrl+Shift+M | `_show_memory_manager` | 6979 | ✅ Opens MemoryManagerDialog |
| 41 | AI Chat Focus | Ctrl+Shift+A | `_focus_ai_chat` | 4573 | ✅ Focuses AI chat input, raises window |
| 42 | Clear Chat | — | `_ai_chat.clear_chat` | — | ✅ Clears chat (only if not native chat mode) |

---

## Terminal Menu

| # | Action | Shortcut | Handler | Line | Status |
|---|--------|----------|---------|------|--------|
| 43 | New Terminal | Ctrl+Shift+` | `_new_terminal` | 4226 | ✅ Creates new terminal instance |
| 44 | Kill Terminal | — | `_kill_current_terminal` | 4288 | ✅ Kills active terminal |
| 45 | Toggle Terminal Panel | Ctrl+J | `_toggle_terminal` | 4384 | ✅ Shows/hides terminal panel |

---

## Window Menu

| # | Action | Shortcut | Handler | Line | Status |
|---|--------|----------|---------|------|--------|
| 46 | Minimize | Ctrl+M | `_minimize_window` | 4498 | ✅ Calls `self.showMinimized()` |
| 47 | Zoom | — | `_zoom_window` | 4502 | ✅ Toggles maximize/restore |
| 48 | Close | Ctrl+F4 | `_close_window` | 4509 | ✅ Calls `self.close()` |

---

## Help Menu

| # | Action | Shortcut | Handler | Line | Status |
|---|--------|----------|---------|------|--------|
| 49 | Cortex Documentation | — | `_open_documentation` | 6191 | ✅ Opens docs URL via `webbrowser.open()` |
| 50 | What's New | — | `_show_whats_new` | 6197 | ❌ STUB — only `log.info()`, no URL or dialog |
| 51 | Automations | — | `_show_automations` | 6201 | ❌ STUB — only `log.info()`, no URL or dialog |
| 52 | Local Environments | — | `_show_local_envs` | 6205 | ❌ STUB — only `log.info()`, no URL or dialog |
| 53 | Worktrees | — | `_show_worktrees` | 6209 | ❌ STUB — only `log.info()`, no URL or dialog |
| 54 | Skills | — | `_show_skills_help` | 6213 | ❌ STUB — only `log.info()`, no URL or dialog |
| 55 | Model Context Protocol | — | `_show_mcp_help` | 6217 | ❌ STUB — only `log.info()`, no URL or dialog |
| 56 | Troubleshooting | — | `_show_troubleshooting` | 6221 | ❌ STUB — only `log.info()`, no URL or dialog |
| 57 | Send Feedback | — | `_send_feedback` | 6225 | ✅ Opens feedback URL via `webbrowser.open()` |
| 58 | Start Trace Recording | — | `_start_trace` | 6231 | ❌ STUB — only `log.info()`, no URL or action |
| 59 | Keyboard Shortcuts | F1 | `_show_keyboard_shortcuts` | 6235 | ✅ Opens real QDialog with shortcuts reference |
| 60 | About Cortex | — | `_show_about` | 6183 | ✅ Opens `QMessageBox.about()` dialog |

---

## Keyboard-Only Shortcuts (not in menus)

| # | Action | Shortcut | Handler | Line | Status |
|---|--------|----------|---------|------|--------|
| 61 | Close Tab | Ctrl+W | `_close_current_tab` | 5141 | ✅ Closes active editor tab |
| 62 | Close All Tabs | Ctrl+Shift+W | `_close_all_tabs` | 5152 | ✅ Closes all editor tabs |
| 63 | Next Tab | Ctrl+Tab | `_next_tab` | 5161 | ✅ Switches to next tab |
| 64 | Previous Tab | Ctrl+Shift+Tab | `_prev_tab` | 5173 | ✅ Switches to previous tab |
| 65 | Format Code | Shift+Alt+F | `_format_code` | 4965 | ✅ JS `editor.action.formatDocument` |
| 66 | Toggle Terminal (Debug) | Ctrl+Alt+D | `_toggle_terminal_panel` | 4358 | ✅ Toggles terminal panel |

---

## Panel Toggle Bar (right corner of menu bar)

| Button | Handler | Status |
|--------|---------|--------|
| Left Sidebar | `_toggle_left_sidebar` | ✅ |
| AI Chat | `_toggle_ai_chat_panel` | ✅ |
| Code Editor | `_toggle_code_panel` | ✅ |
| Terminal | `_toggle_terminal_panel` | ✅ |
| Run File | `_run_file` | ✅ |
| Review Panel | `_toggle_review_panel` | ✅ |

---

## Summary

| Status | Count | Percentage |
|--------|-------|------------|
| ✅ WORKING | 52 | 78.8% |
| ⚠️ PLACEHOLDER | 1 | 1.5% |
| ❌ STUB | 13 | 19.7% |
| **Total** | **66** | **100%** |

---

## Broken Items Needing Fix

### AI Menu → Tasks & TODOs
| Item | Issue | Fix Needed |
|------|-------|------------|
| `_show_todo_manager` | Dialog says "removed" — informational only | Wire to real todo system or remove |
| `_add_todo_task` | Statusbar "removed" message | Wire to real todo system or remove |
| `_complete_todo_task` | Statusbar "removed" message | Wire to real todo system or remove |

### Help Menu (7 of 12 items are stubs)
| Item | Issue | Fix Needed |
|------|-------|------------|
| `_show_whats_new` | Only `log.info()` | Add `webbrowser.open()` with changelog URL |
| `_show_automations` | Only `log.info()` | Add `webbrowser.open()` with automations URL |
| `_show_local_envs` | Only `log.info()` | Add `webbrowser.open()` with local envs URL |
| `_show_worktrees` | Only `log.info()` | Add `webbrowser.open()` with worktrees URL |
| `_show_skills_help` | Only `log.info()` | Add `webbrowser.open()` with skills URL |
| `_show_mcp_help` | Only `log.info()` | Add `webbrowser.open()` with MCP URL |
| `_show_troubleshooting` | Only `log.info()` | Add `webbrowser.open()` with troubleshooting URL |
| `_start_trace` | Only `log.info()` | Add `webbrowser.open()` with trace URL or remove |
