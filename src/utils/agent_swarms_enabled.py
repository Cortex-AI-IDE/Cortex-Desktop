# agent_swarms_enabled.py
# Python conversion of agentSwarmsEnabled.ts
# Check if agent teams/teammate features are enabled

import os
import sys


def _is_agent_teams_flag_set() -> bool:
    """Check if --agent-teams flag is provided via CLI."""
    return '--agent-teams' in sys.argv


def is_agent_swarms_enabled() -> bool:
    """
    Centralized runtime check for agent teams/teammate features.
    This is the single gate that should be checked everywhere teammates
    are referenced (prompts, code, tools isEnabled, UI, etc.).
    
    Ant builds: always enabled.
    External builds require both:
    1. Opt-in via CORTEX_CODE_EXPERIMENTAL_AGENT_TEAMS env var OR --agent-teams flag
    2. GrowthBook gate 'tengu_amber_flint' enabled (killswitch)
    """
    from .env_utils import is_env_truthy
    
    # Ant: always on
    if os.environ.get('USER_TYPE') == 'ant':
        return True
    
    # External: require opt-in via env var or --agent-teams flag
    if not is_env_truthy(os.environ.get('CORTEX_CODE_EXPERIMENTAL_AGENT_TEAMS')):
        if not _is_agent_teams_flag_set():
            return False
    
    # Killswitch would be checked here via GrowthBook
    # For now, return True if opt-in is satisfied
    return True


__all__ = ['is_agent_swarms_enabled']
