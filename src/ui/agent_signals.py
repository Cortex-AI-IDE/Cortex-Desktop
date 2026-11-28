"""Signal contract between the agent backend and the chat UI.

Lives apart from chat_panel.py to break circular imports: chat_panel and
native_chat_bridge both import from here.
"""

from PyQt6.QtCore import QObject, pyqtSignal


class AgentSignals(QObject):
    """Signals emitted by the agent backend, consumed by the chat UI.

    PyQt does not support named args, so each signal's payload order is
    documented on the signal itself below.
    """

# streaming content
    thinking_delta = pyqtSignal(str)
    text_delta     = pyqtSignal(str)

# tool lifecycle
    tool_start     = pyqtSignal(str, str, object)
    tool_end       = pyqtSignal(str, str, object)  # tool_id, status, result_data (or None)
    tool_diff      = pyqtSignal(str, str, str)
    tool_image     = pyqtSignal(str)              # image the agent chose to show

# file edit lifecycle
    file_creating  = pyqtSignal(str, str)
    file_diff      = pyqtSignal(str, str, list)
    file_done      = pyqtSignal(str, int, int)

# turn lifecycle
    turn_done      = pyqtSignal()
    error          = pyqtSignal(str)

# secondary UI
    permission_req           = pyqtSignal(str, str, str, str)
    file_edit_permission_req = pyqtSignal()
    project_access_req       = pyqtSignal()
    question       = pyqtSignal(str, str, str, list, str)
    todos          = pyqtSignal(list, str)
    token_budget   = pyqtSignal(int, int, str)
    status         = pyqtSignal(str, str)
    turn_limit_hit = pyqtSignal(list, str)
    loop_debug     = pyqtSignal(object)  # loop result dict when loop terminates (verified/halt)
