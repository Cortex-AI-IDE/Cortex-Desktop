"""
secondary_ui.py — Phase 8 secondary UI components
===================================================

- PermissionCard: gates agent loop, user must Accept/Reject (with Allow always → confirm step)
- QuestionCard: interactive question from agent with choices
- TodoSection: task progress display
- ContextBar: token budget / context usage display
"""

from __future__ import annotations
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QProgressBar, QSizePolicy,
)

from src.ui.tokens import TOKENS as T


# ============================================================
# PERMISSION CARD  (opencode-style: Allow once / Allow always / Reject)
# ============================================================
class PermissionCard(QFrame):
    """
    Gates the agent loop — user must Allow once, Allow always, or Reject.
    Allow always → confirm step with "until Cortex is restarted" warning.

    Emits: allow_once(str), allow_always(str), rejected(str) with request_id.
    """
    allow_once   = pyqtSignal(str)   # request_id
    allow_always = pyqtSignal(str)   # request_id
    rejected     = pyqtSignal(str)   # request_id

    def __init__(self, request_id: str, command: str, warning: str = "",
                 patterns: list[str] | None = None, parent=None):
        super().__init__(parent)
        self._rid = request_id
        self._command = command
        self._warning = warning
        self._patterns = patterns or ([command] if command else [])
        self._resolved = False

        # Responsive containment: fill parent width, never overflow
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.setObjectName("permFrame")
        self.setStyleSheet(
            f"QFrame#permFrame {{ border-left: 2px solid {T['orange']};"
            f" background: rgba(255,184,108,0.04); border-radius: 0px; }}")
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(16, 12, 16, 12)
        self._v.setSpacing(10)
        self._build_main(command, warning)

    def _clear(self):
        while self._v.count():
            it = self._v.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                lay = it.layout()
                while lay.count():
                    x = lay.takeAt(0)
                    if x.widget():
                        x.widget().deleteLater()

    def _patterns_block(self, title: str):
        if title:
            t = QLabel(title)
            t.setStyleSheet(f"color:{T['muted']};font-size:12px;")
            self._v.addWidget(t)
        for pat in self._patterns:
            p = QLabel(f"- {pat}")
            p.setStyleSheet(f"font-family:{T['font_mono']};font-size:12px;color:{T['text_dim']};")
            p.setWordWrap(True)
            self._v.addWidget(p)

    def _btn(self, text: str, color: str, filled: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if filled:
            b.setStyleSheet(
                f"background:{color};color:#1a1205;border:none;"
                f"font-size:12px;font-weight:600;padding:4px 12px;border-radius:4px;")
        else:
            b.setStyleSheet(
                f"background:transparent;color:{color};border:none;"
                f"font-size:12px;padding:4px 10px;")
        return b

    def _build_main(self, command: str, warning: str):
        self._clear()
        head = QHBoxLayout()
        ic = QLabel("\u26A0")
        ic.setStyleSheet(f"color:{T['orange']};font-size:14px;")
        ti = QLabel("Permission required")
        ti.setStyleSheet(f"color:{T['text']};font-weight:600;font-size:13px;")
        head.addWidget(ic)
        head.addWidget(ti)
        head.addStretch()
        self._v.addLayout(head)

        if warning:
            act = QLabel(warning)
            act.setStyleSheet(f"color:{T['text_dim']};font-size:13px;")
            act.setWordWrap(True)
            self._v.addWidget(act)

        if self._patterns:
            self._patterns_block("Patterns")

        row = QHBoxLayout()
        row.setSpacing(8)
        once = self._btn("Allow once", T['orange'], filled=True)
        always = self._btn("Allow always", T['text_dim'])
        rej = self._btn("Reject", T['text_dim'])
        once.clicked.connect(lambda: (self.allow_once.emit(self._rid), self._resolved_ok("Allowed once", T['green'])))
        rej.clicked.connect(lambda: (self.rejected.emit(self._rid), self._resolved_ok("Rejected", T['red'])))
        always.clicked.connect(self._build_confirm)
        row.addWidget(once)
        row.addWidget(always)
        row.addWidget(rej)
        row.addStretch()
        self._v.addLayout(row)

    def _build_confirm(self):
        self._clear()
        head = QHBoxLayout()
        ic = QLabel("\u26A0")
        ic.setStyleSheet(f"color:{T['orange']};font-size:14px;")
        ti = QLabel("Always allow")
        ti.setStyleSheet(f"color:{T['text']};font-weight:600;font-size:13px;")
        head.addWidget(ic)
        head.addWidget(ti)
        head.addStretch()
        self._v.addLayout(head)

        sub = QLabel("This will allow all operations until Cortex is restarted")
        sub.setStyleSheet(f"color:{T['muted']};font-size:12px;")
        sub.setWordWrap(True)
        self._v.addWidget(sub)

        row = QHBoxLayout()
        row.setSpacing(8)
        confirm = self._btn("Confirm", T['orange'], filled=True)
        cancel = self._btn("Cancel", T['text_dim'])
        confirm.clicked.connect(lambda: (self.allow_always.emit(self._rid), self._resolved_ok("Always allowed", T['green'])))
        cancel.clicked.connect(lambda: self._build_main(self._command, self._warning))
        row.addWidget(confirm)
        row.addWidget(cancel)
        row.addStretch()
        self._v.addLayout(row)

    def _resolved_ok(self, label: str, color: str):
        self._resolved = True
        self._clear()
        l = QLabel(f"\u2713 {label}")
        l.setStyleSheet(f"color:{color};font-size:12px;")
        self._v.addWidget(l)

# ============================================================
# QUESTION CARD
# ============================================================
class QuestionCard(QFrame):
    """
    Question from agent with numbered clickable options + custom input.
    Emits: answered(str) with the user's response.
    """
    answered = pyqtSignal(str)

    def __init__(self, question: str, qtype: str = "text",
                 choices: list[dict] | None = None, default: str = "",
                 details: str = "", parent=None):
        super().__init__(parent)
        self._answered = False
        self.setObjectName("questionCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(
            f"QFrame#questionCard {{ border: 1px solid {T['accent']}; "
            f"border-radius: 6px; background: rgba(6,182,212,0.04); }}"
        )

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(8)

        # Question text
        q_lbl = QLabel(question)
        q_lbl.setWordWrap(True)
        q_lbl.setStyleSheet(
            f"color:{T['text']};font-size:{T['font_size_sm']};font-weight:500;"
        )
        v.addWidget(q_lbl)

        # Numbered options (if choices provided)
        if choices:
            for i, choice in enumerate(choices, 1):
                label = choice.get("label", choice.get("value", ""))
                val = choice.get("value", label)
                btn = QPushButton(f"  {i}.  {label}")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setStyleSheet(
                    f"QPushButton {{"
                    f"  text-align:left; padding:6px 12px;"
                    f"  background:rgba(255,255,255,0.03); border:1px solid {T['border']};"
                    f"  border-radius:4px; color:{T['text']}; font-size:{T['font_size_xs']};"
                    f"}}"
                    f"QPushButton:hover {{"
                    f"  background:rgba(6,182,212,0.12); border-color:{T['accent']};"
                    f"}}"
                )
                btn.clicked.connect(lambda _=False, v=val: self._on_answer(v))
                v.addWidget(btn)

        # Custom input row
        row = QHBoxLayout()
        row.setSpacing(6)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type custom answer..." if choices else "Type your answer...")
        self._input.setText(default)
        self._input.setStyleSheet(
            f"background:{T['bg_card']};border:1px solid {T['border']};"
            f"border-radius:4px;padding:6px 10px;font-size:{T['font_size_xs']};"
            f"color:{T['text']};"
        )
        self._input.returnPressed.connect(lambda: self._on_answer(self._input.text()))
        send_btn = QPushButton("Send")
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.setStyleSheet(
            f"background:{T['accent']};border:none;color:#04222a;"
            f"font-size:{T['font_size_xs']};padding:6px 14px;border-radius:4px;font-weight:600;"
        )
        send_btn.clicked.connect(lambda: self._on_answer(self._input.text()))
        row.addWidget(self._input, 1)
        row.addWidget(send_btn)
        v.addLayout(row)

    def _on_answer(self, answer: str):
        if answer.strip() and not self._answered:
            self._answered = True
            self.answered.emit(answer.strip())
            for child in self.findChildren((QPushButton, QLineEdit)):
                child.setEnabled(False)


# ============================================================
# TODO SECTION  (Flat design, spinner for in_progress, breathing space)
# ============================================================
class TodoSection(QFrame):
    """Displays task progress as a collapsible section above input.
    Flat design: no border radius, no card border, 8px outer padding.
    Spinner animation for in_progress items."""

    # Priority sort order
    _PRIORITY = {"in_progress": 0, "pending": 1, "completed": 2}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[dict] = []
        self._collapsed = True
        self._spinners: list = []  # track active spinners for cleanup

        # Responsive: never force wider than parent
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        # FLAT DESIGN: no border, no radius, just background
        self.setObjectName("todoSection")
        self.setStyleSheet(f"""
            QFrame#todoSection {{
                background: {T['bg_secondary']};
                border: none;
                border-radius: 0px;
            }}
        """)

        v = QVBoxLayout(self)
        # 8px padding all sides outside
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(0)

        # ── Header row (clickable) ──
        self._head_widget = QWidget()
        self._head_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        self._head_widget.setStyleSheet("background:transparent;border:none;")
        h = QHBoxLayout(self._head_widget)
        h.setContentsMargins(4, 6, 4, 6)
        h.setSpacing(8)

        # Chevron — ▶ collapsed, ▼ expanded
        self._chev = QLabel("▶")
        self._chev.setFixedWidth(14)
        self._chev.setStyleSheet(f"color:{T['mono_muted']};font-size:10px;background:transparent;border:none;")

        # Title — "Tasks" or "Tasks — main_task"
        self._title = QLabel("Tasks")
        self._title.setStyleSheet(
            f"color:{T['text']};font-size:{T['font_size_sm']};font-weight:600;"
            f"background:transparent;border:none;"
        )

        h.addWidget(self._chev)
        h.addWidget(self._title)
        h.addStretch()

        # Progress badge — "2/6" count pill (flat, no radius)
        self._count = QLabel("0/0")
        self._count.setStyleSheet(
            f"background:rgba(124,108,231,0.12);color:{T['think_label']};"
            f"font-size:{T['font_size_xxs']};font-weight:500;"
            f"padding:2px 8px;border-radius:0px;border:1px solid rgba(255,255,255,0.15);"
        )
        h.addWidget(self._count)

        # Expand/collapse chevron button (right side)
        self._expand_btn = QLabel("▼")
        self._expand_btn.setFixedWidth(14)
        self._expand_btn.setStyleSheet(f"color:{T['mono_muted']};font-size:10px;background:transparent;border:none;")
        self._expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        h.addWidget(self._expand_btn)

        v.addWidget(self._head_widget)

        # ── Progress bar (always visible, thin) ──
        self._progress = QProgressBar()
        self._progress.setMaximum(100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(3)
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                background: {T['border_dim']};
                border: 0;
                border-radius: 0px;
                margin: 0 4px;
            }}
            QProgressBar::chunk {{
                background: {T['think_label']};
                border-radius: 0px;
            }}
        """)
        v.addWidget(self._progress)

        # ── Body (collapsible todo items) ──
        self._body = QWidget()
        self._body.setStyleSheet("background:transparent;border:none;")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(4, 8, 4, 4)
        self._body_layout.setSpacing(10)  # breathing space between items
        v.addWidget(self._body)
        self._body.setVisible(False)

        # Click-to-toggle: both header and expand button
        self._head_widget.mousePressEvent = lambda e: self._toggle()
        self._expand_btn.mousePressEvent = lambda e: self._toggle()

    def _toggle(self):
        self._collapsed = not self._collapsed
        self._body.setVisible(not self._collapsed)
        self._chev.setText("▶" if self._collapsed else "▼")
        self._expand_btn.setText("▲" if not self._collapsed else "▼")

    def _stop_all_spinners(self):
        """Stop and clean up all active spinners."""
        for sp in self._spinners:
            sp.stop()
            sp.deleteLater()
        self._spinners.clear()

    def update_todos(self, todos: list[dict], main_task: str = ""):
        """Update the todo list. Each todo: {text, done, status}.
        Sorts by priority: in_progress → pending → completed."""
        # Stop old spinners
        self._stop_all_spinners()

        # Sort by priority
        self._items = sorted(todos, key=lambda t: self._PRIORITY.get(t.get("status", "pending"), 1))

        # Clear old items
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        done_count = 0
        for todo in self._items:
            text = todo.get("text", "")
            status = todo.get("status", "pending")
            done = todo.get("done", False) or status == "completed"
            if done:
                done_count += 1

            # ── Task card container: flat design, white border, no radius ──
            row = QWidget()
            row.setObjectName("todoTaskCard")
            row.setStyleSheet(f"""
                QWidget#todoTaskCard {{
                    background: {T['bg_secondary']};
                    border: 1px solid rgba(255,255,255,0.25);
                    border-radius: 0px;
                    padding: 0px;
                }}
            """)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 8, 10, 8)
            rl.setSpacing(10)

            # Status indicator
            if done:
                # Green checkmark
                ind = QLabel("✓")
                ind.setFixedWidth(18)
                ind.setAlignment(Qt.AlignmentFlag.AlignCenter)
                ind.setStyleSheet(f"color:{T['green']};font-size:13px;font-weight:600;background:transparent;border:none;")
                rl.addWidget(ind)
            elif status == "in_progress":
                # NEW: TodoRingSpinner — circle ring with spinning arc inside
                from src.ui.spinner import TodoRingSpinner
                spinner = TodoRingSpinner(18, T.get("think_label", "#a89df0"))
                spinner.start()
                self._spinners.append(spinner)
                rl.addWidget(spinner)
            else:
                # Empty circle for pending
                ind = QLabel("○")
                ind.setFixedWidth(18)
                ind.setAlignment(Qt.AlignmentFlag.AlignCenter)
                ind.setStyleSheet(f"color:{T['mono_muted']};font-size:13px;background:transparent;border:none;")
                rl.addWidget(ind)

            # Todo text
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            if done:
                lbl.setStyleSheet(
                    f"color:{T['muted']};font-size:{T['font_size_xs']};"
                    f"text-decoration:line-through;background:transparent;border:none;"
                )
            elif status == "in_progress":
                lbl.setStyleSheet(
                    f"color:{T['text']};font-size:{T['font_size_xs']};font-weight:600;"
                    f"background:transparent;border:none;"
                )
            else:
                lbl.setStyleSheet(
                    f"color:{T['text_dim']};font-size:{T['font_size_xs']};"
                    f"background:transparent;border:none;"
                )
            rl.addWidget(lbl, 1)
            self._body_layout.addWidget(row)

        total = len(self._items)
        self._count.setText(f"{done_count}/{total}")

        # Progress bar
        pct = int(done_count / total * 100) if total > 0 else 0
        self._progress.setValue(pct)

        # Update title
        if main_task:
            self._title.setText(f"Tasks \u2014 {main_task}")
        else:
            self._title.setText("Tasks")


# ============================================================
# CONTEXT BAR / TOKEN BUDGET
# ============================================================
class ContextBar(QFrame):
    """Displays token budget / context usage."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:transparent;border:none;")
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 4, 12, 4)
        h.setSpacing(8)

        self._label = QLabel("Context")
        self._label.setStyleSheet(f"color:{T['muted']};font-size:{T['font_size_xxs']};")
        self._bar = QProgressBar()
        self._bar.setMaximum(100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(4)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background:{T['border_dim']};border:0;border-radius:2px; }}"
            f"QProgressBar::chunk {{ background:{T['accent']};border-radius:2px; }}"
        )
        self._usage = QLabel("0%")
        self._usage.setStyleSheet(f"color:{T['muted']};font-size:{T['font_size_xxs']};")
        self._provider = QLabel("")
        self._provider.setStyleSheet(f"color:{T['mono_muted']};font-size:{T['font_size_xxs']};")

        h.addWidget(self._label)
        h.addWidget(self._bar, 1)
        h.addWidget(self._usage)
        h.addWidget(self._provider)

    def update_budget(self, used: int, budget: int, provider: str = ""):
        """Update the context budget display."""
        pct = int(used / budget * 100) if budget > 0 else 0
        self._bar.setValue(min(pct, 100))
        self._usage.setText(f"{pct}%")
        if provider:
            self._provider.setText(provider)
        # Color based on usage
        if pct > 90:
            self._bar.setStyleSheet(
                f"QProgressBar {{ background:{T['border_dim']};border:0;border-radius:2px; }}"
                f"QProgressBar::chunk {{ background:{T['red']};border-radius:2px; }}"
            )
        elif pct > 70:
            self._bar.setStyleSheet(
                f"QProgressBar {{ background:{T['border_dim']};border:0;border-radius:2px; }}"
                f"QProgressBar::chunk {{ background:{T['orange']};border-radius:2px; }}"
            )
        else:
            self._bar.setStyleSheet(
                f"QProgressBar {{ background:{T['border_dim']};border:0;border-radius:2px; }}"
                f"QProgressBar::chunk {{ background:{T['accent']};border-radius:2px; }}"
            )