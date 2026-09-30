"""
Custom Providers dialog (AI menu > Custom Providers...).

* Global providers (published by Cortex, e.g. NVIDIA NIM): add your key, test it.
* Your own providers (Ollama / vLLM / LM Studio / any OpenAI-compatible URL):
  add, test, refresh the model list, submit for everyone.

Keys are saved locally (KeyManager "dyn_<slug>") and go straight to the
provider. The Cortex server only receives the provider URL and model ids.
All network work runs off the GUI thread.
"""
from __future__ import annotations

import re
import threading
import webbrowser
from typing import Any, Callable, Dict, Optional

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from src.ai import dynamic_providers as dyn
from src.utils.logger import get_logger

log = get_logger("custom_providers_dialog")

_AUTH_CHOICES = [("bearer", "API key (Authorization: Bearer)"),
                 ("header", "API key in custom header"),
                 ("none", "No key (local / self-hosted)")]


class _Relay(QObject):
    done = pyqtSignal(object, object, object)  # callback, result, error


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return (s or "my-provider")[:40].strip("-")


class CustomProvidersDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Custom Providers")
        self.resize(860, 540)
        self._relay = _Relay()
        self._relay.done.connect(self._on_bg_done)
        self._mine: Dict[str, Dict[str, Any]] = {}
        self._current: Optional[Dict[str, Any]] = None
        self._build()
        self._reload_list()
        self._bg(dyn.list_mine, self._on_mine_loaded, quiet=True)

    # ── threading helper ──────────────────────────────────────────────
    def _bg(self, fn: Callable, on_done: Callable, *args, quiet: bool = False):
        self._status.setText("Working…")

        def run():
            try:
                self._relay.done.emit(on_done, fn(*args), None)
            except Exception as e:  # noqa: BLE001 - shown to the user
                self._relay.done.emit(on_done, None, (e, quiet))
        threading.Thread(target=run, daemon=True).start()

    def _on_bg_done(self, cb, result, err):
        self._status.setText("")
        if err:
            exc, quiet = err
            log.info(f"[CustomProviders] {exc}")
            if not quiet:
                QMessageBox.warning(self, "Custom Providers", str(exc))
            return
        cb(result)

    # ── layout ────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_select)
        lv.addWidget(self._list, 1)
        row = QHBoxLayout()
        b_add = QPushButton("+ Add my provider")
        b_add.clicked.connect(self._new_provider)
        b_sync = QPushButton("Sync")
        b_sync.setToolTip("Fetch the latest providers from Cortex")
        b_sync.clicked.connect(self._force_sync)
        row.addWidget(b_add)
        row.addWidget(b_sync)
        lv.addLayout(row)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        self._title = QLabel("Select a provider")
        self._title.setStyleSheet("font-size:15px;font-weight:600")
        self._subtitle = QLabel("")
        self._subtitle.setWordWrap(True)
        rv.addWidget(self._title)
        rv.addWidget(self._subtitle)

        form = QFormLayout()
        self._f_name = QLineEdit()
        self._f_slug = QLineEdit()
        self._f_url = QLineEdit()
        self._f_url.setPlaceholderText("https://integrate.api.nvidia.com/v1  or  http://localhost:11434/v1")
        self._f_auth = QComboBox()
        for val, label in _AUTH_CHOICES:
            self._f_auth.addItem(label, val)
        self._f_header = QLineEdit()
        self._f_header.setPlaceholderText("e.g. api-key")
        self._f_key = QLineEdit()
        self._f_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._f_name.textEdited.connect(lambda t: self._f_slug.isEnabled() and self._f_slug.setText(_slugify(t)))
        form.addRow("Name", self._f_name)
        form.addRow("Short id", self._f_slug)
        form.addRow("Base URL", self._f_url)
        form.addRow("Auth", self._f_auth)
        form.addRow("Header name", self._f_header)
        form.addRow("API key", self._f_key)
        rv.addLayout(form)

        self._models_lbl = QLabel("")
        self._models_lbl.setWordWrap(True)
        rv.addWidget(self._models_lbl)

        btns = QHBoxLayout()
        self._b_key = QPushButton("Save key")
        self._b_key.clicked.connect(self._save_key)
        self._b_delkey = QPushButton("Remove key")
        self._b_delkey.clicked.connect(self._remove_key)
        self._b_test = QPushButton("Test connection")
        self._b_test.clicked.connect(self._test)
        self._b_signup = QPushButton("Get a key ↗")
        self._b_signup.clicked.connect(lambda: self._current and webbrowser.open(self._current.get("signup_url", "")))
        for b in (self._b_key, self._b_delkey, self._b_test, self._b_signup):
            btns.addWidget(b)
        rv.addLayout(btns)

        own = QHBoxLayout()
        self._b_save = QPushButton("Save provider")
        self._b_save.clicked.connect(self._save_provider)
        self._b_models = QPushButton("Refresh models")
        self._b_models.setToolTip("List models from the provider with your key and sync them")
        self._b_models.clicked.connect(self._refresh_models)
        self._b_submit = QPushButton("Submit for everyone")
        self._b_submit.clicked.connect(self._submit)
        self._b_delete = QPushButton("Delete")
        self._b_delete.clicked.connect(self._delete)
        for b in (self._b_save, self._b_models, self._b_submit, self._b_delete):
            own.addWidget(b)
        rv.addLayout(own)
        rv.addStretch(1)
        self._status = QLabel("")
        self._status.setStyleSheet("color:#888")
        rv.addWidget(self._status)
        split.addWidget(right)
        split.setSizes([280, 580])
        self._set_mode(None)

    # ── list ──────────────────────────────────────────────────────────
    def _reload_list(self, select: Optional[str] = None):
        self._list.blockSignals(True)
        self._list.clear()
        specs = {p["slug"]: p for p in dyn.all_providers()}
        for slug, mine in self._mine.items():  # include pending/rejected not in sync yet
            specs.setdefault(slug, {**mine, "scope": "private"})
        for slug, p in sorted(specs.items(), key=lambda kv: (kv[1].get("scope") != "global", kv[0])):
            ready = p.get("auth_style") == "none" or bool(dyn.get_key(slug))
            status = self._mine.get(slug, {}).get("status") or p.get("status", "")
            tag = "🌐" if p.get("scope") == "global" else "👤"
            badge = "✓ key" if ready else "no key"
            if status in ("pending", "rejected", "published"):
                badge += f" · {status}"
            it = QListWidgetItem(f"{tag}  {p.get('name', slug)}   ({badge})")
            it.setData(Qt.ItemDataRole.UserRole, slug)
            self._list.addItem(it)
            if slug == select:
                self._list.setCurrentItem(it)
        self._list.blockSignals(False)
        if select:
            self._on_select(self._list.currentItem(), None)

    def _on_mine_loaded(self, rows):
        self._mine = {r["slug"]: r for r in rows or []}
        self._reload_list(self._current and self._current.get("slug"))

    def _force_sync(self):
        dyn.refresh_async(force=True)
        self._bg(dyn.list_mine, self._on_mine_loaded, quiet=True)

    # ── selection / modes ─────────────────────────────────────────────
    def _set_mode(self, mode: Optional[str]):
        """None = nothing selected, 'global', 'private', 'new'."""
        editable = mode in ("private", "new")
        for w in (self._f_name, self._f_url, self._f_auth, self._f_header):
            w.setEnabled(editable)
        self._f_slug.setEnabled(mode == "new")
        self._f_key.setEnabled(mode is not None)
        for b in (self._b_key, self._b_delkey, self._b_test):
            b.setEnabled(mode in ("global", "private"))
        self._b_test.setEnabled(mode is not None)
        self._b_signup.setVisible(bool(self._current and self._current.get("signup_url")))
        self._b_save.setVisible(editable)
        for b in (self._b_models, self._b_submit, self._b_delete):
            b.setVisible(mode == "private")

    def _on_select(self, item, _prev):
        if item is None:
            return
        slug = item.data(Qt.ItemDataRole.UserRole)
        spec = dyn.get_provider_spec(slug) or {**self._mine.get(slug, {}), "scope": "private"}
        self._current = spec
        mode = "global" if spec.get("scope") == "global" else "private"
        self._title.setText(spec.get("name", slug))
        mine = self._mine.get(slug, {})
        note = mine.get("review_note") or ""
        sub = spec.get("description", "")
        if mode == "private":
            sub = f"Your provider · status: {mine.get('status', spec.get('status', 'private'))}" + (f" · {note}" if note else "")
        self._subtitle.setText(sub)
        self._f_name.setText(spec.get("name", ""))
        self._f_slug.setText(slug)
        self._f_url.setText(spec.get("base_url", ""))
        self._f_auth.setCurrentIndex(max(0, self._f_auth.findData(spec.get("auth_style", "bearer"))))
        self._f_header.setText(spec.get("auth_header_name", ""))
        self._f_key.clear()
        hint = spec.get("key_prefix_hint")
        self._f_key.setPlaceholderText(("saved ✓  (type to replace)" if dyn.get_key(slug) else "paste key")
                                       + (f" · starts with {hint}" if hint else ""))
        n = len(spec.get("models") or [])
        self._models_lbl.setText(f"{n} models available in the model picker." if n else "No models yet.")
        self._set_mode(mode)

    def _new_provider(self):
        self._list.clearSelection()
        self._current = {"scope": "new"}
        self._title.setText("Add my provider")
        self._subtitle.setText("Any OpenAI-compatible server: your own Ollama, vLLM, LM Studio, "
                               "llama.cpp, or a hosted gateway. Only you see it unless you submit it.")
        for w in (self._f_name, self._f_slug, self._f_url, self._f_header, self._f_key):
            w.clear()
        self._f_key.setPlaceholderText("stored only on this PC")
        self._f_auth.setCurrentIndex(0)
        self._models_lbl.setText("")
        self._set_mode("new")

    # ── actions ───────────────────────────────────────────────────────
    def _slug(self) -> str:
        return (self._f_slug.text() or "").strip().lower()

    def _conn_args(self, key: Optional[str] = None):
        slug = self._slug()
        return dict(
            base_url=self._f_url.text().strip(),
            api_key=key if key is not None else (self._f_key.text().strip() or dyn.get_key(slug)),
            auth_style=self._f_auth.currentData(),
            auth_header_name=self._f_header.text().strip(),
            models_path=(self._current or {}).get("models_path", "/models"),
            extra_headers=(self._current or {}).get("extra_headers") or {},
        )

    def _save_key(self):
        key = self._f_key.text().strip()
        if not key:
            return
        if dyn.set_key(self._slug(), key):
            self._f_key.clear()
            self._reload_list(self._slug())

    def _remove_key(self):
        dyn.delete_key(self._slug())
        self._reload_list(self._slug())

    def _test(self):
        args = self._conn_args()
        self._bg(lambda: dyn.fetch_upstream_models(**args),
                 lambda ids: QMessageBox.information(
                     self, "Connection OK",
                     f"{len(ids)} models found.\n\n" + "\n".join(ids[:15]) + ("\n…" if len(ids) > 15 else "")))

    def _save_provider(self):
        fields = {
            "slug": self._slug(), "name": self._f_name.text().strip(),
            "base_url": self._f_url.text().strip(), "auth_style": self._f_auth.currentData(),
            "auth_header_name": self._f_header.text().strip(),
        }
        key = self._f_key.text().strip()
        is_new = (self._current or {}).get("scope") == "new"
        args = self._conn_args(key or None)

        def work():
            # List models first (proves the URL + key work), then save.
            ids = dyn.fetch_upstream_models(**args)
            if is_new:
                dyn.create_mine({**fields, "models": ids})
            else:
                fields.pop("slug")
                dyn.update_mine(self._slug(), fields)
            if key:
                dyn.set_key(self._slug(), key)
            return dyn.list_mine()
        self._bg(work, lambda rows: (self._on_mine_loaded(rows), self._reload_list(self._slug())))

    def _refresh_models(self):
        slug, args = self._slug(), self._conn_args()

        def work():
            ids = dyn.fetch_upstream_models(**args)
            dyn.push_models(slug, ids)
            return len(ids)
        self._bg(work, lambda n: self._models_lbl.setText(f"{n} models synced. They appear in the picker shortly."))

    def _submit(self):
        note, ok = QInputDialog.getText(self, "Submit for everyone",
                                        "Tell the Cortex team why this provider is useful (optional):")
        if not ok:
            return
        self._bg(lambda: (dyn.submit_mine(self._slug(), note), dyn.list_mine())[1], self._after_submit)

    def _after_submit(self, rows):
        self._on_mine_loaded(rows)
        QMessageBox.information(self, "Submitted",
                                "Thanks! Once an admin publishes it, every Cortex user gets it automatically.")

    def _delete(self):
        slug = self._slug()
        if QMessageBox.question(self, "Delete provider", f"Delete '{slug}' from all your devices?") \
                != QMessageBox.StandardButton.Yes:
            return
        self._bg(lambda: (dyn.delete_mine(slug), dyn.delete_key(slug), dyn.list_mine())[2],
                 lambda rows: (self._on_mine_loaded(rows), self._new_provider()))


def open_custom_providers_dialog(parent=None) -> None:
    CustomProvidersDialog(parent).exec()
