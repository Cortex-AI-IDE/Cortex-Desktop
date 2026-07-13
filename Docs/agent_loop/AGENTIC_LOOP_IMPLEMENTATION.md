# Agentic Loop Mode Implementation — Dual-Mode Architecture

**Date:** 2026-07-07  
**Status:** Integrated with Safety & Permissions UI  
**Architecture:** Autonomy Manager + Loop Engine + Settings Sync

---

## Overview

Cortex now runs in **dual mode**:

1. **Normal Mode (Default)** — `ASK` level autonomy
   - AI requires explicit prompts for each tool action (Write, Edit, Grep, Glob, Bash, etc.)
   - User has full control and visibility
   - Tools: read-only operations (Read, Grep, Glob) allowed; write/exec require permission

2. **Agentic Loop Mode** — `AUTO` level autonomy (when enabled)
   - AI autonomously iterates to solve complex tasks
   - Built-in verification gates (tests, type checking, linting)
   - Iterative refinement based on objective success criteria
   - Includes cost tracking and safety gates
   - Runs the loop engine (src/core/loop_engine/)

---

## User-Facing Implementation

### Settings Toggle (Memory Manager)

**Location:** `src/ui/html/memory_manager/memory_management.html` (lines 698–728)

#### UI Elements
- **Toggle button:** "Enable autonomous looping" (checkbox id=`agenticLoopMode`)
- **Warning card:** Shows when loop mode is active, alerts user to:
  - AI will execute without explicit prompts until goal is verified
  - Requires clear success criteria and budgets
  - Shows orange warning: "⚠ Loop Mode Active"

#### JavaScript Handler

**Location:** `src/ui/html/memory_manager/memory_management.js`

```javascript
SETTINGS_MAP = {
  agenticLoopMode: "ai.agentic_loop_mode",  // Maps toggle to backend setting
}

// Handler: when user toggles, show/hide warning + persist setting
agenticLoopToggle.addEventListener("change", () => {
  persistSetting("agenticLoopMode", agenticLoopToggle.checked);
  // Show/hide warning based on state
  // Toast notification: "ON — AI will autonomously iterate" / "OFF — Normal mode"
});
```

#### Settings Persistence

- Setting saved to: `~/.cortex/settings.json` → `ai.agentic_loop_mode` (boolean)
- Persisted via bridge: `setSetting("ai.agentic_loop_mode", "true"|"false")`
- Loaded on app startup and settings refresh

---

## Backend Integration

### Agent Bridge (agent_bridge.py)

**Location:** `src/ai/agent_bridge.py` (lines 2422–2448)

#### Initialization
```python
# In CortexAgentBridge.__init__():

self._agentic_loop_mode: bool = False  # Current setting state
self._autonomy_level: str = "ask"      # "ask" or "auto"

# Load from settings on startup
try:
    from src.config.settings import get_settings
    from src.core.autonomy_manager import get_autonomy_manager, AutonomyLevel
    
    settings = get_settings()
    loop_mode = settings.get('ai', {}).get('agentic_loop_mode', False)
    self._agentic_loop_mode = bool(loop_mode)
    self._autonomy_level = "auto" if loop_mode else "ask"
    
    # Sync autonomy manager
    autonomy_mgr = get_autonomy_manager()
    target = AutonomyLevel.AUTO if loop_mode else AutonomyLevel.ASK
    autonomy_mgr.set_level(target)
    
    log.info(f"Agentic Loop Mode: {loop_mode} → Autonomy: {autonomy_mgr.get_level().value}")
except Exception as e:
    log.warning(f"Failed to load agentic_loop_mode: {e}")
```

### Autonomy Manager Integration

**Location:** `src/core/autonomy_manager.py`

The autonomy manager enforces tool permissions based on the autonomy level:

| Level | Behavior |
|-------|----------|
| `ASK` | All tool actions require user permission |
| `AUTO` | Read tools allowed; write/exec pass safety gates |
| `PLAN` | All approved tools in plan scope allowed |

#### Tool Category Mapping (autonomy_manager.py, lines 55–87)

- **READ** (always allowed): `Read`, `Glob`, `Grep`, `WebFetch`, `WebSearch`
- **WRITE** (needs gate): `Write`, `Edit`, `FileWrite`, `FileEdit`
- **EXEC** (safety checked): `Bash`, `PowerShell`, `Terminal`, `Loop`
- **DESTRUCTIVE** (always asks): file deletion, `git push --force`, etc.
- **SOCIAL** (always asks): `AskUserQuestion`, external APIs

#### Permission Flow (agent_bridge.py, lines 7410–7437)

```python
async def _dispatch_tool(self, tool_name: str, tool_id: str, args: Dict[str, Any]):
    try:
        # Autonomy gate check
        autonomy_mgr = get_autonomy_manager()
        if autonomy_mgr.get_level().value != "ask":
            decision = autonomy_mgr.check_action(tool_name, args)
            if decision.requires_permission:
                # Blocked or requires user confirmation
                # (unless it's Bash in AUTO mode with _always_allowed)
                return ToolResult(error=decision.reason, success=False)
        
        # Tool execution continues if permission granted/not needed
        return await _execute_tool(tool_name, args)
```

---

## Loop Engine Integration

### Loop Orchestrator

**Location:** `src/core/loop_engine/loop_orchestrator.py`

When agentic loop mode is active and the user provides a goal with success criteria:

1. **DISCOVER** — Baseline verification (tests, type checks, linting)
2. **PLAN** — AI plans the next highest-impact fix
3. **ACT** — AI executes one targeted change (Read/Write/Edit/Bash)
4. **VERIFY** — Run the gate commands (exit codes only, no model judgment)
5. **REVISE** — Compare results, detect progress, update state
6. **REVIEW** — Reviewer sub-agent audits the diff (maker/checker split)
7. **HALT** — Stop on budget exhaustion, stall, or user abort

Triggered via: `agent.executeAgentic(action="start"|"verify"|"stop")`

---

## Safety & Permission Gates

### Hard Blocks (Always Ask, Even in AUTO Mode)

From autonomy_manager.py, lines 89–119:

```python
DANGEROUS_COMMAND_PATTERNS = [
    "rm -rf /", "git push --force", "git reset --hard",
    "drop table", "truncate table", "chmod 777",
    "sudo", "format", "mkfs.", "diskpart", ...
]
```

Any Bash/PowerShell command matching these patterns triggers a permission prompt, **regardless of autonomy mode.**

### Reviewer Auto-Rejects (Loop Mode §5.1)

When loop reaches green (all tests pass) and reviewer audits the diff:

- **Auto-reject if:** any file changed outside `allowPaths`
- **Auto-reject if:** test files deleted or assertion count reduced
- **Auto-reject if:** secret-like strings added (API keys, passwords)

---

## Settings Schema

### New Setting: `ai.agentic_loop_mode`

**Type:** Boolean  
**Default:** `false` (Normal Mode)  
**Location:** `~/.cortex/settings.json`

```json
{
  "ai": {
    "model": "deepseek-v4",
    "agentic_loop_mode": false,
    "context_window": 200000
  },
  "safety": {
    "require_approval": true,
    "allow_file_delete": false
  }
}
```

---

## User Workflow

### Enabling Agentic Loop Mode

1. Open **Settings** → **Safety & Permissions**
2. Scroll to **Agentic Loop Mode** section
3. Toggle **"Enable autonomous looping"**
4. See warning card appear: "⚠ Loop Mode Active — AI will execute without prompts"
5. Setting persists to settings.json automatically

### Using Loop Mode

1. Write a clear goal: *"All tests in tests/auth/ pass; tsc and eslint clean"*
2. Define success criteria (which tests, linters to run)
3. Set budget: max iterations (default 8), max tokens (default 500k), max USD (default $2.00)
4. Click **"Run Loop"** or **"Start Agentic Loop"**
5. Cortex iterates autonomously:
   - ✅ Runs tests → sees failures
   - ✅ Fixes one issue per iteration
   - ✅ Verifies improvement
   - ✅ Loops until green or budget exhausted
   - ✅ Reviewer audits final diff
   - ✅ Reports cost per accepted change

### Disabling Agentic Loop Mode

1. Same settings panel
2. Toggle **"Enable autonomous looping"** OFF
3. Cortex returns to Normal Mode (ASK autonomy)
4. AI requires explicit prompts for all actions

---

## Files Modified

| File | Change | Purpose |
|------|--------|---------|
| `src/ui/html/memory_manager/memory_management.html` | Added toggle + warning card (lines 698–728) | UI for agentic loop mode |
| `src/ui/html/memory_manager/memory_management.js` | Added SETTINGS_MAP entry + handler (lines 90, 379–397) | Persist setting + show/hide warning |
| `src/ai/agent_bridge.py` | Init agentic mode + sync autonomy (lines 2422–2448) | Load setting and apply autonomy level |
| `src/config/settings.py` | *No changes* | Setting auto-persisted via setSetting() |
| `src/core/autonomy_manager.py` | *No changes* | Existing implementation used |
| `src/core/loop_engine/loop_orchestrator.py` | *No changes* | Existing implementation used |

---

## Testing Checklist

- [ ] Toggle appears in Settings → Safety & Permissions
- [ ] Warning card shows when ON, hides when OFF
- [ ] Setting persists after app restart
- [ ] Normal Mode (OFF): AI prompts for Write/Bash/Edit
- [ ] Loop Mode (ON): AI autonomously executes without prompts (with safety gates)
- [ ] Dangerous commands still blocked (git push --force, rm -rf, etc.)
- [ ] Loop engine runs when agentic mode ON + goal provided
- [ ] Reviewer audits diff before finalizing loop
- [ ] Cost tracker shows per-iteration cost + total cost
- [ ] Budget constraints enforced (iterations, tokens, USD, wall-clock time)

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ Settings UI (Memory Manager)                                    │
│  └─ Agentic Loop Mode Toggle (checkbox)                        │
│  └─ Persisted to: ai.agentic_loop_mode (boolean)               │
└──────────────────────┬──────────────────────────────────────────┘
                       │ setSetting()
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ Config Layer (settings.json)                                    │
│  └─ ai.agentic_loop_mode: true | false                         │
└──────────────────────┬──────────────────────────────────────────┘
                       │ get_settings()
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ Agent Bridge (CortexAgentBridge.__init__)                      │
│  ├─ Load ai.agentic_loop_mode setting                          │
│  ├─ Set self._agentic_loop_mode (bool)                         │
│  └─ Sync autonomy_mgr.set_level(ASK | AUTO)                   │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ Autonomy Manager (check_action)                                 │
│  ├─ ASK:  all actions require permission                        │
│  ├─ AUTO: read allowed, write/exec gated, dangerous blocked     │
│  └─ PLAN: approved tools allowed, reviewer blocks bad diffs     │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ Tool Dispatch (_dispatch_tool)                                  │
│  ├─ Check autonomy permission                                  │
│  ├─ Execute if allowed (Read, Write, Edit, Bash, etc.)        │
│  └─ Return ToolResult                                          │
└──────────────────────┬──────────────────────────────────────────┘
                       │
            ┌──────────┴──────────┐
            ▼                     ▼
   ┌──────────────────┐  ┌──────────────────┐
   │ Normal Mode      │  │ Loop Mode        │
   │ (ASK autonomy)   │  │ (AUTO autonomy)  │
   │                  │  │                  │
   │ Each action      │  │ Autonomous       │
   │ requires prompt  │  │ iteration:       │
   │                  │  │ DISCOVER→PLAN→   │
   │                  │  │ ACT→VERIFY→      │
   │                  │  │ REVISE→REVIEW→   │
   │                  │  │ FINALIZE         │
   └──────────────────┘  └──────────────────┘
```

---

## Future Enhancements

1. **Per-Project Loop Settings** — Custom loop specs saved to `.cortex/loop.json`
2. **Scheduled Loops** — Cron-based autonomous runs (Phase D in spec)
3. **Cost Dashboard** — Real-time token/USD meter + cost-per-accepted-change analytics
4. **Loop History** — Iteration timeline with diffs, reviewer comments, cost breakdown
5. **Skills Registry** — Persistent rules/conventions loaded into loop context
6. **Checkpoint Resume** — Crash recovery via git checkpoints + state.json

---

## References

- **Spec:** `Docs/agent_loop/agent_loop.md` (full implementation specification)
- **Autonomy Manager:** `src/core/autonomy_manager.py` (tool permission gates)
- **Loop Engine:** `src/core/loop_engine/` (DISCOVER→VERIFY→REVISE state machine)
- **Bridge:** `src/ai/agent_bridge.py` (tool dispatcher + safety integration)
- **UI:** `src/ui/html/memory_manager/memory_management.html` (toggle + warning)
