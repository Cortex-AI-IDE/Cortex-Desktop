"""Relaunching Cortex, one implementation, shared by every caller.

This logic was written for the Settings theme switch and carries two fixes
that are easy to lose if a second copy is ever written by hand:

  1. Frozen build: sys.executable is Cortex.exe and there is NO main.py on
     disk (it is bundled inside the exe). The original code ran
     Popen([exe, "src/main.py"]), passing a nonexistent argument, so the
     INSTALLED app never came back up. A frozen relaunch must be the exe
     ALONE.

  2. Single-instance mutex race: the outgoing process holds
     "Global\\CortexAIAgentIDE_v1" for the ~4s it takes to flush memory and
     the database on shutdown. A new instance started before that sees
     ERROR_ALREADY_EXISTS, foregrounds the DYING window, and exits itself -
     the restart silently cancels and the user is left with no app. The
     relauncher therefore WAITS on this exact pid rather than guessing a
     delay.
"""
from __future__ import annotations

import os
import subprocess
import sys

from src.utils.logger import get_logger

log = get_logger("app_restart")

_NO_WINDOW = 0x08000000   # subprocess.CREATE_NO_WINDOW
_NEW_GROUP = 0x00000200   # CREATE_NEW_PROCESS_GROUP


def relaunch_target() -> tuple[str, str | None, str]:
    """(executable, script_or_None, working_dir) for the current build.

    The entry point is src/main.py, NOT <root>/main.py. This module sits at
    src/core/app_restart.py, so the package root is two dirnames up, one
    fewer than the original copy needed from src/ui/dialogs/. Getting this
    wrong points the relauncher at a file that does not exist and the dev
    build never comes back, so it is asserted below and in the tests.
    """
    exe = sys.executable
    if getattr(sys, "frozen", False):
        return exe, None, os.path.dirname(exe)

    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main_script = os.path.join(src_dir, "main.py")
    # Working dir is the project root (parent of src/), matching how Cortex
    # is normally launched, imports are rooted there.
    workdir = os.path.dirname(src_dir)
    if not os.path.isfile(main_script):
        log.error(f"Relaunch entry point not found: {main_script}, "
                  f"a dev restart would start nothing")
    return exe, main_script, workdir


def build_relauncher_command(exe: str, main_script: str | None,
                             workdir: str, pid: int) -> list[str] | str:
    """The detached command that waits for `pid` to die, then starts Cortex.

    Returned rather than run so it can be asserted on in tests, this is the
    part that silently broke the installed build twice.
    """
    if os.name == "nt":
        # PowerShell single-quoted strings are literal; Windows paths cannot
        # contain single quotes, so this quoting is safe for spaces
        # (Program Files, OneDrive folders, ...).
        if main_script:
            launch = (f"Start-Process -FilePath '{exe}' "
                      f"-ArgumentList '\"{main_script}\"' "
                      f"-WorkingDirectory '{workdir}'")
        else:
            launch = (f"Start-Process -FilePath '{exe}' "
                      f"-WorkingDirectory '{workdir}'")
        ps_cmd = (f"Wait-Process -Id {pid} -ErrorAction SilentlyContinue; {launch}")
        return ["powershell", "-NoProfile", "-Command", ps_cmd]

    target = f'"{exe}"' + (f' "{main_script}"' if main_script else "")
    return f'while kill -0 {pid} 2>/dev/null; do sleep 0.5; done; {target} &'


def restart_ide(reason: str = "") -> bool:
    """Schedule a relaunch, then quit. Returns False if it could not start.

    The relauncher is spawned BEFORE app.quit() so it exists to do the work,
    but it blocks on this pid, so nothing launches until shutdown finishes.
    """
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            log.warning("Cannot restart: no QApplication instance")
            return False

        exe, main_script, workdir = relaunch_target()
        cmd = build_relauncher_command(exe, main_script, workdir, os.getpid())
        log.info(f"IDE restart requested ({reason or 'no reason given'}); "
                 f"relauncher: {cmd}")

        if os.name == "nt":
            # CREATE_NO_WINDOW, NOT DETACHED_PROCESS: a detached PowerShell
            # (a console app with no console) dies before running its
            # command, verified in a live process test. NO_WINDOW gives it
            # a hidden console and it runs reliably.
            subprocess.Popen(
                cmd, creationflags=_NO_WINDOW | _NEW_GROUP, close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(cmd, shell=True, start_new_session=True,
                             close_fds=True)

        log.info("Relaunch scheduled, quitting now")
        app.quit()
        return True
    except Exception as e:
        log.error(f"restart_ide failed: {e}")
        return False
