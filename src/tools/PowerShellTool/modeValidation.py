"""
PowerShell permission mode validation.

Checks if commands should be auto-allowed based on the current permission mode.
In acceptEdits mode, filesystem-modifying PowerShell cmdlets are auto-allowed.
Follows the same patterns as BashTool/modeValidation.py.
"""

from typing import Any, Dict, List, Optional

# Defensive imports
try:
    from ...Tool import ToolPermissionContext
except ImportError:
    class ToolPermissionContext:
        def __init__(self, mode='default'):
            self.mode = mode

try:
    from ...utils.permissions.PermissionResult import PermissionResult
except ImportError:
    class PermissionResult(Dict[str, Any]):
        pass

try:
    from ...utils.powershell.parser import (
        deriveSecurityFlags,
        getPipelineSegments,
        ParsedPowerShellCommand,
        PS_TOKENIZER_DASH_CHARS,
    )
except ImportError:
    class ParsedPowerShellCommand:
        def __init__(self):
            self.valid = False
    
    PS_TOKENIZER_DASH_CHARS = set()
    
    def deriveSecurityFlags(parsed):
        return type('obj', (object,), {
            'hasSubExpressions': False,
            'hasScriptBlocks': False,
            'hasMemberInvocations': False,
            'hasSplatting': False,
            'hasAssignments': False,
            'hasStopParsing': False,
            'hasExpandableStrings': False,
        })()
    
    def getPipelineSegments(parsed):
        return []

try:
    from .readOnlyValidation import (
        argLeaksValue,
        isAllowlistedPipelineTail,
        isCwdChangingCmdlet,
        isSafeOutputCommand,
        resolveToCanonical,
    )
except ImportError:
    def resolveToCanonical(name):
        return name.lower()
    
    def isCwdChangingCmdlet(name):
        return name.lower() in ['set-location', 'push-location', 'pop-location']
    
    def isSafeOutputCommand(name):
        return name.lower() in ['out-null']
    
    def isAllowlistedPipelineTail(cmd, command):
        return False
    
    def argLeaksValue(name, cmd):
        return False


# Filesystem-modifying cmdlets that are auto-allowed in acceptEdits mode.
# Stored as canonical (lowercase) cmdlet names.
#
# Tier 3 cmdlets with complex parameter binding removed — they fall through to
# 'ask'. Only simple write cmdlets (first positional = -Path) are auto-allowed
# here, and they get path validation via CMDLET_PATH_CONFIG in pathValidation.py.
ACCEPT_EDITS_ALLOWED_CMDLETS = frozenset([
    'set-content',
    'add-content',
    'remove-item',
    'clear-content',
])


def isAcceptEditsAllowedCmdlet(name: str) -> bool:
    """
    Check if a cmdlet is allowed in acceptEdits mode.
    
    resolveToCanonical handles aliases via COMMON_ALIASES, so e.g. 'rm' → 'remove-item',
    'ac' → 'add-content'. Any alias that resolves to an allowed cmdlet is automatically
    allowed. Tier 3 cmdlets (new-item, copy-item, move-item, etc.) and their aliases
    (mkdir, ni, cp, mv, etc.) resolve to cmdlets NOT in the set and fall through to 'ask'.
    """
    canonical = resolveToCanonical(name)
    return canonical in ACCEPT_EDITS_ALLOWED_CMDLETS


# New-Item -ItemType values that create filesystem links (reparse points or
# hard links). All three redirect path resolution at runtime — symbolic links
# and junctions are directory/file reparse points; hard links alias a file's
# inode. Any of these let a later relative-path write land outside the
# validator's view.
LINK_ITEM_TYPES = frozenset(['symboliclink', 'junction', 'hardlink'])


def isItemTypeParamAbbrev(p: str) -> bool:
    """
    Check if a lowered, dash-normalized arg (colon-value stripped) is an
    unambiguous PowerShell abbreviation of New-Item's -ItemType or -Type param.
    Min prefixes: `-it` (avoids ambiguity with other New-Item params), `-ty`
    (avoids `-t` colliding with `-Target`).
    """
    return (
        (len(p) >= 3 and '-itemtype'.startswith(p)) or
        (len(p) >= 3 and '-type'.startswith(p))
    )


def isSymlinkCreatingCommand(cmd: Dict[str, Any]) -> bool:
    """
    Detects New-Item creating a filesystem link (-ItemType SymbolicLink /
    Junction / HardLink, or the -Type alias). Links poison subsequent path
    resolution the same way Set-Location/New-PSDrive do: a relative path
    through the link resolves to the link target, not the validator's view.
    Finding #18.

    Handles PS parameter abbreviation (`-it`, `-ite`, ... `-itemtype`; `-ty`,
    `-typ`, `-type`), unicode dash prefixes (en-dash/em-dash/horizontal-bar),
    and colon-bound values (`-it:Junction`).
    """
    canonical = resolveToCanonical(cmd.get('name', ''))
    if canonical != 'new-item':
        return False
    
    args = cmd.get('args', [])
    for i in range(len(args)):
        raw = args[i] if i < len(args) else ''
        if len(raw) == 0:
            continue
        
        # Normalize unicode dash prefixes (–, —, ―) and forward-slash (PS 5.1
        # parameter prefix) → ASCII `-` so prefix comparison works. PS tokenizer
        # treats all four dash chars plus `/` as parameter markers. (bug #26)
        normalized = ('-' + raw[1:]) if (raw[0] in PS_TOKENIZER_DASH_CHARS or raw[0] == '/') else raw
        lower = normalized.lower()
        
        # Split colon-bound value: -it:SymbolicLink → param='-it', val='symboliclink'
        colon_idx = lower.find(':', 1)
        param_raw = lower[:colon_idx] if colon_idx > 0 else lower
        
        # Strip backtick escapes: -Item`Type → -ItemType (bug #22)
        param = param_raw.replace('`', '')
        
        if not isItemTypeParamAbbrev(param):
            continue
        
        raw_val = (
            lower[colon_idx + 1:] if colon_idx > 0
            else (args[i + 1].lower() if i + 1 < len(args) else '')
        )
        
        # Strip backtick escapes from colon-bound value: -it:Sym`bolicLink → symboliclink
        # Mirrors the param-name strip. Space-separated args use .value
        # (backtick-resolved by .NET parser), but colon-bound uses .text (raw source).
        # Strip surrounding quotes: -it:'SymbolicLink' or -it:"Junction" (bug #6)
        val = raw_val.replace('`', '').strip("'\"")
        
        if val in LINK_ITEM_TYPES:
            return True
    
    return False


def checkPermissionMode(
    input_data: Dict[str, str],
    parsed: ParsedPowerShellCommand,
    tool_permission_context: ToolPermissionContext,
) -> PermissionResult:
    """
    Checks if commands should be handled differently based on the current permission mode.

    In acceptEdits mode, auto-allows filesystem-modifying PowerShell cmdlets.
    Uses the AST to resolve aliases before checking the allowlist.

    Args:
        input_data: The PowerShell command input
        parsed: The parsed AST of the command
        tool_permission_context: Context containing mode and permissions
    
    Returns:
        - 'allow' if the current mode permits auto-approval
        - 'passthrough' if no mode-specific handling applies
    """
    # Skip bypass and dontAsk modes (handled elsewhere)
    if tool_permission_context.mode in ['bypassPermissions', 'dontAsk']:
        return {
            'behavior': 'passthrough',
            'message': 'Mode is handled in main permission flow',
        }

    if tool_permission_context.mode != 'acceptEdits':
        return {
            'behavior': 'passthrough',
            'message': 'No mode-specific validation required',
        }

    # acceptEdits mode: check if all commands are filesystem-modifying cmdlets
    if not getattr(parsed, 'valid', False):
        return {
            'behavior': 'passthrough',
            'message': 'Cannot validate mode for unparsed command',
        }

    # SECURITY: Check for subexpressions, script blocks, or member invocations
    # that could be used to smuggle arbitrary code through acceptEdits mode.
    security_flags = deriveSecurityFlags(parsed)
    if (
        security_flags.hasSubExpressions or
        security_flags.hasScriptBlocks or
        security_flags.hasMemberInvocations or
        security_flags.hasSplatting or
        security_flags.hasAssignments or
        security_flags.hasStopParsing or
        security_flags.hasExpandableStrings
    ):
        return {
            'behavior': 'passthrough',
            'message':
                'Command contains subexpressions, script blocks, or member invocations that require approval',
        }

    segments = getPipelineSegments(parsed)

    # SECURITY: Empty segments with valid parse = no commands to check, don't auto-allow
    if len(segments) == 0:
        return {
            'behavior': 'passthrough',
            'message': 'No commands found to validate for acceptEdits mode',
        }

    # SECURITY: Compound cwd desync guard — BashTool parity.
    # When any statement in a compound contains Set-Location/Push-Location/Pop-Location
    # (or aliases like cd, sl, chdir, pushd, popd), the cwd changes between statements.
    # Path validation resolves relative paths against the stale process cwd, so a write
    # cmdlet in a later statement targets a different directory than the validator checked.
    # Example: `Set-Location ./.cortex; Set-Content ./settings.json '...'` — the validator
    # sees ./settings.json as /project/settings.json, but PowerShell writes to
    # /project/.cortex/settings.json. Refuse to auto-allow any write operation in a
    # compound that contains a cwd-changing command. This matches BashTool's
    # compoundCommandHasCd guard (BashTool/pathValidation.py:630-655).
    total_commands = sum(len(seg.commands) for seg in segments)
    if total_commands > 1:
        has_cd_command = False
        has_symlink_create = False
        has_write_command = False
        
        for seg in segments:
            for cmd in seg.commands:
                if cmd.get('elementType') != 'CommandAst':
                    continue
                if isCwdChangingCmdlet(cmd.get('name', '')):
                    has_cd_command = True
                if isSymlinkCreatingCommand(cmd):
                    has_symlink_create = True
                if isAcceptEditsAllowedCmdlet(cmd.get('name', '')):
                    has_write_command = True
        
        if has_cd_command and has_write_command:
            return {
                'behavior': 'passthrough',
                'message':
                    'Compound command contains a directory-changing command (Set-Location/Push-Location/Pop-Location) with a write operation — cannot auto-allow because path validation uses stale cwd',
            }
        
        # SECURITY: Link-create compound guard (finding #18). Mirrors the cd
        # guard above. `New-Item -ItemType SymbolicLink -Path ./link -Value /etc;
        # Get-Content ./link/passwd` — path validation resolves ./link/passwd
        # against cwd (no link there at validation time), but runtime follows
        # the just-created link to /etc/passwd. Same TOCTOU shape as cwd desync.
        # Applies to SymbolicLink, Junction, and HardLink — all three redirect
        # path resolution at runtime.
        # No `has_write_command` requirement: read-through-symlink is equally
        # dangerous (exfil via Get-Content ./link/etc/shadow), and any other
        # command using paths after a just-created link is unvalidatable.
        if has_symlink_create:
            return {
                'behavior': 'passthrough',
                'message':
                    'Compound command creates a filesystem link (New-Item -ItemType SymbolicLink/Junction/HardLink) — cannot auto-allow because path validation cannot follow just-created links',
            }

    for segment in segments:
        for cmd in segment.commands:
            if cmd.get('elementType') != 'CommandAst':
                # SECURITY: This guard is load-bearing for THREE cases. Do not narrow it.
                #
                # 1. Expression pipeline sources (designed): '/etc/passwd' | Remove-Item
                #    — the string literal is CommandExpressionAst, piped value binds to
                #    -Path. We cannot statically know what path it represents.
                #
                # 2. Control-flow statements (accidental but relied upon):
                #    foreach ($x in ...) { Remove-Item $x }. Non-PipelineAst statements
                #    produce a synthetic CommandExpressionAst entry in segment.commands
                #    (parser.py transformStatement). Without this guard, Remove-Item $x
                #    in nestedCommands would be checked below and auto-allowed — but $x
                #    is a loop-bound variable we cannot validate.
                #
                # 3. Non-PipelineAst redirection coverage (accidental): cmd && cmd2 > /tmp
                #    also produces a synthetic element here. isReadOnlyCommand relies on
                #    the same accident (its allowlist rejects the synthetic element's
                #    full-text name), so both paths fail safe together.
                return {
                    'behavior': 'passthrough',
                    'message': f"Pipeline contains expression source ({cmd.get('elementType')}) that cannot be statically validated",
                }
            
            # SECURITY: nameType is computed from the raw name before stripModulePrefix.
            # 'application' = raw name had path chars (. \\ /). scripts\\Remove-Item
            # strips to Remove-Item and would match ACCEPT_EDITS_ALLOWED_CMDLETS below,
            # but PowerShell runs scripts\\Remove-Item.ps1. Same gate as isAllowlistedCommand.
            if cmd.get('nameType') == 'application':
                return {
                    'behavior': 'passthrough',
                    'message': f"Command '{cmd.get('name')}' resolved from a path-like name and requires approval",
                }
            
            # SECURITY: elementTypes whitelist — same as isAllowlistedCommand.
            # deriveSecurityFlags above checks hasSubExpressions/etc. but does NOT
            # flag bare Variable/Other elementTypes. `Remove-Item $env:PATH`:
            #   elementTypes = ['StringConstant', 'Variable']
            #   deriveSecurityFlags: no subexpression → passes
            #   checkPathConstraints: resolves literal text '$env:PATH' as relative
            #     path → cwd/$env:PATH → inside cwd → allow
            #   RUNTIME: PowerShell expands $env:PATH → deletes actual env value path
            # isAllowlistedCommand rejects non-StringConstant/Parameter; this is the
            # acceptEdits parity gate.
            #
            # Also check colon-bound expression metachars (same as isAllowlistedCommand's
            # colon-bound check). `Remove-Item -Path:(1 > /tmp/x)`:
            #   elementTypes = ['StringConstant', 'Parameter'] — passes whitelist above
            #   deriveSecurityFlags: ParenExpressionAst in .Argument not detected by
            #     Get-SecurityPatterns (ParenExpressionAst not in FindAll filter)
            #   checkPathConstraints: literal text '-Path:(1 > /tmp/x)' not a path
            #   RUNTIME: paren evaluates, redirection writes /tmp/x → arbitrary write
            element_types = cmd.get('elementTypes', [])
            if element_types:
                for i in range(1, len(element_types)):
                    t = element_types[i]
                    if t not in ['StringConstant', 'Parameter']:
                        return {
                            'behavior': 'passthrough',
                            'message': f'Command argument has unvalidatable type ({t}) — variable paths cannot be statically resolved',
                        }
                    
                    if t == 'Parameter':
                        # elementTypes[i] ↔ args[i-1] (elementTypes[0] is the command name).
                        arg = cmd['args'][i - 1] if i - 1 < len(cmd.get('args', [])) else ''
                        colon_idx = arg.find(':')
                        if colon_idx > 0 and any(c in arg[colon_idx + 1:] for c in ['$(@{[]']):
                            return {
                                'behavior': 'passthrough',
                                'message':
                                    'Colon-bound parameter contains an expression that cannot be statically validated',
                            }
            
            # Safe output cmdlets (Out-Null, etc.) and allowlisted pipeline-tail
            # transformers (Format-*, Measure-Object, Select-Object, etc.) don't
            # affect the semantics of the preceding command. Skip them so
            # `Remove-Item ./foo | Out-Null` or `Set-Content ./foo hi | Format-Table`
            # auto-allows the same as the bare write cmdlet. isAllowlistedPipelineTail
            # is the narrow fallback for cmdlets moved from SAFE_OUTPUT_CMDLETS to
            # CMDLET_ALLOWLIST (argLeaksValue validates their args).
            if (
                isSafeOutputCommand(cmd.get('name', '')) or
                isAllowlistedPipelineTail(cmd, input_data.get('command', ''))
            ):
                continue
            
            if not isAcceptEditsAllowedCmdlet(cmd.get('name', '')):
                return {
                    'behavior': 'passthrough',
                    'message': f"No mode-specific handling for '{cmd.get('name')}' in acceptEdits mode",
                }
            
            # SECURITY: Reject commands with unclassifiable argument types. 'Other'
            # covers HashtableAst, ConvertExpressionAst, BinaryExpressionAst — all
            # can contain nested redirections or code that the parser cannot fully
            # decompose. isAllowlistedCommand (readOnlyValidation.py) already
            # enforces this whitelist via argLeaksValue; this closes the same gap
            # in acceptEdits mode. Without this, @{k='payload' > ~/.bashrc} as a
            # -Value argument passes because HashtableAst maps to 'Other'.
            # argLeaksValue also catches colon-bound variables (-Flag:$env:SECRET).
            if argLeaksValue(cmd.get('name', ''), cmd):
                return {
                    'behavior': 'passthrough',
                    'message': f"Arguments in '{cmd.get('name')}' cannot be statically validated in acceptEdits mode",
                }

        # Also check nested commands from control flow statements
        nested_commands = getattr(segment, 'nestedCommands', None)
        if nested_commands:
            for cmd in nested_commands:
                if cmd.get('elementType') != 'CommandAst':
                    # SECURITY: Same as above — non-CommandAst element in nested commands
                    # (control flow bodies) cannot be statically validated as a path source.
                    return {
                        'behavior': 'passthrough',
                        'message': f"Nested expression element ({cmd.get('elementType')}) cannot be statically validated",
                    }
                
                if cmd.get('nameType') == 'application':
                    return {
                        'behavior': 'passthrough',
                        'message': f"Nested command '{cmd.get('name')}' resolved from a path-like name and requires approval",
                    }
                
                if (
                    isSafeOutputCommand(cmd.get('name', '')) or
                    isAllowlistedPipelineTail(cmd, input_data.get('command', ''))
                ):
                    continue
                
                if not isAcceptEditsAllowedCmdlet(cmd.get('name', '')):
                    return {
                        'behavior': 'passthrough',
                        'message': f"No mode-specific handling for '{cmd.get('name')}' in acceptEdits mode",
                    }
                
                # SECURITY: Same argLeaksValue check as the main command loop above.
                if argLeaksValue(cmd.get('name', ''), cmd):
                    return {
                        'behavior': 'passthrough',
                        'message': f"Arguments in nested '{cmd.get('name')}' cannot be statically validated in acceptEdits mode",
                    }

    # All commands are filesystem-modifying cmdlets -- auto-allow
    return {
        'behavior': 'allow',
        'updatedInput': input_data,
        'decisionReason': {
            'type': 'mode',
            'mode': 'acceptEdits',
        },
    }
