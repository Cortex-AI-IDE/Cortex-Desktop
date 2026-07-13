"""
memoryScan - Memory directory scanning primitives.

Scans memory directories for .md files, extracts frontmatter headers,
and formats manifests for LLM consumption. Split out to avoid circular
dependencies with findRelevantMemories and API client chains.
"""

import os
from typing import List, Optional, TypedDict

# Defensive imports
try:
    from ..utils.frontmatterParser import parseFrontmatter
except ImportError:
    def parseFrontmatter(content, file_path):
        """Mock frontmatter parser."""
        return {'frontmatter': {}}

try:
    from ..utils.readFileInRange import readFileInRange
except ImportError:
    async def readFileInRange(file_path, start_line, end_line, encoding=None, signal=None):
        """Mock file reader."""
        try:
            with open(file_path, 'r', encoding=encoding or 'utf-8') as f:
                lines = f.readlines()
                content = ''.join(lines[start_line:end_line])
            
            mtime_ms = os.path.getmtime(file_path) * 1000
            
            return type('Result', (), {
                'content': content,
                'mtimeMs': mtime_ms,
            })()
        except Exception:
            return type('Result', (), {
                'content': '',
                'mtimeMs': 0,
            })()

try:
    from .memoryTypes import MemoryType, parseMemoryType
except ImportError:
    MemoryType = str
    
    def parseMemoryType(raw):
        """Parse memory type from string."""
        valid_types = ['user', 'feedback', 'project', 'reference']
        if isinstance(raw, str) and raw in valid_types:
            return raw
        return None


class MemoryHeader(TypedDict, total=False):
    """Memory file header information."""
    filename: str
    filePath: str
    mtimeMs: float
    description: Optional[str]
    type: Optional[MemoryType]


MAX_MEMORY_FILES = 200
FRONTMATTER_MAX_LINES = 30


async def scanMemoryFiles(memory_dir: str, signal=None) -> List[MemoryHeader]:
    """
    Scan a memory directory for .md files, read their frontmatter, and return
    a header list sorted newest-first (capped at MAX_MEMORY_FILES). Shared by
    findRelevantMemories (query-time recall) and extractMemories (pre-injects
    the listing so the extraction agent doesn't spend a turn on `ls`).
    
    Single-pass: readFileInRange stats internally and returns mtimeMs, so we
    read-then-sort rather than stat-sort-read. For the common case (N ≤ 200)
    this halves syscalls vs a separate stat round; for large N we read a few
    extra small files but still avoid the double-stat on the surviving 200.
    """
    try:
        # Walk directory recursively to find all .md files
        md_files = []
        for root, dirs, files in os.walk(memory_dir):
            for file in files:
                if file.endswith('.md') and file != 'MEMORY.md':
                    relative_path = os.path.relpath(os.path.join(root, file), memory_dir)
                    md_files.append(relative_path)
        
        # Process all files concurrently
        import asyncio
        
        async def process_file(relative_path):
            file_path = os.path.join(memory_dir, relative_path)
            
            result = await readFileInRange(
                file_path,
                0,
                FRONTMATTER_MAX_LINES,
                None,
                signal,
            )
            
            parsed = parseFrontmatter(result.content, file_path)
            frontmatter = parsed.get('frontmatter', {})
            
            return {
                'filename': relative_path,
                'filePath': file_path,
                'mtimeMs': result.mtimeMs,
                'description': frontmatter.get('description') or None,
                'type': parseMemoryType(frontmatter.get('type')),
            }
        
        # Process all files concurrently
        tasks = [process_file(path) for path in md_files]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter successful results
        headers = [r for r in results if not isinstance(r, Exception)]
        
        # Sort by modification time (newest first) and cap
        headers.sort(key=lambda h: h['mtimeMs'], reverse=True)
        
        return headers[:MAX_MEMORY_FILES]
    
    except Exception:
        return []


def formatMemoryManifest(memories: List[MemoryHeader]) -> str:
    """
    Format memory headers as a text manifest: one line per file with
    [type] filename (timestamp): description. Used by both the recall
    selector prompt and the extraction-agent prompt.
    """
    lines = []
    
    for m in memories:
        tag = f"[{m['type']}] " if m.get('type') else ''
        ts = __import__('datetime').datetime.fromtimestamp(m['mtimeMs'] / 1000).isoformat()
        
        if m.get('description'):
            lines.append(f"- {tag}{m['filename']} ({ts}): {m['description']}")
        else:
            lines.append(f"- {tag}{m['filename']} ({ts})")
    
    return '\n'.join(lines)
