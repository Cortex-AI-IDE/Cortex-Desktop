"""
Dangerous command patterns for Cortex AI IDE.

Defines patterns that could bypass safety checks if auto-allowed
in permission rules. These patterns are checked before enabling
auto mode to prevent security bypasses.

Multi-LLM Support: Works with all providers as it's provider-agnostic
security pattern definitions.

Categories:
1. Cross-platform code execution (bash & powershell)
2. Bash-specific dangerous patterns (interpreters, nested shells)
3. PowerShell-specific dangerous patterns (cmdlets, .NET escape hatches)

Example:
    >>> from dangerousPatterns import DANGEROUS_BASH_PATTERNS
    >>> 'python' in DANGEROUS_BASH_PATTERNS
    True
"""

# ============================================================================
# Cross-Platform Code Execution Patterns
# ============================================================================

# Patterns that can execute arbitrary code on any platform
# Used by both Bash and PowerShell dangerous permission checks
CROSS_PLATFORM_CODE_EXEC: tuple[str, ...] = (
    'python',
    'python3',
    'node',
    'ruby',
    'perl',
    'php',
    'bash',
    'sh',
    'zsh',
    'fish',
    'lua',
    'julia',
    'R',
    'java',
    'javaw',
    'dotnet',
    'mono',
    'script',
    'cscript',
    'wscript',
)


# ============================================================================
# Bash-Specific Dangerous Patterns
# ============================================================================

# Patterns that are dangerous when allowed in Bash permission rules
# These would bypass the auto mode classifier's safety evaluation
DANGEROUS_BASH_PATTERNS: tuple[str, ...] = (
    # Script interpreters
    'python',
    'python3',
    'ipython',
    'node',
    'nodejs',
    'ruby',
    'irb',
    'perl',
    'php',
    'lua',
    'julia',
    'R',
    'Rscript',
    # Shell interpreters
    'bash',
    'sh',
    'zsh',
    'fish',
    'dash',
    'ksh',
    'csh',
    'tcsh',
    # Compiled languages (runners)
    'java',
    'javaw',
    'dotnet',
    'mono',
    # Script runners
    'npm',
    'npx',
    'yarn',
    'pnpm',
    'pip',
    'pip3',
    'gem',
    'cargo',
    'go',
    'rustc',
    # System command execution
    'eval',
    'exec',
    'source',
    '.',  # dot command (source in bash)
    # Remote code execution
    'curl',
    'wget',
    'fetch',
)


# ============================================================================
# PowerShell-Specific Dangerous Patterns
# ============================================================================

# Patterns that are dangerous when allowed in PowerShell permission rules
# PowerShell is case-insensitive, so these should be lowercased before matching
DANGEROUS_POWERSHELL_PATTERNS: tuple[str, ...] = (
    # Cross-platform patterns (lowercased)
    'python',
    'python3',
    'node',
    'ruby',
    'perl',
    'php',
    'bash',
    # Nested PowerShell + shells launchable from PS
    'pwsh',
    'powershell',
    'cmd',
    'wsl',
    # String/scriptblock evaluators
    'iex',
    'invoke-expression',
    'icm',
    'invoke-command',
    # Process spawners
    'start-process',
    'saps',
    'start',
    'start-job',
    'sajb',
    'start-threadjob',
    # Event/session code execution
    'register-objectevent',
    'register-engineevent',
    'register-wmievent',
    'register-scheduledjob',
    'new-pssession',
    'nsn',
    'enter-pssession',
    'etsn',
    # .NET escape hatches
    'add-type',
    'new-object',
    # PowerShell aliases for dangerous commands
    'ii',  # Invoke-Item
    'sas',  # Start-Process alias
    'saps',  # Start-Process alias
    'sal',  # Set-Alias (can create dangerous aliases)
)


# ============================================================================
# Agent/Task Dangerous Patterns
# ============================================================================

# Any allow rule for Agent tool is dangerous because it would
# auto-approve sub-agent spawns before classifier can evaluate
# the sub-agent's prompt, defeating delegation attack prevention
DANGEROUS_AGENT_TOOLS: tuple[str, ...] = (
    'Agent',
    'Task',
    'TaskCreate',
    'SubAgent',
)


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
    'CROSS_PLATFORM_CODE_EXEC',
    'DANGEROUS_BASH_PATTERNS',
    'DANGEROUS_POWERSHELL_PATTERNS',
    'DANGEROUS_AGENT_TOOLS',
]
