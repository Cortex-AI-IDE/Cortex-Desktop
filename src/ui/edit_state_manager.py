"""
edit_state_manager.py — Centralized File Edit State Manager
============================================================

Single source of truth for all file-edit state during a turn.
Synchronizes DiffCard <-> EditedFileRow <-> Changed Files section.

Supports MULTIPLE edits to the same file — all DiffCards for a file
are linked together. Accept/Reject one → all update.

One instance per ChatPanel turn. Created fresh in begin_assistant_turn,
resolved/consumed in on_turn_done.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

import PyQt6.sip as sip
from PyQt6.QtCore import QObject, pyqtSignal

if TYPE_CHECKING:
    from src.ui.chat_panel import DiffCard, EditedFileRow


class EditStateManager(QObject):
    """
    Single source of truth for all file-edit state during a turn.
    Synchronizes DiffCard <-> EditedFileRow <-> Changed Files section.

    Supports MULTIPLE edits to the same file — all DiffCards for a file
    are linked together. Accept/Reject one → all update.
    """
    # Signal emitted when file changes should be applied to disk + editor opened
    file_accepted = pyqtSignal(str, object)   # filename, hunk_lines
    file_rejected = pyqtSignal(str)            # filename
    state_changed = pyqtSignal()               # any file state change (for header updates)

    def __init__(self, parent=None):
        super().__init__(parent)
        # {filename: {"status": "pending"|"accepted"|"rejected",
        #              "added": int, "removed": int, "hunk_lines": list,
        #              "diff_cards": [DiffCard, ...], "ef_rows": [EditedFileRow, ...]}}
        self._files: dict[str, dict] = {}

    def register_diff_card(self, filename: str, added: int, removed: int,
                           hunk_lines: list, diff_card: "DiffCard"):
        """Register a DiffCard for a file. Supports multiple cards per file."""
        if diff_card is None:
            return
        if filename not in self._files:
            self._files[filename] = {
                "status": "pending", "added": added, "removed": removed,
                "hunk_lines": hunk_lines, "diff_cards": [diff_card], "ef_rows": []
            }
        else:
            # Clean stale DiffCard references (widgets from prior turns may be deleted)
            self._files[filename]["diff_cards"] = [
                c for c in self._files[filename]["diff_cards"]
                if c is not None and not sip.isdeleted(c)
            ]
            # Append new card
            self._files[filename]["diff_cards"].append(diff_card)
            self._files[filename]["added"] += added
            self._files[filename]["removed"] += removed
            self._files[filename]["hunk_lines"].extend(hunk_lines)
            # Reset to pending so new-round edits start fresh
            old_status = self._files[filename]["status"]
            self._files[filename]["status"] = "pending"
            # Emit if status actually changed (was accepted/rejected before)
            if old_status != "pending":
                self.state_changed.emit()

    def register_ef_row(self, filename: str, added: int, removed: int,
                        hunk_lines: list, ef_row: "EditedFileRow"):
        """Register an EditedFileRow for a file. Supports multiple rows per file."""
        if ef_row is None:
            return
        if filename not in self._files:
            self._files[filename] = {
                "status": "pending", "added": added, "removed": removed,
                "hunk_lines": hunk_lines, "diff_cards": [], "ef_rows": [ef_row]
            }
        else:
            # Clean stale ef_row references
            self._files[filename]["ef_rows"] = [
                r for r in self._files[filename]["ef_rows"]
                if r is not None and not sip.isdeleted(r)
            ]
            self._files[filename]["ef_rows"].append(ef_row)
            # Reset to pending so new-round edits start fresh
            old_status = self._files[filename]["status"]
            self._files[filename].setdefault("added", 0)
            self._files[filename]["added"] += added
            self._files[filename]["removed"] += removed
            if hunk_lines:
                self._files[filename].setdefault("hunk_lines", [])
                self._files[filename]["hunk_lines"].extend(hunk_lines)
            self._files[filename]["status"] = "pending"
            if old_status != "pending":
                self.state_changed.emit()

    @staticmethod
    def _detect_content_duplication(preview: str, original: str | None = None) -> tuple[bool, str]:
        """Detect if NEW content has internal duplication (same heading/title repeated).
        Returns (is_duplicated, warning_message).
        Prevents accidental concatenation when AI regenerates documents repeatedly.

        If ORIGINAL content is provided (existing file being edited), only flags
        duplication if the preview count EXCEEDS the original count by >1.
        This avoids false positives when editing files that already have standard
        headings (e.g. project index, README, documentation files).

        Skips separator/decorator comments (e.g. # ========, # --------, # ********)
        which legitimately appear many times in source files.
        """
        lines = preview.split("\n")
        if len(lines) < 10:
            return False, ""

        # --- Size-based guard: detect massive content multiplication ---
        # If preview is >2x original size, it's almost certainly content duplication,
        # even if no single heading repeats. The AI agent sometimes regenerates
        # entire file sections, causing ballooning without triggering heading checks.
        if original is not None:
            original_lines = original.split("\n")
            original_line_count = len(original_lines)
            preview_line_count = len(lines)
            new_lines_added = preview_line_count - original_line_count
            size_ratio = preview_line_count / max(original_line_count, 1)
            # Block: 5x+ size increase OR 2000+ new lines added
            # (relaxed from 2x/500 to allow legitimate file expansions)
            if size_ratio >= 5.0 or new_lines_added > 2000:
                return True, (
                    f"CONTENT MULTIPLICATION DETECTED: Preview is {preview_line_count} lines "
                    f"(original was {original_line_count}, +{new_lines_added} added, "
                    f"{size_ratio:.1f}x growth). "
                    f"Accept REJECTED to prevent file multiplication. "
                    f"Review the diff content before accepting."
                )

        def _is_separator_line(stripped: str) -> bool:
            """Check if a # comment line is a decorator/separator, not a real heading."""
            if not stripped.startswith("# "):
                return False
            body = stripped[2:].strip()  # text after "# "
            if not body:
                return True
            # If body is 60%+ repeated non-alphanumeric chars, it's a separator
            non_alpha = sum(1 for c in body if not c.isalnum() and not c.isspace())
            if len(body) > 3 and non_alpha / len(body) > 0.6:
                return True
            return False

        # Find the first real heading (skip separator/decorator lines)
        first_heading = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("# ") and len(stripped) > 5:
                if not _is_separator_line(stripped):
                    first_heading = stripped
                    break
        if not first_heading:
            return False, ""

        # Count occurrences in preview
        preview_count = sum(1 for line in lines if line.strip() == first_heading)

        # If we have the original content, only flag NEW duplication
        if original is not None:
            original_lines = original.split("\n")
            original_count = sum(1 for line in original_lines if line.strip() == first_heading)
            new_occurrences = preview_count - original_count
            # Only block if the NEW content adds 2+ duplicate headings
            if new_occurrences > 1:
                return True, (
                    f"CONTENT DUPLICATION DETECTED: The heading '{first_heading[:60]}' "
                    f"appears {new_occurrences} NEW times (was {original_count} in original). "
                    f"Accept REJECTED to prevent file multiplication. "
                    f"Review the diff content before accepting."
                )
            return False, ""

        # No original content (new file) — check absolute count
        if preview_count > 2:
            return True, (
                f"CONTENT DUPLICATION DETECTED: The heading '{first_heading[:60]}' "
                f"appears {preview_count} times. "
                f"Accept REJECTED to prevent file multiplication. "
                f"Review the diff content before accepting."
            )
        return False, ""

    def _preview_hunks(self, filename: str) -> tuple[str | None, str | None]:
        """Apply hunks to current file content and return (preview, original).
        Returns (None, None) if file can't be read.
        The original content is needed by _detect_content_duplication to compare
        heading counts before vs after the edit.
        """
        import os
        if not os.path.exists(filename):
            return None, None
        try:
            with open(filename, "r", encoding="utf-8") as f:
                original = f.read()
        except Exception:
            return None, None
        original = original.replace("\r\n", "\n")
        preview = original
        for hunk in self._files.get(filename, {}).get("hunk_lines", []):
            if hasattr(hunk, "apply"):
                preview = hunk.apply(preview)
            elif isinstance(hunk, dict):
                old = hunk.get("old_string", hunk.get("original", ""))
                new = hunk.get("new_string", hunk.get("modified", ""))
                replace_all = hunk.get("replace_all", False)
                if replace_all:
                    preview = preview.replace(old, new)
                elif old in preview:
                    preview = preview.replace(old, new, 1)
        return preview, original

    def accept(self, filename: str) -> bool:
        """Accept ALL edits for a file. Returns True if state changed."""
        import logging
        log = logging.getLogger(__name__)
        
        if filename not in self._files:
            log.warning(f"[EDIT-STATE] accept: filename '{filename}' not found in _files")
            return False
        entry = self._files[filename]
        if entry["status"] != "pending":
            log.info(f"[EDIT-STATE] accept: filename '{filename}' already {entry['status']}")
            return False

        # --- Duplication guard: preview-apply hunks and check for multiplied content ---
        preview, original_content = self._preview_hunks(filename)
        if preview is not None:
            is_dup, dup_msg = self._detect_content_duplication(preview, original_content)
            if is_dup:
                log.error(f"[EDIT-STATE] accept BLOCKED for '{filename}': {dup_msg}")
                entry["status"] = "rejected"
                # Update DiffCards & EditedFileRows to show rejected status
                for card in entry["diff_cards"]:
                    if card is not None and not sip.isdeleted(card):
                        card._apply_rejected_from_manager()
                for row in entry["ef_rows"]:
                    if row is not None and not sip.isdeleted(row):
                        row._apply_rejected_from_manager()
                self.state_changed.emit()
                self.file_rejected.emit(filename)
                return False

        entry["status"] = "accepted"
        # Update ALL DiffCards for this file
        log.info(f"[EDIT-STATE] accept: updating {len(entry['diff_cards'])} DiffCards for '{filename}'")
        for card in entry["diff_cards"]:
            if card is not None and not sip.isdeleted(card):
                card._apply_accepted_from_manager()
        # Update ALL EditedFileRows for this file
        log.info(f"[EDIT-STATE] accept: updating {len(entry['ef_rows'])} EditedFileRows for '{filename}'")
        for row in entry["ef_rows"]:
            if row is not None and not sip.isdeleted(row):
                row._apply_accepted_from_manager()
        self.state_changed.emit()
        self.file_accepted.emit(filename, entry["hunk_lines"])
        log.info(f"[EDIT-STATE] accept: '{filename}' completed")
        return True

    def reject(self, filename: str) -> bool:
        """Reject ALL edits for a file. Returns True if state changed."""
        if filename not in self._files:
            return False
        entry = self._files[filename]
        if entry["status"] != "pending":
            return False
        entry["status"] = "rejected"
        # Update ALL DiffCards for this file
        for card in entry["diff_cards"]:
            if card is not None and not sip.isdeleted(card):
                card._apply_rejected_from_manager()
        # Update ALL EditedFileRows for this file
        for row in entry["ef_rows"]:
            if row is not None and not sip.isdeleted(row):
                row._apply_rejected_from_manager()
        self.state_changed.emit()
        self.file_rejected.emit(filename)
        return True

    def accept_all(self) -> int:
        """Accept all pending files. Returns count accepted."""
        import logging
        log = logging.getLogger(__name__)
        
        count = 0
        log.info(f"[EDIT-STATE] accept_all: {len(self._files)} files total")
        for filename in list(self._files.keys()):
            if self._files[filename]["status"] == "pending":
                log.info(f"[EDIT-STATE] accept_all: accepting '{filename}'")
                if self.accept(filename):
                    count += 1
        log.info(f"[EDIT-STATE] accept_all: {count} files accepted")
        return count

    def reject_all(self) -> int:
        """Reject all pending files. Returns count rejected."""
        count = 0
        for filename in list(self._files.keys()):
            if self._files[filename]["status"] == "pending":
                if self.reject(filename):
                    count += 1
        return count

    @property
    def file_count(self) -> int:
        return len(self._files)

    @property
    def accepted_count(self) -> int:
        return sum(1 for e in self._files.values() if e["status"] == "accepted")

    @property
    def rejected_count(self) -> int:
        return sum(1 for e in self._files.values() if e["status"] == "rejected")

    @property
    def pending_count(self) -> int:
        return sum(1 for e in self._files.values() if e["status"] == "pending")

    @property
    def aggregate_status(self) -> str:
        """Aggregate status: 'Accepted' | 'Rejected' | 'Partially Accepted' | 'Pending'"""
        total = self.file_count
        if total == 0:
            return "Pending"
        acc = self.accepted_count
        rej = self.rejected_count
        if acc == total:
            return "Accepted"
        if rej == total:
            return "Rejected"
        if acc > 0 or rej > 0:
            return "Partially Accepted"
        return "Pending"
