"""
Filesystem operations abstraction layer.
Converted from fsOperations.ts - provides async/sync filesystem operations with
symlink resolution, permission checking, and mock support.
"""

from __future__ import annotations

import os
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


@dataclass
class ResolvedPath:
    """Result of safe path resolution."""
    resolvedPath: str
    isSymlink: bool
    isCanonical: bool


@dataclass
class ReadFileRangeResult:
    """Result of reading a file range."""
    content: str
    bytesRead: int
    bytesTotal: int


class FsOperations:
    """
    Filesystem operations interface.
    Provides async/sync operations with type safety.
    Allows abstraction for alternative implementations (e.g., mock, virtual).
    """
    
    # File access and information operations
    def cwd(self) -> str:
        """Gets the current working directory."""
        raise NotImplementedError
    
    def existsSync(self, path: str) -> bool:
        """Checks if a file or directory exists."""
        raise NotImplementedError
    
    async def stat(self, path: str) -> os.stat_result:
        """Gets file stats asynchronously."""
        raise NotImplementedError
    
    async def readdir(self, path: str) -> List[os.DirEntry]:
        """Lists directory contents with file type information asynchronously."""
        raise NotImplementedError
    
    async def unlink(self, path: str) -> None:
        """Deletes file asynchronously."""
        raise NotImplementedError
    
    async def rmdir(self, path: str) -> None:
        """Removes an empty directory asynchronously."""
        raise NotImplementedError
    
    async def rm(self, path: str, options: Optional[Dict[str, bool]] = None) -> None:
        """Removes files and directories asynchronously (with recursive option)."""
        raise NotImplementedError
    
    async def mkdir(self, path: str, options: Optional[Dict[str, Any]] = None) -> None:
        """Creates directory recursively asynchronously."""
        raise NotImplementedError
    
    async def read_file(self, path: str, options: Dict[str, str]) -> str:
        """Reads file content as string asynchronously."""
        raise NotImplementedError
    
    async def rename(self, oldPath: str, newPath: str) -> None:
        """Renames/moves file asynchronously."""
        raise NotImplementedError
    
    def statSync(self, path: str) -> os.stat_result:
        """Gets file stats."""
        raise NotImplementedError
    
    def lstatSync(self, path: str) -> os.stat_result:
        """Gets file stats without following symlinks."""
        raise NotImplementedError
    
    # File content operations
    def readFileSync(self, path: str, options: Dict[str, str]) -> str:
        """Reads file content as string with specified encoding."""
        raise NotImplementedError
    
    def readFileBytesSync(self, path: str) -> bytes:
        """Reads raw file bytes."""
        raise NotImplementedError
    
    def readSync(self, path: str, options: Dict[str, int]) -> Dict[str, Any]:
        """Reads specified number of bytes from file start."""
        raise NotImplementedError
    
    def appendFileSync(self, path: str, data: str, options: Optional[Dict[str, Any]] = None) -> None:
        """Appends string to file."""
        raise NotImplementedError
    
    def copyFileSync(self, src: str, dest: str) -> None:
        """Copies file from source to destination."""
        raise NotImplementedError
    
    def unlinkSync(self, path: str) -> None:
        """Deletes file."""
        raise NotImplementedError
    
    def renameSync(self, oldPath: str, newPath: str) -> None:
        """Renames/moves file."""
        raise NotImplementedError
    
    def linkSync(self, target: str, path: str) -> None:
        """Creates hard link."""
        raise NotImplementedError
    
    def symlinkSync(self, target: str, path: str, type: Optional[str] = None) -> None:
        """Creates symbolic link."""
        raise NotImplementedError
    
    def readlinkSync(self, path: str) -> str:
        """Reads symbolic link."""
        raise NotImplementedError
    
    def realpathSync(self, path: str) -> str:
        """Resolves symbolic links and returns the canonical pathname."""
        raise NotImplementedError
    
    # Directory operations
    def mkdirSync(self, path: str, options: Optional[Dict[str, Any]] = None) -> None:
        """Creates directory recursively."""
        raise NotImplementedError
    
    def readdirSync(self, path: str) -> List[os.DirEntry]:
        """Lists directory contents with file type information."""
        raise NotImplementedError
    
    def readdirStringSync(self, path: str) -> List[str]:
        """Lists directory contents as strings."""
        raise NotImplementedError
    
    def isDirEmptySync(self, path: str) -> bool:
        """Checks if the directory is empty."""
        raise NotImplementedError
    
    def rmdirSync(self, path: str) -> None:
        """Removes an empty directory."""
        raise NotImplementedError
    
    def rmSync(self, path: str, options: Optional[Dict[str, bool]] = None) -> None:
        """Removes files and directories (with recursive option)."""
        raise NotImplementedError


class NodeFsOperations(FsOperations):
    """Default Node.js-like filesystem implementation using Python os/path."""
    
    def cwd(self) -> str:
        return os.getcwd()
    
    def existsSync(self, path: str) -> bool:
        return os.path.exists(path)
    
    async def stat(self, path: str) -> os.stat_result:
        return await asyncio.get_event_loop().run_in_executor(None, os.stat, path)
    
    async def readdir(self, path: str) -> List[os.DirEntry]:
        return await asyncio.get_event_loop().run_in_executor(None, lambda: list(os.scandir(path)))
    
    async def unlink(self, path: str) -> None:
        from src.utils.safe_delete import safe_delete
        await asyncio.get_event_loop().run_in_executor(None, safe_delete, path)
    
    async def rmdir(self, path: str) -> None:
        from src.utils.safe_delete import safe_delete
        await asyncio.get_event_loop().run_in_executor(None, safe_delete, path)
    
    async def rm(self, path: str, options: Optional[Dict[str, bool]] = None) -> None:
        from src.utils.safe_delete import safe_delete
        await asyncio.get_event_loop().run_in_executor(None, safe_delete, path)
    
    async def mkdir(self, path: str, options: Optional[Dict[str, Any]] = None) -> None:
        import errno
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
    
    async def read_file(self, path: str, options: Dict[str, str]) -> str:
        encoding = options.get('encoding', 'utf-8')
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: Path(path).read_text(encoding=encoding))
    
    async def rename(self, oldPath: str, newPath: str) -> None:
        await asyncio.get_event_loop().run_in_executor(None, os.rename, oldPath, newPath)
    
    def statSync(self, path: str) -> os.stat_result:
        return os.stat(path)
    
    def lstatSync(self, path: str) -> os.stat_result:
        return os.lstat(path)
    
    def readFileSync(self, path: str, options: Dict[str, str]) -> str:
        encoding = options.get('encoding', 'utf-8')
        return Path(path).read_text(encoding=encoding)
    
    def readFileBytesSync(self, path: str) -> bytes:
        return Path(path).read_bytes()
    
    def readSync(self, path: str, options: Dict[str, int]) -> Dict[str, Any]:
        length = options['length']
        with open(path, 'rb') as f:
            buffer = f.read(length)
            return {'buffer': buffer, 'bytesRead': len(buffer)}
    
    def appendFileSync(self, path: str, data: str, options: Optional[Dict[str, Any]] = None) -> None:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(data)
    
    def copyFileSync(self, src: str, dest: str) -> None:
        import shutil
        shutil.copy2(src, dest)
    
    def unlinkSync(self, path: str) -> None:
        from src.utils.safe_delete import safe_delete
        safe_delete(path)
    
    def renameSync(self, oldPath: str, newPath: str) -> None:
        os.rename(oldPath, newPath)
    
    def linkSync(self, target: str, path: str) -> None:
        os.link(target, path)
    
    def symlinkSync(self, target: str, path: str, type: Optional[str] = None) -> None:
        os.symlink(target, path)
    
    def readlinkSync(self, path: str) -> str:
        return os.readlink(path)
    
    def realpathSync(self, path: str) -> str:
        return os.path.realpath(path)
    
    def mkdirSync(self, path: str, options: Optional[Dict[str, Any]] = None) -> None:
        import errno
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
    
    def readdirSync(self, path: str) -> List[os.DirEntry]:
        return list(os.scandir(path))
    
    def readdirStringSync(self, path: str) -> List[str]:
        return os.listdir(path)
    
    def isDirEmptySync(self, path: str) -> bool:
        return len(os.listdir(path)) == 0
    
    def rmdirSync(self, path: str) -> None:
        from src.utils.safe_delete import safe_delete
        safe_delete(path)
    
    def rmSync(self, path: str, options: Optional[Dict[str, bool]] = None) -> None:
        from src.utils.safe_delete import safe_delete
        safe_delete(path)


# The currently active filesystem implementation
activeFs: FsOperations = NodeFsOperations()


def safeResolvePath(fs: FsOperations, filePath: str) -> ResolvedPath:
    """
    Safely resolves a file path, handling symlinks and errors gracefully.
    
    Error handling strategy:
    - If the file doesn't exist, returns the original path (allows for file creation)
    - If symlink resolution fails (broken symlink, permission denied, circular links),
      returns the original path and marks it as not a symlink
    - This ensures operations can continue with the original path rather than failing
    """
    # Block UNC paths before any filesystem access to prevent network
    # requests (DNS/SMB) during validation on Windows
    if filePath.startswith('//') or filePath.startswith('\\\\'):
        return ResolvedPath(resolvedPath=filePath, isSymlink=False, isCanonical=False)
    
    try:
        # Check for special file types (FIFOs, sockets, devices) before calling realpathSync.
        # realpathSync can block on FIFOs waiting for a writer, causing hangs.
        stats = fs.lstatSync(filePath)
        
        import stat
        if (stat.S_ISFIFO(stats.st_mode) or 
            stat.S_ISSOCK(stats.st_mode) or
            stat.S_ISCHR(stats.st_mode) or
            stat.S_ISBLK(stats.st_mode)):
            return ResolvedPath(resolvedPath=filePath, isSymlink=False, isCanonical=False)
        
        resolvedPath = fs.realpathSync(filePath)
        return ResolvedPath(
            resolvedPath=resolvedPath,
            isSymlink=(resolvedPath != filePath),
            isCanonical=True,  # realpathSync returned: resolvedPath is canonical
        )
    except Exception:
        # If lstat/realpath fails for any reason (ENOENT, broken symlink,
        # EACCES, ELOOP, etc.), return the original path to allow operations
        # to proceed
        return ResolvedPath(resolvedPath=filePath, isSymlink=False, isCanonical=False)


def isDuplicatePath(fs: FsOperations, filePath: str, loadedPaths: Set[str]) -> bool:
    """
    Check if a file path is a duplicate and should be skipped.
    Resolves symlinks to detect duplicates pointing to the same file.
    If not a duplicate, adds the resolved path to loadedPaths.
    
    Returns: true if the file should be skipped (is duplicate)
    """
    resolved = safeResolvePath(fs, filePath)
    if resolved.resolvedPath in loadedPaths:
        return True
    loadedPaths.add(resolved.resolvedPath)
    return False


def resolveDeepestExistingAncestorSync(fs: FsOperations, absolutePath: str) -> Optional[str]:
    """
    Resolve the deepest existing ancestor of a path via realpathSync, walking
    up until it succeeds. Detects dangling symlinks (link entry exists, target
    doesn't) via lstat and resolves them via readlink.
    
    Use when the input path may not exist (new file writes) and you need to
    know where the write would ACTUALLY land after the OS follows symlinks.
    
    Returns the resolved absolute path with non-existent tail segments
    rejoined, or None if no symlink was found in any existing ancestor.
    """
    dir_path = absolutePath
    segments: List[str] = []
    
    # Walk up using lstat (cheap, O(1)) to find the first existing component.
    # lstat does not follow symlinks, so dangling symlinks are detected here.
    # Only call realpathSync (expensive, O(depth)) once at the end.
    while dir_path != os.path.dirname(dir_path):
        try:
            st = fs.lstatSync(dir_path)
        except Exception:
            # lstat failed: truly non-existent. Walk up.
            segments.insert(0, os.path.basename(dir_path))
            dir_path = os.path.dirname(dir_path)
            continue
        
        if os.path.islink(dir_path):
            # Found a symlink (live or dangling). Try realpath first (resolves
            # chained symlinks); fall back to readlink for dangling symlinks.
            try:
                resolved = fs.realpathSync(dir_path)
                return resolved if not segments else os.path.join(resolved, *segments)
            except Exception:
                # Dangling: realpath failed but lstat saw the link entry.
                target = fs.readlinkSync(dir_path)
                abs_target = target if os.path.isabs(target) else os.path.join(os.path.dirname(dir_path), target)
                return abs_target if not segments else os.path.join(abs_target, *segments)
        
        # Existing non-symlink component. One realpath call resolves any
        # symlinks in its ancestors. If none, return None (no symlink).
        try:
            resolved = fs.realpathSync(dir_path)
            if resolved != dir_path:
                return resolved if not segments else os.path.join(resolved, *segments)
        except Exception:
            # realpath can still fail (e.g. EACCES in ancestors). Return
            # None — we can't resolve, and the logical path is already
            # in pathSet for the caller.
            pass
        
        return None
    
    return None


def getPathsForPermissionCheck(inputPath: str) -> List[str]:
    """
    Gets all paths that should be checked for permissions.
    This includes the original path, all intermediate symlink targets in the chain,
    and the final resolved path.
    
    For example, if test.txt -> /etc/passwd -> /private/etc/passwd:
    - test.txt (original path)
    - /etc/passwd (intermediate symlink target)
    - /private/etc/passwd (final resolved path)
    """
    # Expand tilde notation defensively
    path = inputPath
    if path == '~':
        path = os.path.expanduser('~')
    elif path.startswith('~/'):
        path = os.path.join(os.path.expanduser('~'), path[2:])
    
    pathSet: Set[str] = {path}
    fsImpl = getFsImplementation()
    
    # Always check the original path
    # Block UNC paths before any filesystem access to prevent network
    # requests (DNS/SMB) during validation on Windows
    if path.startswith('//') or path.startswith('\\\\'):
        return list(pathSet)
    
    # Follow the symlink chain, collecting ALL intermediate targets
    try:
        currentPath = path
        visited: Set[str] = set()
        maxDepth = 40  # Prevent runaway loops, matches typical SYMLOOP_MAX
        
        for _ in range(maxDepth):
            # Prevent infinite loops from circular symlinks
            if currentPath in visited:
                break
            visited.add(currentPath)
            
            if not fsImpl.existsSync(currentPath):
                # Path doesn't exist (new file case). existsSync follows symlinks,
                # so this is also reached for DANGLING symlinks.
                if currentPath == path:
                    resolved = resolveDeepestExistingAncestorSync(fsImpl, path)
                    if resolved is not None:
                        pathSet.add(resolved)
                break
            
            # Skip special file types that can cause issues
            try:
                stats = fsImpl.lstatSync(currentPath)
                import stat
                if (stat.S_ISFIFO(stats.st_mode) or
                    stat.S_ISSOCK(stats.st_mode) or
                    stat.S_ISCHR(stats.st_mode) or
                    stat.S_ISBLK(stats.st_mode)):
                    break
                
                if not os.path.islink(currentPath):
                    break
            except Exception:
                break
            
            # Get the immediate symlink target
            target = fsImpl.readlinkSync(currentPath)
            
            # If target is relative, resolve it relative to the symlink's directory
            absoluteTarget = target if os.path.isabs(target) else os.path.join(os.path.dirname(currentPath), target)
            
            # Add this intermediate target to the set
            pathSet.add(absoluteTarget)
            currentPath = absoluteTarget
    except Exception:
        # If anything fails during chain traversal, continue with what we have
        pass
    
    # Also add the final resolved path using realpathSync for completeness
    resolved = safeResolvePath(fsImpl, path)
    if resolved.isSymlink and resolved.resolvedPath != path:
        pathSet.add(resolved.resolvedPath)
    
    return list(pathSet)


def setFsImplementation(implementation: FsOperations) -> None:
    """Overrides the filesystem implementation."""
    global activeFs
    activeFs = implementation


def getFsImplementation() -> FsOperations:
    """Gets the currently active filesystem implementation."""
    return activeFs


def setOriginalFsImplementation() -> None:
    """Resets the filesystem implementation to the default implementation."""
    global activeFs
    activeFs = NodeFsOperations()


async def readFileRange(path: str, offset: int, maxBytes: int) -> Optional[ReadFileRangeResult]:
    """
    Read up to `maxBytes` from a file starting at `offset`.
    Returns None if the file is smaller than the offset.
    """
    loop = asyncio.get_event_loop()
    
    def _read():
        size = os.path.getsize(path)
        if size <= offset:
            return None
        
        bytesToRead = min(size - offset, maxBytes)
        with open(path, 'rb') as f:
            f.seek(offset)
            content = f.read(bytesToRead)
            
            return ReadFileRangeResult(
                content=content.decode('utf-8'),
                bytesRead=len(content),
                bytesTotal=size,
            )
    
    return await loop.run_in_executor(None, _read)


async def tailFile(path: str, maxBytes: int) -> ReadFileRangeResult:
    """
    Read the last `maxBytes` of a file.
    Returns the whole file if it's smaller than maxBytes.
    """
    loop = asyncio.get_event_loop()
    
    def _tail():
        size = os.path.getsize(path)
        if size == 0:
            return ReadFileRangeResult(content='', bytesRead=0, bytesTotal=0)
        
        offset = max(0, size - maxBytes)
        with open(path, 'rb') as f:
            f.seek(offset)
            content = f.read()
            
            return ReadFileRangeResult(
                content=content.decode('utf-8'),
                bytesRead=len(content),
                bytesTotal=size,
            )
    
    return await loop.run_in_executor(None, _tail)


__all__ = [
    'FsOperations',
    'NodeFsOperations',
    'ResolvedPath',
    'ReadFileRangeResult',
    'safeResolvePath',
    'isDuplicatePath',
    'resolveDeepestExistingAncestorSync',
    'getPathsForPermissionCheck',
    'setFsImplementation',
    'getFsImplementation',
    'setOriginalFsImplementation',
    'readFileRange',
    'tailFile',
]
