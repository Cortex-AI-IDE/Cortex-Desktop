"""GPU compatibility detection for the WebEngine software-rendering fallback.

Why this exists
---------------
On some older machines (Windows 10/11 office PCs, AMD chipsets are the
classic case) Chromium cannot obtain a usable GPU context. Every WebEngine
surface — sidebar.html, editor.html, terminal.html, memory_management.html,
settings — paints BLANK/dark while the Python side looks perfectly healthy:
pages report loadFinished, bridges connect, nothing raises. Only the pixels
are missing.

The fix is SwiftShader (CPU rendering), but it must be decided BEFORE
QApplication exists, because QTWEBENGINE_CHROMIUM_FLAGS is read once at
WebEngine initialisation. So this module is Qt-free: main.py imports it
before any PyQt6 import. Never add a Qt import here.

Decision order (first match wins):
  1. CORTEX_SOFTWARE_RENDERING=1 in the environment   -> software
  2. settings.json ui.rendering_mode = software|gpu   -> explicit user choice
  3. legacy settings.json ui.software_rendering=true  -> software
  3b. a crash on a native (Chromium GPU) thread in    -> software, this
      the last 7 days (crash_fallback)                    launch; the window
                                                          then asks to keep it
  4. Automatic hardware detection:
       - ONLY Microsoft Basic Display/Render adapters  -> software
         (no real driver installed, GPU rendering is guaranteed to fail)
       - AMD/ATI adapter with a driver older than 2017 -> software
         (the driver cohort reported blank on; modern AMD drivers are fine)
       - only Intel adapters, driver older than 2017   -> software
         (same failure, see classify_gpus)
  5. Default                                          -> gpu

Anything uncertain stays on GPU: the runtime WebGL probe in
src/ui/render_health.py catches machines this heuristic cannot see, and
offers the same fix with evidence instead of guessing.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
from typing import Dict, List, Optional, Tuple

# Windows display-adapter device class key. Each numeric subkey (0000,
# 0001, ...) is one adapter with DriverDesc / ProviderName / DriverVersion /
# DriverDate values. Reading it takes milliseconds, no subprocess.
_DISPLAY_CLASS_KEY = (
    r"SYSTEM\CurrentControlSet\Control\Class"
    r"\{4d36e968-e325-11ce-bfc1-08002be10318}"
)

# Drivers from before this year are the cohort reported to render blank.
# Modern Ryzen-APU drivers (2020+) are fine, so date matters, not vendor.
_OLD_DRIVER_CUTOFF_YEAR = 2017

_BASIC_ADAPTER_MARKERS = ("basic display adapter", "basic render adapter")
_AMD_MARKERS = ("amd", "radeon", "ati radeon", "ati mobility", "ati ")
_INTEL_MARKERS = ("intel",)

# Render mode chosen for this process. Set by main.py at startup; read by
# the runtime watchdog to know whether detection still makes sense.
_current_mode: Optional[str] = None
_current_reason: str = ""


def set_current_mode(mode: str, reason: str = "") -> None:
    global _current_mode, _current_reason
    _current_mode = mode
    _current_reason = reason


def current_mode() -> Optional[str]:
    return _current_mode


def current_reason() -> str:
    return _current_reason


# Registry enumeration

def _query_str(dev_key, name: str) -> str:
    """Read a string-ish registry value, tolerating REG_DWORD oddities."""
    try:
        import winreg
        value, _rtype = winreg.QueryValueEx(dev_key, name)
        if value is None:
            return ""
        return str(value).strip()
    except Exception:
        return ""


def _query_raw(dev_key, name: str):
    try:
        import winreg
        value, _rtype = winreg.QueryValueEx(dev_key, name)
        return value
    except Exception:
        return None


def enumerate_gpus() -> List[Dict]:
    """List display adapters from the Windows registry.

    Returns dicts with keys: desc, provider, version, date_raw.
    Empty list on non-Windows or any enumeration failure (callers must
    treat "unknown hardware" as "keep GPU", never as "force software").
    """
    if sys.platform != "win32":
        return []
    gpus: List[Dict] = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS_KEY) as cls:
            index = 0
            while True:
                try:
                    sub = winreg.EnumKey(cls, index)
                except OSError:
                    break
                index += 1
                # Only numeric subkeys are devices; skip "Properties" etc.
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(cls, sub) as dev:
                        desc = _query_str(dev, "DriverDesc")
                        if not desc:
                            continue  # phantom entry
                        gpus.append({
                            "desc": desc,
                            "provider": _query_str(dev, "ProviderName"),
                            "version": _query_str(dev, "DriverVersion"),
                            "date_raw": _query_raw(dev, "DriverDate"),
                        })
                except OSError:
                    continue  # silent-ok: this display adapter could not be queried
    except Exception:
        return []
    return gpus


def parse_driver_date(raw) -> Optional[_dt.datetime]:
    """Parse the registry DriverDate value.

    Usually REG_BINARY holding an 8-byte FILETIME (100-ns ticks since
    1601-01-01); some installs store a locale date string instead.
    Returns None when unparseable — callers treat that as "unknown age".
    """
    if isinstance(raw, (bytes, bytearray)) and len(raw) == 8:
        ticks = int.from_bytes(bytes(raw), "little")
        if ticks <= 0:
            return None
        try:
            return _dt.datetime(1601, 1, 1) + _dt.timedelta(microseconds=ticks // 10)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, str):
        text = raw.strip()
        for fmt in ("%m-%d-%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
            try:
                return _dt.datetime.strptime(text, fmt)
            except ValueError:
                continue  # silent-ok: this format does not match; try the next one
    return None


# Classification

def _is_basic_adapter(gpu: Dict) -> bool:
    desc = gpu.get("desc", "").lower()
    return any(marker in desc for marker in _BASIC_ADAPTER_MARKERS)


def _is_amd_adapter(gpu: Dict) -> bool:
    desc = gpu.get("desc", "").lower()
    return any(marker in desc for marker in _AMD_MARKERS)


def _is_intel_adapter(gpu: Dict) -> bool:
    desc = gpu.get("desc", "").lower()
    return any(marker in desc for marker in _INTEL_MARKERS)


def classify_gpus(gpus: List[Dict]) -> Tuple[str, str]:
    """Decide render mode from the adapter list.

    Returns (mode, reason). Only CERTAIN breakage forces software:
    a false positive costs speed on every launch, a false negative is
    caught later by the runtime probe, so the bar for forcing is high.
    """
    if not gpus:
        return ("gpu", "no adapters enumerated (unknown hardware)")

    real = [g for g in gpus if not _is_basic_adapter(g)]

    # Every adapter is the Microsoft fallback driver => no real GPU driver
    # is installed at all. Chromium cannot composite on it; this is the
    # guaranteed-blank case (common on freshly-imaged older PCs).
    if not real:
        names = "; ".join(g.get("desc", "?") for g in gpus[:3])
        return ("software", f"only Microsoft fallback adapter present ({names})")

    # AMD/ATI with a driver older than the cutoff year. Date must actually
    # parse: unknown age stays on GPU and lets the runtime probe decide.
    for gpu in real:
        if not _is_amd_adapter(gpu):
            continue
        date = parse_driver_date(gpu.get("date_raw"))
        if date is not None and date.year < _OLD_DRIVER_CUTOFF_YEAR:
            return ("software",
                    f"AMD adapter '{gpu.get('desc')}' with driver from "
                    f"{date.year} (before {_OLD_DRIVER_CUTOFF_YEAR})")

    # Intel with an old driver and nothing else to draw on. Seen 2026-10-30 on
    # an OptiPlex 7010 (i5-3470, Intel HD Graphics, driver 10.18.10.4252 from
    # 2015-07): sidebar, editor and terminal black, while the pages loaded,
    # Monaco reported ready and the WebGL probe passed, so the runtime check
    # never fired. Only when Intel is the whole list: a laptop with an old
    # Intel driver and a current NVIDIA card must not lose its GPU.
    if all(_is_intel_adapter(g) for g in real):
        dates = [parse_driver_date(g.get("date_raw")) for g in real]
        if all(d is not None and d.year < _OLD_DRIVER_CUTOFF_YEAR for d in dates):
            return ("software",
                    f"Intel adapter '{real[0].get('desc')}' with driver from "
                    f"{dates[0].year} (before {_OLD_DRIVER_CUTOFF_YEAR})")

    return ("gpu", "no known-broken adapter detected")


def gpu_inventory_summary(gpus: Optional[List[Dict]] = None) -> str:
    """One-line human-readable inventory for the log (remote triage)."""
    if gpus is None:
        gpus = enumerate_gpus()
    if not gpus:
        return "none detected"
    parts = []
    for gpu in gpus[:4]:
        date = parse_driver_date(gpu.get("date_raw"))
        parts.append(
            f"{gpu.get('desc', '?')} "
            f"[provider={gpu.get('provider') or '?'} "
            f"ver={gpu.get('version') or '?'} "
            f"date={date.strftime('%Y-%m') if date else '?'}]"
        )
    return " | ".join(parts)


# Resolution

# Crash-driven fallback
# Qt WebEngine runs Chromium's GPU code on a thread INSIDE Cortex.exe
# ("Chrome_InProcGpuThread"), whatever flags we pass. In Chrome a graphics
# driver crash only kills the GPU process; here it kills the whole app.
# Microsoft Store report, 3.0.73 on Windows 11 + NVIDIA: write access
# violation on Chrome_InProcGpuThread, Qt6WebEngineCore -> d3d11 ->
# nvwgf2umx.dll. crash.log, 3.0.78, 2026-11-21: two access violations four
# minutes apart, both on a thread that was not Python's.
# faulthandler marks the crashing thread "Current thread" when it is a
# Python thread. A fatal native exception with NO "Current thread" in the
# dump happened on a native thread, which in this process means Chromium,
# and almost always its GPU thread. One of those within a week switches the
# next launch to CPU rendering; the window then asks whether to keep it.
# One, not two: waiting for a second crash cost the user their work twice
# (2026-11-21: two crashes four minutes apart). A false alarm only costs
# some speed, and "Use the graphics card again" undoes it.

CRASH_LOG = os.path.join(os.path.expanduser("~"), ".cortex", "crash.log")
CRASH_WINDOW_DAYS = 7
CRASH_THRESHOLD = 1
# Exceptions that end the process. 0x8001010d (a COM call refused during
# input processing) is logged by faulthandler too but is usually handled,
# so it is not counted.
_FATAL_MARKERS = ("access violation", "illegal instruction", "stack overflow",
                  "in page error", "integer divide by zero")
_SESSION_PREFIX = "===== Cortex crash session "


def native_thread_crashes(path: Optional[str] = None, since: Optional[_dt.datetime] = None,
                          tail_bytes: int = 2_000_000) -> List[_dt.datetime]:
    """Start times of earlier sessions that died on a native (non-Python) thread."""
    try:
        with open(path or CRASH_LOG, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - tail_bytes))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    found: List[_dt.datetime] = []
    for block in text.split(_SESSION_PREFIX)[1:]:
        header, _, body = block.partition("\n")
        try:
            started = _dt.datetime.fromisoformat(header.strip().strip("=").strip())
        except ValueError:
            continue
        if since is not None and started <= since:
            continue
        for line_no, line in enumerate(body.splitlines()):
            if line.startswith("Windows fatal exception:") and any(
                    m in line for m in _FATAL_MARKERS):
                after = body.splitlines()[line_no + 1:]
                if not any(l.startswith("Current thread 0x") for l in after):
                    found.append(started)
                break
    return found


def crash_fallback(ui: dict, now: Optional[_dt.datetime] = None) -> Optional[str]:
    """A reason string when recent native crashes call for CPU rendering."""
    now = now or _dt.datetime.now()
    since = now - _dt.timedelta(days=CRASH_WINDOW_DAYS)
    # "Use the graphics card again" in the prompt records when the user
    # chose that; crashes up to that moment are not counted again.
    ack = str(ui.get("gpu_crash_ack") or "").strip()
    if ack:
        try:
            since = max(since, _dt.datetime.fromisoformat(ack))
        except ValueError:
            pass
    crashes = native_thread_crashes(since=since)
    if len(crashes) < CRASH_THRESHOLD:
        return None
    times = "once" if len(crashes) == 1 else f"{len(crashes)} times"
    return (f"crash fallback: Cortex closed {times} since "
            f"{crashes[0]:%Y-%m-%d %H:%M} on a graphics (Chromium) thread")


def resolve_render_mode(settings: Optional[dict] = None) -> Tuple[str, str]:
    """Resolve the render mode for this launch.

    *settings* is the already-loaded settings.json dict (plain json — the
    settings module cannot be imported this early). Returns (mode, reason).
    """
    # 1. Environment override (support/tooling escape hatch).
    env = os.environ.get("CORTEX_SOFTWARE_RENDERING", "").strip().lower()
    if env in ("1", "true", "yes"):
        return ("software", "environment CORTEX_SOFTWARE_RENDERING")

    ui = (settings or {}).get("ui") or {}

    # 2. Explicit user choice via the three-state rendering_mode.
    mode = str(ui.get("rendering_mode") or "").strip().lower()
    if mode == "software":
        return ("software", "user setting ui.rendering_mode=software")
    if mode == "gpu":
        return ("gpu", "user setting ui.rendering_mode=gpu")
    # "auto" or unset falls through to detection.

    # 3. Legacy boolean toggle (what render_health.enable_software_rendering
    #    writes, so the existing fix path keeps working unchanged).
    if bool(ui.get("software_rendering")):
        return ("software", "legacy user setting ui.software_rendering=true")

    # 3b. Recent crashes on Chromium's GPU thread (see crash_fallback).
    try:
        reason = crash_fallback(ui)
    except Exception:
        reason = None
    if reason:
        return ("software", reason)

    # 4. Automatic hardware detection.
    detected, reason = classify_gpus(enumerate_gpus())
    return (detected, f"auto-detect: {reason}")
