"""Detect WebEngine panels that load but never paint, and offer the fix.

Why this exists
---------------
On some machines (older office PCs with old Intel drivers are the classic
case) Chromium cannot obtain a GPU context. Every WebEngine surface, the
sidebar, editor, terminal and settings pages, comes up BLANK/dark while the
rest of the app behaves normally and the logs look healthy: pages report
loadFinished, bridges connect, nothing raises. Only the pixels are missing.

Cortex ships a software-rendering fallback for exactly this, but it is
opt-in, and a user staring at a blank panel has no way to know it exists.
Worse, the owner cannot reasonably test every affected machine.

So the app checks itself: once, a few seconds after startup, it grabs the
WebEngine widgets and asks whether they actually painted anything. If they
did not, it says so in plain language and offers to switch on compatibility
rendering and restart. Native Qt dialogs do not use WebEngine, so the
prompt is visible even when every web panel is blank.

Deliberately conservative
-------------------------
- Runs once per launch, never in a loop.
- Requires EVERY checked widget to be blank. One blank panel can be
  legitimate (an empty editor, a page mid-load); all of them blank at once
  is the failure signature.
- Skips entirely when software rendering is already on.
- Never switches silently: a false positive would only cost speed, but the
  user still decides.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from src.utils.logger import get_logger

log = get_logger("render_health")

# A painted UI has many distinct colours. A failed render is one flat fill.
# 3 keeps a legitimately plain dark panel (background + border + text
# antialiasing) from being called blank.
_MIN_DISTINCT_COLOURS = 3
_SAMPLE_STEP = 11          # px between samples; prime-ish to avoid aligning
                           # with regular UI grids
_MIN_SIZE = 40             # ignore widgets too small to judge
_SCREEN_ENOUGH_COLOURS = 64   # plenty to say a panel painted; stop reading


# Failures Chromium itself reported. Nothing else is allowed to raise the
# compatibility-mode dialog.
_failures: List[str] = []

# ──────────────────────────────────────────────────────────────────────────
# Silent-failure probe. The failure this module was written for does NOT
# fire renderProcessTerminated or loadFinished(False): on the affected
# machines (older AMD-chipset Windows 10/11 PCs are the classic case) the
# pages load, the bridges connect, the logs look healthy — only the pixels
# are missing, because Chromium never obtained a GPU context. So a few
# seconds after startup we ask the pages themselves whether Chromium could
# create one: a WebGL context needs exactly the GPU context compositing
# needs. Two or more core surfaces reporting "no WebGL" while we are in
# GPU mode is that silent signature, and the fix is offered.
# ──────────────────────────────────────────────────────────────────────────
GPU_PROBE_JS = """(function(){
  try {
    var out = {webgl: false, renderer: ''};
    var c = document.createElement('canvas');
    c.width = 16; c.height = 16;
    var gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    if (gl) {
      out.webgl = true;
      // gl.RENDERER is always the masked "WebKit WebGL". The real name is what
      // tells a report whether this was the driver or a fallback.
      try {
        var dbg = gl.getExtension('WEBGL_debug_renderer_info');
        out.renderer = String(gl.getParameter(
          dbg ? dbg.UNMASKED_RENDERER_WEBGL : gl.RENDERER) || '');
      } catch (e2) {}
    }
    return JSON.stringify(out);
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()"""

# source -> parsed probe result dict
_probes: dict = {}
_gpu_report_started = False


_GPU_REPORT_TEXT_JS = """(function(){
  var out = [];
  (function walk(root){
    var kids = root.children || [];
    for (var i = 0; i < kids.length; i++) {
      var el = kids[i];
      if (el.shadowRoot) { walk(el.shadowRoot); }
      else if (!el.children.length) {
        var t = (el.textContent || '').trim();
        if (t) out.push(t);
      }
      walk(el);
    }
  })(document.documentElement);
  return out.join('\\n');
})()"""


def record_render_failure(source: str, reason: str) -> None:
    """Note that Chromium failed to render a panel.

    Called from the WebEngine views' renderProcessTerminated and
    loadFinished(False) handlers. These are Qt telling us the render
    process died or the page never loaded, which is the actual failure on
    the machines this exists for: a GPU/driver incompatibility kills the
    render process and the panel is left empty.
    """
    entry = f"{source}: {reason}"
    _failures.append(entry)
    log.error("[RenderHealth] render failure reported, %s", entry)





def probe_page(page, source: str) -> None:
    """Ask a freshly loaded WebEngine page whether Chromium got a GPU context.

    Hooked from the loadFinished(True) handlers of the core surfaces
    (sidebar, editor, terminal, memory manager). Runs in compatibility mode
    too, for the log only: should_offer_from_probes() ignores it there.
    """
    try:
        if source in _probes:
            return                       # one probe per surface per launch
        page.runJavaScript(
            GPU_PROBE_JS,
            lambda result, src=source: _record_probe(src, result))
    except Exception as exc:
        log.debug("[RenderHealth] probe skipped for %s: %s", source, exc)


def _record_probe(source: str, result) -> None:
    """Store a probe result. Runs on the GUI thread (runJavaScript callback)."""
    import json as _json
    try:
        parsed = _json.loads(result) if isinstance(result, str) else (result or {})
    except Exception:
        parsed = {"error": "unparseable probe result"}
    _probes[source] = parsed
    if parsed.get("webgl"):
        log.info("[RenderHealth] GPU probe OK on %s (renderer: %s)",
                 source, parsed.get("renderer") or "?")
    elif _already_software():
        log.info("[RenderHealth] no WebGL on %s, expected in compatibility mode",
                 source)
    else:
        log.warning("[RenderHealth] GPU probe found NO WebGL context on %s (%s)",
                    source, parsed.get("renderer") or parsed.get("error") or "no context")


def probe_failures() -> List[str]:
    """Sources whose page could not obtain a WebGL (=> GPU) context."""
    return [src for src, res in _probes.items() if not res.get("webgl")]


def should_offer_from_probes(min_failures: int = 2) -> bool:
    """Pure decision: is the silent blank-panel signature present?

    Requires GPU mode AND at least *min_failures* distinct surfaces without
    a GPU context. One blank surface can be legitimate; all core surfaces
    losing the context at once is the driver failure this module exists for.
    """
    try:
        from src.core import gpu_compat
        if gpu_compat.current_mode() == 'software':
            return False                 # already fixed, nothing to offer
    except Exception:
        pass  # silent-ok: without gpu_compat the failure count alone decides
    return len(probe_failures()) >= min_failures


def widget_is_blank(widget) -> Optional[bool]:
    """True if *widget* rendered as a single flat colour.

    ADVISORY ONLY. This must never be the sole reason to tell a user their
    graphics driver is broken, because it cannot see a QWebEngineView
    reliably: grab() renders through Qt's paint system while the page is
    composited by Chromium, so a perfectly healthy panel can come back as a
    flat colour. It did exactly that on a working machine, reporting every
    panel blank while the file tree was plainly visible on screen, after
    reporting "painting normally" on the same machine the day before.

    Returns None when the widget cannot be judged (too small, not visible,
    grab failed), so callers can tell "no evidence" apart from "blank".
    """
    try:
        if widget is None or not widget.isVisible():
            return None
        if widget.width() < _MIN_SIZE or widget.height() < _MIN_SIZE:
            return None
        img = widget.grab().toImage()
        if img.isNull() or img.width() < _MIN_SIZE:
            return None
        seen = set()
        for x in range(0, img.width(), _SAMPLE_STEP):
            for y in range(0, img.height(), _SAMPLE_STEP):
                seen.add(img.pixelColor(x, y).rgb())
                if len(seen) >= _MIN_DISTINCT_COLOURS:
                    return False       # enough variation: it painted
        return True
    except Exception as exc:
        log.debug("[RenderHealth] could not sample widget: %s", exc)
        return None


def screen_colours(widget) -> Optional[Dict]:
    """What the screen really shows where *widget* sits.

    Reads the desktop, not the widget. That is what widget_is_blank() cannot
    do: grab() goes through Qt's paint system and misses what Chromium
    composites, while this sees the same pixels the user does, so a panel
    that loaded and never painted is caught in either render mode.

    Returns {"colours": n, "sample": "#rrggbb"}, or None when it cannot be
    read (hidden, too small, or a platform that refuses, such as Wayland).
    """
    try:
        if widget is None or not widget.isVisible():
            return None
        if widget.width() < _MIN_SIZE or widget.height() < _MIN_SIZE:
            return None
        from PyQt6.QtCore import QPoint
        screen = widget.screen()
        origin = widget.mapToGlobal(QPoint(0, 0)) - screen.geometry().topLeft()
        img = screen.grabWindow(0, origin.x(), origin.y(),
                                widget.width(), widget.height()).toImage()
        if img.isNull() or img.width() < _MIN_SIZE:
            return None
        seen = set()
        for x in range(0, img.width(), _SAMPLE_STEP):
            for y in range(0, img.height(), _SAMPLE_STEP):
                seen.add(img.pixelColor(x, y).rgb() & 0xffffff)
            if len(seen) >= _SCREEN_ENOUGH_COLOURS:
                break
        return {"colours": len(seen), "sample": "#%06x" % min(seen)}
    except Exception as exc:
        log.debug("[RenderHealth] could not read the screen: %s", exc)
        return None


def log_screen_check(panels: Dict[str, object], in_front: bool) -> Optional[bool]:
    """Write down whether each panel really reached the screen.

    Log only, never a dialog: another window can cover a panel, and this has
    not yet been proved on enough machines to act on. Returns True when
    every panel that could be read was flat with Cortex in front.
    """
    from src.core import gpu_compat
    readings = {name: screen_colours(w) for name, w in panels.items()}
    shown = ", ".join(
        f"{name}={r['colours']} colour(s) {r['sample']}" if r else f"{name}=unreadable"
        for name, r in readings.items())
    known = [r for r in readings.values() if r]
    mode = gpu_compat.current_mode()
    if not known:
        log.info("[RenderHealth] on-screen check unavailable (mode=%s): %s", mode, shown)
        return None
    blank = all(r["colours"] < _MIN_DISTINCT_COLOURS for r in known)
    if blank and in_front:
        log.error("[RenderHealth] PANELS BLANK ON SCREEN (mode=%s): %s", mode, shown)
    else:
        log.info("[RenderHealth] on-screen check (mode=%s, in front=%s): %s",
                 mode, in_front, shown)
    return blank and in_front


def dump_gpu_report(parent) -> None:
    """With CORTEX_RENDER_DEBUG=1, save chrome://gpu to the logs folder.

    Chromium's own GPU errors are silenced by --log-level=3, and this page is
    the record of what it tried, what it fell back to and why.
    """
    global _gpu_report_started
    if _gpu_report_started or not os.environ.get("CORTEX_RENDER_DEBUG"):
        return
    _gpu_report_started = True
    from PyQt6.QtCore import QTimer, QUrl
    from PyQt6.QtWebEngineCore import QWebEnginePage
    page = QWebEnginePage(parent)
    target = Path.home() / ".cortex" / "logs" / "chromium_gpu.txt"

    def _save(text):
        try:
            target.write_text(text or "", encoding="utf-8")
            log.info("[RenderHealth] chrome://gpu saved to %s (%d chars)",
                     target, len(text or ""))
        except OSError as exc:
            log.warning("[RenderHealth] could not save chrome://gpu: %s", exc)
        page.deleteLater()

    # The page fills itself in from script after it reports loaded, inside
    # shadow roots that toPlainText() cannot see.
    page.loadFinished.connect(lambda _ok: QTimer.singleShot(
        3000, lambda: page.runJavaScript(_GPU_REPORT_TEXT_JS, _save)))
    page.load(QUrl("chrome://gpu"))


def diagnose(widgets: Iterable) -> Optional[bool]:
    """True if every judgeable widget is blank, None if nothing was judgeable."""
    verdicts = [v for v in (widget_is_blank(w) for w in widgets) if v is not None]
    if not verdicts:
        return None
    return all(verdicts)


def enable_software_rendering() -> bool:
    """Persist the compatibility switch. Takes effect on the next launch.

    Writes both the legacy boolean (kept so older code paths and existing
    installs keep working) and the explicit three-state rendering_mode.
    """
    try:
        from src.config.settings import get_settings
        settings = get_settings()
        settings.set("ui", "software_rendering", True)
        try:
            settings.set("ui", "rendering_mode", "software")
        except Exception:
            pass   # silent-ok: the legacy bool alone is enough for main.py
        log.info("[RenderHealth] software rendering enabled for next launch")
        return True
    except Exception as exc:
        log.error("[RenderHealth] could not save the setting: %s", exc)
        return False


def _already_software() -> bool:
    """True when compatibility rendering is already active or configured."""
    try:
        from src.core import gpu_compat
        if gpu_compat.current_mode() == 'software':
            return True
    except Exception:
        pass  # silent-ok: falls through to the setting check below
    try:
        from src.config.settings import get_settings
        if get_settings().get("ui", "software_rendering", default=False):
            return True
        if str(get_settings().get("ui", "rendering_mode", default="") or "").lower() == "software":
            return True
    except Exception:
        pass  # silent-ok: no settings read means software rendering is not configured
    return False


def _offer_compat_mode(parent, evidence: str) -> bool:
    """Show the native dialog, persist the switch, restart. Shared by every
    detection path. Native Qt widgets do not use WebEngine, so this dialog
    is visible even when every web panel is blank."""
    try:
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Display problem detected")
        box.setText("Cortex cannot draw its panels on this computer.")
        box.setInformativeText(
            "The file explorer, editor and terminal are blank because this "
            "computer's graphics driver cannot render them.\n\n"
            f"Evidence: {evidence}\n\n"
            "Compatibility mode draws them using the processor instead. It is "
            "slightly slower, but it works on any machine.\n\n"
            "Turn on compatibility mode and restart Cortex?")
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        if box.exec() != QMessageBox.StandardButton.Yes:
            log.info("[RenderHealth] user declined compatibility mode")
            return False
    except Exception as exc:
        log.error("[RenderHealth] could not show the prompt: %s", exc)
        return False

    if not enable_software_rendering():
        return False

    try:
        from src.core.app_restart import restart_ide
        restart_ide(reason="enabling software rendering")
    except Exception as exc:
        log.warning("[RenderHealth] automatic restart failed (%s); the setting "
                    "is saved and applies next launch", exc)
    return True


def check_and_offer_fix(parent, widgets: Iterable) -> bool:
    """Crash-driven path: offer the fix when Chromium itself reported a
    render failure. Returns True when the user accepted and it was saved.
    """
    if _already_software():
        return False           # already on; nothing to offer

    # Chromium must have ACTUALLY reported a failure before we do anything.
    #
    # The pixel sample is NOT run in the healthy case any more. widget.grab()
    # on a QWebEngineView forces a synchronous surface read on the GUI thread,
    # and the watchdog caught it freezing startup for 10.7s:
    #   _check_render_health -> check_and_offer_fix -> widget_is_blank
    #     -> widget.grab().toImage()
    # It was only ever an advisory log line, and it was never worth freezing
    # the app to write it. Nothing failed, so there is nothing to check.
    if not _failures:
        log.info("[RenderHealth] no render-process failure reported")
        return False

    evidence = (f"Chromium reported {len(_failures)} render failure(s): "
                + "; ".join(_failures[:4]))
    log.error("[RenderHealth] %s. This is a GPU/driver incompatibility; "
              "offering software rendering.", evidence)
    return _offer_compat_mode(parent, evidence)


def evaluate_probes(parent) -> bool:
    """Silent-failure path: offer the fix when the GPU probes show that the
    core surfaces never obtained a GPU context, even though nothing crashed.

    This is the path that fires on the affected machines (older AMD-chipset
    Windows 10/11 PCs): loadFinished(True), bridges connected, blank pixels.
    Returns True when the user accepted and the switch was saved.
    """
    if _already_software():
        return False
    if not should_offer_from_probes():
        if _probes:
            log.info("[RenderHealth] GPU probes healthy (%d surface(s) answered, "
                     "failures: %s)", len(_probes), probe_failures() or "none")
        return False

    fails = probe_failures()
    evidence = (f"{len(fails)} core panels loaded but could not create a "
                f"graphics context ({', '.join(fails)})")
    log.error("[RenderHealth] silent blank-panel signature detected: %s. "
              "Offering software rendering.", evidence)
    return _offer_compat_mode(parent, evidence)
