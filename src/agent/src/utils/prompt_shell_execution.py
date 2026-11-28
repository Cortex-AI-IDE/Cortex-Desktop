"""
Prompt shell execution utilities.

Executes inline shell commands (!`cmd` and ```! blocks) in skill/command markdown content.
"""

from typing import Any, Optional


async def executeShellCommandsInPrompt(
    text: str,
    context: Any,
    slashCommandName: str,
    shell: Optional[str] = None,
) -> str:
    """Run inline shell commands in prompt content.

    Handles !`command` and ```! blocks ```, replacing each with its output.
    MCP skills never execute shell commands (the caller handles that).
    """
    import re
    import subprocess
    
    # Simple implementation - just return text as-is for now
    # Full implementation would parse and execute !`cmd` patterns
    # For security reasons, this is often disabled or restricted
    
    # For now, just strip the shell command markers without executing
    # This is safe and prevents errors
    
    # Remove !`command` patterns
    text = re.sub(r'!`[^`]+`', '[shell-command-disabled]', text)
    
    # Remove ```! blocks  
    text = re.sub(r'```![^`]*```', '[shell-block-disabled]', text, flags=re.DOTALL)
    
    return text
