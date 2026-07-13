# Profile & Usage Tracking — Remaining 15%

**Date:** 2026-06-28  
**Status:** Implementation in progress

---

## What's Already Done (85%) ✅

| Component | Status | Files |
|-----------|--------|-------|
| Profile nav button (👤) | ✅ Done | `memory_management.html` |
| Usage & Billing nav button (💰) | ✅ Done | `memory_management.html` |
| Profile hero card | ✅ Done | `memory_management.html` |
| 5 stat cards (tokens, streaks, etc.) | ✅ Done | `memory_management.html` |
| Token activity graph with toggles | ✅ Done | `memory_management.html` + `.js` |
| Activity insights (fast mode, reasoning, skills) | ✅ Done | `memory_management.html` |
| Most used models list | ✅ Done | `memory_management.html` |
| Most used plugins list | ✅ Done | `memory_management.html` |
| Usage & Billing meters | ✅ Done | `memory_management.html` |
| Per-model breakdown | ✅ Done | `memory_management.html` |
| Plan card + upgrade button | ✅ Done | `memory_management.html` |
| All CSS styles (dark theme, cards, meters, chart) | ✅ Done | `memory_management.css` |
| Edit profile modal | ✅ Done | `memory_management.css` + `.js` |
| Avatar color picker | ✅ Done | `memory_management.js` |
| Chart range toggle (Daily/Weekly/Cumulative) | ✅ Done | `memory_management.js` |
| Token formatting utility | ✅ Done | `memory_management.js` |
| Progress bar warning/danger states | ✅ Done | `memory_management.js` |
| Toast notifications | ✅ Done | `memory_management.js` |

---

## What's Remaining (15%) 🔲

### 1. Backend Bridge Methods — `agent_bridge.py`

**Status:** 🔲 NOT YET INTEGRATED  
**File:** `src/ai/agent_bridge.py`  
**Effort:** 30 min

The following QWebChannel bridge methods need to be added so the frontend HTML/JS can call them:

```python
# Profile methods
def getProfile(self, callback):
    """Return profile.json contents to JS frontend"""
    callback(self.usage_tracker.get_profile())

def setProfile(self, key, value):
    """Update a profile field"""
    return self.usage_tracker.set_profile(key, value)

def setAvatar(self, avatar_color, avatar_initials):
    """Update avatar color and initials"""
    self.usage_tracker.set_avatar(avatar_color, avatar_initials)

# Usage methods
def getUsageStats(self, callback):
    """Return full usage stats to JS frontend"""
    callback(self.usage_tracker.get_usage_stats())

def getUsageForRange(self, range_type, callback):
    """Return chart data for daily/weekly/cumulative"""
    callback(self.usage_tracker.get_usage_for_range(range_type))

def getCurrentLimits(self, callback):
    """Return current period limits"""
    callback(self.usage_tracker.get_current_limits())

def getModelBreakdown(self, callback):
    """Return per-model usage"""
    callback(self.usage_tracker.get_model_breakdown())

def getInsights(self, callback):
    """Return activity insights"""
    callback(self.usage_tracker.get_insights())

# Auth methods (Phase 3 — future)
def login(self, method, credentials, callback):
    """Authenticate user (email/github/google)"""
    pass

def logout(self):
    """Sign out user"""
    pass

def getAuthStatus(self, callback):
    """Check if user is logged in"""
    callback({"logged_in": False})

def upgradePlan(self, plan):
    """Open upgrade flow"""
    pass
```

**Integration steps:**
1. Add `from src.ai.usage_tracker import UsageTracker` at top of `agent_bridge.py`
2. Initialize `self.usage_tracker = UsageTracker()` in `__init__`
3. Add all bridge methods above as `@Slot` decorated methods
4. Wire `record_token_usage()` calls into the AI response handler
5. Wire `record_tool_call()` calls into the tool execution handler

### 2. Wire Tracking Into AI Response Pipeline

**Status:** 🔲 NOT YET INTEGRATED  
**File:** `src/ai/agent_bridge.py`  
**Effort:** 30 min

The tracker exists but isn't called anywhere. These hooks need to be added:

| Hook Point | Where in `agent_bridge.py` | What to call |
|------------|---------------------------|--------------|
| After every AI response | `_handle_ai_response()` or equivalent | `tracker.record_token_usage(model, input_tokens, output_tokens)` |
| After every tool call | `_execute_tool()` or equivalent | `tracker.record_tool_call(tool_name, duration)` |
| On session start | `_start_new_chat()` or equivalent | `tracker.record_session_start()` |
| On model switch | Model selection handler | `tracker.record_model_switch(from, to)` |
| On fast mode toggle | Fast mode handler | `tracker.record_fast_mode(enabled)` |

### 3. Python Backend — `usage_tracker.py`

**Status:** ✅ JUST CREATED  
**File:** `src/ai/usage_tracker.py`

Complete Python class with:
- Token usage tracking per model
- Tool call tracking with duration
- Streak calculation (current + longest)
- Period management (auto-reset after 30 days)
- Chart data generation (daily/weekly/cumulative)
- Profile management (read/write)
- CSV/JSON export

### 4. Data Files

**Status:** ✅ JUST CREATED  
**Files:**
- `.cortex/profile.json` — Default empty profile
- `.cortex/usage.json` — Default empty usage stats

Both files are created with proper schemas matching the plan. They auto-populate as the user interacts with Cortex.

### 5. Phase 3 Features (Future — Not Required Now)

| Feature | Priority | Notes |
|---------|----------|-------|
| OAuth login (GitHub/Google) | P2 | Needs backend server |
| Cloud sync | P2 | Needs backend server |
| Usage export (CSV/JSON) | P2 | `usage_tracker.py` already has export methods |
| Per-project usage | P2 | Needs project ID tracking in daily_usage |
| Skills tracking | P2 | Needs tool registry integration |
| Plugin/MCP tracking | P2 | Needs MCP server usage logging |

---

## Integration Checklist

To complete the remaining 15%, follow these steps in order:

- [ ] **Step 1:** Add `UsageTracker` import + initialization to `agent_bridge.py`
- [ ] **Step 2:** Add bridge methods (getProfile, setProfile, getUsageStats, etc.)
- [ ] **Step 3:** Add `@Slot` decorators for QWebChannel exposure
- [ ] **Step 4:** Wire `record_token_usage()` into AI response handler
- [ ] **Step 5:** Wire `record_tool_call()` into tool execution handler
- [ ] **Step 6:** Wire `record_session_start()` into chat init
- [ ] **Step 7:** Test profile edit → save → reload cycle
- [ ] **Step 8:** Test usage tracking → chart rendering cycle
- [ ] **Step 9:** Verify streak calculation across sessions

---

## Quick Test

Once bridge methods are wired, test in browser console:

```javascript
// Test profile
bridge.getProfile(function(data) { console.log('Profile:', data); });

// Test usage stats
bridge.getUsageStats(function(data) { console.log('Usage:', data); });

// Test chart data
bridge.getUsageForRange('daily', function(data) { console.log('Chart:', data); });

// Test recording (should update the files)
bridge.recordTokenUsage('deepseek-v4', 5000, 2000);
```
