"""
Command semantics configuration for interpreting exit codes in PowerShell.

PowerShell-native cmdlets do NOT need exit-code semantics:
  - Select-String (grep equivalent) exits 0 on no-match (returns $null)
  - Compare-Object (diff equivalent) exits 0 regardless
  - Test-Path exits 0 regardless (returns bool via pipeline)
Native cmdlets signal failure via terminating errors ($?), not exit codes.

However, EXTERNAL executables invoked from PowerShell DO set $LASTEXITCODE,
and many use non-zero codes to convey information rather than failure:
  - grep.exe / rg.exe (Git for Windows, scoop, etc.): 1 = no match
  - findstr.exe (Windows native): 1 = no match
  - robocopy.exe (Windows native): 0-7 = success, 8+ = error (notorious!)

Without this module, PowerShellTool throws ShellError on any non-zero exit,
so `robocopy` reporting "files copied successfully" (exit 1) shows as an error.
"""

from typing import Callable, Dict, Tuple


CommandSemantic = Callable[[int, str, str], Dict[str, any]]


def default_semantic(exit_code: int, stdout: str, stderr: str) -> Dict[str, any]:
    """Default semantic: treat only 0 as success, everything else as error."""
    return {
        'isError': exit_code != 0,
        'message': f'Command failed with exit code {exit_code}' if exit_code != 0 else None,
    }


def grep_semantic(exit_code: int, stdout: str, stderr: str) -> Dict[str, any]:
    """grep / ripgrep: 0 = matches found, 1 = no matches, 2+ = error."""
    return {
        'isError': exit_code >= 2,
        'message': 'No matches found' if exit_code == 1 else None,
    }


def robocopy_semantic(exit_code: int, stdout: str, stderr: str) -> Dict[str, any]:
    """
    robocopy.exe: Windows native robust file copy.
    Exit codes are a BITFIELD — 0-7 are success, 8+ indicates at least one failure:
      0 = no files copied, no mismatch, no failures (already in sync)
      1 = files copied successfully
      2 = extra files/dirs detected (no copy)
      4 = mismatched files/dirs detected
      8 = some files/dirs could not be copied (copy errors)
     16 = serious error (robocopy did not copy any files)
    This is the single most common "CI failed but nothing's wrong" Windows gotcha.
    """
    if exit_code >= 8:
        return {
            'isError': True,
            'message': f'Robocopy encountered errors (exit code {exit_code})',
        }
    
    # Success codes (0-7)
    messages = {
        0: 'No files copied, already in sync',
        1: 'Files copied successfully',
        2: 'Extra files/directories detected (no copy needed)',
        3: 'Files copied and extra files detected',
        4: 'Mismatched files/directories detected',
        5: 'Files copied and mismatches detected',
        6: 'Extra and mismatched files detected',
        7: 'Files copied, extra files, and mismatches detected',
    }
    
    return {
        'isError': False,
        'message': messages.get(exit_code, f'Robocopy completed (exit code {exit_code})'),
    }


# Command-specific semantics for external executables.
# Keys are lowercase command names WITHOUT .exe suffix.
#
# Deliberately omitted:
#   - 'diff': Ambiguous. Windows PowerShell 5.1 aliases `diff` → Compare-Object
#     (exit 0 on differ), but PS Core / Git for Windows may resolve to diff.exe
#     (exit 1 on differ). Cannot reliably interpret.
#   - 'fc': Ambiguous. PowerShell aliases `fc` → Format-Custom (a native cmdlet),
#     but `fc.exe` is the Windows file compare utility (exit 1 = files differ).
#     Same aliasing problem as `diff`.
#   - 'find': Ambiguous. Windows find.exe (text search) vs Unix find.exe
#     (file search via Git for Windows) have different semantics.
#   - 'test', '[': Not PowerShell constructs.
#   - 'select-string', 'compare-object', 'test-path': Native cmdlets exit 0.
COMMAND_SEMANTICS: Dict[str, CommandSemantic] = {
    # External grep/ripgrep (Git for Windows, scoop, choco)
    'grep': grep_semantic,
    'rg': grep_semantic,
    
    # findstr.exe: Windows native text search
    # 0 = match found, 1 = no match, 2 = error
    'findstr': grep_semantic,
    
    # robocopy.exe: Windows native robust file copy
    'robocopy': robocopy_semantic,
}


def get_command_semantic(command_name: str) -> CommandSemantic:
    """Get the semantic interpreter for a command, or default."""
    # Remove .exe suffix if present
    base_name = command_name.lower()
    if base_name.endswith('.exe'):
        base_name = base_name[:-4]
    
    return COMMAND_SEMANTICS.get(base_name, default_semantic)


def interpret_exit_code(
    command_name: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> Dict[str, any]:
    """Interpret exit code for a specific command."""
    semantic = get_command_semantic(command_name)
    return semantic(exit_code, stdout, stderr)
