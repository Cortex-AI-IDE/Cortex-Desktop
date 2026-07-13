# constants/tools.py
# Python conversion of tools.ts constants
# Tool-related constants

# Tools that are disallowed for all agent types
ALL_AGENT_DISALLOWED_TOOLS: list = []

# Tools that are disallowed for custom agents
CUSTOM_AGENT_DISALLOWED_TOOLS: list = []

# Tools that async agents are allowed to use
ASYNC_AGENT_ALLOWED_TOOLS: list = []

# Tools allowed in coordinator mode
COORDINATOR_MODE_ALLOWED_TOOLS: list = []

__all__ = [
    'ALL_AGENT_DISALLOWED_TOOLS',
    'CUSTOM_AGENT_DISALLOWED_TOOLS',
    'ASYNC_AGENT_ALLOWED_TOOLS',
    'COORDINATOR_MODE_ALLOWED_TOOLS',
]
