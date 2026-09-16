"""Process-wide startup (boot) flag.

While the main window's startup overlay is up, WebEngine pages load in
"boot-hidden" mode: they paint only their flat dark background and keep all
content invisible. Chromium's native child window z-orders ABOVE the Qt
startup overlay, so any opaque page content (logo, welcome screen, old
splash animation) would punch through and appear NEXT TO the main loading
screen - the "two CORTEX splashes" boot bug.

main_window marks boot complete the moment the overlay is dismissed; the
panels then call each page's window.__cortexReveal() to show real content.
Pages also self-reveal as a backstop (20s) and on any post-boot load, so a
missed reveal can never leave a panel invisible.
"""

_booting = True


def is_booting() -> bool:
    """True while the startup overlay has not been dismissed yet."""
    return _booting


def mark_boot_complete() -> None:
    """Flip the flag once the startup overlay is being dismissed.

    Idempotent. Must be called BEFORE revealing the pages so that any page
    load started afterwards (reload, lazy panels) never enters boot-hidden.
    """
    global _booting
    _booting = False
