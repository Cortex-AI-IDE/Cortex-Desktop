# constants/prompts.py
# Stub file for prompt generation utilities
# Note: This is a stub - full implementation depends on TypeScript source

from typing import Any, Dict, List, Optional


async def enhance_system_prompt_with_env_details(
    prompts: List[str],
    model: str,
    additional_working_directories: List[str],
) -> List[str]:
    """Enhance system prompts with environment details."""
    return prompts


async def get_system_prompt(
    tools: List[Any],
    model: str,
    additional_working_directories: List[str],
    mcp_clients: Optional[List[Any]] = None,
) -> str:
    """Get the system prompt for the current context."""
    return ""


__all__ = [
    "enhance_system_prompt_with_env_details",
    "get_system_prompt",
]
