"""
Windows Toast Notification Utility
Shows native Windows notifications when AI tasks complete.

Uses PowerShell + System.Windows.Forms.NotifyIcon.ShowBalloonTip()
for reliable notifications on ALL Windows 10/11 — no external
dependencies or AUMID registration required.

On Windows 10/11, ShowBalloonTip produces a notification in the
Action Center with a brief popup near the system tray area.
"""

import logging
import subprocess
import sys
import threading
from typing import Optional

log = logging.getLogger("notifications")

# App identity for toast notifications
APP_ID = "Cortex AI IDE"


def show_toast_notification(title: str, message: str, duration: str = "short", app_id: str = APP_ID) -> None:
    """
    Show a Windows notification popup.

    Uses NotifyIcon.ShowBalloonTip() which is built into Windows/.NET
    and works on ALL Windows 10/11 without:
      - External packages (no pip install needed)
      - AUMID registration (no Start Menu shortcuts)
      - Build tools (no MSVC required)

    On Windows 10/11, ShowBalloonTip shows as a modern Action Center
    notification with a brief popup near the clock.

    Args:
        title: Notification title (bold, first line)
        message: Notification body text (second line)
        duration: "short" (7s) or "long" (25s)
        app_id: Application identifier (kept for API compatibility)
    """
    timeout_ms = 8000 if duration == "short" else 25000

    # Sanitize for PowerShell single-quoted strings
    title_safe = title.replace("'", "''").replace("\n", " ")
    message_safe = message.replace("'", "''").replace("\n", " ")

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


def show_task_complete_notification(task_summary: str = "Task completed successfully") -> None:
    """Show notification for AI task completion — clean, minimal format."""
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


def play_alert_sound() -> None:
    """Play a short alert sound if sound_alerts is enabled in settings.

    Uses winsound.Beep() (built-in on Windows) — no external dependencies.
    Runs in a daemon thread to avoid blocking the main thread.
    """
    try:
        from src.config.settings import get_settings
        enabled = get_settings().get("notifications", "sound_alerts", default=False)
        log.info(f"[SOUND] play_alert_sound called — setting={enabled}")
        if not enabled:
            return
    except Exception as e:
        log.warning(f"[SOUND] Failed to read setting: {e}")
        return

    def _beep():
        try:
            import winsound
            log.info("[SOUND] Playing beep...")
            winsound.Beep(523, 120)
            winsound.Beep(659, 150)
            log.info("[SOUND] Beep done")
        except Exception as e:
            log.warning(f"[SOUND] Beep failed: {e}")

    threading.Thread(target=_beep, daemon=True, name="AlertSound").start()


def notify_task_complete(response: str, progress_msg: str = "") -> None:
    """Config-aware task completion notification.

    Checks settings.notifications.task_complete_enabled before firing.
    Also respects only_when_unfocused setting.
    Plays an alert sound if notifications.sound_alerts is enabled.
    """
    # Play sound first (before checking focus — user may be in another window)
    play_alert_sound()

    if not _get_notif_setting("task_complete_enabled", True):
        return
    if _get_notif_setting("only_when_unfocused", True) and _is_ide_focused():
        return

    from src.main_window import _extract_notification_summary

    if response and len(response) > 10:
        summary = _extract_notification_summary(response)
        if progress_msg:
            msg = f"{progress_msg}  \u2022  {summary}" if summary else progress_msg
        else:
            msg = summary or "Task completed successfully."
    else:
        msg = progress_msg or "Task completed successfully."
    show_toast_notification("Cortex AI \u2014 Task Complete", msg)


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
    # Permission cards are visible in-chat — only toast when IDE is backgrounded
    if _is_ide_focused():
        return
    show_toast_notification("Cortex AI \u2014 Permission Required", detail[:120])
