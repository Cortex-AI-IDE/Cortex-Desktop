"""
Web-based Memory Manager dialog for Cortex IDE.

Hosts a QWebEngineView that renders the memory manager UI from
src/ui/html/memory_manager/memory_management.html and exposes a small
QWebChannel bridge for memory CRUD actions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List

from PyQt6.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QVBoxLayout
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView

from src.utils.logger import get_logger

log = get_logger("memory_manager")

# Import cross-project memory manager
try:
    from src.agent.src.memdir.crossProjectMemory import get_cross_project_manager
    HAS_CROSS_PROJECT = True
except ImportError:
    HAS_CROSS_PROJECT = False


def _parse_frontmatter(content: str):
    """Return (frontmatter_dict, body_text)."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    fm_text = content[3:end].strip()
    body = content[end + 4 :].strip()
    fm: dict = {}
    for line in fm_text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
            value = value[1:-1]
        fm[key] = value
    log.debug(f"[MemoryManager] Parsed frontmatter: {fm}")
    return fm, body


def _compute_memory_dir(project_root: str) -> str:
    """Return the memory directory INSIDE the project at <project>/.cortex/memory/"""
    return os.path.join(project_root, ".cortex", "memory")

def _compute_global_memory_dir() -> str:
    try:
        from src.agent.src.memdir.paths import getGlobalMemPath
        return getGlobalMemPath().rstrip("/\\")
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".cortex", "global", "memory")


def _compute_project_rules_dir(project_root: str) -> str:
    return os.path.join(project_root, ".cortex", "rules")


def _compute_global_rules_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cortex", "rules")


def _age_label(mtime: float) -> str:
    days = int((time.time() - mtime) / 86400)
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def _load_memories(memory_dir: str) -> List[dict]:
    memories: List[dict] = []
    if not os.path.isdir(memory_dir):
        return memories

    for root, _dirs, files in os.walk(memory_dir):
        for fname in files:
            if not fname.endswith(".md") or fname == "MEMORY.md":
                continue
            fpath = os.path.join(root, fname)
            try:
                mtime = os.path.getmtime(fpath)
                with open(fpath, encoding="utf-8") as handle:
                    raw = handle.read()
                fm, body = _parse_frontmatter(raw)
                mem_type = fm.get("type", "").strip()
                description = fm.get("description", "").strip()
                name = fm.get("name") or os.path.splitext(fname)[0]
                log.debug(f"[MemoryManager] Loaded memory: name='{name}', type='{mem_type}', file='{fname}'")
                memories.append(
                    {
                        "path": fpath,
                        "filename": os.path.relpath(fpath, memory_dir).replace("\\", "/"),
                        "name": name,
                        "description": description,
                        "type": mem_type,
                        "body": body,
                        "mtime": mtime,
                        "age": _age_label(mtime),
                        "stale": int((time.time() - mtime) / 86400) > 7,
                        "keywords": [part.strip() for part in description.split(",") if part.strip()],
                    }
                )
            except Exception as exc:
                log.warning(f"Failed to parse memory file {fpath}: {exc}")

    memories.sort(key=lambda item: item["mtime"], reverse=True)
    return memories


class _MemoryPage(QWebEnginePage):
    """Capture JS console messages for easier debugging."""

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        level_value = level.value if hasattr(level, "value") else int(level)
        # Route by level: INFO(0)→debug, WARN(1)→warning, ERROR(2+)→error
        # JS info messages too verbose for file — only warn/error go to file
        if level_value == 1:
            log.warning(f"[MEMORY_JS-WARN] {message}")
        elif level_value >= 2:
            log.error(f"[MEMORY_JS-ERROR] {message}")
        else:
            log.debug(f"[MEMORY_JS] {message}")

    def createWindow(self, window_type):
        """CAPSULE-FIX-R11: Prevent WebEngine from spawning native top-level
        windows (with title bar [−][□][X]). Return self to handle in-page."""
        log.warning("[CAPSULE-FIX-R11] MemoryManager.createWindow() intercepted — "
                    "preventing native window spawn")
        return self

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        """Open clicked external web links (e.g. the MCP "View plans" /
        pricing link) in the user's real browser instead of navigating the
        Settings webview away from its own page. The settings UI's own
        content (file://, qrc://, data:, about:) loads normally."""
        try:
            if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked \
                    and url.scheme().lower() in ("http", "https"):
                from PyQt6.QtGui import QDesktopServices
                QDesktopServices.openUrl(url)
                log.info(f"[MemoryManager] External link → system browser: {url.toString()}")
                return False  # block in-page navigation
        except Exception as e:
            log.warning(f"[MemoryManager] acceptNavigationRequest error: {e}")
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class MemoryManagerBridge(QObject):
    data_changed = pyqtSignal(str)
    toast_requested = pyqtSignal(str, str)
    model_changed = pyqtSignal(str, str)  # (model_id, model_label)
    login_complete = pyqtSignal(object)   # Emits user_info dict or None
    # (kind, payload_json) — fresh server data fetched off-thread by
    # refreshServerData(); kinds: "auth" | "profile" | "usage".
    server_data_ready = pyqtSignal(str, str)

    def __init__(self, project_root: str, settings=None, parent=None):
        super().__init__(parent)
        self._project_root = project_root or os.getcwd()
        self._settings = settings
        self._project_memory_dir = _compute_memory_dir(self._project_root)
        self._global_memory_dir = _compute_global_memory_dir()
        self._project_rules_dir = _compute_project_rules_dir(self._project_root)
        self._global_rules_dir = _compute_global_rules_dir()
        if self._settings:
            self._enabled = bool(self._settings.get("memory", "enabled", default=True))
            self._active_scope = str(self._settings.get("memory", "ui_scope", default="project") or "project")
        else:
            self._enabled = True
            self._active_scope = "project"

        if self._active_scope not in ("project", "global"):
            self._active_scope = "project"
        
        # Register login callback with auth manager (persists across dialog recreations)
        try:
            from src.core.auth_manager import get_auth_manager
            auth = get_auth_manager()
            auth.set_on_login_callback(self._on_login_complete)
        except Exception:
            pass
        
        # Initialize semantic search
        self._semantic_searcher = None
        self._init_semantic_search()
    
    def _init_semantic_search(self):
        """Initialize semantic search for current project."""
        try:
            from src.agent.src.memdir.semanticSearch import get_semantic_searcher
            self._semantic_searcher = get_semantic_searcher(self._project_memory_dir)
            log.info("[MemoryManager] Semantic search initialized")
        except Exception as e:
            log.warning(f"[MemoryManager] Semantic search unavailable: {e}")
            self._semantic_searcher = None

    def _get_scope_dir(self, scope: str) -> str:
        return self._global_memory_dir if scope == "global" else self._project_memory_dir

    def _get_rules_dir(self, scope: str) -> str:
        return self._global_rules_dir if scope == "global" else self._project_rules_dir

    def _get_rules_file(self, scope: str) -> str:
        return os.path.join(self._get_rules_dir(scope), "ide_rules.md")

    def _serialize_state(self) -> str:
        project_memories = _load_memories(self._project_memory_dir)
        global_memories = _load_memories(self._global_memory_dir)
        log.info(f"[MemoryManager] Serialized state: {len(project_memories)} project memories, {len(global_memories)} global memories")
        if project_memories:
            log.debug(f"[MemoryManager] First project memory: name='{project_memories[0].get('name', 'N/A')}', type='{project_memories[0].get('type', 'N/A')}'")
        payload = {
            "enabled": self._enabled,
            "activeScope": self._active_scope,
            "scopes": {
                "project": {
                    "name": "Current Project",
                    "projectRoot": self._project_root,
                    "memoryDir": self._project_memory_dir,
                    "rulesDir": self._project_rules_dir,
                    "memories": project_memories,
                },
                "global": {
                    "name": "Global",
                    "memoryDir": self._global_memory_dir,
                    "rulesDir": self._global_rules_dir,
                    "memories": global_memories,
                },
            },
        }
        return json.dumps(payload)

    def _emit_refresh(self):
        payload = self._serialize_state()
        self.data_changed.emit(payload)
        return payload

    def _remove_from_index(self, memory_dir: str, filename: str):
        index_path = os.path.join(memory_dir, "MEMORY.md")
        if not os.path.exists(index_path):
            return
        try:
            with open(index_path, encoding="utf-8") as handle:
                lines = handle.readlines()
            stem = os.path.splitext(filename)[0]
            new_lines = [line for line in lines if stem not in line and filename not in line]
            with open(index_path, "w", encoding="utf-8") as handle:
                handle.writelines(new_lines)
        except Exception as exc:
            log.warning(f"Failed to update memory index {index_path}: {exc}")

    @pyqtSlot(result=str)
    def loadInitialData(self):
        return self._serialize_state()

    @pyqtSlot(result=str)
    def getVersion(self):
        """Get the application version."""
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                return app.applicationVersion() or "2.8.0"
        except Exception:
            pass
        return "2.8.0"

    @pyqtSlot(result=str)
    def refresh(self):
        return self._emit_refresh()

    @pyqtSlot(str, result=str)
    def setActiveScope(self, scope: str):
        scope = (scope or "").strip().lower()
        if scope not in ("project", "global"):
            return self._emit_refresh()
        self._active_scope = scope
        if self._settings:
            self._settings.set("memory", "ui_scope", scope)
        return self._emit_refresh()

    @pyqtSlot(str, result=str)
    def openRulesDir(self, scope: str):
        scope = (scope or "").strip().lower()
        if scope == "shared":
            scope = "global"
        if scope not in ("project", "global"):
            scope = self._active_scope

        rules_dir = self._global_rules_dir if scope == "global" else self._project_rules_dir
        try:
            os.makedirs(rules_dir, exist_ok=True)
            
            # Create default AGENTS.md if it doesn't exist
            agents_file = os.path.join(rules_dir, "AGENTS.md")
            if not os.path.exists(agents_file):
                default_content = """---
name: coding-style
description: Python coding conventions for this project
priority: 10
scope: project
---
# Coding Rules

## Python Style
- Always use type hints in function signatures
- Use f-strings instead of .format()
- Follow PEP 8 naming conventions
- Write docstrings for public functions

## Error Handling
- Use specific exception types, not bare except
- Log errors with context before re-raising
- Always clean up resources in finally blocks

## Code Quality
- Keep functions under 50 lines
- One function = one responsibility
- Use constants for magic numbers
"""
                with open(agents_file, 'w', encoding='utf-8') as f:
                    f.write(default_content)
                log.info(f"[MemoryManager] Created default AGENTS.md at {agents_file}")
            
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(rules_dir))
            if not opened:
                raise RuntimeError("Could not open rules folder in file explorer")
            return json.dumps({"success": True, "scope": scope, "rulesDir": rules_dir})
        except Exception as exc:
            log.error(f"[MemoryManager] Failed to open rules dir '{rules_dir}': {exc}")
            return json.dumps({"error": str(exc), "scope": scope, "rulesDir": rules_dir})

    @pyqtSlot(str, result=str)
    def loadRules(self, scope: str):
        scope = (scope or "").strip().lower()
        if scope == "shared":
            scope = "global"
        if scope not in ("project", "global"):
            scope = self._active_scope
        try:
            rules_dir = self._get_rules_dir(scope)
            os.makedirs(rules_dir, exist_ok=True)
            target = self._get_rules_file(scope)
            content = ""
            if os.path.exists(target):
                with open(target, encoding="utf-8") as handle:
                    content = handle.read()
            return json.dumps(
                {
                    "success": True,
                    "scope": scope,
                    "rulesDir": rules_dir,
                    "filePath": target,
                    "content": content,
                }
            )
        except Exception as exc:
            log.error(f"[MemoryManager] Failed to load rules for scope '{scope}': {exc}")
            return json.dumps({"error": str(exc), "scope": scope})

    @pyqtSlot(str)
    def openExternal(self, url: str):
        """Open URL in system browser (external)."""
        try:
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(url))
            log.info(f"[MemoryManager] Opened external URL: {url}")
        except Exception as e:
            log.warning(f"[MemoryManager] Failed to open URL: {e}")

    @pyqtSlot(str, str, result=str)
    def saveRules(self, scope: str, content: str):
        scope = (scope or "").strip().lower()
        if scope == "shared":
            scope = "global"
        if scope not in ("project", "global"):
            scope = self._active_scope
        try:
            rules_dir = self._get_rules_dir(scope)
            os.makedirs(rules_dir, exist_ok=True)
            target = self._get_rules_file(scope)
            normalized = (content or "").rstrip() + "\n"
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(normalized)
            self.toast_requested.emit("success", f"Saved {scope} rules")
            return json.dumps(
                {
                    "success": True,
                    "scope": scope,
                    "rulesDir": rules_dir,
                    "filePath": target,
                }
            )
        except Exception as exc:
            log.error(f"[MemoryManager] Failed to save rules for scope '{scope}': {exc}")
            return json.dumps({"error": str(exc), "scope": scope})

    @pyqtSlot(object, result=str)
    def setMemoryEnabled(self, checked):
        self._enabled = bool(checked)
        if self._settings:
            self._settings.set("memory", "enabled", self._enabled)
        log.info(f"Memory enabled set to {self._enabled}")
        self.toast_requested.emit("success", "Memory generation updated")
        return self._emit_refresh()

    @pyqtSlot(str, str, result=str)
    def deleteMemory(self, scope: str, path: str):
        scope = (scope or "").strip().lower()
        if scope not in ("project", "global"):
            scope = self._active_scope
        memory_dir = self._get_scope_dir(scope)
        # Resolve relative path to absolute using memory_dir
        if path and not os.path.isabs(path):
            path = os.path.join(memory_dir, path)
        name = os.path.basename(path or "")
        log.info(f"[MemoryManager] deleteMemory: scope={scope}, resolved_path={path}")
        try:
            from src.utils.safe_delete import safe_delete
            result = safe_delete(path)
            if not result.get("success"):
                raise OSError(result.get("message", "Delete failed"))
            self._remove_from_index(memory_dir, name)
            self.toast_requested.emit("success", f"Deleted {name}")
        except OSError as exc:
            log.error(f"[MemoryManager] deleteMemory failed for '{path}': {exc}")
            self.toast_requested.emit("error", f"Delete failed: {exc}")
        return self._emit_refresh()

    @pyqtSlot(str, result=str)
    def clearAll(self, scope: str):
        scope = (scope or "").strip().lower()
        if scope not in ("project", "global"):
            scope = self._active_scope
        memory_dir = self._get_scope_dir(scope)
        errors = []
        from src.utils.safe_delete import safe_delete
        for memory in _load_memories(memory_dir):
            try:
                result = safe_delete(memory["path"])
                if not result.get("success"):
                    raise OSError(result.get("message", "Delete failed"))
            except OSError as exc:
                errors.append(str(exc))

        index_path = os.path.join(memory_dir, "MEMORY.md")
        try:
            if os.path.exists(index_path):
                result = safe_delete(index_path)
                if not result.get("success"):
                    raise OSError(result.get("message", "Delete failed"))
        except OSError as exc:
            errors.append(str(exc))

        if errors:
            self.toast_requested.emit("error", errors[0])
        else:
            self.toast_requested.emit("success", f"Cleared {scope} memories")
        return self._emit_refresh()
    
    @pyqtSlot(str, result=str)
    def semanticSearch(self, query: str):
        """Perform semantic search on memories."""
        query = (query or "").strip()
        if not query:
            return self._emit_refresh()
        
        if not self._semantic_searcher:
            self.toast_requested.emit("error", "Semantic search not available")
            return self._emit_refresh()
        
        try:
            # Perform semantic search
            results = self._semantic_searcher.search_memories(
                query, 
                self._project_memory_dir, 
                top_k=20
            )
            
            # Convert results to dict format for JSON
            search_results = [
                {
                    "path": r.file_path,
                    "filename": r.filename,
                    "name": r.title,
                    "description": r.description,
                    "type": r.memory_type,
                    "similarity_score": r.similarity_score,
                    "content_preview": r.content_preview,
                    "mtime": r.mtime,
                    "age": _age_label(r.mtime),
                    "stale": int((time.time() - r.mtime) / 86400) > 7,
                    "keywords": [part.strip() for part in r.description.split(",") if part.strip()],
                    "body": self._load_file_body(r.file_path),
                }
                for r in results
            ]
            
            log.info(f"[MemoryManager] Semantic search found {len(search_results)} results for '{query[:50]}...'")
            
            # Update scope with search results
            payload = {
                "enabled": self._enabled,
                "activeScope": self._active_scope,
                "searchQuery": query,
                "isSearchMode": True,
                "scopes": {
                    "project": {
                        "name": f"Search: {query[:30]}...",
                        "memoryDir": self._project_memory_dir,
                        "rulesDir": self._project_rules_dir,
                        "memories": search_results,
                    },
                    "global": {
                        "name": "Global",
                        "memoryDir": self._global_memory_dir,
                        "rulesDir": self._global_rules_dir,
                        "memories": [],
                    },
                },
            }
            
            return json.dumps(payload)
            
        except Exception as e:
            log.error(f"[MemoryManager] Semantic search failed: {e}", exc_info=True)
            self.toast_requested.emit("error", f"Semantic search failed: {e}")
            return self._emit_refresh()
    
    def _load_file_body(self, file_path: str) -> str:
        """Load file body content."""
        try:
            with open(file_path, encoding="utf-8") as handle:
                raw = handle.read()
            _, body = _parse_frontmatter(raw)
            return body[:500]  # Limit preview to 500 chars
        except Exception:
            return ""
    
    @pyqtSlot(result=str)
    def exitSearchMode(self):
        """Exit search mode and return to normal view."""
        return self._emit_refresh()
    
    @pyqtSlot(str, result=str)
    def getMemoryStats(self, scope: str):
        """Get memory statistics for dashboard."""
        scope = (scope or "").strip().lower()
        if scope not in ("project", "global"):
            scope = self._active_scope
        memory_dir = self._get_scope_dir(scope)
        
        memories = _load_memories(memory_dir)
        
        # Calculate stats
        type_counts = {}
        total_size = 0
        oldest_mtime = time.time()
        newest_mtime = 0
        
        for mem in memories:
            mem_type = mem.get("type", "unknown")
            type_counts[mem_type] = type_counts.get(mem_type, 0) + 1
            
            try:
                file_size = os.path.getsize(mem["path"])
                total_size += file_size
            except Exception:
                pass
            
            mtime = mem.get("mtime", 0)
            if mtime < oldest_mtime:
                oldest_mtime = mtime
            if mtime > newest_mtime:
                newest_mtime = mtime
        
        stats = {
            "total": len(memories),
            "type_counts": type_counts,
            "total_size_kb": round(total_size / 1024, 2),
            "oldest_age": _age_label(oldest_mtime) if oldest_mtime < time.time() else "N/A",
            "newest_age": _age_label(newest_mtime) if newest_mtime > 0 else "N/A",
            "stale_count": sum(1 for m in memories if m.get("stale", False)),
            "fresh_count": sum(1 for m in memories if not m.get("stale", False)),
        }
        
        return json.dumps(stats)
    
    @pyqtSlot(str, bool, result=str)
    def runConsolidation(self, scope: str, auto_merge: bool = False):
        """Run memory consolidation and deduplication."""
        scope = (scope or "").strip().lower()
        if scope not in ("project", "global"):
            scope = self._active_scope
        memory_dir = self._get_scope_dir(scope)
        
        if not os.path.isdir(memory_dir):
            self.toast_requested.emit("error", "Memory directory not found")
            return json.dumps({"error": "Memory directory not found"})
        
        try:
            log.info(f"[MemoryManager] Running consolidation on {memory_dir} (auto_merge={auto_merge})")
            self.toast_requested.emit("info", "Starting memory consolidation...")
            
            from src.agent.src.memdir.memoryConsolidation import MemoryConsolidator
            
            consolidator = MemoryConsolidator(memory_dir)
            report = consolidator.run_consolidation(auto_merge=auto_merge)
            
            # Convert report to JSON-serializable dict
            report_dict = {
                "total_memories_scanned": report.total_memories_scanned,
                "duplicates_found": report.duplicates_found,
                "memories_merged": report.memories_merged,
                "memories_deleted": report.memories_deleted,
                "space_saved_bytes": report.space_saved_bytes,
                "space_saved_kb": round(report.space_saved_bytes / 1024, 2),
                "timestamp": report.timestamp,
                "clusters": [
                    {
                        "cluster_id": c.cluster_id,
                        "memory_count": len(c.memories),
                        "recommended_action": c.recommended_action,
                        "memories": [
                            {
                                "filename": m.get("filename", ""),
                                "title": m.get("title", ""),
                                "file_path": m.get("file_path", ""),
                            }
                            for m in c.memories
                        ],
                    }
                    for c in report.clusters
                ],
            }
            
            log.info(f"[MemoryManager] Consolidation complete: {report.duplicates_found} duplicates found, {report.memories_merged} merged")
            
            if report.duplicates_found > 0:
                action_msg = f"Found {report.duplicates_found} duplicate groups"
                if auto_merge:
                    action_msg += f", merged {report.memories_merged} memories"
                self.toast_requested.emit("success", action_msg)
            else:
                self.toast_requested.emit("success", "No duplicates found - memories are clean!")
            
            # Refresh memory list after consolidation
            return json.dumps(report_dict)
            
        except Exception as e:
            log.error(f"[MemoryManager] Consolidation failed: {e}", exc_info=True)
            self.toast_requested.emit("error", f"Consolidation failed: {e}")
            return json.dumps({"error": str(e)})
    
    @pyqtSlot(result=str)
    def getGlobalMemories(self):
        """Get all global (cross-project) memories."""
        if not HAS_CROSS_PROJECT:
            return json.dumps({"error": "Cross-project memory not available"})
        
        try:
            manager = get_cross_project_manager()
            memories = manager.load_global_memories()
            
            memories_list = [
                {
                    "filename": m.filename,
                    "title": m.title,
                    "description": m.description,
                    "memory_type": m.memory_type,
                    "mtime": m.mtime,
                    "age": _age_label(m.mtime),
                    "content_preview": m.content[:300],
                }
                for m in memories
            ]
            
            return json.dumps({"memories": memories_list})
            
        except Exception as e:
            log.error(f"[MemoryManager] Failed to load global memories: {e}")
            return json.dumps({"error": str(e)})
    
    @pyqtSlot(str, str, str, result=str)
    def saveGlobalMemory(self, filename: str, title: str, content: str):
        """Save a memory to global (cross-project) scope."""
        if not HAS_CROSS_PROJECT:
            return json.dumps({"error": "Cross-project memory not available"})
        
        try:
            manager = get_cross_project_manager()
            
            metadata = {
                "title": title,
                "type": "user_preference",
                "created": datetime.now().isoformat(),
            }
            
            file_path = manager.save_global_memory(filename, content, metadata)
            
            log.info(f"[MemoryManager] Saved global memory: {filename}")
            self.toast_requested.emit("success", f"Saved global memory: {title}")
            
            return json.dumps({"success": True, "file_path": file_path})
            
        except Exception as e:
            log.error(f"[MemoryManager] Failed to save global memory: {e}")
            return json.dumps({"error": str(e)})
    
    @pyqtSlot(str, result=str)
    def deleteGlobalMemory(self, filename: str):
        """Delete a global memory."""
        if not HAS_CROSS_PROJECT:
            return json.dumps({"error": "Cross-project memory not available"})
        
        try:
            manager = get_cross_project_manager()
            success = manager.delete_global_memory(filename)
            
            if success:
                self.toast_requested.emit("success", f"Deleted global memory: {filename}")
                return json.dumps({"success": True})
            else:
                return json.dumps({"error": "Memory not found"})
            
        except Exception as e:
            log.error(f"[MemoryManager] Failed to delete global memory: {e}")
            return json.dumps({"error": str(e)})
    
    @pyqtSlot(str, bool, result=str)
    def syncGlobalMemoriesToProject(self, project_root: str, auto_merge: bool = True):
        """Sync global memories to a project."""
        if not HAS_CROSS_PROJECT:
            return json.dumps({"error": "Cross-project memory not available"})
        
        try:
            manager = get_cross_project_manager()
            report = manager.sync_memories_to_project(project_root, auto_merge)
            
            report_dict = {
                "global_memories_loaded": report.global_memories_loaded,
                "project_memories_loaded": report.project_memories_loaded,
                "conflicts_resolved": report.conflicts_resolved,
                "merged_memory_path": report.merged_memory_path,
            }
            
            log.info(f"[MemoryManager] Synced global memories to project: {project_root}")
            self.toast_requested.emit(
                "success",
                f"Synced {report.global_memories_loaded} global memories"
            )
            
            return json.dumps(report_dict)
            
        except Exception as e:
            log.error(f"[MemoryManager] Failed to sync global memories: {e}")
            return json.dumps({"error": str(e)})
    
    @pyqtSlot(str, result=str)
    def promoteToGlobal(self, memory_path: str):
        """Promote a project memory to global scope."""
        if not HAS_CROSS_PROJECT:
            return json.dumps({"error": "Cross-project memory not available"})
        
        try:
            manager = get_cross_project_manager()
            
            # Extract filename from path
            filename = os.path.basename(memory_path)
            project_root = self._project_memory_dir
            
            # Find project root from memory dir
            if project_root.endswith("/memory") or project_root.endswith("\\memory"):
                project_root = project_root.rsplit(os.sep + "memory", 1)[0]
            
            result = manager.share_project_memory(project_root, filename, promote_to_global=True)
            
            if result:
                self.toast_requested.emit("success", f"Promoted {filename} to global memory")
                return json.dumps({"success": True, "global_path": result})
            else:
                return json.dumps({"error": "Failed to promote memory"})
            
        except Exception as e:
            log.error(f"[MemoryManager] Failed to promote memory: {e}")
            return json.dumps({"error": str(e)})

    # ═══════════════════════════════════════════════════════════════
    # SETTINGS BRIDGE (JS calls these for settings persistence)
    # ═══════════════════════════════════════════════════════════════

    @pyqtSlot(result=str)
    def getState(self) -> str:
        """Alias for loadInitialData — JS calls bridge.getState()."""
        return self.loadInitialData()

    # API key fields that should be stored in KeyManager (encrypted)
    _API_KEY_FIELDS = {
        "ai.openai_key":       "openai",
        "ai.deepseek_key":     "deepseek",
        "ai.mistral_key":      "mistral",
        "ai.mimo_key":         "mimo",
        "ai.openrouter_key":   "openrouter",
        "ai.alibaba_key":      "alibaba",
        "ai.kimi_key":         "kimi",
        "ai.siliconflow_key":  "siliconflow",
    }

    @pyqtSlot(str, str)
    def setSetting(self, key: str, value: str):
        """Persist a single setting by dotted path (e.g. 'editor.font_size').

        API key fields (ai.*_key) are stored encrypted via KeyManager instead
        of plain JSON.  All other settings go to ~/.cortex/settings.json.
        """
        try:
            if self._settings:
                # Parse dotted path into section / setting_key
                if "." in key:
                    section, setting_key = key.split(".", 1)
                else:
                    section, setting_key = "ui", key

                # ── API Key: store encrypted via KeyManager ──
                km_provider = self._API_KEY_FIELDS.get(key)
                if km_provider is not None:
                    self._store_api_key(km_provider, value)
                    # Also store a placeholder in settings (so UI knows a key exists)
                    self._settings.set(section, setting_key, "***")
                    log.info(f"[MemoryManager] API key for {km_provider} stored in KeyManager")
                    return

                # Coerce value to the right Python type
                low = value.lower()
                if low in ("true", "false"):
                    coerced: object = low == "true"
                else:
                    try:
                        coerced = int(value)
                    except (ValueError, TypeError):
                        try:
                            coerced = float(value)
                        except (ValueError, TypeError):
                            coerced = value

                self._settings.set(section, setting_key, coerced)
                log.info(f"[MemoryManager] setSetting({section}.{setting_key} = {coerced!r})")
        except Exception as e:
            log.warning(f"[MemoryManager] setSetting failed: {e}")

    @pyqtSlot(str)
    def setTheme(self, theme: str):
        """Switch the entire IDE theme (dark/light/system).

        Called from JS when the user clicks a theme option in
        Settings → Appearance → Theme picker.

        Bug history: this whitelisted only ("dark", "light"), so selecting
        "System" silently coerced to "dark" before it ever reached
        _set_theme() / ThemeManager — "System" mode was unreachable and
        always forced dark regardless of the actual OS preference.
        """
        theme = theme if theme in ("dark", "light", "system") else "dark"
        log.info(f"[MemoryManager] setTheme called: {theme}")

        # Persist to settings (raw value, including "system")
        if self._settings:
            self._settings.theme = theme

        # Notify main window to apply theme globally
        try:
            from PyQt6.QtWidgets import QApplication as _App
            # Find the main window and call _set_theme
            for widget in _App.instance().topLevelWidgets():
                if hasattr(widget, '_set_theme') and callable(widget._set_theme):
                    widget._set_theme(theme)
                    log.info(f"[MemoryManager] Theme '{theme}' applied via main window")
                    break
        except Exception as e:
            log.warning(f"[MemoryManager] Could not apply theme globally: {e}")

    @pyqtSlot(result=str)
    def getTheme(self) -> str:
        """Return the RAW theme setting ('dark', 'light', or 'system').

        Used by the JS theme picker to highlight which button is active.
        Use getResolvedTheme() for the actual dark/light appearance to draw
        — "system" is not a CSS state and must never be passed to
        data-theme directly (it matches no CSS rule and silently falls back
        to whatever the default looks like, independent of OS preference).
        """
        try:
            from src.config.theme_manager import get_theme_manager
            return get_theme_manager().current
        except Exception:
            return "dark"

    @pyqtSlot(result=str)
    def getResolvedTheme(self) -> str:
        """Return the ACTUAL appearance to draw: always 'dark' or 'light',
        never 'system'. When the raw setting is 'system', resolves it via
        the OS preference (same logic main_window uses for every other
        panel) so this settings page matches the rest of the IDE."""
        try:
            from src.config.theme_manager import get_theme_manager
            return "dark" if get_theme_manager().is_dark else "light"
        except Exception:
            return "dark"

    @pyqtSlot(str, str)
    def setDefaultModel(self, model_id: str, model_label: str):
        """Update the default model and notify the chat panel to sync its button."""
        try:
            if self._settings:
                self._settings.set("ai", "model", model_id)
                self._settings.set("ai", "model_label", model_label)
            self.model_changed.emit(model_id, model_label)
            log.info(f"[MemoryManager] Default model changed to: {model_id} ({model_label})")
        except Exception as e:
            log.warning(f"[MemoryManager] setDefaultModel failed: {e}")

    # ── Profile & Usage Bridge Methods ────────────────────────────

    @pyqtSlot(result=str)
    def getProfile(self) -> str:
        """Return profile data as JSON string. Merges server + local data."""
        try:
            from src.ai.usage_tracker import get_usage_tracker
            tracker = get_usage_tracker()
            local_profile = tracker.get_profile()
            
            # Merge CACHED server profile (api.user_info) — INSTANT, no
            # network. This slot runs on the GUI thread via QWebChannel;
            # the inline api.get_profile() it used to make froze the whole
            # app per call. Fresh data arrives via refreshServerData().
            try:
                from src.core.cortex_api import get_api_client
                api = get_api_client()
                if api.is_logged_in():
                    server_profile = api.user_info or {}
                    if server_profile:
                        # Merge server data into local
                        local_profile["profile"]["display_name"] = server_profile.get("display_name", local_profile["profile"].get("display_name"))
                        local_profile["profile"]["email"] = server_profile.get("email", local_profile["profile"].get("email"))
                        local_profile["auth"]["logged_in"] = True
                        local_profile["auth"]["email"] = server_profile.get("email")
                        local_profile["auth"]["has_subscription"] = server_profile.get("has_subscription", False)
                        local_profile["auth"]["plan"] = server_profile.get("plan")
                        local_profile["auth"]["plan_display"] = server_profile.get("plan_display")
                        local_profile["auth"]["subscription_status"] = server_profile.get("subscription_status")
            except Exception as e:
                log.debug(f"[MemoryManager] Cached profile merge failed: {e}")
            
            return json.dumps(local_profile)
        except Exception as e:
            log.warning(f"[MemoryManager] getProfile failed: {e}")
            return "{}"

    @pyqtSlot(result=str)
    def getUsageStats(self) -> str:
        """Return usage stats as JSON string. Merges server + local data."""
        try:
            from src.ai.usage_tracker import get_usage_tracker
            tracker = get_usage_tracker()
            local_stats = tracker.get_usage_stats()
            
            # Merge CACHED server usage — INSTANT, no network. The inline
            # api.get_usage_summary() this slot used to make ran on the GUI
            # thread and froze the app; refreshServerData("usage") now
            # fetches on a worker thread and fills _server_usage_cache.
            from src.core.cortex_api import get_api_client
            api = get_api_client()
            if api.is_logged_in():
                try:
                    server_usage = getattr(self, "_server_usage_cache", None)
                    if server_usage:
                        local_stats["server"] = {
                            "subscription": server_usage.get("subscription", {}),
                            "credits": server_usage.get("credits", {}),
                            "usage": server_usage.get("usage", {}),
                        }
                        # Service Usage (OCR / embeddings / web search) must
                        # show the ACCOUNT-wide numbers the server metered —
                        # the same source the website /account/usage/ page
                        # reads. Bug history (user screenshots, 2026-07-13):
                        # this panel showed per-device local JSON counters
                        # (6 OCR / 14,970 embeddings / 0 searches) while the
                        # site showed the account truth (14 / real tokens /
                        # 8) — permanently out of sync for anyone using
                        # Cortex on more than one machine. Local counters
                        # remain the offline/logged-out fallback below.
                        _services = (server_usage.get("usage", {}) or {}).get("services") or {}
                        if _services:
                            _cp = local_stats.setdefault("current_period", {})
                            _cp["ocr_pages_used"] = _services.get(
                                "ocr_pages", _cp.get("ocr_pages_used", 0))
                            _cp["embedding_tokens_used"] = _services.get(
                                "embedding_tokens", _cp.get("embedding_tokens_used", 0))
                            _cp["web_searches_used"] = _services.get(
                                "web_searches", _cp.get("web_searches_used", 0))
                except Exception as e:
                    log.debug(f"[MemoryManager] Server usage fetch failed: {e}")
            else:
                # Not logged in - clear any cached server data
                local_stats.pop("server", None)
            
            return json.dumps(local_stats)
        except Exception as e:
            log.warning(f"[MemoryManager] getUsageStats failed: {e}")
            return "{}"

    @pyqtSlot(str)
    def setProfile(self, data_json: str):
        """Update profile fields from JSON. Syncs to server if logged in."""
        try:
            from src.ai.usage_tracker import get_usage_tracker
            tracker = get_usage_tracker()
            data = json.loads(data_json)
            tracker.update_profile_bulk(data)
            log.info(f"[MemoryManager] Profile updated: {list(data.keys())}")
            
            # Sync to server if logged in
            try:
                from src.core.cortex_api import get_api_client
                api = get_api_client()
                if api.is_logged_in():
                    server_data = {}
                    if "display_name" in data:
                        server_data["display_name"] = data["display_name"]
                    if "email" in data:
                        server_data["email"] = data["email"]
                    if server_data:
                        api.update_profile(server_data)
            except Exception as e:
                log.debug(f"[MemoryManager] Server profile sync failed: {e}")
        except Exception as e:
            log.warning(f"[MemoryManager] setProfile failed: {e}")

    # ── Auth Bridge Methods ───────────────────────────────────────

    @pyqtSlot(result=str)
    def getAuthStatus(self) -> str:
        """Return auth status as JSON string — INSTANT, memory-only.

        Bug history (the "Settings feels frozen" report): this slot used to
        fetch api.get_profile() inline. QWebChannel slots execute ON the
        GUI thread, so every Settings open blocked the whole app for a
        network round-trip (15s timeout worst case) — and getProfile +
        getUsageStats did the same, serially. Fresh server data now comes
        from refreshServerData() on a worker thread; this returns the
        cached api.user_info immediately."""
        try:
            from src.core.cortex_api import get_api_client
            api = get_api_client()
            return json.dumps({
                "logged_in": api.is_logged_in(),
                "user": api.user_info or {},
                "server_url": api.base_url,
            })
        except Exception as e:
            log.warning(f"[MemoryManager] getAuthStatus failed: {e}")
            return json.dumps({"logged_in": False, "error": str(e)})

    @pyqtSlot(str)
    def refreshServerData(self, kind: str):
        """Fetch fresh server data on a WORKER thread, then signal the page.

        kind="account" → one get_profile() fetch, merged into api.user_info,
        then emits ("auth", …) and ("profile", …) payloads.
        kind="usage"   → get_usage_summary() into the usage cache, then
        emits ("usage", …).

        The emitted payloads are produced by the same (now cache-only) sync
        getters the page already renders from, so the JS needs no new
        parsing — it just re-runs its existing loaders on the signal."""
        def _work():
            try:
                from src.core.cortex_api import get_api_client
                api = get_api_client()
                if not api.is_logged_in():
                    return
                if kind == "account":
                    sp = api.get_profile()
                    if sp:
                        info = dict(api.user_info or {})
                        info.update(sp)
                        api.user_info = info
                    self.server_data_ready.emit("auth", self.getAuthStatus())
                    self.server_data_ready.emit("profile", self.getProfile())
                elif kind == "usage":
                    su = api.get_usage_summary()
                    if su:
                        self._server_usage_cache = su
                    self.server_data_ready.emit("usage", self.getUsageStats())
            except Exception as e:
                log.debug(f"[MemoryManager] refreshServerData({kind}) failed: {e}")
        import threading
        threading.Thread(target=_work, daemon=True,
                         name=f"SettingsRefresh-{kind}").start()

    @pyqtSlot(result=bool)
    def startLogin(self) -> bool:
        """Start OAuth2 login flow. Opens browser."""
        try:
            from src.core.auth_manager import get_auth_manager
            auth = get_auth_manager()
            auth.set_on_login_callback(self._on_login_complete)
            return auth.start_login(use_browser=True)
        except Exception as e:
            log.error(f"[MemoryManager] startLogin failed: {e}")
            return False

    @pyqtSlot(str, str, result=bool)
    def loginWithCredentials(self, email: str, password: str) -> bool:
        """Direct login with email + password."""
        try:
            from src.core.auth_manager import get_auth_manager
            auth = get_auth_manager()
            auth.set_on_login_callback(self._on_login_complete)
            return auth.login_with_credentials(email, password)
        except Exception as e:
            log.error(f"[MemoryManager] loginWithCredentials failed: {e}")
            return False

    @pyqtSlot(result=bool)
    def logout(self) -> bool:
        """Logout and clear tokens."""
        try:
            from src.core.auth_manager import get_auth_manager
            auth = get_auth_manager()
            auth.logout()
            return True
        except Exception as e:
            log.error(f"[MemoryManager] logout failed: {e}")
            return False

    def _on_login_complete(self, user_info):
        """Callback when login completes — emit signal for main thread UI update."""
        log.info(f"[MemoryManager] Login complete: {user_info}")
        # Emit signal to safely update UI on main thread
        self.login_complete.emit(user_info)

    @pyqtSlot(str, str, str, result=str)
    def getUsageForRange(self, start_date: str, end_date: str, granularity: str) -> str:
        """Return usage data for a date range (daily/weekly/cumulative)."""
        try:
            from src.ai.usage_tracker import get_usage_tracker
            tracker = get_usage_tracker()
            data = tracker.get_usage_for_range(start_date, end_date, granularity)
            return json.dumps(data)
        except Exception as e:
            log.warning(f"[MemoryManager] getUsageForRange failed: {e}")
            return json.dumps({"error": str(e)})

    def _store_api_key(self, provider: str, api_key: str):
        """Store an API key in KeyManager (encrypted) and hot-reload the provider."""
        try:
            # Sanitize the key before storing (strip null bytes, spaces, quotes)
            if api_key:
                api_key = api_key.replace('\x00', '').replace('\u0000', '').replace(' ', '').replace('\n', '').replace('\r', '').strip().strip("'\"")
            from src.core.key_manager import get_key_manager
            km = get_key_manager()
            success = km.store_key(provider, api_key)
            if success:
                log.info(f"[MemoryManager] Encrypted key stored for {provider}")
                # Hot-reload: update the live provider instance
                self._reload_provider_key(provider, api_key)
            else:
                log.warning(f"[MemoryManager] Failed to store key for {provider}")
        except Exception as e:
            log.warning(f"[MemoryManager] _store_api_key error: {e}")

    def _reload_provider_key(self, provider: str, api_key: str):
        """Push new API key into the live provider instance (no restart needed)."""
        try:
            from src.ai.providers import get_provider_registry, ProviderType
            registry = get_provider_registry()
            provider_map = {
                "openai":     ProviderType.OPENAI,
                "deepseek":   ProviderType.DEEPSEEK,
                "mistral":    ProviderType.MISTRAL,
                "mimo":       ProviderType.MIMO,
                "openrouter": ProviderType.OPENROUTER,
                "alibaba":    ProviderType.ALIBABA,
                "siliconflow":ProviderType.SILICONFLOW,
                "anthropic":  ProviderType.ANTHROPIC,
            }
            pt = provider_map.get(provider)
            if pt:
                # Access _providers dict directly to avoid _ensure_provider()
                # which re-imports the module (fails in frozen builds if module
                # was garbage-collected after background thread exit)
                p = registry._providers.get(pt)
                if p is None:
                    # Provider not yet registered — try get_provider as fallback
                    try:
                        p = registry.get_provider(pt)
                    except Exception:
                        log.debug(f"[MemoryManager] Provider {provider} not available for hot-reload")
                        return
                if p and hasattr(p, 'set_api_key'):
                    p.set_api_key(api_key)
                    log.info(f"[MemoryManager] Hot-reloaded key for {provider}")
        except Exception as e:
            log.debug(f"[MemoryManager] Hot-reload skipped: {e}")

    @pyqtSlot(str, result=str)
    def getApiKey(self, provider: str) -> str:
        """Get a stored API key (masked for display). Returns empty string if not found."""
        try:
            from src.core.key_manager import get_key_manager
            km = get_key_manager()
            key = km.get_key(provider)
            if key:
                return key
            return ""
        except Exception as e:
            log.warning(f"[MemoryManager] getApiKey error: {e}")
            return ""

    @pyqtSlot(str, str, result=bool)
    def setApiKey(self, provider: str, api_key: str) -> bool:
        """Store an API key securely."""
        try:
            self._store_api_key(provider, api_key)
            return True
        except Exception as e:
            log.warning(f"[MemoryManager] setApiKey error: {e}")
            return False

    @pyqtSlot(str, result=bool)
    def removeApiKey(self, provider: str) -> bool:
        """Remove a stored API key."""
        log.info(f"[MemoryManager] removeApiKey called for {provider}")
        try:
            from src.core.key_manager import get_key_manager
            km = get_key_manager()
            
            # Check if key exists first
            existing_key = km.get_key(provider)
            log.info(f"[MemoryManager] Key exists for {provider}: {bool(existing_key)}")
            
            success = km.delete_key(provider)
            log.info(f"[MemoryManager] delete_key result for {provider}: {success}")
            
            if success:
                # Also clear from live provider
                self._reload_provider_key(provider, "")
                
                # Clear settings placeholder
                settings_map = {
                    "mimo": ("ai", "mimo_key"),
                    "deepseek": ("ai", "deepseek_key"),
                    "openai": ("ai", "openai_key"),
                    "openrouter": ("ai", "openrouter_key"),
                    "alibaba": ("ai", "alibaba_key"),
                    "siliconflow": ("ai", "siliconflow_key"),
                    "mistral": ("ai", "mistral_key"),
                    "anthropic": ("ai", "anthropic_key"),
                }
                if provider in settings_map and self._settings:
                    section, key = settings_map[provider]
                    self._settings.set(section, key, "")
                    log.info(f"[MemoryManager] Cleared settings for {provider}")
                
                log.info(f"[MemoryManager] Successfully removed key for {provider}")
            else:
                log.warning(f"[MemoryManager] delete_key returned False for {provider}")
            
            return success
        except Exception as e:
            log.error(f"[MemoryManager] removeApiKey error for {provider}: {e}")
            return False

    # ── MCP Servers (Settings → MCP Servers) ────────────────────────────

    @pyqtSlot(result=str)
    def getMcpStatus(self) -> str:
        """JSON list of configured MCP servers with live status/tools.
        Includes 'subscribed' — MCP is a subscription feature."""
        try:
            from src.services.mcp_manager import (get_mcp_manager,
                                                  _has_active_subscription)
            return json.dumps({"success": True,
                               "subscribed": _has_active_subscription(),
                               "servers": get_mcp_manager().get_status()})
        except Exception as e:
            log.warning(f"[MemoryManager] getMcpStatus error: {e}")
            return json.dumps({"success": False, "error": str(e),
                               "subscribed": False, "servers": []})

    @pyqtSlot(str, str, str, result=str)
    def addMcpServer(self, name: str, command_line: str, env_text: str) -> str:
        """Add + start an MCP server. env_text: 'KEY=val, KEY2=val2'."""
        try:
            from src.services.mcp_manager import get_mcp_manager
            env = {}
            for pair in (env_text or "").replace("\n", ",").split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    if k.strip():
                        env[k.strip()] = v.strip()
            get_mcp_manager().add_server(name.strip(), command_line.strip(), env)
            log.info(f"[MemoryManager] MCP server added: {name}")
            return json.dumps({"success": True})
        except Exception as e:
            log.warning(f"[MemoryManager] addMcpServer error: {e}")
            return json.dumps({"success": False, "error": str(e)})

    @pyqtSlot(str, result=bool)
    def removeMcpServer(self, name: str) -> bool:
        try:
            from src.services.mcp_manager import get_mcp_manager
            get_mcp_manager().remove_server(name)
            log.info(f"[MemoryManager] MCP server removed: {name}")
            return True
        except Exception as e:
            log.warning(f"[MemoryManager] removeMcpServer error: {e}")
            return False

    @pyqtSlot(str, bool, result=bool)
    def toggleMcpServer(self, name: str, enabled: bool) -> bool:
        try:
            from src.services.mcp_manager import get_mcp_manager
            get_mcp_manager().set_enabled(name, enabled)
            return True
        except Exception as e:
            log.warning(f"[MemoryManager] toggleMcpServer error: {e}")
            return False

    @pyqtSlot(str, result=bool)
    def reconnectMcpServer(self, name: str) -> bool:
        try:
            from src.services.mcp_manager import get_mcp_manager
            get_mcp_manager().reconnect(name)
            return True
        except Exception as e:
            log.warning(f"[MemoryManager] reconnectMcpServer error: {e}")
            return False

    @pyqtSlot(str, result=str)
    def importMcpJson(self, text: str) -> str:
        """Import a standard {"mcpServers": {...}} JSON blob."""
        try:
            from src.services.mcp_manager import get_mcp_manager
            added = get_mcp_manager().import_json(text)
            log.info(f"[MemoryManager] MCP import: {added} server(s)")
            return json.dumps({"success": True, "added": added})
        except Exception as e:
            log.warning(f"[MemoryManager] importMcpJson error: {e}")
            return json.dumps({"success": False, "error": str(e)})

    @pyqtSlot(str, result=str)
    def testApiKey(self, provider: str) -> str:
        """Test if a stored API key works. Returns JSON {success: bool, error: str}."""
        log.info(f"[MemoryManager] testApiKey called for {provider}")
        try:
            from src.core.key_manager import get_key_manager
            from src.ai.providers import get_provider_registry, ProviderType
            
            km = get_key_manager()
            key = km.get_key(provider)
            if not key:
                log.info(f"[MemoryManager] testApiKey: no key for {provider}")
                return json.dumps({"success": False, "error": "No key stored"})
            
            # Sanitize the key (strip null bytes, spaces, quotes)
            if key:
                key = key.replace('\x00', '').replace('\u0000', '').replace(' ', '').replace('\n', '').replace('\r', '').strip().strip("'\"")
            
            log.info(f"[MemoryManager] testApiKey: key prefix={repr(key[:8])}, len={len(key)}")
            
            # Try to validate with the provider
            registry = get_provider_registry()
            provider_map = {
                "openai":     ProviderType.OPENAI,
                "deepseek":   ProviderType.DEEPSEEK,
                "mistral":    ProviderType.MISTRAL,
                "mimo":       ProviderType.MIMO,
                "openrouter": ProviderType.OPENROUTER,
                "alibaba":    ProviderType.ALIBABA,
                "siliconflow":ProviderType.SILICONFLOW,
                "anthropic":  ProviderType.ANTHROPIC,
            }
            pt = provider_map.get(provider)
            if pt:
                p = registry.get_provider(pt)
                if p and hasattr(p, 'validate_api_key'):
                    # Use set_api_key() to properly re-detect endpoints (important for MiMo tp-/sk- routing)
                    old_key = getattr(p, '_api_key', None)
                    try:
                        if hasattr(p, 'set_api_key'):
                            p.set_api_key(key)
                        else:
                            p._api_key = key
                        valid = p.validate_api_key()
                        log.info(f"[MemoryManager] testApiKey: validate_api_key returned {valid} for {provider}")
                        if valid:
                            return json.dumps({"success": True, "error": ""})
                        else:
                            return json.dumps({"success": False, "error": "Key validation failed"})
                    finally:
                        # Restore old key
                        if hasattr(p, 'set_api_key'):
                            p.set_api_key(old_key or '')
                        else:
                            p._api_key = old_key
            
            # If no validate method, just check key length
            if len(key) > 8:
                return json.dumps({"success": True, "error": ""})
            return json.dumps({"success": False, "error": "Key too short"})
            
        except Exception as e:
            log.warning(f"[MemoryManager] testApiKey error: {e}")
            return json.dumps({"success": False, "error": str(e)})

    @pyqtSlot(result=str)
    def getEnabledProviders(self) -> str:
        """Return JSON list of providers activated for the chat model dropdown."""
        try:
            from src.ai.model_registry import get_enabled_providers
            return json.dumps(get_enabled_providers())
        except Exception as e:
            log.warning(f"[MemoryManager] getEnabledProviders error: {e}")
            return json.dumps(["mimo", "deepseek"])

    @pyqtSlot(str, bool, result=str)
    def setProviderEnabled(self, provider: str, enabled: bool) -> str:
        """Enable/disable a provider for the chat model dropdown; returns new JSON list."""
        try:
            from src.ai.model_registry import set_provider_enabled
            new_list = set_provider_enabled(provider, enabled)
            log.info(f"[MemoryManager] Provider '{provider}' {'enabled' if enabled else 'disabled'} → {new_list}")
            return json.dumps(new_list)
        except Exception as e:
            log.warning(f"[MemoryManager] setProviderEnabled error: {e}")
            return json.dumps([])

    @pyqtSlot(str, result=str)
    def getSetting(self, key: str) -> str:
        """Read a single setting value by dotted path (e.g. 'editor.font_size')."""
        try:
            if self._settings:
                if "." in key:
                    parts = key.split(".")
                    val = self._settings.get(*parts, default="")
                else:
                    val = self._settings.get("ui", key, default="")
                return str(val) if val is not None else ""
        except Exception:
            pass
        return ""

    @pyqtSlot(result=str)
    def getSettings(self) -> str:
        """Return ALL settings from ALL sections as nested JSON dict.

        API keys are loaded from KeyManager (decrypted) and injected into
        the ai section so the settings page can display them.
        """
        try:
            if self._settings:
                data = self._settings.all()
                # Inject API keys from KeyManager into the ai section
                ai = data.get("ai", {})
                if isinstance(ai, dict):
                    for settings_key, km_name in self._API_KEY_FIELDS.items():
                        _, field = settings_key.split(".", 1)
                        key = self._load_api_key(km_name)
                        if key:
                            ai[field] = key
                        else:
                            # Clear placeholder if no real key exists
                            if ai.get(field) == "***":
                                ai[field] = ""
                    data["ai"] = ai
                return json.dumps(data)
        except Exception:
            pass
        return "{}"

    def _load_api_key(self, provider: str) -> str:
        """Load an API key from KeyManager (decrypted)."""
        try:
            from src.core.key_manager import get_key_manager
            km = get_key_manager()
            key = km.get_key(provider)
            return key or ""
        except Exception:
            return ""

    @pyqtSlot()
    def restartIDE(self):
        """Restart the IDE by closing and relaunching it."""
        log.info("[MemoryManager] restartIDE called")
        try:
            import os
            import sys
            import subprocess
            from PyQt6.QtWidgets import QApplication
            
            # Get the main application entry point
            app = QApplication.instance()
            if not app:
                log.warning("[MemoryManager] Cannot restart: no QApplication instance")
                return
            
            # Get the Python executable and main script
            python_exe = sys.executable
            main_script = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "main.py")
            
            # Close current instance
            app.quit()
            
            # Relaunch in background
            subprocess.Popen([python_exe, main_script], cwd=os.getcwd())
            
            log.info("[MemoryManager] IDE restart initiated")
        except Exception as e:
            log.error(f"[MemoryManager] restartIDE failed: {e}")
    
    @pyqtSlot()
    def restart(self):
        """Alias for restartIDE for compatibility."""
        self.restartIDE()

    @pyqtSlot()
    def onSettingsClosed(self):
        """Called when user clicks 'Back to app' — close the dialog."""
        log.info("[MemoryManager] onSettingsClosed — closing dialog")
        try:
            # Close the dialog (self is the bridge, need to find the dialog)
            dialog = self.parent()
            if dialog and hasattr(dialog, 'close'):
                dialog.close()
        except Exception as e:
            log.warning(f"[MemoryManager] onSettingsClosed error: {e}")


class MemoryManagerDialog(QDialog):
    """WebEngine-backed memory manager dialog."""

    def __init__(self, project_root: str, settings=None, parent=None):
        super().__init__(parent)
        self._bridge = MemoryManagerBridge(project_root, settings=settings, parent=self)
        self._page_loaded = False

        self.setWindowTitle("Memory Manager - Cortex IDE")
        self.setMinimumSize(980, 720)
        self.resize(1120, 780)

        self._build_ui()

    def _build_ui(self):
        log.debug("[MemoryManager] _build_ui START")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._view = QWebEngineView(self)
        self._page = _MemoryPage(self._view)
        self._view.setPage(self._page)
        log.debug("[MemoryManager] QWebEngineView + _MemoryPage created")

        # Kill the WHITE flash while Chromium boots + the page loads
        # (multi-second on compiled builds under RAM pressure): paint the
        # webview in the theme background instead of Chromium's default
        # white, so the dialog looks intentionally dark while loading.
        try:
            from PyQt6.QtGui import QColor
            _theme = "dark"
            if self._bridge._settings:
                _theme = str(self._bridge._settings.get(
                    "appearance", "theme", default="dark") or "dark")
            self._page.setBackgroundColor(
                QColor("#f5f5f5" if _theme == "light" else "#1e1e1e"))
        except Exception as _bg_err:
            log.debug(f"[MemoryManager] setBackgroundColor skipped: {_bg_err}")

        settings = self._view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        # NOTE: JavascriptCanAccessClipboard intentionally DISABLED —
        # Memory manager uses Python bridge for clipboard, not navigator.clipboard.
        # Enabling it causes Qt/Chromium to spam clipboard retry errors.

        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self._bridge)
        self._channel.registerObject("memoryBridge", self._bridge)
        self._bind_web_channel()

        self._view.loadFinished.connect(self._on_page_loaded)
        self._bridge.data_changed.connect(self._push_state_to_page)
        self._bridge.toast_requested.connect(self._show_toast)
        self._bridge.login_complete.connect(self._handle_login_complete)

        layout.addWidget(self._view)

        # Track load progress so the safety net can tell "still loading"
        # apart from "stuck". Bug history: a fixed 3s timer fired on every
        # open in compiled builds (QtWebEngine cold-starts slower there,
        # especially under RAM pressure) and yanked the still-loading real
        # page, replacing it with the broken setHtml fallback — users saw
        # an unstyled Times-Roman settings page with no keys/toggles.
        self._load_progress = 0
        self._view.loadProgress.connect(self._on_load_progress)

        self._load_page()

        # Safety net: only give up on the real page if loading has genuinely
        # stalled — never while Chromium is still making progress.
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(15000, self._safety_fallback)

    def _on_load_progress(self, progress: int):
        self._load_progress = progress

    def _safety_fallback(self):
        """If page never loaded AND loading stalled, inject HTML directly."""
        try:
            if self._page_loaded:
                return
            if 0 < self._load_progress < 100:
                # Still actively loading — check again instead of nuking it.
                log.info("[MemoryManager] Page still loading (%d%%) — safety check postponed",
                         self._load_progress)
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(10000, self._safety_fallback)
                return
            log.warning("[MemoryManager] Safety fallback triggered — page load stalled")
            self._load_html_fallback()
        except RuntimeError:
            pass  # dialog was closed/deleted before the timer fired

    def _handle_login_complete(self, user_info):
        """Handle login complete on main thread — bring window to focus and refresh UI."""
        log.info(f"[MemoryManager] _handle_login_complete called on main thread")
        try:
            from PyQt6.QtWidgets import QApplication
            # Get the main window
            main_window = None
            for w in QApplication.topLevelWidgets():
                if w.__class__.__name__ == "MainWindow":
                    main_window = w
                    break
            if main_window is None:
                main_window = self.window()

            if main_window:
                main_window.showNormal()
                main_window.raise_()
                main_window.activateWindow()
                # Windows-specific: force foreground
                try:
                    import ctypes
                    hwnd = int(main_window.winId())
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass

            # Refresh the webview to show logged-in state
            if self._view and self._page_loaded:
                self._view.page().runJavaScript("if(typeof loadProfile==='function') loadProfile();")
                self._view.page().runJavaScript("if(typeof loadUsageStats==='function') loadUsageStats();")
                log.info("[MemoryManager] Triggered UI refresh after login")
            else:
                log.warning(f"[MemoryManager] Cannot refresh UI: _view={self._view}, _page_loaded={self._page_loaded}")

            # MCP is subscription-gated and its servers were parked in
            # status="subscription" while logged out — signing in must
            # relaunch them and refresh the MCP panel. Bug history: login
            # refreshed profile/usage only, so the "Subscription Required"
            # lock card stayed (and servers stayed unlaunched) until the
            # user clicked Refresh manually. start() does a network
            # subscription check + spawns processes → run it off the GUI
            # thread; the JS refreshes are staggered on GUI timers to show
            # the connecting → connected progression.
            def _restart_mcp():
                try:
                    from src.services.mcp_manager import get_mcp_manager
                    get_mcp_manager().start()
                    log.info("[MemoryManager] MCP manager restarted after login")
                except Exception as _mcp_err:
                    log.warning(f"[MemoryManager] MCP restart after login failed: {_mcp_err}")
            import threading
            threading.Thread(target=_restart_mcp, daemon=True,
                             name="McpLoginRestart").start()

            if self._view and self._page_loaded:
                from PyQt6.QtCore import QTimer
                _js = "if(window.refreshMcpStatus) window.refreshMcpStatus();"
                for _delay in (1500, 5000, 12000):
                    QTimer.singleShot(_delay, lambda js=_js: (
                        self._view.page().runJavaScript(js)
                        if self._view else None))
        except Exception as e:
            log.error(f"[MemoryManager] _handle_login_complete error: {e}")

    def _load_page(self):
        html_path = (
            Path(__file__).resolve().parent.parent / "html" / "memory_manager" / "memory_management.html"
        )
        log.debug(f"[MemoryManager] _load_page: html_path={html_path} exists={html_path.exists()}")
        if not html_path.exists():
            QMessageBox.critical(self, "Memory Manager", f"Missing UI file:\n{html_path}")
            return

        try:
            # Read HTML content for fallback
            with open(html_path, "r", encoding="utf-8") as f:
                self._html_content = f.read()

            url = QUrl.fromLocalFile(str(html_path))
            url.setQuery(f"v={int(time.time())}")
            log.debug(f"[MemoryManager] Loading URL: {url.toString()}")
            self._view.setUrl(url)
        except Exception as e:
            log.error(f"[MemoryManager] _load_page error: {e}")
            # Fallback: load HTML directly
            self._load_html_fallback()

    def _load_html_fallback(self):
        """Fallback: load HTML content directly via setHtml.

        The baseUrl argument is REQUIRED: setHtml(html) alone gives the page
        an about:blank origin and Chromium then refuses to fetch the file://
        CSS/JS subresources (a <base> tag does not grant that permission) —
        the page rendered as unstyled raw HTML with no bridge.
        """
        log.debug("[MemoryManager] Using setHtml fallback")
        html_path = (
            Path(__file__).resolve().parent.parent / "html" / "memory_manager" / "memory_management.html"
        )
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        self._view.setHtml(html, QUrl.fromLocalFile(str(html_path)))

    def _on_page_loaded(self, ok: bool):
        self._page_loaded = ok
        log.debug(f"[MemoryManager] Page loaded ok={ok}")
        if not ok:
            QMessageBox.warning(self, "Memory Manager", "Failed to load memory management page.")
            return

        # Apply current theme to the web view
        try:
            from src.config.theme_manager import get_theme_manager
            tm = get_theme_manager()
            current_theme = tm.current
            js_theme = f"document.documentElement.setAttribute('data-theme', '{current_theme}');"
            self._view.page().runJavaScript(js_theme)
            log.debug(f"[MemoryManager] Applied theme: {current_theme}")
        except Exception as e:
            log.debug(f"[MemoryManager] Could not apply initial theme: {e}")

        # Re-apply channel binding after load to avoid intermittent transport injection races.
        self._bind_web_channel()
        initial = self._bridge.loadInitialData()
        log.debug(f"[MemoryManager] Initial data length={len(initial)} first200={initial[:200]}")

        # Use a delayed push to ensure JS has fully initialized.
        # Multiple retries ensure the page renders even if scripts load slowly.
        from PyQt6.QtCore import QTimer
        for delay_ms in (200, 600, 1500, 3000):
            QTimer.singleShot(delay_ms, lambda d=delay_ms: self._try_push_state(initial, d))

    def _bind_web_channel(self):
        try:
            # Use the same stable pattern as the working AI chat webview.
            self._page.setWebChannel(self._channel)
        except TypeError:
            # Fallback for builds that only accept setWebChannel(channel).
            self._page.setWebChannel(self._channel)
        except Exception as exc:
            log.warning(f"[MemoryManager] Failed to bind web channel: {exc}")

    def _try_push_state(self, payload: str, delay_ms: int):
        """Push state to page — always call _push_state_to_page which handles fallback."""
        if not self._page_loaded:
            return
        def _check_and_push(func_type):
            log.debug(f"[MemoryManager] _try_push_state(delay={delay_ms}ms) receiveMemoryState type={func_type}")
            # Always push — _push_state_to_page has its own fallback renderer
            try:
                self._push_state_to_page(payload)
            except RuntimeError:
                pass  # dialog closed while the JS callback was in flight
        self._view.page().runJavaScript(
            "typeof window.receiveMemoryState",
            _check_and_push,
        )

    def _push_state_to_page(self, payload: str):
        log.debug(f"[MemoryManager] _push_state_to_page called, page_loaded={self._page_loaded}, payload_len={len(payload)}")
        if not self._page_loaded:
            log.warning("[MemoryManager] _push_state_to_page skipped: page not loaded")
            return
        # Build JS that:
        # 1. Tries the real receiveMemoryState render first
        # 2. ALWAYS injects a visible fallback container as safety net
        # The JSON payload is injected directly (it's already valid JS object notation).
        js_code = (
            "(function(){\n"
            f"  var _s = {payload};\n"
            "  var _rendered = false;\n"
            "  /* Step 1: try the real renderer */\n"
            "  try {\n"
            "    if (typeof window.receiveMemoryState === 'function') {\n"
            "      window.receiveMemoryState(_s);\n"
            "      /* Check if real renderer actually populated the list */\n"
            "      var lv = document.getElementById('listView');\n"
            "      if (lv && lv.children.length > 0) { _rendered = true; }\n"
            "    }\n"
            "  } catch(e) { console.error('[MEMORY] render error:', e); }\n"
            "  /* Step 2: always inject fallback into a safety container */\n"
            "  var _scope = (_s.scopes || {})[_s.activeScope] || (_s.scopes || {}).project || {};\n"
            "  var _mems = Array.isArray(_scope.memories) ? _scope.memories : [];\n"
            "  var _fb = document.getElementById('mm-python-fallback');\n"
            "  if (!_fb) {\n"
            "    _fb = document.createElement('div');\n"
            "    _fb.id = 'mm-python-fallback';\n"
            "    _fb.style.cssText = 'padding:24px;font-family:sans-serif;color:#ccc;min-height:200px;';\n"
            "    document.body.appendChild(_fb);\n"
            "  }\n"
            "  var _h = '<div style=\"padding:16px 0;\">';\n"
            "  _h += '<p style=\"color:#888;margin-bottom:12px;\">Scope: <strong style=\"color:#fff;\">' + (_scope.name || 'N/A') + '</strong> \\u2022 ' + _mems.length + ' memor' + (_mems.length === 1 ? 'y' : 'ies') + '</p>';\n"
            "  if (_scope.memoryDir) _h += '<p style=\"color:#666;font-size:12px;margin-bottom:16px;\"><code>' + _scope.memoryDir + '</code></p>';\n"
            "  _mems.forEach(function(m){\n"
            "    _h += '<div style=\"border:1px solid #444;border-radius:6px;padding:12px;margin:8px 0;background:#2d2d2d;\">';\n"
            "    _h += '<strong style=\"color:#fff;\">' + (m.name || m.filename || 'unnamed') + '</strong>';\n"
            "    _h += ' <span style=\"color:#888;\">(' + (m.type || 'general') + ')</span>';\n"
            "    if (m.age) _h += ' <span style=\"color:#666;font-size:11px;\">\\u2022 ' + m.age + '</span>';\n"
            "    if (m.description) _h += '<p style=\"color:#aaa;margin:4px 0 0;\">' + m.description + '</p>';\n"
            "    _h += '</div>';\n"
            "  });\n"
            "  if (_mems.length === 0) _h += '<p style=\"color:#888;\">No memories saved yet. The agent will populate this space as it learns.</p>';\n"
            "  _h += '</div>';\n"
            "  _fb.innerHTML = _h;\n"
            "  /* Hide fallback if real renderer worked */\n"
            "  if (_rendered) { _fb.style.display = 'none'; }\n"
            "  return _rendered ? 'ok' : 'fallback';\n"
            "})()"
        )
        log.debug(f"[MemoryManager] Running JS code length={len(js_code)}")
        self._view.page().runJavaScript(
            js_code,
            lambda result: log.debug(f"[MemoryManager] JS result: {result}"),
        )

    def _show_toast(self, level: str, message: str):
        if not self._page_loaded:
            return
        safe_level = json.dumps(level)
        safe_message = json.dumps(message)
        self._view.page().runJavaScript(
            f"window.showToast && window.showToast({safe_level}, {safe_message});"
        )
