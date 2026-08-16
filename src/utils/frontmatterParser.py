"""
frontmatterParser - YAML frontmatter extraction from markdown files.

Used by memoryScan.py to read memory file headers (name, description, type).
Simple regex-based parser — no external dependencies required.
"""

import re
from typing import Any, Dict, List, Optional, Union


# Type definitions
FrontmatterData = Dict[str, Any]
FrontmatterShell = Any  # Simplified - full implementation would be TypedDict


def parseFrontmatter(content: str, file_path: str = '') -> Dict[str, Any]:
    """
    Parse YAML frontmatter from the top of a markdown file.

    Expects content to start with --- (opening fence), followed by key: value
    lines, then a closing --- line.

    Returns:
        {'frontmatter': {key: value, ...}}
        Returns {'frontmatter': {}} if no valid frontmatter is found.
    """
    if not content or not content.startswith('---'):
        return {'frontmatter': {}}

    # Find the closing ---
    end_match = re.search(r'\n---', content[3:])
    if not end_match:
        return {'frontmatter': {}}

    fm_text = content[3 : 3 + end_match.start()].strip()
    frontmatter: Dict[str, Any] = {}

    for line in fm_text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' in line:
            key, _, raw_val = line.partition(':')
            key = key.strip()
            val = raw_val.strip()
            # Strip surrounding quotes
            if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
                val = val[1:-1]
            # Coerce simple booleans / numbers
            if val.lower() == 'true':
                frontmatter[key] = True
            elif val.lower() == 'false':
                frontmatter[key] = False
            else:
                try:
                    frontmatter[key] = int(val)
                except ValueError:
                    try:
                        frontmatter[key] = float(val)
                    except ValueError:
                        frontmatter[key] = val

    return {'frontmatter': frontmatter}


def coerceDescriptionToString(desc: Any, name: str) -> Optional[str]:
    """
    Coerce description field to string, validating it's not empty.
    
    Args:
        desc: Description value from frontmatter
        name: Skill/command name for error messages
        
    Returns:
        Validated description string or None
    """
    if desc is None:
        return None
    if isinstance(desc, str):
        return desc.strip() if desc.strip() else None
    return str(desc)


def parseBooleanFrontmatter(val: Any) -> bool:
    """
    Parse a boolean from frontmatter value.
    Handles strings like 'true', 'false', 'yes', 'no', and actual booleans.
    
    Args:
        val: Value to parse
        
    Returns:
        Boolean value
    """
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ('true', 'yes', '1', 'on')
    return bool(val)


def parseShellFrontmatter(shell: Any, name: str) -> Optional[FrontmatterShell]:
    """
    Parse shell specification from frontmatter.
    
    Args:
        shell: Shell value ('bash', 'powershell', etc.)
        name: Skill name for validation
        
    Returns:
        Shell configuration or None
    """
    if shell is None:
        return None
    if isinstance(shell, str):
        shell = shell.lower()
        if shell in ('bash', 'powershell', 'sh', 'cmd'):
            return shell
    return 'bash'  # Default


def splitPathInFrontmatter(paths: Any) -> List[str]:
    """
    Parse paths from frontmatter (comma-separated string or list).
    
    Args:
        paths: Path specification (string or list)
        
    Returns:
        List of path patterns
    """
    if paths is None:
        return []
    if isinstance(paths, list):
        return [str(p).strip() for p in paths if str(p).strip()]
    if isinstance(paths, str):
        return [p.strip() for p in paths.split(',') if p.strip()]
    return []
