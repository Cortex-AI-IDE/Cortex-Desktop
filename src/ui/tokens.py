"""
tokens.py — Cortex IDE Design Tokens
=====================================

Single source of truth for all chat UI theming.
Based on OpenCode OC-2 dark theme design tokens.

Every color in the UI flows from here. No hardcoded hex values outside this file.
"""

# ── Dark theme tokens (default) — OpenCode OC-2 exact ──
DARK = {
    # Backgrounds — matched to editor.html color scheme
    "bg":                "#1e1e1e",     # editor #app background
    "bg_card":           "#252526",     # editor tab-bar / elevated panel bg
    "bg_secondary":      "#2d2d2d",     # editor inactive tab bg
    "bg_tertiary":       "#18181c",     # editor file-path-bar bg
    "bg_hover":          "#2a2a2a",     # editor tab hover
    "bg_input":          "#161616",     # chat input — UNCHANGED
    "bg_elevated":       "#252526",     # elevated panels
    "bg_raised":         "#252526",     # raised panels

    # Borders — matched to editor.html
    "border":            "#3c3c3c",     # editor tab-bar border-bottom
    "border_dim":        "#2a2a2e",     # editor file-path-bar border-bottom
    "border_color":      "#3c3c3c",     # consistent with editor borders
    "border_active":     "#3b82f6",

    # Text — demo: text .85, text_secondary .55, text_tertiary .35
    "text":              "rgba(255,255,255,0.85)",
    "text_dim":          "rgba(255,255,255,0.55)",
    "text_secondary":    "rgba(255,255,255,0.55)",
    "text_primary":      "rgba(255,255,255,0.85)",
    "muted":             "rgba(255,255,255,0.35)",
    "mono_muted":        "#8b949e",
    "mono_bright":       "#e6edf3",

    # Accent
    "accent":            "#06b6d4",
    "accent_primary":    "#7c3aed",
    "accent_secondary":  "#6c5ce7",

    # Semantic
    "think":             "#7c6ce7",
    "think_label":       "#a89df0",
    "user_bubble":       "#212529",
    "green":             "#3fb950",
    "red":               "#f85149",
    "orange":            "#ff8c00",
    "warning":           "#e5c07b",
    "blue":              "#06b6d4",
    "info":              "#9d7cd8",
    "streaming_cursor":  "#06b6d4",

    # Tool card specific
    "tool_header_bg":    "rgba(255,255,255,0.03)",
    "tool_body_bg":      "rgba(255,255,255,0.01)",
    "diff_add_bg":       "rgba(63,185,80,0.12)",
    "diff_del_bg":       "rgba(248,81,73,0.12)",
    "diff_hunk_bg":      "rgba(110,118,129,0.06)",
    "diff_add_line":     "rgba(63,185,80,0.22)",
    "diff_del_line":     "rgba(248,81,73,0.22)",

    # Syntax — OC-2 exact values from TUI_DISPLAY_DESIGN.md
    "syntax_string":     "#00ceb9",    # teal — strings
    "syntax_property":   "#ff9ae2",    # pink — properties
    "syntax_keyword":    "#c678dd",    # purple — keywords (was muted, now proper purple)
    "syntax_variable":   "rgba(255,255,255,0.936)",  # strong — variables
    "syntax_function":   "#61afef",    # blue — functions/methods
    "syntax_number":     "#ffba92",    # peach — numbers/booleans
    "syntax_type":       "#ecf58c",    # yellow-green — types/classes
    "syntax_builtin":    "#f85149",    # RED — builtins (OpenCode: builtins are red!)
    "syntax_comment":    "rgba(255,255,255,0.422)",  # muted italic — comments
    "syntax_operator":   "#abb2bf",    # light-gray — operators/punctuation
    "syntax_constant":   "#d19a66",    # orange — constants
    "syntax_decorator":  "#ecf58c",    # yellow-green — decorators
    "syntax_tag":        "#e06c75",    # coral — HTML tags
    "syntax_attribute":  "#d19a66",    # orange — HTML attributes
    "syntax_namespace":  "#ecf58c",    # yellow-green — namespaces

    # Markdown colors — matches chat_panel_design_demo.html
    "md_heading":        "#f0f6fc",    # white — all heading levels (demo)
    "md_text":           "rgba(255,255,255,0.85)",  # white — body text (demo)
    "md_link":           "#58a6ff",    # blue — link URLs (demo)
    "md_link_text":      "#58a6ff",    # blue — link label text
    "md_code":           "#7fd88f",    # green — inline code (demo)
    "md_code_bg":        "rgba(110,118,129,0.15)",  # inline code background (demo)
    "md_blockquote":     "rgba(255,255,255,0.55)",  # secondary — blockquote text (demo)
    "md_blockquote_border": "#3b82f6", # blue — blockquote border (demo)
    "md_strong":         "#f5a742",    # orange — bold/strong (demo)
    "md_emph":           "#e5c07b",    # gold — italic/emphasis (OC-2 exact)
    "md_hr":             "#282828",    # subtle — horizontal rule
    "md_list_marker":    "#61afef",    # blue — bullet/numbers
    "md_table_header_bg": "rgba(255,255,255,0.04)",  # th background
    "md_table_border":   "#282828",    # subtle — table borders
    "md_strikethrough":  "rgba(255,255,255,0.4)",    # strikethrough
    "md_mark_bg":        "rgba(229,192,123,0.25)",   # mark highlight bg
    "md_filename":       "#3fb950",    # green — file names/paths

    # Syntax highlight palette (used by tool_cards.py)
    "tool_read":      "#58a6ff",   # blue   — reading / inspecting
    "tool_edit":      "#3fb950",   # green  — mutating files
    "tool_write":     "#3fb950",   # green  — creating files
    "tool_search":    "#bc8cff",   # purple — grep / glob / semantic search
    "tool_terminal":  "#f0883e",   # orange — shell / bash / powershell
    "tool_web":       "#56d4dd",   # cyan   — web_search / web_fetch
    "tool_task":      "#d2a8ff",   # lilac  — task / planning
    "tool_team":      "#ff7b72",   # coral  — multi-agent / delegation
    "tool_thought":   "#7c6ce7",   # violet — thinking
    "tool_generic":   "#8b949e",   # gray   — fallback

    # Status colors
    "status_running": "#56d4dd",   # spinner default tint
    "status_ok":      "#3fb950",
    "status_error":   "#f85149",

    # Fonts
    "font_ui":           "Geist, 'Segoe UI', system-ui, -apple-system, sans-serif",
    "font_mono":         "'JetBrains Mono','Fira Code',Consolas,'Courier New',monospace",
    "font_size":         "14px",
    "font_size_sm":      "13px",
    "font_size_xs":      "12px",
    "font_size_xxs":     "11px",

    # Line heights
    "line_height":       "1.45",
    "line_height_code":  "1.4",

    # Spacing & Radius
    "radius_xs":         "4px",
    "radius_sm":         "6px",
    "radius_md":         "8px",
    "radius_lg":         "10px",
    "radius_xl":         "14px",

    # Input / Menu / Button tokens (used by InputArea, context menus, etc.)
    "input_border":      "#262626",
    "input_hover":       "#2A2A2A",
    "separator":         "#3c3c3c",     # match editor borders
    "btn_bg":            "#242424",
    "btn_hover":         "#2D2D2D",
    "btn_text":          "#aaaaaa",
    "btn_text_hover":    "#ffffff",
    "menu_bg":           "#1F1F1F",
    "menu_text":         "#cccccc",
    "menu_selected":     "#2A2A2A",
    "divider":           "#3c3c3c",     # match editor borders
    "white":             "#ffffff",
    "card_border_subtle": "rgba(255,255,255,0.08)",
    "card_bg_subtle":    "rgba(255,255,255,0.015)",
    "stop_btn":          "#f85149",
    "stop_btn_hover_bg": "rgba(248,81,73,0.15)",
    "spell_error":       "#ff4040",
    "spell_input_bg":    "#1a1a1a",
    "context_menu_bg":   "#252526",
    "context_menu_border": "#3c3c3c",
    "context_menu_sel":  "#094771",
    "ring_purple":       "#7c3aed",
    "ring_purple_light": "#8b5cf6",
    "ring_cyan":         "#06b6d4",
    "ring_cyan_light":   "#22d3ee",
    "edited_row_bg":     "#18181c",     # match bg_tertiary
    "edited_row_hover":  "#2a2a2a",     # match bg_hover
    "edited_row_text":   "#e3e4e6",
    "edited_row_badge":  "#ffb300",

    # Code block decoration
    "code_header_bg":    "#2d2d2d",     # match bg_secondary (editor inactive tab)
    "code_header_border": "#3c3c3c",    # match editor borders
    "code_copy_color":   "#8b949e",
    "code_copy_hover":   "#f0f6fc",
    "code_copy_bg":      "transparent",
    "code_copy_bg_hover": "#30363d",
    "code_lang_color":   "#8b949e",
    "code_line_number":  "rgba(255,255,255,0.35)",
    "code_scrollbar":    "rgba(255,255,255,0.10)",
}

# ── Light theme tokens ──
# Built as DARK-merge so key parity is guaranteed: any token not overridden
# here inherits the dark value (correct for fonts/radii/line-heights, and a
# safe fallback for any future key someone adds to DARK only).
# Palette: warm Anthropic/Claude scheme — MATCHES editor.html, sidebar.html,
# the status bar, and memory_management.css [data-theme="light"] exactly
# (bg #ECE9E0, surfaces #E4E1D8/#DDDAD0, text #1A1814, muted #6B6860,
# terracotta accent #C96A3E). Every "black-based" rgba tint below uses the
# warm text RGB (26,24,20) instead of pure black so translucent overlays
# read as warm, not cool-gray. THE RULE: light backgrounds get DARK text,
# never light-hash grays. Syntax-highlighting hues (keyword/string/etc.)
# are intentionally NOT warmed — those follow language-convention colors
# for code readability, independent of chrome theming.
LIGHT = {
    **DARK,

    # Backgrounds
    "bg":                "#ECE9E0",
    "bg_card":           "#EDEAE1",
    "bg_secondary":      "#E4E1D8",
    "bg_tertiary":       "#DDDAD0",
    "bg_hover":          "#DDDAD0",
    "bg_input":          "#F4F1EA",
    "bg_elevated":       "#F4F1EA",
    "bg_raised":         "#F4F1EA",

    # Borders
    "border":            "#CCC9C0",
    "border_dim":        "#DDDAD0",
    "border_color":      "#CCC9C0",
    "border_active":     "#C96A3E",

    # Text — warm near-black on warm surfaces (user requirement: NO light-hash fonts in light mode)
    "text":              "rgba(26,24,20,0.92)",
    "text_dim":          "rgba(26,24,20,0.62)",
    "text_secondary":    "rgba(26,24,20,0.62)",
    "text_primary":      "rgba(26,24,20,0.92)",
    "muted":             "rgba(26,24,20,0.45)",
    "mono_muted":        "#6B6860",
    "mono_bright":       "#1F1E1B",

    # Accent — terracotta, matches editor.html active-tab accent
    "accent":            "#C96A3E",
    "accent_primary":    "#C96A3E",
    "accent_secondary":  "#B85A32",

    # Semantic
    "think":             "#A85D3A",
    "think_label":       "#8B4A2C",
    "user_bubble":       "#DDDAD0",
    "green":             "#3B7A4A",
    "red":               "#B83232",
    "orange":            "#C96A3E",
    "warning":           "#8A5C00",
    "blue":              "#5B7A96",
    "info":              "#6B5B8C",
    "streaming_cursor":  "#C96A3E",

    # Tool card specific
    "tool_header_bg":    "rgba(26,24,20,0.05)",
    "tool_body_bg":      "rgba(26,24,20,0.025)",
    "diff_add_bg":       "rgba(59,122,74,0.12)",
    "diff_del_bg":       "rgba(184,50,50,0.10)",
    "diff_hunk_bg":      "rgba(107,104,96,0.10)",
    "diff_add_line":     "rgba(59,122,74,0.22)",
    "diff_del_line":     "rgba(184,50,50,0.22)",

    # Syntax — GitHub Light values (language-convention colors, not chrome — left as-is)
    "syntax_string":     "#0e7569",
    "syntax_property":   "#bf3989",
    "syntax_keyword":    "#8250df",
    "syntax_variable":   "rgba(26,24,20,0.92)",
    "syntax_function":   "#0550ae",
    "syntax_number":     "#b35900",
    "syntax_type":       "#66700d",
    "syntax_builtin":    "#cf222e",
    "syntax_comment":    "rgba(26,24,20,0.48)",
    "syntax_operator":   "#6B6860",
    "syntax_constant":   "#953800",
    "syntax_decorator":  "#66700d",
    "syntax_tag":        "#cf222e",
    "syntax_attribute":  "#953800",
    "syntax_namespace":  "#66700d",

    # Markdown
    "md_heading":        "#1A1814",
    "md_text":           "rgba(26,24,20,0.92)",
    "md_link":           "#C96A3E",
    "md_link_text":      "#C96A3E",
    "md_code":           "#3B7A4A",
    "md_code_bg":        "rgba(107,104,96,0.12)",
    "md_blockquote":     "rgba(26,24,20,0.62)",
    "md_blockquote_border": "#C96A3E",
    "md_strong":         "#A8542E",
    "md_emph":           "#8A5C00",
    "md_hr":             "#DDDAD0",
    "md_list_marker":    "#C96A3E",
    "md_table_header_bg": "rgba(26,24,20,0.05)",
    "md_table_border":   "#DDDAD0",
    "md_strikethrough":  "rgba(26,24,20,0.40)",
    "md_mark_bg":        "rgba(138,92,0,0.20)",
    "md_filename":       "#3B7A4A",

    # Tool colors
    "tool_read":      "#5B7A96",
    "tool_edit":      "#3B7A4A",
    "tool_write":     "#3B7A4A",
    "tool_search":    "#8B5A8C",
    "tool_terminal":  "#C96A3E",
    "tool_web":       "#5B8C8C",
    "tool_task":      "#8B5A8C",
    "tool_team":      "#B83232",
    "tool_thought":   "#A85D3A",
    "tool_generic":   "#6B6860",

    # Status colors
    "status_running": "#5B8C8C",
    "status_ok":      "#3B7A4A",
    "status_error":   "#B83232",

    # Input / Menu / Button tokens
    "input_border":      "#CCC9C0",
    "input_hover":       "#DDDAD0",
    "separator":         "#CCC9C0",
    "btn_bg":            "#DDDAD0",
    "btn_hover":         "#D6D3CA",
    "btn_text":          "#6B6860",
    "btn_text_hover":    "#1A1814",
    "menu_bg":           "#F4F1EA",
    "menu_text":         "#1A1814",
    "menu_selected":     "#DDDAD0",
    "divider":           "#CCC9C0",
    "card_border_subtle": "rgba(26,24,20,0.10)",
    "card_bg_subtle":    "rgba(26,24,20,0.025)",
    "stop_btn":          "#B83232",
    "stop_btn_hover_bg": "rgba(184,50,50,0.12)",
    "spell_error":       "#B83232",
    "spell_input_bg":    "#F4F1EA",
    "context_menu_bg":   "#F4F1EA",
    "context_menu_border": "#CCC9C0",
    "context_menu_sel":  "rgba(201,106,62,0.18)",
    "ring_cyan":         "#5B8C8C",
    "ring_cyan_light":   "#6FA0A0",
    "edited_row_bg":     "#E4E1D8",
    "edited_row_hover":  "#DDDAD0",
    "edited_row_text":   "#1A1814",
    "edited_row_badge":  "#8A5C00",

    # Code block decoration
    "code_header_bg":    "#E4E1D8",
    "code_header_border": "#CCC9C0",
    "code_copy_color":   "#6B6860",
    "code_copy_hover":   "#1A1814",
    "code_copy_bg_hover": "#DDDAD0",
    "code_lang_color":   "#6B6860",
    "code_line_number":  "rgba(26,24,20,0.35)",
    "code_scrollbar":    "rgba(26,24,20,0.15)",
}

# ── Theme state ──
_current_theme = DARK


class _TokenProxy:
    """Dict-like live view of the ACTIVE theme.

    Bug history: seven UI modules did `from tokens import DARK as T`,
    binding their T name to the DARK dict object at import time — so
    set_theme('light') switched _current_theme but every T['key'] in those
    modules still read dark values forever (chat panel, tool cards,
    spinners, icons all stayed dark in light mode). Importing TOKENS
    instead routes every lookup through _current_theme at call time.
    """
    __slots__ = ()

    def __getitem__(self, key):
        # A missing token must NEVER crash a widget. Bug history: the update
        # dialog referenced 'mono_bright' which no palette defined — the
        # KeyError killed the dialog before it showed, so FORCE updates
        # silently never appeared (9 crashes in one log, three releases).
        # Log loudly, fall back to the theme's text color.
        try:
            return _current_theme[key]
        except KeyError:
            import logging
            logging.getLogger("tokens").error(
                "[TOKENS] Unknown token %r — add it to BOTH palettes in tokens.py", key)
            return _current_theme.get("text", "#e6edf3")
    def get(self, key, default=None): return _current_theme.get(key, default)
    def __contains__(self, key): return key in _current_theme
    def keys(self): return _current_theme.keys()
    def values(self): return _current_theme.values()
    def items(self): return _current_theme.items()
    def copy(self): return dict(_current_theme)
    def __iter__(self): return iter(_current_theme)
    def __len__(self): return len(_current_theme)


TOKENS = _TokenProxy()


def get_theme(mode: str = "dark") -> dict:
    """Return the token dict for the given theme mode.

    Bug history: this always returned DARK ("only dark is supported"), so
    the entire chat panel silently could not render light mode — every
    other layer switched but chat kept dark tokens (light-hash text on
    light backgrounds elsewhere, dark chat here)."""
    return LIGHT if mode == "light" else DARK


def set_theme(mode: str = "dark"):
    """Switch the active theme tokens ('dark' or 'light')."""
    global _current_theme
    _current_theme = LIGHT if mode == "light" else DARK


def T() -> dict:
    """Get current theme tokens. Usage: T()['key'] — allows runtime switching."""
    return _current_theme


def build_markdown_css(t: dict | None = None) -> str:
    """Generate unified markdown CSS from design tokens. Single source of truth.

    OpenCode OC-2 styling:
    - Headings: purple, bold
    - Strong/bold: orange
    - Emphasis/italic: gold
    - Inline code: green with subtle bg
    - Code blocks: dark bg, subtle border, no padding (handled by wrapper)
    - Blockquotes: gold, italic, left border
    - Links: peach underline
    - Lists: blue markers
    - Tables: subtle borders, header bg
    """
    if t is None:
        t = _current_theme
    return f"""<style>
  body {{
    color: {t['md_text']} !important; font-size: {t['font_size']};
    line-height: {t['line_height']}; font-family: {t['font_ui']};
    overflow-wrap: break-word; word-break: break-word;
    max-width: 100%; box-sizing: border-box;
  }}

  /* Universal overflow prevention — NO element can cause horizontal scroll */
  * {{ overflow-wrap: break-word; word-wrap: break-word; box-sizing: border-box; }}

  /* Headings — OpenCode: purple (#9d7cd8), bold */
  h1, h2, h3, h4, h5, h6 {{
    color: {t['md_heading']} !important; font-weight: 600; margin: 8px 0 4px 0;
    overflow-wrap: break-word; word-wrap: break-word; max-width: 100%;
  }}
  h1 {{ font-size: 1.5em; border-bottom: 1px solid {t['border_dim']}; padding-bottom: 4px; margin-top: 12px; }}
  h2 {{ font-size: 1.35em; margin-top: 10px; }}
  h3 {{ font-size: 1.2em; margin-top: 8px; }}
  h4 {{ font-size: 1.05em; }}
  h5, h6 {{ font-size: 1em; color: {t['text_dim']} !important; }}

  /* Paragraphs */
  p {{ color: {t['md_text']} !important; margin: 3px 0; line-height: {t['line_height']};
      overflow-wrap: break-word; word-wrap: break-word; word-break: break-word;
      max-width: 100%; box-sizing: border-box; }}

  /* Strong — OpenCode: orange (#f5a742) */
  strong, b {{ color: {t['md_strong']} !important; font-weight: 600; }}

  /* Emphasis — OpenCode: gold (#e5c07b) */
  em, i {{ color: {t['md_emph']} !important; font-style: italic; }}

  /* Inline code — OpenCode: green (#7fd88f) with subtle bg */
  code {{
    color: {t['md_code']} !important; background: {t['md_code_bg']};
    font-family: {t['font_mono']}; font-size: 0.9em;
    padding: 2px 6px; border-radius: 4px;
  }}

  /* Code blocks — dark bg, subtle border */
  pre {{
    max-width: 100%; overflow-x: auto; box-sizing: border-box;
    background: {t['bg']}; border: 1px solid {t['border_dim']};
    border-radius: {t['radius_md']}; padding: 10px 14px; margin: 8px 0;
  }}
  pre code {{
    color: {t['text']} !important; background: transparent;
    padding: 0; border-radius: 0;
    font-size: 0.88em; line-height: {t['line_height_code']};
    white-space: pre-wrap;
  }}

  /* Links — OpenCode: peach (#fab283) */
  a {{ color: {t['md_link']} !important; text-decoration: underline; }}
  a:visited {{ color: {t['md_link']} !important; }}

  /* Blockquotes — demo: blue left border, normal text */
  blockquote {{
    border-left: 3px solid {t['md_blockquote_border']};
    color: {t['md_blockquote']} !important;
    padding: 4px 0 4px 12px; margin: 8px 0;
    background: transparent;
    border-radius: 0;
    overflow-wrap: break-word; word-wrap: break-word; word-break: break-word;
  }}

  /* Horizontal rules — subtle */
  hr {{ border: none; border-top: 1px solid {t['md_hr']}; margin: 10px 0; }}

  /* Lists — pure white text, NOT purple */
  ul, ol {{ padding-left: 20px; margin: 4px 0; overflow-wrap: break-word; word-wrap: break-word; }}
  li {{ margin: 2px 0; color: {t['md_text']} !important; line-height: {t['line_height']};
       overflow-wrap: break-word; word-wrap: break-word; word-break: break-word; }}
  li > ul, li > ol {{ margin: 2px 0 2px 0; }}

  /* Tables — purple header, white body, file names green */
  table {{ border-collapse: collapse; margin: 6px 0; width: 100%; font-size: 13px;
           table-layout: fixed; max-width: 100%; overflow-wrap: break-word; word-wrap: break-word; }}
  th, td {{
    border-bottom: 1px solid {t['md_table_border']};
    padding: 8px 12px; text-align: left; vertical-align: top;
    overflow-wrap: break-word; word-wrap: break-word; word-break: break-word;
  }}
  th {{
    color: {t['md_heading']} !important; font-weight: 600;
    border-bottom: 1px solid {t['border']};
    background: {t['md_table_header_bg']};
  }}
  /* Qt setMarkdown() uses <td> for headers \u2014 style first row like th */
  tr:first-child td {{
    color: {t['md_heading']} !important; font-weight: 600;
    border-bottom: 1px solid {t['border']};
    background: {t['md_table_header_bg']};
  }}
  td {{ color: {t['md_text']} !important; }}
  /* Remove outer borders */
  table tr:first-child th, table tr:first-child td {{ border-top: none; }}
  table tr:last-child td {{ border-bottom: none; }}
  table th:first-child, table td:first-child {{ border-left: none; }}
  table th:last-child, table td:last-child {{ border-right: none; }}

  /* Strikethrough */
  del, s {{ color: {t['md_strikethrough']} !important; text-decoration: line-through; }}

  /* Mark highlighting */
  mark {{ background: {t['md_mark_bg']}; color: {t['text']} !important; border-radius: 2px; padding: 0 3px; }}

  /* Images */
  img {{ max-width: 100%; border-radius: {t['radius_sm']}; margin: 8px 0; }}

  /* Definition lists */
  dt {{ font-weight: 600; color: {t['md_heading']} !important; margin-top: 12px; }}
  dd {{ margin-left: 20px; color: {t['text_dim']} !important; }}

  /* Task lists (checkboxes) */
  input[type="checkbox"] {{
    margin-right: 6px;
  }}

  /* Keyboard shortcuts */
  kbd {{
    background: {t['bg_secondary']}; border: 1px solid {t['border_dim']};
    border-radius: 3px; padding: 1px 5px;
    font-family: {t['font_mono']}; font-size: 0.88em;
    color: {t['text_dim']} !important;
  }}

  /* File names/paths — GREEN highlight */
  code.filename, code.filepath, span.filename {{
    color: {t['md_filename']} !important;
    background: rgba(63,185,80,0.08) !important;
    padding: 1px 4px !important;
    font-weight: 500;
    font-family: {t['font_mono']};
  }}
  /* Any code element that looks like a path (contains / or \\) — green */
  code[class*=\"path\"], code[class*=\"file\"] {{
    color: {t['md_filename']} !important;
    background: rgba(63,185,80,0.08) !important;
    padding: 1px 4px !important;
  }}
</style>"""


def build_code_block_css(t: dict | None = None) -> str:
    """CSS for code block decorations (header bar, copy button, language label)."""
    if t is None:
        t = _current_theme
    return f"""<style>
    /* Code block wrapper */
    .cb-wrapper {{
      margin: 12px 0; border-radius: {t['radius_md']};
      border: 1px solid {t['code_header_border']};
    }}
    /* Code block header bar */
    .cb-header {{
      background: {t['code_header_bg']};
      border-bottom: 1px solid {t['code_header_border']};
      padding: 6px 14px; min-height: 32px;
    }}
    /* Language label */
    .cb-lang {{
      color: {t['code_lang_color']}; font-family: {t['font_mono']};
      font-size: 11px; text-transform: uppercase;
      font-weight: 500;
    }}
    /* Copy button */
    .cb-copy {{
      color: {t['code_copy_color']}; font-size: 11px;
      padding: 3px 10px; border-radius: {t['radius_xs']};
      border: 1px solid {t['border_dim']}; background: {t['code_copy_bg']};
      font-family: {t['font_ui']};
    }}
    /* Code body */
    .cb-body {{
      background: {t['bg']};
      border-top: none;
    }}
    .cb-body pre {{
      margin: 0; border: none; border-radius: 0;
    }}
    </style>"""


def build_qss(t: dict | None = None, mode: str | None = None) -> str:
    """Build the full QSS stylesheet from tokens.

    Defaults to the ACTIVE theme — a hardcoded mode="dark" default here
    would hand dark styles to light-mode callers."""
    if t is None:
        t = get_theme(mode) if mode else _current_theme
    return f"""
    QWidget {{
        background: {t['bg']};
        color: {t['text']};
        font-family: {t['font_ui']};
        font-size: {t['font_size']};
    }}
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{
        background: transparent; width: 8px; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {t['border']}; border-radius: 4px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {t['border_active']};
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

    #userBubble {{
        background: {t['bg_card']};
        border: none;
        border-right: 3px solid #ff8c00;
        border-radius: 0px;
        padding: 12px 18px;
        font-size: 14px;
        line-height: 1.65;
        color: rgba(255,255,255,0.92);
        font-family: {t['font_mono']};
    }}

    #aiCard {{
        background: transparent;
        border: none;
    }}

    QTextBrowser {{
        background: transparent;
        border: none;
        font-size: {t['font_size']};
    }}

    #cardFrame {{
        border: 1px solid {t['border']};
        border-radius: 0px;
    }}
    #cardHeader {{ background: transparent; }}
    #thoughtHeader {{
        background: rgba(124,108,231,0.06);
        border-radius: {t['radius_md']};
    }}
    #cardHeaderLabel {{
        color: {t['text_secondary']};
        font-size: {t['font_size_xs']};
        font-weight: 400;
    }}
    #thoughtLabel {{
        color: {t['think_label']};
        font-size: {t['font_size_sm']};
        font-weight: 500;
    }}
    #thoughtBody {{
        color: {t['text_secondary']};
        font-size: {t['font_size_sm']};
        font-style: italic;
    }}

    #toolName {{
        color: {t['text_secondary']};
        font-size: {t['font_size_xs']};
    }}
    #toolArg {{
        color: {t['muted']};
        font-family: {t['font_mono']};
        font-size: {t['font_size_xxs']};
    }}

    #chatInput {{
        background: {t['bg_card']};
        border: 1px solid {t['border']};
        border-radius: {t['radius_lg']};
        padding: 10px 12px;
        font-size: {t['font_size']};
    }}
    #chatInput:focus {{
        border-color: {t['border_active']};
    }}
    #sendBtn {{
        background: {t['accent']};
        border: none;
        border-radius: {t['radius_md']};
        padding: 8px 14px;
        color: #04222a;
        font-weight: 600;
    }}
    #sendBtn:hover {{
        background: {t['accent']};
        opacity: 0.9;
    }}
    #stopBtn {{
        background: transparent;
        border: 1px solid {t['red']};
        border-radius: {t['radius_md']};
        padding: 8px 14px;
        color: {t['red']};
        font-weight: 600;
    }}
    #stopBtn:hover {{
        background: rgba(248,81,73,0.10);
    }}
    #toolbarBtn {{
        background: rgba(255,255,255,0.05);
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: {t['radius_sm']};
        padding: 4px 10px;
        color: {t['text_dim']};
        font-size: {t['font_size_xs']};
    }}
    #toolbarBtn:hover {{ background: rgba(255,255,255,0.08); }}
    #toolbarBtn::menu-indicator {{ image: none; width: 0; }}
    QMenu {{
        background: {t['bg_card']};
        border: 1px solid {t['border']};
        border-radius: {t['radius_md']};
        padding: 6px;
        color: {t['text']};
    }}
    QMenu::item {{
        padding: 6px 12px;
        border-radius: {t['radius_sm']};
        font-size: {t['font_size_xs']};
    }}
    QMenu::item:selected {{ background: rgba(255,255,255,0.08); }}
    QMenu::item:disabled {{ color: {t['muted']}; font-size: 10px; }}
    QMenu::separator {{
        height: 1px;
        background: {t['border']};
        margin: 6px 4px;
    }}
    #chev {{
        color: {t['muted']};
        border: none;
        background: transparent;
        font-size: 14px;
        width: 20px;
    }}
    """


# ── Context Window Compact Thresholds ──────────────────────────────────────
# These are UI design tokens for display purposes.
# The actual runtime threshold is calculated by
# agent.src.services.compact.autoCompact.getAutoCompactThreshold()
# which uses model-aware buffer tokens (13K for auto, 3K for manual).

COMPACT_EARLY_THRESHOLD = 0.85   # 85% — visual warning in token bar
COMPACT_URGENT_THRESHOLD = 0.95  # 95% — visual urgent state in token bar


def get_model_context_limit(model_id: str) -> int:
    """
    Get the context window size (tokens) for a given model ID.

    Used by UI components to determine when to show context pressure
    warnings and trigger early compaction.

    Delegates to model_limits.py registry. Falls back to 200K tokens.
    """
    try:
        from src.ai.model_limits import get_model_limits
        limits = get_model_limits(model_id)
        return limits.context_window
    except Exception:
        return 200_000


def get_auto_compact_threshold(model_id: str) -> int:
    """
    Get the model-aware auto-compact threshold (tokens).

    Delegates to the existing autoCompact system which calculates:
        effectiveContextWindow - AUTOCOMPACT_BUFFER_TOKENS(13,000)

    This is more precise than a flat 85% multiplier and matches
    the production compaction trigger used by the agent runtime.
    """
    try:
        try:
            from src.agent.src.services.compact.autoCompact import getAutoCompactThreshold
        except ImportError:
            from agent.src.services.compact.autoCompact import getAutoCompactThreshold
        return getAutoCompactThreshold(model_id)
    except ImportError:
        # Fallback: 85% of context window
        ctx = get_model_context_limit(model_id)
        return int(ctx * 0.85)