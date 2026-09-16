"""
Code Chunker - Parse code into semantic chunks for embedding and search
Supports Python, JavaScript, TypeScript, Java, Go, Rust, C/C++, and more
"""

import re
import ast
import hashlib
from pathlib import Path
from typing import List, Optional, Tuple
from dataclasses import dataclass, field
from src.utils.logger import get_logger

log = get_logger("code_chunker")


@dataclass
class CodeChunk:
    """A chunk of code extracted from a file."""
    file_path: str
    start_line: int
    end_line: int
    chunk_type: str  # 'function', 'class', 'method', 'import', 'variable', 'constant', 'comment'
    name: str  # Function/class/variable name
    code: str  # Actual code content
    signature: str = ""  # Function signature (params, return type)
    docstring: str = ""  # Docstring/comment
    language: str = ""
    dependencies: List[str] = field(default_factory=list)  # Imported modules
    parent: str = ""  # Parent class/function name for nested structures
    hash: str = ""


class CodeChunker:
    """
    Parse source code files into semantic chunks.
    Supports multiple languages with AST parsing and regex fallbacks.
    """
    
    # Supported languages with their AST parsers
    AST_LANGUAGES = {
        'python': '.py',
    }
    
    # Regex patterns for languages without AST support
    LANGUAGE_PATTERNS = {
        'javascript': {
            'extensions': ['.js', '.jsx', '.mjs'],
            'function': [
                r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\([^)]*\)',
                r'(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>)',
                r'(?:export\s+)?(\w+)\s*:\s*(?:async\s+)?function\s*\([^)]*\)',
                r'(?:export\s+)?class\s+(\w+)',
            ],
            'class': [
                r'(?:export\s+)?class\s+(\w+)(?:\s+extends\s+\w+)?',
            ],
            'import': [
                r"import\s+[^;]+;",
                r"require\s*\([^)]+\)",
            ],
        },
        'typescript': {
            'extensions': ['.ts', '.tsx'],
            'function': [
                r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(?:<[^>]+>)?\s*\([^)]*\)',
                r'(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>)',
                r'(?:export\s+)?interface\s+(\w+)',
                r'(?:export\s+)?type\s+(\w+)',
            ],
            'class': [
                r'(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+\w+)?(?:\s+implements\s+[^{]+)?',
            ],
            'import': [
                r"import\s+[^;]+;",
                r"import\s*\{[^}]+\}\s*from\s*[^;]+;",
            ],
        },
        'java': {
            'extensions': ['.java'],
            'function': [
                r'(?:public|private|protected|static)?\s*(?:abstract|final)?\s*\w+(?:<[^>]+>)?\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w,]+)?\s*\{',
                r'(?:public|private|protected|static)\s+(?:abstract|final)?\s*void\s+(\w+)\s*\([^)]*\)',
            ],
            'class': [
                r'(?:public|private|protected)?\s*(?:abstract|final)?\s*class\s+(\w+)(?:\s+extends\s+\w+)?(?:\s+implements\s+[^{]+)?',
                r'(?:public|private|protected)?\s*interface\s+(\w+)',
            ],
            'import': [
                r"import\s+[\w.]+;",
                r"import\s+static\s+[\w.]+;",
            ],
        },
        'go': {
            'extensions': ['.go'],
            'function': [
                r'func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\([^)]*\)',
            ],
            'struct': [
                r'type\s+(\w+)\s+struct',
            ],
            'interface': [
                r'type\s+(\w+)\s+interface',
            ],
            'import': [
                r'import\s*\([^)]+\)',
                r'import\s+"[^"]+"',
            ],
        },
        'rust': {
            'extensions': ['.rs'],
            'function': [
                r'(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*(?:<[^>]+>)?\s*\([^)]*\)',
            ],
            'struct': [
                r'(?:pub\s+)?struct\s+(\w+)(?:<[^>]+>)?',
            ],
            'enum': [
                r'(?:pub\s+)?enum\s+(\w+)(?:<[^>]+>)?',
            ],
            'trait': [
                r'(?:pub\s+)?trait\s+(\w+)',
            ],
            'use': [
                r'use\s+[\w:]+(?:\s+as\s+\w+)?;',
            ],
        },
        'c': {
            'extensions': ['.c', '.h'],
            'function': [
                r'(?:static\s+)?(?:inline\s+)?\w+(?:\s*\*+\s*|\s+)\s*(\w+)\s*\([^)]*\)\s*\{',
                r'(\w+)\s*\([^)]*\)\s*;',
            ],
            'struct': [
                r'struct\s+(\w+)\s*\{',
                r'typedef\s+struct\s+(\w+)',
            ],
            'include': [
                r'#include\s*<[^>]+>',
                r'#include\s*"[^"]+"',
            ],
        },
        'cpp': {
            'extensions': ['.cpp', '.hpp', '.cc', '.cxx'],
            'function': [
                r'(?:static\s+)?(?:inline\s+)?(?:virtual\s+)?(?:explicit\s+)?(?:constexpr\s+)?\w+(?:\s*[*&]+\s*|\s+)~?\s*(\w+)\s*(?:<[^>]+>)?\s*\([^)]*\)\s*(?:const)?\s*(?:override)?\s*(?:final)?\s*\{',
                r'(?:class|struct)\s+(\w+)(?:\s*:\s*(?:public|private|protected)\s+\w+)?',
            ],
            'class': [
                r'(?:class|struct)\s+(\w+)(?:\s*:\s*(?:public|private|protected)\s+\w+)?',
            ],
            'namespace': [
                r'namespace\s+(\w+)',
            ],
            'include': [
                r'#include\s*<[^>]+>',
                r'#include\s*"[^"]+"',
            ],
        },
        'csharp': {
            'extensions': ['.cs'],
            'function': [
                r'(?:public|private|protected|internal|static)\s+(?:async\s+)?(?:virtual|override|abstract)?\s*\w+(?:<[^>]+>)?\s+(\w+)\s*\([^)]*\)',
            ],
            'class': [
                r'(?:public|private|protected|internal|static)\s*(?:abstract|sealed|partial)?\s*class\s+(\w+)',
            ],
            'interface': [
                r'(?:public|private|protected|internal)?\s*interface\s+(\w+)',
            ],
            'using': [
                r'using\s+[\w.]+;',
                r'using\s+\w+\s*=\s*[\w.]+;',
            ],
        },
        'ruby': {
            'extensions': ['.rb'],
            'function': [
                r'def\s+(\w+)(?:\([^)]*\))?',
                r'def\s+self\.(\w+)(?:\([^)]*\))?',
            ],
            'class': [
                r'class\s+(\w+)(?:\s*<\s*\w+)?',
                r'module\s+(\w+)',
            ],
            'require': [
                r"require\s*['\"][^'\"]+['\"]",
                r"require_relative\s*['\"][^'\"]+['\"]",
            ],
        },
        'php': {
            'extensions': ['.php'],
            'function': [
                r'(?:public|private|protected)?\s*(?:static)?\s*function\s+(\w+)\s*\([^)]*\)',
                r'function\s+__(?:construct|destruct|get|set|call)\s*\([^)]*\)',
            ],
            'class': [
                r'(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+\w+)?(?:\s+implements\s+[^{]+)?',
                r'interface\s+(\w+)',
                r'trait\s+(\w+)',
            ],
            'namespace': [
                r'namespace\s+[\w\\]+;',
            ],
            'use': [
                r'use\s+[\w\\]+(?:\s+as\s+\w+)?;',
            ],
        },
    }
    
    def __init__(self):
        self.min_chunk_size = 50  # Minimum characters for a chunk
        self.max_chunk_size = 2000  # Maximum characters for a chunk
    
    def chunk_file(self, file_path: str, content: str = None, language: str = None) -> List[CodeChunk]:
        """
        Parse a file into code chunks.
        
        Args:
            file_path: Path to the file
            content: File content (if None, reads from file)
            language: Language override (if None, detects from extension)
        
        Returns:
            List of CodeChunk objects
        """
        # Read content if not provided
        if content is None:
            try:
                content = Path(file_path).read_text(encoding='utf-8', errors='ignore')
            except Exception as e:
                log.warning(f"Failed to read file {file_path}: {e}")
                return []
        
        # Detect language from extension
        if language is None:
            language = self._detect_language(file_path)
        
        # Choose parser based on language
        if language == 'python':
            return self._chunk_python(file_path, content)
        else:
            return self._chunk_regex(file_path, content, language)
    
    def _detect_language(self, file_path: str) -> str:
        """Detect language from file extension."""
        ext = Path(file_path).suffix.lower()
        
        # Check Python first
        if ext == '.py':
            return 'python'
        
        # Check other languages
        for lang, config in self.LANGUAGE_PATTERNS.items():
            if ext in config.get('extensions', []):
                return lang
        
        return 'unknown'
    
    def _chunk_python(self, file_path: str, content: str) -> List[CodeChunk]:
        """Parse Python file using AST."""
        chunks = []
        
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            log.warning(f"Failed to parse Python file {file_path}: {e}")
            # Fall back to regex
            return self._chunk_regex(file_path, content, 'python')
        
        lines = content.split('\n')
        
        # Extract imports
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                start_line = node.lineno
                end_line = getattr(node, 'end_lineno', start_line) or start_line
                code = '\n'.join(lines[start_line-1:end_line])
                
                # Extract imported names
                if isinstance(node, ast.Import):
                    deps = [alias.name for alias in node.names]
                else:
                    deps = [node.module] if node.module else []
                
                chunk = CodeChunk(
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    chunk_type='import',
                    name='',
                    code=code,
                    language='python',
                    dependencies=deps
                )
                chunk.hash = self._compute_hash(chunk)
                chunks.append(chunk)
        
        # Extract functions and classes
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                chunk = self._extract_function(file_path, node, lines, content)
                if chunk:
                    chunks.append(chunk)
            
            elif isinstance(node, ast.ClassDef):
                class_chunk = self._extract_class(file_path, node, lines, content)
                if class_chunk:
                    chunks.append(class_chunk)
                    
                    # Extract methods
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            method_chunk = self._extract_method(file_path, item, lines, content, node.name)
                            if method_chunk:
                                chunks.append(method_chunk)
        
        return chunks
    
    def _extract_function(self, file_path: str, node, lines: List[str], content: str) -> Optional[CodeChunk]:
        """Extract a function definition."""
        start_line = node.lineno
        end_line = getattr(node, 'end_lineno', start_line) or start_line
        
        if end_line is None:
            end_line = start_line
        
        code = '\n'.join(lines[start_line-1:end_line])
        
        # Get function signature
        args = []
        for arg in node.args.args:
            arg_str = arg.arg
            if arg.annotation:
                arg_str += f": {ast.unparse(arg.annotation)}"
            args.append(arg_str)
        
        signature = f"def {node.name}({', '.join(args)})"
        
        # Get docstring
        docstring = ast.get_docstring(node) or ""
        
        # Extract dependencies from imports (will be filled by parent)
        
        chunk = CodeChunk(
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            chunk_type='function',
            name=node.name,
            code=code,
            signature=signature,
            docstring=docstring,
            language='python'
        )
        chunk.hash = self._compute_hash(chunk)
        return chunk
    
    def _extract_class(self, file_path: str, node, lines: List[str], content: str) -> Optional[CodeChunk]:
        """Extract a class definition."""
        start_line = node.lineno
        end_line = getattr(node, 'end_lineno', start_line) or start_line
        
        if end_line is None:
            end_line = start_line
        
        # Find the end of the class header
        # (First non-empty line after class declaration that's not indented)
        header_end = start_line
        for i in range(start_line, min(start_line + 10, len(lines))):
            line = lines[i]
            if line.strip() and not line.startswith(' ') and not line.startswith('\t'):
                if i > start_line:
                    header_end = i
                    break
        
        # Get just the class header + docstring
        header_code = '\n'.join(lines[start_line-1:header_end])
        
        # Get full class code
        code = '\n'.join(lines[start_line-1:end_line])
        
        # Get class signature
        bases = [ast.unparse(base) for base in node.bases]
        signature = f"class {node.name}"
        if bases:
            signature += f"({', '.join(bases)})"
        
        # Get docstring
        docstring = ast.get_docstring(node) or ""
        
        chunk = CodeChunk(
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            chunk_type='class',
            name=node.name,
            code=code,
            signature=signature,
            docstring=docstring,
            language='python'
        )
        chunk.hash = self._compute_hash(chunk)
        return chunk
    
    def _extract_method(self, file_path: str, node, lines: List[str], content: str, class_name: str) -> Optional[CodeChunk]:
        """Extract a method definition from a class."""
        chunk = self._extract_function(file_path, node, lines, content)
        if chunk:
            chunk.chunk_type = 'method'
            chunk.parent = class_name
            chunk.name = f"{class_name}.{node.name}"
        return chunk
    
    def _chunk_regex(self, file_path: str, content: str, language: str) -> List[CodeChunk]:
        """Parse file using regex patterns (fallback for non-Python languages)."""
        chunks = []
        
        if language not in self.LANGUAGE_PATTERNS:
            # Unknown language - just return whole file as one chunk
            chunk = CodeChunk(
                file_path=file_path,
                start_line=1,
                end_line=content.count('\n') + 1,
                chunk_type='unknown',
                name='',
                code=content,
                language=language
            )
            chunk.hash = self._compute_hash(chunk)
            return [chunk]
        
        patterns = self.LANGUAGE_PATTERNS[language]
        lines = content.split('\n')
        
        # Extract imports first
        if 'import' in patterns:
            for import_pattern in patterns['import']:
                for match in re.finditer(import_pattern, content, re.MULTILINE):
                    # Find line number
                    start_pos = match.start()
                    start_line = content[:start_pos].count('\n') + 1
                    end_line = start_line + match.group().count('\n')
                    
                    code = match.group()
                    chunk = CodeChunk(
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        chunk_type='import',
                        name='',
                        code=code,
                        language=language
                    )
                    chunk.hash = self._compute_hash(chunk)
                    chunks.append(chunk)
        
        # Extract functions
        if 'function' in patterns:
            for pattern in patterns['function']:
                for match in re.finditer(pattern, content, re.MULTILINE):
                    name = match.group(1) if match.groups() else ''
                    start_pos = match.start()
                    start_line = content[:start_pos].count('\n') + 1
                    
                    # Try to find function body end (match braces)
                    end_line = self._find_block_end(lines, start_line - 1, language)
                    
                    code = '\n'.join(lines[start_line-1:end_line])
                    
                    chunk = CodeChunk(
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        chunk_type='function',
                        name=name,
                        code=code,
                        signature=match.group().strip(),
                        language=language
                    )
                    chunk.hash = self._compute_hash(chunk)
                    chunks.append(chunk)
        
        # Extract classes
        if 'class' in patterns:
            for pattern in patterns['class']:
                for match in re.finditer(pattern, content, re.MULTILINE):
                    name = match.group(1) if match.groups() else ''
                    start_pos = match.start()
                    start_line = content[:start_pos].count('\n') + 1
                    
                    # Try to find class body end
                    end_line = self._find_block_end(lines, start_line - 1, language)
                    
                    code = '\n'.join(lines[start_line-1:end_line])
                    
                    chunk = CodeChunk(
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        chunk_type='class',
                        name=name,
                        code=code,
                        signature=match.group().strip(),
                        language=language
                    )
                    chunk.hash = self._compute_hash(chunk)
                    chunks.append(chunk)
        
        # Sort by start line
        chunks.sort(key=lambda c: c.start_line)
        
        return chunks
    
    def _find_block_end(self, lines: List[str], start_idx: int, language: str) -> int:
        """Find the end of a code block (class, function, etc.)."""
        # Language-specific delimiters
        if language in ('python', 'ruby'):
            # Indentation-based
            if start_idx >= len(lines):
                return start_idx + 1
            
            base_indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
            end_idx = start_idx + 1
            
            for i in range(start_idx + 1, len(lines)):
                line = lines[i]
                if line.strip() == '':  # Empty line
                    continue
                current_indent = len(line) - len(line.lstrip())
                if current_indent <= base_indent and line.strip():
                    break
                end_idx = i + 1
            
            return end_idx
        
        else:
            # Brace-based languages
            brace_count = 0
            found_opening = False
            
            for i in range(start_idx, min(start_idx + 500, len(lines))):
                line = lines[i]
                
                for char in line:
                    if char == '{':
                        brace_count += 1
                        found_opening = True
                    elif char == '}':
                        brace_count -= 1
                        
                        if found_opening and brace_count == 0:
                            return i + 1
            
            return min(start_idx + 50, len(lines))  # Default: 50 lines
    
    def _compute_hash(self, chunk: CodeChunk) -> str:
        """Compute a hash for a chunk (for deduplication)."""
        content = f"{chunk.file_path}:{chunk.start_line}:{chunk.chunk_type}:{chunk.name}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def get_chunk_summary(self, chunks: List[CodeChunk]) -> str:
        """Get a summary of chunks (for logging/debugging)."""
        summary = {
            'total': len(chunks),
            'by_type': {},
            'by_file': {}
        }
        
        for chunk in chunks:
            chunk_type = chunk.chunk_type
            summary['by_type'][chunk_type] = summary['by_type'].get(chunk_type, 0) + 1
            
            file_name = Path(chunk.file_path).name
            summary['by_file'][file_name] = summary['by_file'].get(file_name, 0) + 1
        
        return str(summary)
    
    def extract_dependencies(self, chunks: List[CodeChunk]) -> List[str]:
        """Extract all dependencies from import chunks."""
        deps = []
        for chunk in chunks:
            if chunk.chunk_type == 'import':
                deps.extend(chunk.dependencies)
        return list(set(deps))


# Global chunker instance
_chunker: Optional[CodeChunker] = None

def get_chunker() -> CodeChunker:
    """Get or create the global chunker instance."""
    global _chunker
    if _chunker is None:
        _chunker = CodeChunker()
    return _chunker