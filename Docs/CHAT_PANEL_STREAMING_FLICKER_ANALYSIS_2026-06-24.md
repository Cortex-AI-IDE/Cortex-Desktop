# Chat Panel Streaming Flicker & Scroll Analysis

_Conducted: 2026-06-24_

## Executive Summary

The chat panel has **4 interrelated bugs** that create a degraded streaming experience:

1. **Text left-slip** — Streaming text visibly shifts leftward mid-stream
2. **Streaming flicker** — Vertical "vibration" during rapid content updates
3. **Scroll disruption** — User reading position disturbed by new content below
4. **Tool card scroll jump** — Tool insertions/updats cause sudden scroll jumps

All bugs share a **common root cause**: multiple conflicting scroll/repaint operations per 50ms flush cycle.

---

## Architecture Overview

```
AgentSignals.text_delta(chunk)
  → ChatPanel.on_text(chunk)
    → buf += chunk → _prose_debounce(50ms).start()
      → _flush_prose()
        → _markdown_to_clean_html(text)
        → [FAST PATH] cursor.insertHtml(new_chunk_html)
          OR [SLOW PATH] cur.select(Document); cur.insertHtml(full_html)
        → setMinimumHeight(h)          ← layout trigger #1
        → _fit()                       ← layout trigger #2
        → bar.setValue(bar.maximum())  ← scroll trigger #1
      → _prose_debounce fires again → repeat at 50ms
```

---

## Bug 1: Text Left-Slip

### Symptoms
During streaming, text visibly shifts left — first letters of words get cut off at the container edge, then snap back.

### Root Cause Chain
1. `_flush_prose()` has a **FAST PATH** (incremental `cursor.insertHtml()`) and **SLOW PATH** (full `select+insertHtml()`)
2. The FAST PATH appends new HTML at cursor position — BUT `_markdown_to_clean_html()` re-interprets the ENTIRE remaining buffer, producing HTML that may differ from the previous render
3. Small markdown changes (e.g. `*` → `<em>`, `|` → `<table>`) cause the HTML structure to change, which reflows ALL existing text
4. Each reflow changes the document width momentarily → text shifts left/right
5. `setMinimumHeight()` on every flush triggers layout recalculation which can momentarily change the viewport width
6. `_fit()` called EVERY 50ms flush also recalculates — the viewport width may be 0 or stale during these rapid calls

### Root Cause (Single Line)
**Text width is recalculated on every 50ms flush tick, causing continuous reflow.**

### Fix
- Set `document().setTextWidth()` ONCE in `_ensure("prose")` and DON'T recalculate during streaming
- Use `setHtml()` (full rebuild) instead of incremental `insertHtml()` — single consistent render
- Only call `setMinimumHeight()` when height actually changes

---

## Bug 2: Streaming Flicker (Vertical Vibration)

### Symptoms
During streaming, the entire chat view "vibrates" vertically — content jumps up by 1-3px and snaps back, creating a flickering/buzzing effect.

### Root Cause Chain
1. `_flush_prose()` does 3 layout operations per 50ms tick:
   - `cursor.insertHtml()` → triggers `contentsChanged` → `_fit_timer.start()` (60ms delayed)
   - `setMinimumHeight(h)` → triggers layout recalculation
   - `bar.setValue(bar.maximum())` → triggers scrollbar repaint
2. `_flush_think()` (60ms) also calls `bar.setValue(bar.maximum())` independently
3. `_flush_tools()` (50ms) also triggers layout changes independently
4. When thinking + prose + tools are active simultaneously (common during agent work):
   - 3 independent flush timers firing at ~50ms intervals
   - Each calling `bar.setValue(bar.maximum())` independently
   - The scrollbar value oscillates because `bar.maximum()` changes between calls
   - This creates visible vertical "jumps" = flicker
5. On Windows, `container.setUpdatesEnabled(False/True)` causes a full viewport repaint
   - If the content height changed during the freeze, the repaint shows the NEW height
   - The previous frame showed the OLD height
   - The transition between them is the "flicker"

### Root Cause (Single Line)
**Multiple independent scroll operations per paint cycle, each seeing a different `bar.maximum()`.**

### Fix
- Single scroll point per flush: check `_is_at_bottom()` BEFORE mutations, scroll ONCE after
- Eliminate redundant `bar.setValue(max)` calls from `_flush_think()` and `_flush_tools()`
- Use freeze/thaw pattern consistently

---

## Bug 3: Scroll Position Disruption

### Symptoms
When user scrolls up to read previous content, new streaming content below pushes the viewport, disrupting reading position.

### Root Cause
1. `_flush_prose()` ALWAYS calls `bar.setValue(bar.maximum())` unless `_scroll_locked`
2. `_scroll_locked` only activates when user scrolls >60px from bottom
3. During streaming, new content changes `bar.maximum()` which shifts the visible content
4. Even without explicit `bar.setValue()`, the Qt layout system can shift the viewport when content above the visible area grows

### Expected Behavior (Social Media Pattern)
- **User at bottom**: New content appears, view stays pinned to bottom
- **User reading above**: New content appends silently below, view position stays EXACTLY fixed
- **User scrolls to bottom**: Resume auto-scroll

### Fix
- Replace binary `_scroll_locked` with a **proximity check** at each flush
- `_is_at_bottom()`: `dist_from_bottom < 200px` → auto-scroll
- Before mutations: save `bar.value()` and `was_at_bottom`
- After mutations: if `was_at_bottom` → pin to bottom; else → restore saved position

---

## Bug 4: Tool Card Scroll Jump

### Symptoms
When a tool card inserts or updates during streaming, the entire view jumps suddenly.

### Root Cause
1. `on_tool_start()` wraps in `_stabilize_scroll()` which saves position, freezes, then restores
2. `on_tool_end()` also wraps in `_stabilize_scroll()`
3. `_stabilize_scroll()` calls `_fit()` on outgoing prose blocks during the freeze
4. The `_fit()` changes widget height → changes `bar.maximum()`
5. When restoring `bar.setValue(saved_pos)`, the position may be wrong because the content grew

### Fix
- Don't call `_fit()` on outgoing blocks during tool transitions
- Use `_is_at_bottom()` pattern for tool events too
- Skip `_autoscroll()` calls during tool events (already partially done)

---

## Fix Plan

### Step 1: Add `_is_at_bottom()` helper
```python
def _is_at_bottom(self, threshold: int = 200) -> bool:
    bar = self.scroll.verticalScrollBar()
    if bar.maximum() <= 0:
        return True
    return (bar.maximum() - bar.value()) < threshold
```

### Step 2: Rewrite `_flush_prose()`
- Freeze viewport at start
- Use `setHtml()` (full rebuild) instead of incremental `insertHtml()`
- Call `setMinimumHeight()` only when height changes >2px
- Single scroll at end: `_is_at_bottom()` → pin; else → restore
- Thaw viewport at end

### Step 3: Rewrite `_flush_think()`
- Same freeze/single-scroll/thaw pattern
- Remove redundant `bar.setValue(max)` 

### Step 4: Clean up `_flush_tools()`
- Already has freeze/thaw; add scroll-at-bottom check

### Step 5: Remove deferred `_autoscroll()` during streaming
- `_stabilize_scroll()` already handles scroll synchronously
- Deferred `_autoscroll()` (50ms timer) creates SECOND scroll = jitter

---

## Files to Modify

| File | Function | Change |
|------|----------|--------|
| `chat_panel.py` | `_is_at_bottom()` | NEW — proximity check helper |
| `chat_panel.py` | `_flush_prose()` | REWRITE — freeze/full-rebuild/single-scroll |
| `chat_panel.py` | `_flush_think()` | REWRITE — freeze/single-scroll |
| `chat_panel.py` | `_flush_tools()` | MODIFY — add scroll-at-bottom check |
| `chat_panel.py` | `_autoscroll()` | MODIFY — skip during active streaming |

## Expected Results

| Bug | Before | After |
|-----|--------|-------|
| Left-slip | Text shifts left every 50ms | Text stays stable (fixed textWidth) |
| Flicker | 3+ scroll ops per paint cycle | 1 scroll op per paint cycle |
| Scroll disruption | Always scrolls to bottom | Only scrolls when user is at bottom |
| Tool jump | Sudden scroll change on tool events | Smooth, no scroll disruption |
