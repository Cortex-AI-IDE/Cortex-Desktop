# Cortex IDE — Anthropic Claude Light Mode Design Tokens

> **2026-07-08 IMPLEMENTATION STATUS (evening sweep):** Light mode is now wired
> END-TO-END — chat panel design tokens (`src/ui/tokens.py` `LIGHT` dict + live
> `TOKENS` proxy), Monaco editor (`cortex-light` + `body.light-theme` in
> `src/assets/editor.html`), file-explorer sidebar (`sidebar.html`
> `[data-theme="light"]`), menu bar / status bar / toolbar icons / DWM title bar
> (`main_window._apply_chrome_theme`), and the base `QPalette` in `main.py`
> (was hardcoded dark). **THE RULE everywhere: light backgrounds get DARK
> (near-black) fonts — never light-hash grays.** Full bug list and architecture:
> see `LIGHT_MODE_IMPLEMENTATION.md` → "Changelog". Guarded by GROUP J tests in
> `tests/test_release_suite.py`.
>
> **2026-07-08 NOTE:** The active light mode QSS (`src/ui/themes/light.qss`) now uses a
> **VS Code/Excel-inspired cool-gray palette with green accent** (`#4CAF50`). This
> Anthropic warm-beige / terracotta palette is preserved here as an **alternative design
> reference** — the tokens and system architecture (dark-on-light zones, grid, typography)
> remain valuable for future theming work.
>
> **Source:** Extracted from Anthropic's official UI design system (`anthropic-ui-skills`).
> Anthropic's light mode is **warm beige-based**, NOT white. The primary surface is `#ECE9E0`.
> These tokens complement your existing dark mode — they never overwrite it.

---

## How to Use

Apply these via a `.light-mode` class or a `[data-theme="light"]` attribute on your root element.
Your existing dark mode styles remain untouched under their own selector (`.dark-mode` / `[data-theme="dark"]`).

```css
/* Dark mode — your existing tokens remain here, unchanged */
[data-theme="dark"] { /* ... your dark tokens ... */ }

/* Light mode — additive, never overwrites dark */
[data-theme="light"] {
  /* paste the tokens below */
}
```

---

## Color Palette

### Base Backgrounds (Light → Warm)

| Token | Hex | Usage |
|---|---|---|
| `--bg-primary` | `#ECE9E0` | Main editor / app background |
| `--bg-secondary` | `#E4E1D8` | Sidebars, panels, inactive tabs |
| `--bg-tertiary` | `#DDDAD0` | Hover states, nested panels |
| `--bg-elevated` | `#F4F1EA` | Modals, popovers, dropdowns |
| `--bg-sunken` | `#D8D5CC` | Input fields, inset areas |

### Surface & Card Backgrounds

| Token | Hex | Usage |
|---|---|---|
| `--surface-default` | `#EDEAE1` | Cards, editor panes |
| `--surface-raised` | `#F2EFE8` | Floating panels, tooltips |
| `--surface-overlay` | `rgba(236,233,224,0.92)` | Transparent overlays / backdrops |

---

## Text Colors (Dark text on light backgrounds)

| Token | Hex | Usage |
|---|---|---|
| `--text-primary` | `#1A1814` | Body text, main labels |
| `--text-secondary` | `#3D3A33` | Subtitles, descriptions |
| `--text-tertiary` | `#6B6860` | Placeholders, hints, metadata |
| `--text-disabled` | `#A8A59E` | Disabled / muted text |
| `--text-link` | `#8B5E3C` | Hyperlinks (warm terracotta) |
| `--text-link-hover` | `#6B4429` | Link hover state |

---

## Dark UI Elements in Light Mode (auto light text)

Some UI elements — like terminal panels, code blocks, active tab bars, and badge chips — keep a **dark background even in light mode**. Text automatically swaps to a light color for those.

| Element | Background Token | Background Hex | Text Token | Text Hex |
|---|---|---|---|---|
| Terminal / Console | `--bg-terminal` | `#1C1B18` | `--text-on-dark` | `#F0EDE6` |
| Code blocks | `--bg-code` | `#28261F` | `--text-on-dark` | `#F0EDE6` |
| Active tab / focused item | `--bg-active` | `#2E2C25` | `--text-on-active` | `#ECE9E0` |
| Notification badges | `--bg-badge` | `#3D3A33` | `--text-on-badge` | `#ECE9E0` |
| Sidebar header | `--bg-sidebar-header` | `#242219` | `--text-on-sidebar-header` | `#D8D5CC` |
| Tooltip (dark style) | `--bg-tooltip` | `#1A1814` | `--text-on-tooltip` | `#ECE9E0` |
| Status bar | `--bg-statusbar` | `#1C1B18` | `--text-on-statusbar` | `#A8A59E` |

> **Rule:** Any element whose background luminance falls below ~30% automatically receives `--text-on-dark` (`#F0EDE6`) instead of `--text-primary`. In CSS, implement this using explicit class pairing or the `color-mix` / `color-contrast()` utility.

---

## Accent & Brand Colors

Anthropic uses a **warm terracotta accent** over neutral backgrounds. These remain consistent across light and dark modes with minor luminance adjustments.

| Token | Hex | Usage |
|---|---|---|
| `--accent-primary` | `#C96A3E` | Primary buttons, active indicators, cursor |
| `--accent-primary-hover` | `#A8522E` | Button hover |
| `--accent-primary-subtle` | `#F2DDD1` | Accent tints, highlighted selections |
| `--accent-secondary` | `#7A6B58` | Secondary actions, toggle tracks |
| `--accent-secondary-subtle` | `#E8E3DA` | Secondary tint backgrounds |

---

## Border & Divider Colors

| Token | Hex | Usage |
|---|---|---|
| `--border-default` | `#CCC9C0` | Default element borders |
| `--border-subtle` | `#D8D5CC` | Lightweight separators |
| `--border-strong` | `#A8A59E` | Emphasized borders, focus rings |
| `--border-focus` | `#C96A3E` | Keyboard focus ring (matches accent) |

---

## Spacing & Grid

Anthropic's system uses a **4px base grid** throughout.

| Token | Value | Usage |
|---|---|---|
| `--space-1` | `4px` | Micro padding |
| `--space-2` | `8px` | Tight padding |
| `--space-3` | `12px` | Standard padding |
| `--space-4` | `16px` | Component gap |
| `--space-6` | `24px` | Section gap |
| `--space-8` | `32px` | Large sections |

---

## Border Radius

| Token | Value | Usage |
|---|---|---|
| `--radius-sm` | `3px` | Inputs, small tags |
| `--radius-md` | `6px` | Buttons, cards (standard) |
| `--radius-lg` | `12px` | Modals, large panels |
| `--radius-full` | `9999px` | Badges, pills, toggles |

> **Rule:** Never animate border-radius unless explicitly required. Anthropic's system avoids motion unless purposeful.

---

## Typography

> Anthropic's official system uses **Inter exclusively**. For Cortex IDE, Inter is appropriate for UI chrome — pair with a monospace font for code.

| Token | Value | Usage |
|---|---|---|
| `--font-ui` | `'Inter', sans-serif` | All UI labels, menus, inputs |
| `--font-code` | `'JetBrains Mono', 'Fira Code', monospace` | Editor, terminal |
| `--font-size-xs` | `11px` | Status bar, badges |
| `--font-size-sm` | `12px` | Sidebar labels, metadata |
| `--font-size-base` | `13px` | Body / editor UI text |
| `--font-size-md` | `14px` | Panel headings, tabs |
| `--font-size-lg` | `16px` | Modal titles |
| `--font-size-xl` | `20px` | Page-level headings |

---

## Shadows (Light Mode)

| Token | Value | Usage |
|---|---|---|
| `--shadow-sm` | `0 1px 2px rgba(26,24,20,0.08)` | Cards, inputs |
| `--shadow-md` | `0 2px 8px rgba(26,24,20,0.12)` | Dropdowns, popovers |
| `--shadow-lg` | `0 4px 20px rgba(26,24,20,0.16)` | Modals, floating panels |

---

## Semantic State Colors

| Token | Hex | Usage |
|---|---|---|
| `--color-success` | `#3B7A4A` | Success text, icons |
| `--color-success-bg` | `#D6EDD9` | Success backgrounds |
| `--color-warning` | `#8A5C00` | Warning text |
| `--color-warning-bg` | `#FFF0C2` | Warning backgrounds |
| `--color-error` | `#B83232` | Error text, destructive actions |
| `--color-error-bg` | `#FDDDD9` | Error backgrounds |
| `--color-info` | `#1F5FA6` | Info text, links |
| `--color-info-bg` | `#D6E8F7` | Info backgrounds |

---

## Complete CSS Block (Ready to paste)

```css
[data-theme="light"] {

  /* === BACKGROUNDS === */
  --bg-primary:             #ECE9E0;
  --bg-secondary:           #E4E1D8;
  --bg-tertiary:            #DDDAD0;
  --bg-elevated:            #F4F1EA;
  --bg-sunken:              #D8D5CC;

  /* === SURFACES === */
  --surface-default:        #EDEAE1;
  --surface-raised:         #F2EFE8;
  --surface-overlay:        rgba(236, 233, 224, 0.92);

  /* === TEXT (dark text on light bg) === */
  --text-primary:           #1A1814;
  --text-secondary:         #3D3A33;
  --text-tertiary:          #6B6860;
  --text-disabled:          #A8A59E;
  --text-link:              #8B5E3C;
  --text-link-hover:        #6B4429;

  /* === TEXT ON DARK ELEMENTS (light text for dark bg zones) === */
  --text-on-dark:           #F0EDE6;
  --text-on-active:         #ECE9E0;
  --text-on-badge:          #ECE9E0;
  --text-on-sidebar-header: #D8D5CC;
  --text-on-tooltip:        #ECE9E0;
  --text-on-statusbar:      #A8A59E;

  /* === DARK BACKGROUNDS (stay dark in light mode) === */
  --bg-terminal:            #1C1B18;
  --bg-code:                #28261F;
  --bg-active:              #2E2C25;
  --bg-badge:               #3D3A33;
  --bg-sidebar-header:      #242219;
  --bg-tooltip:             #1A1814;
  --bg-statusbar:           #1C1B18;

  /* === ACCENT === */
  --accent-primary:         #C96A3E;
  --accent-primary-hover:   #A8522E;
  --accent-primary-subtle:  #F2DDD1;
  --accent-secondary:       #7A6B58;
  --accent-secondary-subtle:#E8E3DA;

  /* === BORDERS === */
  --border-default:         #CCC9C0;
  --border-subtle:          #D8D5CC;
  --border-strong:          #A8A59E;
  --border-focus:           #C96A3E;

  /* === SPACING (4px grid) === */
  --space-1:  4px;
  --space-2:  8px;
  --space-3:  12px;
  --space-4:  16px;
  --space-6:  24px;
  --space-8:  32px;

  /* === BORDER RADIUS === */
  --radius-sm:   3px;
  --radius-md:   6px;
  --radius-lg:   12px;
  --radius-full: 9999px;

  /* === TYPOGRAPHY === */
  --font-ui:       'Inter', sans-serif;
  --font-code:     'JetBrains Mono', 'Fira Code', monospace;
  --font-size-xs:  11px;
  --font-size-sm:  12px;
  --font-size-base:13px;
  --font-size-md:  14px;
  --font-size-lg:  16px;
  --font-size-xl:  20px;

  /* === SHADOWS === */
  --shadow-sm: 0 1px 2px rgba(26, 24, 20, 0.08);
  --shadow-md: 0 2px 8px rgba(26, 24, 20, 0.12);
  --shadow-lg: 0 4px 20px rgba(26, 24, 20, 0.16);

  /* === SEMANTIC STATES === */
  --color-success:    #3B7A4A;
  --color-success-bg: #D6EDD9;
  --color-warning:    #8A5C00;
  --color-warning-bg: #FFF0C2;
  --color-error:      #B83232;
  --color-error-bg:   #FDDDD9;
  --color-info:       #1F5FA6;
  --color-info-bg:    #D6E8F7;
}
```

---

## Implementation Pattern for Dark-on-Light + Light-on-Dark Zones

```css
/* Standard light mode element — dark text on warm beige */
.panel {
  background: var(--bg-primary);
  color: var(--text-primary);
}

/* Terminal stays dark even in light mode — text auto-flips to light */
.terminal-panel {
  background: var(--bg-terminal);
  color: var(--text-on-dark);
}

/* Code blocks stay dark — text auto-flips */
.code-block {
  background: var(--bg-code);
  color: var(--text-on-dark);
  font-family: var(--font-code);
}

/* Active tab — dark even in light mode */
.tab.active {
  background: var(--bg-active);
  color: var(--text-on-active);
}

/* Tooltip — always dark */
.tooltip {
  background: var(--bg-tooltip);
  color: var(--text-on-tooltip);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-md);
}
```

---

## Notes

- **Never hardcode hex values** — always reference tokens so theme swaps work cleanly.
- **4px grid is strict** — all spacing must be multiples of 4px.
- **No motion without purpose** — avoid animating purely decorative elements.
- **Focus rings** always use `--border-focus` (`#C96A3E`) for accessibility.
- **These tokens are additive** — your dark mode tokens under `[data-theme="dark"]` are completely unaffected.
