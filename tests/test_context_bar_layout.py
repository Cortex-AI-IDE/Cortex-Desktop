"""Chat footer ContextBar: the meter must not move as usage changes.

2026-10-21 report (screenshot): the footer read "Context ... 0% ... provider"
with the 0% stranded in the MIDDLE of the row.

Cause: the row is [label][bar stretch=1][usage][provider]. update_budget()
did `self._bar.setVisible(pct > 0)`, and the bar is the only stretch-1 item,
so at 0% QBoxLayout spread the leftover width across the three zero-stretch
labels. The 0% was not centred by design, it was sharing slack. Above 0% the
bar returned, took the slack, and the row snapped right.

The fix keeps the bar visible at every percentage and only makes its TRACK
transparent at 0% (a tinted track used to draw a stray dash, which is why it
was hidden in the first place).
"""
from __future__ import annotations

import pytest

# Imported before any QApplication exists (Qt WebEngine requirement).
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PyQt6.QtWidgets import QApplication


@pytest.fixture
def bar(qtbot):
    from src.ui.secondary_ui import ContextBar
    # Top-level, not a child of a local host: a host that goes out of scope
    # takes the whole widget tree (and the QProgressBar) with it.
    widget = ContextBar()
    qtbot.addWidget(widget)
    widget.resize(900, 24)
    widget.show()
    qtbot.wait(50)
    return widget


def _settle(qtbot) -> None:
    QApplication.processEvents()
    qtbot.wait(30)
    QApplication.processEvents()


@pytest.mark.gui
def test_zero_percent_stays_at_the_right_edge(bar, qtbot):
    """The regression: 0% used to strand the label in the MIDDLE of the row.

    With the bar hidden, QBoxLayout spread the slack across the three
    zero-stretch labels and the usage label landed around the centre
    (measured: x=307 of 900). It belongs next to the provider, at the edge
    (measured after the fix: x=847).
    """
    bar.update_budget(0, 1)
    _settle(qtbot)
    assert bar._usage.geometry().x() > bar.width() * 0.75


@pytest.mark.gui
def test_provider_label_does_not_move_either(bar, qtbot):
    bar.update_budget(0, 1, "deepseek")
    _settle(qtbot)
    at_zero = bar._provider.geometry()
    bar.update_budget(800, 1000, "deepseek")
    _settle(qtbot)
    assert at_zero.x() == bar._provider.geometry().x()


@pytest.mark.gui
def test_bar_stays_visible_at_zero_percent(bar):
    """It is the only stretching item; hiding it reflows the whole row."""
    bar.update_budget(0, 1)
    assert bar._bar.isVisible()
    assert not bar._bar.isHidden()


@pytest.mark.gui
def test_track_is_transparent_at_zero(bar):
    """No stray dash, which is why the bar used to be hidden at 0%."""
    bar.update_budget(0, 1)
    assert "rgba(" in bar._bar.styleSheet()
    assert ",0.0)" in bar._bar.styleSheet()


@pytest.mark.gui
def test_track_returns_when_usage_is_real(bar):
    bar.update_budget(0, 1)
    bar.update_budget(500, 1000)
    assert ",0.3)" in bar._bar.styleSheet()
    assert bar._usage.text() == "50%"


@pytest.mark.gui
def test_zero_budget_does_not_divide_by_zero(bar):
    bar.update_budget(0, 0)
    assert bar._usage.text() == "0%"
