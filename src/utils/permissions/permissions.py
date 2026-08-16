"""
Permissions - TypeScript to Python conversion (COMPLETE - All 10 Phases).
TypeScript source: utils/permissions/permissions.ts (1486 lines)


"""

import os
import time
from typing import Optional, Dict, Any, List, Tuple, Set, Callable
from copy import deepcopy

# ============================================================
# PHASE 1: Core types and imports
# ============================================================

try:
    from .PermissionResult import (
        PermissionAskDecision,
        PermissionDecision,
        PermissionDecisionReason,
        PermissionDenyDecision,
        PermissionResult,
    )
except ImportError:
    class PermissionResult:
        def __init__(self, behavior: str, message: str = '', **kwargs):
            self.behavior = behavior
            self.message = message
            self.__dict__.update(kwargs)
    
    class PermissionDecision(PermissionResult):
        pass
    
    class PermissionAskDecision(PermissionDecision):
        pass
    
    class PermissionDenyDecision(PermissionDecision):
        pass
    
    class PermissionDecisionReason:
        def __init__(self, type: str, **kwargs):
            self.type = type
            self.__dict__.update(kwargs)

try:
    from .PermissionRule import PermissionBehavior, PermissionRule, PermissionRuleSource, PermissionRuleValue
except ImportError:
    PermissionBehavior = str  # 'allow' | 'deny' | 'ask'
    PermissionRuleSource = str
    class PermissionRuleValue:
        def __init__(self, toolName: str, ruleContent: Optional[str] = None):
            self.toolName = toolName
            self.ruleContent = ruleContent
    
    class PermissionRule:
        def __init__(self, source: str, ruleBehavior: str, ruleValue: PermissionRuleValue):
            self.source = source
            self.ruleBehavior = ruleBehavior
            self.ruleValue = ruleValue

try:
    from .PermissionUpdate import applyPermissionUpdate, applyPermissionUpdates, persistPermissionUpdates
except ImportError:
    def applyPermissionUpdate(context, update):
        return context
    
    def applyPermissionUpdates(context, updates):
        return context
    
    def persistPermissionUpdates(updates):
        pass

try:
    from .PermissionUpdateSchema import PermissionUpdate, PermissionUpdateDestination
except ImportError:
    class PermissionUpdate:
        def __init__(self, type: str, rules: List, behavior: str, destination: str):
            self.type = type
            self.rules = rules
            self.behavior = behavior
            self.destination = destination
    
    PermissionUpdateDestination = str

try:
    from .permissionRuleParser import permissionRuleValueFromString, permissionRuleValueToString
except ImportError:
    def permissionRuleValueFromString(s: str) -> PermissionRuleValue:
        parts = s.split('(', 1)
        toolName = parts[0]
        ruleContent = parts[1].rstrip(')') if len(parts) > 1 else None
        return PermissionRuleValue(toolName, ruleContent)
    
    def permissionRuleValueToString(ruleValue: PermissionRuleValue) -> str:
        if ruleValue.ruleContent:
            return f'{ruleValue.toolName}({ruleValue.ruleContent})'
        return ruleValue.toolName

try:
    from .permissionsLoader import (
        deletePermissionRuleFromSettings,
        PermissionRuleFromEditableSettings,
        shouldAllowManagedPermissionRulesOnly,
    )
except ImportError:
    class PermissionRuleFromEditableSettings:
        pass
    
    def deletePermissionRuleFromSettings(rule):
        pass
    
    def shouldAllowManagedPermissionRulesOnly():
        return False

try:
    from .denialTracking import (
        createDenialTrackingState,
        DENIAL_LIMITS,
        DenialTrackingState,
        recordDenial,
        recordSuccess,
        shouldFallbackToPrompting,
    )
except ImportError:
    class DenialTrackingState:
        def __init__(self, consecutiveDenials: int = 0, totalDenials: int = 0):
            self.consecutiveDenials = consecutiveDenials
            self.totalDenials = totalDenials
    
    DENIAL_LIMITS = {'maxTotal': 10, 'maxConsecutive': 5}
    
    def createDenialTrackingState():
        return DenialTrackingState()
    
    def recordDenial(state):
        return DenialTrackingState(
            state.consecutiveDenials + 1,
            state.totalDenials + 1
        )
    
    def recordSuccess(state):
        return DenialTrackingState(0, state.totalDenials)
    
    def shouldFallbackToPrompting(state):
        return (state.consecutiveDenials >= DENIAL_LIMITS['maxConsecutive'] or
                state.totalDenials >= DENIAL_LIMITS['maxTotal'])

try:
    from .yoloClassifier import classifyYoloAction, formatActionForClassifier
except ImportError:
    def classifyYoloAction(messages, action, tools, context, signal):
        return {'shouldBlock': False, 'reason': 'Classifier unavailable', 'unavailable': True}
    
    def formatActionForClassifier(toolName, input):
        return f'{toolName}: {input}'

try:
    from .PermissionMode import permissionModeTitle
except ImportError:
    def permissionModeTitle(mode):
        return mode

try:
    from .classifierDecision import isAutoModeAllowlistedTool
except ImportError:
    def isAutoModeAllowlistedTool(toolName):
        return False

try:
    from ..hooks import executePermissionRequestHooks
except ImportError:
    async def executePermissionRequestHooks(*args, **kwargs):
        return
        yield

try:
    from ..settings.constants import getSettingSourceDisplayNameLowercase, SETTING_SOURCES
except ImportError:
    SETTING_SOURCES = ['userSettings', 'projectSettings', 'localSettings', 'policySettings', 'flagSettings']
    
    def getSettingSourceDisplayNameLowercase(source):
        return source

try:
    from ..messages import (
        AUTO_REJECT_MESSAGE,
        buildClassifierUnavailableMessage,
        buildYoloRejectionMessage,
        DONT_ASK_REJECT_MESSAGE,
    )
except ImportError:
    def AUTO_REJECT_MESSAGE(toolName):
        return f'{toolName} auto-rejected in headless mode'
    
    def buildClassifierUnavailableMessage(toolName, model):
        return f'Classifier unavailable for {toolName}'
    
    def buildYoloRejectionMessage(reason):
        return f'Action blocked: {reason}'
    
    def DONT_ASK_REJECT_MESSAGE(toolName):
        return f'{toolName} denied in dontAsk mode'

try:
    from ..utils.stringUtils import plural
except ImportError:
    def plural(n, singular, plural=None):
        return singular if n == 1 else (plural or singular + 's')

CLASSIFIER_FAIL_CLOSED_REFRESH_MS = 30 * 60 * 1000  # 30 minutes


# ============================================================
# PHASE 2: Permission rule sources and display
# ============================================================

PERMISSION_RULE_SOURCES = [
    *SETTING_SOURCES,
    'cliArg',
    'command',
    'session',
]


def permissionRuleSourceDisplayString(source: PermissionRuleSource) -> str:
    """Get display string for permission rule source"""
    return getSettingSourceDisplayNameLowercase(source)


# ============================================================
# PHASE 3: Permission rule extraction (allow/deny/ask)
# ============================================================

def getAllowRules(context) -> List[PermissionRule]:
    """Get all allow rules from context"""
    rules = []
    for source in PERMISSION_RULE_SOURCES:
        source_rules = context.alwaysAllowRules.get(source, [])
        for ruleString in source_rules:
            rules.append(PermissionRule(
                source=source,
                ruleBehavior='allow',
                ruleValue=permissionRuleValueFromString(ruleString)
            ))
    return rules


def getDenyRules(context) -> List[PermissionRule]:
    """Get all deny rules from context"""
    rules = []
    for source in PERMISSION_RULE_SOURCES:
        source_rules = context.alwaysDenyRules.get(source, [])
        for ruleString in source_rules:
            rules.append(PermissionRule(
                source=source,
                ruleBehavior='deny',
                ruleValue=permissionRuleValueFromString(ruleString)
            ))
    return rules


def getAskRules(context) -> List[PermissionRule]:
    """Get all ask rules from context"""
    rules = []
    for source in PERMISSION_RULE_SOURCES:
        source_rules = context.alwaysAskRules.get(source, [])
        for ruleString in source_rules:
            rules.append(PermissionRule(
                source=source,
                ruleBehavior='ask',
                ruleValue=permissionRuleValueFromString(ruleString)
            ))
    return rules


# ============================================================
# PHASE 4: Tool and agent rule matching
# ============================================================

def toolMatchesRule(tool, rule: PermissionRule) -> bool:
    """
    Check if the entire tool matches a rule.
    For example, this matches "Bash" but not "Bash(prefix:*)" for BashTool
    """
    # Rule must not have content to match the entire tool
    if rule.ruleValue.ruleContent is not None:
        return False
    
    # Get tool name for permission check
    try:
        from ..services.mcp.mcpStringUtils import getToolNameForPermissionCheck, mcpInfoFromString
    except ImportError:
        def getToolNameForPermissionCheck(t):
            return t.name
        
        def mcpInfoFromString(name):
            if name.startswith('mcp__'):
                parts = name.split('__')
                if len(parts) >= 3:
                    return {'serverName': parts[1], 'toolName': parts[2]}
                elif len(parts) == 2:
                    return {'serverName': parts[1], 'toolName': None}
            return None
    
    nameForRuleMatch = getToolNameForPermissionCheck(tool)
    
    # Direct tool name match
    if rule.ruleValue.toolName == nameForRuleMatch:
        return True
    
    # MCP server-level permission
    ruleInfo = mcpInfoFromString(rule.ruleValue.toolName)
    toolInfo = mcpInfoFromString(nameForRuleMatch)
    
    return (
        ruleInfo is not None and
        toolInfo is not None and
        (ruleInfo.get('toolName') is None or ruleInfo.get('toolName') == '*') and
        ruleInfo.get('serverName') == toolInfo.get('serverName')
    )


def toolAlwaysAllowedRule(context, tool) -> Optional[PermissionRule]:
    """Check if the entire tool is listed in the always allow rules"""
    for rule in getAllowRules(context):
        if toolMatchesRule(tool, rule):
            return rule
    return None


def getDenyRuleForTool(context, tool) -> Optional[PermissionRule]:
    """Check if the tool is listed in the always deny rules"""
    for rule in getDenyRules(context):
        if toolMatchesRule(tool, rule):
            return rule
    return None


def getAskRuleForTool(context, tool) -> Optional[PermissionRule]:
    """Check if the tool is listed in the always ask rules"""
    for rule in getAskRules(context):
        if toolMatchesRule(tool, rule):
            return rule
    return None


def getDenyRuleForAgent(context, agentToolName: str, agentType: str) -> Optional[PermissionRule]:
    """Check if a specific agent is denied via Agent(agentType) syntax"""
    for rule in getDenyRules(context):
        if (rule.ruleValue.toolName == agentToolName and
            rule.ruleValue.ruleContent == agentType):
            return rule
    return None


def filterDeniedAgents(agents: List, context, agentToolName: str) -> List:
    """Filter agents to exclude those that are denied via Agent(agentType) syntax"""
    # Parse deny rules once and collect Agent(x) contents into a Set
    deniedAgentTypes = set()
    for rule in getDenyRules(context):
        if (rule.ruleValue.toolName == agentToolName and
            rule.ruleValue.ruleContent is not None):
            deniedAgentTypes.add(rule.ruleValue.ruleContent)
    
    return [agent for agent in agents if agent.agentType not in deniedAgentTypes]


def getRuleByContentsForTool(context, tool, behavior: PermissionBehavior) -> Dict[str, PermissionRule]:
    """Map of rule contents to the associated rule for a given tool"""
    try:
        from ..services.mcp.mcpStringUtils import getToolNameForPermissionCheck
    except ImportError:
        def getToolNameForPermissionCheck(t):
            return t.name
    
    return getRuleByContentsForToolName(
        context,
        getToolNameForPermissionCheck(tool),
        behavior,
    )


def getRuleByContentsForToolName(
    context,
    toolName: str,
    behavior: PermissionBehavior,
) -> Dict[str, PermissionRule]:
    """Get rules by contents for a tool name"""
    ruleByContents = {}
    
    if behavior == 'allow':
        rules = getAllowRules(context)
    elif behavior == 'deny':
        rules = getDenyRules(context)
    elif behavior == 'ask':
        rules = getAskRules(context)
    else:
        rules = []
    
    for rule in rules:
        if (rule.ruleValue.toolName == toolName and
            rule.ruleValue.ruleContent is not None and
            rule.ruleBehavior == behavior):
            ruleByContents[rule.ruleValue.ruleContent] = rule
    
    return ruleByContents


# ============================================================
# PHASE 5: Permission request message creation
# ============================================================

def createPermissionRequestMessage(
    toolName: str,
    decisionReason: Optional[PermissionDecisionReason] = None,
) -> str:
    """Creates a permission request message that explains the permission request"""
    if decisionReason:
        try:
            from bun.bundle import feature
        except ImportError:
            def feature(name):
                return False
        
        if (feature('BASH_CLASSIFIER') or feature('TRANSCRIPT_CLASSIFIER')):
            if getattr(decisionReason, 'type', None) == 'classifier':
                return (f"Classifier '{decisionReason.classifier}' requires approval "
                       f"for this {toolName} command: {decisionReason.reason}")
        
        reason_type = getattr(decisionReason, 'type', None)
        
        if reason_type == 'hook':
            hookMessage = (
                f"Hook '{decisionReason.hookName}' blocked this action: {decisionReason.reason}"
                if decisionReason.reason
                else f"Hook '{decisionReason.hookName}' requires approval for this {toolName} command"
            )
            return hookMessage
        
        elif reason_type == 'rule':
            ruleString = permissionRuleValueToString(decisionReason.rule.ruleValue)
            sourceString = permissionRuleSourceDisplayString(decisionReason.rule.source)
            return (f"Permission rule '{ruleString}' from {sourceString} requires approval "
                   f"for this {toolName} command")
        
        elif reason_type == 'subcommandResults':
            try:
                from ..bash.commands import extractOutputRedirections
            except ImportError:
                def extractOutputRedirections(cmd):
                    return {'commandWithoutRedirections': cmd, 'redirections': []}
            
            needsApproval = []
            for cmd, result in decisionReason.reasons.items():
                if result.behavior in ['ask', 'passthrough']:
                    if toolName == 'Bash':
                        extracted = extractOutputRedirections(cmd)
                        displayCmd = (
                            extracted['commandWithoutRedirections']
                            if len(extracted.get('redirections', [])) > 0
                            else cmd
                        )
                        needsApproval.append(displayCmd)
                    else:
                        needsApproval.append(cmd)
            
            if needsApproval:
                n = len(needsApproval)
                return (f"This {toolName} command contains multiple operations. "
                       f"The following {plural(n, 'part')} {plural(n, 'requires', 'require')} "
                       f"approval: {', '.join(needsApproval)}")
            return f"This {toolName} command contains multiple operations that require approval"
        
        elif reason_type == 'permissionPromptTool':
            return f"Tool '{decisionReason.permissionPromptToolName}' requires approval for this {toolName} command"
        
        elif reason_type == 'sandboxOverride':
            return 'Run outside of the sandbox'
        
        elif reason_type == 'workingDir':
            return decisionReason.reason
        
        elif reason_type in ['safetyCheck', 'other', 'asyncAgent']:
            return decisionReason.reason
        
        elif reason_type == 'mode':
            modeTitle = permissionModeTitle(decisionReason.mode)
            return f"Current permission mode ({modeTitle}) requires approval for this {toolName} command"
    
    # Default message
    return f"Claude requested permissions to use {toolName}, but you haven't granted it yet."


# ============================================================
# PHASE 6: Headless agent hook execution
# ============================================================

async def runPermissionRequestHooksForHeadlessAgent(
    tool,
    input: Dict[str, Any],
    toolUseID: str,
    context,
    permissionMode: Optional[str],
    suggestions: Optional[List],
) -> Optional[PermissionDecision]:
    """
    Runs PermissionRequest hooks for headless/async agents that cannot show
    permission prompts.
    """
    try:
        async for hookResult in executePermissionRequestHooks(
            tool.name,
            toolUseID,
            input,
            context,
            permissionMode,
            suggestions,
            context.abortController.signal if hasattr(context, 'abortController') else None,
        ):
            if not hasattr(hookResult, 'permissionRequestResult') or not hookResult.permissionRequestResult:
                continue
            
            decision = hookResult.permissionRequestResult
            
            if decision.behavior == 'allow':
                finalInput = getattr(decision, 'updatedInput', None) or input
                
                # Persist permission updates if provided
                if hasattr(decision, 'updatedPermissions') and decision.updatedPermissions:
                    persistPermissionUpdates(decision.updatedPermissions)
                    if hasattr(context, 'setAppState'):
                        context.setAppState(lambda prev: {
                            **prev,
                            'toolPermissionContext': applyPermissionUpdates(
                                prev.get('toolPermissionContext', {}),
                                decision.updatedPermissions,
                            ),
                        })
                
                return PermissionDecision(
                    behavior='allow',
                    updatedInput=finalInput,
                    decisionReason=PermissionDecisionReason(
                        type='hook',
                        hookName='PermissionRequest',
                    ),
                )
            
            if decision.behavior == 'deny':
                if hasattr(decision, 'interrupt') and decision.interrupt:
                    logForDebugging(
                        f'Hook interrupt: tool={tool.name} hookMessage={getattr(decision, "message", None)}'
                    )
                    if hasattr(context, 'abortController'):
                        context.abortController.abort()
                
                return PermissionDecision(
                    behavior='deny',
                    message=getattr(decision, 'message', None) or 'Permission denied by hook',
                    decisionReason=PermissionDecisionReason(
                        type='hook',
                        hookName='PermissionRequest',
                        reason=getattr(decision, 'message', None),
                    ),
                )
    except Exception as error:
        # If hooks fail, fall through to auto-deny rather than crashing
        logError(Exception(f'PermissionRequest hook failed for headless agent: {error}'))
    
    return None


# ============================================================
# PHASE 7: Main permission checking (hasPermissionsToUseTool)
# ============================================================

async def hasPermissionsToUseTool(
    tool,
    input: Dict[str, Any],
    context,
    assistantMessage,
    toolUseID: str,
) -> PermissionDecision:
    """Main entry point for checking tool permissions"""
    result = await hasPermissionsToUseToolInner(tool, input, context)
    
    # Reset consecutive denials on any allowed tool use in auto mode
    if result.behavior == 'allow':
        appState = context.getAppState()
        try:
            from bun.bundle import feature
        except ImportError:
            def feature(name):
                return False
        
        if feature('TRANSCRIPT_CLASSIFIER'):
            currentDenialState = (
                getattr(context, 'localDenialTracking', None) or
                appState.get('denialTracking')
            )
            if (appState.get('toolPermissionContext', {}).get('mode') == 'auto' and
                currentDenialState and
                currentDenialState.consecutiveDenials > 0):
                newDenialState = recordSuccess(currentDenialState)
                persistDenialState(context, newDenialState)
        
        return result
    
    # Apply dontAsk mode transformation: convert 'ask' to 'deny'
    if result.behavior == 'ask':
        appState = context.getAppState()
        
        if appState.get('toolPermissionContext', {}).get('mode') == 'dontAsk':
            return PermissionDecision(
                behavior='deny',
                decisionReason=PermissionDecisionReason(
                    type='mode',
                    mode='dontAsk',
                ),
                message=DONT_ASK_REJECT_MESSAGE(tool.name),
            )
        
        # Apply auto mode: use AI classifier instead of prompting user
        if feature('TRANSCRIPT_CLASSIFIER'):
            mode = appState.get('toolPermissionContext', {}).get('mode')
            try:
                from .autoModeState import isAutoModeActive
                autoModeActive = isAutoModeActive()
            except ImportError:
                autoModeActive = False
            
            if mode == 'auto' or (mode == 'plan' and autoModeActive):
                return await handleAutoModeClassification(
                    tool, input, context, toolUseID, result, assistantMessage
                )
        
        # When permission prompts should be avoided (headless agents)
        if appState.get('toolPermissionContext', {}).get('shouldAvoidPermissionPrompts'):
            hookDecision = await runPermissionRequestHooksForHeadlessAgent(
                tool,
                input,
                toolUseID,
                context,
                appState.get('toolPermissionContext', {}).get('mode'),
                getattr(result, 'suggestions', None),
            )
            if hookDecision:
                return hookDecision
            
            return PermissionDecision(
                behavior='deny',
                decisionReason=PermissionDecisionReason(
                    type='asyncAgent',
                    reason='Permission prompts are not available in this context',
                ),
                message=AUTO_REJECT_MESSAGE(tool.name),
            )
    
    return result


async def hasPermissionsToUseToolInner(
    tool,
    input: Dict[str, Any],
    context,
) -> PermissionDecision:
    """Inner permission checking logic"""
    try:
        from ..errors import AbortError
    except ImportError:
        pass
    
    if hasattr(context, 'abortController') and context.abortController.signal.aborted:
        raise AbortError('Aborted')
    
    appState = context.getAppState()
    
    # 1a. Entire tool is denied
    denyRule = getDenyRuleForTool(appState.get('toolPermissionContext', {}), tool)
    if denyRule:
        return PermissionDecision(
            behavior='deny',
            decisionReason=PermissionDecisionReason(
                type='rule',
                rule=denyRule,
            ),
            message=f'Permission to use {tool.name} has been denied.',
        )
    
    # 1b. Check if the entire tool should always ask for permission
    askRule = getAskRuleForTool(appState.get('toolPermissionContext', {}), tool)
    if askRule:
        try:
            from ..tools.BashTool.shouldUseSandbox import shouldUseSandbox
            from ..utils.sandbox.sandbox_adapter import SandboxManager
        except ImportError:
            def shouldUseSandbox(inp):
                return False
            
            class SandboxManager:
                @staticmethod
                def isSandboxingEnabled():
                    return False
                
                @staticmethod
                def isAutoAllowBashIfSandboxedEnabled():
                    return False
        
        canSandboxAutoAllow = (
            tool.name == 'Bash' and
            SandboxManager.isSandboxingEnabled() and
            SandboxManager.isAutoAllowBashIfSandboxedEnabled() and
            shouldUseSandbox(input)
        )
        
        if not canSandboxAutoAllow:
            return PermissionDecision(
                behavior='ask',
                decisionReason=PermissionDecisionReason(
                    type='rule',
                    rule=askRule,
                ),
                message=createPermissionRequestMessage(tool.name),
            )
    
    # 1c. Ask the tool implementation for a permission result
    toolPermissionResult = PermissionResult(
        behavior='passthrough',
        message=createPermissionRequestMessage(tool.name),
    )
    
    try:
        if hasattr(tool, 'inputSchema') and hasattr(tool.inputSchema, 'parse'):
            parsedInput = tool.inputSchema.parse(input)
        else:
            parsedInput = input
        
        if hasattr(tool, 'checkPermissions'):
            toolPermissionResult = await tool.checkPermissions(parsedInput, context)
    except AbortError:
        raise
    except Exception as e:
        logError(e)
    
    # 1d. Tool implementation denied permission
    if getattr(toolPermissionResult, 'behavior', None) == 'deny':
        return toolPermissionResult
    
    # 1e. Tool requires user interaction even in bypass mode
    if (hasattr(tool, 'requiresUserInteraction') and
        tool.requiresUserInteraction() and
        getattr(toolPermissionResult, 'behavior', None) == 'ask'):
        return toolPermissionResult
    
    # 1f. Content-specific ask rules
    if (getattr(toolPermissionResult, 'behavior', None) == 'ask' and
        getattr(getattr(toolPermissionResult, 'decisionReason', None), 'type', None) == 'rule' and
        getattr(getattr(getattr(toolPermissionResult, 'decisionReason', None), 'rule', None), 'ruleBehavior', None) == 'ask'):
        return toolPermissionResult
    
    # 1g. Safety checks are bypass-immune
    if (getattr(toolPermissionResult, 'behavior', None) == 'ask' and
        getattr(getattr(toolPermissionResult, 'decisionReason', None), 'type', None) == 'safetyCheck'):
        return toolPermissionResult
    
    # 2a. Check if mode allows the tool to run
    appState = context.getAppState()
    toolPermissionContext = appState.get('toolPermissionContext', {})
    mode = toolPermissionContext.get('mode')
    isBypassPermissionsModeAvailable = toolPermissionContext.get('isBypassPermissionsModeAvailable', False)
    
    shouldBypassPermissions = (
        mode == 'bypassPermissions' or
        (mode == 'plan' and isBypassPermissionsModeAvailable)
    )
    
    if shouldBypassPermissions:
        return PermissionDecision(
            behavior='allow',
            updatedInput=getUpdatedInputOrFallback(toolPermissionResult, input),
            decisionReason=PermissionDecisionReason(
                type='mode',
                mode=mode,
            ),
        )
    
    # 2b. Entire tool is allowed
    alwaysAllowedRule = toolAlwaysAllowedRule(appState.get('toolPermissionContext', {}), tool)
    if alwaysAllowedRule:
        return PermissionDecision(
            behavior='allow',
            updatedInput=getUpdatedInputOrFallback(toolPermissionResult, input),
            decisionReason=PermissionDecisionReason(
                type='rule',
                rule=alwaysAllowedRule,
            ),
        )
    
    # 3. Convert "passthrough" to "ask"
    if getattr(toolPermissionResult, 'behavior', None) == 'passthrough':
        result = PermissionDecision(
            **{k: v for k, v in toolPermissionResult.__dict__.items() if k != 'behavior'},
            behavior='ask',
            message=createPermissionRequestMessage(
                tool.name,
                getattr(toolPermissionResult, 'decisionReason', None),
            ),
        )
    else:
        result = toolPermissionResult
    
    if getattr(result, 'behavior', None) == 'ask' and hasattr(result, 'suggestions') and result.suggestions:
        try:
            from ..slowOperations import jsonStringify
            logForDebugging(f'Permission suggestions for {tool.name}: {jsonStringify(result.suggestions, indent=2)}')
        except:
            pass
    
    return result


# ============================================================
# PHASE 8: Auto mode classifier integration
# ============================================================

async def handleAutoModeClassification(
    tool,
    input: Dict[str, Any],
    context,
    toolUseID: str,
    result: PermissionDecision,
    assistantMessage,
) -> PermissionDecision:
    """Handle auto mode classifier integration"""
    appState = context.getAppState()
    
    # Non-classifier-approvable safetyCheck decisions stay immune
    if (getattr(result, 'decisionReason', None) and
        result.decisionReason.type == 'safetyCheck' and
        not getattr(result.decisionReason, 'classifierApprovable', False)):
        
        if appState.get('toolPermissionContext', {}).get('shouldAvoidPermissionPrompts'):
            return PermissionDecision(
                behavior='deny',
                message=getattr(result, 'message', None),
                decisionReason=PermissionDecisionReason(
                    type='asyncAgent',
                    reason='Safety check requires interactive approval and permission prompts are not available in this context',
                ),
            )
        return result
    
    # Tool requires user interaction
    if hasattr(tool, 'requiresUserInteraction') and tool.requiresUserInteraction():
        if getattr(result, 'behavior', None) == 'ask':
            return result
    
    # Get denial tracking state
    denialState = (
        getattr(context, 'localDenialTracking', None) or
        appState.get('denialTracking') or
        createDenialTrackingState()
    )
    
    # PowerShell requires explicit user permission in auto mode
    try:
        from bun.bundle import feature
    except ImportError:
        def feature(name):
            return False
    
    if tool.name == 'PowerShell' and not feature('POWERSHELL_AUTO_MODE'):
        if appState.get('toolPermissionContext', {}).get('shouldAvoidPermissionPrompts'):
            return PermissionDecision(
                behavior='deny',
                message='PowerShell tool requires interactive approval',
                decisionReason=PermissionDecisionReason(
                    type='asyncAgent',
                    reason='PowerShell tool requires interactive approval and permission prompts are not available in this context',
                ),
            )
        logForDebugging(f'Skipping auto mode classifier for {tool.name}: tool requires explicit user permission')
        return result
    
    # Check if acceptEdits mode would allow this action
    if getattr(result, 'behavior', None) == 'ask' and tool.name not in ['Agent', 'REPL']:
        try:
            if hasattr(tool, 'inputSchema') and hasattr(tool.inputSchema, 'parse'):
                parsedInput = tool.inputSchema.parse(input)
            else:
                parsedInput = input
            
            if hasattr(tool, 'checkPermissions'):
                # Create modified context with acceptEdits mode
                original_getAppState = context.getAppState
                def modifiedGetAppState():
                    return {
                        **appState,
                        'toolPermissionContext': {
                            **appState.get('toolPermissionContext', {}),
                            'mode': 'acceptEdits',
                        },
                    }
                
                # Temporarily override getAppState
                if hasattr(context, 'getAppState'):
                    context.getAppState = modifiedGetAppState
                
                try:
                    acceptEditsResult = await tool.checkPermissions(parsedInput, context)
                finally:
                    # Restore original getAppState
                    if hasattr(context, 'getAppState'):
                        context.getAppState = original_getAppState
                
                if getattr(acceptEditsResult, 'behavior', None) == 'allow':
                    newDenialState = recordSuccess(denialState)
                    persistDenialState(context, newDenialState)
                    logForDebugging(f'Skipping auto mode classifier for {tool.name}: would be allowed in acceptEdits mode')
                    logEvent('tengu_auto_mode_decision', {
                        'decision': 'allowed',
                        'toolName': tool.name,
                        'fastPath': 'acceptEdits',
                    })
                    return PermissionDecision(
                        behavior='allow',
                        updatedInput=getattr(acceptEditsResult, 'updatedInput', None) or input,
                        decisionReason=PermissionDecisionReason(
                            type='mode',
                            mode='auto',
                        ),
                    )
        except AbortError:
            raise
        except Exception as e:
            pass  # Fall through to classifier
    
    # Allowlisted tools are safe
    if isAutoModeAllowlistedTool(tool.name):
        newDenialState = recordSuccess(denialState)
        persistDenialState(context, newDenialState)
        logForDebugging(f'Skipping auto mode classifier for {tool.name}: tool is on the safe allowlist')
        logEvent('tengu_auto_mode_decision', {
            'decision': 'allowed',
            'toolName': tool.name,
            'fastPath': 'allowlist',
        })
        return PermissionDecision(
            behavior='allow',
            updatedInput=input,
            decisionReason=PermissionDecisionReason(
                type='mode',
                mode='auto',
            ),
        )
    
    # Run the auto mode classifier
    action = formatActionForClassifier(tool.name, input)
    setClassifierChecking(toolUseID)
    
    try:
        classifierResult = await classifyYoloAction(
            appState.get('messages', []),
            action,
            context.options.tools if hasattr(context, 'options') else [],
            appState.get('toolPermissionContext', {}),
            context.abortController.signal if hasattr(context, 'abortController') else None,
        )
    finally:
        clearClassifierChecking(toolUseID)
    
    # Log classifier decision
    yoloDecision = (
        'unavailable' if classifierResult.get('unavailable')
        else 'blocked' if classifierResult.get('shouldBlock')
        else 'allowed'
    )
    
    logEvent('tengu_auto_mode_decision', {
        'decision': yoloDecision,
        'toolName': tool.name,
    })
    
    if classifierResult.get('durationMs') is not None:
        try:
            from ..bootstrap.state import addToTurnClassifierDuration
            addToTurnClassifierDuration(classifierResult['durationMs'])
        except:
            pass
    
    if classifierResult.get('shouldBlock'):
        # Transcript exceeded context window
        if classifierResult.get('transcriptTooLong'):
            if appState.get('toolPermissionContext', {}).get('shouldAvoidPermissionPrompts'):
                raise AbortError('Agent aborted: auto mode classifier transcript exceeded context window in headless mode')
            
            logForDebugging('Auto mode classifier transcript too long, falling back to normal permission handling', level='warn')
            return PermissionDecision(
                **{k: v for k, v in result.__dict__.items()},
                decisionReason=PermissionDecisionReason(
                    type='other',
                    reason='Auto mode classifier transcript exceeded context window — falling back to manual approval',
                ),
            )
        
        # Classifier unavailable
        if classifierResult.get('unavailable'):
            try:
                from ..services.analytics.growthbook import getFeatureValue_CACHED_WITH_REFRESH
                ironGateClosed = getFeatureValue_CACHED_WITH_REFRESH(
                    'tengu_iron_gate_closed',
                    True,
                    CLASSIFIER_FAIL_CLOSED_REFRESH_MS,
                )
            except:
                ironGateClosed = True
            
            if ironGateClosed:
                logForDebugging('Auto mode classifier unavailable, denying with retry guidance (fail closed)', level='warn')
                return PermissionDecision(
                    behavior='deny',
                    decisionReason=PermissionDecisionReason(
                        type='classifier',
                        classifier='auto-mode',
                        reason='Classifier unavailable',
                    ),
                    message=buildClassifierUnavailableMessage(tool.name, classifierResult.get('model')),
                )
            
            # Fail open
            logForDebugging('Auto mode classifier unavailable, falling back to normal permission handling (fail open)', level='warn')
            return result
        
        # Update denial tracking
        newDenialState = recordDenial(denialState)
        persistDenialState(context, newDenialState)
        
        logForDebugging(f'Auto mode classifier blocked action: {classifierResult.get("reason")}', level='warn')
        
        # Check denial limits
        denialLimitResult = handleDenialLimitExceeded(
            newDenialState,
            appState,
            classifierResult.get('reason', ''),
            assistantMessage,
            tool,
            result,
            context,
        )
        if denialLimitResult:
            return denialLimitResult
        
        return PermissionDecision(
            behavior='deny',
            decisionReason=PermissionDecisionReason(
                type='classifier',
                classifier='auto-mode',
                reason=classifierResult.get('reason', ''),
            ),
            message=buildYoloRejectionMessage(classifierResult.get('reason', '')),
        )
    
    # Reset consecutive denials on success
    newDenialState = recordSuccess(denialState)
    persistDenialState(context, newDenialState)
    
    return PermissionDecision(
        behavior='allow',
        updatedInput=input,
        decisionReason=PermissionDecisionReason(
            type='classifier',
            classifier='auto-mode',
            reason=classifierResult.get('reason', ''),
        ),
    )


def setClassifierChecking(toolUseID: str):
    """Set classifier checking flag"""
    try:
        from ..classifierApprovals import setClassifierChecking
        setClassifierChecking(toolUseID)
    except:
        pass


def clearClassifierChecking(toolUseID: str):
    """Clear classifier checking flag"""
    try:
        from ..classifierApprovals import clearClassifierChecking
        clearClassifierChecking(toolUseID)
    except:
        pass


# ============================================================
# PHASE 9: Denial tracking and limit handling
# ============================================================

def persistDenialState(context, newState: DenialTrackingState):
    """Persist denial tracking state"""
    if hasattr(context, 'localDenialTracking') and context.localDenialTracking:
        # Mutate local state in place for subagents
        context.localDenialTracking.consecutiveDenials = newState.consecutiveDenials
        context.localDenialTracking.totalDenials = newState.totalDenials
    else:
        # Write to appState
        if hasattr(context, 'setAppState'):
            context.setAppState(lambda prev: {
                **prev,
                'denialTracking': newState,
            })


def handleDenialLimitExceeded(
    denialState: DenialTrackingState,
    appState: Dict,
    classifierReason: str,
    assistantMessage,
    tool,
    result: PermissionDecision,
    context,
) -> Optional[PermissionDecision]:
    """Check if a denial limit was exceeded and return an 'ask' result"""
    if not shouldFallbackToPrompting(denialState):
        return None
    
    hitTotalLimit = denialState.totalDenials >= DENIAL_LIMITS.get('maxTotal', 10)
    isHeadless = appState.get('toolPermissionContext', {}).get('shouldAvoidPermissionPrompts', False)
    
    totalCount = denialState.totalDenials
    consecutiveCount = denialState.consecutiveDenials
    
    warning = (
        f'{totalCount} actions were blocked this session. Please review the transcript before continuing.'
        if hitTotalLimit
        else f'{consecutiveCount} consecutive actions were blocked. Please review the transcript before continuing.'
    )
    
    logEvent('tengu_auto_mode_denial_limit_exceeded', {
        'limit': 'total' if hitTotalLimit else 'consecutive',
        'mode': 'headless' if isHeadless else 'gui',
        'consecutiveDenials': consecutiveCount,
        'totalDenials': totalCount,
        'toolName': tool.name,
    })
    
    if isHeadless:
        raise AbortError('Agent aborted: too many classifier denials in headless mode')
    
    logForDebugging(f'Classifier denial limit exceeded, falling back to prompting: {warning}', level='warn')
    
    if hitTotalLimit:
        persistDenialState(context, DenialTrackingState(0, 0))
    
    # Preserve the original classifier value
    originalClassifier = (
        result.decisionReason.classifier
        if hasattr(result, 'decisionReason') and hasattr(result.decisionReason, 'classifier')
        else 'auto-mode'
    )
    
    return PermissionDecision(
        **{k: v for k, v in result.__dict__.items() if k != 'decisionReason'},
        decisionReason=PermissionDecisionReason(
            type='classifier',
            classifier=originalClassifier,
            reason=f'{warning}\n\nLatest blocked action: {classifierReason}',
        ),
    )


async def checkRuleBasedPermissions(
    tool,
    input: Dict[str, Any],
    context,
) -> Optional:
    """
    Check only the rule-based steps of the permission pipeline.
    Returns a deny/ask decision if a rule blocks the tool, or null if no rule objects.
    """
    try:
        from ..errors import AbortError
    except ImportError:
        pass
    
    appState = context.getAppState()
    
    # 1a. Entire tool is denied by rule
    denyRule = getDenyRuleForTool(appState.get('toolPermissionContext', {}), tool)
    if denyRule:
        return PermissionDenyDecision(
            behavior='deny',
            decisionReason=PermissionDecisionReason(
                type='rule',
                rule=denyRule,
            ),
            message=f'Permission to use {tool.name} has been denied.',
        )
    
    # 1b. Entire tool has an ask rule
    askRule = getAskRuleForTool(appState.get('toolPermissionContext', {}), tool)
    if askRule:
        try:
            from ..tools.BashTool.shouldUseSandbox import shouldUseSandbox
            from ..utils.sandbox.sandbox_adapter import SandboxManager
        except ImportError:
            def shouldUseSandbox(inp):
                return False
            
            class SandboxManager:
                @staticmethod
                def isSandboxingEnabled():
                    return False
                
                @staticmethod
                def isAutoAllowBashIfSandboxedEnabled():
                    return False
        
        canSandboxAutoAllow = (
            tool.name == 'Bash' and
            SandboxManager.isSandboxingEnabled() and
            SandboxManager.isAutoAllowBashIfSandboxedEnabled() and
            shouldUseSandbox(input)
        )
        
        if not canSandboxAutoAllow:
            return PermissionAskDecision(
                behavior='ask',
                decisionReason=PermissionDecisionReason(
                    type='rule',
                    rule=askRule,
                ),
                message=createPermissionRequestMessage(tool.name),
            )
    
    # 1c. Tool-specific permission check
    toolPermissionResult = PermissionResult(
        behavior='passthrough',
        message=createPermissionRequestMessage(tool.name),
    )
    
    try:
        if hasattr(tool, 'inputSchema') and hasattr(tool.inputSchema, 'parse'):
            parsedInput = tool.inputSchema.parse(input)
        else:
            parsedInput = input
        
        if hasattr(tool, 'checkPermissions'):
            toolPermissionResult = await tool.checkPermissions(parsedInput, context)
    except AbortError:
        raise
    except Exception as e:
        logError(e)
    
    # 1d. Tool implementation denied
    if getattr(toolPermissionResult, 'behavior', None) == 'deny':
        return toolPermissionResult
    
    # 1f. Content-specific ask rules
    if (getattr(toolPermissionResult, 'behavior', None) == 'ask' and
        getattr(getattr(toolPermissionResult, 'decisionReason', None), 'type', None) == 'rule' and
        getattr(getattr(getattr(toolPermissionResult, 'decisionReason', None), 'rule', None), 'ruleBehavior', None) == 'ask'):
        return toolPermissionResult
    
    # 1g. Safety checks
    if (getattr(toolPermissionResult, 'behavior', None) == 'ask' and
        getattr(getattr(toolPermissionResult, 'decisionReason', None), 'type', None) == 'safetyCheck'):
        return toolPermissionResult
    
    # No rule-based objection
    return None


# ============================================================
# PHASE 10: Permission rule management (add/delete/sync)
# ============================================================

async def deletePermissionRule(
    rule: PermissionRule,
    initialContext,
    setToolPermissionContext: Callable,
) -> None:
    """Delete a permission rule from the appropriate destination"""
    if rule.source in ['policySettings', 'flagSettings', 'command']:
        raise Exception('Cannot delete permission rules from read-only settings')
    
    updatedContext = applyPermissionUpdate(initialContext, {
        'type': 'removeRules',
        'rules': [rule.ruleValue],
        'behavior': rule.ruleBehavior,
        'destination': rule.source,
    })
    
    # Per-destination logic to delete the rule from settings
    destination = rule.source
    if destination in ['localSettings', 'userSettings', 'projectSettings']:
        deletePermissionRuleFromSettings(rule)
    # cliArg and session: No action needed for in-memory sources
    
    # Update React state with updated context
    setToolPermissionContext(updatedContext)


def convertRulesToUpdates(
    rules: List[PermissionRule],
    updateType: str,
) -> List[PermissionUpdate]:
    """Helper to convert PermissionRule array to PermissionUpdate array"""
    # Group rules by source and behavior
    grouped = {}
    
    for rule in rules:
        key = f'{rule.source}:{rule.ruleBehavior}'
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(rule.ruleValue)
    
    # Convert to PermissionUpdate array
    updates = []
    for key, ruleValues in grouped.items():
        source, behavior = key.split(':')
        updates.append(PermissionUpdate(
            type=updateType,
            rules=ruleValues,
            behavior=behavior,
            destination=source,
        ))
    
    return updates


def applyPermissionRulesToPermissionContext(
    toolPermissionContext,
    rules: List[PermissionRule],
):
    """Apply permission rules to context (additive - for initial setup)"""
    updates = convertRulesToUpdates(rules, 'addRules')
    return applyPermissionUpdates(toolPermissionContext, updates)


def syncPermissionRulesFromDisk(
    toolPermissionContext,
    rules: List[PermissionRule],
):
    """Sync permission rules from disk (replacement - for settings changes)"""
    context = toolPermissionContext
    
    # When allowManagedPermissionRulesOnly is enabled, clear all non-policy sources
    if shouldAllowManagedPermissionRulesOnly():
        sourcesToClear = ['userSettings', 'projectSettings', 'localSettings', 'cliArg', 'session']
        behaviors = ['allow', 'deny', 'ask']
        
        for source in sourcesToClear:
            for behavior in behaviors:
                context = applyPermissionUpdate(context, {
                    'type': 'replaceRules',
                    'rules': [],
                    'behavior': behavior,
                    'destination': source,
                })
    
    # Clear all disk-based source:behavior combos before applying new rules
    diskSources = ['userSettings', 'projectSettings', 'localSettings']
    for diskSource in diskSources:
        for behavior in ['allow', 'deny', 'ask']:
            context = applyPermissionUpdate(context, {
                'type': 'replaceRules',
                'rules': [],
                'behavior': behavior,
                'destination': diskSource,
            })
    
    updates = convertRulesToUpdates(rules, 'replaceRules')
    return applyPermissionUpdates(context, updates)


def getUpdatedInputOrFallback(
    permissionResult,
    fallback: Dict[str, Any],
) -> Dict[str, Any]:
    """Extract updatedInput from a permission result, falling back to the original input"""
    if hasattr(permissionResult, 'updatedInput'):
        return permissionResult.updatedInput or fallback
    return fallback


# ============================================================
# STUB FUNCTIONS - For missing imports
# ============================================================

def logForDebugging(msg: str, level: str = 'info', **kwargs) -> None:
    """Stub logging with level support"""
    pass


def logError(error: Exception) -> None:
    """Stub error logging"""
    pass


def logEvent(event: str, data: Dict) -> None:
    """Stub analytics"""
    pass


class AbortError(Exception):
    """Abort error for cancellation"""
    pass


# ============================================================
# SNAKE_CASE ALIASES - For Python convention compatibility
# ============================================================

get_deny_rule_for_tool = getDenyRuleForTool
get_ask_rule_for_tool = getAskRuleForTool
get_deny_rule_for_agent = getDenyRuleForAgent
filter_denied_agents = filterDeniedAgents
