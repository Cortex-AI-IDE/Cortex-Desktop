# RENAME / DELETE Signal Chain Analysis

_Conducted: 2026-06-24_

## Executive Summary

The rename and delete operations span **7 layers** across 6 files. Three bugs exist:

1. **Sidebar tree doesn't auto-refresh** after rename/delete — `_suppress_watcher` blocks fallback `refreshTree()` calls in the JS
2. **Editor tab stays on old name** after rename — `webview_panel.rename_file()` does exact dict key lookup (`old_path not in self._open_files`) which fails silently if path format differs
3. **Editor tab doesn't close on delete** — same path normalization issue in `webview_panel.close_file()`

---

## Complete Signal Chains

### RENAME Flow

```
sidebar.html                     sidebar.py                    sidebar_bridge.py
─────────────                    ───────────                   ─────────────────
context menu                     
  ↓                             
pushNativeModal('rename',        
  path, node.name)               
  ↓                             
_pendingNativeModals.push(...)   
                                 _poll_native_modals() timer
                                   ↓
                                 _handle_native_modal()
                                   QInputDialog.getText()
                                   ↓
                                 bridge.onRename(path, new_name)
                                                                renameFile(path, new_name)
                                                                  Path(path).rename(new_path)
                                                                  file_renamed.emit(old, new) ──→ sidebar.py forwards
                                                                  ↓                                                            ↓
                                                                _suppress_watcher = True                           main_window.py
                                                                _call_js(renameTreeNode(old, name))                  ↓
                                                                                                                   _on_sidebar_file_renamed()
                                                                                                                     updates QTabWidget tabs
                                                                                                                   webview_panel.rename_file(old, new)
                                                                                                                     ↓
                                                                                                                   _open_files lookup → JS renameFileTab()
```

### DELETE Flow

```
sidebar.html                     sidebar.py                    sidebar_bridge.py
─────────────                    ───────────                   ─────────────────
context menu
  ↓
pushNativeModal('delete',
  path, node.name)
  ↓
_pendingNativeModals.push(...)
                                 _poll_native_modals() timer
                                   ↓
                                 _handle_native_modal()
                                   QMessageBox.question()
                                   ↓
                                 bridge.onDelete(path)
                                                                deleteFile(path)
                                                                  safe_delete(path)
                                                                  file_deleted.emit(abs) ──→ main_window (backup)
                                                                  ↓
                                                                _suppress_watcher = True
                                                                _call_js(removeTreeNode(path))
                                 file_deleted.emit(abs_path) ──→ main_window._on_sidebar_file_deleted()
                                 main_window.close_editor_tabs_for_path(abs)
                                   Strategy 1: _editor_tabs._files
                                   Strategy 2: tabToolTip
                                   Strategy 3: webview_panel.close_file()
```

---

## Bug Analysis

### Bug 1: Sidebar tree doesn't auto-refresh after rename/delete

**Root cause:** `_suppress_watcher = True` is set permanently after every rename/delete/create. The JS `renameTreeNode()`/`removeTreeNode()` AJAX calls work, but if they fail (path mismatch, DOM state), their fallback calls `refreshTree()` → Python's `refreshFileTree()` which checks `_suppress_watcher` and returns early.

**Additionally:** `_on_directory_changed()` in sidebar_bridge.py is intentionally a no-op ("Do nothing — let the user click Refresh"). The file watcher debounce `_on_watcher_refresh()` also checks `_suppress_watcher`.

**Fix:** After a short delay (500ms), reset `_suppress_watcher = False` so the next file watcher cycle can pick up any missed changes. Also ensure the AJAX calls use consistent path formats.

### Bug 2: Editor tab stays on old name after rename

**Root cause:** `webview_panel.rename_file()` does:
```python
if old_path not in self._open_files:
    return  # Silent failure!
```

The `old_path` comes from the `file_renamed` signal (via `sidebar_bridge.renameFile`), and `_open_files` keys come from `webview_panel.open_file()`. Both SHOULD use the same format (Windows backslashes from `os.path.join`), but there's no normalization. If there's any mismatch (trailing separator, casing, mixed slashes), the lookup fails silently and `renameFileTab()` is never called.

**Fix:** Add `os.path.normpath` + `os.path.normcase` normalization to the `_open_files` lookup in both `rename_file()` and `close_file()`.

### Bug 3: Editor tab doesn't close on delete

**Root cause:** Same as Bug 2 — `close_file()` does exact dict key lookup on `_open_files` without normalization. `close_editor_tabs_for_path()` in main_window.py uses `os.path.normcase(os.path.normpath(...))` for matching, but the final `webview_panel.close_file(wv_path)` passes the raw path from `_open_files` iteration, not the normalized version.

**Fix:** Add normalization to `webview_panel.close_file()` and ensure `close_editor_tabs_for_path` passes normalized paths.

---

## Files Involved

| File | Role |
|------|------|
| `src/ui/html/sidebar.html` (L2825-2826) | Context menu → `pushNativeModal('rename'/'delete', ...)` |
| `src/ui/components/sidebar.py` (L228-263) | `_handle_native_modal()` — shows Qt dialogs, calls bridge methods |
| `src/ui/components/sidebar_bridge.py` (L459-472, 1207-1228, 1397-1415) | `renameFile()`, `deleteFile()`, `onRename()`, `onDelete()` — OS operations + signals |
| `src/main_window.py` (L1746-1753, 4766-4874) | Signal connections + `_on_sidebar_file_renamed()` + `close_editor_tabs_for_path()` |
| `src/ui/components/webview_panel.py` (L803-845) | `close_file()`, `rename_file()` — editor tab management |
| `src/assets/editor.html` (L1941-1985) | `renameFileTab()` — Monaco model rename |

## Fixes to Apply

1. **`webview_panel.py` — `rename_file()`**: Add `_find_file_key()` helper with normpath/normcase matching
2. **`webview_panel.py` — `close_file()`**: Same normalization
3. **`sidebar_bridge.py` — `onRename()`/`onDelete()`**: Add delayed `_suppress_watcher = False` reset (500ms)
4. **Verify**: `renameFileTab()` in editor.html already handles path normalization (3-level fallback)
