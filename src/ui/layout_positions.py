"""Ordering of the main window's three panels.

Both the sidebar and chat "put me on this edge" settings resolve into ONE
order here, on purpose: two independent handlers each inserting at a fixed
index silently undo each other. The editor+terminal block fills the gap.
Pure string logic, no Qt, so it is testable headless.
"""

from typing import Tuple

LEFT = "left"
RIGHT = "right"

_POSITIONS = (LEFT, RIGHT)

#: Names used in the returned order, in the order the panels are built.
PANELS: Tuple[str, ...] = ("sidebar", "chat", "editor")


def normalise_position(value: object, default: str = LEFT) -> str:
    """Return 'left' or 'right', falling back to ``default``.

    Missing keys read as the default (no migration needed for a settings file
    written before the option existed), and an unknown value must never
    produce an order Qt cannot satisfy.
    """
    text = str(value if value is not None else "").strip().lower()
    return text if text in _POSITIONS else normalise_default(default)


def normalise_default(default: object) -> str:
    """Guard the fallback itself: a bad default still yields 'left'."""
    text = str(default if default is not None else "").strip().lower()
    return text if text in _POSITIONS else LEFT


def panel_order(sidebar: object = LEFT, chat: object = LEFT) -> Tuple[str, ...]:
    """Left-to-right order of the three panels.

    The sidebar keeps its outer edge, the chat panel hugs whichever side it was
    given, and the editor+terminal block fills the space between. Always a
    permutation of PANELS, so no two panels ever claim the same slot.
    """
    side = normalise_position(sidebar)
    chat_side = normalise_position(chat)
    # Inside the sidebar, the chat panel takes its own edge and the editor
    # fills the gap towards the sidebar.
    inner = ["chat", "editor"] if chat_side == LEFT else ["editor", "chat"]
    order = ["sidebar"] + inner if side == LEFT else inner + ["sidebar"]
    return tuple(order)
