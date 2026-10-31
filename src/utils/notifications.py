"""
Desktop notification utility.

Windows: PowerShell + System.Windows.Forms.NotifyIcon.ShowBalloonTip(), which
works on Windows 10/11 with no dependencies or AUMID registration.
Linux: notify-send (libnotify), the freedesktop standard available on GNOME,
KDE, XFCE and every desktop with a notification daemon.
macOS: osascript, which drives Notification Center with no dependencies.

The public functions are identical on every platform, so callers never branch.
"""

import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import Optional

log = logging.getLogger("notifications")

# App identity for toast notifications
APP_ID = "Cortex AI IDE"


def _show_windows_toast(title: str, message: str, timeout_ms: int) -> None:
    """Show a notification via the Windows balloon tip, on a worker thread."""

    def _ps_quote(s: str) -> str:
        """Sanitize text for a PowerShell single-quoted string.

        PowerShell treats the Unicode smart quotes U+2018/U+2019/U+201A/U+201B
        as single-quote DELIMITERS too, AI-generated summaries routinely
        contain U+2019 ("I’m", "it’s"), which the old replace("'", "''")
        missed entirely, so the generated script broke with "Unexpected
        token 'm' in expression" and no notification appeared. Normalize
        every smart-quote variant to a straight quote first, then double.
        """
        for q in ("‘", "’", "‚", "‛"):
            s = s.replace(q, "'")
        return s.replace("'", "''").replace("\r", " ").replace("\n", " ")

    title_safe = _ps_quote(title)
    message_safe = _ps_quote(message)

    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms\n"
        "Add-Type -AssemblyName System.Drawing\n"
        "$n = New-Object System.Windows.Forms.NotifyIcon\n"
        "$n.Icon = [System.Drawing.SystemIcons]::Information\n"
        "$n.Visible = $true\n"
        "$n.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info\n"
        f"$n.BalloonTipTitle = '{title_safe}'\n"
        f"$n.BalloonTipText = '{message_safe}'\n"
        f"$n.ShowBalloonTip({timeout_ms})\n"
        "Start-Sleep -Milliseconds 2500\n"
        "$n.Visible = $false\n"
        "$n.Dispose()\n"
    )

    def _run():
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=20,
                startupinfo=si,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                stderr = (result.stderr or "")[:300]
                log.warning(f"[Notification] BalloonTip exit code {result.returncode}: {stderr}")
            else:
                log.info(f"[Notification] BalloonTip shown: {title}")
        except subprocess.TimeoutExpired:
            log.warning("[Notification] BalloonTip timed out (20s)")
        except Exception as e:
            log.warning(f"[Notification] BalloonTip failed: {e}")

    threading.Thread(target=_run, daemon=True).start()


def _as_quote(s: str) -> str:
    """Sanitize text for an AppleScript double-quoted string.

    AppleScript ends a string at an unescaped double quote and ends the whole
    statement at a newline. The backslash is replaced first, otherwise it
    would escape the escapes added after it and produce broken script.
    """
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _show_macos_notification(title: str, message: str, app_id: str) -> None:
    """Show a notification through Notification Center, on a worker thread.

    `notify-send` is a Linux libnotify tool and does not exist on macOS, so
    this platform needs its own path or every notification is dropped without
    a trace. The AppleScript is passed as its own argv entry, so no shell is
    involved and the title can never be read as shell syntax.

    `app_id` is accepted for signature parity with the Linux path. macOS
    attributes the notification to the calling application and osascript
    offers no per-call override for that.
    """
    if not shutil.which("osascript"):
        log.debug("[Notification] osascript not found, skipping notification")
        return

    script = 'display notification "%s" with title "%s"' % (
        _as_quote(message), _as_quote(title))
    cmd = ["osascript", "-e", script]

    def _run():
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                stderr = (result.stderr or "")[:300]
                log.warning(f"[Notification] osascript exit code {result.returncode}: {stderr}")
            else:
                log.info(f"[Notification] shown: {title}")
        except subprocess.TimeoutExpired:
            log.warning("[Notification] osascript timed out (10s)")
        except Exception as e:
            log.warning(f"[Notification] osascript failed: {e}")

    threading.Thread(target=_run, daemon=True).start()


def _show_posix_notification(title: str, message: str, duration: str, app_id: str) -> None:
    """Show a notification via notify-send, on a worker thread. Linux only."""
    if not shutil.which("notify-send"):
        log.debug("[Notification] notify-send not found, skipping notification")
        return

    cmd = [
        "notify-send",
        "--app-name", app_id,
        "--category", "devices.computer",
    ]
    if duration == "long":
        # Never auto-expire; the daemon decides how to present it.
        cmd += ["--expire-time", "0"]
    else:
        cmd += ["--expire-time", "8000"]
    cmd += [title, message]

    def _run():
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                stderr = (result.stderr or "")[:300]
                log.warning(f"[Notification] notify-send exit code {result.returncode}: {stderr}")
            else:
                log.info(f"[Notification] shown: {title}")
        except subprocess.TimeoutExpired:
            log.warning("[Notification] notify-send timed out (10s)")
        except Exception as e:
            log.warning(f"[Notification] notify-send failed: {e}")

    threading.Thread(target=_run, daemon=True).start()


def show_toast_notification(title: str, message: str, duration: str = "short", app_id: str = APP_ID) -> None:
    """
    Show a desktop notification popup.

    Args:
        title: Notification title (bold, first line)
        message: Notification body text (second line)
        duration: "short" (8s) or "long" (no expiry)
        app_id: Application identifier shown as the sender
    """
    if sys.platform == "win32":
        timeout_ms = 8000 if duration == "short" else 25000
        _show_windows_toast(title, message, timeout_ms)
    elif sys.platform == "darwin":
        _show_macos_notification(title, message, app_id)
    else:
        _show_posix_notification(title, message, duration, app_id)


def show_task_complete_notification(task_summary: str = "Task completed successfully") -> None:
    """Show notification for AI task completion, clean, minimal format."""
    show_toast_notification(
        title="Cortex AI \u2014 Task Complete",
        message=task_summary[:120] if len(task_summary) > 120 else task_summary,
        duration="short",
    )


def _is_ide_focused() -> bool:
    """Check if the Cortex IDE window is currently the active/focused window."""
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return False
        active = app.activeWindow()
        return active is not None
    except Exception:
        return False


def _get_notif_setting(key: str, default: bool = True) -> bool:
    """Read a notification setting from ~/.cortex/settings.json."""
    try:
        from src.config.settings import get_settings

        return get_settings().get("notifications", key, default=default)
    except Exception:
        return default


def _play_windows_sound() -> bool:
    """Two-tone beep through winsound (built into Windows)."""
    try:
        import winsound
        log.info("[SOUND] Playing beep...")
        winsound.Beep(523, 120)
        winsound.Beep(659, 150)
        log.info("[SOUND] Beep done")
        return True
    except Exception as e:
        log.warning(f"[SOUND] Beep failed: {e}")
        return False


def _play_posix_sound() -> bool:
    """Bell through the freedesktop sound theme. Linux only.

    canberra-gtk-play is the theme-aware player; paplay and pw-play are the
    plain PipeWire/PulseAudio players on the standard bell sample.
    """
    try:
        if shutil.which("canberra-gtk-play"):
            subprocess.run(["canberra-gtk-play", "--id=bell"],
                           capture_output=True, timeout=5)
            log.info("[SOUND] Beep done (canberra)")
            return True
        bell = "/usr/share/sounds/freedesktop/stereo/bell.oga"
        for player in ("paplay", "pw-play"):
            if shutil.which(player) and os.path.exists(bell):
                subprocess.run([player, bell], capture_output=True, timeout=5)
                log.info(f"[SOUND] Beep done ({player})")
                return True
        log.debug("[SOUND] No sound backend available, skipping")
        return False
    except Exception as e:
        log.warning(f"[SOUND] Beep failed: {e}")
        return False


def _play_macos_sound() -> bool:
    """Bell through the system alert sound via osascript.

    The freedesktop theme is Linux only: canberra-gtk-play, paplay, pw-play
    and /usr/share/sounds/freedesktop are all absent on macOS, so the Linux
    helper is silent there. AppleScript's `beep` plays whichever alert sound
    the user chose in System Settings, so there is no file path to guess.
    """
    try:
        if not shutil.which("osascript"):
            log.debug("[SOUND] osascript not found, skipping")
            return False
        result = subprocess.run(["osascript", "-e", "beep 1"],
                                capture_output=True, timeout=5)
        if result.returncode != 0:
            log.warning("[SOUND] osascript beep failed")
            return False
        log.info("[SOUND] Beep done (osascript)")
        return True
    except Exception as e:
        log.warning(f"[SOUND] Beep failed: {e}")
        return False


def play_alert_sound() -> None:
    """Play a short alert sound if sound_alerts is enabled in settings.

    Windows uses winsound.Beep() (built in). Linux uses the freedesktop sound
    theme through canberra-gtk-play, falling back to paplay or pw-play on the
    standard bell sample. macOS uses osascript's beep, which plays the alert
    sound chosen in System Settings.
    Runs in a daemon thread to avoid blocking the main thread.
    """
    try:
        from src.config.settings import get_settings
        enabled = get_settings().get("notifications", "sound_alerts", default=False)
        log.info(f"[SOUND] play_alert_sound called, setting={enabled}")
        if not enabled:
            return
    except Exception as e:
        log.warning(f"[SOUND] Failed to read setting: {e}")
        return

    def _beep():
        if sys.platform == "win32":
            _play_windows_sound()
        elif sys.platform == "darwin":
            _play_macos_sound()
        else:
            _play_posix_sound()

    threading.Thread(target=_beep, daemon=True, name="AlertSound").start()



def notify_input_needed(question_text: str) -> None:
    """Config-aware input-needed notification."""
    if not _get_notif_setting("input_needed_enabled", True):
        return
    if _get_notif_setting("only_when_unfocused", True) and _is_ide_focused():
        return
    preview = question_text.replace("\n", " ").strip()
    show_toast_notification("Cortex AI \u2014 Needs your input", preview[:120])


def notify_permission_required(detail: str) -> None:
    """Config-aware permission card notification.

    Only fires when the IDE is NOT focused (the card itself is the
    notification when the user is already looking at the chat).
    """
    if not _get_notif_setting("permission_card_enabled", True):
        return
    # Permission cards are visible in-chat, only toast when IDE is backgrounded
    if _is_ide_focused():
        return
    show_toast_notification("Cortex AI \u2014 Permission Required", detail[:120])
