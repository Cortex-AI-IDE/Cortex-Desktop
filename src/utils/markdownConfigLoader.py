"""
Markdown config loader utilities.

Loads and parses markdown configuration files (CORTEX.md, skills, commands)
from various directories (managed, user, project).
"""

import os
from typing import Any, Dict, List, Optional, TypedDict


# Type definitions
class MarkdownFile(TypedDict):
    """Represents a loaded markdown file with metadata."""
    filePath: str
    baseDir: str
    frontmatter: Dict[str, Any]
    content: str
    source: str  # SettingSource


def extractDescriptionFromMarkdown(
    content: str,
    defaultDescription: str = 'Custom item',
) -> str:
    """
    Extracts a description from markdown content.
    Uses the first non-empty line as the description, or falls back to a default.
    
    Args:
        content: Markdown content
        defaultDescription: Fallback description
        
    Returns:
        Extracted description string
    """
    lines = content.split('\n')
    for line in lines:
        trimmed = line.strip()
        if trimmed:
            # If it's a header, strip the header prefix
            import re
            header_match = re.match(r'^#+\s+(.+)$', trimmed)
            text = header_match.group(1) if header_match else trimmed
            return text
    return defaultDescription


def parseSlashCommandToolsFromFrontmatter(toolsValue: Any) -> List[str]:
    """
    Parse tool list from frontmatter 'allowed-tools' field.
    
    Accepts comma-separated string or array of strings.
    
    Args:
        toolsValue: Tools specification from frontmatter
        
    Returns:
        List of tool names
    """
    if toolsValue is None:
        return []
    if isinstance(toolsValue, list):
        return [str(t).strip() for t in toolsValue if str(t).strip()]
    if isinstance(toolsValue, str):
        return [t.strip() for t in toolsValue.split(',') if t.strip()]
    return []


def getProjectDirsUpToHome(subdir: str, cwd: str) -> List[str]:
    """
    Get project directories from cwd up to home directory.
    
    Walks up from current directory to git root or home,
    collecting directories that contain the specified subdir.
    
    Args:
        subdir: Subdirectory name (e.g., 'skills', 'commands')
        cwd: Current working directory
        
    Returns:
        List of directory paths containing the subdir
    """
    home = os.path.expanduser('~')
    current = os.path.abspath(cwd)
    dirs = []
    
    # Traverse from current directory up to home
    while True:
        target_dir = os.path.join(current, '.cortex', subdir)
        if os.path.isdir(target_dir):
            dirs.append(target_dir)
        
        # Move to parent
        parent = os.path.dirname(current)
        if parent == current:
            break  # Reached root
        if current == home:
            break  # Reached home
        current = parent
    
    return dirs


async def loadMarkdownFilesForSubdir(
    subdir: str,
    cwd: str,
) -> List[MarkdownFile]:
    """
    Load markdown files from a subdirectory across all config locations.
    
    Searches:
    - User config (~/.cortex/<subdir>)
    - Managed config (/etc/cortex/.cortex/<subdir>)
    - Project config (.cortex/<subdir> in cwd and parents)
    
    Args:
        subdir: Subdirectory name (e.g., 'skills', 'commands')
        cwd: Current working directory
        
    Returns:
        List of MarkdownFile objects
    """
    from .frontmatterParser import parseFrontmatter
    
    files = []
    
    # Collect all directories to search
    search_dirs = []
    
    # User directory
    user_dir = os.path.join(os.path.expanduser('~'), '.cortex', subdir)
    if os.path.isdir(user_dir):
        search_dirs.append((user_dir, 'userSettings'))
    
    # Project directories
    project_dirs = getProjectDirsUpToHome(subdir, cwd)
    for proj_dir in project_dirs:
        search_dirs.append((proj_dir, 'projectSettings'))
    
    # Load markdown files from each directory
    for base_dir, source in search_dirs:
        try:
            for entry in os.listdir(base_dir):
                file_path = os.path.join(base_dir, entry)
                if os.path.isfile(file_path) and entry.endswith('.md'):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    parsed = parseFrontmatter(content, file_path)
                    frontmatter = parsed.get('frontmatter', {})
                    
                    files.append(MarkdownFile(
                        filePath=file_path,
                        baseDir=base_dir,
                        frontmatter=frontmatter,
                        content=content,
                        source=source,
                    ))
        except (OSError, PermissionError):
            # Skip directories we can't read
            continue
    
    return files
