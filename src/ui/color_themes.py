"""Settings -> Appearance -> Color theme: one theme for the whole of Cortex.

"cortex" (the default) is Cortex's own look, following Dark / Light / System.
Any other choice is a well-known editor theme applied everywhere:

* Web pages (Settings, sidebar, Project Health, the editor's tab bar, the
  terminal header) - live. One script on the shared WebEngine profile adds a
  <style id="cortex-color-theme"> that overrides each page's colour
  variables; switching rewrites it in the open pages.
* The code editor - live, when Editor -> Theme is "auto" it follows this.
* The terminal - live, through xterm's own 16-colour theme.
* Native Qt (chat panel, menus, dialogs, status bar) - live too. Their
  colours come from the token palettes in tokens.py, baked into stylesheets
  when each widget is built. A switch points the palettes at the theme
  (apply_native) and then runs the dark/light switch pipeline - the chat's
  batched re-theme, the scoped QSS, the inline remap - with the previous
  palette as the colour source (main_window._apply_color_theme_live). At
  startup the palettes are set before any widget exists.

A theme is dark or light (`base`); choosing one also switches Cortex to that
mode, so everything not covered by a colour override still matches.
"""
from __future__ import annotations

import json
import re

from src.utils.logger import get_logger

log = get_logger("color_themes")

DEFAULT_COLOR_THEME = "cortex"

# Base colours per theme. ui: bg (editor), bg_alt (sidebar / darkest),
# surface (panels, tab bar), surface2 (inputs, inactive tabs), hover,
# border, fg, fg_dim, muted, accent, selection, plus the semantic hues and
# the syntax colours the chat's code blocks use. ansi: the 16 terminal
# colours (black..white, then the bright row).
THEMES = {
    "dark-plus": {
        "label": "Dark+ (VS Code)", "base": "dark",
        "ui": dict(bg="#1e1e1e", bg_alt="#181818", surface="#252526", surface2="#2d2d2d",
                   hover="#2a2d2e", border="#3c3c3c", fg="#d4d4d4", fg_dim="#a0a0a0",
                   muted="#6e6e6e", accent="#0078d4", selection="#264f78",
                   red="#f14c4c", green="#89d185", yellow="#cca700", orange="#ce9178",
                   blue="#3794ff", purple="#c586c0", cyan="#4ec9b0",
                   kw="#569cd6", str="#ce9178", fn="#dcdcaa", num="#b5cea8",
                   cmt="#6a9955", type="#4ec9b0"),
        "ansi": ["#000000", "#cd3131", "#0dbc79", "#e5e510", "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5",
                 "#666666", "#f14c4c", "#23d18b", "#f5f543", "#3b8eea", "#d670d6", "#29b8db", "#ffffff"],
    },
    "light-plus": {
        "label": "Light+ (VS Code)", "base": "light",
        "ui": dict(bg="#ffffff", bg_alt="#f3f3f3", surface="#f3f3f3", surface2="#e8e8e8",
                   hover="#e4e6f1", border="#d4d4d4", fg="#1f1f1f", fg_dim="#555555",
                   muted="#767676", accent="#005fb8", selection="#add6ff",
                   red="#c72e0f", green="#388a34", yellow="#8a6d00", orange="#a31515",
                   blue="#0451a5", purple="#af00db", cyan="#267f99",
                   kw="#0000ff", str="#a31515", fn="#795e26", num="#098658",
                   cmt="#008000", type="#267f99"),
        "ansi": ["#000000", "#cd3131", "#00bc00", "#949800", "#0451a5", "#bc05bc", "#0598bc", "#555555",
                 "#666666", "#cd3131", "#14ce14", "#b5ba00", "#0451a5", "#bc05bc", "#0598bc", "#1f1f1f"],
    },
    "one-dark-pro": {
        "label": "One Dark Pro", "base": "dark",
        "ui": dict(bg="#282c34", bg_alt="#21252b", surface="#21252b", surface2="#2c313a",
                   hover="#2c313c", border="#3a3f4b", fg="#abb2bf", fg_dim="#9da5b4",
                   muted="#5c6370", accent="#61afef", selection="#3e4451",
                   red="#e06c75", green="#98c379", yellow="#e5c07b", orange="#d19a66",
                   blue="#61afef", purple="#c678dd", cyan="#56b6c2",
                   kw="#c678dd", str="#98c379", fn="#61afef", num="#d19a66",
                   cmt="#7f848e", type="#e5c07b"),
        "ansi": ["#3f4451", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#abb2bf",
                 "#5c6370", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#ffffff"],
    },
    "dracula": {
        "label": "Dracula", "base": "dark",
        "ui": dict(bg="#282a36", bg_alt="#21222c", surface="#21222c", surface2="#343746",
                   hover="#343746", border="#44475a", fg="#f8f8f2", fg_dim="#c4c6d4",
                   muted="#6272a4", accent="#bd93f9", selection="#44475a",
                   red="#ff5555", green="#50fa7b", yellow="#f1fa8c", orange="#ffb86c",
                   blue="#8be9fd", purple="#bd93f9", cyan="#8be9fd",
                   kw="#ff79c6", str="#f1fa8c", fn="#50fa7b", num="#bd93f9",
                   cmt="#6272a4", type="#8be9fd"),
        "ansi": ["#21222c", "#ff5555", "#50fa7b", "#f1fa8c", "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2",
                 "#6272a4", "#ff6e6e", "#69ff94", "#ffffa5", "#d6acff", "#ff92df", "#a4ffff", "#ffffff"],
    },
    "monokai": {
        "label": "Monokai", "base": "dark",
        "ui": dict(bg="#272822", bg_alt="#1e1f1c", surface="#1e1f1c", surface2="#34352f",
                   hover="#3e3d32", border="#414339", fg="#f8f8f2", fg_dim="#c2c2b0",
                   muted="#75715e", accent="#a6e22e", selection="#49483e",
                   red="#f92672", green="#a6e22e", yellow="#e6db74", orange="#fd971f",
                   blue="#66d9ef", purple="#ae81ff", cyan="#a1efe4",
                   kw="#f92672", str="#e6db74", fn="#a6e22e", num="#ae81ff",
                   cmt="#75715e", type="#66d9ef"),
        "ansi": ["#333333", "#f92672", "#a6e22e", "#f4bf75", "#66d9ef", "#ae81ff", "#a1efe4", "#f8f8f2",
                 "#75715e", "#f92672", "#a6e22e", "#f4bf75", "#66d9ef", "#ae81ff", "#a1efe4", "#f9f8f5"],
    },
    "github-dark": {
        "label": "GitHub Dark", "base": "dark",
        "ui": dict(bg="#0d1117", bg_alt="#010409", surface="#161b22", surface2="#21262d",
                   hover="#1c2128", border="#30363d", fg="#e6edf3", fg_dim="#9198a1",
                   muted="#6e7681", accent="#2f81f7", selection="#1c3a5e",
                   red="#f85149", green="#3fb950", yellow="#d29922", orange="#db6d28",
                   blue="#58a6ff", purple="#bc8cff", cyan="#39c5cf",
                   kw="#ff7b72", str="#a5d6ff", fn="#d2a8ff", num="#79c0ff",
                   cmt="#8b949e", type="#ffa657"),
        "ansi": ["#484f58", "#ff7b72", "#3fb950", "#d29922", "#58a6ff", "#bc8cff", "#39c5cf", "#b1bac4",
                 "#6e7681", "#ffa198", "#56d364", "#e3b341", "#79c0ff", "#d2a8ff", "#56d4dd", "#ffffff"],
    },
    "github-light": {
        "label": "GitHub Light", "base": "light",
        "ui": dict(bg="#ffffff", bg_alt="#f6f8fa", surface="#f6f8fa", surface2="#eaeef2",
                   hover="#eef1f4", border="#d0d7de", fg="#1f2328", fg_dim="#59636e",
                   muted="#6e7781", accent="#0969da", selection="#ddf4ff",
                   red="#cf222e", green="#1a7f37", yellow="#9a6700", orange="#bc4c00",
                   blue="#0969da", purple="#8250df", cyan="#1b7c83",
                   kw="#cf222e", str="#0a3069", fn="#8250df", num="#0550ae",
                   cmt="#6e7781", type="#953800"),
        "ansi": ["#24292f", "#cf222e", "#116329", "#4d2d00", "#0969da", "#8250df", "#1b7c83", "#59636e",
                 "#57606a", "#a40e26", "#1a7f37", "#633c01", "#218bff", "#a475f9", "#3192aa", "#1f2328"],
    },
    "night-owl": {
        "label": "Night Owl", "base": "dark",
        "ui": dict(bg="#011627", bg_alt="#01111d", surface="#0b2942", surface2="#13344f",
                   hover="#0e2c47", border="#1d3b53", fg="#d6deeb", fg_dim="#a1b3c7",
                   muted="#637777", accent="#82aaff", selection="#1d3b53",
                   red="#ef5350", green="#22da6e", yellow="#addb67", orange="#f78c6c",
                   blue="#82aaff", purple="#c792ea", cyan="#7fdbca",
                   kw="#c792ea", str="#ecc48d", fn="#82aaff", num="#f78c6c",
                   cmt="#637777", type="#ffcb8b"),
        "ansi": ["#011627", "#ef5350", "#22da6e", "#addb67", "#82aaff", "#c792ea", "#21c7a8", "#ffffff",
                 "#575656", "#ef5350", "#22da6e", "#ffeb95", "#82aaff", "#c792ea", "#7fdbca", "#ffffff"],
    },
    "tokyo-night": {
        "label": "Tokyo Night", "base": "dark",
        "ui": dict(bg="#1a1b26", bg_alt="#16161e", surface="#16161e", surface2="#1f2335",
                   hover="#232433", border="#292e42", fg="#c0caf5", fg_dim="#9aa5ce",
                   muted="#565f89", accent="#7aa2f7", selection="#283457",
                   red="#f7768e", green="#9ece6a", yellow="#e0af68", orange="#ff9e64",
                   blue="#7aa2f7", purple="#bb9af7", cyan="#7dcfff",
                   kw="#bb9af7", str="#9ece6a", fn="#7aa2f7", num="#ff9e64",
                   cmt="#565f89", type="#2ac3de"),
        "ansi": ["#15161e", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
                 "#414868", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#c0caf5"],
    },
    "catppuccin-mocha": {
        "label": "Catppuccin Mocha", "base": "dark",
        "ui": dict(bg="#1e1e2e", bg_alt="#181825", surface="#181825", surface2="#313244",
                   hover="#2a2b3c", border="#313244", fg="#cdd6f4", fg_dim="#a6adc8",
                   muted="#6c7086", accent="#cba6f7", selection="#45475a",
                   red="#f38ba8", green="#a6e3a1", yellow="#f9e2af", orange="#fab387",
                   blue="#89b4fa", purple="#cba6f7", cyan="#94e2d5",
                   kw="#cba6f7", str="#a6e3a1", fn="#89b4fa", num="#fab387",
                   cmt="#6c7086", type="#f9e2af"),
        "ansi": ["#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
                 "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8"],
    },
    "nord": {
        "label": "Nord", "base": "dark",
        "ui": dict(bg="#2e3440", bg_alt="#292e39", surface="#3b4252", surface2="#434c5e",
                   hover="#434c5e", border="#4c566a", fg="#d8dee9", fg_dim="#aeb6c4",
                   muted="#7b88a1", accent="#88c0d0", selection="#434c5e",
                   red="#bf616a", green="#a3be8c", yellow="#ebcb8b", orange="#d08770",
                   blue="#81a1c1", purple="#b48ead", cyan="#88c0d0",
                   kw="#81a1c1", str="#a3be8c", fn="#88c0d0", num="#b48ead",
                   cmt="#616e88", type="#8fbcbb"),
        "ansi": ["#3b4252", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
                 "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4"],
    },
    "solarized-light": {
        "label": "Solarized Light", "base": "light",
        "ui": dict(bg="#fdf6e3", bg_alt="#eee8d5", surface="#eee8d5", surface2="#e4ddc8",
                   hover="#e4ddc8", border="#d3cbb7", fg="#3b4f56", fg_dim="#586e75",
                   muted="#7c8b8b", accent="#268bd2", selection="#dfd7bf",
                   red="#dc322f", green="#6c7c00", yellow="#8f6c00", orange="#cb4b16",
                   blue="#268bd2", purple="#6c71c4", cyan="#2aa198",
                   kw="#859900", str="#2aa198", fn="#268bd2", num="#d33682",
                   cmt="#93a1a1", type="#b58900"),
        "ansi": ["#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#657b83",
                 "#002b36", "#cb4b16", "#586e75", "#657b83", "#268bd2", "#6c71c4", "#2aa198", "#4f6168"],
    },
}

# ── Accessibility themes ────────────────────────────────────────────────────
# For users with low vision or colour-vision deficiency. Same structure as
# the themes above, so they reach every part of Cortex the same way.
#
# High Contrast: text and every syntax colour at least 7:1 on the background
# (WCAG AAA), strong borders, and Monaco's own hc-black / hc-light editor
# bases, which outline the current line, focus and selections.
#
# Distinct Colors: built on the Okabe-Ito palette, whose colours stay
# distinguishable for every type of colour vision (including protanopia,
# deuteranopia and tritanopia). Meanings that other themes carry in red vs
# green (errors vs success, git removed vs added, terminal red vs green) use
# orange vs blue here instead, as GitHub's colour-vision themes do. Named for
# what the theme does, not for who might pick it: anyone can choose it
# without labelling themselves.
THEMES.update({
    "high-contrast-dark": {
        "label": "High Contrast Dark", "base": "dark", "a11y": True,
        "ui": dict(bg="#000000", bg_alt="#000000", surface="#0a0a0a", surface2="#1a1a1a",
                   hover="#262626", border="#9a9a9a", fg="#ffffff", fg_dim="#e6e6e6",
                   muted="#c8c8c8", accent="#ffd60a", selection="#1f4f8f",
                   red="#ff7a7a", green="#5cff8f", yellow="#ffff4d", orange="#ffad5c",
                   blue="#7cc0ff", purple="#e0b0ff", cyan="#5cf2ff",
                   kw="#7cc0ff", str="#ffd27f", fn="#fff36b", num="#ffad5c",
                   cmt="#c8c8c8", type="#5cf2ff"),
        "ansi": ["#000000", "#ff7a7a", "#5cff8f", "#ffff4d", "#7cc0ff", "#ff8cff", "#5cf2ff", "#ffffff",
                 "#a6a6a6", "#ff9999", "#85ffab", "#ffff80", "#a3d4ff", "#ffadff", "#8cf7ff", "#ffffff"],
    },
    "high-contrast-light": {
        "label": "High Contrast Light", "base": "light", "a11y": True,
        "ui": dict(bg="#ffffff", bg_alt="#f5f5f5", surface="#f5f5f5", surface2="#e8e8e8",
                   hover="#dedede", border="#5a5a5a", fg="#000000", fg_dim="#1f1f1f",
                   muted="#3d3d3d", accent="#0f4a85", selection="#b3d7ff",
                   red="#a80000", green="#005a16", yellow="#6b5000", orange="#8a3600",
                   blue="#0039a6", purple="#6d1fa8", cyan="#005b66",
                   kw="#0039a6", str="#a31515", fn="#6d1fa8", num="#005b66",
                   cmt="#2f5f2f", type="#7a3d00"),
        "ansi": ["#000000", "#a80000", "#005a16", "#6b5000", "#0039a6", "#8b008b", "#005b66", "#3d3d3d",
                 "#3d3d3d", "#c00000", "#007a1f", "#7a5c00", "#0047c2", "#9c1aa8", "#006b78", "#000000"],
    },
    "distinct-colors-dark": {
        "label": "Distinct Colors Dark", "base": "dark", "a11y": True,
        "ui": dict(bg="#0d1117", bg_alt="#010409", surface="#161b22", surface2="#21262d",
                   hover="#1c2128", border="#3d444d", fg="#e6edf3", fg_dim="#9ea7b3",
                   muted="#8b949e", accent="#56b4e9", selection="#1d3f66",
                   red="#ff8c42", green="#5aa9ff", yellow="#f0e442", orange="#e69f00",
                   blue="#5aa9ff", purple="#e6a0c4", cyan="#8fd3f5",
                   kw="#56b4e9", str="#f0e442", fn="#e6a0c4", num="#e69f00",
                   cmt="#8b949e", type="#2fd1a0"),
        "ansi": ["#484f58", "#ff8c42", "#5aa9ff", "#f0e442", "#56b4e9", "#e6a0c4", "#8fd3f5", "#b1bac4",
                 "#6e7681", "#ffa666", "#85c1ff", "#f5ec75", "#85c8f0", "#f0bcd8", "#b3e3fa", "#ffffff"],
    },
    "distinct-colors-light": {
        "label": "Distinct Colors Light", "base": "light", "a11y": True,
        "ui": dict(bg="#ffffff", bg_alt="#f6f8fa", surface="#f6f8fa", surface2="#eaeef2",
                   hover="#eef1f4", border="#c4ccd5", fg="#1f2328", fg_dim="#57606a",
                   muted="#656d76", accent="#0072b2", selection="#cce4f6",
                   red="#b84a00", green="#0062b8", yellow="#6e5c00", orange="#9a5600",
                   blue="#0062b8", purple="#9c3f72", cyan="#00658f",
                   kw="#0072b2", str="#9a4a00", fn="#9c3f72", num="#00658f",
                   cmt="#656d76", type="#00775a"),
        "ansi": ["#24292f", "#b84a00", "#0062b8", "#6e5c00", "#0072b2", "#9c3f72", "#00658f", "#57606a",
                 "#57606a", "#d55e00", "#1f7fd6", "#857000", "#2d8fd5", "#b5558a", "#0083b3", "#1f2328"],
    },
})

_listeners: list = []
_active_at_start = DEFAULT_COLOR_THEME     # the theme the native palettes hold now
_web_installed = False


# ── settings ────────────────────────────────────────────────────────────────

def color_theme() -> str:
    """The saved colour theme id; anything unknown is "cortex"."""
    try:
        from src.config.settings import get_settings
        raw = str(get_settings().get("ui", "color_theme", default="") or "").strip()
    except Exception:
        raw = ""
    return raw if raw in THEMES else DEFAULT_COLOR_THEME


def theme_base(tid: str) -> str | None:
    """"dark" / "light" for a named theme, None for "cortex"."""
    t = THEMES.get(tid)
    return t["base"] if t else None


def active_at_start() -> str:
    return _active_at_start


# ── colour helpers ──────────────────────────────────────────────────────────

def _rgb(hexcol: str) -> tuple:
    h = hexcol.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgba(hexcol: str, a: float) -> str:
    r, g, b = _rgb(hexcol)
    return f"rgba({r},{g},{b},{a})"


# ── native Qt palettes (tokens.py), applied at startup ─────────────────────

def token_overrides(tid: str) -> tuple:
    """(base-palette overrides, chat-palette overrides) for theme `tid`."""
    c = THEMES[tid]["ui"]
    light = THEMES[tid]["base"] == "light"
    base = {}

    def put(value, *keys):
        for k in keys:
            base[k] = value

    put(c["bg"], "bg")
    put(c["surface"], "bg_card", "bg_elevated", "bg_raised", "context_menu_bg", "menu_bg")
    put(c["surface2"], "bg_secondary", "code_header_bg", "btn_bg", "user_bubble", "md_code_bg",
        "border_dim", "md_hr", "md_table_border")
    put(c["bg_alt"], "bg_tertiary", "edited_row_bg")
    put(c["bg"] if light else c["bg_alt"], "bg_input", "spell_input_bg")
    put(c["hover"], "bg_hover", "input_hover", "btn_hover", "menu_selected",
        "edited_row_hover", "code_copy_bg_hover")
    put(c["border"], "border", "border_color", "separator", "divider",
        "context_menu_border", "code_header_border", "input_border")
    put(c["fg"], "text", "text_primary", "md_text", "md_heading", "syntax_variable",
        "menu_text", "edited_row_text", "btn_text_hover", "code_copy_hover",
        "mono_bright", "text_on_error")
    put(c["fg_dim"], "text_dim", "text_secondary", "md_blockquote", "btn_text", "syntax_operator")
    put(c["muted"], "muted", "mono_muted", "code_lang_color", "code_copy_color",
        "tool_generic", "code_line_number")
    put(c["accent"], "accent", "accent_primary", "accent_secondary", "border_active",
        "streaming_cursor", "md_link", "md_link_text", "md_blockquote_border",
        "md_list_marker", "ring_cyan", "ring_cyan_light", "ring_purple",
        "ring_purple_light", "think", "think_label", "tool_thought", "status_running")
    put(c["green"], "green", "status_ok", "tool_edit", "tool_write", "md_filename")
    put(c["red"], "red", "error", "error_border", "status_error", "stop_btn",
        "spell_error", "tool_team", "syntax_builtin", "syntax_tag")
    put(c["orange"], "orange", "tool_terminal", "md_strong", "syntax_attribute")
    put(c["yellow"], "warning", "edited_row_badge", "md_emph")
    put(c["blue"], "blue", "tool_read")
    put(c["purple"], "info", "tool_search", "tool_task")
    put(c["cyan"], "tool_web", "syntax_property")
    put(c["selection"], "context_menu_sel")
    put(c["str"], "syntax_string", "md_code")
    put(c["kw"], "syntax_keyword")
    put(c["fn"], "syntax_function")
    put(c["num"], "syntax_number", "syntax_constant")
    put(c["type"], "syntax_type", "syntax_decorator", "syntax_namespace")
    put(c["cmt"], "syntax_comment")
    put(_rgba(c["red"], 0.14), "bg_error")
    put(_rgba(c["red"], 0.15), "stop_btn_hover_bg")
    put(_rgba(c["green"], 0.12), "diff_add_bg")
    put(_rgba(c["red"], 0.12), "diff_del_bg")
    put(_rgba(c["green"], 0.22), "diff_add_line")
    put(_rgba(c["red"], 0.22), "diff_del_line")
    put(_rgba(c["fg"], 0.06), "diff_hunk_bg")
    put(_rgba(c["fg"], 0.03), "tool_header_bg")
    put(_rgba(c["fg"], 0.01), "tool_body_bg")
    put(_rgba(c["fg"], 0.08), "card_border_subtle")
    put(_rgba(c["fg"], 0.015), "card_bg_subtle")
    put(_rgba(c["fg"], 0.04), "md_table_header_bg")
    put(_rgba(c["fg"], 0.10), "code_scrollbar")
    put(_rgba(c["fg"], 0.40), "md_strikethrough")
    put(_rgba(c["yellow"], 0.25), "md_mark_bg")

    chat = {}
    for k in ("text", "text_primary", "mono_bright", "md_heading", "md_text", "md_code",
              "md_strong", "md_emph", "md_filename", "btn_text_hover", "menu_text",
              "edited_row_text", "code_copy_hover"):
        chat[k] = c["fg"]
    for k in ("text_dim", "text_secondary", "md_blockquote", "btn_text", "info"):
        chat[k] = c["fg_dim"]
    for k in ("muted", "mono_muted", "think", "think_label", "md_list_marker", "status_ok",
              "code_copy_color", "code_lang_color", "tool_read", "tool_edit", "tool_write",
              "tool_search", "tool_terminal", "tool_web", "tool_task", "tool_team",
              "tool_generic"):
        chat[k] = c["muted"]
    for k in ("accent", "accent_primary", "accent_secondary", "border_active", "accent_fill",
              "accent_fill_hover", "blue", "streaming_cursor", "md_link", "md_link_text",
              "tool_thought", "status_running", "ring_cyan", "ring_cyan_light",
              "ring_purple", "ring_purple_light"):
        chat[k] = c["accent"]
    for k in ("red", "error", "error_border", "status_error", "stop_btn", "spell_error"):
        chat[k] = c["red"]
    for k in ("orange", "warning"):
        chat[k] = c["yellow"]
    chat["accent_ink"] = c["bg"]
    chat["user_bubble"] = c["surface2"]
    chat["md_code_bg"] = c["surface2"]
    chat["md_blockquote_border"] = c["border"]
    chat["md_hr"] = c["border"]
    chat["md_table_border"] = c["border"]
    chat["md_table_header_bg"] = _rgba(c["fg"], 0.04)
    chat["md_mark_bg"] = _rgba(c["accent"], 0.22)
    chat["md_strikethrough"] = _rgba(c["fg"], 0.45)
    chat["context_menu_sel"] = c["selection"]
    return base, chat


# dark.qss / light.qss use their own hex values, not the token palette. Each
# one mapped by what the property paints: (fg, bg, border) -> theme colour.
_QSS_ROLES = {
    "dark": {
        "#141414": (None, "bg_alt", None), "#163761": (None, "selection", None),
        "#181818": (None, "bg_alt", None), "#1a1a1a": (None, "bg", None),
        "#1e1e1e": (None, "bg", None), "#228df2": ("accent", "accent", "accent"),
        "#292929": (None, "surface", None), "#2a2a2a": (None, "surface2", "border"),
        "#2d2d2d": (None, "surface2", None), "#3d3d3d": (None, "hover", None),
        "#404040": (None, None, "border"), "#4c9df3": (None, "accent", None),
        "#505050": (None, "muted", None), "#6d6d6d": ("muted", "muted", None),
        "#d4d4d4": ("fg", None, None), "#d6d6dd": ("fg", None, None),
        "#ffffff": ("fg", None, None),
    },
    "light": {
        "#000000": ("fg", None, None), "#43a047": (None, "accent", None),
        "#4caf50": ("accent", "accent", "accent"), "#6d6d6d": ("muted", None, None),
        "#a0a0a0": ("muted", "muted", None), "#b0b0b0": (None, "muted", "border"),
        "#d0d0d0": (None, "surface2", "border"), "#e0e0e0": (None, "surface", None),
        "#e8f5e9": (None, "selection", None), "#eeeeee": (None, "surface2", None),
        "#f5f5f5": (None, "bg", None), "#ffffff": ("bg", "bg", None),
    },
}
_DECL = re.compile(r"([a-zA-Z-]+)(\s*:\s*)([^;{}]*)")
_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")


def remap_qss(qss: str, mode: str, tid: str | None = None) -> str:
    """The app-wide QSS for `mode`, recoloured to theme `tid` (default: the
    theme Cortex started with). Unchanged for "cortex" or a mode mismatch."""
    tid = tid or _active_at_start
    t = THEMES.get(tid)
    if not t or t["base"] != mode or not qss:
        return qss
    roles, c = _QSS_ROLES.get(mode, {}), t["ui"]

    def decl(m):
        prop = m.group(1).lower()
        slot = 0 if prop in ("color", "selection-color") else (
            2 if prop.startswith("border") or prop.startswith("outline") else 1)

        def col(cm):
            r = roles.get(cm.group(0).lower())
            key = r[slot] if r else None
            return c[key] if key else cm.group(0)
        return m.group(1) + m.group(2) + _HEX.sub(col, m.group(3))
    return _DECL.sub(decl, qss)


def qpalette_colors(tid: str | None = None) -> dict:
    """QPalette role name -> colour for the startup palette (empty: keep)."""
    t = THEMES.get(tid or _active_at_start)
    if not t:
        return {}
    c = t["ui"]
    return {"Window": c["bg"], "WindowText": c["fg"], "Base": c["bg"],
            "AlternateBase": c["surface"], "ToolTipBase": c["surface"],
            "ToolTipText": c["fg"], "Text": c["fg"], "Button": c["surface2"],
            "ButtonText": c["fg"], "Link": c["accent"], "Highlight": c["selection"],
            "HighlightedText": c["fg"]}


def apply_native(tid: str) -> None:
    """Point the token palettes at theme `tid` ("cortex": Cortex's own).

    Only the palettes: widgets already on screen are recoloured by the live
    switch (main_window._apply_color_theme_live), which also runs this.
    """
    global _active_at_start
    from src.ui import tokens
    if tid in THEMES:
        base, chat = token_overrides(tid)
        tokens.apply_named_palette(THEMES[tid]["base"], base, chat)
    else:
        tid = DEFAULT_COLOR_THEME
        tokens.apply_named_palette(None, {}, {})
    _active_at_start = tid
    log.info("[ColorTheme] native palette: %s", tid)


def apply_at_startup() -> str:
    """Rewrite the token palettes for the saved theme. Call once, after the
    QApplication exists and BEFORE any widget is built. Returns the theme."""
    global _active_at_start
    tid = color_theme()
    _active_at_start = tid
    if tid == DEFAULT_COLOR_THEME:
        return tid
    apply_native(tid)
    # The theme decides dark / light. 2026-10-17: GitHub Dark was saved but
    # the mode on disk said "light" (overwritten after the switch), so Cortex
    # started with the dark palette loaded and drew its light one - the
    # chosen theme looked lost after a restart. Correct the mode here,
    # before the window, the palette and the QSS read it.
    try:
        from src.config.settings import get_settings
        s = get_settings()
        base = THEMES[tid]["base"]
        if s.theme != base:
            log.info("[ColorTheme] mode %s -> %s to match %s", s.theme, base, tid)
            s.theme = base
    except Exception as exc:
        log.warning("[ColorTheme] mode not matched to the theme: %s", exc)
    return tid


# ── web pages ───────────────────────────────────────────────────────────────

def diff_vars(tid: str) -> list:
    """(--diff-* variable, colour) pairs for the editor's read-only diff.

    Changed lines are tinted, not recoloured: the text keeps the theme's
    foreground, the row a faint wash of its green / red and the line-number
    gutter a stronger one. Light themes need a little more tint to show on
    a pale background.
    """
    c = THEMES[tid]["ui"]
    light = THEMES[tid]["base"] == "light"
    row, gutter = (0.11, 0.20) if light else (0.09, 0.18)
    return [
        ("--diff-bg", c["bg"]), ("--diff-surface", c["surface"]),
        ("--diff-border", c["border"]), ("--diff-fg", c["fg"]),
        ("--diff-fg-strong", c["fg"]), ("--diff-fg-dim", c["fg_dim"]),
        ("--diff-gutter-fg", c["muted"]), ("--diff-hover", _rgba(c["fg"], 0.04)),
        ("--diff-btn-hover", _rgba(c["fg"], 0.08)), ("--diff-accent", c["accent"]),
        ("--diff-accent-bg", _rgba(c["accent"], 0.16)),
        ("--diff-green", c["green"]), ("--diff-red", c["red"]),
        ("--diff-add-bg", _rgba(c["green"], row)), ("--diff-add-gutter", _rgba(c["green"], gutter)),
        ("--diff-del-bg", _rgba(c["red"], row)), ("--diff-del-gutter", _rgba(c["red"], gutter)),
        ("--diff-skip-bg", _rgba(c["fg"], 0.03)),
        ("--diff-scroll", _rgba(c["muted"], 0.45)), ("--diff-scroll-hover", _rgba(c["fg_dim"], 0.55)),
    ]


def _page_css(tid: str) -> dict:
    """page key -> CSS for theme `tid`. Keys match the page's file name."""
    c = THEMES[tid]["ui"]
    imp = " !important"
    on = ":root,html,body,html[data-theme],[data-theme],body.light-theme"

    def vars_(pairs):
        return on + "{" + ";".join(f"{k}:{v}{imp}" for k, v in pairs) + "}"

    sidebar = vars_([
        ("--bg-sidebar", c["bg_alt"]), ("--bg-strip", c["bg_alt"]), ("--bg-panel", c["surface"]),
        ("--bg-hover", c["hover"]), ("--bg-selected", c["selection"]),
        ("--bg-selected-hover", c["selection"]), ("--bg-active", _rgba(c["accent"], 0.25)),
        ("--border-accent", c["accent"]), ("--text-primary", c["fg"]),
        ("--text-secondary", c["fg_dim"]), ("--text-muted", c["muted"]),
        ("--text-white", c["fg"]), ("--search-input-bg", c["surface2"]),
        ("--search-input-bg-focus", c["surface2"]), ("--search-input-border", c["border"]),
        ("--search-clear-color", c["muted"]), ("--search-clear-hover-color", c["fg"]),
        ("--search-clear-hover-bg", _rgba(c["fg"], 0.1)), ("--menu-border", c["border"]),
        ("--input-bg", c["surface2"]), ("--input-border", c["border"]),
        ("--git-modified", c["yellow"]), ("--git-added", c["green"]),
        ("--git-deleted", c["red"]), ("--git-conflict", c["purple"]),
    ]) + f"body{{background:{c['bg_alt']}{imp};color:{c['fg']}{imp}}}"

    settings = vars_([
        ("--bg", c["bg"]), ("--surface", c["surface"]), ("--surface-2", c["surface2"]),
        ("--surface-3", c["hover"]), ("--border", c["border"]), ("--border-strong", c["border"]),
        ("--text", c["fg"]), ("--muted", c["fg_dim"]), ("--accent", c["accent"]),
        ("--accent-2", c["accent"]), ("--danger", c["red"]),
        ("--danger-bg", _rgba(c["red"], 0.12)), ("--success", c["green"]),
        ("--warning", c["orange"]),
    ]) + (
        f"body{{background:{c['bg']}{imp};color:{c['fg']}{imp}}}"
        # Light mode hard-codes Cortex's terracotta and a beige nav in these
        # rules (memory_management.css, "LIGHT MODE" block); the same
        # selectors, so dark themes are covered too.
        f".settings-nav{{background:{c['bg_alt']}{imp}}}"
        f".nav-item{{color:{c['fg_dim']}{imp}}}"
        f".nav-item.active,.stat-value,.md-inline-code{{color:{c['accent']}{imp}}}"
        f".nav-item.active,.theme-option.active{{background:{_rgba(c['accent'], 0.12)}{imp}}}"
        f".nav-search input:focus,.setting-select:focus{{border-color:{c['accent']}{imp}}}"
        f".setting-btn.primary-btn,input:checked + .switch-slider{{background:{c['accent']}{imp};"
        f"border-color:{c['accent']}{imp}}}"
        f".setting-btn.primary-btn{{color:{c['bg']}{imp}}}"
        f".setting-range{{accent-color:{c['accent']}{imp}}}"
        f".settings-card,.stat-card,.consolidation-modal,.toast-host > div"
        f"{{background:{c['surface']}{imp}}}"
        f".nav-search input,.setting-select,.setting-btn,.md-code-block"
        f"{{background:{c['surface2']}{imp};color:{c['fg']}{imp}}}"
        f".nav-item:hover,.setting-btn:hover,.back-btn:hover,.md-inline-code"
        f"{{background:{c['hover']}{imp}}}"
        f".switch-slider{{background:{c['border']}{imp}}}"
        f".settings-card h3,.section-header h1,.profile-name,.insight-value,"
        f".nav-item:hover,.back-btn:hover,.model-token-pill,.toast-host > div"
        f"{{color:{c['fg']}{imp}}}"
        f".stat-label,.section-desc,.profile-meta,.insight-label,.empty-state-small p"
        f"{{color:{c['fg_dim']}{imp}}}"
        f".nav-search input::placeholder{{color:{c['muted']}{imp}}}"
        f"::-webkit-scrollbar-thumb{{background:{c['border']}{imp}}}"
    )

    health = vars_([
        ("--bg", c["bg"]), ("--surface", c["surface"]), ("--surface-2", c["surface2"]),
        ("--surface-3", c["bg_alt"]), ("--hover", c["hover"]), ("--border", c["border"]),
        ("--border-dim", c["surface2"]), ("--text", c["fg"]), ("--text-dim", c["fg_dim"]),
        ("--muted", c["muted"]), ("--accent", c["accent"]), ("--broken", c["red"]),
        ("--warning", c["yellow"]), ("--affected", c["orange"]), ("--new", c["blue"]),
        ("--healthy", c["green"]), ("--broken-bg", _rgba(c["red"], 0.12)),
        ("--warning-bg", _rgba(c["yellow"], 0.12)), ("--affected-bg", _rgba(c["orange"], 0.12)),
        ("--new-bg", _rgba(c["blue"], 0.12)), ("--healthy-bg", _rgba(c["green"], 0.10)),
        ("--edge", c["border"]), ("--edge-impact", c["orange"]),
        ("--grid", _rgba(c["fg"], 0.03)),
    ])

    # The read-only diff (Source Control -> Changes) draws only with these
    # variables; editor.html holds Cortex's own dark / light values.
    editor = vars_(diff_vars(tid)) + (
        f"body,#app{{background:{c['bg']}{imp};color:{c['fg']}{imp}}}"
        f"#tab-bar{{background:{c['surface']}{imp};border-bottom-color:{c['border']}{imp}}}"
        f".tab{{background:{c['surface2']}{imp};color:{c['fg_dim']}{imp};"
        f"border-right-color:{c['surface']}{imp}}}"
        f".tab.active{{background:{c['bg']}{imp};color:{c['fg']}{imp};"
        f"border-bottom-color:{c['accent']}{imp}}}"
        f".tab:hover:not(.active){{background:{c['hover']}{imp}}}"
        f".tab .close-btn{{color:{c['muted']}{imp}}}"
        f"#no-files-hint{{background:{c['bg']}{imp};color:{c['fg_dim']}{imp}}}"
    )

    terminal = (
        f"body{{background:{c['bg']}{imp}}}"
        f"#terminal-header{{background:{c['surface']}{imp};border-bottom-color:{c['border']}{imp};"
        f"color:{c['fg_dim']}{imp}}}"
        f"#terminal-header .term-select-btn{{background:{c['surface2']}{imp};color:{c['fg']}{imp};"
        f"border-color:{c['border']}{imp}}}"
        f"#terminal-header .term-select-btn:hover{{background:{c['hover']}{imp}}}"
        f"#terminal-header .term-dropdown{{background:{c['surface']}{imp};"
        f"border-color:{c['border']}{imp}}}"
        f"#terminal-header .term-dropdown-item{{color:{c['fg']}{imp}}}"
        f"#terminal-header .term-dropdown-item:hover{{background:{c['selection']}{imp}}}"
    )
    # Keys are file-name prefixes: the editor and terminal load resolved
    # copies (editor_resolved.html, terminal_1_resolved.html).
    return {"sidebar": sidebar, "memory_management": settings,
            "health_map": health, "editor": editor, "terminal": terminal}


def _apply_js(tid: str) -> str:
    """JS that sets (or clears) the theme style on a Cortex page."""
    from src.ui.fonts import _own_page_prefixes
    css = _page_css(tid) if tid in THEMES else {}
    return (
        "(function(){try{"
        f"var allow={json.dumps(_own_page_prefixes())};"
        "var href=(location.href||'').toLowerCase();"
        "if(href.indexOf('file:')!==0)return;"
        "if(!allow.some(function(p){return href.indexOf(p)===0;}))return;"
        f"var map={json.dumps(css)};"
        "var name=(location.pathname||'').split('/').pop().toLowerCase();"
        "var css='';Object.keys(map).forEach(function(k){if(name.indexOf(k)===0)css=map[k];});"
        "function put(){var s=document.getElementById('cortex-color-theme');"
        "if(!css){if(s)s.remove();return true;}"
        "var root=document.head||document.documentElement;if(!root)return false;"
        "if(!s){s=document.createElement('style');s.id='cortex-color-theme';}"
        "s.textContent=css;"
        # Last in <head>, after the page's own sheets.
        "root.appendChild(s);return true;}"
        "if(!put()){var mo=new MutationObserver(function(){if(put())mo.disconnect();});"
        "mo.observe(document,{childList:true,subtree:true});}"
        "document.addEventListener('DOMContentLoaded',put);"
        "}catch(e){}})();"
    )


def install_web_theme() -> bool:
    """Put the saved theme on every Cortex page, including pages created later."""
    global _web_installed
    try:
        from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript
        scripts = QWebEngineProfile.defaultProfile().scripts()
        for s in scripts.find("cortex-color-theme"):
            scripts.remove(s)
        script = QWebEngineScript()
        script.setName("cortex-color-theme")
        script.setSourceCode(_apply_js(color_theme()))
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        scripts.insert(script)
        _web_installed = True
        return True
    except Exception as exc:
        log.warning("[ColorTheme] web theme not installed: %s", exc)
        return False


# ── terminal ────────────────────────────────────────────────────────────────

def terminal_theme(tid: str | None = None):
    """xterm theme object for `tid`, or None for Cortex's own colours."""
    t = THEMES.get(tid or color_theme())
    if not t:
        return None
    c, a = t["ui"], t["ansi"]
    names = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")
    theme = {"background": c["bg"], "foreground": c["fg"], "cursor": c["fg"],
             "cursorAccent": c["bg"], "selectionBackground": c["selection"],
             "selectionForeground": c["fg"]}
    for i, n in enumerate(names):
        theme[n] = a[i]
        theme["bright" + n[0].upper() + n[1:]] = a[8 + i]
    return theme


def terminal_theme_js() -> str:
    return (f"window.__cortexSetTerminalTheme && "
            f"window.__cortexSetTerminalTheme({json.dumps(terminal_theme())});")


# ── live change ─────────────────────────────────────────────────────────────

def on_color_theme_changed(callback) -> None:
    if callback not in _listeners:
        _listeners.append(callback)


def remove_listener(callback) -> None:
    try:
        _listeners.remove(callback)
    except ValueError:
        pass


def notify_color_theme_changed() -> None:
    """Settings changed the colour theme: restyle the live surfaces now."""
    tid = color_theme()
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWidgets import QApplication
        install_web_theme()
        js = _apply_js(tid)
        n = 0
        for w in QApplication.allWidgets():
            if isinstance(w, QWebEngineView) and w.page() is not None:
                w.page().runJavaScript(js)
                n += 1
        log.info("[ColorTheme] %s -> %d web views", tid, n)
    except Exception as exc:
        log.warning("[ColorTheme] web pages not restyled: %s", exc)
    for cb in list(_listeners):
        try:
            cb()
        except Exception as exc:
            log.debug("[ColorTheme] listener failed: %s", exc)
    try:
        from src.ui.editor_theme import notify_editor_theme_changed
        notify_editor_theme_changed()      # editor "auto" follows this theme
    except Exception:
        pass
