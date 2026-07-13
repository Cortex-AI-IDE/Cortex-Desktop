"""
Permission prompt tool result schema and normalization for Cortex AI IDE.

Defines schemas and processing logic for permission prompt responses,
validating and normalizing allow/deny decisions from user interactions.

Multi-LLM Support: Works with all providers as it's provider-agnostic
permission response handling.

Permission Decision Types:
- allow: User approved the operation (possibly with modifications)
- deny: User rejected the operation (possibly with interrupt)

Decision Classifications:
- user_temporary: Allow once for this session
- user_permanent: Allow permanently (create rule)
- user_reject: Deny operation

Example:
    >>> from PermissionPromptToolResultSchema import PermissionAllowResult
    >>> result = PermissionAllowResult(
    ...     behavior='allow',
    ...     updated_input={'command': 'rm -rf build/ --verbose'},
    ...     decision_classification='user_permanent'
    ... )
    >>> decision = normalize_permission_result(result, original_input)
"""

import logging
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


# ============================================================================
# Type Definitions
# ============================================================================

# Decision classification types
DecisionClassification = Literal[
    'user_temporary',   # Allow once for this session
    'user_permanent',   # Allow permanently (create rule)
    'user_reject',      # Deny operation
]

# Behavior types
BehaviorType = Literal['allow', 'deny']


# ============================================================================
# Input Schema
# ============================================================================

class PermissionPromptInput(BaseModel):
    """Input schema for permission prompt tool."""
    tool_name: str = Field(
        description='The name of the tool requesting permission'
    )
    input: dict[str, Any] = Field(
        description='The input for the tool'
    )
    tool_use_id: str | None = Field(
        default=None,
        description='The unique tool use request ID'
    )


# ============================================================================
# Permission Update Schema (simplified)
# ============================================================================

class PermissionUpdate(BaseModel):
    """Simplified permission update rule."""
    type: str = Field(description='allow or deny')
    pattern: str = Field(description='Pattern to match')
    scope: str = Field(
        default='session',
        description='session or permanent'
    )


# ============================================================================
# Output Schemas
# ============================================================================

class PermissionAllowResult(BaseModel):
    """Schema for permission allow result."""
    behavior: Literal['allow'] = Field(description='Allow behavior')
    updated_input: dict[str, Any] = Field(
        description='Modified tool input (empty dict means use original)'
    )
    updated_permissions: list[PermissionUpdate] | None = Field(
        default=None,
        description='Permission rule updates from user'
    )
    tool_use_id: str | None = Field(
        default=None,
        description='Original tool use ID'
    )
    decision_classification: DecisionClassification | None = Field(
        default=None,
        description='How to classify this decision'
    )
    
    @model_validator(mode='after')
    def validate_updated_permissions(self) -> 'PermissionAllowResult':
        """Log warning if updated_permissions is malformed (tolerant validation)."""
        # Pydantic already validates the structure, but we log for debugging
        if self.updated_permissions is not None:
            logger.debug(
                f'[permission-result] Allow with {len(self.updated_permissions)} permission updates'
            )
        return self


class PermissionDenyResult(BaseModel):
    """Schema for permission deny result."""
    behavior: Literal['deny'] = Field(description='Deny behavior')
    message: str = Field(description='Reason for denial')
    interrupt: bool = Field(
        default=False,
        description='Whether to abort execution'
    )
    tool_use_id: str | None = Field(
        default=None,
        description='Original tool use ID'
    )
    decision_classification: DecisionClassification | None = Field(
        default=None,
        description='How to classify this decision'
    )


# Union type for all permission results
PermissionResult = Union[PermissionAllowResult, PermissionDenyResult]


# ============================================================================
# Permission Decision
# ============================================================================

class PermissionDecisionReason(BaseModel):
    """Reason metadata for permission decision."""
    type: str = 'permissionPromptTool'
    permission_prompt_tool_name: str
    tool_result: PermissionResult


class PermissionDecision(BaseModel):
    """Normalized permission decision."""
    behavior: BehaviorType
    decision_reason: PermissionDecisionReason
    
    # For allow results
    updated_input: dict[str, Any] | None = None
    
    # For deny results
    message: str | None = None
    interrupt: bool = False
    
    # Common fields
    tool_use_id: str | None = None
    decision_classification: DecisionClassification | None = None


# ============================================================================
# Result Normalization
# ============================================================================

def normalize_permission_result(
    result: PermissionResult,
    original_input: dict[str, Any],
    tool_name: str = 'Unknown',
) -> PermissionDecision:
    """
    Normalizes the result of a permission prompt tool to a PermissionDecision.
    
    Handles:
    - Allow with updated input (falls back to original if empty)
    - Allow with permission updates (applies rules)
    - Deny with optional interrupt
    - Decision classification
    
    Args:
        result: Permission prompt result (allow or deny)
        original_input: Original tool input (fallback for empty updated_input)
        tool_name: Name of the tool requesting permission
        
    Returns:
        Normalized PermissionDecision object
        
    Example:
        >>> result = PermissionAllowResult(
        ...     behavior='allow',
        ...     updated_input={},  # Empty means use original
        ... )
        >>> decision = normalize_permission_result(result, {'command': 'ls'})
        >>> decision.updated_input
        {'command': 'ls'}
    """
    # Create decision reason
    decision_reason = PermissionDecisionReason(
        permission_prompt_tool_name=tool_name,
        tool_result=result,
    )
    
    if result.behavior == 'allow':
        # Mobile clients responding from push notifications don't have the
        # original tool input, so they send `{}` to satisfy the schema.
        # Treat an empty object as "use original" so the tool doesn't run
        # with no args.
        updated_input = (
            result.updated_input
            if len(result.updated_input) > 0
            else original_input
        )
        
        # Apply permission updates if provided
        if result.updated_permissions:
            _apply_permission_updates(result.updated_permissions)
        
        return PermissionDecision(
            behavior='allow',
            decision_reason=decision_reason,
            updated_input=updated_input,
            tool_use_id=result.tool_use_id,
            decision_classification=result.decision_classification,
        )
    
    elif result.behavior == 'deny':
        # Handle interrupt (abort execution)
        if result.interrupt:
            logger.warning(
                f'[permission-result] Deny+interrupt: tool={tool_name} message={result.message}'
            )
            # In Cortex IDE, this would emit a signal to abort
            # emit_abort_signal(result.message)
        
        return PermissionDecision(
            behavior='deny',
            decision_reason=decision_reason,
            message=result.message,
            interrupt=result.interrupt,
            tool_use_id=result.tool_use_id,
            decision_classification=result.decision_classification,
        )
    
    else:
        # Should never happen due to Pydantic validation
        raise ValueError(f"Invalid behavior: {result.behavior}")


def _apply_permission_updates(updates: list[PermissionUpdate]) -> None:
    """
    Apply permission updates from user decision.
    
    This would integrate with Cortex IDE's permission rule system to:
    - Add temporary allow rules (session-scoped)
    - Add permanent allow rules (persisted to config)
    - Add deny rules
    
    Args:
        updates: List of permission updates to apply
    """
    for update in updates:
        logger.info(
            f'[permission-result] Applying update: type={update.type} '
            f'pattern={update.pattern} scope={update.scope}'
        )
        
        # In Cortex IDE, this would:
        # 1. Add to session permission rules (temporary)
        # 2. Persist to config file (permanent)
        # 3. Update UI to show new rules
        
        # Example implementation:
        # if update.scope == 'permanent':
        #     config.add_permission_rule(update)
        #     config.save()
        # else:
        #     session_rules.add(update)


# ============================================================================
# Validation Helpers
# ============================================================================

def validate_permission_result(data: dict[str, Any]) -> PermissionResult:
    """
    Validate and parse permission result from raw data.
    
    Handles malformed data gracefully (tolerant validation).
    
    Args:
        data: Raw dictionary from permission prompt
        
    Returns:
        Validated PermissionResult object
        
    Example:
        >>> data = {
        ...     'behavior': 'allow',
        ...     'updated_input': {'command': 'ls -la'},
        ... }
        >>> result = validate_permission_result(data)
        >>> isinstance(result, PermissionAllowResult)
        True
    """
    try:
        if data.get('behavior') == 'allow':
            return PermissionAllowResult(**data)
        elif data.get('behavior') == 'deny':
            return PermissionDenyResult(**data)
        else:
            raise ValueError(f"Invalid behavior: {data.get('behavior')}")
    except Exception as e:
        logger.error(f'[permission-result] Validation failed: {e}')
        # Return a safe deny result as fallback
        return PermissionDenyResult(
            behavior='deny',
            message=f'Validation error: {str(e)}',
            interrupt=False,
        )


def is_allow_result(result: PermissionResult) -> bool:
    """Check if result is an allow decision."""
    return result.behavior == 'allow'


def is_deny_result(result: PermissionResult) -> bool:
    """Check if result is a deny decision."""
    return result.behavior == 'deny'


def should_interrupt(result: PermissionResult) -> bool:
    """Check if execution should be interrupted."""
    if isinstance(result, PermissionDenyResult):
        return result.interrupt
    return False


def get_rule_behavior_description(behavior: BehaviorType) -> str:
    """
    Get user-friendly description for rule behavior.
    
    Useful for UI messages, logs, and user notifications.
    
    Args:
        behavior: Permission behavior ('allow' or 'deny')
        
    Returns:
        Human-readable description string
        
    Example:
        >>> get_rule_behavior_description('allow')
        'allowed'
        >>> get_rule_behavior_description('deny')
        'denied'
    """
    match behavior:
        case 'allow':
            return 'allowed'
        case 'deny':
            return 'denied'
        case _:
            return 'asked for confirmation for'


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
    # Type definitions
    'DecisionClassification',
    'BehaviorType',
    'PermissionResult',
    
    # Input/Output schemas
    'PermissionPromptInput',
    'PermissionUpdate',
    'PermissionAllowResult',
    'PermissionDenyResult',
    
    # Decision types
    'PermissionDecisionReason',
    'PermissionDecision',
    
    # Core functions
    'normalize_permission_result',
    'validate_permission_result',
    
    # Helper functions
    'is_allow_result',
    'is_deny_result',
    'should_interrupt',
    'get_rule_behavior_description',
]
