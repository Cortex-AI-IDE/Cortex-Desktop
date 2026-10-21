"""Panel ordering for Settings -> Appearance -> Interface.

Two settings place the three main panels: the sidebar edge and the chat panel
edge. The editor+terminal block has no setting of its own; it fills whatever
space the other two leave, so a "chat right and editor right" combination
cannot exist.

Both edges are resolved together in src/ui/layout_positions.py, because
separate "move mine here" handlers undo each other: whichever runs second
inserts at a fixed index and overwrites the first one's position.
"""
from __future__ import annotations

import pytest

from src.ui.layout_positions import (
    PANELS,
    normalise_position,
    is_permutation,
    panel_order,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "sidebar, chat, expected",
    [
        ("left", "left", ("sidebar", "chat", "editor")),
        ("left", "right", ("sidebar", "editor", "chat")),
        ("right", "left", ("chat", "editor", "sidebar")),
        ("right", "right", ("editor", "chat", "sidebar")),
    ],
)
def test_sidebar_chat_matrix(sidebar, chat, expected):
    """The documented four-combination matrix."""
    assert panel_order(sidebar, chat) == expected


@pytest.mark.unit
@pytest.mark.parametrize("sidebar", ["left", "right"])
@pytest.mark.parametrize("chat", ["left", "right"])
def test_every_result_is_a_permutation(sidebar, chat):
    """A missing panel would silently drop a QWebEngineView from the splitter."""
    order = panel_order(sidebar, chat)
    assert is_permutation(order)
    assert sorted(order) == sorted(PANELS)


@pytest.mark.unit
@pytest.mark.parametrize("sidebar", ["left", "right"])
@pytest.mark.parametrize("chat", ["left", "right"])
def test_sidebar_keeps_its_outer_edge(sidebar, chat):
    """The sidebar never moves inward, whatever the chat panel asked for."""
    order = panel_order(sidebar, chat)
    outer = 0 if sidebar == "left" else -1
    assert order[outer] == "sidebar"


@pytest.mark.unit
@pytest.mark.parametrize("sidebar", ["left", "right"])
@pytest.mark.parametrize("chat", ["left", "right"])
def test_chat_hugs_its_own_edge_inside_the_sidebar(sidebar, chat):
    """Chat takes its side of the remaining space; it never crosses over."""
    order = panel_order(sidebar, chat)
    inner = order[1:] if sidebar == "left" else order[:-1]
    if chat == "left":
        assert inner[0] == "chat"
    else:
        assert inner[-1] == "chat"


@pytest.mark.unit
@pytest.mark.parametrize("sidebar", ["left", "right"])
@pytest.mark.parametrize("chat", ["left", "right"])
def test_editor_fills_the_space_left_over(sidebar, chat):
    """No editor setting exists: it takes the slot the two edges did not claim.

    When the edges differ, that slot is the middle. When both panels asked for
    the same edge, one of them has to yield and the editor necessarily takes
    the opposite end: two panels cannot share a single edge.
    """
    order = panel_order(sidebar, chat)
    if sidebar == chat:
        assert order.index("editor") == (2 if sidebar == "left" else 0)
    else:
        assert order.index("editor") == 1


@pytest.mark.unit
@pytest.mark.parametrize("value", ["", None, "centre", "LEFT ", "Right", 7])
def test_normalise_position_tolerates_anything(value):
    """A settings file written before the option existed must read as left."""
    expected = "right" if str(value).strip().lower() == "right" else "left"
    assert normalise_position(value) == expected


@pytest.mark.unit
def test_bad_default_still_yields_a_valid_position():
    assert normalise_position("??", default="nowhere") == "left"


@pytest.mark.unit
def test_unknown_values_fall_back_to_the_default_order():
    assert panel_order("nowhere", "elsewhere") == (
        "sidebar", "chat", "editor",
    )


@pytest.mark.unit
def test_defaults():
    """Defaults: sidebar left, chat left, editor fills the right."""
    assert panel_order() == ("sidebar", "chat", "editor")
