"""
agent_signals.py — Signal contract between agent backend and chat UI.

Extracted from chat_panel.py to break circular import coupling.
Both chat_panel.py and native_chat_bridge.py import from here.
"""

from PyQt6.QtCore import QObject, pyqtSignal


class AgentSignals(QObject):
    """Signal contract between agent backend and chat UI.

    Signal signatures (PyQt does not support named args):
        thinking_delta(text: str)
        text_delta(text: str)
        tool_start(tool_id: str, name: str, arg_or_data: object)
        tool_end(tool_id: str, status: str)  — status is "ok" or "error"
        tool_diff(tool_id: str, added_line: str, removed_line: str)
        file_creating(file_id: str, filename: str)
        file_diff(file_id: str, filename: str, hunk_lines: list[str])
        file_done(file_id: str, added_count: int, removed_count: int)
        turn_done()
        error(message: str)
        permission_req(request_id: str, command: str, warning: str, files_json: str)
        file_edit_permission_req()
        question(question_id: str, question: str, qtype: str, choices: list[str], default: str)
        todos(todos_list: list[dict], main_task: str)
        token_budget(used: int, budget: int, provider: str)
        status(type: str, message: str)
        turn_limit_hit(pending_todos: list[dict], checkpoint: str)
    """

    # ---- streaming content ----
    thinking_delta = pyqtSignal(str)
    text_delta     = pyqtSignal(str)

    # ---- tool lifecycle ----
    tool_start     = pyqtSignal(str, str, object)
    tool_end       = pyqtSignal(str, str, object)  # tool_id, status, result_data (or None)
    tool_diff      = pyqtSignal(str, str, str)

    # ---- file edit lifecycle ----
    file_creating  = pyqtSignal(str, str)
    file_diff      = pyqtSignal(str, str, list)
    file_done      = pyqtSignal(str, int, int)

    # ---- turn lifecycle ----
    turn_done      = pyqtSignal()
    error          = pyqtSignal(str)

    # ---- secondary UI ----
    permission_req           = pyqtSignal(str, str, str, str)
    file_edit_permission_req = pyqtSignal()
    project_access_req       = pyqtSignal()
    question       = pyqtSignal(str, str, str, list, str)
    todos          = pyqtSignal(list, str)
    token_budget   = pyqtSignal(int, int, str)
    status         = pyqtSignal(str, str)
    turn_limit_hit = pyqtSignal(list, str)
