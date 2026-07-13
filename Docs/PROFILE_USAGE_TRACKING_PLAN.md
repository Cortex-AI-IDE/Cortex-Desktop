# Cortex Profile & Usage Tracking System — Implementation Plan

**Version:** 1.0  
**Date:** 2026-06-28  
**Status:** PLANNING (not yet implemented)  
**Author:** Cortex AI Agent  
**Target File:** `src/ui/html/memory_manager/memory_management.html` (+ CSS/JS)  
**Reference:** Cursor IDE Settings → Profile section

---

## Table of Contents

1. [Overview](#overview)
2. [Research & Benchmarks](#research--benchmarks)
3. [Feature Requirements](#feature-requirements)
4. [Sidebar Button & Navigation](#sidebar-button--navigation)
5. [Profile Section UI Design](#profile-section-ui-design)
6. [Usage & Billing Section UI Design](#usage--billing-section-ui-design)
7. [Data Model & Storage](#data-model--storage)
8. [Backend Bridge Methods](#backend-bridge-methods)
9. [Charts & Visualizations](#charts--visualizations)
10. [Login System Design](#login-system-design)
11. [Implementation Phases](#implementation-phases)
12. [File Changes Required](#file-changes-required)
13. [CSS Design Tokens](#css-design-tokens)
14. [References](#references)

---

## Overview

### What We're Building

A **Profile section** inside Cortex Settings (`memory_management.html`) that provides:

1. **User Profile Card** — Avatar, display name, username, plan badge
2. **Usage Statistics Dashboard** — Lifetime tokens, peak tokens, task duration, streaks
3. **Token Activity Graph** — Daily/Weekly/Cumulative bar chart
4. **Activity Insights** — Fast mode usage %, most used reasoning level, skills
5. **Most Used Plugins/Models** — Ranked list of frequently used integrations
6. **Usage & Billing Section** — Plan details, monthly limits, progress bars, reset dates
7. **Login/Authentication** — Sign in/sign out, account management

### Why

Currently Cortex has no profile system, no usage visibility, and no way for users to track their AI consumption. This is a critical feature gap compared to Cursor, ChatGPT Desktop, and Claude Desktop — all of which show usage stats prominently.

### Reference Screenshot (Cursor Profile)

The user provided a screenshot showing Cursor's Profile settings with:
- Orange avatar circle with initials "HA"
- Username `@hakeemph · Free`
- Stats: `209.1M Lifetime tokens`, `58.1M Peak tokens`, `10m 14s Longest task`, streaks
- Token activity graph with Daily/Weekly/Cumulative toggles
- Activity insights: `Fast Mode 56%`, `Most used reasoning Medium - 53%`
- Most used plugins section
- Usage & billing with plan card, progress bar, reset date

---

## Research & Benchmarks

### How Competitors Do It

| App | Profile Features | Usage Tracking | Login |
|-----|-----------------|----------------|-------|
| **Cursor IDE** | Avatar, name, plan badge, email | Token counts, request limits, per-model breakdown, cost in USD | Email/OAuth |
| **ChatGPT Desktop** | Avatar, name, email, plan | Message limits, token meters, progress bars, reset dates | Email/OAuth/Google |
| **Claude Desktop** | Avatar, name, plan | Usage bars, session limits | Anthropic account |
| **VS Code** | No profile (extensions only) | No built-in tracking | Microsoft/GitHub |
| **GitHub Copilot** | GitHub avatar, plan | Completions count, chat messages | GitHub OAuth |
| **Windsurf** | Avatar, name, plan | Flow credits, cascade usage | Email |

### Key Design Patterns Observed

1. **Profile = Left sidebar button** under "Personal" group (Cursor, ChatGPT)
2. **Usage stats = Metric cards** in a horizontal row (4-5 cards)
3. **Activity graph = Bar chart** with time range toggles
4. **Insights = Percentage breakdowns** with colored bars
5. **Billing = Separate section** with plan card + progress bars
6. **Login = Modal popup** or dedicated sub-section

### Sources
- [Cursor Settings UI](https://cursor.com/) — Profile & usage patterns
- [Cursor Pricing & Usage](https://fadamakis.com/understanding-cursor-pricing-2ce7a6fd7930.html)
- [Cursor Usage Monitor Extension](https://open-vsx.org/extension/lixen/cursor-usage)
- [Cursor AI Usage Tracker (VS Code)](https://marketplace.visualstudio.com/items?itemName=mce.cursor-ai-usage-tracker)
- [Cursor Analytics - Jellyfish](https://jellyfish.co/library/cursor-usage-analytics/)
- [Track Cursor Usage - Worklytics](https://www.worklytics.co/blog/tracking-how-employees-utilize-cursor-ai)
- [Tokscale CLI - Token Usage Tracking](https://github.com/junhoyeo/tokscale)
- [Designing Streaks for User Growth](https://www.nuancebehavior.com/article/designing-streaks-for-long-term-user-growth)
- [Streaks - Gamification Best Practices](https://medium.com/design-bootcamp/streaks-the-gamification-feature-everyone-gets-wrong-6506e46fa9ca)
- [Dark UI Best Practices](https://uxdesign.cc/dark-ui-design-principles-and-best-practices-9b9061b86e1)

---

## Feature Requirements

### P0 — Must Have (Phase 1)

| Feature | Description |
|---------|-------------|
| **Profile sidebar button** | 👤 Profile button in settings nav under "Personal" group |
| **Profile card** | Avatar (initials), display name, username, plan badge |
| **Basic usage stats** | 4 metric cards: Lifetime tokens, Peak tokens, Longest task, Current streak |
| **Plan info** | Current plan name, price, upgrade button |
| **Monthly usage bar** | Progress bar showing remaining monthly allowance |

### P1 — Should Have (Phase 2)

| Feature | Description |
|---------|-------------|
| **Token activity graph** | Bar chart with Daily/Weekly/Cumulative toggle |
| **Activity insights** | Fast mode %, reasoning level breakdown, model usage |
| **Most used models** | Ranked list of AI models by usage |
| **Usage & Billing section** | Separate nav item with full billing details |
| **Longest streak** | Track consecutive days of usage |
| **Reset date** | Show when monthly limits reset |

### P2 — Nice to Have (Phase 3)

| Feature | Description |
|---------|-------------|
| **Login/Sign-in** | Email + OAuth authentication flow |
| **Most used plugins** | Track MCP servers, extensions usage |
| **Skills explored** | Track which AI capabilities the user has tried |
| **Usage export** | Download usage data as CSV/JSON |
| **Cost estimation** | Show estimated cost based on token prices |
| **Per-project usage** | Breakdown of token usage by project/workspace |

---

## Sidebar Button & Navigation

### Current Sidebar Structure

```html
<div class="nav-group">
  <div class="nav-group-label">Personal</div>
  <button class="nav-item active" data-section="general">
    ⚙ General
  </button>
</div>
```

### Proposed Change

Add 👤 Profile button between General and the next group:

```html
<div class="nav-group">
  <div class="nav-group-label">Personal</div>
  <button class="nav-item active" data-section="general">
    ⚙ <span>General</span>
  </button>
  <button class="nav-item" data-section="profile">
    👤 <span>Profile</span>
  </button>
</div>
```

### SVG Icon for Profile

```html
<svg width="16" height="16" viewBox="0 0 24 24" fill="none" 
     stroke="currentColor" stroke-width="2" stroke-linecap="round" 
     stroke-linejoin="round">
  <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
  <circle cx="12" cy="7" r="4"/>
</svg>
```

### Navigation Behavior

- Clicking `👤 Profile` shows the Profile section in the main content area
- Clicking `Usage & Billing` (separate button or sub-section) scrolls/billing area
- The Profile section replaces the General section in the right panel
- Same active state pattern as other nav items (`.nav-item.active`)

---

## Profile Section UI Design

### Layout (Wireframe)

```
┌─────────────────────────────────────────────────────┐
│ Profile                                    [Edit]   │
│ Manage your account and view usage statistics.      │
├─────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────┐ │
│ │          ┌──────┐                               │ │
│ │          │  HA  │  hakeemph                      │ │
│ │          │(avtr)│  @hakeemph · Free              │ │
│ │          └──────┘                               │ │
│ └─────────────────────────────────────────────────┘ │
│                                                     │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐│
│ │ 209.1M   │ │ 58.1M    │ │ 10m 14s  │ │ 0 days   ││
│ │ Lifetime │ │ Peak     │ │ Longest  │ │ Current  ││
│ │ tokens   │ │ tokens   │ │ task     │ │ streak   ││
│ └──────────┘ └──────────┘ └──────────┘ └──────────┘│
│                                                     │
│ ┌─────────────────────────────────────────────────┐ │
│ │ Token activity    [Daily] [Weekly] [Cumulative]  │ │
│ │                                                 │ │
│ │  ▓                                            ▓ │ │
│ │  ▓  ▓                                      ▓  ▓ │ │
│ │  ▓  ▓  ▓        ▓  ▓        ▓           ▓  ▓  ▓ │ │
│ │  ▓  ▓  ▓  ▓  ▓  ▓  ▓  ▓  ▓  ▓  ▓  ▓  ▓  ▓  ▓ │ │
│ │  Jul Aug Sep Oct Nov Dec Jan Feb Mar Apr May Jun │ │
│ └─────────────────────────────────────────────────┘ │
│                                                     │
│ ┌──────────────────────┐ ┌────────────────────────┐ │
│ │ Activity insights    │ │ Most used models       │ │
│ │                      │ │                        │ │
│ │ Fast Mode    56%     │ │ 1. DeepSeek V4  42%   │ │
│ │ ██████████░░░░░░░░░  │ │ 2. GPT-5.4      28%   │ │
│ │                      │ │ 3. Qwen 3.7     18%   │ │
│ │ Most reasoning       │ │ 4. Claude Opus   12%   │ │
│ │ Medium - 53%         │ │                        │ │
│ │ ██████████░░░░░░░░░  │ │ No plugins used yet   │ │
│ │                      │ │                        │ │
│ │ Skills explored None │ │                        │ │
│ └──────────────────────┘ └────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

### HTML Structure

```html
<section class="settings-section" data-section="profile">
  <!-- Section Header -->
  <div class="section-header">
    <div>
      <h1>Profile</h1>
      <p class="section-desc">Manage your account and view usage statistics.</p>
    </div>
    <div class="section-actions">
      <button class="setting-btn" id="editProfileBtn">Edit Profile</button>
    </div>
  </div>

  <!-- Profile Hero Card -->
  <div class="settings-card profile-hero">
    <div class="profile-avatar" id="profileAvatar">HA</div>
    <div class="profile-info">
      <h2 class="profile-name" id="profileName">hakeemph</h2>
      <p class="profile-meta">
        <span class="profile-username" id="profileUsername">@hakeemph</span>
        <span class="profile-separator">·</span>
        <span class="profile-plan" id="profilePlan">Free</span>
      </p>
    </div>
  </div>

  <!-- Usage Stats Cards -->
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-value" id="lifetimeTokens">0</div>
      <div class="stat-label">Lifetime tokens</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" id="peakTokens">0</div>
      <div class="stat-label">Peak tokens</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" id="longestTask">0m 0s</div>
      <div class="stat-label">Longest task</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" id="currentStreak">0 days</div>
      <div class="stat-label">Current streak</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" id="longestStreak">0 days</div>
      <div class="stat-label">Longest streak</div>
    </div>
  </div>

  <!-- Token Activity Graph -->
  <div class="settings-card">
    <div class="activity-header">
      <h3>Token activity</h3>
      <div class="activity-toggles">
        <button class="activity-tab active" data-range="daily">Daily</button>
        <button class="activity-tab" data-range="weekly">Weekly</button>
        <button class="activity-tab" data-range="cumulative">Cumulative</button>
      </div>
    </div>
    <div class="activity-chart" id="activityChart">
      <!-- Canvas or SVG bar chart rendered by JS -->
    </div>
  </div>

  <!-- Insights Row -->
  <div class="insights-row">
    <!-- Activity Insights -->
    <div class="settings-card insights-card">
      <h3>Activity insights</h3>
      <div class="insight-item">
        <span class="insight-label">Fast Mode</span>
        <span class="insight-value" id="fastModePercent">0%</span>
      </div>
      <div class="insight-bar">
        <div class="insight-fill" id="fastModeBar" style="width: 0%"></div>
      </div>
      <div class="insight-item">
        <span class="insight-label">Most used reasoning</span>
        <span class="insight-value" id="reasoningLevel">None</span>
      </div>
      <div class="insight-bar">
        <div class="insight-fill" id="reasoningBar" style="width: 0%"></div>
      </div>
      <div class="insight-item">
        <span class="insight-label">Skills explored</span>
        <span class="insight-value" id="skillsExplored">None</span>
      </div>
      <div class="insight-item">
        <span class="insight-label">Total skills used</span>
        <span class="insight-value" id="totalSkills">None</span>
      </div>
    </div>

    <!-- Most Used Models/Plugins -->
    <div class="settings-card insights-card">
      <h3>Most used models</h3>
      <div id="modelUsageList" class="model-usage-list">
        <!-- Dynamically populated -->
        <div class="empty-state-small">
          <p>No model usage data yet</p>
        </div>
      </div>
    </div>
  </div>
</section>
```

### Profile Hero Card Styling

```css
.profile-hero {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 28px 32px;
}

.profile-avatar {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: linear-gradient(135deg, #f97316, #fb923c);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 1px;
  flex-shrink: 0;
}

.profile-name {
  font-size: 22px;
  font-weight: 600;
  color: var(--text);
  margin: 0;
}

.profile-meta {
  font-size: 14px;
  color: var(--muted);
  margin: 4px 0 0;
}

.profile-plan {
  color: var(--accent);
  font-weight: 500;
}
```

### Stats Grid Styling

```css
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  text-align: center;
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.stat-label {
  font-size: 12px;
  color: var(--muted);
  margin-top: 4px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
```

---

## Usage & Billing Section UI Design

### Can Be Part of Profile or Separate Nav Item

**Option A:** Sub-section within Profile page (scroll down)
**Option B:** Separate `📊 Usage & Billing` nav item under "Personal"

**Recommendation: Option B** — Separate nav item for cleaner separation.

### Layout (Wireframe)

```
┌─────────────────────────────────────────────────────┐
│ Usage & billing                                      │
│ View invoices, manage payment, and track limits.     │
├─────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────┐ │
│ │ Your plan                                        │ │
│ │                                                  │ │
│ │ ┌──────────────────────────────────────────────┐ │ │
│ │ │  Free plan                                   │ │ │
│ │ │  $0/mo                                       │ │ │
│ │ │                               [Upgrade plan] │ │ │
│ │ └──────────────────────────────────────────────┘ │ │
│ └─────────────────────────────────────────────────┘ │
│                                                     │
│ ┌─────────────────────────────────────────────────┐ │
│ │ General usage limits                             │ │
│ │                                                  │ │
│ │ Monthly token usage                              │ │
│ │ ████████████████░░░░░░░░░░  67% used            │ │
│ │ 134K / 200K tokens    Resets Jul 19              │ │
│ │                                                  │ │
│ │ Daily request limit                              │ │
│ │ ████████░░░░░░░░░░░░░░░░░░  32% used            │ │
│ │ 32 / 100 requests    Resets tomorrow             │ │
│ │                                                  │ │
│ │ Agent tool calls                                 │ │
│ │ ████████████████████████░░  88% used  ⚠️         │ │
│ │ 440 / 500 calls      Resets Jul 19               │ │
│ └─────────────────────────────────────────────────┘ │
│                                                     │
│ ┌─────────────────────────────────────────────────┐ │
│ │ Per-model breakdown                              │ │
│ │                                                  │ │
│ │ DeepSeek V4      ██████████████░░  72K tokens   │ │
│ │ GPT-5.4          ████████░░░░░░░░  38K tokens   │ │
│ │ Qwen 3.7 Plus    █████░░░░░░░░░░░  21K tokens   │ │
│ │ Claude Opus      ███░░░░░░░░░░░░░  12K tokens   │ │
│ └─────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

### HTML Structure

```html
<section class="settings-section" data-section="usage">
  <div class="section-header">
    <div>
      <h1>Usage & billing</h1>
      <p class="section-desc">View invoices, manage payment, and track your AI usage limits.</p>
    </div>
  </div>

  <!-- Plan Card -->
  <div class="settings-card plan-card">
    <h3>Your plan</h3>
    <div class="plan-info">
      <div class="plan-details">
        <span class="plan-name" id="planName">Free plan</span>
        <span class="plan-price" id="planPrice">$0/mo</span>
      </div>
      <button class="setting-btn primary-btn" id="upgradePlanBtn">Upgrade plan</button>
    </div>
  </div>

  <!-- Usage Limits -->
  <div class="settings-card">
    <h3>General usage limits</h3>
    
    <div class="usage-meter">
      <div class="meter-header">
        <span class="meter-label">Monthly token usage</span>
        <span class="meter-percent" id="monthlyPercent">0%</span>
      </div>
      <div class="meter-bar">
        <div class="meter-fill" id="monthlyFill" style="width: 0%"></div>
      </div>
      <div class="meter-footer">
        <span id="monthlyDetail">0 / 0 tokens</span>
        <span id="monthlyReset" class="meter-reset">Resets —</span>
      </div>
    </div>

    <div class="usage-meter">
      <div class="meter-header">
        <span class="meter-label">Daily request limit</span>
        <span class="meter-percent" id="dailyPercent">0%</span>
      </div>
      <div class="meter-bar">
        <div class="meter-fill" id="dailyFill" style="width: 0%"></div>
      </div>
      <div class="meter-footer">
        <span id="dailyDetail">0 / 0 requests</span>
        <span id="dailyReset" class="meter-reset">Resets tomorrow</span>
      </div>
    </div>

    <div class="usage-meter">
      <div class="meter-header">
        <span class="meter-label">Agent tool calls</span>
        <span class="meter-percent" id="toolCallPercent">0%</span>
      </div>
      <div class="meter-bar">
        <div class="meter-fill" id="toolCallFill" style="width: 0%"></div>
      </div>
      <div class="meter-footer">
        <span id="toolCallDetail">0 / 0 calls</span>
        <span id="toolCallReset" class="meter-reset">Resets —</span>
      </div>
    </div>
  </div>

  <!-- Per-Model Breakdown -->
  <div class="settings-card">
    <h3>Per-model breakdown</h3>
    <div id="modelBreakdown" class="model-breakdown">
      <!-- Dynamically populated -->
    </div>
  </div>
</section>
```

### Sidebar Button for Usage & Billing

```html
<button class="nav-item" data-section="usage">
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" 
       stroke="currentColor" stroke-width="2">
    <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
  </svg>
  <span>Usage & Billing</span>
</button>
```

---

## Data Model & Storage

### Local Storage Structure

All data stored locally in `~/.cortex/profile.json` (encrypted) and `~/.cortex/usage.json`.

#### profile.json

```json
{
  "version": 1,
  "profile": {
    "display_name": "hakeemph",
    "username": "@hakeemph",
    "email": "hakeem@example.com",
    "avatar_color": "#f97316",
    "avatar_initials": "HA",
    "plan": "free",
    "created_at": "2026-06-01T00:00:00Z",
    "last_active": "2026-06-28T22:00:00Z"
  },
  "auth": {
    "logged_in": false,
    "auth_method": null,
    "token_hash": null,
    "expires_at": null
  }
}
```

#### usage.json

```json
{
  "version": 1,
  "lifetime": {
    "total_tokens": 209100000,
    "total_requests": 15420,
    "total_tool_calls": 8230,
    "total_sessions": 342,
    "longest_task_seconds": 614,
    "first_session": "2026-06-01T00:00:00Z"
  },
  "current_period": {
    "start_date": "2026-06-19",
    "end_date": "2026-07-19",
    "tokens_used": 134000,
    "tokens_limit": 200000,
    "requests_used": 32,
    "requests_limit": 100,
    "tool_calls_used": 440,
    "tool_calls_limit": 500
  },
  "streaks": {
    "current_streak_days": 0,
    "longest_streak_days": 2,
    "last_active_date": "2026-06-28",
    "streak_start_date": null
  },
  "daily_usage": {
    "2026-06-28": {
      "tokens": 45000,
      "requests": 12,
      "tool_calls": 28,
      "models": {
        "deepseek-v4": 30000,
        "gpt-5.4": 15000
      }
    }
  },
  "model_usage": {
    "deepseek-v4": { "total_tokens": 72000, "total_requests": 180 },
    "gpt-5.4": { "total_tokens": 38000, "total_requests": 95 },
    "qwen3.7-plus": { "total_tokens": 21000, "total_requests": 52 },
    "claude-opus": { "total_tokens": 12000, "total_requests": 30 }
  },
  "insights": {
    "fast_mode_percent": 56,
    "most_reasoning_level": "medium",
    "reasoning_percent": 53,
    "skills_explored": [],
    "total_skills_used": 0,
    "plugins_used": []
  },
  "peak": {
    "peak_tokens_single_session": 58100000,
    "peak_date": "2026-06-15"
  }
}
```

### Data Tracking Events

The backend (Python) needs to track these events:

| Event | When | What to Record |
|-------|------|---------------|
| `session_start` | Chat session begins | Timestamp, model |
| `session_end` | Chat session ends | Duration, tokens used, model |
| `token_usage` | Every AI response | Model, input_tokens, output_tokens |
| `tool_call` | Every agent tool call | Tool name, duration |
| `model_switch` | User changes model | From model, to model |
| `fast_mode` | Fast mode toggled | On/off, timestamp |
| `agent_mode` | Agent mode used | Timestamp |

---

## Backend Bridge Methods

### New Python Bridge Methods

These methods need to be added to the QWebChannel bridge (in the Python backend):

```python
# Profile methods
def getProfile(self, callback):
    """Return profile.json contents"""
    pass

def setProfile(self, key, value):
    """Update a profile field (display_name, email, etc.)"""
    pass

def setAvatar(self, avatar_color, avatar_initials):
    """Update avatar color and initials"""
    pass

# Usage methods
def getUsageStats(self, callback):
    """Return full usage.json contents"""
    pass

def getUsageForRange(self, range_type, callback):
    """Return usage data for daily/weekly/cumulative"""
    # range_type: "daily" | "weekly" | "cumulative"
    pass

def getCurrentLimits(self, callback):
    """Return current period limits and usage"""
    pass

# Auth methods
def login(self, method, credentials, callback):
    """Authenticate user"""
    # method: "email" | "github" | "google"
    pass

def logout(self):
    """Sign out user"""
    pass

def getAuthStatus(self, callback):
    """Check if user is logged in"""
    pass

# Upgrade methods
def upgradePlan(self, plan):
    """Open upgrade flow"""
    # plan: "pro" | "ultra"
    pass
```

### Internal Tracking (Python-side)

```python
# In agent_bridge.py or a new usage_tracker.py

class UsageTracker:
    """Tracks all AI usage metrics locally."""
    
    def __init__(self, data_dir: str):
        self.usage_file = Path(data_dir) / "usage.json"
        self.data = self._load()
    
    def record_token_usage(self, model: str, input_tokens: int, output_tokens: int):
        """Called after every AI response."""
        total = input_tokens + output_tokens
        today = date.today().isoformat()
        
        # Update daily
        self.data["daily_usage"][today]["tokens"] += total
        self.data["daily_usage"][today]["models"][model] += total
        
        # Update lifetime
        self.data["lifetime"]["total_tokens"] += total
        
        # Update model usage
        self.data["model_usage"][model]["total_tokens"] += total
        
        # Update peak
        if total > self.data["peak"]["peak_tokens_single_session"]:
            self.data["peak"]["peak_tokens_single_session"] = total
        
        # Update streaks
        self._update_streaks(today)
        
        # Update current period
        self.data["current_period"]["tokens_used"] += total
        
        # Update insights
        self._update_insights()
        
        self._save()
    
    def record_tool_call(self, tool_name: str, duration_seconds: float):
        """Called after every agent tool call."""
        today = date.today().isoformat()
        self.data["daily_usage"][today]["tool_calls"] += 1
        self.data["lifetime"]["total_tool_calls"] += 1
        self.data["current_period"]["tool_calls_used"] += 1
        
        if duration_seconds > self.data["lifetime"]["longest_task_seconds"]:
            self.data["lifetime"]["longest_task_seconds"] = duration_seconds
        
        self._save()
    
    def _update_streaks(self, today: str):
        """Update current and longest streak."""
        last = self.data["streaks"]["last_active_date"]
        if last == today:
            return  # Already counted today
        
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        if last == yesterday:
            self.data["streaks"]["current_streak_days"] += 1
        else:
            self.data["streaks"]["current_streak_days"] = 1
        
        if self.data["streaks"]["current_streak_days"] > self.data["streaks"]["longest_streak_days"]:
            self.data["streaks"]["longest_streak_days"] = self.data["streaks"]["current_streak_days"]
        
        self.data["streaks"]["last_active_date"] = today
```

---

## Charts & Visualizations

### Token Activity Bar Chart

**Implementation:** Pure CSS + JS (no external chart library needed)

```
Approach: Flexbox row of bars with dynamic heights

HTML: <div class="chart-bar" style="height: 45%"></div>
CSS:  .chart-bar { width: 24px; background: var(--accent); border-radius: 4px 4px 0 0; }
```

#### Chart Component Design

```html
<div class="activity-chart">
  <div class="chart-bars" id="chartBars">
    <!-- 12 bars for months, or 7 for daily, 4 for weekly -->
    <div class="chart-column">
      <div class="chart-bar" style="height: 45%" title="Jul: 12K tokens"></div>
      <span class="chart-label">Jul</span>
    </div>
    <!-- ... more bars ... -->
  </div>
</div>
```

```css
.activity-chart {
  height: 180px;
  display: flex;
  align-items: flex-end;
  padding: 16px 0;
}

.chart-bars {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  width: 100%;
  height: 100%;
}

.chart-column {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  height: 100%;
  justify-content: flex-end;
}

.chart-bar {
  width: 100%;
  max-width: 32px;
  background: var(--accent);
  border-radius: 4px 4px 0 0;
  min-height: 2px;
  transition: height 0.3s ease;
  opacity: 0.8;
}

.chart-bar:hover {
  opacity: 1;
}

.chart-label {
  font-size: 11px;
  color: var(--muted);
}
```

#### Range Toggle Logic (JS)

```javascript
// When user clicks Daily / Weekly / Cumulative
document.querySelectorAll('.activity-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelector('.activity-tab.active').classList.remove('active');
    tab.classList.add('active');
    const range = tab.dataset.range;
    renderChart(range);
  });
});

function renderChart(range) {
  bridge.getUsageForRange(range, (data) => {
    const bars = document.getElementById('chartBars');
    bars.innerHTML = '';
    const maxVal = Math.max(...data.points.map(p => p.value));
    
    data.points.forEach(point => {
      const col = document.createElement('div');
      col.className = 'chart-column';
      
      const bar = document.createElement('div');
      bar.className = 'chart-bar';
      bar.style.height = `${(point.value / maxVal) * 100}%`;
      bar.title = `${point.label}: ${formatTokens(point.value)}`;
      
      const label = document.createElement('span');
      label.className = 'chart-label';
      label.textContent = point.label;
      
      col.appendChild(bar);
      col.appendChild(label);
      bars.appendChild(col);
    });
  });
}
```

### Usage Progress Bars

Simple CSS-only progress bars:

```css
.meter-bar {
  height: 8px;
  background: var(--surface-2);
  border-radius: 4px;
  overflow: hidden;
  margin: 8px 0;
}

.meter-fill {
  height: 100%;
  border-radius: 4px;
  background: var(--accent);
  transition: width 0.5s ease;
}

/* Warning states */
.meter-fill.warning { background: #ff9800; }
.meter-fill.danger { background: #ff5252; }
```

### Token Formatting Utility

```javascript
function formatTokens(n) {
  if (n >= 1_000_000_000) return (n / 1_000_000_000).toFixed(1) + 'B';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return n.toString();
}

function formatDuration(seconds) {
  if (seconds >= 3600) return Math.floor(seconds / 3600) + 'h ' + Math.floor((seconds % 3600) / 60) + 'm';
  if (seconds >= 60) return Math.floor(seconds / 60) + 'm ' + (seconds % 60) + 's';
  return seconds + 's';
}
```

---

## Login System Design

### Authentication Approaches for Desktop Apps

| Approach | Pros | Cons | Recommended? |
|----------|------|------|-------------|
| **Local-only (no login)** | Simple, private, no server | No sync, no cloud features | ✅ Phase 1 |
| **Email + Password** | Familiar, server-side auth | Needs backend server | Phase 3 |
| **OAuth (GitHub/Google)** | One-click, trusted | Needs OAuth app registration | Phase 3 |
| **License Key** | Simple, offline-capable | No real-time features | Phase 2 |

### Phase 1: Local Profile (No Server)

For the initial implementation, **no login is required**. The profile is purely local:

- User sets display name and avatar locally
- All usage data tracked locally
- No data leaves the machine
- Plan is always "Free" initially

```javascript
// Edit profile modal
function openEditProfile() {
  const modal = document.getElementById('modalHost');
  modal.innerHTML = `
    <div class="edit-profile-modal">
      <div class="modal-header">
        <h2>Edit Profile</h2>
        <button class="modal-close" onclick="closeModal()">✕</button>
      </div>
      <div class="modal-body">
        <div class="avatar-picker">
          <div class="profile-avatar" id="editAvatar">HA</div>
          <div class="color-options">
            <button class="color-dot" data-color="#f97316" style="background:#f97316"></button>
            <button class="color-dot" data-color="#3b82f6" style="background:#3b82f6"></button>
            <button class="color-dot" data-color="#8b5cf6" style="background:#8b5cf6"></button>
            <button class="color-dot" data-color="#10b981" style="background:#10b981"></button>
            <button class="color-dot" data-color="#ef4444" style="background:#ef4444"></button>
            <button class="color-dot" data-color="#f59e0b" style="background:#f59e0b"></button>
          </div>
        </div>
        <label>Display Name</label>
        <input type="text" class="setting-input" id="editDisplayName" value="hakeemph">
        <label>Username</label>
        <input type="text" class="setting-input" id="editUsername" value="@hakeemph">
      </div>
      <div class="modal-footer">
        <button class="setting-btn primary-btn" onclick="saveProfile()">Save</button>
        <button class="setting-btn" onclick="closeModal()">Cancel</button>
      </div>
    </div>
  `;
  modal.classList.remove('hidden');
}
```

### Phase 3: Full Authentication (Future)

When a backend server exists:

```
Login Flow:
1. User clicks "Sign In" in Profile section
2. Modal shows: Email / GitHub / Google options
3. OAuth redirect → callback → token stored locally
4. Profile synced with server
5. Usage data synced (optional)
```

---

## Implementation Phases

### Phase 1 — Basic Profile + Stats (1-2 days)

| Task | Files | Effort |
|------|-------|--------|
| Add `👤 Profile` nav button | `memory_management.html` | 5 min |
| Add `📊 Usage & Billing` nav button | `memory_management.html` | 5 min |
| Create Profile section HTML | `memory_management.html` | 30 min |
| Create Usage section HTML | `memory_management.html` | 20 min |
| Add Profile/Usage CSS styles | `memory_management.css` | 45 min |
| Add JS navigation handler | `memory_management.js` | 15 min |
| Create `UsageTracker` class | New: `src/ai/usage_tracker.py` | 1 hr |
| Wire bridge methods | `agent_bridge.py` | 30 min |
| Create `profile.json` + `usage.json` | Auto-generated | — |
| Test end-to-end | — | 30 min |

### Phase 2 — Charts + Insights (1-2 days)

| Task | Files | Effort |
|------|-------|--------|
| Build bar chart component (CSS+JS) | `memory_management.js/css` | 1 hr |
| Implement range toggle (daily/weekly/cumulative) | `memory_management.js` | 30 min |
| Build progress bars with warning states | `memory_management.css` | 30 min |
| Implement activity insights calculation | `usage_tracker.py` | 1 hr |
| Build model breakdown list | `memory_management.js` | 30 min |
| Add streak tracking logic | `usage_tracker.py` | 30 min |
| Test with real usage data | — | 30 min |

### Phase 3 — Login + Polish (2-3 days)

| Task | Files | Effort |
|------|-------|--------|
| Build edit profile modal | `memory_management.html/js` | 1 hr |
| Implement avatar color picker | `memory_management.js` | 30 min |
| Add toast notifications for profile saves | `memory_management.js` | 15 min |
| (Future) OAuth login integration | New: `auth_manager.py` | 3 hrs |
| (Future) Cloud sync | New: `sync_manager.py` | 4 hrs |
| Responsive polish | CSS | 1 hr |

---

## File Changes Required

### Files to Modify

| File | Change Description |
|------|-------------------|
| `src/ui/html/memory_manager/memory_management.html` | Add Profile nav button, Usage nav button, Profile section HTML, Usage section HTML |
| `src/ui/html/memory_manager/memory_management.css` | Add styles for profile hero, stats grid, activity chart, usage meters, insights cards |
| `src/ui/html/memory_manager/memory_management.js` | Add profile navigation, chart rendering, data binding, edit profile modal, range toggle |

### Files to Create

| File | Purpose |
|------|---------|
| `src/ai/usage_tracker.py` | Python class for tracking all token/request/tool usage, streak calculation, insights |
| `~/.cortex/profile.json` | User profile data (auto-created on first launch) |
| `~/.cortex/usage.json` | Usage statistics data (auto-created on first launch) |

### Files to Integrate With

| File | Integration Point |
|------|------------------|
| `src/ai/agent_bridge.py` | Add bridge methods: `getProfile`, `getUsageStats`, `getUsageForRange`, `setProfile` |
| `src/ui/html/sidebar.html` | (Optional) Add profile button to main sidebar that opens settings to profile section |

---

## CSS Design Tokens

### New Tokens Required

```css
/* Profile-specific tokens */
--profile-avatar-size: 64px;
--profile-avatar-gradient: linear-gradient(135deg, #f97316, #fb923c);
--stat-card-bg: var(--surface);
--stat-card-border: var(--border);
--stat-value-size: 24px;
--stat-label-size: 12px;

/* Chart tokens */
--chart-bar-color: var(--accent);
--chart-bar-hover: var(--accent-2);
--chart-bar-radius: 4px;
--chart-height: 180px;
--chart-label-color: var(--muted);

/* Usage meter tokens */
--meter-height: 8px;
--meter-bg: var(--surface-2);
--meter-fill: var(--accent);
--meter-warning: #ff9800;
--meter-danger: #ff5252;

/* Insight tokens */
--insight-bar-height: 6px;
--insight-fill: var(--accent);
```

### Dark Theme Alignment

All new components must follow the existing dark theme from `SETTINGS_DESIGN_CONCEPT.md`:

| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | `#0e1116` | Page background |
| `--surface` | `#141923` | Card backgrounds |
| `--surface-2` | `#1a2130` | Input/hover backgrounds |
| `--border` | `#293247` | Borders |
| `--text` | `#edf2ff` | Primary text |
| `--muted` | `#9ba6bf` | Secondary text |
| `--accent` | `#4da3ff` | Primary accent |

---

## References

### Design Inspiration
- [Cursor IDE Settings](https://cursor.com/) — Profile page layout, usage stats, activity graph
- [ChatGPT Desktop Settings](https://chatgpt.com/features/desktop/) — Usage meters, plan cards, reset dates
- [Dribbble Streak Dashboard](https://dribbble.com/search/streak-dashboard) — Streak visualization patterns
- [Dribbble Habit Tracker UI](https://dribbble.com/shots/27447630-Habit-Streak-Tracker-Mobile-App-UI-UX-Design) — Streak UI patterns

### Technical References
- [Canvas Bar Chart Tutorial](https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API/Tutorial/Basic_usage) — Pure JS chart rendering
- [CSS Grid Dashboard Layout](https://css-tricks.com/auto-sizing-columns-css-grid-auto-fill-vs-auto-fit/) — Stats grid layout
- [Designing Streaks for Growth](https://www.nuancebehavior.com/article/designing-streaks-for-long-term-user-growth) — Streak gamification best practices
- [Streaks Gamification Guide](https://medium.com/design-bootcamp/streaks-the-gamification-feature-everyone-gets-wrong-6506e46fa9ca) — Avoiding streak pitfalls
- [Tokscale CLI](https://github.com/junhoyeo/tokscale) — Token tracking patterns

### Internal References
- [SETTINGS_DESIGN_CONCEPT.md](SETTINGS_DESIGN_CONCEPT.md) — Existing settings design system, color tokens, component patterns
- [memory_management.html](../src/ui/html/memory_manager/memory_management.html) — Current settings page structure
- [memory_management.css](../src/ui/html/memory_manager/memory_management.css) — Current CSS design system

---

## Decision Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Chart library | Pure CSS+JS (no Chart.js) | Keeps bundle small, no external deps, dark theme easy to control |
| Data storage | Local JSON files | Privacy-first, no server dependency, easy to migrate later |
| Login system | Phase 1 = local-only, Phase 3 = OAuth | Ship fast, add auth later when backend exists |
| Usage tracking location | New `usage_tracker.py` | Separation of concerns from `agent_bridge.py` |
| Avatar system | Initials + color picker | Simple, no image upload needed, matches Cursor pattern |
| Streak calculation | Python-side (not JS) | Data persists across sessions, JS can't reliably track across restarts |
| Progress bar colors | Green < 60%, Yellow 60-85%, Red > 85% | Standard UX pattern for usage limits |

---

*This document is a PLAN ONLY. No code has been implemented yet. Review and approve before proceeding to implementation.*
