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
    """
    Execute inline shell commands in prompt content.
    
    Processes patterns like:
    - !`command` - inline command execution
    - ```! commands ``` - block command execution
    
    For security, MCP skills never execute shell commands (handled by caller).
    
    Args:
        text: Markdown content potentially containing shell commands
        context: Tool use context (unused in stub)
        slashCommandName: Name of the slash command for logging
        shell: Shell to use ('bash', 'powershell', etc.)
        
    Returns:
        Content with shell commands executed and replaced with output
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
