"""
Git Integration for Cortex AI IDE
"""

import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

# Suppress console windows for subprocess calls in frozen exe on Windows
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
from PyQt6.QtCore import QObject, pyqtSignal
from src.utils.logger import get_logger

log = get_logger("git_manager")


class GitStatus(Enum):
    MODIFIED = "M"
    ADDED = "A"
    DELETED = "D"
    RENAMED = "R"
    COPIED = "C"
    UPDATED = "U"
    UNTRACKED = "??"
    IGNORED = "!!"


@dataclass
class GitFile:
    """Represents a file with git status."""
    path: str
    status: GitStatus
    staged: bool
    old_path: Optional[str] = None  # For renamed files


class GitManager(QObject):
    """Manages Git operations for the project."""
    
    status_changed = pyqtSignal()  # Emitted when git status changes
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._repo_path: Optional[str] = None
        self._available = self._check_git_available()
        self._auto_commit_enabled = False
        self._commit_prefix = "[cortex-ai]"
        self._default_branch = "main"
        self._load_settings()
    
    def _load_settings(self):
        """Load Git settings from settings.json."""
        try:
            from src.config.settings import get_settings
            settings = get_settings()
            self._auto_commit_enabled = bool(settings.get("git", "auto_commit", default=False))
            self._commit_prefix = str(settings.get("git", "commit_prefix", default="[cortex-ai]"))
            self._default_branch = str(settings.get("git", "default_branch", default="main"))
            log.info(f"[Git] Settings loaded: auto_commit={self._auto_commit_enabled}, prefix='{self._commit_prefix}', branch='{self._default_branch}'")
        except Exception as e:
            log.debug(f"[Git] Could not load settings: {e}")
    
    def _check_git_available(self) -> bool:
        """Check if git is available on the system."""
        try:
            result = subprocess.run(
                ["git", "--version"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=5,
                creationflags=_NO_WINDOW
            )
            return result.returncode == 0
        except:
            return False
            
    def set_repository(self, path: str) -> bool:
        """Set the repository path."""
        if not self._available:
            return False
        # Restricted Mode (src/core/workspace_trust.py): a repository's own
        # config can name programs git runs, so Cortex does not drive git in
        # a folder the user has not trusted.
        from src.core.workspace_trust import is_restricted
        if is_restricted(path):
            self._repo_path = None
            return False
            
        # Check if it's a git repository
        git_dir = Path(path) / ".git"
        if git_dir.exists():
            self._repo_path = path
            return True
            
        # Check parent directories
        current = Path(path)
        while current.parent != current:
            git_dir = current / ".git"
            if git_dir.exists():
                self._repo_path = str(current)
                return True
            current = current.parent
            
        return False
        
    def _run_git(self, args: List[str], timeout: int = 30) -> Tuple[bool, str, str]:
        """Run a git command."""
        if not self._available or not self._repo_path:
            return False, "", "Git not available"
            
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=self._repo_path,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding='utf-8',
                errors='replace',
                creationflags=_NO_WINDOW
            )
            return result.returncode == 0, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return False, "", "Command timed out"
        except Exception as e:
            return False, "", str(e)
            
    def get_status(self) -> List[GitFile]:
        """Get the current git status."""
        files = []
        
        success, stdout, stderr = self._run_git([
            # --untracked-files=all, not bare -u: see git_utils.get_status()
            # for why bare -u is not safe to rely on across git versions.
            "status", "--porcelain=v1", "-z", "--untracked-files=all"
        ])
        
        if not success:
            log.error(f"Git status failed: {stderr}")
            return files

        entries = stdout.split('\0')
        i = 0
        while i < len(entries):
            entry = entries[i]
            i += 1
            if not entry or len(entry) < 3:
                continue

            x = entry[0]  # Staged status
            y = entry[1]  # Unstaged status
            path_part = entry[3:]
            old_path = None

            # In -z porcelain output, renames/copies are emitted as:
            # "XY old_path\0new_path\0"
            if (x in ('R', 'C') or y in ('R', 'C')) and i < len(entries) and entries[i]:
                old_path = path_part
                path_part = entries[i]
                i += 1

            status_code = x if x not in (' ', '?') else y
            status = self._parse_status(status_code)
            staged = x not in (' ', '?')

            files.append(GitFile(
                path=path_part,
                status=status,
                staged=staged,
                old_path=old_path
            ))
            
        return files
        
    def _parse_status(self, code: str) -> GitStatus:
        """Parse status code."""
        status_map = {
            'M': GitStatus.MODIFIED,
            'A': GitStatus.ADDED,
            'D': GitStatus.DELETED,
            'R': GitStatus.RENAMED,
            'C': GitStatus.COPIED,
            'U': GitStatus.UPDATED,
            '?': GitStatus.UNTRACKED,
            '!': GitStatus.IGNORED,
        }
        return status_map.get(code, GitStatus.UNTRACKED)
        
    def stage_file(self, file_path: str) -> bool:
        """Stage a file."""
        success, _, stderr = self._run_git(["add", file_path])
        if success:
            self.status_changed.emit()
        else:
            log.error(f"Git add failed: {stderr}")
        return success
        
    def unstage_file(self, file_path: str) -> bool:
        """Unstage a file."""
        success, _, stderr = self._run_git(["reset", "HEAD", file_path])
        if success:
            self.status_changed.emit()
        else:
            log.error(f"Git unstage failed: {stderr}")
        return success
        
    def commit(self, message: str, amend: bool = False) -> Tuple[bool, str]:
        """Commit staged changes. Returns (success, error_message)."""
        # Prepend commit prefix if not already present
        if self._commit_prefix and not message.startswith(self._commit_prefix):
            message = f"{self._commit_prefix} {message}"
        args = ["commit", "-m", message]
        if amend:
            args.append("--amend")
        success, stdout, stderr = self._run_git(args)
        if success:
            self.status_changed.emit()
            return True, ""
        else:
            error_msg = stderr.strip() or stdout.strip() or "Unknown error"
            log.error(f"Git commit failed: {error_msg}")
            return False, error_msg
    
    def auto_commit_file(self, file_path: str, action: str = "modified") -> Tuple[bool, str]:
        """Auto-commit a single file change made by the AI agent.
        
        Args:
            file_path: Path to the changed file
            action: Description of the action (created, modified, deleted)
        
        Returns:
            (success, error_message)
        """
        # Reload settings to pick up any changes made in the UI
        self._load_settings()
        
        if not self._auto_commit_enabled:
            return False, "Auto-commit disabled"
        
        if not self._repo_path:
            return False, "No repository set"
        
        try:
            # Stage the file
            if action == "deleted":
                success, _, stderr = self._run_git(["rm", "--cached", file_path])
            else:
                success, _, stderr = self._run_git(["add", file_path])
            
            if not success:
                log.warning(f"[Git] Failed to stage {file_path}: {stderr}")
                return False, f"Failed to stage: {stderr}"
            
            # Build commit message
            filename = os.path.basename(file_path)
            message = f"{action} {filename}"
            
            # Commit
            success, error = self.commit(message)
            if success:
                log.info(f"[Git] Auto-committed: {message}")
            return success, error
        except Exception as e:
            log.error(f"[Git] Auto-commit failed: {e}")
            return False, str(e)
    
    def get_branch(self) -> str:
        """Get current branch name."""
        success, stdout, stderr = self._run_git([
            "rev-parse", "--abbrev-ref", "HEAD"
        ])
        return stdout.strip() if success else ""
        
    def get_branches(self) -> List[str]:
        """Get list of branches."""
        success, stdout, stderr = self._run_git([
            "branch", "-a"
        ])
        
        branches = []
        if success:
            for line in stdout.strip().split('\n'):
                line = line.strip()
                if line.startswith('*'):
                    line = line[2:]  # Remove '* '
                if line:
                    branches.append(line)
                    
        return branches
        
    def pull(self, remote: str = "origin", branch: str = None) -> Tuple[bool, str]:
        """Pull from remote."""
        args = ["pull", remote]
        if branch:
            args.append(branch)
            
        success, stdout, stderr = self._run_git(args, timeout=60)
        if success:
            self.status_changed.emit()
        return success, stderr if not success else stdout
        
    def push(self, remote: str = "origin", branch: str = None) -> Tuple[bool, str]:
        """Push to remote."""
        args = ["push", remote]
        if branch:
            args.append(branch)
            
        success, stdout, stderr = self._run_git(args, timeout=60)
        if success:
            self.status_changed.emit()
        return success, stderr if not success else stdout
        
