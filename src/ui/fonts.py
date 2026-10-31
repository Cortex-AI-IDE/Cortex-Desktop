"""Cortex's bundled typefaces: Inter for the interface, JetBrains Mono for code.

Before this module Cortex shipped no font files. The design tokens named
Geist and JetBrains Mono, neither was installed, and every surface silently
fell back to Segoe UI and Consolas. The fonts now live in src/assets/fonts/
(SIL Open Font License 1.1, licence texts alongside) and reach both halves of
the UI from here:

* Native Qt widgets (the chat panel, menus, dialogs) through
  QFontDatabase.addApplicationFont - see register_qt_fonts().
* Every Qt WebEngine page (editor, sidebar, terminal, Settings, Project
  Health, diagrams) through one script on the shared default profile that adds
  @font-face rules and the --font-ui / --font-mono variables - see
  install_web_fonts(). Chromium does not see fonts registered with Qt, so the
  pages need their own @font-face.

Naming, measured on Windows: Qt registers the variable Inter file as
"Inter Variable" - a request for plain "Inter" resolves to Tahoma. The stacks
below therefore list both: "Inter" is the name the @font-face rules give it
inside web pages, "Inter Variable" is the name Qt knows.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from src.utils.logger import get_logger

log = get_logger("fonts")

UI_FAMILY = "Inter"
MONO_FAMILY = "JetBrains Mono"

# Full fallback stacks, CSS syntax. Used by tokens.py and the web pages.
UI_STACK = ("'Inter', 'Inter Variable', 'Segoe UI Variable Text', 'Segoe UI', "
            "system-ui, -apple-system, sans-serif")
MONO_STACK = ("'JetBrains Mono', 'Cascadia Code', 'Fira Code', Consolas, "
              "'Courier New', monospace")

# The code fonts offered in Settings -> Editor -> Font family. Values are the
# family names themselves, so the saved setting and the option always match.
EDITOR_FONT_CHOICES = ("JetBrains Mono", "Cascadia Code", "Fira Code", "Consolas")
DEFAULT_EDITOR_FONT = "JetBrains Mono"

_FILES = {
    "InterVariable.ttf": (UI_FAMILY, "100 900", "normal"),
    "JetBrainsMono-Regular.ttf": (MONO_FAMILY, "400", "normal"),
    "JetBrainsMono-Bold.ttf": (MONO_FAMILY, "700", "normal"),
}

_registered = False
_web_installed = False
_mono_stack = MONO_STACK      # the code font in use (Editor -> Font family)


def fonts_dir() -> Path:
    """src/assets/fonts, from source or from the PyInstaller bundle."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "src" / "assets" / "fonts"
    return Path(__file__).resolve().parent.parent / "assets" / "fonts"


def register_qt_fonts() -> list:
    """Register the bundled fonts with Qt. Call once, after QApplication.

    Returns the family names Qt registered (empty when the files are missing,
    in which case every widget keeps its fallback font and nothing breaks).
    """
    global _registered
    if _registered:
        return []
    from PyQt6.QtGui import QFontDatabase

    families = []
    base = fonts_dir()
    for name in _FILES:
        path = base / name
        if not path.is_file():
            log.warning("[Fonts] missing bundled font %s", path)
            continue
        fid = QFontDatabase.addApplicationFont(str(path))
        if fid < 0:
            log.warning("[Fonts] Qt could not load %s", path)
            continue
        for fam in QFontDatabase.applicationFontFamilies(fid):
            if fam not in families:
                families.append(fam)
    # Widgets that ask for a family by name: "Inter" (several do) is not a
    # name Qt knows - it registers the file as "Inter Variable" - and
    # "Geist" was named in the code but never shipped. Both fell back to
    # Tahoma / Segoe UI. A substitution is only consulted when the requested
    # family is missing, so it cannot override a font that exists.
    if "Inter Variable" in families:
        from PyQt6.QtGui import QFont
        for alias in ("Inter", "Geist"):
            QFont.insertSubstitution(alias, "Inter Variable")
    if MONO_FAMILY in families:
        from PyQt6.QtGui import QFont
        QFont.insertSubstitution("Geist Mono", MONO_FAMILY)
    _registered = True
    log.info("[Fonts] registered with Qt: %s", families[:4])
    return families


# ── Editor (Monaco) font: Settings -> Editor -> Font family / Font size ──────
#
# Those two controls were saved to settings.json but never reached Monaco,
# whose font was hard-coded in editor.html - they only ever applied to an
# older Qt editor. The editor panel now reads them when Monaco is ready and
# listens here for changes made while it is open.

_editor_font_listeners: list = []
_terminal_font_listeners: list = []

# Fixed-pitch families Windows reports that are not code fonts: bitmap system
# fonts, OCR faces and CJK fonts whose Latin glyphs are an afterthought.
_NOT_CODE_FONTS = {"8514oem", "fixedsys", "terminal", "system", "courier",
                   "modern", "small fonts", "ocr a", "ocr a extended",
                   "ms gothic", "ms mincho", "nsimsun", "simsun-extb",
                   "simsun-extg", "mingliu-extb", "mingliu_hkscs-extb"}
_WEIGHT_SUFFIXES = ("thin", "extralight", "ultralight", "light", "semilight",
                    "regular", "medium", "semibold", "demibold", "bold",
                    "extrabold", "ultrabold", "black", "heavy")


def installed_code_fonts() -> list:
    """Code fonts that are actually installed, JetBrains Mono first.

    Only base families: "Cascadia Code SemiBold" is dropped when
    "Cascadia Code" is there. Empty when Qt is not running.
    """
    try:
        from PyQt6.QtGui import QFontDatabase, QGuiApplication
        if QGuiApplication.instance() is None:
            return []
        fixed = [f for f in QFontDatabase.families()
                 if not f.startswith("@") and QFontDatabase.isFixedPitch(f)]
    except Exception:
        return []
    have = {f.lower() for f in fixed}
    out = []
    for fam in fixed:
        low = fam.lower()
        if low in _NOT_CODE_FONTS:
            continue
        head, _, last = low.rpartition(" ")
        if head and last in _WEIGHT_SUFFIXES and head in have:
            continue
        if fam not in out:
            out.append(fam)
    out.sort(key=str.lower)
    if DEFAULT_EDITOR_FONT in out:
        out.remove(DEFAULT_EDITOR_FONT)
    return [DEFAULT_EDITOR_FONT] + out


def _saved_code_font(section: str) -> tuple:
    """(family, size) saved under `section`, sanitised.

    A saved value that is not an installed code font (older builds saved
    whole CSS stacks, or slugs like "jetbrains"; or the font was uninstalled)
    falls back to the default instead of handing the page something it
    cannot use.
    """
    default_size = 14 if section == "editor" else 13
    family, size = DEFAULT_EDITOR_FONT, default_size
    try:
        from src.config.settings import get_settings
        s = get_settings()
        raw = str(s.get(section, "font_family", default="") or "").strip().strip("'\"")
        slugs = {"jetbrains": "JetBrains Mono", "cascadia": "Cascadia Code",
                 "fira": "Fira Code", "consolas": "Consolas"}
        raw = slugs.get(raw.lower(), raw)
        installed = installed_code_fonts()
        if raw and "," not in raw and (raw in installed
                                       or (not installed and raw in EDITOR_FONT_CHOICES)):
            family = raw
        size = int(s.get(section, "font_size", default=default_size) or default_size)
    except Exception:
        pass  # silent-ok: keeps the caller's defaults when the setting cannot be read
    return family, max(8, min(40, size))


def editor_font_settings() -> tuple:
    """(family, size_px) for the editor, from settings, sanitised."""
    return _saved_code_font("editor")


def terminal_font_settings() -> tuple:
    """(family, size_px) for the terminal, from settings, sanitised."""
    return _saved_code_font("terminal")


def editor_font_stack(family: str) -> str:
    """The chosen family first, then the bundled one, then system fallbacks."""
    stack = [family, "JetBrains Mono", "Cascadia Code", "Consolas"]
    seen, out = set(), []
    for f in stack:
        if f.lower() not in seen:
            seen.add(f.lower())
            out.append(f"'{f}'")
    return ", ".join(out) + ", monospace"


def editor_font_js() -> str:
    """JS that applies the saved editor font to every Monaco instance."""
    import json
    family, size = editor_font_settings()
    return (f"window.__cortexSetEditorFont && window.__cortexSetEditorFont("
            f"{json.dumps(editor_font_stack(family))}, {int(size)}, {json.dumps(family)});")


def on_editor_font_changed(callback) -> None:
    """Register a callable to run when the editor font setting changes."""
    if callback not in _editor_font_listeners:
        _editor_font_listeners.append(callback)


def code_font_stack() -> str:
    """The code-font stack for all of Cortex, from Editor -> Font family."""
    family, _ = editor_font_settings()
    return MONO_STACK if family == DEFAULT_EDITOR_FONT else editor_font_stack(family)


def apply_code_font_at_startup() -> None:
    """Point the font_mono token at the user's code font.

    Call after register_qt_fonts() and before any widget or web page is
    built, so everything starts in the chosen font.
    """
    global _mono_stack
    from src.ui.tokens import set_code_font
    _mono_stack = code_font_stack()
    set_code_font(_mono_stack)
    log.info("[Fonts] code font: %s", _mono_stack.split(",")[0])


def _apply_code_font_live() -> None:
    """Editor -> Font family changed: restyle code text across Cortex now."""
    global _mono_stack, _web_installed
    old, new = _mono_stack, code_font_stack()
    if new == old:
        return
    from src.ui.tokens import set_code_font
    set_code_font(new)
    _mono_stack = new
    # Web pages (Settings, sidebar, Project Health, editor chrome, diagrams):
    # replace the script for pages loaded later, then rewrite the
    # #cortex-fonts style in the open ones. Only Cortex's own pages carry
    # that element, so the user's site in Live Preview is untouched.
    try:
        import json
        from PyQt6.QtWebEngineCore import QWebEngineProfile
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWidgets import QApplication
        scripts = QWebEngineProfile.defaultProfile().scripts()
        for s in scripts.find("cortex-fonts"):
            scripts.remove(s)
        _web_installed = False
        install_web_fonts()
        js = ("(function(){var s=document.getElementById('cortex-fonts');"
              f"if(s)s.textContent={json.dumps(font_face_css())};}})();")
        pages = 0
        for w in QApplication.allWidgets():
            if isinstance(w, QWebEngineView) and w.page() is not None:
                w.page().runJavaScript(js)
                pages += 1
    except Exception as exc:
        pages = -1
        log.warning("[Fonts] web pages not restyled: %s", exc)
    n_sheet, n_doc, n_font = _restyle_native(old, new)
    log.info("[Fonts] code font -> %s: %d web views, %d stylesheets, "
             "%d text runs, %d widget fonts", new.split(",")[0], pages,
             n_sheet, n_doc, n_font)


def _restyle_native(old: str, new: str) -> tuple:
    """Swap the code font in native widgets already on screen."""
    try:
        from PyQt6.QtWidgets import QApplication, QTextEdit, QWidget
        from PyQt6.QtCore import Qt
        from src.ui.tokens import family_list
    except Exception:
        return 0, 0, 0
    app = QApplication.instance()
    if app is None:
        return 0, 0, 0
    old_primary = family_list(old)[0].lower()
    new_fams = family_list(new)
    n_sheet = n_doc = n_font = 0
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss and old in ss:
                # Restyling a container re-polishes everything under it - the
                # cost behind the theme-switch freeze. Code-font sheets sit
                # on small leaf widgets; leave anything big alone.
                if w.isWindow() or len(w.findChildren(QWidget)) > 40:
                    log.debug("[Fonts] skipped container %s", type(w).__name__)
                else:
                    w.setStyleSheet(ss.replace(old, new))
                    n_sheet += 1
            elif w.testAttribute(Qt.WidgetAttribute.WA_SetFont):
                fams = w.font().families()
                if fams and fams[0].lower() == old_primary:
                    f = w.font()
                    f.setFamilies(new_fams)
                    w.setFont(f)
                    n_font += 1
            if isinstance(w, QTextEdit):
                n_doc += _refont_document(w.document(), old_primary, new_fams)
        except RuntimeError:
            continue  # silent-ok: the widget was deleted mid-walk
    return n_sheet, n_doc, n_font


def _refont_document(doc, old_primary: str, new_fams: list) -> int:
    """Rendered chat text keeps the font it was rendered with: code spans
    and blocks carry it inline. Re-font those runs in place."""
    from PyQt6.QtGui import QTextCharFormat, QTextCursor
    runs = []
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                fams = frag.charFormat().fontFamilies() or []
                if fams and str(fams[0]).lower() == old_primary:
                    runs.append((frag.position(), frag.length()))
            it += 1
        block = block.next()
    if not runs:
        return 0
    fmt = QTextCharFormat()
    fmt.setFontFamilies(new_fams)
    cur = QTextCursor(doc)
    cur.beginEditBlock()
    for pos, length in runs:
        cur.setPosition(pos)
        cur.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
        cur.mergeCharFormat(fmt)
    cur.endEditBlock()
    return len(runs)


def notify_editor_font_changed() -> None:
    try:
        _apply_code_font_live()
    except Exception as exc:
        log.warning("[Fonts] code font not applied live: %s", exc)
    for cb in list(_editor_font_listeners):
        try:
            cb()
        except Exception as exc:
            log.debug("[Fonts] editor font listener failed: %s", exc)


def terminal_font_js() -> str:
    """JS that applies the saved terminal font to xterm."""
    import json
    family, size = terminal_font_settings()
    return (f"window.__cortexSetTerminalFont && window.__cortexSetTerminalFont("
            f"{json.dumps(editor_font_stack(family))}, {int(size)}, {json.dumps(family)});")


def on_terminal_font_changed(callback) -> None:
    """Register a callable to run when the terminal font setting changes."""
    if callback not in _terminal_font_listeners:
        _terminal_font_listeners.append(callback)


def remove_terminal_font_listener(callback) -> None:
    try:
        _terminal_font_listeners.remove(callback)
    except ValueError:
        pass  # silent-ok: removing a listener that is not registered is harmless


def notify_terminal_font_changed() -> None:
    for cb in list(_terminal_font_listeners):
        try:
            cb()
        except Exception as exc:
            log.debug("[Fonts] terminal font listener failed: %s", exc)


def ui_font(point_size: float = 10, weight=None):
    """A QFont on the interface stack - for code that sets fonts directly."""
    from PyQt6.QtGui import QFont
    from src.ui.tokens import TOKENS as T, family_list
    f = QFont()
    f.setFamilies(family_list(T["font_ui"]))
    f.setPointSizeF(float(point_size))
    if weight is not None:
        f.setWeight(weight)
    return f


def mono_font(point_size: float = 10, weight=None):
    """A QFont on the code stack, with a monospace style hint."""
    from PyQt6.QtGui import QFont
    from src.ui.tokens import TOKENS as T, family_list
    f = QFont()
    f.setFamilies(family_list(T["font_mono"]))
    f.setPointSizeF(float(point_size))
    f.setStyleHint(QFont.StyleHint.Monospace)
    if weight is not None:
        f.setWeight(weight)
    return f


def font_face_css() -> str:
    """@font-face rules plus the two family variables, for web pages."""
    base = fonts_dir()
    rules = []
    for name, (family, weight, style) in _FILES.items():
        url = (base / name).as_uri()
        rules.append(
            f'@font-face{{font-family:"{family}";src:url("{url}") format("truetype");'
            f"font-weight:{weight};font-style:{style};font-display:swap;}}"
        )
    # !important: each page also defines these variables in its own :root,
    # and that rule comes later in the document than this injected one.
    rules.append(
        f":root{{--font-ui:{UI_STACK} !important;--font-mono:{_mono_stack} !important;}}"
    )
    return "".join(rules)


def _own_page_prefixes() -> list:
    """file:// URL prefixes of Cortex's own pages - nothing else is touched.

    The Live Preview panel shows the user's own website in the same browser
    profile; their CSS variables and fonts must stay theirs.
    """
    import tempfile
    roots = [
        fonts_dir().parent.parent,                     # <bundle>/src
        # Same folder the editor and terminal panels resolve their pages into.
        Path(tempfile.gettempdir()) / "cortex_webview",
    ]
    out = []
    for r in roots:
        try:
            if str(r).strip():
                out.append(r.resolve().as_uri().lower())
        except Exception:
            continue  # silent-ok: a path that cannot be resolved to a URI is skipped
    return out


def install_web_fonts() -> bool:
    """Add the fonts to every Cortex page, through the shared profile.

    All of Cortex's QWebEngineViews use QWebEngineProfile.defaultProfile(),
    so a single script there reaches the editor, sidebar, terminal, Settings,
    Project Health and the chat's diagram cards - including pages created
    later. It runs at DocumentCreation, before the page's own CSS, so text
    never paints in the fallback font first.
    """
    global _web_installed
    if _web_installed:
        return True
    try:
        import json
        from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript

        css = font_face_css()
        prefixes = _own_page_prefixes()
        js = (
            "(function(){try{"
            f"var allow={json.dumps(prefixes)};"
            "var href=(location.href||'').toLowerCase();"
            "if(href.indexOf('file:')!==0)return;"
            "if(!allow.some(function(p){return href.indexOf(p)===0;}))return;"
            f"var css={json.dumps(css)};"
            # At DocumentCreation the document is still empty: no <html> yet.
            # Insert as soon as the root element exists - before any text is
            # painted - instead of failing quietly on a missing node.
            "function put(){if(document.getElementById('cortex-fonts'))return true;"
            "var root=document.head||document.documentElement;if(!root)return false;"
            "var s=document.createElement('style');s.id='cortex-fonts';"
            "s.textContent=css;root.appendChild(s);return true;}"
            "if(!put()){var mo=new MutationObserver(function(){if(put())mo.disconnect();});"
            "mo.observe(document,{childList:true,subtree:true});"
            "document.addEventListener('DOMContentLoaded',function(){put();mo.disconnect();});}"
            "}catch(e){}})();"
        )
        script = QWebEngineScript()
        script.setName("cortex-fonts")
        script.setSourceCode(js)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        QWebEngineProfile.defaultProfile().scripts().insert(script)
        _web_installed = True
        log.info("[Fonts] web pages: @font-face installed on the shared profile")
        return True
    except Exception as exc:
        log.warning("[Fonts] could not install web fonts: %s", exc)
        return False
