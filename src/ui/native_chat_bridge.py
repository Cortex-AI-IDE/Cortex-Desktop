"""
native_chat_bridge.py, Adapter: agent_bridge signals → AgentSignals
====================================================================

Translates the existing agent_bridge.py signal format into the AgentSignals
format that ChatPanel (native PyQt6) expects.

Signal mapping:
  bridge.response_chunk(str)        → parse thinking tags → thinking_delta / text_delta
  bridge.response_complete(str)     → turn_done
  bridge.tool_activity(name,info,status) → tool_start / tool_end
  bridge.file_creating_started(path)    → file_creating
  bridge.file_edited_diff(path,old,new) → file_diff
  bridge.file_operation_completed(id,path,content,op) → file_done
  bridge.thinking_started()         → (handled via tags in response_chunk)
  bridge.thinking_stopped()         → (handled via tags in response_chunk)
"""

from __future__ import annotations
import re
import difflib
from PyQt6.QtCore import QObject, pyqtSignal

from src.ui.agent_signals import AgentSignals
from src.ui.chat_text import full_clean, strip_all_control_tags, strip_todo_blocks
from src.utils.logger import get_logger as _get_cortex_logger
# Attach the ~/.cortex/logs/cortex.log handler to this module's logger once -
# all the local logging.getLogger(__name__) calls below resolve to the same
# logger object, so chunk-routing decisions become visible in cortex.log.
_get_cortex_logger(__name__)


# ── Todo line pattern for extracting from text ──
_TODO_LINE = re.compile(r'^[ \t]*[-*]?\s*\[([ xX✓✔•·])\]\s*(.+?)\s*$', re.MULTILINE)

# Compiled once: extracts "text":"..." JSON fragments from streamed activity info.
# Kept module-level so it is never recompiled per streaming tick.
_RE_TEXT_JSON = re.compile(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}')
# A tool-activity info that is itself a thinking fragment, not tool data.
_RE_THINKING_INFO = re.compile(r'\{\s*"type"\s*:\s*"(thinking|reasoning)"')

# Hard cap on the size of a single tool-activity `info` payload that the GUI
# thread will process. Streaming providers can emit multi-megabyte `info`
# blobs (e.g. large Write/Edit content or accumulated thinking); running the
# regex + join + replace chain over that on the GUI thread froze the whole app
# for ~60s (see ui_stall_detector reports). Nobody can read millions of chars
# in a thinking bubble, so we only ever process the most recent tail.
_MAX_ACTIVITY_INFO = 65536


class NativeChatBridge(QObject):
    """
    Adapts agent_bridge signals into AgentSignals for the native ChatPanel.

    Usage in main_window.py:
        self._native_bridge = NativeChatBridge(self._ai_agent)
        self._ai_chat.bind(self._native_bridge.signals, bridge=self._ai_agent)
    """

    def __init__(self, agent_bridge, parent=None):
        super().__init__(parent)
        self._bridge = agent_bridge
        self.signals = AgentSignals()

        # State for parsing thinking tags from response_chunk
        self._in_thought = False
        self._text_emitted = False  # track if any text was streamed
        self._tool_counter = 0
        self._active_tools: dict[str, str] = {}
        self._seen_tools: set[str] = set()  # keys of tools whose card is OPEN
        self._active_start_key: dict[str, str] = {}  # tool_id -> its dedup key

        # Tag patterns
        self._RE_THOUGHT_DELTA = re.compile(
            r'<cortex_thought_delta>(.*?)</cortex_thought_delta>',
            re.IGNORECASE | re.DOTALL
        )
        self._RE_THOUGHT_START = re.compile(
            r'<cortex_thought_start>|<cortex_thought>|'
            r'<(think|thinking|antThinking|scratchpad)(\s[^>]*)?>',
            re.IGNORECASE
        )
        self._RE_THOUGHT_END = re.compile(
            r'<cortex_thought_end>|</cortex_thought>|'
            r'</(think|thinking|antThinking|scratchpad)\s*>',
            re.IGNORECASE
        )
        # Raw "thinking {"text": "..."}" JSON fragments from some providers
        self._RE_THINKING_JSON = re.compile(
            r'^\s*thinking\s*\{.*?"text"\s*:\s*"(.*?)".*?\}\s*$',
            re.DOTALL
        )

        # Buffer for accumulating text to detect todo blocks across chunks
        self._text_buffer = ""
        self._todo_emit_count = 0  # track how many times we emitted todos

        self._connect_bridge_signals()

    def _connect_bridge_signals(self):
        """Wire agent_bridge signals to our translation handlers."""
        b = self._bridge

        # Streaming text (prose + thinking mixed)
        b.response_chunk.connect(self._on_response_chunk)
        b.response_complete.connect(self._on_response_complete)

        # Tool activity
        b.tool_activity.connect(self._on_tool_activity)

        # File operations
        b.file_creating_started.connect(self._on_file_creating)
        b.file_edited_diff.connect(self._on_file_diff)
        b.file_operation_completed.connect(self._on_file_done)

        # Errors
        b.request_error.connect(self._on_error)

        # Phase 8: secondary UI signals
        b.permission_requested.connect(self._on_permission_requested)
        b.file_edit_permission_requested.connect(self._on_file_edit_permission_requested)
        b.project_access_requested.connect(self._on_project_access_requested)
        b.user_question_requested.connect(self._on_user_question)
        b.todos_updated.connect(self._on_todos_updated)
        b.context_budget_update.connect(self._on_context_budget)
        b.agent_status_update.connect(self._on_agent_status)
        b.turn_limit_hit.connect(self._on_turn_limit_hit)

    def _on_response_chunk(self, chunk: str):
        """Parse thinking tags and route to appropriate signal."""
        try:
            import logging
            log = logging.getLogger(__name__)
            if not chunk:
                return
            log.debug("[NATIVE-BRIDGE] response_chunk received: len=%d", len(chunk))
            self._handle_response_chunk(chunk)
        except Exception as e:
            import traceback
            try:
                logging.getLogger(__name__).error(
                    "[NATIVE-BRIDGE] _on_response_chunk CRASH: %s\n%s",
                    e, traceback.format_exc()
                )
            except Exception:
                pass

    def _handle_response_chunk(self, chunk: str):
        import logging
        log = logging.getLogger(__name__)

        # 1. Extract <cortex_thought_delta>content</cortex_thought_delta>
        delta_match = self._RE_THOUGHT_DELTA.search(chunk)
        if delta_match:
            self.signals.thinking_delta.emit(delta_match.group(1))
            return

        # 2. Thought start tags, handle chunk that may ALSO contain end tag
        #    e.g. "<think>reasoning</think>actual text" in a single chunk
        start_match = self._RE_THOUGHT_START.search(chunk)
        if start_match:
            self._in_thought = True
            # Check if the SAME chunk also has a closing tag
            end_match = self._RE_THOUGHT_END.search(chunk)
            if end_match:
                # Extract thinking content (between start and end tags)
                think_start = start_match.end()
                think_end = end_match.start()
                think_content = chunk[think_start:think_end].strip()
                if think_content:
                    self.signals.thinking_delta.emit(think_content)
                # Extract text content (after end tag).
                # NO .strip(): the leading whitespace of the resuming content
                # is REAL ("...on **port" [thought] " 8084**"), stripping it
                # glued words across every thought→text transition (Grok
                # interleaves thinking constantly, so commands/prose displayed
                # as "port8084", "```powershellnpx", etc.).
                after_think = chunk[end_match.end():]
                self._in_thought = False
                if after_think:
                    if after_think.strip():
                        self._text_emitted = True
                    self.signals.text_delta.emit(after_think)
                return
            else:
                # Only start tag, stream thinking content
                # lstrip only: the trailing space belongs to the next chunk
                # ("Let me check the " + "file" must not become "thefile").
                cleaned = self._RE_THOUGHT_START.sub('', chunk).lstrip()
                if cleaned:
                    self.signals.thinking_delta.emit(cleaned)
                return

        # 3. Thought end tags
        if self._RE_THOUGHT_END.search(chunk):
            self._in_thought = False
            # NO .strip(), whitespace around the tag belongs to the content
            # stream (see the thought-boundary gluing note above).
            cleaned = self._RE_THOUGHT_END.sub('', chunk)
            if cleaned:
                if cleaned.strip():
                    self._text_emitted = True
                    log.info(f"[BRIDGE] text_delta emitted (thought end): {len(cleaned)} chars")
                self.signals.text_delta.emit(cleaned)
            return

        # 4. Plain text, route based on current state
        if self._in_thought:
            self.signals.thinking_delta.emit(chunk)
        else:
            # STREAM RAW, do NOT strip control tags during streaming.
            # strip_all_control_tags on partial content mangles incomplete
            # XML tags, causing text to vanish until on_turn_done re-renders.
            # Full cleaning happens in ChatPanel.on_turn_done() via _light_clean().
            # BUG history: this was gated on `if chunk.strip():`, which DROPPED
            # pure-whitespace deltas entirely, models emit standalone " " and
            # "\n\n" tokens routinely, so words glued ("port" + [" " dropped] +
            # "8084" → "port8084") and paragraphs merged. Every chunk must be
            # forwarded byte-for-byte; only _text_emitted stays gated on
            # non-whitespace (it controls the response_complete fallback).
            if chunk:
                if chunk.strip():
                    self._text_emitted = True
                log.debug(f"[Bridge] text_delta emit: len={len(chunk)}, preview={repr(chunk[:80])}")
                self.signals.text_delta.emit(chunk)

    def _on_response_complete(self, response: str):
        """Turn is done, stop all remaining tools, flush text, emit turn_done."""
        import logging
        log = logging.getLogger(__name__)
        log.info(f"[TODO-COMPLETE] Response length: {len(response or '')}")
        self._in_thought = False
        # Stop all tools that are still running
        for name, tool_id in list(self._active_tools.items()):
            self.signals.tool_end.emit(tool_id, "ok", None)

        # ALWAYS try to extract todos from the full response
        # This catches todos even if text was already streamed
        if response and response.strip():
            self._maybe_extract_todos(response)

        if not self._text_emitted and response and response.strip():
            self.signals.text_delta.emit(response)
        self._text_emitted = False
        self._active_tools.clear()
        self._seen_tools.clear()
        self._active_start_key.clear()
        self.signals.turn_done.emit()

    def _maybe_extract_todos(self, text: str) -> bool:
        """If the agent wrote a todo block as text, parse it and emit the widget signal.
        Returns True if todos were extracted (caller should skip emitting text)."""
        if not text:
            return False
        matches = _TODO_LINE.findall(text)
        import logging
        logging.getLogger(__name__).info(f"[TODO-EXTRACT] Found {len(matches)} todo lines in response text")
        if len(matches) >= 2:
            todos = []
            for mark, label in matches:
                done = mark.strip() in ("x", "X", "✓", "✔")
                in_prog = mark.strip() in ("•", "·")
                todos.append({
                    "text": label,
                    "done": done,
                    "status": "completed" if done else ("in_progress" if in_prog else "pending"),
                })
            logging.getLogger(__name__).info(f"[TODO-EXTRACT] Emitting {len(todos)} todos to widget")
            self.signals.todos.emit(todos, "")
            return True
        return False

    def _on_tool_activity(self, name: str, info: str, status: str):
        """Map tool activity to tool_start / tool_end."""
        # GUI-THREAD GUARD: cap the payload before any regex/join/replace work.
        # A single streamed `info` can be multiple megabytes; scanning that on
        # the GUI thread froze the app for ~60s. Keep only the most recent tail
        # so downstream string work is always bounded.
        if info and len(info) > _MAX_ACTIVITY_INFO:
            info = info[-_MAX_ACTIVITY_INFO:]
        # Filter out thinking/reasoning chunks that come through tool_activity
        if name.lower() in ("thinking", "thought", "think", "reasoning"):
            if info:
                if '"text"' in info:
                    texts = _RE_TEXT_JSON.findall(info)
                    if texts:
                        combined = "".join(texts).replace('\\n', '\n').replace('\\"', '"')
                        self.signals.thinking_delta.emit(combined)
                        return
                self.signals.thinking_delta.emit(info)
            return
        # Filter out JSON thinking fragments in info field. ONLY a thinking
        # fragment: Grep/search results carry a "text" key per match
        # ({"file":..,"line":..,"text":"<matched line>"}), and treating those
        # as thinking glued every matched line into a Thought card with no
        # separators ("...still" + "showed...", "fix)." + "### 1a.") while the
        # Grep card never got its results.
        _s = (info or "").lstrip()
        if '"text"' in _s and (_s.startswith("thinking") or _RE_THINKING_INFO.match(_s)):
            texts = _RE_TEXT_JSON.findall(info)
            if texts:
                combined = "".join(texts).replace('\\n', '\n').replace('\\"', '"')
                if combined.strip():
                    self.signals.thinking_delta.emit(combined)
                    return

        # Deduplicate: key on the FULL args, not info[:50].
        #
        # info[:50] collided DIFFERENT tools onto one key whenever they shared
        # a long common prefix. run_command is the worst case: its info always
        # starts with the same 'cd "<project path>" && …', so the first 50
        # chars were identical for every command and their tool cards were all
        # suppressed as "duplicates" (field log: 8x THOUGHT-SEG SUPPRESSED, same
        # truncated key) — the "Let me check X … Let me find Y …" narration then
        # walled up with no cards between it. A hash of the full info gives each
        # distinct command its own key, while a genuine duplicate emit (byte-
        # identical info) still hashes the same and is caught.
        import hashlib as _hashlib
        dedup_key = f"{name}:{_hashlib.md5((info or '').encode('utf-8', 'ignore')).hexdigest()}"
        if status in ("running", "started", "pending"):
            # ── Segment boundary: a real tool is starting, so any reasoning
            # segment is over. Reset _in_thought, if a </think> tag was split
            # across stream chunks the end-regex never matched and the flag
            # would stay True forever, silently routing ALL later prose
            # (including the final answer) into the collapsed Thought block.
            # Also reset _text_emitted so it tracks only the segment AFTER the
            # last tool: _on_response_complete's fallback re-emit then fires
            # whenever the final answer produced no visible text, instead of
            # being suppressed by prose from earlier agentic turns.
            self._in_thought = False
            self._text_emitted = False
            if dedup_key in self._seen_tools:
                # Suppressed: this exact name+info[:50] was already shown earlier
                # in the run (session-wide set, cleared only at response_complete).
                # This is invisible in the UI, its tool card vanishes AND, because
                # tool_end still fires, the surrounding reasoning can merge into a
                # "wall" Thought unless on_tool_end closes the think block. Log it
                # so this path is finally observable in cortex.log.
                import logging
                logging.getLogger(__name__).info(
                    f"[THOUGHT-SEG] tool_start SUPPRESSED (dedup) name={name!r} "
                    f"key={dedup_key[:70]!r} — card hidden; on_tool_end must close any open thought"
                )
                return  # already processed this tool
            self._seen_tools.add(dedup_key)
            self._tool_counter += 1
            tool_id = f"tool_{self._tool_counter}"
            self._active_tools[name] = tool_id
            # Remember this start's key so it is RELEASED when the tool ends
            # (below). The dedup then only catches a genuine duplicate start
            # emitted while the tool's card is still open; a legitimate re-run
            # later (the agent re-Reads/re-Greps the same file, which it does
            # constantly) gets its own card instead of being suppressed for the
            # whole response. Session-wide suppression was hiding those cards,
            # so the "Let me read X … Let me read Y …" narration between them
            # collapsed into one wall Thought - the reported bug.
            self._active_start_key[tool_id] = dedup_key
            # Parse JSON data for tool cards, fallback to clean display text
            data = self._parse_tool_data(info)
            if data:
                self.signals.tool_start.emit(tool_id, name, data)
            else:
                clean_info = self._clean_tool_info(info)
                self.signals.tool_start.emit(tool_id, name, clean_info)
        elif status in ("completed", "done", "ok", "success"):
            tool_id = self._active_tools.pop(name, f"tool_?")
            # Release the start key so a later re-run of this tool shows a
            # fresh card instead of being dedup-suppressed for the whole run.
            self._seen_tools.discard(self._active_start_key.pop(tool_id, ""))
            # Parse result data from info so tool cards can display output
            result_data = self._parse_tool_data(info) if info else None
            self.signals.tool_end.emit(tool_id, "ok", result_data)
        elif status in ("error", "failed"):
            tool_id = self._active_tools.pop(name, f"tool_?")
            self._seen_tools.discard(self._active_start_key.pop(tool_id, ""))
            result_data = self._parse_tool_data(info) if info else None
            self.signals.tool_end.emit(tool_id, "error", result_data)

    def _on_file_creating(self, file_path: str):
        """File creation started."""
        file_id = f"file_{id(file_path)}"
        self.signals.file_creating.emit(file_id, file_path)

    def _on_file_diff(self, file_path: str, original: str, new: str):
        """File edit diff, convert to hunk_lines format."""
        file_id = f"file_{id(file_path)}"
        hunk_lines = self._compute_hunk_lines(original, new)
        self.signals.file_diff.emit(file_id, file_path, hunk_lines)

    def _on_file_done(self, card_id: str, file_path: str, content: str, op_type: str):
        """File operation completed."""
        added = content.count('\n') + 1 if content else 0
        self.signals.file_done.emit(card_id, added, 0)

    def _on_error(self, error: str):
        """Error, emit error message to chat AND close the turn."""
        self.signals.error.emit(error)
        self.signals.turn_done.emit()

    # ── Phase 8: secondary UI handlers ──
    def _on_file_edit_permission_requested(self):
        self.signals.file_edit_permission_req.emit()

    def _on_project_access_requested(self):
        self.signals.project_access_req.emit()

    def _on_permission_requested(self, command: str, warning: str, files_json: str):
        request_id = f"perm_{self._tool_counter}"
        self.signals.permission_req.emit(request_id, command, warning, files_json)

    def _on_user_question(self, payload: dict):
        question_id = payload.get("id", "")
        question = payload.get("question", payload.get("text", ""))
        details = (payload.get("details") or "").strip()
        if details:  # extra context the model sent; the card used to drop it
            question = f"{question}\n\n{details}"
        qtype = payload.get("type", "text")
        choices = payload.get("choices", [])
        default = payload.get("default", "")
        self.signals.question.emit(question_id, question, qtype, choices, default)

    def _on_todos_updated(self, todos_list: list, main_task: str):
        """Forward todos from agent_bridge to ChatPanel widget."""
        import logging
        logging.getLogger(__name__).info(f"[TODO-BRIDGE] Received {len(todos_list)} todos from agent_bridge")
        self.signals.todos.emit(todos_list, main_task)

    def _on_context_budget(self, used: int, budget: int, provider: str):
        self.signals.token_budget.emit(used, budget, provider)

    def _on_agent_status(self, status_type: str, message: str):
        self.signals.status.emit(status_type, message)

    def _on_turn_limit_hit(self, pending_todos: list, checkpoint: str):
        """Forward turn_limit_hit from agent_bridge to ChatPanel for ResumeTaskCard."""
        self.signals.turn_limit_hit.emit(pending_todos, checkpoint)

    def _clean_tool_info(self, info: str) -> str:
        """Extract meaningful display text from tool info (often raw JSON)."""
        if not info:
            return ""
        info = info.strip()
        # If it's JSON, extract key fields
        if info.startswith('{') and info.endswith('}'):
            import json
            try:
                data = json.loads(info)
                # Show full command if available
                if 'command' in data and data['command']:
                    return str(data['command'])
                # Show pattern with context for grep
                if 'pattern' in data and data['pattern']:
                    pattern = str(data['pattern'])
                    if 'path' in data and data['path']:
                        path = str(data['path'])
                        fname = path.split('\\')[-1].split('/')[-1]
                        return f'"{pattern}" in {fname}'
                    return pattern
                # Show query for search tools (WebSearch, SemanticSearch)
                if 'query' in data and data['query']:
                    return str(data['query'])
                # Show URL for WebFetch
                if 'url' in data and data['url']:
                    url = str(data['url'])
                    # Shorten long URLs, show hostname + path
                    from urllib.parse import urlparse
                    try:
                        parsed = urlparse(url)
                        return f'{parsed.hostname}{parsed.path}' if parsed.path and parsed.path != '/' else parsed.hostname
                    except Exception:
                        return url[:80]
                # Show path (shortened but not just filename)
                for key in ('path', 'file_path', 'file'):
                    if key in data and data[key]:
                        val = str(data[key])
                        # Shorten long paths, show last 2 segments
                        parts = val.replace('\\', '/').split('/')
                        if len(parts) > 2:
                            return '/'.join(parts[-2:])
                        return val
                # Fallback: show first string value
                for v in data.values():
                    if isinstance(v, str) and v:
                        return v
                return ""
            except (json.JSONDecodeError, ValueError):
                pass
        return info

    def _parse_tool_data(self, info: str) -> dict:
        """Parse tool info JSON into a data dict for tool cards."""
        if not info:
            return {}
        info = info.strip()
        if info.startswith('{') and info.endswith('}'):
            import json
            try:
                data = json.loads(info)
                return data
            except (json.JSONDecodeError, ValueError):
                pass
        return {"info": info}

    def _compute_hunk_lines(self, original: str, new: str) -> list:
        """
        Compute diff hunk from original and new content using difflib.
        Returns list of 4-tuples: (kind, old_lineno, new_lineno, text).
        kind in {'add', 'del', 'ctx', 'hunk'}.
        """
        o = (original or "").splitlines()
        n = (new or "").splitlines()
        rows = []
        sm = difflib.SequenceMatcher(a=o, b=n)

        opcodes = sm.get_opcodes()
        if not opcodes:
            return rows

        # Find first and last changed line for a proper hunk header
        first_old = first_new = None
        last_old_end = last_new_end = 0
        for tag, i1, i2, j1, j2 in opcodes:
            if tag != "equal":
                if first_old is None:
                    first_old = i1 + 1
                    first_new = j1 + 1
                last_old_end = i2
                last_new_end = j2
        if first_old is None:
            return rows

        old_count = last_old_end - (first_old - 1)
        new_count = last_new_end - (first_new - 1)
        rows.append(("hunk", None, None,
                      f"@@ -{first_old},{old_count} +{first_new},{new_count} @@"))

        # Keep only CTX unchanged lines either side of a change (the card
        # shows 3). Emitting every unchanged line made a 1,500-line CSS edit
        # a 1,500-row list, and the storage cap then saved only context, so
        # the card expanded to nothing after an IDE restart.
        CTX = 3
        last = len(opcodes) - 1
        for idx, (tag, i1, i2, j1, j2) in enumerate(opcodes):
            if tag == "equal":
                lead = CTX if idx > 0 else 0      # after the previous change
                tail = CTX if idx < last else 0   # before the next change
                ks = list(range(i1, i2))
                if len(ks) > lead + tail:
                    ks = ks[:lead] + ([None] if lead and tail else []) + ks[len(ks) - tail:]
                for k in ks:
                    if k is None:
                        rows.append(("hunk", None, None, "@@ ..."))
                    else:
                        rows.append(("ctx", k + 1, j1 + (k - i1) + 1, o[k]))
            elif tag == "delete":
                for k in range(i1, i2):
                    rows.append(("del", k + 1, None, o[k]))
            elif tag == "insert":
                for k in range(j1, j2):
                    rows.append(("add", None, k + 1, n[k]))
            elif tag == "replace":
                for k in range(i1, i2):
                    rows.append(("del", k + 1, None, o[k]))
                for k in range(j1, j2):
                    rows.append(("add", None, k + 1, n[k]))

        return rows

    def disconnect_all(self):
        """Disconnect all bridge signals (for cleanup)."""
        try:
            self._bridge.response_chunk.disconnect(self._on_response_chunk)
            self._bridge.response_complete.disconnect(self._on_response_complete)
            self._bridge.tool_activity.disconnect(self._on_tool_activity)
            self._bridge.file_creating_started.disconnect(self._on_file_creating)
            self._bridge.file_edited_diff.disconnect(self._on_file_diff)
            self._bridge.file_operation_completed.disconnect(self._on_file_done)
            self._bridge.request_error.disconnect(self._on_error)
        except Exception:
            pass
