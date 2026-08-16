# ------------------------------------------------------------
# constants.py
# Python conversion of REPLTool/constants.ts
# 
# REPL mode configuration and tool access control for AI Agent IDE.
# Controls when REPL mode is enabled and which tools are REPL-only.
# ------------------------------------------------------------

import os
from typing import Set

# Import dependencies
try:
    from ...utils.envUtils import isEnvDefinedFalsy, isEnvTruthy
except ImportError:
    def isEnvDefinedFalsy(value):
        """Check if env var is defined and falsy."""
        if value is None:
            return False
        return value.strip().lower() in ('0', 'false', 'no', 'off')
    
    def isEnvTruthy(value):
        """Check if env var is truthy."""
        if value is None:
            return False
        return value.strip().lower() in ('1', 'true', 'yes', 'on')

try:
    from ..AgentTool.constants import AGENT_TOOL_NAME
except ImportError:
    AGENT_TOOL_NAME = 'Agent'

try:
    from ..BashTool.toolName import BASH_TOOL_NAME
except ImportError:
    BASH_TOOL_NAME = 'Bash'

try:
    from ..FileEditTool.constants import FILE_EDIT_TOOL_NAME
except ImportError:
    FILE_EDIT_TOOL_NAME = 'FileEdit'

try:
    from ..FileReadTool.prompt import FILE_READ_TOOL_NAME
except ImportError:
    FILE_READ_TOOL_NAME = 'FileRead'

try:
    from ..FileWriteTool.prompt import FILE_WRITE_TOOL_NAME
except ImportError:
    FILE_WRITE_TOOL_NAME = 'FileWrite'

try:
    from ..GlobTool.prompt import GLOB_TOOL_NAME
except ImportError:
    GLOB_TOOL_NAME = 'Glob'

try:
    from ..GrepTool.prompt import GREP_TOOL_NAME
except ImportError:
    GREP_TOOL_NAME = 'Grep'

try:
    from ..NotebookEditTool.constants import NOTEBOOK_EDIT_TOOL_NAME
except ImportError:
    NOTEBOOK_EDIT_TOOL_NAME = 'NotebookEdit'


REPL_TOOL_NAME = 'REPL'


def isReplModeEnabled() -> bool:
    """
    Check if REPL mode is enabled.
    
    REPL mode is default-on for ants in the interactive AI agent (opt out with
    CORTEX_CODE_REPL=0). The legacy CORTEX_REPL_MODE=1 also forces it on.
    
    SDK entrypoints (sdk-ts, sdk-py, sdk-cli) are NOT defaulted on — SDK
    consumers script direct tool calls (Bash, Read, etc.) and REPL mode
    hides those tools. USER_TYPE is a build-time --define, so the ant-native
    binary would otherwise force REPL mode on every SDK subprocess regardless
    of the env the caller passes.
    """
    # Check if explicitly disabled
    if isEnvDefinedFalsy(os.environ.get('CORTEX_CODE_REPL')):
        return False
    
    # Check legacy env var
    if isEnvTruthy(os.environ.get('CORTEX_REPL_MODE')):
        return True
    
    # Default: enabled for internal agents in AI agent mode
    user_type = os.environ.get('USER_TYPE', '')
    entrypoint = os.environ.get('CORTEX_CODE_ENTRYPOINT', '')
    
    return user_type == 'ant' and entrypoint == 'cli'


# Tools that are only accessible via REPL when REPL mode is enabled.
# When REPL mode is on, these tools are hidden from Cortex's direct use,
# forcing Cortex to use REPL for batch operations.
REPL_ONLY_TOOLS: Set[str] = {
    FILE_READ_TOOL_NAME,
    FILE_WRITE_TOOL_NAME,
    FILE_EDIT_TOOL_NAME,
    GLOB_TOOL_NAME,
    GREP_TOOL_NAME,
    BASH_TOOL_NAME,
    NOTEBOOK_EDIT_TOOL_NAME,
    AGENT_TOOL_NAME,
}


# Snake_case aliases for Python convention compatibility
is_repl_mode_enabled = isReplModeEnabled
