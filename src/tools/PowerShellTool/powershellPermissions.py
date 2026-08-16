# ------------------------------------------------------------
# powershellPermissions.py
# Python conversion of PowerShellTool/powershellPermissions.ts
# 
# PowerShell-specific permission checking, adapted from bashPermissions.ts
# for case-insensitive cmdlet matching.
# ------------------------------------------------------------

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Import actual modules (with defensive fallbacks)
try:
    from ...utils.cwd import getCwd
except ImportError:
    def getCwd() -> str:
        import os
        return os.getcwd()

try:
    from ...utils.git import isCurrentDirectoryBareGitRepo
except ImportError:
    def isCurrentDirectoryBareGitRepo() -> bool:
        return False

try:
    from ...utils.permissions.permissions import (
        createPermissionRequestMessage,
        getRuleByContentsForToolName,
    )
except ImportError:
    def createPermissionRequestMessage(tool_name: str, reason: dict = None) -> str:
        return f"Permission to use {tool_name} requires approval"
    
    def getRuleByContentsForToolName(context: dict, tool_name: str, behavior: str) -> Dict[str, Any]:
        return context.get(f'{behavior}_rules', {})

try:
    from ...utils.permissions.shellRuleMatching import (
        matchWildcardPattern,
        parsePermissionRule,
        suggestionForExactCommand as sharedSuggestionForExactCommand,
    )
except ImportError:
    import fnmatch
    def matchWildcardPattern(pattern: str, text: str, case_insensitive: bool = False) -> bool:
        if case_insensitive:
            pattern, text = pattern.lower(), text.lower()
        return fnmatch.fnmatch(text, pattern)
    
    def parsePermissionRule(rule_str: str) -> Dict[str, Any]:
        if ':' in rule_str and not rule_str.startswith('\\') and not rule_str.startswith('/'):
            parts = rule_str.split(':', 1)
            return {'type': 'exact', 'command': parts[0], 'args': parts[1] if len(parts) > 1 else ''}
        if '*' in rule_str:
            return {'type': 'wildcard', 'pattern': rule_str}
        return {'type': 'exact', 'command': rule_str}
    
    def sharedSuggestionForExactCommand(tool_name: str, command: str) -> List[dict]:
        return []

try:
    from ...utils.powershell.parser import (
        classifyCommandName,
        deriveSecurityFlags,
        getAllCommandNames,
        getFileRedirections,
        parsePowerShellCommand,
        stripModulePrefix,
        PS_TOKENIZER_DASH_CHARS,
    )
except ImportError:
    PS_TOKENIZER_DASH_CHARS = {'-', '\u2013', '\u2014', '\u2015'}
    
    async def parsePowerShellCommand(command: str) -> Dict[str, Any]:
        return {'valid': False, 'errors': [{'message': 'Parser unavailable'}], 'statements': []}
    
    def getAllCommandNames(parsed: dict) -> List[str]:
        return []
    
    def getFileRedirections(parsed: dict) -> List[dict]:
        return []
    
    def classifyCommandName(name: str) -> str:
        if '\\' in name or '/' in name or '.' in name:
            return 'application'
        return 'cmdlet'
    
    def stripModulePrefix(name: str) -> str:
        return name.split('\\', 1)[1] if '\\' in name else name
    
    def deriveSecurityFlags(parsed: dict) -> dict:
        return {'hasScriptBlocks': False}

try:
    from ...utils.shell.readOnlyCommandValidation import containsVulnerableUncPath
except ImportError:
    def containsVulnerableUncPath(path: str) -> bool:
        return path.startswith('\\\\') or path.startswith('//')

try:
    from .gitSafety import isDotGitPathPS, isGitInternalPathPS
except ImportError:
    def isDotGitPathPS(path: str) -> bool:
        normalized = path.replace('\\', '/')
        return '.git/' in normalized or normalized.startswith('.git/')
    
    def isGitInternalPathPS(path: str) -> bool:
        normalized = path.replace('\\', '/')
        return any(p in normalized for p in ['hooks/', 'refs/', 'objects/', 'HEAD'])

try:
    from .modeValidation import checkPermissionMode, isSymlinkCreatingCommand
except ImportError:
    def checkPermissionMode(input_cmd: dict, parsed: dict, context: dict) -> dict:
        return {'behavior': 'passthrough'}
    
    def isSymlinkCreatingCommand(element: dict) -> bool:
        return False

try:
    from .pathValidation import checkPathConstraints, dangerousRemovalDeny, isDangerousRemovalRawPath
except ImportError:
    def checkPathConstraints(input_cmd: dict, parsed: dict, context: dict, has_cd: bool = False) -> dict:
        return {'behavior': 'passthrough'}
    
    def dangerousRemovalDeny(path: str) -> dict:
        return {'behavior': 'deny', 'message': f'Removal of {path} denied'}
    
    def isDangerousRemovalRawPath(path: str) -> bool:
        return path in ('/', '~', '/etc', '/usr', 'C:\\', 'C:/')

try:
    from .powershellSecurity import powershellCommandIsSafe
except ImportError:
    def powershellCommandIsSafe(command: str, parsed: dict) -> dict:
        return {'behavior': 'passthrough'}

try:
    from .readOnlyValidation import (
        argLeaksValue,
        isAllowlistedCommand,
        isCwdChangingCmdlet,
        isProvablySafeStatement,
        isReadOnlyCommand,
        isSafeOutputCommand,
        resolveToCanonical,
    )
except ImportError:
    def resolveToCanonical(name: str) -> str:
        aliases = {
            'rm': 'remove-item', 'del': 'remove-item', 'ri': 'remove-item', 'erase': 'remove-item',
            'ls': 'get-childitem', 'dir': 'get-childitem', 'gci': 'get-childitem',
            'cat': 'get-content', 'type': 'get-content', 'gc': 'get-content',
            'cp': 'copy-item', 'copy': 'copy-item', 'cpi': 'copy-item',
            'mv': 'move-item', 'move': 'move-item', 'mi': 'move-item',
            'echo': 'write-output', 'write': 'write-output',
        }
        return aliases.get(name.lower(), name.lower())
    
    def isSafeOutputCommand(name: str) -> bool:
        return name.lower() in ('out-null', 'out-string', 'out-host')
    
    def isCwdChangingCmdlet(name: str) -> bool:
        return name.lower() in ('set-location', 'push-location', 'pop-location', 'cd', 'sl')
    
    def isReadOnlyCommand(command: str, parsed: dict) -> bool:
        return False
    
    def isAllowlistedCommand(element: dict, text: str) -> bool:
        return False
    
    def isProvablySafeStatement(statement: dict) -> bool:
        return False
    
    def argLeaksValue(text: str, element: dict) -> bool:
        return False

try:
    from .toolName import POWERSHELL_TOOL_NAME
except ImportError:
    POWERSHELL_TOOL_NAME = 'PowerShell'

# Type aliases
PowerShellInput = Dict[str, Any]
PermissionResult = Dict[str, Any]
PermissionRule = Dict[str, Any]
ToolPermissionContext = Dict[str, Any]
ToolUseContext = Any

# Matches `$var = `, `$var += `, `$env:X = `, `$x ??= ` etc.
PS_ASSIGN_PREFIX_RE = re.compile(r'^\$[\w:]+\s*(?:[+\-*/%]|\?\?)?\s*=\s*')

GIT_SAFETY_WRITE_CMDLETS: Set[str] = {
    'new-item', 'set-content', 'add-content', 'out-file',
    'copy-item', 'move-item', 'rename-item', 'expand-archive',
    'invoke-webrequest', 'invoke-restmethod', 'tee-object',
    'export-csv', 'export-clixml',
}

GIT_SAFETY_ARCHIVE_EXTRACTORS: Set[str] = {
    'tar', 'tar.exe', 'bsdtar', 'bsdtar.exe',
    'unzip', 'unzip.exe', '7z', '7z.exe', '7za', '7za.exe',
    'gzip', 'gzip.exe', 'gunzip', 'gunzip.exe',
    'expand-archive',
}


def powershellPermissionRule(permission_rule: str) -> Dict[str, Any]:
    """Parse a permission rule string into a structured rule object."""
    return parsePermissionRule(permission_rule)


def suggestionForExactCommand(command: str) -> List[Dict[str, Any]]:
    """
    Generate permission update suggestion for exact command match.
    
    Skip exact-command suggestion for commands that can't round-trip cleanly:
    - Multi-line: newlines don't survive normalization
    - Literal *: storing `Remove-Item * -Force` re-parses as wildcard rule
    """
    if '\n' in command or '*' in command:
        return []
    return sharedSuggestionForExactCommand(POWERSHELL_TOOL_NAME, command)


def filterRulesByContentsMatchingInput(
    input_cmd: PowerShellInput,
    rules: Dict[str, PermissionRule],
    matchMode: str,
    behavior: str,
) -> List[PermissionRule]:
    """
    Filter rules by contents matching an input command.
    PowerShell-specific: uses case-insensitive matching throughout.
    """
    command = input_cmd.get('command', '').strip()
    if not command:
        return []

    def str_equals(a: str, b: str) -> bool:
        return a.lower() == b.lower()

    def str_starts_with(s: str, prefix: str) -> bool:
        return s.lower().startswith(prefix.lower())

    # SECURITY: stripModulePrefix on RULE names widens secondary-canonical match
    def strip_module_for_rule(name: str) -> str:
        if behavior == 'allow':
            return name
        return stripModulePrefix(name)

    # Extract command name
    parts = command.split()
    raw_cmd_name = parts[0] if parts else ''
    input_cmd_name = stripModulePrefix(raw_cmd_name)
    input_canonical = resolveToCanonical(input_cmd_name)

    # Build canonical command with normalized whitespace
    rest = re.sub(r'^\s+', ' ', command[len(raw_cmd_name):])
    canonical_command = input_canonical + rest

    matching_rules = []
    for rule_content, rule in rules.items():
        rule_type = rule.get('type', 'exact')

        def matches_command(cmd: str) -> bool:
            if rule_type == 'exact':
                return str_equals(rule.get('command', ''), cmd)
            elif rule_type == 'prefix':
                if matchMode == 'exact':
                    return str_equals(rule.get('prefix', ''), cmd)
                return (str_equals(cmd, rule.get('prefix', '')) or 
                        str_starts_with(cmd, rule.get('prefix', '') + ' '))
            elif rule_type == 'wildcard':
                if matchMode == 'exact':
                    return False
                return matchWildcardPattern(rule.get('pattern', ''), cmd, True)
            return False

        # Check against original command
        if matches_command(command):
            matching_rules.append(rule)
            continue

        # Check against canonical form
        if matches_command(canonical_command):
            matching_rules.append(rule)
            continue

        # Check canonical resolution (deny rm also blocks Remove-Item)
        if rule_type == 'exact':
            rule_parts = rule.get('command', '').split()
            raw_rule_cmd = rule_parts[0] if rule_parts else ''
            rule_canonical = resolveToCanonical(strip_module_for_rule(raw_rule_cmd))
            if rule_canonical == input_canonical:
                rule_rest = re.sub(r'^\s+', ' ', rule.get('command', '')[len(raw_rule_cmd):])
                input_rest = rest
                if str_equals(rule_rest, input_rest):
                    matching_rules.append(rule)
        elif rule_type == 'prefix':
            rule_parts = rule.get('prefix', '').split()
            raw_rule_cmd = rule_parts[0] if rule_parts else ''
            rule_canonical = resolveToCanonical(strip_module_for_rule(raw_rule_cmd))
            if rule_canonical == input_canonical:
                rule_rest = re.sub(r'^\s+', ' ', rule.get('prefix', '')[len(raw_rule_cmd):])
                canonical_prefix = input_canonical + rule_rest
                if matchMode == 'exact':
                    if str_equals(canonical_prefix, canonical_command):
                        matching_rules.append(rule)
                else:
                    if (str_equals(canonical_command, canonical_prefix) or
                            str_starts_with(canonical_command, canonical_prefix + ' ')):
                        matching_rules.append(rule)
        elif rule_type == 'wildcard':
            rule_parts = rule.get('pattern', '').split()
            raw_rule_cmd = rule_parts[0] if rule_parts else ''
            rule_canonical = resolveToCanonical(strip_module_for_rule(raw_rule_cmd))
            if rule_canonical == input_canonical and matchMode != 'exact':
                rule_rest = re.sub(r'^\s+', ' ', rule.get('pattern', '')[len(raw_rule_cmd):])
                canonical_pattern = input_canonical + rule_rest
                if matchWildcardPattern(canonical_pattern, canonical_command, True):
                    matching_rules.append(rule)

    return matching_rules


def matchingRulesForInput(
    input_cmd: PowerShellInput,
    toolPermissionContext: ToolPermissionContext,
    matchMode: str,
) -> Dict[str, List[PermissionRule]]:
    """Get matching rules for input across all rule types (deny, ask, allow)."""
    deny_rule_by_contents = getRuleByContentsForToolName(
        toolPermissionContext, POWERSHELL_TOOL_NAME, 'deny'
    )
    matching_deny_rules = filterRulesByContentsMatchingInput(
        input_cmd, deny_rule_by_contents, matchMode, 'deny'
    )

    ask_rule_by_contents = getRuleByContentsForToolName(
        toolPermissionContext, POWERSHELL_TOOL_NAME, 'ask'
    )
    matching_ask_rules = filterRulesByContentsMatchingInput(
        input_cmd, ask_rule_by_contents, matchMode, 'ask'
    )

    allow_rule_by_contents = getRuleByContentsForToolName(
        toolPermissionContext, POWERSHELL_TOOL_NAME, 'allow'
    )
    matching_allow_rules = filterRulesByContentsMatchingInput(
        input_cmd, allow_rule_by_contents, matchMode, 'allow'
    )

    return {
        'matchingDenyRules': matching_deny_rules,
        'matchingAskRules': matching_ask_rules,
        'matchingAllowRules': matching_allow_rules,
    }


def powershellToolCheckExactMatchPermission(
    input_cmd: PowerShellInput,
    toolPermissionContext: ToolPermissionContext,
) -> PermissionResult:
    """Check if the command is an exact match for a permission rule."""
    trimmed_command = input_cmd.get('command', '').strip()
    result = matchingRulesForInput(input_cmd, toolPermissionContext, 'exact')

    if result['matchingDenyRules']:
        return {
            'behavior': 'deny',
            'message': f'Permission to use {POWERSHELL_TOOL_NAME} with command {trimmed_command} has been denied.',
            'decisionReason': {'type': 'rule', 'rule': result['matchingDenyRules'][0]},
        }

    if result['matchingAskRules']:
        return {
            'behavior': 'ask',
            'message': createPermissionRequestMessage(POWERSHELL_TOOL_NAME),
            'decisionReason': {'type': 'rule', 'rule': result['matchingAskRules'][0]},
        }

    if result['matchingAllowRules']:
        return {
            'behavior': 'allow',
            'updatedInput': input_cmd,
            'decisionReason': {'type': 'rule', 'rule': result['matchingAllowRules'][0]},
        }

    decision_reason = {'type': 'other', 'reason': 'This command requires approval'}
    return {
        'behavior': 'passthrough',
        'message': createPermissionRequestMessage(POWERSHELL_TOOL_NAME, decision_reason),
        'decisionReason': decision_reason,
        'suggestions': suggestionForExactCommand(trimmed_command),
    }


def powershellToolCheckPermission(
    input_cmd: PowerShellInput,
    toolPermissionContext: ToolPermissionContext,
) -> PermissionResult:
    """Check permission for a PowerShell command including prefix matches."""
    command = input_cmd.get('command', '').strip()

    # 1. Check exact match first
    exact_match_result = powershellToolCheckExactMatchPermission(input_cmd, toolPermissionContext)

    # 1a. Deny/ask if exact command has a rule
    if exact_match_result['behavior'] in ('deny', 'ask'):
        return exact_match_result

    # 2. Find all matching rules (prefix or exact)
    result = matchingRulesForInput(input_cmd, toolPermissionContext, 'prefix')

    # 2a. Deny if command has a deny rule
    if result['matchingDenyRules']:
        return {
            'behavior': 'deny',
            'message': f'Permission to use {POWERSHELL_TOOL_NAME} with command {command} has been denied.',
            'decisionReason': {'type': 'rule', 'rule': result['matchingDenyRules'][0]},
        }

    # 2b. Ask if command has an ask rule
    if result['matchingAskRules']:
        return {
            'behavior': 'ask',
            'message': createPermissionRequestMessage(POWERSHELL_TOOL_NAME),
            'decisionReason': {'type': 'rule', 'rule': result['matchingAskRules'][0]},
        }

    # 3. Allow if command had an exact match allow
    if exact_match_result['behavior'] == 'allow':
        return exact_match_result

    # 4. Allow if command has an allow rule
    if result['matchingAllowRules']:
        return {
            'behavior': 'allow',
            'updatedInput': input_cmd,
            'decisionReason': {'type': 'rule', 'rule': result['matchingAllowRules'][0]},
        }

    # 5. Passthrough
    decision_reason = {'type': 'other', 'reason': 'This command requires approval'}
    return {
        'behavior': 'passthrough',
        'message': createPermissionRequestMessage(POWERSHELL_TOOL_NAME, decision_reason),
        'decisionReason': decision_reason,
        'suggestions': suggestionForExactCommand(command),
    }


async def extractCommandName(command: str) -> str:
    """Extract the command name from a PowerShell command string."""
    trimmed = command.strip()
    if not trimmed:
        return ''
    parsed = await parsePowerShellCommand(trimmed)
    names = getAllCommandNames(parsed)
    return names[0] if names else ''


async def getSubCommandsForPermissionCheck(
    parsed: Dict[str, Any],
    original_command: str,
) -> List[Dict[str, Any]]:
    """
    Extract sub-commands that need independent permission checking.
    Safe output cmdlets are flagged but NOT filtered out.
    """
    if not parsed.get('valid', False):
        return [{
            'text': original_command,
            'element': {
                'name': await extractCommandName(original_command),
                'nameType': 'unknown',
                'elementType': 'CommandAst',
                'args': [],
                'text': original_command,
            },
            'statement': None,
            'isSafeOutput': False,
        }]

    sub_commands = []

    for statement in parsed.get('statements', []):
        for cmd in statement.get('commands', []):
            if cmd.get('elementType') != 'CommandAst':
                continue
            sub_commands.append({
                'text': cmd.get('text', ''),
                'element': cmd,
                'statement': statement,
                'isSafeOutput': (
                    cmd.get('nameType') != 'application' and
                    isSafeOutputCommand(cmd.get('name', '')) and
                    len(cmd.get('args', [])) == 0
                ),
            })

        # Check nested commands from control flow
        if statement.get('nestedCommands'):
            for cmd in statement['nestedCommands']:
                sub_commands.append({
                    'text': cmd.get('text', ''),
                    'element': cmd,
                    'statement': statement,
                    'isSafeOutput': (
                        cmd.get('nameType') != 'application' and
                        isSafeOutputCommand(cmd.get('name', '')) and
                        len(cmd.get('args', [])) == 0
                    ),
                })

    if sub_commands:
        return sub_commands

    # Fallback
    return [{
        'text': original_command,
        'element': {
            'name': await extractCommandName(original_command),
            'nameType': 'unknown',
            'elementType': 'CommandAst',
            'args': [],
            'text': original_command,
        },
        'statement': None,
        'isSafeOutput': False,
    }]


async def powershellToolHasPermission(
    input_cmd: PowerShellInput,
    context: ToolUseContext,
) -> PermissionResult:
    """
    Main permission check function for PowerShell tool.
    
    Implements the full permission flow with collect-then-reduce pattern:
    deny > ask > allow > passthrough
    """
    tool_permission_context = getattr(context, 'getAppState', lambda: {'toolPermissionContext': {}})().get('toolPermissionContext', {})
    command = input_cmd.get('command', '').strip()

    # Empty command check
    if not command:
        return {
            'behavior': 'allow',
            'updatedInput': input_cmd,
            'decisionReason': {'type': 'other', 'reason': 'Empty command is safe'},
        }

    # Parse the command once
    parsed = await parsePowerShellCommand(command)

    # SECURITY: Check deny/ask rules BEFORE parse validity check
    # 1. Check exact match first
    exact_match_result = powershellToolCheckExactMatchPermission(input_cmd, tool_permission_context)

    # Exact command was denied
    if exact_match_result['behavior'] == 'deny':
        return exact_match_result

    # 2. Check prefix/wildcard rules
    prefix_result = matchingRulesForInput(input_cmd, tool_permission_context, 'prefix')

    # 2a. Deny if command has a deny rule
    if prefix_result['matchingDenyRules']:
        return {
            'behavior': 'deny',
            'message': f'Permission to use {POWERSHELL_TOOL_NAME} with command {command} has been denied.',
            'decisionReason': {'type': 'rule', 'rule': prefix_result['matchingDenyRules'][0]},
        }

    # 2b. Ask if command has an ask rule — DEFERRED into decisions[]
    pre_parse_ask_decision = None
    if prefix_result['matchingAskRules']:
        pre_parse_ask_decision = {
            'behavior': 'ask',
            'message': createPermissionRequestMessage(POWERSHELL_TOOL_NAME),
            'decisionReason': {'type': 'rule', 'rule': prefix_result['matchingAskRules'][0]},
        }

    # Block UNC paths — DEFERRED into decisions[]
    if pre_parse_ask_decision is None and containsVulnerableUncPath(command):
        pre_parse_ask_decision = {
            'behavior': 'ask',
            'message': 'Command contains a UNC path that could trigger network requests',
        }

    # 2c. Exact allow rules short-circuit when parsing failed AND no pre-parse ask
    if (exact_match_result['behavior'] == 'allow' and
            not parsed.get('valid', False) and
            pre_parse_ask_decision is None and
            classifyCommandName(command.split()[0] if command.split() else '') != 'application'):
        return exact_match_result

    # 0. Check if command can be parsed
    if not parsed.get('valid', False):
        # Fallback sub-command deny scan for parse-failed path
        backtick_stripped = command.replace('`\r\n', '').replace('`\n', '').replace('`', '')
        for fragment in re.split(r'[;|\n\r{}()&]+', backtick_stripped):
            trimmed_frag = fragment.strip()
            if not trimmed_frag:
                continue
            if (trimmed_frag == command and
                    not re.match(r'^\$[\w:]', trimmed_frag) and
                    not re.match(r'^[&.]\s', trimmed_frag)):
                continue

            # Normalize invocation operators and assignment prefixes
            normalized = trimmed_frag
            while True:
                m = PS_ASSIGN_PREFIX_RE.match(normalized)
                if not m:
                    break
                normalized = normalized[len(m.group(0)):]
            normalized = re.sub(r'^[&.]\s+', '', normalized)
            
            raw_first = normalized.split()[0] if normalized.split() else ''
            first_tok = raw_first.strip("'\"")
            normalized_frag = first_tok + normalized[len(raw_first):]

            # Check dangerous removal paths
            if resolveToCanonical(first_tok) == 'remove-item':
                for arg in normalized.split()[1:]:
                    if arg[0] if arg else '' not in PS_TOKENIZER_DASH_CHARS:
                        if isDangerousRemovalRawPath(arg):
                            return dangerousRemovalDeny(arg)

            # Check deny rules on fragment
            frag_result = matchingRulesForInput(
                {'command': normalized_frag}, tool_permission_context, 'prefix'
            )
            if frag_result['matchingDenyRules']:
                return {
                    'behavior': 'deny',
                    'message': f'Permission to use {POWERSHELL_TOOL_NAME} with command {command} has been denied.',
                    'decisionReason': {'type': 'rule', 'rule': frag_result['matchingDenyRules'][0]},
                }

        # Preserve pre-parse ask messaging
        if pre_parse_ask_decision is not None:
            return pre_parse_ask_decision

        decision_reason = {
            'type': 'other',
            'reason': f"Command contains malformed syntax that cannot be parsed: {parsed.get('errors', [{}])[0].get('message', 'unknown error')}",
        }
        return {
            'behavior': 'ask',
            'decisionReason': decision_reason,
            'message': createPermissionRequestMessage(POWERSHELL_TOOL_NAME, decision_reason),
        }

    # ========================================================================
    # COLLECT-THEN-REDUCE: post-parse decisions
    # ========================================================================
    all_sub_commands = await getSubCommandsForPermissionCheck(parsed, command)
    decisions: List[PermissionResult] = []

    # Decision: deferred pre-parse ask
    if pre_parse_ask_decision is not None:
        decisions.append(pre_parse_ask_decision)

    # Decision: security check
    safety_result = powershellCommandIsSafe(command, parsed)
    if safety_result.get('behavior') != 'passthrough':
        decision_reason = {
            'type': 'other',
            'reason': (
                safety_result.get('message') if safety_result.get('behavior') == 'ask' and safety_result.get('message')
                else 'This command contains patterns that could pose security risks and requires approval'
            ),
        }
        decisions.append({
            'behavior': 'ask',
            'message': createPermissionRequestMessage(POWERSHELL_TOOL_NAME, decision_reason),
            'decisionReason': decision_reason,
            'suggestions': suggestionForExactCommand(command),
        })

    # Decision: using statements
    if parsed.get('hasUsingStatements'):
        decision_reason = {
            'type': 'other',
            'reason': 'Command contains a `using` statement that may load external code (module or assembly)',
        }
        decisions.append({
            'behavior': 'ask',
            'message': createPermissionRequestMessage(POWERSHELL_TOOL_NAME, decision_reason),
            'decisionReason': decision_reason,
            'suggestions': suggestionForExactCommand(command),
        })

    # Decision: script requirements
    if parsed.get('hasScriptRequirements'):
        decision_reason = {
            'type': 'other',
            'reason': 'Command contains a `#Requires` directive that may trigger module loading',
        }
        decisions.append({
            'behavior': 'ask',
            'message': createPermissionRequestMessage(POWERSHELL_TOOL_NAME, decision_reason),
            'decisionReason': decision_reason,
            'suggestions': suggestionForExactCommand(command),
        })

    # Decision: provider/UNC scan
    NON_FS_PROVIDER_PATTERN = re.compile(
        r'^(?:[\w.]+\\)?(env|hklm|hkcu|function|alias|variable|cert|wsman|registry)::?',
        re.IGNORECASE
    )

    def extract_provider_path_from_arg(arg: str) -> str:
        s = arg
        if len(s) > 0 and s[0] in PS_TOKENIZER_DASH_CHARS:
            colon_idx = s.find(':', 1)
            if colon_idx > 0:
                s = s[colon_idx + 1:]
        return s.replace('`', '')

    def provider_or_unc_decision_for_arg(arg: str) -> Optional[PermissionResult]:
        value = extract_provider_path_from_arg(arg)
        if NON_FS_PROVIDER_PATTERN.match(value):
            return {
                'behavior': 'ask',
                'message': f"Command argument '{arg}' uses a non-filesystem provider path and requires approval",
            }
        if containsVulnerableUncPath(value):
            return {
                'behavior': 'ask',
                'message': f"Command argument '{arg}' contains a UNC path that could trigger network requests",
            }
        return None

    provider_scan_found = False
    for statement in parsed.get('statements', []):
        if provider_scan_found:
            break
        for cmd in statement.get('commands', []):
            if provider_scan_found:
                break
            if cmd.get('elementType') != 'CommandAst':
                continue
            for arg in cmd.get('args', []):
                decision = provider_or_unc_decision_for_arg(arg)
                if decision is not None:
                    decisions.append(decision)
                    provider_scan_found = True
                    break

        if not provider_scan_found and statement.get('nestedCommands'):
            for cmd in statement['nestedCommands']:
                if provider_scan_found:
                    break
                for arg in cmd.get('args', []):
                    decision = provider_or_unc_decision_for_arg(arg)
                    if decision is not None:
                        decisions.append(decision)
                        provider_scan_found = True
                        break

    # Decision: per-sub-command deny/ask rules
    for sub_info in all_sub_commands:
        sub_cmd = sub_info['text']
        element = sub_info['element']

        canonical_sub_cmd = None
        if element.get('name', ''):
            canonical_sub_cmd = ' '.join([element['name']] + element.get('args', []))

        sub_input = {'command': sub_cmd}
        sub_result = matchingRulesForInput(sub_input, tool_permission_context, 'prefix')
        matched_deny_rule = sub_result['matchingDenyRules'][0] if sub_result['matchingDenyRules'] else None
        matched_ask_rule = sub_result['matchingAskRules'][0] if sub_result['matchingAskRules'] else None

        if matched_deny_rule is None and canonical_sub_cmd is not None:
            canonical_result = matchingRulesForInput(
                {'command': canonical_sub_cmd}, tool_permission_context, 'prefix'
            )
            matched_deny_rule = canonical_result['matchingDenyRules'][0] if canonical_result['matchingDenyRules'] else None
            if matched_ask_rule is None and canonical_result['matchingAskRules']:
                matched_ask_rule = canonical_result['matchingAskRules'][0]

        if matched_deny_rule is not None:
            decisions.append({
                'behavior': 'deny',
                'message': f'Permission to use {POWERSHELL_TOOL_NAME} with command {command} has been denied.',
                'decisionReason': {'type': 'rule', 'rule': matched_deny_rule},
            })
        elif matched_ask_rule is not None:
            decisions.append({
                'behavior': 'ask',
                'message': createPermissionRequestMessage(POWERSHELL_TOOL_NAME),
                'decisionReason': {'type': 'rule', 'rule': matched_ask_rule},
            })

    # Decision: cd+git compound guard
    has_cd_sub_command = (
        len(all_sub_commands) > 1 and
        any(isCwdChangingCmdlet(sub['element'].get('name', '')) for sub in all_sub_commands)
    )
    has_symlink_create = (
        len(all_sub_commands) > 1 and
        any(isSymlinkCreatingCommand(sub['element']) for sub in all_sub_commands)
    )
    has_git_sub_command = any(
        resolveToCanonical(sub['element'].get('name', '')) == 'git'
        for sub in all_sub_commands
    )

    if has_cd_sub_command and has_git_sub_command:
        decisions.append({
            'behavior': 'ask',
            'message': 'Compound commands with cd/Set-Location and git require approval to prevent bare repository attacks',
        })

    # Decision: bare-git-repo guard
    if has_git_sub_command and isCurrentDirectoryBareGitRepo():
        decisions.append({
            'behavior': 'ask',
            'message': 'Git command in a directory with bare-repository indicators (HEAD, objects/, refs/ in cwd without .git/HEAD). Git may execute hooks from cwd.',
        })

    # Decision: git-internal-paths write guard
    if has_git_sub_command:
        def writes_to_git_internal(sub_info: dict) -> bool:
            element = sub_info['element']
            statement = sub_info['statement']

            # Check redirections
            for r in element.get('redirections', []):
                if isGitInternalPathPS(r.get('target', '')):
                    return True

            # Check write cmdlet args
            canonical = resolveToCanonical(element.get('name', ''))
            if canonical not in GIT_SAFETY_WRITE_CMDLETS:
                return False

            for arg in element.get('args', []):
                for part in arg.split(','):
                    if isGitInternalPathPS(part):
                        return True

            # Check pipeline input
            if statement is not None:
                for c in statement.get('commands', []):
                    if c.get('elementType') == 'CommandAst':
                        continue
                    if isGitInternalPathPS(c.get('text', '')):
                        return True

            return False

        writes_to_git = any(writes_to_git_internal(sub) for sub in all_sub_commands)
        redir_writes_to_git = any(
            isGitInternalPathPS(r.get('target', ''))
            for r in getFileRedirections(parsed)
        )

        if writes_to_git or redir_writes_to_git:
            decisions.append({
                'behavior': 'ask',
                'message': 'Command writes to a git-internal path (HEAD, objects/, refs/, hooks/, .git/) and runs git. This could plant a malicious hook that git then executes.',
            })

        # Archive-extraction TOCTOU
        has_archive_extractor = any(
            sub['element'].get('name', '').lower() in GIT_SAFETY_ARCHIVE_EXTRACTORS
            for sub in all_sub_commands
        )
        if has_archive_extractor:
            decisions.append({
                'behavior': 'ask',
                'message': 'Compound command extracts an archive and runs git. Archive contents may plant bare-repository indicators (HEAD, hooks/, refs/) that git then treats as the repository root.',
            })

    # Decision: .git/ writes without git
    found_dot_git = (
        any(
            any(isDotGitPathPS(r.get('target', '')) for r in sub['element'].get('redirections', [])) or
            (resolveToCanonical(sub['element'].get('name', '')) in GIT_SAFETY_WRITE_CMDLETS and
             any(isDotGitPathPS(part) for arg in sub['element'].get('args', []) for part in arg.split(',')))
            for sub in all_sub_commands
        ) or
        any(isDotGitPathPS(r.get('target', '')) for r in getFileRedirections(parsed))
    )
    if found_dot_git:
        decisions.append({
            'behavior': 'ask',
            'message': 'Command writes to .git/ — hooks or config planted there execute on the next git operation.',
        })

    # Decision: path constraints
    path_result = checkPathConstraints(
        input_cmd, parsed, tool_permission_context, has_cd_sub_command
    )
    if path_result.get('behavior') != 'passthrough':
        decisions.append(path_result)

    # Decision: exact allow (parse-succeeded case)
    if (exact_match_result['behavior'] == 'allow' and
            all_sub_commands and
            all(
                sub['element'].get('nameType') != 'application' and
                not argLeaksValue(sub['text'], sub['element'])
                for sub in all_sub_commands
            )):
        decisions.append(exact_match_result)

    # Decision: read-only allowlist
    if isReadOnlyCommand(command, parsed):
        decisions.append({
            'behavior': 'allow',
            'updatedInput': input_cmd,
            'decisionReason': {'type': 'other', 'reason': 'Command is read-only and safe to execute'},
        })

    # Decision: file redirections
    file_redirections = getFileRedirections(parsed)
    if file_redirections:
        decisions.append({
            'behavior': 'ask',
            'message': 'Command contains file redirections that could write to arbitrary paths',
            'suggestions': suggestionForExactCommand(command),
        })

    # Decision: mode-specific handling
    mode_result = checkPermissionMode(input_cmd, parsed, tool_permission_context)
    if mode_result.get('behavior') != 'passthrough':
        decisions.append(mode_result)

    # REDUCE: deny > ask > allow > passthrough
    denied_decision = next((d for d in decisions if d.get('behavior') == 'deny'), None)
    if denied_decision is not None:
        return denied_decision

    ask_decision = next((d for d in decisions if d.get('behavior') == 'ask'), None)
    if ask_decision is not None:
        return ask_decision

    allow_decision = next((d for d in decisions if d.get('behavior') == 'allow'), None)
    if allow_decision is not None:
        return allow_decision

    # 5. Pipeline/statement splitting: check each sub-command independently
    # Filter out safe output cmdlets — they were checked for deny rules in step 4.4
    # but shouldn't need independent approval here.
    # SECURITY: Keep 'application' commands in the list so they reach isAllowlistedCommand
    sub_commands = [
        sub for sub in all_sub_commands
        if not (sub['isSafeOutput'] and sub['element'].get('nameType') != 'application')
    ]

    # Filter out cd to CWD
    def should_keep_sub(sub: dict) -> bool:
        element = sub['element']
        if element.get('nameType') == 'application':
            return True
        canonical = resolveToCanonical(element.get('name', ''))
        if canonical == 'set-location' and element.get('args'):
            target = next(
                (arg for arg in element['args'] if not arg or arg[0] not in PS_TOKENIZER_DASH_CHARS),
                None
            )
            if target and str(Path(getCwd()) / target) == getCwd():
                return False
        return True

    sub_commands = [sub for sub in sub_commands if should_keep_sub(sub)]

    sub_commands_needing_approval = []
    statements_seen_in_loop = set()

    for sub_info in sub_commands:
        sub_cmd = sub_info['text']
        element = sub_info['element']
        statement = sub_info['statement']

        # Check deny rules FIRST
        sub_input = {'command': sub_cmd}
        sub_result = powershellToolCheckPermission(sub_input, tool_permission_context)

        if sub_result['behavior'] == 'deny':
            return {
                'behavior': 'deny',
                'message': f'Permission to use {POWERSHELL_TOOL_NAME} with command {command} has been denied.',
                'decisionReason': sub_result.get('decisionReason'),
            }

        if sub_result['behavior'] == 'ask':
            if statement is not None:
                statements_seen_in_loop.add(id(statement))
            sub_commands_needing_approval.append(sub_cmd)
            continue

        # Explicitly allowed by user rule — BUT NOT for applications/scripts
        if (sub_result['behavior'] == 'allow' and
                element.get('nameType') != 'application' and
                not has_symlink_create):
            if argLeaksValue(sub_cmd, element):
                if statement is not None:
                    statements_seen_in_loop.add(id(statement))
                sub_commands_needing_approval.append(sub_cmd)
                continue
            continue

        if sub_result['behavior'] == 'allow':
            if statement is not None:
                statements_seen_in_loop.add(id(statement))
            sub_commands_needing_approval.append(sub_cmd)
            continue

        # Fail-closed gate
        if (statement is not None and
                not has_cd_sub_command and
                not has_symlink_create and
                isProvablySafeStatement(statement) and
                isAllowlistedCommand(element, sub_cmd)):
            continue

        # Check per-sub-command acceptEdits mode
        if statement is not None and not has_cd_sub_command and not has_symlink_create:
            sub_mode_result = checkPermissionMode(
                {'command': sub_cmd},
                {
                    'valid': True,
                    'errors': [],
                    'variables': parsed.get('variables', []),
                    'hasStopParsing': parsed.get('hasStopParsing', False),
                    'originalCommand': sub_cmd,
                    'statements': [statement],
                },
                tool_permission_context,
            )
            if sub_mode_result.get('behavior') == 'allow':
                continue

        # Needs approval
        if statement is not None:
            statements_seen_in_loop.add(id(statement))
        sub_commands_needing_approval.append(sub_cmd)

    # Fail-closed gate: check statements not seen in loop
    for stmt in parsed.get('statements', []):
        if not isProvablySafeStatement(stmt) and id(stmt) not in statements_seen_in_loop:
            sub_commands_needing_approval.append(stmt.get('text', ''))

    if not sub_commands_needing_approval:
        # Check for script blocks
        if deriveSecurityFlags(parsed).get('hasScriptBlocks', False):
            return {
                'behavior': 'ask',
                'message': createPermissionRequestMessage(POWERSHELL_TOOL_NAME),
                'decisionReason': {
                    'type': 'other',
                    'reason': 'Pipeline consists of output-formatting cmdlets with script blocks — block content cannot be verified',
                },
            }
        return {
            'behavior': 'allow',
            'updatedInput': input_cmd,
            'decisionReason': {'type': 'other', 'reason': 'All pipeline commands are individually allowed'},
        }

    # Build suggestions
    pending_suggestions = []
    for sub_cmd in sub_commands_needing_approval:
        pending_suggestions.extend(suggestionForExactCommand(sub_cmd))

    decision_reason = {'type': 'other', 'reason': 'This command requires approval'}
    return {
        'behavior': 'passthrough',
        'message': createPermissionRequestMessage(POWERSHELL_TOOL_NAME, decision_reason),
        'decisionReason': decision_reason,
        'suggestions': pending_suggestions,
    }
