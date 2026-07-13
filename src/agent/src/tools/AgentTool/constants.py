# constants.py
"""
Constants for AgentTool module.

Defines agent tool names, types, and configuration values.
"""

from typing import Set

# Agent tool name
AGENT_TOOL_NAME = 'Agent'

# Legacy wire name for backward compatibility (permission rules, hooks, resumed sessions)
LEGACY_AGENT_TOOL_NAME = 'Task'

# Verification agent type identifier
VERIFICATION_AGENT_TYPE = 'verification'

# Built-in agents that run once and return a report — the parent never
# SendMessages back to continue them. Skip the agentId/SendMessage/usage
# trailer for these to save tokens (~135 chars × 34M Explore runs/week).
ONE_SHOT_BUILTIN_AGENT_TYPES: Set[str] = frozenset([
    'Explore',
    'Plan',
])
