"""
file_skeleton.py
----------------
Generate compact structural skeletons of source files for LLM context.

Instead of reading an entire 3,000+ line file (which wastes tokens and risks
context overflow), this module extracts only the structural elements:
  - Imports
  - Class definitions (with bases)
  - Function/method signatures (with decorators)
  - Top-level constants and assignments

The skeleton is typically 5-10% of the original file size, saving 90-95% of
tokens while giving the LLM enough information to locate the exact section
it needs to read with offset/limit.

Supported languages:
  - Python (.py)
  - JavaScript / TypeScript (.js, .ts, .jsx, .tsx)
  - Java (.java)
  - C / C++ (.c, .cpp, .h, .hpp)
  - Go (.go)
  - Rust (.rs)
  - HTML (.html, .htm)
  - CSS / SCSS / LESS (.css, .scss, .less)

Usage:
    from src.ai.file_skeleton import generate_skeleton
    skeleton = generate_skeleton("/path/to/large_file.py")
"""

import os
import re
from typing import Optional


def generate_skeleton(file_path: str, max_lines: int = 300) -> Optional[str]:
    """
    Generate a structural skeleton of a source file.

    Args:
        file_path: Absolute path to the source file.
        max_lines: Maximum skeleton lines to return (safety cap).

    Returns:
        A string containing the skeleton with line numbers, or None if
        the file type is not supported or cannot be read.
    """
    if not os.path.isfile(file_path):
        return None

    ext = os.path.splitext(file_path)[1].lower()

    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as fh:
            lines = fh.readlines()
    except (OSError, PermissionError):
        return None

    total_lines = len(lines)
    basename = os.path.basename(file_path)

    if ext == '.py':
        skeleton_lines = _skeleton_python(lines)
    elif ext in ('.js', '.jsx', '.ts', '.tsx', '.mjs'):
        skeleton_lines = _skeleton_javascript(lines)
    elif ext == '.java':
        skeleton_lines = _skeleton_java(lines)
    elif ext in ('.c', '.cpp', '.h', '.hpp', '.cc', '.cxx'):
        skeleton_lines = _skeleton_c_cpp(lines)
    elif ext == '.go':
        skeleton_lines = _skeleton_go(lines)
    elif ext == '.rs':
        skeleton_lines = _skeleton_rust(lines)
    elif ext in ('.html', '.htm'):
        skeleton_lines = _skeleton_html(lines)
    elif ext in ('.css', '.scss', '.less'):
        skeleton_lines = _skeleton_css(lines)
    else:
        # Unsupported: return first 20 + last 10 lines as minimal skeleton
        skeleton_lines = _skeleton_generic(lines)

    # Cap output
    if len(skeleton_lines) > max_lines:
        skeleton_lines = skeleton_lines[:max_lines]
        skeleton_lines.append("... (skeleton truncated)")

    header = f"# {basename} ({total_lines:,} lines) -- SKELETON VIEW"
    hint = (
        "# To read a specific section, use: "
        f"Read(file_path=\"{file_path}\", offset=LINE_NUMBER, limit=80)"
    )

    return header + "\n" + hint + "\n\n" + "\n".join(skeleton_lines)


# ---------------------------------------------------------------------------
# Python skeleton
# ---------------------------------------------------------------------------

_PY_IMPORT    = re.compile(r'^(import |from \S+ import )')
_PY_CLASS     = re.compile(r'^(\s*)class\s+(\w+)')
_PY_DEF       = re.compile(r'^(\s*)(async\s+)?def\s+(\w+)\s*\(')
_PY_DECORATOR = re.compile(r'^(\s*)@\w+')
_PY_ASSIGN    = re.compile(r'^([A-Z_][A-Z_0-9]*)\s*=')  # Top-level CONSTANTS
_PY_DOCSTRING = re.compile(r'^\s*"""')


def _skeleton_python(lines):
    """Extract Python structural elements."""
    result = []
    in_imports = False
    import_count = 0
    decorator_buf = []

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip()
        stripped = line.lstrip()

        # Imports: show first 5, then summarize
        if _PY_IMPORT.match(stripped):
            import_count += 1
            if import_count <= 5:
                result.append(f"L{i}: {line}")
            elif import_count == 6:
                result.append(f"L{i}: ... ({import_count}+ more imports)")
            in_imports = True
            continue
        elif in_imports and not stripped:
            in_imports = False
            continue

        # Decorators: buffer them
        if _PY_DECORATOR.match(line):
            decorator_buf.append(f"L{i}: {line}")
            continue

        # Class definitions
        m = _PY_CLASS.match(line)
        if m:
            indent = m.group(1)
            # Flush decorators
            result.extend(decorator_buf)
            decorator_buf = []
            result.append(f"L{i}: {line}")
            # Check for docstring on next line
            if i < len(lines):
                next_line = lines[i].strip()
                if next_line.startswith('"""') or next_line.startswith("'''"):
                    doc = next_line.strip('"').strip("'").strip()
                    if doc:
                        result.append(f"L{i+1}:   # {doc[:80]}")
            continue

        # Function/method definitions
        m = _PY_DEF.match(line)
        if m:
            # Flush decorators
            result.extend(decorator_buf)
            decorator_buf = []
            # Get the full signature (may span multiple lines)
            sig = line
            if ')' not in sig and ':' not in sig:
                # Multi-line signature: grab up to closing paren
                for j in range(i, min(i + 5, len(lines))):
                    sig += ' ' + lines[j].strip()
                    if ')' in lines[j]:
                        break
                # Clean up
                sig = re.sub(r'\s+', ' ', sig).strip()
                if len(sig) > 120:
                    sig = sig[:117] + '...'
            result.append(f"L{i}: {sig}")
            continue

        # Clear decorator buffer if we hit something else
        decorator_buf = []

        # Top-level constants
        if _PY_ASSIGN.match(stripped) and not line.startswith(' '):
            val_preview = stripped[:80]
            result.append(f"L{i}: {val_preview}")

    return result


# ---------------------------------------------------------------------------
# JavaScript / TypeScript skeleton
# ---------------------------------------------------------------------------

_JS_IMPORT   = re.compile(r'^(import |const .+ = require\()')
_JS_EXPORT   = re.compile(r'^export\s+(default\s+)?(class|function|const|let|interface|type|enum)\s+(\w+)')
_JS_CLASS    = re.compile(r'^(\s*)(export\s+)?(default\s+)?class\s+(\w+)')
_JS_FUNC     = re.compile(r'^(\s*)(export\s+)?(default\s+)?(async\s+)?function\s*\*?\s+(\w+)')
_JS_ARROW    = re.compile(r'^(\s*)(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(async\s+)?\(')
_JS_METHOD   = re.compile(r'^(\s+)(async\s+)?(\w+)\s*\(')
_JS_IFACE    = re.compile(r'^(\s*)(export\s+)?(interface|type)\s+(\w+)')


def _skeleton_javascript(lines):
    """Extract JS/TS structural elements."""
    result = []
    import_count = 0

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip()
        stripped = line.lstrip()

        # Imports
        if _JS_IMPORT.match(stripped):
            import_count += 1
            if import_count <= 5:
                result.append(f"L{i}: {line}")
            elif import_count == 6:
                result.append(f"L{i}: ... ({import_count}+ more imports)")
            continue

        # Interface / type definitions
        m = _JS_IFACE.match(line)
        if m:
            result.append(f"L{i}: {line}")
            continue

        # Class definitions
        m = _JS_CLASS.match(line)
        if m:
            result.append(f"L{i}: {line}")
            continue

        # Function declarations
        m = _JS_FUNC.match(line)
        if m:
            result.append(f"L{i}: {line}")
            continue

        # Arrow function assignments
        m = _JS_ARROW.match(line)
        if m:
            sig = line
            if len(sig) > 120:
                sig = sig[:117] + '...'
            result.append(f"L{i}: {sig}")
            continue

        # Class methods (indented, starts with identifier followed by paren)
        m = _JS_METHOD.match(line)
        if m and not stripped.startswith('if') and not stripped.startswith('for') \
                and not stripped.startswith('while') and not stripped.startswith('switch') \
                and not stripped.startswith('return') and not stripped.startswith('//'):
            result.append(f"L{i}: {line}")
            continue

        # Exports
        m = _JS_EXPORT.match(line)
        if m:
            result.append(f"L{i}: {line}")

    return result


# ---------------------------------------------------------------------------
# Java skeleton
# ---------------------------------------------------------------------------

_JAVA_IMPORT  = re.compile(r'^import\s+')
_JAVA_CLASS   = re.compile(r'^(\s*)(public|private|protected)?\s*(abstract\s+)?(class|interface|enum)\s+(\w+)')
_JAVA_METHOD  = re.compile(r'^(\s+)(public|private|protected)\s+.*\w+\s*\(')
_JAVA_ANNOT   = re.compile(r'^(\s*)@\w+')


def _skeleton_java(lines):
    """Extract Java structural elements."""
    result = []
    import_count = 0
    annot_buf = []

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip()
        stripped = line.lstrip()

        if _JAVA_IMPORT.match(stripped):
            import_count += 1
            if import_count <= 3:
                result.append(f"L{i}: {line}")
            elif import_count == 4:
                result.append(f"L{i}: ... ({import_count}+ more imports)")
            continue

        if _JAVA_ANNOT.match(line):
            annot_buf.append(f"L{i}: {line}")
            continue

        if _JAVA_CLASS.match(line):
            result.extend(annot_buf)
            annot_buf = []
            result.append(f"L{i}: {line}")
            continue

        if _JAVA_METHOD.match(line) and '{' not in stripped[:stripped.find('(')]:
            result.extend(annot_buf)
            annot_buf = []
            sig = line.rstrip('{').rstrip()
            if len(sig) > 120:
                sig = sig[:117] + '...'
            result.append(f"L{i}: {sig}")
            continue

        annot_buf = []

    return result


# ---------------------------------------------------------------------------
# C / C++ skeleton
# ---------------------------------------------------------------------------

_C_INCLUDE  = re.compile(r'^#include\s+')
_C_DEFINE   = re.compile(r'^#define\s+(\w+)')
_C_FUNC     = re.compile(r'^(\w[\w\s\*:~]+)\s+(\w+)\s*\(')
_C_CLASS    = re.compile(r'^(\s*)(class|struct|enum)\s+(\w+)')
_C_TYPEDEF  = re.compile(r'^typedef\s+')


def _skeleton_c_cpp(lines):
    """Extract C/C++ structural elements."""
    result = []

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip()
        stripped = line.lstrip()

        if _C_INCLUDE.match(stripped):
            result.append(f"L{i}: {line}")
            continue

        if _C_DEFINE.match(stripped):
            result.append(f"L{i}: {line[:80]}")
            continue

        if _C_CLASS.match(line):
            result.append(f"L{i}: {line}")
            continue

        if _C_TYPEDEF.match(stripped):
            result.append(f"L{i}: {line[:80]}")
            continue

        m = _C_FUNC.match(line)
        if m and not stripped.startswith('if') and not stripped.startswith('for') \
                and not stripped.startswith('while') and not stripped.startswith('return'):
            sig = line.rstrip('{').rstrip()
            if len(sig) > 120:
                sig = sig[:117] + '...'
            result.append(f"L{i}: {sig}")

    return result


# ---------------------------------------------------------------------------
# Go skeleton
# ---------------------------------------------------------------------------

_GO_IMPORT  = re.compile(r'^import\s+')
_GO_FUNC    = re.compile(r'^func\s+')
_GO_TYPE    = re.compile(r'^type\s+(\w+)\s+(struct|interface)')
_GO_VAR     = re.compile(r'^(var|const)\s+')


def _skeleton_go(lines):
    """Extract Go structural elements."""
    result = []

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip()
        stripped = line.lstrip()

        if _GO_IMPORT.match(stripped):
            result.append(f"L{i}: {line}")
            continue

        if _GO_TYPE.match(stripped):
            result.append(f"L{i}: {line}")
            continue

        if _GO_FUNC.match(stripped):
            sig = line.rstrip('{').rstrip()
            if len(sig) > 120:
                sig = sig[:117] + '...'
            result.append(f"L{i}: {sig}")
            continue

        if _GO_VAR.match(stripped):
            result.append(f"L{i}: {line[:80]}")

    return result


# ---------------------------------------------------------------------------
# Rust skeleton
# ---------------------------------------------------------------------------

_RS_USE    = re.compile(r'^use\s+')
_RS_FN     = re.compile(r'^(\s*)(pub\s+)?(async\s+)?fn\s+(\w+)')
_RS_STRUCT = re.compile(r'^(\s*)(pub\s+)?(struct|enum|trait|impl)\s+')
_RS_MOD    = re.compile(r'^(pub\s+)?mod\s+')
_RS_ATTR   = re.compile(r'^(\s*)#\[')


def _skeleton_rust(lines):
    """Extract Rust structural elements."""
    result = []
    attr_buf = []

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip()
        stripped = line.lstrip()

        if _RS_USE.match(stripped):
            result.append(f"L{i}: {line}")
            continue

        if _RS_ATTR.match(line):
            attr_buf.append(f"L{i}: {line}")
            continue

        if _RS_MOD.match(stripped):
            result.append(f"L{i}: {line}")
            continue

        if _RS_STRUCT.match(line):
            result.extend(attr_buf)
            attr_buf = []
            result.append(f"L{i}: {line}")
            continue

        m = _RS_FN.match(line)
        if m:
            result.extend(attr_buf)
            attr_buf = []
            sig = line.rstrip('{').rstrip()
            if len(sig) > 120:
                sig = sig[:117] + '...'
            result.append(f"L{i}: {sig}")
            continue

        attr_buf = []

    return result


# ---------------------------------------------------------------------------
# HTML skeleton
# ---------------------------------------------------------------------------

_HTML_TAG = re.compile(r'<(/?)(\w[\w-]*)([\s\S]*?)(/?)>', re.DOTALL)
_HTML_ID    = re.compile(r'\bid="([^"]+)"')
_HTML_CLASS = re.compile(r'\bclass="([^"]+)"')
_HTML_NAME  = re.compile(r'\bname="([^"]+)"')
_HTML_HREF  = re.compile(r'\bhref="([^"]+)"')
_HTML_SRC   = re.compile(r'\bsrc="([^"]+)"')
_HTML_FOR   = re.compile(r'\bfor="([^"]+)"')


def _skeleton_html(lines):
    """Extract HTML structure: elements with IDs, classes, key attributes."""
    result = []
    stack = []          # track open tags for indentation
    skip_tags = {'script', 'style', 'svg'}  # skip content inside these
    in_skip = False
    skip_tag = None

    for i, raw in enumerate(lines):
        line = raw.strip()
        ln = i + 1

        # Skip comments and empty lines
        if line.startswith('<!--') or not line:
            continue

        # Track skip regions (script/style/svg)
        if in_skip:
            if re.search(rf'</{skip_tag}>', line, re.I):
                in_skip = False
                skip_tag = None
            continue

        # Find all tags on this line
        for m in _HTML_TAG.finditer(line):
            is_close = m.group(1) == '/'
            tag_name = m.group(2).lower()
            attrs    = m.group(3)
            is_self  = m.group(4) == '/'

            # Check if entering skip region
            if not is_close and tag_name in skip_tags:
                in_skip = True
                skip_tag = tag_name

            # --- CLOSING TAG ---
            if is_close:
                if stack and stack[-1] == tag_name:
                    stack.pop()
                continue

            # --- OPENING TAG ---
            indent = '  ' * len(stack)
            parts = [f"L{ln}: {indent}<{tag_name}"]

            # Extract key attributes
            id_m    = _HTML_ID.search(attrs)
            class_m = _HTML_CLASS.search(attrs)
            name_m  = _HTML_NAME.search(attrs)
            href_m  = _HTML_HREF.search(attrs)
            src_m   = _HTML_SRC.search(attrs)
            for_m   = _HTML_FOR.search(attrs)

            if id_m:
                parts.append(f' id="{id_m.group(1)}"')
            if class_m:
                cls = class_m.group(1)
                # Truncate long class lists
                if len(cls) > 50:
                    cls = cls[:47] + '...'
                parts.append(f' class="{cls}"')
            if name_m:
                parts.append(f' name="{name_m.group(1)}"')
            if href_m:
                parts.append(f' href="{href_m.group(1)}"')
            if src_m:
                parts.append(f' src="{src_m.group(1)}"')
            if for_m:
                parts.append(f' for="{for_m.group(1)}"')

            parts.append('/>' if is_self else '>')
            result.append(''.join(parts))

            # Push to stack if not self-closing or void element
            if not is_self and tag_name not in (
                'br', 'hr', 'img', 'input', 'meta', 'link', 'area',
                'base', 'col', 'embed', 'param', 'source', 'track', 'wbr'
            ):
                stack.append(tag_name)

    return result


# ---------------------------------------------------------------------------
# CSS skeleton
# ---------------------------------------------------------------------------

_CSS_SELECTOR  = re.compile(r'^([^{}]+?)\s*\{', re.DOTALL)
_CSS_MEDIA     = re.compile(r'^\s*@media\s+(.+?)\s*\{')
_CSS_KEYFRAMES = re.compile(r'^\s*@keyframes\s+(\S+)\s*\{')
_CSS_IMPORT    = re.compile(r'^\s*@import\s+["\']?([^;"\']+)["\']?\s*;')
_CSS_VAR       = re.compile(r'^\s*(--[\w-]+)\s*:')


def _skeleton_css(lines):
    """Extract CSS structure: selectors, media queries, keyframes, imports, variables."""
    result = []
    in_block = False
    block_depth = 0
    current_media = None
    brace_count = 0

    for i, raw in enumerate(lines):
        line = raw.strip()
        ln = i + 1

        if not line or line.startswith('/*'):
            continue

        # @import
        imp_m = _CSS_IMPORT.match(line)
        if imp_m:
            result.append(f"L{ln}: @import '{imp_m.group(1)}'")
            continue

        # @keyframes
        kf_m = _CSS_KEYFRAMES.match(line)
        if kf_m:
            result.append(f"L{ln}: @keyframes {kf_m.group(1)} {{")
            brace_count += line.count('{') - line.count('}')
            continue

        # @media
        media_m = _CSS_MEDIA.match(line)
        if media_m:
            current_media = media_m.group(1)
            result.append(f"L{ln}: @media {current_media} {{")
            brace_count += line.count('{') - line.count('}')
            continue

        # CSS variable definition (inside rule)
        var_m = _CSS_VAR.match(line)
        if var_m:
            indent = '  ' * max(0, brace_count)
            result.append(f"L{ln}: {indent}{var_m.group(1)}: ...")
            continue

        # Rule selector
        sel_m = _CSS_SELECTOR.match(line)
        if sel_m:
            selector = sel_m.group(1).strip()
            # Clean up multi-line selectors
            selector = re.sub(r'\s+', ' ', selector)
            if len(selector) > 80:
                selector = selector[:77] + '...'
            indent = '  ' * max(0, brace_count)
            result.append(f"L{ln}: {indent}{selector} {{ ... }}")

        # Track braces
        brace_count += line.count('{') - line.count('}')

    return result


# ---------------------------------------------------------------------------
# Generic fallback (unknown file types)
# ---------------------------------------------------------------------------

def _skeleton_generic(lines):
    """For unknown file types, return first 20 + last 10 lines."""
    result = []
    total = len(lines)
    head = min(20, total)
    for i in range(head):
        result.append(f"L{i+1}: {lines[i].rstrip()}")
    if total > 30:
        result.append(f"... ({total - 30} lines omitted)")
        for i in range(total - 10, total):
            result.append(f"L{i+1}: {lines[i].rstrip()}")
    elif total > head:
        for i in range(head, total):
            result.append(f"L{i+1}: {lines[i].rstrip()}")
    return result
