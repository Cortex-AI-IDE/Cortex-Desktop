# Cortex Settings — Design Concept Document

**Version:** 1.0  
**Date:** 2026-06-26  
**Status:** Implemented  
**Author:** Cortex AI Agent

---

## Table of Contents

1. [Overview](#overview)
2. [Design Philosophy](#design-philosophy)
3. [Industry Research & Benchmarks](#industry-research--benchmarks)
4. [Architecture](#architecture)
5. [Navigation Structure](#navigation-structure)
6. [Section Details](#section-details)
7. [Upgrade Modal Design](#upgrade-modal-design)
8. [Visual Design System](#visual-design-system)
9. [Interaction Patterns](#interaction-patterns)
10. [Accessibility](#accessibility)
11. [Technical Implementation](#technical-implementation)
12. [Future Roadmap](#future-roadmap)

---

## Overview

The Cortex Settings page is a full-page, two-column settings interface inspired by the design patterns of [OpenAI ChatGPT Desktop](https://chatgpt.com/features/desktop/), [Cursor IDE](https://cursor.com/), [Claude Desktop](https://claude.ai/), and [VS Code](https://code.visualstudio.com/). It replaces the previous standalone Memory Manager page with a unified settings experience.

### Key Design Goals

| Goal | Description |
|------|-------------|
| **Industry parity** | Match the quality and layout of OpenAI/Cursor/Claude settings |
| **Discoverability** | Every feature accessible within 2 clicks from the settings nav |
| **Consistency** | Unified dark theme, spacing, and interaction patterns |
| **Upgrade path** | Prominent but non-intrusive upgrade flow with clear plan comparison |
| **Memory integration** | Memory Manager preserved as a first-class section within settings |

---

## Design Philosophy

### Three Pillars

1. **Clarity over cleverness** — Standard patterns users already know from VS Code, ChatGPT, Cursor
2. **Progressive disclosure** — Simple defaults, advanced options available but not overwhelming
3. **Dark-first** — Built for developers who spend 8+ hours staring at screens

### Design Principles

- **Left nav + right content** — The universal settings pattern (used by OpenAI, Google, GitHub, Slack, Discord)
- **Grouped sections** — Related settings clustered under labeled groups (Personal, AI & Agent, Integrations, Advanced)
- **Immediate feedback** — Every toggle, slider, and select saves instantly via QWebChannel bridge
- **Non-destructive** — Destructive actions (Clear All, Delete Account) require confirmation

---

## Industry Research & Benchmarks

### Reference Implementations

| App | Settings Pattern | Upgrade Flow | Key Takeaway |
|-----|-----------------|--------------|--------------|
| **OpenAI ChatGPT Desktop** | Left sidebar nav with grouped categories (Personal, Integrations, Coding, Archived). Full-page content area with search. | "Upgrade plan" button in Usage & Billing section. Plan card shows Free/$0 with upgrade CTA. Progress bar for usage limits. | Clean separation between nav and content. Usage limits shown with progress bars and reset dates. |
| **Cursor IDE** | VS Code-style settings (JSON + GUI). Settings accessible via gear icon or `Ctrl+,`. | Pro plan ($20/mo) with unlimited completions. Upgrade prompt when hitting limits. | Inline settings with search. Privacy mode toggle prominent. |
| **Claude Desktop** | Minimal settings panel. MCP server config as JSON. Settings accessible from menubar. | Pro plan ($20/mo) with higher limits. Usage shown in sidebar. | Simplicity wins — fewer settings, smarter defaults. |
| **VS Code** | Two-tier: GUI settings + JSON settings. Search-first approach. | Free with extensions marketplace. | Search is the primary navigation method for power users. |
| **Slack** | Left sidebar nav with sections. Full-page content. | Free → Pro → Business+ → Enterprise Grid. | Plan badges and usage meters clearly visible. |

### Common Patterns Across All

1. **Left sidebar navigation** with grouped sections
2. **Search** at the top of the nav
3. **Back button** to return to the main app
4. **Upgrade banner** in the nav sidebar (persistent, non-modal)
5. **Usage meters** with progress bars and reset dates
6. **Plan badges** (Free/Pro/Enterprise) on profile cards
7. **Toggle switches** for boolean settings (not checkboxes)
8. **Immediate save** (no "Save" button — changes apply instantly)

---

## Architecture

### File Map

```
src/ui/html/memory_manager/
├── memory_management.html    ← Full settings page (HTML structure)
├── memory_management.css     ← Complete styling (dark theme, all sections)
└── memory_management.js      ← Navigation, modals, QWebChannel bridge
```

### Integration Flow

```
sidebar.html (gear icon click)
  → callBridge('onSettingsRequested')
    → sidebar_bridge.py → main_window.py
      → QWebEngineView loads memory_management.html
        → QWebChannel bridge connects
          → Settings page renders with Python-side state
```

### Data Flow

```
User changes setting (toggle/select/input)
  → JS event listener fires
    → bridge.setSetting(key, value)
      → Python side persists to settings.json
        → Returns confirmation
          → UI shows toast "Saved"

User clicks "Back to app"
  → bridge.onSettingsClosed()
    → main_window.py closes settings panel
      → Returns to previous view (editor/chat)
```

---

## Navigation Structure

### Sidebar Groups

```
┌─────────────────────────┐
│ ← Back to app           │
│ 🔍 Search settings...   │
├─────────────────────────┤
│ Personal                │
│   ● General             │
│   ○ Profile             │
│   ○ Appearance          │
│   ○ Keyboard Shortcuts  │
│   ○ Usage & Billing     │
├─────────────────────────┤
│ AI & Agent              │
│   ○ Models & Providers  │
│   ○ Memory              │
│   ○ Personalization     │
│   ○ Safety & Permissions│
├─────────────────────────┤
│ Integrations            │
│   ○ MCP Servers         │
│   ○ Git                 │
│   ○ Extensions          │
├─────────────────────────┤
│ Advanced                │
│   ○ Terminal            │
│   ○ Performance         │
│   ○ About Cortex        │
├─────────────────────────┤
│ ⚡ Upgrade to Pro       │
│   Unlock unlimited AI   │
│   [Upgrade]             │
└─────────────────────────┘
```

### Group Rationale

| Group | Contents | Why grouped together |
|-------|----------|---------------------|
| **Personal** | General, Profile, Appearance, Shortcuts, Usage | User's own preferences and account |
| **AI & Agent** | Models, Memory, Personalization, Safety | Everything related to AI behavior |
| **Integrations** | MCP, Git, Extensions | External tool connections |
| **Advanced** | Terminal, Performance, About | Power user and system settings |

---

## Section Details

### 1. General
- Restore previous session (toggle)
- Check for updates (toggle)
- Default project directory (path picker)
- Language selector (8 languages)
- Desktop notifications (toggle)
- Sound alerts (toggle)
- Telemetry opt-in (toggle)

### 2. Profile
- Avatar + display name + email
- Plan badge (Free/Pro/Ultra)
- Edit profile button
- Account details (name, email)
- Danger zone (delete account)

### 3. Appearance
- Theme picker (Dark / Light / System) with visual previews
- Editor font size (range slider)
- Editor font family (dropdown)
- Tab size (2/4/8)
- Word wrap (toggle)
- Minimap (toggle)
- UI scale (range slider 80-150%)
- Sidebar position (left/right)

### 4. Keyboard Shortcuts
- Read-only display of all shortcuts
- Grouped by category (General, AI, Editor)
- Reset to defaults button
- Future: inline editing of keybindings

### 5. Usage & Billing
- Current plan card with upgrade button
- Monthly AI requests bar (47/100)
- Tokens used bar (1.2M/5M)
- Agent tool calls bar (312/500) with warning color
- Per-model usage breakdown (GPT-4o, Claude, DeepSeek, Gemini)
- Billing history (empty state for free users)
- Reset dates shown per meter

### 6. Models & Providers
- Default model selector (8 models + Auto)
- Provider API key management
  - OpenAI, Anthropic, DeepSeek, Google, Mistral, Ollama
  - Password fields with visibility toggle
  - Encrypted local storage
  - Ollama URL with test button

### 7. Memory (formerly Memory Manager)
- Automatic memory generation toggle
- Scope switcher (Project / Global / Shared)
- Memory search with semantic search button
- Type filters
- Memory card list with delete action
- Consolidation, stats, refresh, clear actions

### 8. Personalization
- Custom system instructions (textarea)
- Verbosity selector (Concise/Balanced/Detailed)
- Code style preference
- Remember conversations toggle
- Context window size selector

### 9. Safety & Permissions
- File creation permission (toggle)
- File deletion permission (toggle)
- Terminal command permission (toggle)
- Require approval for destructive actions (toggle)
- Privacy mode (toggle)
- Local-only mode (toggle)

### 10. MCP Servers
- Server list (empty state with add button)
- Learn about MCP link

### 11. Git
- Auto-commit AI changes (toggle)
- Commit message prefix (text input)
- Default branch name (text input)

### 12. Extensions
- Extension list (empty state with browse button)

### 13. Terminal
- Default shell selector (PowerShell/CMD/Git Bash/WSL)
- Shell arguments (text input)
- Font size (range slider)
- Scrollback lines (dropdown)
- Cursor style (Block/Underline/Bar)
- Copy on select (toggle)

### 14. Performance
- GPU acceleration (toggle)
- Limit background processes (toggle)
- File watcher debounce (range slider)
- Request timeout (range slider)
- Proxy configuration (text input)

### 15. About Cortex
- Logo + version number
- Links (Website, Docs, GitHub, Changelog, License)
- Copyright notice

---

## Upgrade Modal Design

### Trigger Points

1. **Nav sidebar** — Persistent "Upgrade to Pro" banner at bottom
2. **Usage & Billing** — "Upgrade Plan" button on plan card
3. **Usage limit warnings** — When approaching 80%+ of any limit

### Modal Layout

```
┌──────────────────────────────────────────────────────┐
│                                              [X]     │
│                    ⚡                                │
│          Upgrade to Cortex Pro                       │
│    Unlock the full power of AI-driven development    │
│                                                      │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐   │
│  │   FREE      │ │   PRO       │ │   ULTRA     │   │
│  │   $0/mo     │ │   $19/mo    │ │   $49/mo    │   │
│  │             │ │ ★ Popular   │ │             │   │
│  │ ✓ 100 reqs  │ │ ✓ Unlimited │ │ ✓ All Pro   │   │
│  │ ✓ 5M tokens │ │ ✓ 50M tokens│ │ ✓ Unlimited │   │
│  │ ✓ 2 models  │ │ ✓ All models│ │ ✓ Priority  │   │
│  │ ✗ Files     │ │ ✓ Files     │ │ ✓ 1M context│   │
│  │ ✗ Terminal  │ │ ✓ Terminal  │ │ ✓ Team collab│  │
│  │ ✗ MCP       │ │ ✓ MCP       │ │ ✓ Fine-tune │   │
│  │             │ │             │ │ ✓ Support   │   │
│  │ [Current]   │ │ [Upgrade]   │ │ [Upgrade]   │   │
│  └─────────────┘ └─────────────┘ └─────────────┘   │
│                                                      │
│  All plans include encryption, privacy, local model  │
│  fallback. Cancel anytime.                           │
└──────────────────────────────────────────────────────┘
```

### Pricing Strategy

| Plan | Price | Target User | Key Differentiator |
|------|-------|------------|-------------------|
| **Free** | $0/mo | Casual users, evaluation | Limited requests, 2 models, no agent tools |
| **Pro** | $19/mo | Individual developers | Unlimited requests, all models, full agent |
| **Ultra** | $49/mo | Power users, teams | Unlimited tokens, 1M context, team features |

### Conversion Tactics (industry-standard)

1. **"Most Popular" badge** on Pro plan (social proof)
2. **Featured plan highlighted** with accent border and glow
3. **Feature comparison** with ✓/— visual indicators
4. **Persistent nav banner** — always visible, never blocking
5. **Usage warnings** at 60%+ with inline upgrade suggestion
6. **One-click upgrade** — pre-filled checkout, no friction

---

## Visual Design System

### Color Palette

| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | `#0e1116` | Page background |
| `--surface` | `#141923` | Card backgrounds |
| `--surface-2` | `#1a2130` | Input backgrounds, hover states |
| `--surface-3` | `#202a3d` | Active states, badges |
| `--border` | `#293247` | Default borders |
| `--border-strong` | `#3d4a67` | Hover borders |
| `--text` | `#edf2ff` | Primary text |
| `--muted` | `#9ba6bf` | Secondary text |
| `--accent` | `#4da3ff` | Primary accent (blue) |
| `--accent-2` | `#86f7c2` | Secondary accent (green) |
| `--danger` | `#ff7f8f` | Destructive actions |
| `--success` | `#4caf50` | Success states |
| `--warning` | `#ff9800` | Warning states |

### Typography

| Element | Font | Size | Weight |
|---------|------|------|--------|
| Page title | Segoe UI | 28px | 600 |
| Section title | Segoe UI | 20px | 600 |
| Card title | Segoe UI | 15px | 600 |
| Body text | Segoe UI | 14px | 400 |
| Label | Segoe UI | 14px | 500 |
| Caption | Segoe UI | 12px | 400 |
| Code/Mono | Cascadia Code | 13px | 400 |

### Spacing Scale

| Token | Value | Usage |
|-------|-------|-------|
| `--radius` | 18px | Cards, modals |
| `--radius-sm` | 12px | Inputs, small cards |
| `--radius-xs` | 8px | Buttons, badges |

### Shadows

| Element | Shadow |
|---------|--------|
| Cards | `0 24px 60px rgba(0,0,0,0.34)` |
| Modals | Backdrop blur 8px + dark overlay |
| Hover | Border color change (no shadow) |

---

## Interaction Patterns

### Immediate Save (no "Save" button)

Every setting change is persisted immediately:

```
Toggle flipped → change event → bridge.setSetting(key, value) → Python saves → toast "Saved"
```

This matches OpenAI ChatGPT, Cursor, and Slack's settings behavior.

### Toast Notifications

- Position: bottom-right
- Duration: 2.5 seconds
- Animation: fade in + slide up
- Auto-dismiss with opacity fade

### Keyboard Support

| Key | Action |
|-----|--------|
| `Escape` | Close any open modal |
| `Ctrl+,` | Open settings (from main app) |
| `/` or `Ctrl+K` | Focus search (future) |

### Modal Behavior

- Click overlay to close
- Escape to close
- No body scroll when modal open
- Slide-up animation on open

---

## Accessibility

### Implemented

- All interactive elements are keyboard-focusable
- Toggle switches have proper ARIA states
- Color contrast meets WCAG AA (text on dark backgrounds)
- Semantic HTML structure (nav, main, section, label)
- SVG icons have implicit labeling via parent button titles

### Planned

- ARIA landmarks for navigation regions
- Focus trap in modals
- High-contrast mode option
- Screen reader announcements for state changes

---

## Technical Implementation

### Bridge Methods (JS → Python)

| Method | Purpose | Args |
|--------|---------|------|
| `setSetting(key, value)` | Persist a setting | key: string, value: any |
| `getSettings(callback)` | Load all settings | callback receives settings object |
| `getState(callback)` | Load memory state | callback receives memory state |
| `onSettingsClosed()` | Notify settings closed | none |
| `upgradePlan(plan)` | Initiate upgrade flow | "pro" or "ultra" |
| `deleteMemory(scope, id)` | Delete a memory | scope: string, id: string |
| `setEnabled(bool)` | Toggle memory on/off | boolean |
| `switchScope(scope)` | Switch memory scope | "project"/"global"/"shared" |

### State Management

Settings are stored in two layers:

1. **Python-side** — `settings.json` file (persistent across restarts)
2. **JS-side** — In-memory state object (for UI rendering)

On settings page load, Python sends current settings via `getSettings()` callback. Every UI change immediately persists to Python via `setSetting()`.

### Standalone Mode

When loaded outside QWebChannel (e.g., in a browser for preview), the page renders with demo data and all interactions are local-only. This enables design iteration without running the full IDE.

---

## Future Roadmap

### Phase 2 (Next Release)
- [ ] Settings search across all section content
- [ ] Inline keyboard shortcut editing
- [ ] Theme customizer (custom accent colors)
- [ ] Export/import settings as JSON

### Phase 3 (Future)
- [ ] Settings sync across devices (cloud)
- [ ] Plugin settings API (extensions register their own settings sections)
- [ ] Settings versioning (undo/redo for settings changes)
- [ ] Settings profiles (work/personal switching)

### Phase 4 (Long-term)
- [ ] AI-powered settings recommendations
- [ ] Usage analytics dashboard
- [ ] Team settings management (admin console)
- [ ] White-label settings for enterprise

---

## Sources & References

- [OpenAI ChatGPT Desktop](https://chatgpt.com/features/desktop/) — Settings layout, usage meters, upgrade flow
- [Cursor IDE](https://cursor.com/) — Settings search, privacy mode, plan structure
- [Claude Desktop](https://claude.ai/) — Minimal settings, MCP config
- [VS Code Settings](https://code.visualstudio.com/docs/getstarted/userinterface) — Search-first settings, two-tier config
- [Material Design 3](https://m3.material.io/) — Dark theme guidelines, component patterns
- [Apple HIG](https://developer.apple.com/design/human-interface-guidelines) — Settings page best practices
- [NN/g Menu Design](https://www.nngroup.com/articles/menu-design/) — Navigation UX guidelines
- [Dark UI Best Practices](https://uxdesign.cc/dark-ui-design-principles-and-best-practices-9b9061b86e1) — Contrast, elevation, color usage
- [SaaS UI Patterns](https://www.saasui.design/) — Settings, pricing, modal patterns
- [Dribbble Dark Mode](https://dribbble.com/search/dark-saas-dashboard) — Visual inspiration
