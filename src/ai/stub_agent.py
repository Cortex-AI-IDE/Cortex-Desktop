"""
stub_agent.py - Temporary placeholder for AIAgent during OpenHands migration
All methods are no-ops to prevent crashes
"""

from PyQt6.QtCore import QObject, pyqtSignal


class StubAIAgent(QObject):
    """Minimal stub that provides all signals but no functionality."""
    
    # Internal state (for compatibility)
    _project_root = None
    
    # Signals (matching original AIAgent interface)
    response_chunk = pyqtSignal(str)
    response_complete = pyqtSignal(str)
    request_error = pyqtSignal(str)
    file_generated = pyqtSignal(str, str)  # filepath, content
    file_edited_diff = pyqtSignal(str, str, str)  # filepath, old_text, new_text
    tool_activity = pyqtSignal(str, str)  # tool_name, status
    directory_contents = pyqtSignal(str, list)  # path, contents
    thinking_started = pyqtSignal()
    thinking_stopped = pyqtSignal()
    todos_updated = pyqtSignal(list, str)  # todos_list, main_task
    tool_summary_ready = pyqtSignal(dict)  # summary_data
    user_question_requested = pyqtSignal(str, list)  # question, options
    
    def __init__(self, **kwargs):
        super().__init__()
        # No initialization needed
    
    def update_settings(self, **kwargs):
        """No-op - settings will be handled by OpenHands SDK"""
        pass
    
    def set_terminal(self, terminal):
        """No-op - terminal integration will be handled by OpenHands SDK"""
        pass
    
    def process_message(self, message, images=None):
        """No-op - message processing will be handled by OpenHands SDK"""
        pass
    
    def stop_generation(self):
        """No-op - stopping will be handled by OpenHands SDK"""
        pass
    
    def set_project_root(self, path):
        """No-op - project context will be handled by OpenHands SDK"""
        self._project_root = path
    
    def set_project_context(self, context):
        """No-op - project context will be handled by OpenHands SDK"""
        pass
    
    def user_responded(self, answer):
        """No-op - user responses will be handled by OpenHands SDK"""
        pass
    
    def set_ui_parent(self, parent):
        """No-op - UI integration will be handled by OpenHands SDK"""
        pass
    
    def chat(self, message, context=""):
        """No-op - chat will be handled by OpenHands SDK"""
        pass
    
    def chat_with_enhancement(self, message, intent=None, route=None, tools=None, code_context=""):
        """No-op - enhanced chat will be handled by OpenHands SDK"""
        pass
    
    def chat_with_testing(self, *args, **kwargs):
        """No-op - testing chat will be handled by OpenHands SDK"""
        pass
    
    def generate_chat_title(self, message, conv_id):
        """No-op - title generation will be handled by OpenHands SDK"""
        return "New Chat"
    
    def get_last_enhancement_data(self):
        """Returns empty dict - enhancement data not available in stub"""
        return {}
    
    def stop(self):
        """No-op - stopping will be handled by OpenHands SDK"""
        pass
    
    def set_active_file(self, filepath, cursor_pos=None):
        """No-op - active file tracking will be handled by OpenHands SDK"""
        pass
    
    def clear_active_file(self):
        """No-op - clearing active file will be handled by OpenHands SDK"""
        pass
    
    def set_always_allowed(self, allowed):
        """No-op - permission settings will be handled by OpenHands SDK"""
        pass
    
    def set_interaction_mode(self, mode):
        """No-op - interaction mode will be handled by OpenHands SDK"""
        pass


def get_stub_agent(**kwargs):
    """Factory function to create stub agent instance."""
    return StubAIAgent(**kwargs)
