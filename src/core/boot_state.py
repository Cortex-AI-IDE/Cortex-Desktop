"""Process-wide startup (boot) flag.

While the startup overlay is up, WebEngine pages load "boot-hidden": they paint
only a flat dark background. Chromium's native child window z-orders ABOVE the
Qt overlay, so opaque page content would punch through and appear NEXT TO the
loading screen (the "two CORTEX splashes" bug). main_window clears the flag
when the overlay is dismissed; panels then call __cortexReveal(). Pages also
self-reveal after 20s as a backstop.
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
