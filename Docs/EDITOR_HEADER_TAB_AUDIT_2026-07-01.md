# Editor Header / Tab Bar Audit — 2026-07-01

## Problem Statement

When a `.py` or `.js` file is clicked directly in the sidebar, the **file header (tab name)** does NOT display in the editor tab bar. However, if an **image file** is clicked first, and THEN a `.py` file is clicked, the file header name DOES display correctly.

This indicates a **state initialization or rendering race** that only manifests on the first non-image file open.

---

## Architecture Overview

### HTML Layout (editor.html)

```
#app (flex column, height:100%)
├── #tab-bar                  ← File tabs (dynamically populated by JS)
├── #file-path-bar            ← Breadcrumb path (hidden by default, shown on file open)
│   └── #file-path-parts      ← Path segments
├── #no-files-hint            ← Welcome screen (shown when no files open)
├── #editor-container.hidden  ← Monaco editor (hidden by default)
└── #image-preview.hidden     ← Image preview (hidden by default)
```

### Key CSS

| Element | Default State | When Active |
|---------|--------------|-------------|
| `#tab-bar` | `display: flex; height: 35px` | Always visible (empty = no tabs shown) |
| `#file-path-bar` | `display: none` | `display: flex` when `.visible` class added |
| `#no-files-hint` | `display: flex` | `display: none` when files open |
| `#editor-container` | `display: none` (`.hidden`) | `flex: 1` when `.hidden` removed |
| `#image-preview` | `display: none` (`.hidden`) | `flex: 1` when `.hidden` removed |

---

## Key Functions & Flow

### 1. `openFile(filePath, content, language, activate)` — Line 1900

**Purpose**: Called by Python bridge when a file should be opened in the editor.

**Flow**:
```
openFile()
├── If file already open:
│   ├── Clear modified flag
│   ├── Update content if different
│   ├── forceSetContent() → setEditorContent()
│   └── if shouldActivate → switchToFile()
├── If new file:
│   ├── Create openFiles[filePath] entry
│   ├── Push to openOrder[]
│   └── if shouldActivate → switchToFile()
└── Notify Python bridge
```

**Key detail**: `activate` defaults to `true`. The gate `_intendedActiveFile` controls whether this file should actually become active.

### 2. `switchToFile(filePath)` — Line 1746

**Purpose**: Switches the editor to display the specified file.

**Flow**:
```
switchToFile()
├── Guard: if !openFiles[filePath] → return
├── Set _intendedActiveFile = filePath
├── If file isImage:
│   ├── activeFilePath = filePath
│   ├── updateFilePathBar()
│   ├── renderTabs()
│   └── return  ← Does NOT touch editor-container or image-preview
├── hideImagePreview()
├── editor-container.classList.remove('hidden')
├── If same file already active → setEditorContent() + return
├── Set activeFilePath = filePath
├── updateFilePathBar()
├── If editor exists:
│   ├── Cancel pending setContentTimer
│   ├── Get/create per-file model
│   ├── editor.setModel(m)
│   └── Clear Monaco markers
├── Else (editor not ready):
│   ├── Show editor-container
│   └── Try to recreate Monaco or schedule retry
├── renderTabs()
└── Notify bridge.onCursorChanged()
```

### 3. `renderTabs()` — Line 1611

**Purpose**: Rebuilds the entire tab bar from `openOrder[]` and `openFiles{}`.

**Flow**:
```
renderTabs()
├── Clear tab-bar innerHTML
├── For each file in openOrder[]:
│   ├── Create .tab div with active/modified classes
│   ├── Set data-path, role, aria attributes
│   ├── Create name span (textContent = fileName)
│   ├── Create close button
│   ├── Attach onclick → switchToFile()
│   └── Append to bar
├── If openOrder.length === 0:
│   ├── Show no-files-hint
│   └── Hide editor-container
├── Else:
│   ├── Hide no-files-hint
│   └── Show editor-container
└── updateFilePathBar()
```

### 4. `showImagePreview(filePath, dataUri, fileSize)` — Line 1874

**Purpose**: Shows image preview, hides editor.

**Flow**:
```
showImagePreview()
├── Set img.src = dataUri
├── Set info text
├── editor-container.classList.add('hidden')
├── no-files-hint display = 'none'
├── image-preview.classList.remove('hidden')
├── If not in openFiles → create entry with isImage: true
├── activeFilePath = filePath
├── updateFilePathBar()
└── renderTabs()
```

### 5. `hideImagePreview()` — Line 1895

**Purpose**: Hides image preview.

**Flow**: `image-preview.classList.add('hidden')`

### 6. `closeFile(filePath)` — Line 2013

**Purpose**: Closes a single file tab.

**Flow**:
```
closeFile()
├── Guard: if !openFiles[filePath] → return
├── Dispose Monaco model
├── Delete from openFiles{}, splice from openOrder[]
├── If closing active file:
│   ├── hideImagePreview()
│   ├── If other files open → switch to next
│   └── Else → show no-files-hint, hide editor
├── renderTabs()
└── Notify bridge.onFileClosed()
```

### 7. `closeAllFiles()` — Line 2062

**Purpose**: Closes all files at once.

**Flow**:
```
closeAllFiles()
├── Set isSwitchingFile = true
├── Detach editor model
├── Dispose all per-file models
├── Clear openFiles{}, openOrder[]
├── activeFilePath = null
├── Show no-files-hint, hide editor
├── hideImagePreview()
├── renderTabs()
└── Notify bridge.onFileClosed('__ALL__')
```

---

## The Header Display Bug — Root Cause Analysis

### What the user observes:

1. **Direct `.py`/`.js` click** → No tab header appears in `#tab-bar`
2. **Image click first, then `.py` click** → Tab header DOES appear

### Probable causes:

#### Cause A: `setIntendedActive()` Race Condition

`openFile()` has a gate at line 1907:
```js
var shouldActivate = activate && (_intendedActiveFile === null || filePath === _intendedActiveFile);
```

If Python calls `openFile(path, content, lang, true)` but `_intendedActiveFile` was set to a DIFFERENT path by a prior `setIntendedActive()` call, then `shouldActivate` becomes `false`. When `shouldActivate` is false:
- The file gets added to `openFiles{}` and `openOrder[]`
- `renderTabs()` IS called (line 1940)
- But `switchToFile()` is NOT called

This means the tab should still appear (renderTabs runs), but it won't be the active tab. If this is the ONLY file open, the tab bar shows one non-active tab — which might look like "no header" to the user if the active styling is missing.

#### Cause B: Monaco Editor Not Ready

When the first file is opened, Monaco may not be fully initialized. The `switchToFile()` function handles this with `scheduleEditorRetry()` (line 1843), but there's a gap:

1. `switchToFile()` runs
2. `editor` is null (Monaco not loaded)
3. It shows `editor-container` and schedules retry
4. BUT `renderTabs()` still runs — the tab IS created

So the tab should appear even if Monaco isn't ready. This is unlikely to be the sole cause.

#### Cause C: Python Bridge Call Order

The Python side (`editor.py`) controls when `openFile()` and `setIntendedActive()` are called. If the call sequence is:

1. User clicks `.py` file in sidebar
2. Python calls `setIntendedActive(pyPath)`
3. Python calls `openFile(pyPath, content, 'python', true)`

This should work. But if there's an intermediate step where:
1. User clicks `.py` file
2. Python calls `setIntendedActive(pyPath)`
3. **Some other file's `openFile()` arrives first** (e.g., from a background load)
4. That background `openFile()` sets `_intendedActiveFile` to null or different path
5. The real `.py` `openFile()` arrives but `shouldActivate` is false

#### Cause D: Image Preview Creates a Special State Path

When `showImagePreview()` is called, it:
1. Creates `openFiles[filePath]` with `isImage: true`
2. Pushes to `openOrder[]`
3. Sets `activeFilePath = filePath`
4. Calls `updateFilePathBar()` and `renderTabs()`

Then when a `.py` file is opened next via `openFile()`:
1. The file is new → creates entry in `openFiles{}` and `openOrder[]`
2. `shouldActivate` is true → calls `switchToFile()`
3. `switchToFile()` calls `hideImagePreview()`, shows editor-container
4. `renderTabs()` rebuilds the tab bar with BOTH the image tab AND the `.py` tab

This works because the image preview path **forces** `activeFilePath` and calls `renderTabs()`, establishing the tab bar state. The subsequent `.py` open adds to this established state.

Without the image first, the very first file open might hit a race condition where:
- The tab bar is empty
- Monaco is initializing
- `renderTabs()` runs but the DOM isn't fully settled
- The tab is created but not visible (timing issue)

#### Cause E: `#tab-bar` CSS Overflow

```css
#tab-bar {
    display: flex;
    height: 35px;
    overflow-x: auto;
    overflow-y: hidden;
    flex-shrink: 0;
}
```

If the tab bar has `overflow-x: auto` but the content overflows in a way that the first tab is scrolled out of view, the user might not see it. However, with only one tab, this is unlikely.

---

## Relevant State Variables

| Variable | Type | Purpose |
|----------|------|---------|
| `openFiles` | Object | Map of filePath → file data |
| `openOrder` | Array | Ordered list of open file paths |
| `activeFilePath` | String/null | Currently active file |
| `_intendedActiveFile` | String/null | Which file Python wants active |
| `editor` | Monaco/null | Monaco editor instance |
| `isSwitchingFile` | Boolean | Guard against rapid switches |
| `_isPushingContent` | Boolean | Guard against change events during setValue |

---

## HTML Element IDs Reference

| ID | Tag | Purpose |
|----|-----|---------|
| `#tab-bar` | div | Container for file tabs |
| `#file-path-bar` | div | Breadcrumb path bar |
| `#file-path-parts` | span | Path segments container |
| `#no-files-hint` | div | Welcome screen |
| `#editor-container` | div | Monaco editor mount point |
| `#image-preview` | div | Image preview container |
| `#image-preview-img` | img | Preview image element |
| `#image-preview-info` | div | Image name/size text |

---

## CSS Classes Reference

| Class | Element | Effect |
|-------|---------|--------|
| `.tab` | tab div | Base tab styling |
| `.active` | tab div | Active tab highlight |
| `.modified` | tab div | White dot indicator |
| `.hidden` | editor/image | `display: none` |
| `.visible` | file-path-bar | `display: flex` |
| `.close-btn` | tab span | Close button (×) |

---

## Recommended Investigation Steps

1. **Add logging** to `renderTabs()` to confirm it's being called and `openOrder.length > 0` on first `.py` open
2. **Log `shouldActivate`** in `openFile()` to see if the gate is blocking activation
3. **Check Python call order** in `editor.py` — trace when `setIntendedActive()` vs `openFile()` vs `showImagePreview()` are called
4. **Test with Monaco ready** — if the bug only happens when Monaco is still initializing, the retry mechanism might be the issue
5. **Inspect `#tab-bar` DOM** after first file open — check if the tab div is actually present in the DOM but hidden/overlapped

---

## Actual Root Cause & Fix (2026-07-01)

### Root Cause

The audit's 5 hypothesized causes were partially correct (Cause D was closest) but none identified the precise chain:

1. `_flush_pending_switch()` in `webview_panel.py` sends:
   ```js
   setIntendedActive(path); openFile(path, content, lang, false); switchToFile(path);
   ```
2. `openFile()` with `activate=false` evaluates `shouldActivate = false && (...) = false`
3. With `shouldActivate=false`, only `renderTabs()` runs — **`switchToFile()` is NOT called**
4. `renderTabs()` creates the tab div, but `activeFilePath` was **never set** (it's still `null`)
5. Tab gets class `"tab"` instead of `"tab active"` — renders with `background: #2d2d2d` on `#252526` tab bar = **invisible**
6. Then `switchToFile(path)` runs from the same JS string, but if `filePath === activeFilePath && editor` is true (from a prior state), it **returns early without calling `renderTabs()`** — the invisible tab is never corrected

**Why image-first worked:** `showImagePreview()` always sets `activeFilePath` and calls `renderTabs()`, establishing correct tab state before any `openFile()` runs.

**Why direct .py click failed:** `openFile()` was the first call. With `activate=false` (from `_flush_pending_switch`), `activeFilePath` was never set, and the tab rendered invisible.

### Secondary Issue: CSS Overflow

`#no-files-hint` had `height: 100%` in a flex column container. This caused the welcome screen to overflow the parent by the tab bar's height (35px), potentially obscuring tabs on initial load.

### Fix Applied (4 changes in editor.html)

| # | Location | Change |
|---|----------|--------|
| 1 | `switchToFile()` early-return path | Added `renderTabs()` before `return` so tabs always re-render even on redundant switches |
| 2 | `openFile()` new-file path | Changed gate from `shouldActivate` to `shouldActivate \|\| !activeFilePath` — first file always gets activated |
| 3 | `openFileFromUri()` | Same `!activeFilePath` guard for the large-file URI path |
| 4 | CSS `#no-files-hint` | Changed `height: 100%` to `flex: 1; min-height: 0` — prevents overflow | 

---

## Summary

The header/tab display system has multiple interacting components:
- **Tab rendering** (`renderTabs()`) rebuilds the entire bar from `openOrder[]`
- **File activation** (`switchToFile()`) manages which file is shown
- **Image preview** (`showImagePreview()`) creates a special state that bypasses editor
- **Intended active gate** (`_intendedActiveFile`) prevents stale activations

The root cause was a **UI state synchronization bug**: `openFile()` with `activate=false` rendered tabs without setting `activeFilePath`, producing invisible tabs. The fix ensures the first file always activates and `renderTabs()` is called on all code paths.
