"""
Cross-Project Memory Sharing System.

Enables global memories to be shared across all projects while maintaining
project-specific memory isolation. Automatically merges global preferences
with project context.

Features:
- Global memory directory (~/.cortex/global/memory)
- Smart merging with project memories
- Conflict resolution (project wins)
- Automatic sync on project load
- Memory type classification (global vs project-specific)
"""

import os
import json
import shutil
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from src.utils.logger import get_logger

log = get_logger("cross_project_memory")


@dataclass
class MemorySyncReport:
    """Report from memory synchronization."""
    global_memories_loaded: int
    project_memories_loaded: int
    conflicts_resolved: int
    merged_memory_path: Optional[str]
    sync_timestamp: float


@dataclass
class GlobalMemoryEntry:
    """A global memory entry."""
    file_path: str
    filename: str
    title: str
    description: str
    memory_type: str  # user_preferences, coding_style, tool_preferences, etc.
    content: str
    mtime: float
    projects_using: List[str] = field(default_factory=list)


class CrossProjectMemoryManager:
    """
    Manages cross-project memory sharing.
    
    Maintains a global memory directory that all projects can access,
    while keeping project-specific memories isolated.
    """
    
    def __init__(self, base_dir: str = None):
        """
        Initialize cross-project memory manager.
        
        Args:
            base_dir: Base directory for Cortex data (default: ~/.cortex)
        """
        if base_dir is None:
            base_dir = os.path.expanduser("~/.cortex")
        
        self.base_dir = base_dir
        self.global_memory_dir = os.path.join(base_dir, "global", "memory")
        self.projects_dir = os.path.join(base_dir, "projects")
        
        # Ensure directories exist
        os.makedirs(self.global_memory_dir, exist_ok=True)
        os.makedirs(self.projects_dir, exist_ok=True)
    
    def save_global_memory(self, filename: str, content: str, metadata: Dict = None) -> str:
        """
        Save a memory to the global memory directory.
        
        Args:
            filename: Memory file name (e.g., "user_preferences.md")
            content: Memory content in Markdown format
            metadata: Optional metadata dict
            
        Returns:
            Path to saved file
        """
        file_path = os.path.join(self.global_memory_dir, filename)
        
        # Build content with frontmatter
        if metadata:
            frontmatter = "---\n"
            for key, value in metadata.items():
                frontmatter += f"{key}: {value}\n"
            frontmatter += "---\n\n"
            content = frontmatter + content
        
        # Write file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        log.info(f"[CrossProject] Saved global memory: {filename}")
        return file_path
    
    def load_global_memories(self) -> List[GlobalMemoryEntry]:
        """
        Load all global memories.
        
        Returns:
            List of global memory entries
        """
        if not os.path.exists(self.global_memory_dir):
            return []
        
        memories = []
        
        for root, dirs, files in os.walk(self.global_memory_dir):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for filename in files:
                if not filename.endswith('.md') or filename == 'MEMORY.md':
                    continue
                
                file_path = os.path.join(root, filename)
                try:
                    entry = self._load_memory_entry(file_path)
                    if entry:
                        memories.append(entry)
                except Exception as e:
                    log.error(f"[CrossProject] Failed to load {filename}: {e}")
        
        log.info(f"[CrossProject] Loaded {len(memories)} global memories")
        return memories
    
    def _load_memory_entry(self, file_path: str) -> Optional[GlobalMemoryEntry]:
        """Load a single memory entry from file."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Parse frontmatter
            metadata = {}
            body = content
            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 2:
                    for line in parts[1].strip().split('\n'):
                        if ':' in line:
                            key, value = line.split(':', 1)
                            metadata[key.strip()] = value.strip()
                    body = parts[2].strip() if len(parts) > 2 else ''
            
            stat = os.stat(file_path)
            
            return GlobalMemoryEntry(
                file_path=file_path,
                filename=os.path.basename(file_path),
                title=metadata.get('title', os.path.basename(file_path)),
                description=metadata.get('description', ''),
                memory_type=metadata.get('type', 'general'),
                content=body,
                mtime=stat.st_mtime,
            )
            
        except Exception as e:
            log.error(f"[CrossProject] Failed to load entry {file_path}: {e}")
            return None
    
    def get_project_memory_dir(self, project_root: str) -> str:
        """
        Get memory directory for a specific project.
        
        Args:
            project_root: Project root directory
            
        Returns:
            Path to project memory directory
        """
        # Sanitize project path for directory name
        import hashlib
        import re
        
        sanitized = re.sub(r'[<>:"/\\|?*\0]', "_", project_root).strip("_ ")
        if len(sanitized) > 60:
            digest = hashlib.md5(project_root.encode("utf-8")).hexdigest()[:8]
            sanitized = sanitized[-52:].lstrip("_") + "_" + digest
        
        return os.path.join(self.projects_dir, sanitized, "memory")
    
    def sync_memories_to_project(self, project_root: str, auto_merge: bool = True) -> MemorySyncReport:
        """
        Sync global memories to a project's memory directory.
        
        This merges global preferences with project-specific memories,
        with project memories taking priority on conflicts.
        
        Args:
            project_root: Project root directory
            auto_merge: If True, automatically merge conflicts
            
        Returns:
            Sync report
        """
        project_memory_dir = self.get_project_memory_dir(project_root)
        os.makedirs(project_memory_dir, exist_ok=True)
        
        log.info(f"[CrossProject] Syncing global memories to project: {project_root}")
        
        # Load global memories
        global_memories = self.load_global_memories()
        
        # Load existing project memories
        project_memories = self._load_project_memories(project_memory_dir)
        
        # Find conflicts (memories with same filename)
        global_filenames = {m.filename for m in global_memories}
        project_filenames = {m['filename'] for m in project_memories}
        conflicts = global_filenames & project_filenames
        
        conflicts_resolved = 0
        
        # Copy global memories to project (skip conflicts if auto_merge)
        for global_mem in global_memories:
            dest_path = os.path.join(project_memory_dir, global_mem.filename)
            
            if global_mem.filename in conflicts:
                # Conflict: merge or skip
                if auto_merge:
                    self._merge_memory(global_mem, project_memories, dest_path)
                    conflicts_resolved += 1
                else:
                    log.debug(f"[CrossProject] Skipping conflict: {global_mem.filename}")
            else:
                # No conflict: copy global memory
                try:
                    shutil.copy2(global_mem.file_path, dest_path)
                    log.debug(f"[CrossProject] Copied global memory: {global_mem.filename}")
                except Exception as e:
                    log.error(f"[CrossProject] Failed to copy {global_mem.filename}: {e}")
        
        report = MemorySyncReport(
            global_memories_loaded=len(global_memories),
            project_memories_loaded=len(project_memories),
            conflicts_resolved=conflicts_resolved,
            merged_memory_path=project_memory_dir,
            sync_timestamp=time.time()
        )
        
        log.info(f"[CrossProject] Sync complete: {report}")
        return report
    
    def _load_project_memories(self, project_memory_dir: str) -> List[Dict]:
        """Load project-specific memories."""
        if not os.path.exists(project_memory_dir):
            return []
        
        memories = []
        
        for filename in os.listdir(project_memory_dir):
            if not filename.endswith('.md') or filename == 'MEMORY.md':
                continue
            
            file_path = os.path.join(project_memory_dir, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                stat = os.stat(file_path)
                memories.append({
                    'file_path': file_path,
                    'filename': filename,
                    'content': content,
                    'mtime': stat.st_mtime,
                })
            except Exception as e:
                log.error(f"[CrossProject] Failed to load project memory {filename}: {e}")
        
        return memories
    
    def _merge_memory(self, global_mem: GlobalMemoryEntry, project_memories: List[Dict], dest_path: str):
        """
        Merge global memory with project memory (project wins on conflict).
        
        Args:
            global_mem: Global memory entry
            project_memories: List of project memories
            dest_path: Destination file path
        """
        # Find project version
        project_mem = None
        for pm in project_memories:
            if pm['filename'] == global_mem.filename:
                project_mem = pm
                break
        
        if not project_mem:
            # No project version, use global
            try:
                if global_mem.file_path and os.path.exists(global_mem.file_path):
                    shutil.copy2(global_mem.file_path, dest_path)
                else:
                    with open(dest_path, 'w', encoding='utf-8') as f:
                        f.write(global_mem.content or '')
            except Exception as e:
                log.error(f"[CrossProject] Failed to write global memory {global_mem.filename}: {e}")
                with open(dest_path, 'w', encoding='utf-8') as f:
                    f.write(global_mem.content or '')
            return
        
        # Both exist: project wins, but preserve global insights
        try:
            # Extract unique sections from global
            global_sections = self._extract_sections(global_mem.content)
            project_sections = self._extract_sections(project_mem['content'])
            
            # Find sections in global that aren't in project
            unique_global_sections = []
            for g_section in global_sections:
                is_duplicate = False
                for p_section in project_sections:
                    # Simple similarity check
                    if self._sections_similar(g_section, p_section):
                        is_duplicate = True
                        break
                
                if not is_duplicate:
                    unique_global_sections.append(g_section)
            
            # Merge: project content + unique global sections
            merged_content = project_mem['content']
            if unique_global_sections:
                merged_content += "\n\n---\n\n### Additional Global Insights\n\n"
                merged_content += '\n\n'.join(unique_global_sections)
            
            # Write merged content
            with open(dest_path, 'w', encoding='utf-8') as f:
                f.write(merged_content)
            
            log.debug(f"[CrossProject] Merged {global_mem.filename} (project + {len(unique_global_sections)} global sections)")
            
        except Exception as e:
            log.error(f"[CrossProject] Failed to merge {global_mem.filename}: {e}")
            # Fallback: keep project version
            shutil.copy2(project_mem['file_path'], dest_path)
    
    def _extract_sections(self, content: str) -> List[str]:
        """Extract markdown sections from content."""
        sections = []
        current_section = []
        
        for line in content.split('\n'):
            if line.startswith('###') or line.startswith('##'):
                if current_section:
                    sections.append('\n'.join(current_section))
                current_section = [line]
            else:
                current_section.append(line)
        
        if current_section:
            sections.append('\n'.join(current_section))
        
        return sections
    
    def _sections_similar(self, section1: str, section2: str, threshold: float = 0.4) -> bool:
        """Check if two sections are similar."""
        # Simple keyword overlap
        words1 = set(section1.lower().split())
        words2 = set(section2.lower().split())
        
        if not words1 or not words2:
            return False
        
        intersection = words1 & words2
        union = words1 | words2
        
        similarity = len(intersection) / len(union) if union else 0.0
        return similarity >= threshold
    
    def share_project_memory(self, project_root: str, filename: str, promote_to_global: bool = True) -> Optional[str]:
        """
        Share a project memory to all projects (promote to global).
        
        Args:
            project_root: Source project root
            filename: Memory file to share
            promote_to_global: If True, copy to global directory
            
        Returns:
            Path to global memory file, or None
        """
        # Project memories may live in the project itself (standard) or in the
        # manager-managed shared-project directory. Check both.
        candidates = [
            os.path.join(project_root, '.cortex', 'memories'),
            os.path.join(project_root, '.cortex', 'memory'),
            self.get_project_memory_dir(project_root),
        ]

        source_path = None
        for d in candidates:
            try:
                p = os.path.join(d, filename)
                if os.path.exists(p):
                    source_path = p
                    break
            except Exception:
                continue

        if not source_path:
            log.error(f"[CrossProject] Project memory not found: {filename}")
            return None
        
        if promote_to_global:
            dest_path = os.path.join(self.global_memory_dir, filename)
            try:
                shutil.copy2(source_path, dest_path)
                log.info(f"[CrossProject] Promoted {filename} to global memory")
                return dest_path
            except Exception as e:
                log.error(f"[CrossProject] Failed to promote {filename}: {e}")
                return None
        
        return source_path
    
    def delete_global_memory(self, filename: str) -> bool:
        """Delete a global memory file."""
        file_path = os.path.join(self.global_memory_dir, filename)
        
        if not os.path.exists(file_path):
            return False
        
        try:
            os.remove(file_path)
            log.info(f"[CrossProject] Deleted global memory: {filename}")
            return True
        except Exception as e:
            log.error(f"[CrossProject] Failed to delete {filename}: {e}")
            return False
    
    def get_global_memory_stats(self) -> Dict:
        """Get statistics about global memories."""
        memories = self.load_global_memories()
        
        type_counts = {}
        total_size = 0
        
        for mem in memories:
            mem_type = mem.memory_type
            type_counts[mem_type] = type_counts.get(mem_type, 0) + 1
            
            try:
                total_size += os.path.getsize(mem.file_path)
            except:
                pass
        
        return {
            'total': len(memories),
            'type_counts': type_counts,
            'total_size_kb': round(total_size / 1024, 2),
        }
    
    def list_shared_projects(self) -> Dict[str, List[str]]:
        """
        List all projects and their shared global memories.
        
        Returns:
            Dict mapping project paths to list of global memory filenames
        """
        if not os.path.exists(self.projects_dir):
            return {}
        
        project_memories = {}
        
        for project_folder in os.listdir(self.projects_dir):
            project_path = os.path.join(self.projects_dir, project_folder)
            if not os.path.isdir(project_path):
                continue
            
            memory_dir = os.path.join(project_path, "memory")
            if not os.path.exists(memory_dir):
                continue
            
            # Check which global memories are synced
            global_filenames = {m.filename for m in self.load_global_memories()}
            synced_files = []
            
            for filename in os.listdir(memory_dir):
                if filename in global_filenames:
                    synced_files.append(filename)
            
            if synced_files:
                project_memories[project_folder] = synced_files
        
        return project_memories


# Global instance
_cross_project_manager: Optional[CrossProjectMemoryManager] = None


def get_cross_project_manager(base_dir: str = None) -> CrossProjectMemoryManager:
    """Get or create global cross-project memory manager."""
    global _cross_project_manager
    
    if _cross_project_manager is None:
        _cross_project_manager = CrossProjectMemoryManager(base_dir)
    
    return _cross_project_manager


def reset_cross_project_manager():
    """Reset global instance (for testing)."""
    global _cross_project_manager
    _cross_project_manager = None
