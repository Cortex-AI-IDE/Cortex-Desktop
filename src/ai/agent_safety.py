"""
Agent Safety Guard, Industry-standard agentic loop protections.

Implements:
  P1: Doom-loop detection (MD5 fingerprinting, 3x repeat → pause)
  P2: Tool call counter (advisory reminders every 50 calls, never blocks)
  P3: Read-before-edit enforcement (tracks files read, warns before blind edits)
  P4: Stale-read detection (mtime tracking, warns if file changed since last read)
  P5: Search-before-edit enforcement (requires search before editing large files)
  P6: Search exclusion patterns (skip __pycache__, node_modules, etc.)
  P7: Tool-explanation-before-action directive
  P8: Error recovery budget (3 recovery messages then forced exit)

"""

import hashlib
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

# ============================================================
# Constants
# ============================================================

MAX_TOOL_ITERATIONS = 0  # P2: No limit, agent uses unlimited tools (advisory reminders every 50 calls)
DOOM_LOOP_FINGERPRINT_SIZE = 20   # P1: Last N tool calls to fingerprint
DOOM_LOOP_REPEAT_LIMIT = 3        # P1: Same fingerprint → warning
DOOM_LOOP_FORCE_EXIT = 5          # P1: Same fingerprint → forced stop
ERROR_RECOVERY_MAX = 3            # P8: Max recovery messages per error sequence

# Files/patterns to exclude from grep/glob searches (P6)
SEARCH_EXCLUDE_PATTERNS: List[str] = [
    "__pycache__",
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".cortex",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.exe",
    "*.dll",
    "*.so",
    "*.dylib",
    "*.whl",
    "*.egg-info",
    "package-lock.json",
    "yarn.lock",
    "Pipfile.lock",
    "poetry.lock",
    "Cargo.lock",
]

# Edit/write tool names that require read-before-edit check
WRITE_TOOL_NAMES: Set[str] = {
    "Edit", "Write", "CreateFile", "edit_file", "write_file", "create_file",
}

# Read tool names that register files as "read"
READ_TOOL_NAMES: Set[str] = {
    "Read", "read_file", "readFile",
}

# Search tool names that don't require read-before-edit
SEARCH_TOOL_NAMES: Set[str] = {
    "Grep", "Glob", "LS", "SemanticSearch", "WebSearch", "WebFetch",
    "grep", "glob", "list_directory",
}

# ============================================================
# Persistent Directives (P0), survive compaction
# ============================================================

PERSISTENT_DIRECTIVES = """
═══════════════════════════════════════════════════════════════
SAFETY DIRECTIVES (these NEVER get compacted):
═══════════════════════════════════════════════════════════════

1. CONTEXT BUDGET:
   You have a generous context window. Use it wisely.
   Read when you need to, not by default.

2. TOOL CALL BUDGET:
   Use as many tools as needed to complete the task.
   You'll get periodic reminders but will NEVER be blocked.
   Be efficient, avoid redundant reads or searches.

3. DOOM-LOOP DETECTION:
   If you call the same tool with the same arguments 3 times,
   STOP and try a completely different approach.
   Repeating a failing command will never make it succeed.

4. READ-BEFORE-EDIT:
   Read a file before editing it. The system tracks reads.
   If you edit without reading, the system will warn you.

5. ERROR RECOVERY:
   If you encounter 3 consecutive errors on the same operation,
   STOP and explain what went wrong to the user.

6. NO SELF-TALK:
   Do NOT narrate your process. Just call tools.
   The user sees tool calls automatically.
   Visible text = explanations, summaries, questions ONLY.
═══════════════════════════════════════════════════════════════
""".strip()


# ============================================================
# Data classes
# ============================================================

@dataclass
class ToolFingerprint:
    """A fingerprint of a tool call for doom-loop detection."""
    tool_name: str
    args_hash: str       # MD5 of normalized arguments
    timestamp: float = field(default_factory=time.time)


@dataclass
class FileReadRecord:
    """Tracks when a file was last read and its state."""
    path: str
    read_time: float
    mtime: Optional[float] = None       # File modification time at read
    content_hash: Optional[str] = None   # MD5 of content at read
    is_partial: bool = False             # True if read with offset/limit
    read_lines: int = 0                  # Number of lines read
    total_lines: int = 0                 # Total lines in file
    read_start: int = 0                  # First line number read (1-indexed)
    read_end: int = 0                    # Last line number read (1-indexed)


@dataclass
class SearchRecord:
    """Tracks when a search was performed and what was searched."""
    tool_name: str                       # "Grep", "SemanticSearch", etc.
    query: str                           # Search query/pattern
    file_path: Optional[str] = None      # File searched in (if scoped)
    timestamp: float = field(default_factory=time.time)


@dataclass
class SafetyState:
    """Mutable state for the agent safety guard within a single turn."""
    iteration_count: int = 0
    fingerprints: Deque[ToolFingerprint] = field(
        default_factory=lambda: deque(maxlen=DOOM_LOOP_FINGERPRINT_SIZE)
    )
    files_read: Dict[str, FileReadRecord] = field(default_factory=dict)
    searches_done: List[SearchRecord] = field(default_factory=list)
    error_recovery_count: int = 0
    doom_loop_count: int = 0            # How many times we've warned about doom loop
    last_error_text: str = ""
    has_been_warned_about_iterations: bool = False


# ============================================================
# AgentSafetyGuard, the main class
# ============================================================

class AgentSafetyGuard:
    """
    Industry-standard safety guard for the agentic tool loop.

    Tracks tool calls, detects doom loops, enforces read-before-edit,
    and manages persistent directives that survive compaction.
    """

    def __init__(self, max_iterations: int = 0):
        self._state = SafetyState()
        # Persistent state across turns (only reset on new user message)
        self._files_read: Dict[str, FileReadRecord] = {}
        self._consecutive_errors: int = 0

    # ── Reset methods ──────────────────────────────────────────

    def reset_turn(self) -> None:
        """Reset per-turn state. Call at the start of each new user message."""
        self._state = SafetyState()
        self._consecutive_errors = 0

    def reset_files_read(self) -> None:
        """Reset the files-read tracker. Called on new user message."""
        self._files_read.clear()

    # ── P1: Doom-loop detection ────────────────────────────────

    def _fingerprint_args(self, args: Any) -> str:
        """Create an MD5 fingerprint of tool call arguments."""
        if isinstance(args, dict):
            # Sort keys for stable fingerprinting
            normalized = json.dumps(args, sort_keys=True, default=str)
        elif isinstance(args, str):
            normalized = args
        else:
            normalized = str(args)
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:12]

    def check_doom_loop(self, tool_name: str, args: Any) -> Optional[str]:
        """
        Check if this tool call is a doom-loop repeat.

        Returns:
            None if OK, warning message if repeat detected,
            STOP message if force-exit threshold reached.
        """
        fp_hash = self._fingerprint_args(args)
        fp = ToolFingerprint(tool_name=tool_name, args_hash=fp_hash)
        self._state.fingerprints.append(fp)

        # Count how many times this exact fingerprint appears
        count = sum(
            1 for f in self._state.fingerprints
            if f.tool_name == tool_name and f.args_hash == fp_hash
        )

        if count >= DOOM_LOOP_FORCE_EXIT:
            self._state.doom_loop_count += 1
            return (
                f"DOOM LOOP DETECTED: You have called {tool_name} with the exact "
                f"same arguments {count} times. This is a waste of tokens and time. "
                f"STOP calling this tool. Try a completely different approach, or "
                f"stop and report what you've found to the user."
            )
        elif count >= DOOM_LOOP_REPEAT_LIMIT:
            return (
                f"WARNING: You've called {tool_name} with the same arguments "
                f"{count} times in a row. This may be a doom loop. "
                f"If the next attempt produces the same result, you MUST "
                f"try a different approach."
            )
        return None

    # ── P2: Max iteration limit ────────────────────────────────

    def check_max_iterations(self) -> Optional[str]:
        """
        Check tool call count, soft warnings only, NEVER blocks.

        The agent is allowed to use as many tools as needed to complete work.
        Periodic reminders are emitted at intervals so the agent stays aware
        of its usage, but no hard stop is enforced.

        Returns:
            None if OK, advisory reminder message at intervals.
        """
        self._state.iteration_count += 1
        count = self._state.iteration_count

        # Periodic reminders every 50 calls (never blocks)
        if count > 0 and count % 50 == 0:
            return (
                f"[System: {count} tool calls used this turn. "
                f"Continue working to complete the task. "
                f"Be efficient, avoid redundant reads or searches.]"
            )

        return None

    # ── P3: Read-before-edit enforcement ───────────────────────

    @staticmethod
    def _hash_file(abs_path: str) -> Optional[str]:
        """Full-content hash. A 4KB sample missed every change past the
        first page, which made hash comparison useless for staleness."""
        try:
            h = hashlib.md5()
            with open(abs_path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            return h.hexdigest()[:12]
        except Exception:
            return None

    def register_file_read(self, file_path: str, is_partial: bool = False, read_lines: int = 0, total_lines: int = 0, read_start: int = 0, read_end: int = 0) -> None:
        """Register that a file was read. Call after successful read_file."""
        abs_path = os.path.normpath(os.path.abspath(file_path))
        try:
            stat = os.stat(abs_path)
            mtime = stat.st_mtime
            content_hash = self._hash_file(abs_path)
        except Exception:
            mtime = None
            content_hash = None

        self._files_read[abs_path] = FileReadRecord(
            path=abs_path,
            read_time=time.time(),
            mtime=mtime,
            content_hash=content_hash,
            is_partial=is_partial,
            read_lines=read_lines,
            total_lines=total_lines,
            read_start=read_start,
            read_end=read_end,
        )

    def check_read_before_edit(self, file_path: str, is_auto_mode: bool = False, edit_start_line: int = 0, edit_end_line: int = 0) -> Optional[str]:
        """
        Check if a file was read before attempting to edit it.

        Returns:
            None if file was read (OK),
            Warning message if file was NOT read.
        """
        abs_path = os.path.normpath(os.path.abspath(file_path))

        # Creating a NEW file can't require having read it, the file does not
        # exist yet. Warning here was pure noise on every legitimate new-file
        # Write (log 2026-07-22 shows it firing for every created file) and it
        # made the structured Write path look more "wrong" than shelling out.
        if not os.path.exists(abs_path):
            return None

        if abs_path not in self._files_read:
            if is_auto_mode:
                return (
                    f"AUTO-READ: You haven't read '{os.path.basename(file_path)}' yet. "
                    f"The file will be auto-read before your edit to prevent blind changes."
                )
            else:
                return (
                    f"STOP: You haven't read '{os.path.basename(file_path)}' yet. "
                    f"You MUST read it first before editing. "
                    f"Use the Read tool to read the file, then retry your edit."
                )

        return None

    def was_file_read(self, file_path: str) -> bool:
        """Check if a specific file has been read."""
        abs_path = os.path.normpath(os.path.abspath(file_path))
        return abs_path in self._files_read

    # ── P5: Search-before-edit enforcement ──────────────────────

    LARGE_FILE_THRESHOLD = 500  # lines, files above this need search before edit

    def register_search(self, tool_name: str, query: str, file_path: Optional[str] = None) -> None:
        """Register that a search was performed. Call after Grep/SemanticSearch."""
        self._state.searches_done.append(SearchRecord(
            tool_name=tool_name,
            query=query,
            file_path=file_path,
        ))

    def check_search_before_edit(self, file_path: str, edit_old_string: str = "") -> Optional[str]:
        """
        For large files, enforce that the agent searched before editing.
        This prevents blind edits based on partial reads that corrupt files.

        Returns:
            None if search was done or file is small (OK),
            Warning message if search was NOT done for a large file.
        """
        abs_path = os.path.normpath(os.path.abspath(file_path))

        # Only enforce for large files
        record = self._files_read.get(abs_path)
        if record and record.total_lines > 0:
            if record.total_lines <= self.LARGE_FILE_THRESHOLD:
                return None  # Small file, no search needed
        else:
            # Unknown file size, check disk
            try:
                with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f:
                    line_count = sum(1 for _ in f)
                if line_count <= self.LARGE_FILE_THRESHOLD:
                    return None
            except Exception:
                return None  # Can't check, allow

        # Check if any search was done that references this file or a function name
        basename = os.path.basename(file_path).replace('.py', '').replace('.js', '').replace('.ts', '')
        edit_terms = set()
        if edit_old_string:
            # Extract function/class names from old_string
            import re
            for match in re.finditer(r'def\s+(\w+)|class\s+(\w+)|function\s+(\w+)|(\w+)\s*[=:]', edit_old_string[:200]):
                name = match.group(1) or match.group(2) or match.group(3) or match.group(4)
                if name and len(name) > 2:
                    edit_terms.add(name.lower())

        has_file_search = False
        has_term_search = False
        for search in self._state.searches_done:
            # Check if search was scoped to this file
            if search.file_path and abs_path in os.path.normpath(search.file_path):
                has_file_search = True
            # Check if search query contains the file name
            if basename.lower() in search.query.lower():
                has_file_search = True
            # Check if search query contains function names from old_string
            for term in edit_terms:
                if term in search.query.lower():
                    has_term_search = True

        if not has_file_search and not has_term_search and edit_terms:
            terms_str = ', '.join(list(edit_terms)[:3])
            return (
                f"SEARCH REQUIRED: You're editing a large file ({os.path.basename(file_path)}). "
                f"Before editing, you MUST search for the function/variable names first to understand "
                f"their purpose and callers. Use Grep(pattern='{terms_str}') or "
                f"SementicSearch(query='{terms_str}') before editing."
            )

        return None

    # ── P4: Stale-read detection ───────────────────────────────

    def check_stale_read(self, file_path: str) -> Optional[str]:
        """
        Check if a file has been modified since it was last read.

        Returns:
            None if file is fresh or was never read,
            Warning message if file is stale (mtime changed).
        """
        abs_path = os.path.normpath(os.path.abspath(file_path))
        record = self._files_read.get(abs_path)

        if record is None:
            return None  # Not tracked, read-before-edit will catch this

        try:
            current_mtime = os.stat(abs_path).st_mtime
            if record.mtime is not None and current_mtime > record.mtime:
                # mtime alone lies constantly on this codebase: the AUTO-EDIT
                # path rewrites the identical content seconds after the tool
                # call, and OneDrive touches timestamps on sync. 76 STALE READ
                # warnings in one evening (log 23:0x-23:5x), nearly all false.
                # Only the CONTENT changing makes a read stale.
                current_hash = self._hash_file(abs_path)
                if current_hash is not None and current_hash == record.content_hash:
                    record.mtime = current_mtime      # self-heal: touch, not change
                    return None
                return (
                    f"STALE READ: '{os.path.basename(file_path)}' was modified "
                    f"since you last read it. Re-read it before editing to avoid "
                    f"overwriting changes made by other tool calls."
                )
        except Exception:
            pass
        return None

    # ── P8: Error recovery budget ──────────────────────────────

    def check_error_recovery(self, error_text: str) -> Optional[str]:
        """
        Track consecutive errors and enforce recovery budget.

        Returns:
            None if within budget,
            Forced-exit message if recovery budget exhausted.
        """
        # If same error, increment counter. If different error, reset.
        if error_text and self._last_error_similar(error_text):
            self._consecutive_errors += 1
        else:
            self._consecutive_errors = 1
        self._state.last_error_text = error_text

        if self._consecutive_errors > ERROR_RECOVERY_MAX:
            return (
                f"[System: STOP, {self._consecutive_errors} consecutive errors on this operation. "
                f"Try a different approach, or summarize what went wrong for the user.]"
            )
        return None

    def _last_error_similar(self, error_text: str) -> bool:
        """Check if this error is similar to the last one."""
        if not self._state.last_error_text:
            return False
        # Compare first 100 chars of error
        return error_text[:100] == self._state.last_error_text[:100]

    # ── P6: Search exclusion ───────────────────────────────────

    @staticmethod
    def should_exclude_path(path: str) -> bool:
        """
        Check if a file/directory path should be excluded from search results.
        """
        basename = os.path.basename(path)
        for pattern in SEARCH_EXCLUDE_PATTERNS:
            if pattern.startswith("*."):
                # Extension match
                ext = pattern[1:]  # e.g., ".pyc"
                if basename.endswith(ext):
                    return True
            elif basename == pattern:
                return True
            # Check if any parent directory matches
            parts = path.replace("\\", "/").split("/")
            for part in parts:
                if part == pattern:
                    return True
        return False

    @staticmethod
    def filter_search_results(paths: List[str]) -> List[str]:
        """Filter a list of file paths, removing excluded patterns."""
        return [p for p in paths if not AgentSafetyGuard.should_exclude_path(p)]

    # ── Full check for tool dispatch ───────────────────────────

    def check_tool_call(
        self,
        tool_name: str,
        args: Any,
        is_auto_mode: bool = False,
    ) -> Tuple[bool, List[str]]:
        """
        Run all safety checks for a tool call.

        Args:
            tool_name: Name of the tool being called
            args: Tool arguments (dict or string)
            is_auto_mode: Whether the agent is in AUTO mode

        Returns:
            (should_proceed, warnings), whether to proceed and any warning messages
        """
        warnings: List[str] = []
        should_proceed = True

        # P2: Tool call counter, advisory only, never blocks
        iter_msg = self.check_max_iterations()
        if iter_msg:
            warnings.append(iter_msg)

        # P1: Doom-loop detection
        doom_msg = self.check_doom_loop(tool_name, args)
        if doom_msg:
            if "DOOM LOOP DETECTED" in doom_msg:
                # ── AUTO mode: warn, never hard-block a WRITING tool ──
                # Bug history: this exemption covered ONLY Bash ("Bash is the
                # fallback when Edit/Write fail"). That asymmetry taught the
                # model the opposite of what we want: Edit/Write got hard
                # errors while Bash always succeeded, so it abandoned the
                # structured edit tools and started writing throwaway
                # `_fix_x.py` scripts and running them through Bash to do
                # string surgery on real files (log 2026-07-22: 26 turns of
                # Write(_scan1.py)→Bash→Write(_fix_brands.py)→Bash…, edits
                # frequently not landing). Worse, each throwaway script has a
                # NEW path, so it never trips this loop check at all, the
                # guard punished honest iteration on one file and rewarded
                # the fragile workaround.
                # Blocking one tool only reroutes the loop through another;
                # in AUTO mode every writing tool now gets the same warning
                # and the model is told to change approach.
                if is_auto_mode and (tool_name == "Bash" or tool_name in WRITE_TOOL_NAMES):
                    warnings.append(f"[DOOM-LOOP WARNING] {doom_msg}")
                else:
                    return False, [doom_msg]  # FORCE STOP only in ASK mode
            warnings.append(doom_msg)

        # P3: Read-before-edit enforcement
        if tool_name in WRITE_TOOL_NAMES:
            file_path = self._extract_file_path(args)
            if file_path:
                # P4: Stale-read check
                stale_msg = self.check_stale_read(file_path)
                if stale_msg:
                    warnings.append(stale_msg)

                # P3: Read-before-edit, NEVER block, just warn
                read_msg = self.check_read_before_edit(file_path, is_auto_mode)
                if read_msg:
                    warnings.append(read_msg)

                # P5: Search-before-edit for large files
                edit_old_string = ""
                if isinstance(args, dict):
                    edit_old_string = args.get("old_string", args.get("old_str", ""))
                search_msg = self.check_search_before_edit(file_path, edit_old_string)
                if search_msg:
                    warnings.append(search_msg)

        return should_proceed, warnings

    # ── Register tool results ──────────────────────────────────

    def register_tool_result(self, tool_name: str, args: Any, result: Any = None) -> None:
        """Register a SUCCESSFUL tool call. Called from the bridge funnel.

        BUG history: this method existed but had NO caller. Consequences,
        both measured in one evening's log (2026-07-29 23:0x-23:5x):
        - searches were never registered, so check_search_before_edit
          warned 'SEARCH REQUIRED' on every large-file edit even right
          after the model ran the demanded Grep, 40 times;
        - the agent's own Edit/Write bumped the file's mtime with the
          read record never refreshed, so every follow-up edit warned
          'STALE READ', 76 times. The model treats those as real errors
          and reroutes file changes through throwaway shell/python
          scripts (the exact anti-pattern the doom-loop fix targets).

        Reads are NOT registered here: the Read dispatcher calls
        register_file_read directly with partial-read details this
        funnel does not have, and re-registering would clobber them.
        """
        # P4: a successful Write/Edit is the freshest possible knowledge
        # of the file, the agent authored the current content. Refresh
        # the record so our own mtime bump is not flagged stale.
        if tool_name in WRITE_TOOL_NAMES:
            file_path = self._extract_file_path(args)
            if file_path:
                abs_path = os.path.normpath(os.path.abspath(file_path))
                if os.path.exists(abs_path):
                    self.register_file_read(abs_path)

        # P5: Register searches (Grep, SemanticSearch, Glob)
        if tool_name in ("Grep", "SemanticSearch", "Glob"):
            query = ""
            file_path = None
            if isinstance(args, dict):
                query = args.get("pattern", args.get("query", args.get("path", "")))
                file_path = args.get("path", args.get("file_path", None))
            self.register_search(tool_name, query, file_path)

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _extract_file_path(args: Any) -> Optional[str]:
        """Extract the file path from tool arguments."""
        if isinstance(args, dict):
            # Try common key names
            for key in ("file_path", "path", "filePath", "file", "filename"):
                if key in args and args[key]:
                    return str(args[key])
            # Try nested
            if "arguments" in args:
                return AgentSafetyGuard._extract_file_path(args["arguments"])
        elif isinstance(args, str):
            # Might be a JSON string
            try:
                parsed = __import__("json").loads(args)
                return AgentSafetyGuard._extract_file_path(parsed)
            except Exception:
                # Might be a plain file path
                if "/" in args or "\\" in args:
                    return args
        return None

    # ── State serialization (for persistence across compaction) ─

    def get_state_summary(self) -> Dict[str, Any]:
        """Get a serializable summary of the current safety state."""
        return {
            "iteration_count": self._state.iteration_count,
            "files_read_count": len(self._files_read),
            "files_read": list(self._files_read.keys()),
            "consecutive_errors": self._consecutive_errors,
            "doom_loop_count": self._state.doom_loop_count,
        }


# Need json for fingerprinting
import json as json  # noqa: E402 (imported at module level is fine, but for clarity)
