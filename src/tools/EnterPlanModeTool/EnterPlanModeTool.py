"""
EnterPlanModeTool - Switch to plan mode for complex task design.

Allows the AI agent to request permission to enter plan mode, where it can
explore the codebase and design an implementation approach before coding.
"""

import os
from typing import Any, Dict, TypedDict

# Defensive imports
try:
    from ...bootstrap.state import getAllowedChannels, handlePlanModeTransition
except ImportError:
    def getAllowedChannels():
        return []
    
    def handlePlanModeTransition(from_mode, to_mode):
        pass

try:
    from ...Tool import buildTool, ToolDef
except ImportError:
    def buildTool(**kwargs):
        return kwargs
    
    class ToolDef:
        pass

try:
    from ...utils.permissions.PermissionUpdate import applyPermissionUpdate
except ImportError:
    def applyPermissionUpdate(context, update):
        return context

try:
    from ...utils.permissions.permissionSetup import prepareContextForPlanMode
except ImportError:
    def prepareContextForPlanMode(context):
        return context

try:
    from ...utils.planModeV2 import isPlanModeInterviewPhaseEnabled
except ImportError:
    def isPlanModeInterviewPhaseEnabled():
        return False

try:
    from .constants import ENTER_PLAN_MODE_TOOL_NAME
except ImportError:
    ENTER_PLAN_MODE_TOOL_NAME = 'EnterPlanMode'

try:
    from .prompt import getEnterPlanModeToolPrompt
except ImportError:
    def getEnterPlanModeToolPrompt():
        return 'Requests permission to enter plan mode for complex tasks requiring exploration and design'

try:
    from .UI import renderToolResultMessage, renderToolUseMessage, renderToolUseRejectedMessage
except ImportError:
    def renderToolUseMessage(*args, **kwargs):
        return None
    
    def renderToolResultMessage(*args, **kwargs):
        return ''
    
    def renderToolUseRejectedMessage(*args, **kwargs):
        return ''


class Input(TypedDict, total=False):
    """Input schema for EnterPlanModeTool (no parameters)."""
    pass


class Output(TypedDict):
    """Output schema for EnterPlanModeTool."""
    message: str


def isEnabled() -> bool:
    """Check if EnterPlanMode tool is enabled."""
    # When --channels is active, ExitPlanMode is disabled (its approval
    # dialog needs the terminal). Disable entry too so plan mode isn't a
    # trap the model can enter but never leave.
    kairos_enabled = os.environ.get('KAIROS', '').lower() in ('true', '1', 'yes')
    kairos_channels_enabled = os.environ.get('KAIROS_CHANNELS', '').lower() in ('true', '1', 'yes')
    
    if (kairos_enabled or kairos_channels_enabled) and len(getAllowedChannels()) > 0:
        return False
    
    return True


async def call(input_data: Input, context) -> Dict[str, Any]:
    """Execute EnterPlanModeTool - switch to plan mode."""
    if getattr(context, 'agentId', None):
        raise Exception('EnterPlanMode tool cannot be used in agent contexts')
    
    app_state = context.getAppState()
    handlePlanModeTransition(app_state['toolPermissionContext']['mode'], 'plan')
    
    # Update the permission mode to 'plan'. prepareContextForPlanMode runs
    # the classifier activation side effects when the user's defaultMode is
    # 'auto' — see permissionSetup.py for the full lifecycle.
    context.setAppState(lambda prev: {
        **prev,
        'toolPermissionContext': applyPermissionUpdate(
            prepareContextForPlanMode(prev['toolPermissionContext']),
            {'type': 'setMode', 'mode': 'plan', 'destination': 'session'},
        ),
    })
    
    return {
        'data': {
            'message': 'Entered plan mode. You should now focus on exploring the codebase and designing an implementation approach.',
        },
    }


def mapToolResultToToolResultBlockParam(content: Output, toolUseID: str) -> Dict[str, Any]:
    """Map tool output to Anthropic API tool result block."""
    message = content['message']
    
    if isPlanModeInterviewPhaseEnabled():
        instructions = f'''{message}

DO NOT write or edit any files except the plan file. Detailed workflow instructions will follow.'''
    else:
        instructions = f'''{message}

In plan mode, you should:
1. Thoroughly explore the codebase to understand existing patterns
2. Identify similar features and architectural approaches
3. Consider multiple approaches and their trade-offs
4. Use AskUserQuestion if you need to clarify the approach
5. Design a concrete implementation strategy
6. When ready, use ExitPlanMode to present your plan for approval

Remember: DO NOT write or edit any files yet. This is a read-only exploration and planning phase.'''
    
    return {
        'type': 'tool_result',
        'content': instructions,
        'tool_use_id': toolUseID,
    }


# Build the tool definition
EnterPlanModeTool = buildTool(
    name=ENTER_PLAN_MODE_TOOL_NAME,
    searchHint='switch to plan mode to design an approach before coding',
    maxResultSizeChars=100_000,
    description=lambda: 'Requests permission to enter plan mode for complex tasks requiring exploration and design',
    prompt=getEnterPlanModeToolPrompt,
    userFacingName=lambda: '',
    shouldDefer=True,
    isEnabled=isEnabled,
    isConcurrencySafe=lambda: True,
    isReadOnly=lambda: True,
    renderToolUseMessage=renderToolUseMessage,
    renderToolResultMessage=renderToolResultMessage,
    renderToolUseRejectedMessage=renderToolUseRejectedMessage,
    call=call,
    mapToolResultToToolResultBlockParam=mapToolResultToToolResultBlockParam,
)
