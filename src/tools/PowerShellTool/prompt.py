"""
PowerShellTool prompts and description.

Generates dynamic prompts based on PowerShell edition detection and environment settings.
"""

import os
from typing import Optional

# Defensive imports
try:
    from ...utils.envUtils import isEnvTruthy
except ImportError:
    def isEnvTruthy(value):
        if value is None:
            return False
        return str(value).lower() in ('true', '1', 'yes')

try:
    from ...utils.shell.outputLimits import getMaxOutputLength
except ImportError:
    def getMaxOutputLength():
        return 100_000  # Default max output length

try:
    from ...utils.shell.powershellDetection import getPowerShellEdition, PowerShellEdition
except ImportError:
    PowerShellEdition = str  # 'desktop' | 'core' | None
    
    async def getPowerShellEdition():
        return None  # Unknown edition

try:
    from ...utils.timeouts import getDefaultBashTimeoutMs, getMaxBashTimeoutMs
except ImportError:
    def getDefaultBashTimeoutMs():
        return 600_000  # 10 minutes default
    
    def getMaxBashTimeoutMs():
        return 3_600_000  # 60 minutes max

try:
    from ..FileEditTool.constants import FILE_EDIT_TOOL_NAME
except ImportError:
    FILE_EDIT_TOOL_NAME = 'FileEdit'

try:
    from ..FileReadTool.prompt import FILE_READ_TOOL_NAME
except ImportError:
    FILE_READ_TOOL_NAME = 'Read'

try:
    from ..FileWriteTool.prompt import FILE_WRITE_TOOL_NAME
except ImportError:
    FILE_WRITE_TOOL_NAME = 'Write'

try:
    from ..GlobTool.prompt import GLOB_TOOL_NAME
except ImportError:
    GLOB_TOOL_NAME = 'Glob'

try:
    from ..GrepTool.prompt import GREP_TOOL_NAME
except ImportError:
    GREP_TOOL_NAME = 'Grep'

try:
    from .toolName import POWERSHELL_TOOL_NAME
except ImportError:
    POWERSHELL_TOOL_NAME = 'PowerShell'


def getDefaultTimeoutMs() -> int:
    """Get default timeout for PowerShell commands."""
    return getDefaultBashTimeoutMs()


def getMaxTimeoutMs() -> int:
    """Get maximum allowed timeout for PowerShell commands."""
    return getMaxBashTimeoutMs()


def getBackgroundUsageNote() -> Optional[str]:
    """Get background task usage note if enabled."""
    if isEnvTruthy(os.environ.get('CORTEX_CODE_DISABLE_BACKGROUND_TASKS')):
        return None
    
    return '  - You can use the `run_in_background` parameter to run the command in the background. Only use this if you don\'t need the result immediately and are OK being notified when the command completes later. You do not need to check the output right away - you\'ll be notified when it finishes.'


def getSleepGuidance() -> Optional[str]:
    """Get sleep guidance if background tasks are enabled."""
    if isEnvTruthy(os.environ.get('CORTEX_CODE_DISABLE_BACKGROUND_TASKS')):
        return None
    
    return '''  - Avoid unnecessary `Start-Sleep` commands:
    - Do not sleep between commands that can run immediately — just run them.
    - If your command is long running and you would like to be notified when it finishes — simply run your command using `run_in_background`. There is no need to sleep in this case.
    - Do not retry failing commands in a sleep loop — diagnose the root cause or consider an alternative approach.
    - If waiting for a background task you started with `run_in_background`, you will be notified when it completes — do not poll.
    - If you must poll an external process, use a check command rather than sleeping first.
    - If you must sleep, keep the duration short (1-5 seconds) to avoid blocking the user.'''


def getEditionSection(edition: Optional[PowerShellEdition]) -> str:
    """
    Version-specific syntax guidance. The model's training data covers both
    editions but it can't tell which one it's targeting, so it either emits
    pwsh-7 syntax on 5.1 (parser error → exit 1) or needlessly avoids && on 7.
    """
    if edition == 'desktop':
        return '''PowerShell edition: Windows PowerShell 5.1 (powershell.exe)
   - Pipeline chain operators `&&` and `||` are NOT available — they cause a parser error. To run B only if A succeeds: `A; if ($?) { B }`. To chain unconditionally: `A; B`.
   - Ternary (`?:`), null-coalescing (`??`), and null-conditional (`?.`) operators are NOT available. Use `if/else` and explicit `$null -eq` checks instead.
   - Avoid `2>&1` on native executables. In 5.1, redirecting a native command's stderr inside PowerShell wraps each line in an ErrorRecord (NativeCommandError) and sets `$?` to `$false` even when the exe returned exit code 0. stderr is already captured for you — don't redirect it.
   - Default file encoding is UTF-16 LE (with BOM). When writing files other tools will read, pass `-Encoding utf8` to `Out-File`/`Set-Content`.
   - `ConvertFrom-Json` returns a PSCustomObject, not a hashtable. `-AsHashtable` is not available.'''
    
    if edition == 'core':
        return '''PowerShell edition: PowerShell 7+ (pwsh)
   - Pipeline chain operators `&&` and `||` ARE available and work like bash. Prefer `cmd1 && cmd2` over `cmd1; cmd2` when cmd2 should only run if cmd1 succeeds.
   - Ternary (`$cond ? $a : $b`), null-coalescing (`??`), and null-conditional (`?.`) operators are available.
   - Default file encoding is UTF-8 without BOM.'''
    
    # Detection not yet resolved (first prompt build before any tool call) or
    # PS not installed. Give the conservative 5.1-safe guidance.
    return '''PowerShell edition: unknown — assume Windows PowerShell 5.1 for compatibility
   - Do NOT use `&&`, `||`, ternary `?:`, null-coalescing `??`, or null-conditional `?.`. These are PowerShell 7+ only and parser-error on 5.1.
   - To chain commands conditionally: `A; if ($?) { B }`. Unconditionally: `A; B`.'''


async def getPrompt() -> str:
    """Generate the PowerShellTool prompt dynamically."""
    background_note = getBackgroundUsageNote()
    sleep_guidance = getSleepGuidance()
    edition = await getPowerShellEdition()
    
    return f'''Executes a given PowerShell command with optional timeout. Working directory persists between commands; shell state (variables, functions) does not.

IMPORTANT: This tool is for terminal operations via PowerShell: git, npm, docker, and PS cmdlets. DO NOT use it for file operations (reading, writing, editing, searching, finding files) - use the specialized tools for this instead.

{getEditionSection(edition)}

Before executing the command, please follow these steps:

1. Directory Verification:
   - If the command will create new directories or files, first use `Get-ChildItem` (or `ls`) to verify the parent directory exists and is the correct location

2. Command Execution:
   - Always quote file paths that contain spaces with double quotes
   - Capture the output of the command.

PowerShell Syntax Notes:
   - Variables use $ prefix: $myVar = "value"
   - Escape character is backtick (`), not backslash
   - Use Verb-Noun cmdlet naming: Get-ChildItem, Set-Location, New-Item, Remove-Item
   - Common aliases: ls (Get-ChildItem), cd (Set-Location), cat (Get-Content), rm (Remove-Item)
   - Pipe operator | works similarly to bash but passes objects, not text
   - Use Select-Object, Where-Object, ForEach-Object for filtering and transformation
   - String interpolation: "Hello $name" or "Hello $($obj.Property)"
   - Registry access uses PSDrive prefixes: `HKLM:\\SOFTWARE\\...`, `HKCU:\\...` — NOT raw `HKEY_LOCAL_MACHINE\\...`
   - Environment variables: read with `$env:NAME`, set with `$env:NAME = "value"` (NOT `Set-Variable` or bash `export`)
   - Call native exe with spaces in path via call operator: `& "C:\\Program Files\\App\\app.exe" arg1 arg2`

Interactive and blocking commands (will hang — this tool runs with -NonInteractive):
   - NEVER use `Read-Host`, `Get-Credential`, `Out-GridView`, `$Host.UI.PromptForChoice`, or `pause`
   - Destructive cmdlets (`Remove-Item`, `Stop-Process`, `Clear-Content`, etc.) may prompt for confirmation. Add `-Confirm:$false` when you intend the action to proceed. Use `-Force` for read-only/hidden items.
   - Never use `git rebase -i`, `git add -i`, or other commands that open an interactive editor

Passing multiline strings (commit messages, file content) to native executables:
   - Use a single-quoted here-string so PowerShell does not expand `$` or backticks inside. The closing `'@` MUST be at column 0 (no leading whitespace) on its own line — indenting it is a parse error:
<example>
git commit -m @'
Commit message here.
Second line with $literal dollar signs.
'@
</example>
   - Use `@'...'@` (single-quoted, literal) not `@"..."@` (double-quoted, interpolated) unless you need variable expansion
   - For arguments containing `-`, `@`, or other characters PowerShell parses as operators, use the stop-parsing token: `git log --% --format=%H`

Usage notes:
  - The command argument is required.
  - You can specify an optional timeout in milliseconds (up to {getMaxTimeoutMs()}ms / {getMaxTimeoutMs() // 60000} minutes). If not specified, commands will timeout after {getDefaultTimeoutMs()}ms ({getDefaultTimeoutMs() // 60000} minutes).
  - It is very helpful if you write a clear, concise description of what this command does.
  - If the output exceeds {getMaxOutputLength()} characters, output will be truncated before being returned to you.
{background_note + chr(10) if background_note else ''}\
  - Avoid using PowerShell to run commands that have dedicated tools, unless explicitly instructed:
    - File search: Use {GLOB_TOOL_NAME} (NOT Get-ChildItem -Recurse)
    - Content search: Use {GREP_TOOL_NAME} (NOT Select-String)
    - Read files: Use {FILE_READ_TOOL_NAME} (NOT Get-Content)
    - Edit files: Use {FILE_EDIT_TOOL_NAME}
    - Write files: Use {FILE_WRITE_TOOL_NAME} (NOT Set-Content/Out-File)
    - Communication: Output text directly (NOT Write-Output/Write-Host)
  - When issuing multiple commands:
    - If the commands are independent and can run in parallel, make multiple {POWERSHELL_TOOL_NAME} tool calls in a single message.
    - If the commands depend on each other and must run sequentially, chain them in a single {POWERSHELL_TOOL_NAME} call (see edition-specific chaining syntax above).
    - Use `;` only when you need to run commands sequentially but don't care if earlier commands fail.
    - DO NOT use newlines to separate commands (newlines are ok in quoted strings and here-strings)
  - Do NOT prefix commands with `cd` or `Set-Location` -- the working directory is already set to the correct project directory automatically.
{sleep_guidance + chr(10) if sleep_guidance else ''}\
  - For git commands:
    - Prefer to create a new commit rather than amending an existing commit.
    - Before running destructive operations (e.g., git reset --hard, git push --force, git checkout --), consider whether there is a safer alternative that achieves the same goal. Only use destructive operations when they are truly the best approach.
    - NEVER run git pull when there are uncommitted local changes — it can overwrite local code. Always commit or stash first. Prefer git stash -> git pull -> git stash pop.
    - NEVER run git pull --force, git pull --rebase, or git pull -f — these destroy local commits.
    - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c commit.gpgsign=false) unless the user has explicitly asked for it. If a hook fails, investigate and fix the underlying issue.'''
