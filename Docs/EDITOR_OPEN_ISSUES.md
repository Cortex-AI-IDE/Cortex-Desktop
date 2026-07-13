# Editor Open Issues — 2026-07-02

## Problem Statement

Two issues reported with `editor.html`:

1. **Startup Logo/Timer**: When IDE first opens, a Cortex logo animation plays with a "Think Limitless. Build Beyond." tagline and a blinking cursor before the editor is ready.
2. **File Open from Sidebar Shows Logo**: When clicking a file from sidebar search results (single click), the editor shows the Cortex logo/animation AGAIN before displaying the file content and header. This "re-boot" creates a visible delay.

**Key clue**: Opening an IMAGE file first, then clicking a `.py`/`.js` file — the header displays correctly. Direct click on `.py`/`.js` without prior image — header does NOT display initially.

---

## Architecture Overview

### Boot Sequence (editor.html)

```
Page Load
  ├── #splash-overlay (logo + "CORTEX" label, fades out on load)
  ├── Monaco editor loads asynchronously
  ├── QWebChannel bridge connects
  └── #no-files-hint (Cortex ring + tagline) shows when no files open
```

### File Open Flow

```
sidebar.html: onTreeNodeClick(node)
  └── callBridge('onFileOpened', node.path)
      └── Python backend reads file, calls editor's openFile()
          └── openFile() → switchToFile()
              ├── hideImagePreview()
              ├── Remove 'hidden' from #editor-container
              ├── Hide #no-files-hint
              ├── Attach Monaco model
              └── renderTabs()
```

---

## Issue 1: Startup Logo Timer

### What happens
1. `#splash-overlay` appears immediately (dark background with breathing SVG)
2. On `window.load` event, `hideSplash()` adds `.fade-out` class (0.4s CSS transition)
3. After 500ms, the overlay is removed from DOM
4. Safety timeout: always removed after 5 seconds

### Root Cause
The splash overlay animation (`splashBreathe: 3s ease-in-out infinite`) and the safety timeout (5s) create a perceivable delay.

### Location
- **File**: `src/assets/editor.html`
- **CSS**: Lines 10-43 (`#splash-overlay` styles, `@keyframes splashBreathe`)
- **JS**: Lines ~2330-2350 (IIFE that handles fade-out)

### Suggested Fix
- Reduce or remove the splash breathe animation (it runs 3x per second, feels like a cursor blink)
- Reduce safety timeout from 5000ms to 2000ms
- Alternatively, show splash only if Monaco takes >500ms to load

---

## Issue 2: Sidebar File Open Shows Logo Before Content

### What happens
1. User searches for a file in sidebar, clicks a result
2. `sidebar.html` calls `bridge.onFileOpened(path)`
3. Python backend calls `editor.html`'s `openFile(path, content, language, true)`
4. **BUG**: The Cortex logo/`#no-files-hint` briefly appears before file content shows

### Root Cause Analysis

**The critical function chain:**

```javascript
// openFile() — creates file entry, calls switchToFile()
function openFile(filePath, content, language, activate) {
    openFiles[filePath] = { path, language, content, modified: false };
    openOrder.push(filePath);
    if (shouldActivate || !activeFilePath) switchToFile(filePath);
    else renderTabs();
}

// switchToFile() — switches Monaco model, hides hints
function switchToFile(filePath) {
    hideImagePreview();
    document.getElementById('editor-container').classList.remove('hidden');
    
    if (editor) {
        // ... attach model ...
        document.getElementById('no-files-hint').style.display = 'none';
    } else {
        // Monaco not ready
        document.getElementById('no-files-hint').style.display = 'none';
        // May try to recreate editor...
    }
    renderTabs(); // Also manipulates hint visibility
}

// renderTabs() — rebuilds tab bar, manages hint visibility
function renderTabs() {
    // ... rebuilds tab HTML ...
    if (openOrder.length === 0) {
        hint.style.display = 'flex';     // SHOW logo
        container.classList.add('hidden');
    } else {
        hint.style.display = 'none';     // HIDE logo
        container.classList.remove('hidden');
    }
}
```

**Race condition hypothesis:**

When `openFile` is called for the FIRST file (editor state = "no files open"):
1. `openOrder.push(filePath)` — now length = 1
2. `switchToFile(filePath)` called
3. Inside `switchToFile`, if `editor` is null (Monaco not loaded or crashed):
   - It tries to recreate the editor
   - Calls `renderTabs()` which should hide the hint
   - BUT if Monaco recreation fails or is slow, the editor container remains empty

**The "image first" success clue:**

When an image is opened first via `showImagePreview()`:
- `openFiles[path]` is populated with `isImage: true`
- `activeFilePath` is set
- `#editor-container` gets `hidden` class
- `#image-preview` is shown

Then when `.py` is clicked:
- `openFiles` already has entries
- `activeFilePath` is already set
- `switchToFile()` sees the editor might be in a different state
- The transition from image→code is smoother because the "no files" state was never the bottleneck

**Most likely root cause**: When opening the FIRST non-image file from sidebar, `switchToFile()` is called before Monaco has fully initialized. The function enters the `else` branch (Monaco not ready), which:
1. Hides the hint
2. Tries to create a new editor
3. Calls `scheduleEditorRetry()` which polls every 500ms

During the retry polling, the `#no-files-hint` is hidden but the editor container is empty — the logo "re-appears" because the container has no content and the entry animation (`cortexEntry: 1.5s`) triggers on the logo element.

### Location
- **File**: `src/assets/editor.html`
- **`openFile()`**: ~line 1830
- **`switchToFile()`**: ~line 1690
- **`renderTabs()`**: ~line 1630
- **`scheduleEditorRetry()`**: ~line 1790
- **Cortex entry animation CSS**: ~line 170

### Suggested Fixes

#### Fix A: Prevent logo re-animation on file open
Add a flag to track if the logo has already been shown once, and skip the entry animation on subsequent shows:

```javascript
var _logoAnimatedOnce = false;

function renderTabs() {
    // ... existing logic ...
    if (openOrder.length === 0) {
        hint.style.display = 'flex';
        if (_logoAnimatedOnce) {
            hint.querySelector('.cortex-icon-ring').style.animation = 'none';
        }
        _logoAnimatedOnce = true;
    } else {
        hint.style.display = 'none';
    }
}
```

#### Fix B: Ensure editor container has content before showing
In `switchToFile()`, ensure the editor container isn't shown empty:

```javascript
function switchToFile(filePath) {
    // ...
    document.getElementById('no-files-hint').style.display = 'none';
    document.getElementById('editor-container').classList.remove('hidden');
    
    // Only show if we have actual content
    if (!editor) {
        // Don't show empty container — keep a loading state instead
        document.getElementById('editor-container').style.opacity = '0.5';
    }
}
```

#### Fix C: Cache editor state to avoid re-initialization
The core issue is that the editor re-enters a "loading" state. Caching the editor instance or pre-initializing Monaco on sidebar ready would prevent this.

---

## Summary

| Issue | Root Cause | Severity | Fix Complexity |
|-------|-----------|----------|----------------|
| Startup logo timer | Splash overlay + breathe animation runs 3s, safety timeout 5s | Low | Simple CSS/JS change |
| Sidebar file open shows logo | Monaco not ready on first file open, enters retry loop showing empty container with logo | Medium | Requires state management fix |

## Files to Modify

| File | What to Change |
|------|---------------|
| `src/assets/editor.html` | CSS: Reduce/remove splash breathe animation. JS: Add logo animation flag, fix switchToFile empty container handling |
