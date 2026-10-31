"""Ordering of the main window's three panels.

The main splitter holds three panels: the sidebar, the chat panel, and the
editor+terminal block. Settings -> Appearance -> Interface lets the user put
the sidebar and the chat panel on either edge; the editor+terminal block
fills whatever space the other two leave.

Both settings are resolved into ONE order here, on purpose. Two independent
"move my panel here" handlers would fight each other: each inserts at a fixed
index, so the second call silently undoes the first. Computing the whole order
from both values makes every combination deterministic, and the editor needs
no setting of its own.

Pure string logic with no Qt import, so it is testable headless.
"""

from typing import Sequence, Tuple

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
    """Left-to-right panel order for the three panels.

    +---------+-------+---------------------------+
    | sidebar | chat  | result (left -> right)    |
    +---------+-------+---------------------------+
    | left    | left  | sidebar, chat, editor     |
    | left    | right | sidebar, editor, chat     |
    | right   | left  | chat, editor, sidebar     |
    | right   | right | editor, chat, sidebar     |
    +---------+-------+---------------------------+

    The sidebar keeps its outer edge, the chat panel hugs whichever side it
    was given, and the editor+terminal block fills the space between them.
    No combination asks two panels for the same slot, so there is never an
    impossible "chat on the right and editor on the right" state.

    The result is always a permutation of :data:`PANELS`.
    """
    side = normalise_position(sidebar)
    chat_side = normalise_position(chat)
    # Inside the sidebar, the chat panel takes its own edge and the editor
    # fills the gap towards the sidebar.
    inner = ["chat", "editor"] if chat_side == LEFT else ["editor", "chat"]
    order = ["sidebar"] + inner if side == LEFT else inner + ["sidebar"]
    return tuple(order)


def is_permutation(order: Sequence[str]) -> bool:
    """True when ``order`` contains each panel exactly once."""
    return sorted(order) == sorted(PANELS)
