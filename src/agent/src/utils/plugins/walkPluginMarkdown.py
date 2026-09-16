"""
Walk plugin markdown files utility.

Traverses plugin directories to find and process markdown files (.md),
handling namespace detection based on directory structure.
"""

from typing import Callable, Awaitable, Optional, List
import os
import logging

logger = logging.getLogger(__name__)


async def walk_plugin_markdown(
    directory: str,
    callback: Callable[[str, List[str]], Awaitable[None]],
    options: Optional[dict] = None,
) -> None:
    """
    Walk a plugin directory tree and process all markdown files.
    
    For each .md file found, calls the callback with:
    - fullPath: Absolute path to the markdown file
    - namespace: List of directory names from base to file (excluding base and filename)
    
    Args:
        directory: Root directory to walk
        callback: Async function to call for each .md file (fullPath, namespace)
        options: Optional settings
            - logLabel: Label for debug logging (e.g., 'agents', 'commands')
    """
    if options is None:
        options = {}
    
    log_label = options.get('logLabel', 'markdown')
    
    if not os.path.exists(directory):
        logger.debug(f'{log_label} directory not found: {directory}')
        return
    
    if not os.path.isdir(directory):
        logger.warning(f'{log_label} path is not a directory: {directory}')
        return
    
    count = 0
    
    for root, dirs, files in os.walk(directory):
        # Sort for consistent ordering
        dirs.sort()
        files.sort()
        
        for filename in files:
            if not filename.endswith('.md'):
                continue
            
            full_path = os.path.join(root, filename)
            
            # Calculate namespace (relative path from base directory)
            rel_path = os.path.relpath(root, directory)
            if rel_path == '.':
                namespace = []
            else:
                # Split path into components
                namespace = rel_path.split(os.sep)
            
            # Call the callback
            try:
                await callback(full_path, namespace)
                count += 1
            except Exception as e:
                logger.error(f'Error processing {log_label} file {full_path}: {e}')
    
    if count > 0:
        logger.debug(f'Processed {count} {log_label} files in {directory}')


def find_markdown_files(directory: str) -> List[str]:
    """
    Find all markdown files in a directory (synchronous).
    
    Args:
        directory: Directory to search
    
    Returns:
        List of markdown file paths
    """
    if not os.path.exists(directory):
        return []
    
    markdown_files = []
    
    for root, dirs, files in os.walk(directory):
        dirs.sort()
        files.sort()
        
        for filename in files:
            if filename.endswith('.md'):
                full_path = os.path.join(root, filename)
                markdown_files.append(full_path)
    
    return markdown_files


def get_namespace_for_file(file_path: str, base_directory: str) -> List[str]:
    """
    Get the namespace for a markdown file based on its directory structure.
    
    Args:
        file_path: Path to the markdown file
        base_directory: Base directory to calculate namespace from
    
    Returns:
        List of directory components forming the namespace
    """
    dir_path = os.path.dirname(file_path)
    rel_path = os.path.relpath(dir_path, base_directory)
    
    if rel_path == '.':
        return []
    
    return rel_path.split(os.sep)
