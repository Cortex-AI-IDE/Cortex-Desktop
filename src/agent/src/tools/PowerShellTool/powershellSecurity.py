# ------------------------------------------------------------
# powershellSecurity.py
# Python conversion of PowerShellTool/powershellSecurity.ts
# 
# PowerShell-specific security analysis for command validation.
# 
# Detects dangerous patterns: code injection, download cradles, privilege
# escalation, dynamic command names, COM objects, etc.
# 
# All checks are AST-based. If parsing failed (valid=false), none of the
# individual checks match and powershellCommandIsSafe returns 'ask'.
# ------------------------------------------------------------

import re
from typing import Any, Dict, List, Optional, Set

# Import dependencies
try:
    from ...utils.powershell.dangerousCmdlets import (
        DANGEROUS_SCRIPT_BLOCK_CMDLETS,
        FILEPATH_EXECUTION_CMDLETS,
        MODULE_LOADING_CMDLETS,
    )
except ImportError:
    DANGEROUS_SCRIPT_BLOCK_CMDLETS = set()
    FILEPATH_EXECUTION_CMDLETS = set()
    MODULE_LOADING_CMDLETS = set()

try:
    from ...utils.powershell.parser import (
        COMMON_ALIASES,
        commandHasArgAbbreviation,
        deriveSecurityFlags,
        getAllCommands,
        getVariablesByScope,
        hasCommandNamed,
    )
except ImportError:
    COMMON_ALIASES = {}
    
    def commandHasArgAbbreviation(cmd: dict, fullParam: str, minPrefix: str) -> bool:
        return False
    
    def deriveSecurityFlags(parsed: dict) -> dict:
        return {}
    
    def getAllCommands(parsed: dict) -> List[dict]:
        return []
    
    def getVariablesByScope(parsed: dict, scope: str) -> List[str]:
        return []
    
    def hasCommandNamed(parsed: dict, name: str) -> bool:
        return False

try:
    from .clmTypes import isClmAllowedType
except ImportError:
    def isClmAllowedType(type_name: str) -> bool:
        return True

# Type aliases
PowerShellSecurityResult = Dict[str, Any]

POWERSHELL_EXECUTABLES: Set[str] = {
    'pwsh', 'pwsh.exe', 'powershell', 'powershell.exe',
}

# Alternative parameter-prefix characters that PowerShell accepts as equivalent
# to ASCII hyphen-minus (U+002D). PowerShell's tokenizer accepts all four dash
# characters plus Windows PowerShell 5.1's `/` parameter delimiter.
PS_ALT_PARAM_PREFIXES: Set[str] = {
    '/',           # Windows PowerShell 5.1
    '\u2013',      # en-dash
    '\u2014',      # em-dash
    '\u2015',      # horizontal bar
}


def isPowerShellExecutable(name: str) -> bool:
    r"""
    Extracts the base executable name from a command, handling full paths
    like /usr/bin/pwsh, C:\Windows\...\powershell.exe, or .\pwsh.
    """
    lower = name.lower()
    if lower in POWERSHELL_EXECUTABLES:
        return True
    # Extract basename from paths (both / and \ separators)
    last_sep = max(lower.rfind('/'), lower.rfind('\\'))
    if last_sep >= 0:
        return lower[last_sep + 1:] in POWERSHELL_EXECUTABLES
    return False


def psExeHasParamAbbreviation(
    cmd: Dict[str, Any],
    fullParam: str,
    minPrefix: str,
) -> bool:
    """
    Wrapper around commandHasArgAbbreviation that also matches alternative
    parameter prefixes (`/`, en-dash, em-dash, horizontal-bar). PowerShell's
    tokenizer accepts these for both powershell.exe args AND cmdlet parameters.
    """
    if commandHasArgAbbreviation(cmd, fullParam, minPrefix):
        return True
    # Normalize alternative prefixes to `-` and re-check
    normalized = {
        **cmd,
        'args': [
            '-' + a[1:] if (len(a) > 0 and a[0] in PS_ALT_PARAM_PREFIXES) else a
            for a in cmd.get('args', [])
        ],
    }
    return commandHasArgAbbreviation(normalized, fullParam, minPrefix)


def checkInvokeExpression(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Checks if a PowerShell command uses Invoke-Expression or its alias (iex).
    These are equivalent to eval and can execute arbitrary code.
    """
    if hasCommandNamed(parsed, 'Invoke-Expression'):
        return {
            'behavior': 'ask',
            'message': 'Command uses Invoke-Expression which can execute arbitrary code',
        }
    return {'behavior': 'passthrough'}


def checkDynamicCommandName(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Checks for dynamic command invocation where the command name itself is an
    expression that cannot be statically resolved.
    
    PoCs:
      & ${function:Invoke-Expression} 'payload'  — VariableExpressionAst
      & ('iex','x')[0] 'payload'                 — IndexExpressionAst → 'Other'
      & ('i'+'ex') 'payload'                     — BinaryExpressionAst → 'Other'
    
    Legitimate command names are ALWAYS StringConstantExpressionAst ('StringConstant').
    Any other element type in name position is dynamic.
    """
    for cmd in getAllCommands(parsed):
        if cmd.get('elementType') != 'CommandAst':
            continue
        nameElementType = cmd.get('elementTypes', [None])[0]
        if nameElementType is not None and nameElementType != 'StringConstant':
            return {
                'behavior': 'ask',
                'message': 'Command name is a dynamic expression which cannot be statically validated',
            }
    return {'behavior': 'passthrough'}


def checkEncodedCommand(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Checks for encoded command parameters which obscure intent.
    These are commonly used in malware to bypass security tools.
    """
    for cmd in getAllCommands(parsed):
        if isPowerShellExecutable(cmd.get('name', '')):
            if psExeHasParamAbbreviation(cmd, '-encodedcommand', '-e'):
                return {
                    'behavior': 'ask',
                    'message': 'Command uses encoded parameters which obscure intent',
                }
    return {'behavior': 'passthrough'}


def checkPwshCommandOrFile(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Checks for PowerShell re-invocation (nested pwsh/powershell process).
    
    Any PowerShell executable in command position is flagged — not just
    -Command/-File. Bare `pwsh` receiving stdin (`Get-Content x | pwsh`) or
    a positional script path executes arbitrary code.
    """
    for cmd in getAllCommands(parsed):
        if isPowerShellExecutable(cmd.get('name', '')):
            return {
                'behavior': 'ask',
                'message': 'Command spawns a nested PowerShell process which cannot be validated',
            }
    return {'behavior': 'passthrough'}


# Download cradle detection
DOWNLOADER_NAMES: Set[str] = {
    'invoke-webrequest', 'iwr', 'invoke-restmethod', 'irm',
    'new-object', 'start-bitstransfer',  # MITRE T1197
}


def isDownloader(name: str) -> bool:
    return name.lower() in DOWNLOADER_NAMES


def isIex(name: str) -> bool:
    lower = name.lower()
    return lower in ('invoke-expression', 'iex')


def checkDownloadCradles(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Checks for download cradle patterns - common malware techniques
    that download and execute remote code.
    
    Per-statement: catches piped cradles (`IWR ... | IEX`).
    Cross-statement: catches split cradles (`$r = IWR ...; IEX $r.Content`).
    """
    # Per-statement: piped cradle (IWR ... | IEX)
    for statement in parsed.get('statements', []):
        cmds = statement.get('commands', [])
        if len(cmds) < 2:
            continue
        has_downloader = any(isDownloader(c.get('name', '')) for c in cmds)
        has_iex = any(isIex(c.get('name', '')) for c in cmds)
        if has_downloader and has_iex:
            return {
                'behavior': 'ask',
                'message': 'Command downloads and executes remote code',
            }

    # Cross-statement: split cradle
    all_cmds = getAllCommands(parsed)
    if any(isDownloader(c.get('name', '')) for c in all_cmds) and \
       any(isIex(c.get('name', '')) for c in all_cmds):
        return {
            'behavior': 'ask',
            'message': 'Command downloads and executes remote code',
        }

    return {'behavior': 'passthrough'}


def checkDownloadUtilities(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Checks for standalone download utilities — LOLBAS tools commonly used to
    fetch payloads.
    
    Start-BitsTransfer: always a file transfer (MITRE T1197).
    certutil -urlcache: classic LOLBAS download.
    bitsadmin /transfer: legacy BITS download.
    """
    for cmd in getAllCommands(parsed):
        lower = cmd.get('name', '').lower()
        
        # Start-BitsTransfer is purpose-built for file transfer
        if lower == 'start-bitstransfer':
            return {
                'behavior': 'ask',
                'message': 'Command downloads files via BITS transfer',
            }
        
        # certutil - only when -urlcache is present
        if lower in ('certutil', 'certutil.exe'):
            if any(a.lower() in ('-urlcache', '/urlcache') for a in cmd.get('args', [])):
                return {
                    'behavior': 'ask',
                    'message': 'Command uses certutil to download from a URL',
                }
        
        # bitsadmin /transfer
        if lower in ('bitsadmin', 'bitsadmin.exe'):
            if any(a.lower() == '/transfer' for a in cmd.get('args', [])):
                return {
                    'behavior': 'ask',
                    'message': 'Command downloads files via BITS transfer',
                }
    
    return {'behavior': 'passthrough'}


def checkAddType(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Checks for Add-Type usage which compiles and loads .NET code at runtime.
    This can be used to execute arbitrary compiled code.
    """
    if hasCommandNamed(parsed, 'Add-Type'):
        return {
            'behavior': 'ask',
            'message': 'Command compiles and loads .NET code',
        }
    return {'behavior': 'passthrough'}


def checkComObject(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Checks for New-Object -ComObject. COM objects like WScript.Shell,
    Shell.Application, MMC20.Application have their own execution capabilities.
    
    We can't enumerate all dangerous ProgIDs, so flag any -ComObject.
    """
    for cmd in getAllCommands(parsed):
        if cmd.get('name', '').lower() != 'new-object':
            continue
        
        # -ComObject min abbrev is -com
        if psExeHasParamAbbreviation(cmd, '-comobject', '-com'):
            return {
                'behavior': 'ask',
                'message': 'Command instantiates a COM object which may have execution capabilities',
            }
        
        # SECURITY: Extract -TypeName and check against CLM allowlist
        type_name = None
        for i in range(len(cmd.get('args', []))):
            a = cmd['args'][i]
            lower = a.lower()
            
            # Handle colon-bound form: -TypeName:Foo.Bar
            if lower.startswith('-t') and ':' in lower:
                colon_idx = a.index(':')
                param_part = lower[:colon_idx]
                if '-typename'.startswith(param_part):
                    type_name = a[colon_idx + 1:]
                    break
            
            # Space-separated form: -TypeName Foo.Bar
            if (lower.startswith('-t') and '-typename'.startswith(lower) and
                    len(cmd['args']) > i + 1):
                type_name = cmd['args'][i + 1]
                break
        
        # Positional-0 binds to -TypeName
        if type_name is None:
            VALUE_PARAMS = {'-argumentlist', '-comobject', '-property'}
            SWITCH_PARAMS = {'-strict'}
            for i in range(len(cmd.get('args', []))):
                a = cmd['args'][i]
                if a.startswith('-'):
                    lower = a.lower()
                    if lower.startswith('-t') and '-typename'.startswith(lower):
                        i += 1
                        continue
                    if ':' in lower:
                        continue
                    if lower in SWITCH_PARAMS:
                        continue
                    if lower in VALUE_PARAMS:
                        i += 1
                        continue
                    continue
                # First non-dash arg is positional TypeName
                type_name = a
                break
        
        if type_name is not None and not isClmAllowedType(type_name):
            return {
                'behavior': 'ask',
                'message': f"New-Object instantiates .NET type '{type_name}' outside the ConstrainedLanguage allowlist",
            }
    
    return {'behavior': 'passthrough'}


def checkDangerousFilePathExecution(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Checks for DANGEROUS_SCRIPT_BLOCK_CMDLETS invoked with -FilePath (or
    -LiteralPath). These run a script file — arbitrary code execution with no
    ScriptBlockAst in the tree.
    """
    for cmd in getAllCommands(parsed):
        lower = cmd.get('name', '').lower()
        resolved = COMMON_ALIASES.get(lower, '').lower() or lower
        if resolved not in FILEPATH_EXECUTION_CMDLETS:
            continue
        
        if (psExeHasParamAbbreviation(cmd, '-filepath', '-f') or
                psExeHasParamAbbreviation(cmd, '-literalpath', '-l')):
            return {
                'behavior': 'ask',
                'message': f"{cmd['name']} -FilePath executes an arbitrary script file",
            }
        
        # Positional binding: any non-dash StringConstant is a potential -FilePath
        for i in range(len(cmd.get('args', []))):
            arg_type = cmd.get('elementTypes', [None] * (i + 2))[i + 1]
            arg = cmd['args'][i] if i < len(cmd['args']) else None
            if arg_type == 'StringConstant' and arg and not arg.startswith('-'):
                return {
                    'behavior': 'ask',
                    'message': f"{cmd['name']} with positional string argument binds to -FilePath and executes a script file",
                }
    
    return {'behavior': 'passthrough'}


def checkForEachMemberName(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Checks for ForEach-Object -MemberName. Invokes a method by string name on
    every piped object — semantically equivalent to `| % { $_.Method() }` but
    without any ScriptBlockAst or InvokeMemberExpressionAst in the tree.
    
    PoC: `Get-Process | ForEach-Object -MemberName Kill` → kills all processes.
    """
    for cmd in getAllCommands(parsed):
        lower = cmd.get('name', '').lower()
        resolved = COMMON_ALIASES.get(lower, '').lower() or lower
        if resolved != 'foreach-object':
            continue
        
        # -m is unambiguous for -MemberName
        if psExeHasParamAbbreviation(cmd, '-membername', '-m'):
            return {
                'behavior': 'ask',
                'message': 'ForEach-Object -MemberName invokes methods by string name which cannot be validated',
            }
        
        # PS7+: positional string arg binds to -MemberName
        for i in range(len(cmd.get('args', []))):
            arg_type = cmd.get('elementTypes', [None] * (i + 2))[i + 1]
            arg = cmd['args'][i] if i < len(cmd['args']) else None
            if arg_type == 'StringConstant' and arg and not arg.startswith('-'):
                return {
                    'behavior': 'ask',
                    'message': 'ForEach-Object with positional string argument binds to -MemberName and invokes methods by name',
                }
    
    return {'behavior': 'passthrough'}


def checkStartProcess(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Checks for dangerous Start-Process patterns.
    
    Two vectors:
    1. `-Verb RunAs` — privilege escalation (UAC prompt).
    2. Launching a PowerShell executable — nested invocation.
    """
    for cmd in getAllCommands(parsed):
        lower = cmd.get('name', '').lower()
        if lower not in ('start-process', 'saps', 'start'):
            continue
        
        # Vector 1: -Verb RunAs (space syntax)
        if (psExeHasParamAbbreviation(cmd, '-Verb', '-v') and
                any(a.lower() == 'runas' for a in cmd.get('args', []))):
            return {
                'behavior': 'ask',
                'message': 'Command requests elevated privileges',
            }
        
        # Colon syntax — structural (children[])
        if cmd.get('children'):
            for i in range(len(cmd.get('args', []))):
                arg_clean = cmd['args'][i].replace('`', '')
                if not re.match(r'^[-\u2013\u2014\u2015\u2015/]v[a-z]*:', arg_clean, re.I):
                    continue
                kids = cmd['children'][i] if i < len(cmd['children']) else None
                if not kids:
                    continue
                for child in kids:
                    # Strip quotes, backticks, and whitespace from child text
                    child_text = child.get('text', '')
                    cleaned = child_text.replace("'", "").replace('"', "").replace('`', "").replace(' ', "").replace('\t', "").replace('\n', "").replace('\r', "").lower()
                    if cleaned == 'runas':
                        return {
                            'behavior': 'ask',
                            'message': 'Command requests elevated privileges',
                        }
        
        # Regex fallback
        if any(re.match(r"^[-\u2013\u2014\u2015/]v[a-z]*:['\" ]*runas['\" ]*$", 
                        a.replace('`', ''), re.I) for a in cmd.get('args', [])):
            return {
                'behavior': 'ask',
                'message': 'Command requests elevated privileges',
            }
        
        # Vector 2: Start-Process targeting a PowerShell executable
        for arg in cmd.get('args', []):
            stripped = arg.strip("'\"")
            if isPowerShellExecutable(stripped):
                return {
                    'behavior': 'ask',
                    'message': 'Start-Process launches a nested PowerShell process which cannot be validated',
                }
    
    return {'behavior': 'passthrough'}


# Safe script block cmdlets (filtering/output)
SAFE_SCRIPT_BLOCK_CMDLETS: Set[str] = {
    'where-object', 'sort-object', 'select-object', 'group-object',
    'format-table', 'format-list', 'format-wide', 'format-custom',
}


def checkScriptBlockInjection(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Checks for script block injection patterns where script blocks
    appear in suspicious contexts that could execute arbitrary code.
    
    Script blocks used with safe filtering/output cmdlets are allowed.
    """
    security = deriveSecurityFlags(parsed)
    if not security.get('hasScriptBlocks', False):
        return {'behavior': 'passthrough'}

    # Check for dangerous cmdlets with script blocks
    for cmd in getAllCommands(parsed):
        lower = cmd.get('name', '').lower()
        if lower in DANGEROUS_SCRIPT_BLOCK_CMDLETS:
            return {
                'behavior': 'ask',
                'message': 'Command contains script block with dangerous cmdlet that may execute arbitrary code',
            }

    # Check if all commands are safe
    all_commands_safe = all(
        cmd.get('name', '').lower() in SAFE_SCRIPT_BLOCK_CMDLETS or
        COMMON_ALIASES.get(cmd.get('name', '').lower(), '').lower() in SAFE_SCRIPT_BLOCK_CMDLETS
        for cmd in getAllCommands(parsed)
    )

    if all_commands_safe:
        return {'behavior': 'passthrough'}

    return {
        'behavior': 'ask',
        'message': 'Command contains script block that may execute arbitrary code',
    }


def checkSubExpressions(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """AST-only check: Detects subexpressions $() which can hide command execution."""
    if deriveSecurityFlags(parsed).get('hasSubExpressions', False):
        return {
            'behavior': 'ask',
            'message': 'Command contains subexpressions $()',
        }
    return {'behavior': 'passthrough'}


def checkExpandableStrings(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    AST-only check: Detects expandable strings (double-quoted) with embedded
    expressions like "$env:PATH" or "$(dangerous-command)".
    """
    if deriveSecurityFlags(parsed).get('hasExpandableStrings', False):
        return {
            'behavior': 'ask',
            'message': 'Command contains expandable strings with embedded expressions',
        }
    return {'behavior': 'passthrough'}


def checkSplatting(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """AST-only check: Detects splatting (@variable) which can obscure arguments."""
    if deriveSecurityFlags(parsed).get('hasSplatting', False):
        return {
            'behavior': 'ask',
            'message': 'Command uses splatting (@variable)',
        }
    return {'behavior': 'passthrough'}


def checkStopParsing(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """AST-only check: Detects stop-parsing token (--%) which prevents further parsing."""
    if deriveSecurityFlags(parsed).get('hasStopParsing', False):
        return {
            'behavior': 'ask',
            'message': 'Command uses stop-parsing token (--%)',
        }
    return {'behavior': 'passthrough'}


def checkMemberInvocations(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """AST-only check: Detects .NET method invocations which can access system APIs."""
    if deriveSecurityFlags(parsed).get('hasMemberInvocations', False):
        return {
            'behavior': 'ask',
            'message': 'Command invokes .NET methods',
        }
    return {'behavior': 'passthrough'}


def checkTypeLiterals(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    AST-only check: type literals outside Microsoft's ConstrainedLanguage
    allowlist. CLM blocks all .NET type access except ~90 primitives/attributes.
    """
    for t in parsed.get('typeLiterals', []):
        if not isClmAllowedType(t):
            return {
                'behavior': 'ask',
                'message': f'Command uses .NET type [{t}] outside the ConstrainedLanguage allowlist',
            }
    return {'behavior': 'passthrough'}


def checkInvokeItem(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Invoke-Item (alias ii) opens a file with its default handler (ShellExecute).
    On an .exe/.ps1/.bat/.cmd this is RCE. Always ask — there is no safe variant.
    """
    for cmd in getAllCommands(parsed):
        lower = cmd.get('name', '').lower()
        if lower in ('invoke-item', 'ii'):
            return {
                'behavior': 'ask',
                'message': 'Invoke-Item opens files with the default handler (ShellExecute). On executable files this runs arbitrary code.',
            }
    return {'behavior': 'passthrough'}


# Scheduled-task persistence primitives
SCHEDULED_TASK_CMDLETS: Set[str] = {
    'register-scheduledtask', 'new-scheduledtask',
    'new-scheduledtaskaction', 'set-scheduledtask',
}


def checkScheduledTask(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Scheduled-task persistence primitives. Register-ScheduledTask and
    schtasks.exe /create create persistence that survives the session.
    """
    for cmd in getAllCommands(parsed):
        lower = cmd.get('name', '').lower()
        if lower in SCHEDULED_TASK_CMDLETS:
            return {
                'behavior': 'ask',
                'message': f"{cmd['name']} creates or modifies a scheduled task (persistence primitive)",
            }
        if lower in ('schtasks', 'schtasks.exe'):
            if any(a.lower() in ('/create', '/change', '-create', '-change') 
                   for a in cmd.get('args', [])):
                return {
                    'behavior': 'ask',
                    'message': 'schtasks with create/change modifies scheduled tasks (persistence primitive)',
                }
    return {'behavior': 'passthrough'}


# Environment variable write cmdlets
ENV_WRITE_CMDLETS: Set[str] = {
    'set-item', 'si', 'new-item', 'ni', 'remove-item', 'ri',
    'del', 'rm', 'rd', 'rmdir', 'erase', 'clear-item', 'cli',
    'set-content', 'add-content', 'ac',
}


def checkEnvVarManipulation(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    AST-only check: Detects environment variable manipulation via
    Set-Item/New-Item on env: scope.
    """
    env_vars = getVariablesByScope(parsed, 'env')
    if not env_vars:
        return {'behavior': 'passthrough'}
    
    # Check if any command is a write cmdlet
    for cmd in getAllCommands(parsed):
        if cmd.get('name', '').lower() in ENV_WRITE_CMDLETS:
            return {
                'behavior': 'ask',
                'message': 'Command modifies environment variables',
            }
    
    # Also flag if there are assignments involving env vars
    if deriveSecurityFlags(parsed).get('hasAssignments', False) and env_vars:
        return {
            'behavior': 'ask',
            'message': 'Command modifies environment variables',
        }
    
    return {'behavior': 'passthrough'}


def checkModuleLoading(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Module-loading cmdlets execute a .psm1's top-level script body or download
    from arbitrary repositories. A wildcard allow rule would let an attacker-
    supplied .psm1 execute with the user's privileges.
    """
    for cmd in getAllCommands(parsed):
        lower = cmd.get('name', '').lower()
        if lower in MODULE_LOADING_CMDLETS:
            return {
                'behavior': 'ask',
                'message': 'Command loads, installs, or downloads a PowerShell module or script, which can execute arbitrary code',
            }
    return {'behavior': 'passthrough'}


# Runtime state manipulation cmdlets
RUNTIME_STATE_CMDLETS: Set[str] = {
    'set-alias', 'sal', 'new-alias', 'nal',
    'set-variable', 'sv', 'new-variable', 'nv',
}


def checkRuntimeStateManipulation(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Set-Alias/New-Alias can hijack future command resolution. Set-Variable can
    poison $PSDefaultParameterValues. Neither effect can be validated statically.
    """
    for cmd in getAllCommands(parsed):
        raw = cmd.get('name', '').lower()
        lower = raw[raw.rfind('\\') + 1:] if '\\' in raw else raw
        if lower in RUNTIME_STATE_CMDLETS:
            return {
                'behavior': 'ask',
                'message': 'Command creates or modifies an alias or variable that can affect future command resolution',
            }
    return {'behavior': 'passthrough'}


# WMI process spawn cmdlets
WMI_SPAWN_CMDLETS: Set[str] = {
    'invoke-wmimethod', 'iwmi', 'invoke-cimmethod',
}


def checkWmiProcessSpawn(parsed: Dict[str, Any]) -> PowerShellSecurityResult:
    """
    Invoke-WmiMethod / Invoke-CimMethod are Start-Process equivalents via WMI.
    They can spawn arbitrary processes, bypassing checkStartProcess entirely.
    """
    for cmd in getAllCommands(parsed):
        lower = cmd.get('name', '').lower()
        if lower in WMI_SPAWN_CMDLETS:
            return {
                'behavior': 'ask',
                'message': f"{cmd['name']} can spawn arbitrary processes via WMI/CIM (Win32_Process Create)",
            }
    return {'behavior': 'passthrough'}


def powershellCommandIsSafe(
    _command: str,
    parsed: Dict[str, Any],
) -> PowerShellSecurityResult:
    """
    Main entry point for PowerShell security validation.
    Checks a PowerShell command against known dangerous patterns.
    
    All checks are AST-based. If the AST parse failed (parsed.valid === false),
    none of the individual checks will match and we return 'ask' as a safe default.
    
    Args:
        _command: The PowerShell command to validate (unused, kept for API compat)
        parsed: Parsed AST from PowerShell's native parser (required)
    
    Returns:
        Security result indicating whether the command is safe
    """
    # If the AST parse failed, we cannot determine safety — ask the user
    if not parsed.get('valid', False):
        return {
            'behavior': 'ask',
            'message': 'Could not parse command for security analysis',
        }

    validators = [
        checkInvokeExpression,
        checkDynamicCommandName,
        checkEncodedCommand,
        checkPwshCommandOrFile,
        checkDownloadCradles,
        checkDownloadUtilities,
        checkAddType,
        checkComObject,
        checkDangerousFilePathExecution,
        checkInvokeItem,
        checkScheduledTask,
        checkForEachMemberName,
        checkStartProcess,
        checkScriptBlockInjection,
        checkSubExpressions,
        checkExpandableStrings,
        checkSplatting,
        checkStopParsing,
        checkMemberInvocations,
        checkTypeLiterals,
        checkEnvVarManipulation,
        checkModuleLoading,
        checkRuntimeStateManipulation,
        checkWmiProcessSpawn,
    ]

    for validator in validators:
        result = validator(parsed)
        if result.get('behavior') == 'ask':
            return result

    # All checks passed
    return {'behavior': 'passthrough'}
