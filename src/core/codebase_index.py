"""
Codebase Index — symbol indexing and cross-reference tracking.
Provides searchable index of all symbols (functions, classes, variables) in the project.
Used by plugins and AI for context-aware code understanding.
"""

import ast
import os
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, NamedTuple, Union, Any
from dataclasses import dataclass, field
from enum import Enum

from src.utils.logger import get_logger

log = get_logger("codebase_index")


class SymbolType(Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    IMPORT = "import"
    UNKNOWN = "unknown"


@dataclass
class Symbol:
    """Represents a symbol in the codebase."""
    name: str
    type: SymbolType
    file_path: str
    line: int
    column: int = 0
    parent: Optional[str] = None  # fully qualified parent name, e.g., "ClassName.method"
    docstring: Optional[str] = None
    signature: Optional[str] = None  # for functions/methods
    
    @property
    def fully_qualified(self) -> str:
        if self.parent:
            return f"{self.parent}.{self.name}"
        return self.name
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type.value,
            "file_path": self.file_path,
            "line": self.line,
            "column": self.column,
            "parent": self.parent,
            "docstring": self.docstring,
            "signature": self.signature,
        }


@dataclass
class FileIndex:
    """Index of symbols in a single file."""
    file_path: str
    symbols: List[Symbol] = field(default_factory=list)
    language: str = "python"
    last_modified: float = 0.0
    
    def add_symbol(self, symbol: Symbol):
        self.symbols.append(symbol)
    
    def find_symbols(self, name: Optional[str] = None, sym_type: Optional[SymbolType] = None) -> List[Symbol]:
        filtered = self.symbols
        if name:
            filtered = [s for s in filtered if name in s.name]
        if sym_type:
            filtered = [s for s in filtered if s.type == sym_type]
        return filtered


class CodebaseIndex:
    """
    Maintains a searchable index of all symbols in the project.
    Supports incremental updates on file changes.
    """
    
    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self._file_indices: Dict[str, FileIndex] = {}  # file_path -> FileIndex
        self._symbol_map: Dict[str, List[Symbol]] = {}  # symbol_name -> list of Symbols (allow overloads)
        self._excluded_dirs = {
            '.git', '__pycache__', 'node_modules', '.venv', 'venv',
            '.tox', 'dist', 'build', '.pytest_cache', '.mypy_cache',
            '.idea', '.vs', '.vscode', 'target', 'bin', 'obj'
        }
        self._excluded_extensions = {'.pyc', '.pyo', '.so', '.dll', '.pyd', '.db', '.sqlite'}
        log.info(f"Initialized codebase index for project: {self.project_root}")
    
    def index_project(self, force_rebuild: bool = False) -> int:
        """
        Index all source files in the project.
        Returns number of files indexed.
        """
        log.info("Starting project indexing...")
        if force_rebuild:
            self._file_indices.clear()
            self._symbol_map.clear()
        
        count = 0
        for file_path in self._get_source_files():
            if self._should_index_file(file_path):
                if self._index_file(file_path):
                    count += 1
        
        log.info(f"Project indexing complete. Indexed {count} files.")
        return count
    
    def _get_source_files(self) -> List[Path]:
        """Get list of source files in project."""
        files = []
        try:
            for root, dirs, filenames in os.walk(self.project_root):
                # Filter out excluded directories
                dirs[:] = [d for d in dirs if d not in self._excluded_dirs and not d.startswith('.')]
                
                for filename in filenames:
                    file_path = Path(root) / filename
                    ext = file_path.suffix.lower()
                    if ext in self._excluded_extensions:
                        continue
                    files.append(file_path)
        except Exception as e:
            log.error(f"Error walking project directory: {e}")
        return files
    
    def _should_index_file(self, file_path: Path) -> bool:
        """Determine if a file should be indexed."""
        # Currently only index Python files
        if file_path.suffix.lower() != '.py':
            return False
        # Check if file is binary (skip)
        try:
            with open(file_path, 'rb') as f:
                chunk = f.read(8192)
                if b'\x00' in chunk:
                    return False
        except Exception:
            return False
        return True
    
    def _remove_file_symbols_from_map(self, file_path: str) -> None:
        """Remove all previously indexed symbols for a file from the global symbol map."""
        empty_keys = []
        for symbol_name, symbols in self._symbol_map.items():
            remaining = [symbol for symbol in symbols if symbol.file_path != file_path]
            if remaining:
                self._symbol_map[symbol_name] = remaining
            else:
                empty_keys.append(symbol_name)

        for symbol_name in empty_keys:
            del self._symbol_map[symbol_name]

    def _index_file(self, file_path: Path) -> bool:
        """Index a single file, extracting symbols."""
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
            mod_time = file_path.stat().st_mtime
            file_path_str = str(file_path)
            
            # Check if file already indexed and not changed
            existing = self._file_indices.get(file_path_str)
            if existing and existing.last_modified >= mod_time:
                return True  # Already up to date
            
            # Parse AST
            tree = ast.parse(content, filename=file_path_str)
            
            # Create file index
            file_index = FileIndex(
                file_path=file_path_str,
                language="python",
                last_modified=mod_time
            )
            
            # Extract symbols
            visitor = SymbolExtractor(file_path, content)
            visitor.visit(tree)

            # Remove stale symbols for this file before adding refreshed ones
            self._remove_file_symbols_from_map(file_path_str)
            
            for symbol in visitor.symbols:
                file_index.add_symbol(symbol)
                # Add to symbol map
                if symbol.name not in self._symbol_map:
                    self._symbol_map[symbol.name] = []
                self._symbol_map[symbol.name].append(symbol)
            
            self._file_indices[file_path_str] = file_index
            log.debug(f"Indexed {len(visitor.symbols)} symbols in {file_path}")
            return True
            
        except SyntaxError as e:
            log.warning(f"Syntax error in {file_path}: {e}")
            return False
        except Exception as e:
            log.error(f"Failed to index {file_path}: {e}")
            return False
    
    def find_symbols(self, name: Optional[str] = None, sym_type: Optional[SymbolType] = None,
                     file_path: Optional[str] = None) -> List[Symbol]:
        """Find symbols by name, type, and/or file."""
        results = []
        
        if name and name in self._symbol_map:
            candidates = self._symbol_map[name]
        else:
            # Flatten all symbols
            candidates = []
            for sym_list in self._symbol_map.values():
                candidates.extend(sym_list)
        
        # Apply filters
        for symbol in candidates:
            if sym_type and symbol.type != sym_type:
                continue
            if file_path and symbol.file_path != file_path:
                continue
            results.append(symbol)
        
        return results
    
    def find_references(self, symbol_name: str) -> List[Symbol]:
        """Find all references to a symbol (placeholder)."""
        # TODO: Implement reference tracking
        return []
    
    def get_file_symbols(self, file_path: str) -> List[Symbol]:
        """Get all symbols in a file."""
        file_index = self._file_indices.get(file_path)
        if file_index:
            return file_index.symbols
        return []
    
    def get_project_stats(self) -> Dict[str, Any]:
        """Get indexing statistics."""
        total_symbols = sum(len(syms) for syms in self._symbol_map.values())
        return {
            "files_indexed": len(self._file_indices),
            "total_symbols": total_symbols,
            "symbols_by_type": {t.value: len(self.find_symbols(sym_type=t)) for t in SymbolType}
        }


class SymbolExtractor(ast.NodeVisitor):
    """AST visitor that extracts symbols from Python code."""
    
    def __init__(self, file_path: Path, source: str):
        self.file_path = file_path
        self.source_lines = source.splitlines()
        self.symbols: List[Symbol] = []
        self._current_class = None
    
    def _get_docstring(self, node) -> Optional[str]:
        """Extract docstring from node."""
        docstring = ast.get_docstring(node)
        return docstring
    
    def _get_source_segment(self, node) -> Optional[str]:
        """Get source code segment for signature."""
        try:
            if hasattr(node, 'lineno') and hasattr(node, 'end_lineno'):
                lines = self.source_lines[node.lineno - 1:node.end_lineno]
                return '\n'.join(lines)
        except Exception:
            pass
        return None
    
    def visit_ClassDef(self, node):
        """Extract class definition."""
        docstring = self._get_docstring(node)
        signature = self._get_source_segment(node)
        
        symbol = Symbol(
            name=node.name,
            type=SymbolType.CLASS,
            file_path=str(self.file_path),
            line=node.lineno,
            column=node.col_offset,
            docstring=docstring,
            signature=signature
        )
        self.symbols.append(symbol)
        
        # Enter class context
        previous_class = self._current_class
        self._current_class = node.name
        self.generic_visit(node)
        self._current_class = previous_class
    
    def visit_FunctionDef(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]):
        """Extract function or method definition."""
        docstring = self._get_docstring(node)
        signature = self._get_source_segment(node)
        
        sym_type = SymbolType.METHOD if self._current_class else SymbolType.FUNCTION
        parent = self._current_class
        
        symbol = Symbol(
            name=node.name,
            type=sym_type,
            file_path=str(self.file_path),
            line=node.lineno,
            column=node.col_offset,
            parent=parent,
            docstring=docstring,
            signature=signature
        )
        self.symbols.append(symbol)
        self.generic_visit(node)
    
    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)
    
    def visit_Assign(self, node):
        """Extract variable assignments (top-level only)."""
        # Only track top-level assignments (no parent class/function)
        if self._current_class is None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbol = Symbol(
                        name=target.id,
                        type=SymbolType.VARIABLE,
                        file_path=str(self.file_path),
                        line=node.lineno,
                        column=node.col_offset
                    )
                    self.symbols.append(symbol)
        self.generic_visit(node)
    
    def visit_Import(self, node):
        """Extract import statements."""
        for alias in node.names:
            symbol = Symbol(
                name=alias.name,
                type=SymbolType.IMPORT,
                file_path=str(self.file_path),
                line=node.lineno,
                column=node.col_offset
            )
            self.symbols.append(symbol)
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node):
        """Extract import from statements."""
        module = node.module or ""
        for alias in node.names:
            full_name = f"{module}.{alias.name}" if module else alias.name
            symbol = Symbol(
                name=full_name,
                type=SymbolType.IMPORT,
                file_path=str(self.file_path),
                line=node.lineno,
                column=node.col_offset
            )
            self.symbols.append(symbol)
        self.generic_visit(node)


# Singleton instance
_index = None


def get_codebase_index(project_root: Optional[str] = None) -> CodebaseIndex:
    """Get or create codebase index singleton."""
    global _index
    if _index is None and project_root is None:
        raise ValueError("Codebase index not initialized. Provide project_root.")
    if project_root and (_index is None or str(_index.project_root) != str(Path(project_root).resolve())):
        _index = CodebaseIndex(project_root)
    assert _index is not None
    return _index