"""
Permission explanation system for Cortex AI IDE.

Uses LLM to generate human-readable explanations of what commands do,
why they're being run, and their potential risks. Improves user trust
and transparency in AI agent operations.

Multi-LLM Support: Works with all providers that support tool use
(Anthropic, OpenAI, Gemini, DeepSeek, Mistral, Groq, SiliconFlow).

Risk Levels:
- LOW: Safe development workflows (ls, cat, git status)
- MEDIUM: Recoverable changes (rm -rf build/, npm install)
- HIGH: Dangerous/irreversible operations (rm -rf /, git push --force)

Example:
    >>> from permissionExplainer import generate_permission_explanation
    >>> explanation = await generate_permission_explanation(
    ...     tool_name="Bash",
    ...     tool_input="rm -rf build/",
    ...     messages=conversation_history
    ... )
    >>> explanation.risk_level
    'MEDIUM'
    >>> explanation.explanation
    'Removes the build directory and all its contents'
"""

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ============================================================================
# Type Definitions and Schemas
# ============================================================================

# Risk level type - using Literal for proper validation
RiskLevel = Literal['LOW', 'MEDIUM', 'HIGH']

# Map risk levels to numeric values for analytics
RISK_LEVEL_NUMERIC: dict[RiskLevel, int] = {
    'LOW': 1,
    'MEDIUM': 2,
    'HIGH': 3,
}

# Error type codes for analytics
ERROR_TYPE_PARSE = 1
ERROR_TYPE_NETWORK = 2
ERROR_TYPE_UNKNOWN = 3


class PermissionExplanation(BaseModel):
    """Structured permission explanation from LLM."""
    risk_level: RiskLevel = Field(
        description='LOW (safe dev workflows), MEDIUM (recoverable changes), HIGH (dangerous/irreversible)'
    )
    explanation: str = Field(description='What this command does (1-2 sentences)')
    reasoning: str = Field(
        description='Why YOU are running this command. Start with "I" - e.g. "I need to check the file contents"'
    )
    risk: str = Field(description='What could go wrong, under 15 words')


class ExplainCommandToolInput(BaseModel):
    """Tool input schema for forced structured output."""
    explanation: str = Field(description='What this command does (1-2 sentences)')
    reasoning: str = Field(
        description='Why YOU are running this command. Start with "I"'
    )
    risk: str = Field(description='What could go wrong, under 15 words')
    risk_level: RiskLevel = Field(
        description='LOW, MEDIUM, or HIGH risk level'
    )


# ============================================================================
# Tool Definition for Structured Output
# ============================================================================

SYSTEM_PROMPT = "Analyze shell commands and explain what they do, why you're running them, and potential risks."

# Tool definition for forced structured output
EXPLAIN_COMMAND_TOOL = {
    'name': 'explain_command',
    'description': 'Provide an explanation of a shell command',
    'input_schema': {
        'type': 'object',
        'properties': {
            'explanation': {
                'type': 'string',
                'description': 'What this command does (1-2 sentences)',
            },
            'reasoning': {
                'type': 'string',
                'description': 'Why YOU are running this command. Start with "I"',
            },
            'risk': {
                'type': 'string',
                'description': 'What could go wrong, under 15 words',
            },
            'risk_level': {
                'type': 'string',
                'enum': ['LOW', 'MEDIUM', 'HIGH'],
                'description': 'Risk level classification',
            },
        },
        'required': ['explanation', 'reasoning', 'risk', 'risk_level'],
    },
}


# ============================================================================
# Configuration
# ============================================================================

# Permission explainer configuration
PERMISSION_EXPLAINER_CONFIG = {
    'enabled': True,           # Whether the feature is enabled
    'max_context_chars': 1000, # Maximum characters for conversation context
    'timeout_seconds': 10,     # Timeout for LLM request
}


def is_permission_explainer_enabled() -> bool:
    """
    Check if the permission explainer feature is enabled.
    
    Enabled by default; users can opt out via config.
    
    Returns:
        True if permission explainer is enabled
    """
    return PERMISSION_EXPLAINER_CONFIG.get('enabled', True)


def update_permission_explainer_config(config: dict[str, Any]) -> None:
    """
    Update permission explainer configuration.
    
    Args:
        config: Configuration dictionary with:
            - enabled: Whether the feature is enabled
            - max_context_chars: Maximum context characters
            - timeout_seconds: Request timeout
    """
    global PERMISSION_EXPLAINER_CONFIG
    PERMISSION_EXPLAINER_CONFIG.update(config)
    logger.info(f'[permission-explainer] Configuration updated: {PERMISSION_EXPLAINER_CONFIG}')


# ============================================================================
# Utility Functions
# ============================================================================

def format_tool_input(input_data: Any) -> str:
    """
    Format tool input for display in prompt.
    
    Args:
        input_data: Tool input (string or dict)
        
    Returns:
        Formatted string representation
    """
    if isinstance(input_data, str):
        return input_data
    
    try:
        import json
        return json.dumps(input_data, indent=2, default=str)
    except (TypeError, ValueError):
        return str(input_data)


def extract_conversation_context(
    messages: list[dict[str, Any]],
    max_chars: int = 1000,
) -> str:
    """
    Extract recent conversation context from messages for the explainer.
    
    Returns a summary of recent assistant messages to provide context
    for "why" this command is being run.
    
    Args:
        messages: Conversation message history
        max_chars: Maximum characters to extract (default 1000)
        
    Returns:
        String containing recent conversation context
    """
    # Get recent assistant messages (they contain AI's reasoning)
    assistant_messages = [
        msg for msg in messages 
        if msg.get('role') == 'assistant'
    ][-3:]  # Last 3 assistant messages
    
    context_parts = []
    total_chars = 0
    
    for msg in reversed(assistant_messages):
        # Extract text content from assistant message
        content = msg.get('content', '')
        
        # Handle different content formats
        if isinstance(content, str):
            text_blocks = content
        elif isinstance(content, list):
            # List of content blocks
            text_blocks = ' '.join([
                block.get('text', '') 
                for block in content 
                if isinstance(block, dict) and block.get('type') == 'text'
            ])
        else:
            text_blocks = str(content)
        
        if text_blocks and total_chars < max_chars:
            remaining = max_chars - total_chars
            # Account for separator if this isn't the first part
            if context_parts:
                remaining -= 2  # Subtract length of '\n\n'
            
            if remaining <= 0:
                break
            
            # Truncate with ellipsis if needed (ellipsis takes 3 chars)
            if len(text_blocks) > remaining:
                truncate_at = remaining - 3
                if truncate_at > 0:
                    truncated = text_blocks[:truncate_at] + '...'
                else:
                    truncated = text_blocks[:remaining]
            else:
                truncated = text_blocks
            
            context_parts.insert(0, truncated)
            total_chars += len(truncated)
            if context_parts:
                total_chars += 2  # Add separator length
    
    return '\n\n'.join(context_parts)


# ============================================================================
# Core Explanation Generation
# ============================================================================

async def generate_permission_explanation(
    tool_name: str,
    tool_input: Any,
    tool_description: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    llm_client: Any = None,
) -> PermissionExplanation | None:
    """
    Generate a permission explanation using LLM with structured output.
    
    Returns null if the feature is disabled, request is aborted, or an error occurs.
    
    Args:
        tool_name: Name of the tool (e.g., "Bash", "FileWrite")
        tool_input: Tool input data (string or dict)
        tool_description: Optional tool description
        messages: Optional conversation history for context
        llm_client: LLM client instance (must support tool use)
        
    Returns:
        PermissionExplanation object or None if failed
        
    Example:
        >>> explanation = await generate_permission_explanation(
        ...     tool_name="Bash",
        ...     tool_input="rm -rf build/",
        ...     messages=conversation_history,
        ...     llm_client=client
        ... )
        >>> if explanation:
        ...     print(f"Risk: {explanation.risk_level}")
        ...     print(f"Explanation: {explanation.explanation}")
    """
    import time
    
    # Check if feature is enabled
    if not is_permission_explainer_enabled():
        logger.debug('[permission-explainer] Feature disabled')
        return None
    
    start_time = time.time()
    
    try:
        # Format input and extract context
        formatted_input = format_tool_input(tool_input)
        conversation_context = ''
        if messages:
            max_chars = PERMISSION_EXPLAINER_CONFIG.get('max_context_chars', 1000)
            conversation_context = extract_conversation_context(messages, max_chars)
        
        # Build user prompt
        user_prompt = f"Tool: {tool_name}\n"
        if tool_description:
            user_prompt += f"Description: {tool_description}\n"
        user_prompt += f"\nInput:\n{formatted_input}"
        
        if conversation_context:
            user_prompt += f"\n\nRecent conversation context:\n{conversation_context}"
        
        user_prompt += "\n\nExplain this command in context."
        
        logger.debug(f'[permission-explainer] Generating explanation for {tool_name}')
        
        # Call LLM with tool use
        if llm_client is None:
            logger.warning('[permission-explainer] No LLM client provided')
            return None
        
        # Use LLM client with forced tool choice
        response = await _call_llm_with_tool(
            llm_client=llm_client,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            tool=EXPLAIN_COMMAND_TOOL,
        )
        
        latency_ms = int((time.time() - start_time) * 1000)
        logger.debug(f'[permission-explainer] API returned in {latency_ms}ms')
        
        # Parse structured response
        if response and 'tool_use' in response:
            tool_input_data = response['tool_use'].get('input', {})
            
            try:
                # Validate with Pydantic
                parsed = ExplainCommandToolInput(**tool_input_data)
                
                explanation = PermissionExplanation(
                    risk_level=parsed.risk_level,
                    explanation=parsed.explanation,
                    reasoning=parsed.reasoning,
                    risk=parsed.risk,
                )
                
                logger.info(
                    f'[permission-explainer] {explanation.risk_level} risk for {tool_name} ({latency_ms}ms)'
                )
                
                return explanation
                
            except Exception as parse_error:
                logger.error(f'[permission-explainer] Failed to parse response: {parse_error}')
                return None
        
        # No valid tool use in response
        logger.warning('[permission-explainer] No tool use in response')
        return None
        
    except Exception as error:
        latency_ms = int((time.time() - start_time) * 1000)
        
        # Handle abort/cancellation
        if isinstance(error, asyncio.CancelledError):
            logger.debug(f'[permission-explainer] Request cancelled for {tool_name}')
            return None
        
        logger.error(f'[permission-explainer] Error: {error}')
        return None


async def _call_llm_with_tool(
    llm_client: Any,
    system_prompt: str,
    user_prompt: str,
    tool: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Call LLM with forced tool choice for structured output.
    
    This is a generic wrapper that works with multiple LLM providers.
    The llm_client must implement the appropriate API.
    
    Args:
        llm_client: LLM client instance
        system_prompt: System prompt
        user_prompt: User message
        tool: Tool definition dictionary
        
    Returns:
        Response dictionary with tool_use data
    """
    try:
        # This is a generic implementation - adapt to your LLM client
        # Example for OpenAI-compatible API:
        if hasattr(llm_client, 'chat'):
            response = await llm_client.chat.completions.create(
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt},
                ],
                tools=[{
                    'type': 'function',
                    'function': {
                        'name': tool['name'],
                        'description': tool['description'],
                        'parameters': tool['input_schema'],
                    }
                }],
                tool_choice={'type': 'function', 'function': {'name': tool['name']}},
            )
            
            # Extract tool use from response
            if response.choices and response.choices[0].message.tool_calls:
                tool_call = response.choices[0].message.tool_calls[0]
                import json
                return {
                    'tool_use': {
                        'name': tool_call.function.name,
                        'input': json.loads(tool_call.function.arguments),
                    }
                }
        
        # Example for Anthropic API:
        elif hasattr(llm_client, 'messages'):
            response = await llm_client.messages.create(
                model='claude-3-haiku-20240307',
                system=system_prompt,
                messages=[{'role': 'user', 'content': user_prompt}],
                tools=[tool],
                tool_choice={'type': 'tool', 'name': tool['name']},
            )
            
            # Extract tool use from response
            for content_block in response.content:
                if content_block.type == 'tool_use':
                    return {
                        'tool_use': {
                            'name': content_block.name,
                            'input': content_block.input,
                        }
                    }
        
        logger.warning('[permission-explainer] Unknown LLM client type')
        return None
        
    except Exception as error:
        logger.error(f'[permission-explainer] LLM call failed: {error}')
        return None


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
    # Type definitions
    'RiskLevel',
    'PermissionExplanation',
    'ExplainCommandToolInput',
    
    # Constants
    'RISK_LEVEL_NUMERIC',
    'ERROR_TYPE_PARSE',
    'ERROR_TYPE_NETWORK',
    'ERROR_TYPE_UNKNOWN',
    'SYSTEM_PROMPT',
    'EXPLAIN_COMMAND_TOOL',
    
    # Configuration
    'PERMISSION_EXPLAINER_CONFIG',
    'is_permission_explainer_enabled',
    'update_permission_explainer_config',
    
    # Utility functions
    'format_tool_input',
    'extract_conversation_context',
    
    # Core function
    'generate_permission_explanation',
]
