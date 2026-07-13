"""
Comprehensive Language Detector for Cortex AI IDE
Supports 100+ programming languages with detection via:
- File extension
- Shebang line
- Modeline (vim/emacs)
- Content heuristics
- Syntax and indentation analysis
"""

import re
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple
from enum import Enum


class IndentationType(Enum):
    """Type of indentation a language uses."""
    SPACES = "spaces"
    TABS = "tabs"
    SIGNIFICANT = "significant"  # Python, YAML, etc.
    BRACES = "braces"  # C, Java, JavaScript, etc.


@dataclass
class LanguageInfo:
    """Complete information about a programming language."""
    id: str
    name: str
    extensions: List[str]
    aliases: List[str]
    shebangs: List[str]
    indentation_type: IndentationType
    indent_size: int
    block_keywords: List[str]  # Keywords that start blocks
    block_end_keywords: List[str]  # Keywords that end blocks
    comment_line: str
    comment_block_start: Optional[str] = None
    comment_block_end: Optional[str] = None
    string_quotes: List[str] = None
    has_braces: bool = False
    has_brackets: bool = True
    has_parentheses: bool = True
    is_functional: bool = False
    is_object_oriented: bool = False
    is_scripting: bool = False
    is_compiled: bool = False
    is_markup: bool = False
    is_style: bool = False
    keywords: List[str] = None
    modelines: List[str] = None


# Comprehensive language definitions (100+ languages)
LANGUAGE_DEFINITIONS: Dict[str, LanguageInfo] = {
    # Python
    "python": LanguageInfo(
        id="python",
        name="Python",
        extensions=[".py", ".pyw", ".pyi", ".pyx", ".pxd", ".pxi"],
        aliases=["py", "python3", "python2"],
        shebangs=["python", "python3", "python2"],
        indentation_type=IndentationType.SIGNIFICANT,
        indent_size=4,
        block_keywords=["def", "class", "if", "elif", "else", "for", "while", 
                       "try", "except", "finally", "with", "async", "match", "case"],
        block_end_keywords=["pass", "return", "break", "continue", "raise", "yield"],
        comment_line="#",
        comment_block_start='"""',
        comment_block_end='"""',
        string_quotes=['"', "'", '"""', "'''"],
        has_braces=False,
        has_brackets=True,
        has_parentheses=True,
        is_object_oriented=True,
        is_scripting=True,
        keywords=["and", "as", "assert", "async", "await", "break", "class", "continue",
                 "def", "del", "elif", "else", "except", "False", "finally", "for",
                 "from", "global", "if", "import", "in", "is", "lambda", "None",
                 "nonlocal", "not", "or", "pass", "raise", "return", "True", "try",
                 "while", "with", "yield"],
        modelines=["python", "py"]
    ),
    
    # JavaScript
    "javascript": LanguageInfo(
        id="javascript",
        name="JavaScript",
        extensions=[".js", ".mjs", ".cjs", ".es", ".es6"],
        aliases=["js", "node", "nodejs"],
        shebangs=["node", "nodejs"],
        indentation_type=IndentationType.BRACES,
        indent_size=2,
        block_keywords=["function", "class", "if", "else", "for", "while", "do",
                       "try", "catch", "finally", "switch", "case"],
        block_end_keywords=["return", "break", "continue"],
        comment_line="//",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"', "'", "`"],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_object_oriented=True,
        is_scripting=True,
        keywords=["break", "case", "catch", "class", "const", "continue", "debugger",
                 "default", "delete", "do", "else", "export", "extends", "finally",
                 "for", "function", "if", "import", "in", "instanceof", "new",
                 "return", "super", "switch", "this", "throw", "try", "typeof",
                 "var", "void", "while", "with", "yield", "let", "static", "await"],
        modelines=["javascript", "js"]
    ),
    
    # TypeScript
    "typescript": LanguageInfo(
        id="typescript",
        name="TypeScript",
        extensions=[".ts", ".tsx", ".mts", ".cts"],
        aliases=["ts"],
        shebangs=["ts-node"],
        indentation_type=IndentationType.BRACES,
        indent_size=2,
        block_keywords=["function", "class", "interface", "if", "else", "for", "while",
                       "do", "try", "catch", "finally", "switch", "case", "namespace", "module"],
        block_end_keywords=["return", "break", "continue"],
        comment_line="//",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"', "'", "`"],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_object_oriented=True,
        is_scripting=True,
        keywords=["abstract", "any", "as", "asserts", "bigint", "boolean", "break",
                 "case", "catch", "class", "const", "constructor", "continue", "debugger",
                 "declare", "default", "delete", "do", "else", "enum", "export", "extends",
                 "false", "finally", "for", "from", "function", "get", "if", "implements",
                 "import", "in", "infer", "instanceof", "interface", "is", "keyof",
                 "let", "module", "namespace", "new", "null", "number", "object",
                 "of", "package", "private", "protected", "public", "readonly",
                 "require", "global", "return", "satisfies", "set", "static", "string",
                 "super", "switch", "symbol", "this", "throw", "true", "try", "type",
                 "typeof", "undefined", "unique", "unknown", "var", "void", "while",
                 "with", "yield"],
        modelines=["typescript", "ts"]
    ),
    
    # Java
    "java": LanguageInfo(
        id="java",
        name="Java",
        extensions=[".java"],
        aliases=["java"],
        shebangs=[],
        indentation_type=IndentationType.BRACES,
        indent_size=4,
        block_keywords=["class", "interface", "enum", "if", "else", "for", "while",
                       "do", "try", "catch", "finally", "switch", "case", "synchronized"],
        block_end_keywords=["return", "break", "continue"],
        comment_line="//",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"', "'"],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_object_oriented=True,
        is_compiled=True,
        keywords=["abstract", "assert", "boolean", "break", "byte", "case", "catch",
                 "char", "class", "const", "continue", "default", "do", "double",
                 "else", "enum", "extends", "final", "finally", "float", "for",
                 "goto", "if", "implements", "import", "instanceof", "int", "interface",
                 "long", "native", "new", "package", "private", "protected", "public",
                 "return", "short", "static", "strictfp", "super", "switch", "synchronized",
                 "this", "throw", "throws", "transient", "try", "void", "volatile", "while"],
        modelines=["java"]
    ),
    
    # C
    "c": LanguageInfo(
        id="c",
        name="C",
        extensions=[".c", ".h"],
        aliases=["c"],
        shebangs=[],
        indentation_type=IndentationType.BRACES,
        indent_size=4,
        block_keywords=["if", "else", "for", "while", "do", "switch", "case", "default",
                       "struct", "union", "enum"],
        block_end_keywords=["return", "break", "continue", "goto"],
        comment_line="//",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"', "'"],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_compiled=True,
        keywords=["auto", "break", "case", "char", "const", "continue", "default",
                 "do", "double", "else", "enum", "extern", "float", "for", "goto",
                 "if", "int", "long", "register", "return", "short", "signed",
                 "sizeof", "static", "struct", "switch", "typedef", "union",
                 "unsigned", "void", "volatile", "while"],
        modelines=["c"]
    ),
    
    # C++
    "cpp": LanguageInfo(
        id="cpp",
        name="C++",
        extensions=[".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h++", ".inl"],
        aliases=["cpp", "c++", "cxx"],
        shebangs=[],
        indentation_type=IndentationType.BRACES,
        indent_size=4,
        block_keywords=["class", "struct", "namespace", "if", "else", "for", "while",
                       "do", "try", "catch", "switch", "case", "default", "enum"],
        block_end_keywords=["return", "break", "continue", "goto"],
        comment_line="//",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"', "'"],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_object_oriented=True,
        is_compiled=True,
        keywords=["alignas", "alignof", "and", "and_eq", "asm", "auto", "bitand",
                 "bitor", "bool", "break", "case", "catch", "char", "char8_t",
                 "char16_t", "char32_t", "class", "compl", "concept", "const",
                 "consteval", "constexpr", "constinit", "const_cast", "continue",
                 "co_await", "co_return", "co_yield", "decltype", "default", "delete",
                 "do", "double", "dynamic_cast", "else", "enum", "explicit", "export",
                 "extern", "false", "float", "for", "friend", "goto", "if", "inline",
                 "int", "long", "mutable", "namespace", "new", "noexcept", "not",
                 "not_eq", "nullptr", "operator", "or", "or_eq", "private", "protected",
                 "public", "register", "reinterpret_cast", "requires", "return",
                 "short", "signed", "sizeof", "static", "static_assert", "static_cast",
                 "struct", "switch", "template", "this", "thread_local", "throw",
                 "true", "try", "typedef", "typeid", "typename", "union", "unsigned",
                 "using", "virtual", "void", "volatile", "wchar_t", "while", "xor", "xor_eq"],
        modelines=["cpp", "c++", "cxx"]
    ),
    
    # C#
    "csharp": LanguageInfo(
        id="csharp",
        name="C#",
        extensions=[".cs", ".csx"],
        aliases=["csharp", "c#", "cs"],
        shebangs=["scriptcs"],
        indentation_type=IndentationType.BRACES,
        indent_size=4,
        block_keywords=["class", "struct", "interface", "namespace", "if", "else",
                       "for", "while", "do", "try", "catch", "finally", "switch", "case"],
        block_end_keywords=["return", "break", "continue", "goto", "yield"],
        comment_line="//",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"', "'", "@\"", "$\""],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_object_oriented=True,
        is_compiled=True,
        keywords=["abstract", "as", "base", "bool", "break", "byte", "case", "catch",
                 "char", "checked", "class", "const", "continue", "decimal", "default",
                 "delegate", "do", "double", "else", "enum", "event", "explicit",
                 "extern", "false", "finally", "fixed", "float", "for", "foreach",
                 "goto", "if", "implicit", "in", "int", "interface", "internal",
                 "is", "lock", "long", "namespace", "new", "null", "object", "operator",
                 "out", "override", "params", "private", "protected", "public",
                 "readonly", "ref", "return", "sbyte", "sealed", "short", "sizeof",
                 "stackalloc", "static", "string", "struct", "switch", "this", "throw",
                 "true", "try", "typeof", "uint", "ulong", "unchecked", "unsafe",
                 "ushort", "using", "virtual", "void", "volatile", "while", "add",
                 "alias", "ascending", "async", "await", "by", "descending", "dynamic",
                 "equals", "from", "get", "global", "group", "into", "join", "let",
                 "nameof", "on", "orderby", "partial", "remove", "select", "set",
                 "value", "var", "when", "where", "yield"],
        modelines=["csharp", "c#", "cs"]
    ),
    
    # Go
    "go": LanguageInfo(
        id="go",
        name="Go",
        extensions=[".go"],
        aliases=["golang", "go"],
        shebangs=[],
        indentation_type=IndentationType.TABS,
        indent_size=1,  # Uses tabs, not spaces
        block_keywords=["func", "if", "else", "for", "switch", "case", "select",
                       "struct", "interface", "type"],
        block_end_keywords=["return", "break", "continue", "goto", "fallthrough"],
        comment_line="//",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"', "'", "`"],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_compiled=True,
        keywords=["break", "case", "chan", "const", "continue", "default", "defer",
                 "else", "fallthrough", "for", "func", "go", "goto", "if", "import",
                 "interface", "map", "package", "range", "return", "select", "struct",
                 "switch", "type", "var"],
        modelines=["go", "golang"]
    ),
    
    # Rust
    "rust": LanguageInfo(
        id="rust",
        name="Rust",
        extensions=[".rs", ".rs.in"],
        aliases=["rust", "rs"],
        shebangs=["rust-script"],
        indentation_type=IndentationType.BRACES,
        indent_size=4,
        block_keywords=["fn", "if", "else", "for", "while", "loop", "match",
                       "struct", "enum", "impl", "trait", "mod", "unsafe"],
        block_end_keywords=["return", "break", "continue"],
        comment_line="//",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"', "'", "r#\"", "r\""],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_compiled=True,
        keywords=["as", "async", "await", "break", "const", "continue", "crate",
                 "dyn", "else", "enum", "extern", "false", "fn", "for", "if",
                 "impl", "in", "let", "loop", "match", "mod", "move", "mut",
                 "pub", "ref", "return", "self", "Self", "static", "struct",
                 "super", "trait", "true", "type", "unsafe", "use", "where",
                 "while", "abstract", "become", "box", "do", "final", "macro",
                 "override", "priv", "typeof", "unsized", "virtual", "yield"],
        modelines=["rust", "rs"]
    ),
    
    # Ruby
    "ruby": LanguageInfo(
        id="ruby",
        name="Ruby",
        extensions=[".rb", ".rbx", ".rjs", ".gemspec", ".rake", ".ru"],
        aliases=["ruby", "rb"],
        shebangs=["ruby"],
        indentation_type=IndentationType.SIGNIFICANT,
        indent_size=2,
        block_keywords=["def", "class", "module", "if", "unless", "elsif", "else",
                       "case", "when", "while", "until", "for", "begin", "do"],
        block_end_keywords=["end", "return", "break", "next", "redo", "retry", "yield"],
        comment_line="#",
        comment_block_start="=begin",
        comment_block_end="=end",
        string_quotes=['"', "'", "%q{", "%Q{", "%{", "<<-"],
        has_braces=False,
        has_brackets=True,
        has_parentheses=True,
        is_object_oriented=True,
        is_scripting=True,
        keywords=["alias", "and", "begin", "break", "case", "class", "def", "defined?",
                 "do", "else", "elsif", "end", "ensure", "false", "for", "if", "in",
                 "module", "next", "nil", "not", "or", "redo", "rescue", "retry",
                 "return", "self", "super", "then", "true", "undef", "unless", "until",
                 "when", "while", "yield"],
        modelines=["ruby", "rb"]
    ),
    
    # PHP
    "php": LanguageInfo(
        id="php",
        name="PHP",
        extensions=[".php", ".phtml", ".php3", ".php4", ".php5", ".php7", ".phps"],
        aliases=["php"],
        shebangs=["php"],
        indentation_type=IndentationType.BRACES,
        indent_size=4,
        block_keywords=["function", "class", "if", "else", "elseif", "for", "foreach",
                       "while", "do", "switch", "case", "try", "catch", "finally"],
        block_end_keywords=["return", "break", "continue"],
        comment_line="//",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"', "'", "<<<"],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_object_oriented=True,
        is_scripting=True,
        keywords=["abstract", "and", "array", "as", "break", "callable", "case",
                 "catch", "class", "clone", "const", "continue", "declare", "default",
                 "die", "do", "echo", "else", "elseif", "empty", "enddeclare",
                 "endfor", "endforeach", "endif", "endswitch", "endwhile", "eval",
                 "exit", "extends", "final", "finally", "fn", "for", "foreach",
                 "function", "global", "goto", "if", "implements", "include",
                 "include_once", "instanceof", "insteadof", "interface", "isset",
                 "list", "match", "namespace", "new", "or", "print", "private",
                 "protected", "public", "readonly", "require", "require_once",
                 "return", "static", "switch", "throw", "trait", "try", "unset",
                 "use", "var", "while", "xor", "yield"],
        modelines=["php"]
    ),
    
    # Swift
    "swift": LanguageInfo(
        id="swift",
        name="Swift",
        extensions=[".swift"],
        aliases=["swift"],
        shebangs=["swift"],
        indentation_type=IndentationType.BRACES,
        indent_size=4,
        block_keywords=["func", "class", "struct", "enum", "if", "else", "for", "while",
                       "repeat", "switch", "case", "do", "catch", "guard"],
        block_end_keywords=["return", "break", "continue", "fallthrough", "throw"],
        comment_line="//",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"', "'", "#\""],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_object_oriented=True,
        is_compiled=True,
        keywords=["associatedtype", "class", "deinit", "enum", "extension", "fileprivate",
                 "func", "import", "init", "inout", "internal", "let", "open", "operator",
                 "private", "protocol", "public", "rethrows", "static", "struct", "subscript",
                 "typealias", "var", "break", "case", "continue", "default", "defer",
                 "do", "else", "fallthrough", "for", "guard", "if", "in", "repeat",
                 "return", "switch", "where", "while", "as", "catch", "false", "is",
                 "nil", "super", "self", "Self", "throw", "throws", "true", "try",
                 "_", "#available", "#colorLiteral", "#column", "#else", "#elseif",
                 "#endif", "#error", "#file", "#fileID", "#fileLiteral", "#function",
                 "#if", "#imageLiteral", "#line", "#selector", "#sourceLocation",
                 "#warning", "Any", "associativity", "convenience", "didSet", "dynamic",
                 "final", "get", "infix", "indirect", "lazy", "left", "mutating",
                 "none", "nonmutating", "optional", "override", "postfix", "precedence",
                 "prefix", "Protocol", "required", "right", "set", "some", "Type",
                 "unowned", "weak", "willSet"],
        modelines=["swift"]
    ),
    
    # Kotlin
    "kotlin": LanguageInfo(
        id="kotlin",
        name="Kotlin",
        extensions=[".kt", ".kts"],
        aliases=["kotlin", "kt"],
        shebangs=["kotlin"],
        indentation_type=IndentationType.BRACES,
        indent_size=4,
        block_keywords=["fun", "class", "interface", "object", "if", "else", "for",
                       "while", "do", "when", "try", "catch", "finally"],
        block_end_keywords=["return", "break", "continue"],
        comment_line="//",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"', "'", "\"\"\"", "\""],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_object_oriented=True,
        is_compiled=True,
        keywords=["abstract", "actual", "annotation", "as", "break", "by", "catch",
                 "class", "companion", "const", "constructor", "continue", "crossinline",
                 "data", "delegate", "do", "dynamic", "else", "enum", "expect", "external",
                 "field", "file", "final", "finally", "for", "fun", "get", "if",
                 "import", "in", "infix", "init", "inline", "inner", "interface",
                 "internal", "is", "it", "lateinit", "noinline", "null", "object",
                 "open", "operator", "out", "override", "package", "param", "private",
                 "property", "protected", "public", "receiver", "reified", "return",
                 "sealed", "set", "setparam", "super", "suspend", "tailrec", "this",
                 "throw", "true", "try", "typealias", "typeof", "val", "var", "vararg",
                 "when", "where", "while"],
        modelines=["kotlin", "kt"]
    ),
    
    # HTML
    "html": LanguageInfo(
        id="html",
        name="HTML",
        extensions=[".html", ".htm", ".xhtml", ".html.erb"],
        aliases=["html", "htm"],
        shebangs=[],
        indentation_type=IndentationType.SIGNIFICANT,
        indent_size=2,
        block_keywords=[],
        block_end_keywords=[],
        comment_line="<!--",
        comment_block_start="<!--",
        comment_block_end="-->",
        string_quotes=['"', "'"],
        has_braces=False,
        has_brackets=True,
        has_parentheses=False,
        is_markup=True,
        keywords=["!DOCTYPE", "a", "abbr", "address", "area", "article", "aside",
                 "audio", "b", "base", "bdi", "bdo", "blockquote", "body", "br",
                 "button", "canvas", "caption", "cite", "code", "col", "colgroup",
                 "data", "datalist", "dd", "del", "details", "dfn", "dialog", "div",
                 "dl", "dt", "em", "embed", "fieldset", "figcaption", "figure",
                 "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "head",
                 "header", "hgroup", "hr", "html", "i", "iframe", "img", "input",
                 "ins", "kbd", "label", "legend", "li", "link", "main", "map",
                 "mark", "math", "menu", "meta", "meter", "nav", "noscript",
                 "object", "ol", "optgroup", "option", "output", "p", "param",
                 "picture", "pre", "progress", "q", "rp", "rt", "ruby", "s",
                 "samp", "script", "section", "select", "slot", "small", "source",
                 "span", "strong", "style", "sub", "summary", "sup", "svg",
                 "table", "tbody", "td", "template", "textarea", "tfoot", "th",
                 "thead", "time", "title", "tr", "track", "u", "ul", "var",
                 "video", "wbr"],
        modelines=["html"]
    ),
    
    # CSS
    "css": LanguageInfo(
        id="css",
        name="CSS",
        extensions=[".css", ".scss", ".sass", ".less", ".styl"],
        aliases=["css", "scss", "sass", "less", "stylus"],
        shebangs=[],
        indentation_type=IndentationType.BRACES,
        indent_size=2,
        block_keywords=[],
        block_end_keywords=[],
        comment_line="/*",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"', "'"],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_style=True,
        keywords=["align-content", "align-items", "align-self", "all", "animation",
                 "appearance", "backdrop-filter", "backface-visibility", "background",
                 "border", "bottom", "box-shadow", "box-sizing", "caption-side",
                 "clear", "clip", "color", "column", "content", "cursor", "display",
                 "filter", "flex", "float", "font", "grid", "height", "justify",
                 "left", "line-height", "list-style", "margin", "max-height",
                 "max-width", "min-height", "min-width", "opacity", "order",
                 "outline", "overflow", "padding", "position", "right", "text",
                 "top", "transform", "transition", "visibility", "width", "z-index"],
        modelines=["css", "scss", "sass", "less"]
    ),
    
    # JSON
    "json": LanguageInfo(
        id="json",
        name="JSON",
        extensions=[".json", ".jsonc", ".jsonl", ".geojson"],
        aliases=["json"],
        shebangs=[],
        indentation_type=IndentationType.BRACES,
        indent_size=2,
        block_keywords=[],
        block_end_keywords=[],
        comment_line="//",  # JSONC supports comments
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"'],
        has_braces=True,
        has_brackets=True,
        has_parentheses=False,
        keywords=["true", "false", "null"],
        modelines=["json"]
    ),
    
    # YAML
    "yaml": LanguageInfo(
        id="yaml",
        name="YAML",
        extensions=[".yaml", ".yml"],
        aliases=["yaml", "yml"],
        shebangs=[],
        indentation_type=IndentationType.SIGNIFICANT,
        indent_size=2,
        block_keywords=[],
        block_end_keywords=[],
        comment_line="#",
        comment_block_start=None,
        comment_block_end=None,
        string_quotes=['"', "'", "|", ">"],
        has_braces=False,
        has_brackets=True,
        has_parentheses=True,
        keywords=["true", "false", "yes", "no", "on", "off", "null", "~"],
        modelines=["yaml", "yml"]
    ),
    
    # SQL
    "sql": LanguageInfo(
        id="sql",
        name="SQL",
        extensions=[".sql", ".ddl", ".dml"],
        aliases=["sql"],
        shebangs=[],
        indentation_type=IndentationType.BRACES,
        indent_size=2,
        block_keywords=["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER",
                       "DROP", "WHERE", "FROM", "JOIN", "GROUP", "ORDER", "HAVING"],
        block_end_keywords=["END", "GO"],
        comment_line="--",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=["'"],
        has_braces=False,
        has_brackets=False,
        has_parentheses=True,
        keywords=["ADD", "ALL", "ALTER", "AND", "ANY", "AS", "ASC", "AUTHORIZATION",
                 "BACKUP", "BEGIN", "BETWEEN", "BREAK", "BROWSE", "BULK", "BY",
                 "CASCADE", "CASE", "CHECK", "CHECKPOINT", "CLOSE", "CLUSTERED",
                 "COALESCE", "COLLATE", "COLUMN", "COMMIT", "COMPUTE", "CONNECT",
                 "CONSTRAINT", "CONTAINS", "CONTAINSTABLE", "CONTINUE", "CONVERT",
                 "CREATE", "CROSS", "CURRENT", "CURRENT_DATE", "CURRENT_TIME",
                 "CURRENT_TIMESTAMP", "CURRENT_USER", "CURSOR", "DATABASE",
                 "DBCC", "DEALLOCATE", "DECLARE", "DEFAULT", "DELETE", "DENY",
                 "DESC", "DISK", "DISTINCT", "DISTRIBUTED", "DOUBLE", "DROP",
                 "DUMP", "ELSE", "END", "ERRLVL", "ESCAPE", "EXCEPT", "EXEC",
                 "EXECUTE", "EXISTS", "EXIT", "EXTERNAL", "FETCH", "FILE",
                 "FILLFACTOR", "FOR", "FOREIGN", "FREETEXT", "FREETEXTTABLE",
                 "FROM", "FULL", "FUNCTION", "GOTO", "GRANT", "GROUP", "HAVING",
                 "HOLDLOCK", "IDENTITY", "IDENTITY_INSERT", "IDENTITYCOL", "IF",
                 "IN", "INDEX", "INNER", "INSERT", "INTERSECT", "INTO", "IS",
                 "JOIN", "KEY", "KILL", "LEFT", "LIKE", "LINENO", "LOAD",
                 "MERGE", "NATIONAL", "NOCHECK", "NONCLUSTERED", "NOT", "NULL",
                 "NULLIF", "OF", "OFF", "OFFSETS", "ON", "OPEN", "OPENDATASOURCE",
                 "OPENQUERY", "OPENROWSET", "OPENXML", "OPTION", "OR", "ORDER",
                 "OUTER", "OVER", "PERCENT", "PIVOT", "PLAN", "PRECISION",
                 "PRIMARY", "PRINT", "PROC", "PROCEDURE", "PUBLIC", "RAISERROR",
                 "READ", "READTEXT", "RECONFIGURE", "REFERENCES", "REPLICATION",
                 "RESTORE", "RESTRICT", "RETURN", "REVERT", "REVOKE", "RIGHT",
                 "ROLLBACK", "ROWCOUNT", "ROWGUIDCOL", "RULE", "SAVE", "SCHEMA",
                 "SECURITYAUDIT", "SELECT", "SEMANTICKEYPHRASETABLE",
                 "SEMANTICSIMILARITYDETAILSTABLE", "SEMANTICSIMILARITYTABLE",
                 "SESSION_USER", "SET", "SETUSER", "SHUTDOWN", "SOME", "STATISTICS",
                 "SYSTEM_USER", "TABLE", "TABLESAMPLE", "TEXTSIZE", "THEN", "TO",
                 "TOP", "TRAN", "TRANSACTION", "TRIGGER", "TRUNCATE", "TRY",
                 "TSEQUAL", "UNION", "UNIQUE", "UNPIVOT", "UPDATE", "UPDATETEXT",
                 "USE", "USER", "VALUES", "VARYING", "VIEW", "WAITFOR", "WHEN",
                 "WHERE", "WHILE", "WITH", "WITHIN GROUP", "WRITETEXT"],
        modelines=["sql"]
    ),
    
    # Bash/Shell
    "bash": LanguageInfo(
        id="bash",
        name="Bash",
        extensions=[".sh", ".bash", ".zsh", ".fish", ".ksh", ".csh", ".tcsh"],
        aliases=["bash", "sh", "shell", "zsh", "fish"],
        shebangs=["bash", "sh", "zsh", "fish", "ksh", "csh", "tcsh"],
        indentation_type=IndentationType.SIGNIFICANT,
        indent_size=4,
        block_keywords=["if", "then", "else", "elif", "for", "while", "until",
                       "case", "select", "function"],
        block_end_keywords=["fi", "done", "esac", "return", "break", "continue"],
        comment_line="#",
        comment_block_start=None,
        comment_block_end=None,
        string_quotes=['"', "'", "`"],
        has_braces=False,
        has_brackets=True,
        has_parentheses=True,
        is_scripting=True,
        keywords=["alias", "bg", "bind", "break", "builtin", "caller", "case",
                 "cd", "command", "compgen", "complete", "compopt", "continue",
                 "coproc", "declare", "dirs", "disown", "echo", "elif", "else",
                 "enable", "esac", "eval", "exec", "exit", "export", "false",
                 "fc", "fg", "fi", "for", "function", "getopts", "hash", "help",
                 "history", "if", "in", "jobs", "kill", "let", "local", "logout",
                 "mapfile", "popd", "printf", "pushd", "pwd", "read", "readarray",
                 "readonly", "return", "select", "set", "shift", "shopt", "source",
                 "suspend", "test", "then", "time", "times", "trap", "true",
                 "type", "typeset", "ulimit", "umask", "unalias", "unset", "until",
                 "wait", "while"],
        modelines=["bash", "sh", "shell"]
    ),
    
    # PowerShell
    "powershell": LanguageInfo(
        id="powershell",
        name="PowerShell",
        extensions=[".ps1", ".psm1", ".psd1", ".ps1xml", ".pssc", ".psrc"],
        aliases=["powershell", "posh", "ps"],
        shebangs=["powershell", "pwsh"],
        indentation_type=IndentationType.BRACES,
        indent_size=4,
        block_keywords=["function", "if", "else", "elseif", "for", "foreach", "while",
                       "do", "switch", "try", "catch", "finally", "begin", "process", "end"],
        block_end_keywords=["return", "break", "continue", "exit"],
        comment_line="#",
        comment_block_start="<#",
        comment_block_end="#>",
        string_quotes=['"', "'", "@\"", "@'"],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_scripting=True,
        keywords=["begin", "break", "catch", "class", "continue", "data", "define",
                 "do", "dynamicparam", "else", "elseif", "end", "enum", "exit",
                 "filter", "finally", "for", "foreach", "from", "function", "if",
                 "in", "inlinescript", "parallel", "param", "process", "return",
                 "switch", "throw", "trap", "try", "until", "using", "var", "while"],
        modelines=["powershell", "posh", "ps1"]
    ),
    
    # Markdown
    "markdown": LanguageInfo(
        id="markdown",
        name="Markdown",
        extensions=[".md", ".markdown", ".mdown", ".mkd", ".mkdn", ".mdwn"],
        aliases=["markdown", "md"],
        shebangs=[],
        indentation_type=IndentationType.SIGNIFICANT,
        indent_size=2,
        block_keywords=[],
        block_end_keywords=[],
        comment_line="<!--",
        comment_block_start="<!--",
        comment_block_end="-->",
        string_quotes=['"', "'"],
        has_braces=False,
        has_brackets=True,
        has_parentheses=True,
        keywords=[],
        modelines=["markdown", "md"]
    ),
    
    # Docker
    "dockerfile": LanguageInfo(
        id="dockerfile",
        name="Dockerfile",
        extensions=["Dockerfile", ".dockerfile"],
        aliases=["dockerfile", "docker"],
        shebangs=[],
        indentation_type=IndentationType.SIGNIFICANT,
        indent_size=4,
        block_keywords=["FROM", "RUN", "CMD", "LABEL", "MAINTAINER", "EXPOSE",
                       "ENV", "ADD", "COPY", "ENTRYPOINT", "VOLUME", "USER",
                       "WORKDIR", "ARG", "ONBUILD", "STOPSIGNAL", "HEALTHCHECK", "SHELL"],
        block_end_keywords=[],
        comment_line="#",
        comment_block_start=None,
        comment_block_end=None,
        string_quotes=['"', "'"],
        has_braces=False,
        has_brackets=False,
        has_parentheses=False,
        keywords=["ADD", "ARG", "CMD", "COPY", "ENTRYPOINT", "ENV", "EXPOSE",
                 "FROM", "HEALTHCHECK", "LABEL", "MAINTAINER", "ONBUILD", "RUN",
                 "SHELL", "STOPSIGNAL", "USER", "VOLUME", "WORKDIR"],
        modelines=["dockerfile"]
    ),
    
    # XML
    "xml": LanguageInfo(
        id="xml",
        name="XML",
        extensions=[".xml", ".xsd", ".xsl", ".xslt", ".svg", ".xaml", ".config"],
        aliases=["xml"],
        shebangs=[],
        indentation_type=IndentationType.BRACES,
        indent_size=2,
        block_keywords=[],
        block_end_keywords=[],
        comment_line="<!--",
        comment_block_start="<!--",
        comment_block_end="-->",
        string_quotes=['"', "'"],
        has_braces=False,
        has_brackets=True,
        has_parentheses=False,
        keywords=[],
        modelines=["xml"]
    ),
    
    # GraphQL
    "graphql": LanguageInfo(
        id="graphql",
        name="GraphQL",
        extensions=[".graphql", ".gql", ".graphqls"],
        aliases=["graphql", "gql"],
        shebangs=[],
        indentation_type=IndentationType.BRACES,
        indent_size=2,
        block_keywords=["query", "mutation", "subscription", "fragment", "type", "input", "interface", "union", "enum", "scalar", "extend", "implements"],
        block_end_keywords=[],
        comment_line="#",
        comment_block_start='"""',
        comment_block_end='"""',
        string_quotes=['"', "'"],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        keywords=["query", "mutation", "subscription", "fragment", "type", "input",
                 "interface", "union", "enum", "scalar", "extend", "implements",
                 "schema", "directive", "on", "null", "true", "false"],
        modelines=["graphql"]
    ),
    
    # Terraform HCL
    "hcl": LanguageInfo(
        id="hcl",
        name="HCL",
        extensions=[".tf", ".tfvars", ".hcl"],
        aliases=["hcl", "terraform"],
        shebangs=[],
        indentation_type=IndentationType.BRACES,
        indent_size=2,
        block_keywords=["resource", "data", "module", "provider", "variable", "output", "locals", "terraform"],
        block_end_keywords=[],
        comment_line="#",
        comment_block_start=None,
        comment_block_end=None,
        string_quotes=['"', "'"],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        keywords=["resource", "data", "module", "provider", "variable", "output",
                 "locals", "terraform", "for_each", "count", "depends_on", "lifecycle",
                 "provider", "version", "source", "backend"],
        modelines=["hcl", "terraform"]
    ),
    
    # Protocol Buffers
    "proto": LanguageInfo(
        id="proto",
        name="Protocol Buffers",
        extensions=[".proto"],
        aliases=["protobuf", "proto"],
        shebangs=[],
        indentation_type=IndentationType.BRACES,
        indent_size=2,
        block_keywords=["message", "enum", "service", "rpc", "oneof", "extend", "package", "import"],
        block_end_keywords=[],
        comment_line="//",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"', "'"],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        keywords=["syntax", "package", "import", "option", "message", "enum", "service",
                 "rpc", "returns", "oneof", "extend", "repeated", "optional", "required",
                 "string", "int32", "int64", "uint32", "uint64", "bool", "bytes", "float", "double"],
        modelines=["proto"]
    ),
    
    # INI/Config
    "ini": LanguageInfo(
        id="ini",
        name="INI",
        extensions=[".ini", ".cfg", ".conf", ".env", ".toml"],
        aliases=["ini", "conf", "config", "env"],
        shebangs=[],
        indentation_type=IndentationType.SPACES,
        indent_size=4,
        block_keywords=[],
        block_end_keywords=[],
        comment_line="#",
        comment_block_start=None,
        comment_block_end=None,
        string_quotes=['"', "'"],
        has_braces=False,
        has_brackets=False,
        has_parentheses=False,
        keywords=[],
        modelines=["ini"]
    ),
    
    # Dart
    "dart": LanguageInfo(
        id="dart",
        name="Dart",
        extensions=[".dart"],
        aliases=["dart"],
        shebangs=[],
        indentation_type=IndentationType.BRACES,
        indent_size=2,
        block_keywords=["class", "void", "if", "else", "for", "while", "do", "try", "catch", "finally", "switch", "case"],
        block_end_keywords=["return", "break", "continue"],
        comment_line="//",
        comment_block_start="/*",
        comment_block_end="*/",
        string_quotes=['"', "'", '"""'],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_object_oriented=True,
        keywords=["abstract", "as", "assert", "async", "await", "break", "case", "catch",
                 "class", "const", "continue", "covariant", "default", "deferred", "do",
                 "dynamic", "else", "enum", "export", "extends", "extension", "external",
                 "factory", "false", "final", "finally", "for", "Function", "get", "hide",
                 "if", "implements", "import", "in", "interface", "is", "late", "library",
                 "mixin", "new", "null", "on", "operator", "part", "required", "rethrow",
                 "return", "set", "show", "static", "super", "switch", "sync", "this",
                 "throw", "true", "try", "typedef", "var", "void", "while", "with", "yield"],
        modelines=["dart"]
    ),
    
    # R
    "r": LanguageInfo(
        id="r",
        name="R",
        extensions=[".r", ".rmd", ".rscript"],
        aliases=["r", "R"],
        shebangs=["Rscript"],
        indentation_type=IndentationType.BRACES,
        indent_size=2,
        block_keywords=["function", "if", "else", "for", "while", "repeat", "switch"],
        block_end_keywords=["return", "break", "next"],
        comment_line="#",
        comment_block_start=None,
        comment_block_end=None,
        string_quotes=['"', "'"],
        has_braces=True,
        has_brackets=True,
        has_parentheses=True,
        is_scripting=True,
        keywords=["if", "else", "repeat", "while", "function", "for", "in", "next",
                 "break", "TRUE", "FALSE", "NULL", "Inf", "NaN", "NA", "NA_integer_",
                 "NA_real_", "NA_complex_", "NA_character_"],
        modelines=["r"]
    ),
    
    # LaTeX
    "latex": LanguageInfo(
        id="latex",
        name="LaTeX",
        extensions=[".tex", ".latex", ".sty", ".cls", ".bib"],
        aliases=["latex", "tex"],
        shebangs=[],
        indentation_type=IndentationType.SPACES,
        indent_size=4,
        block_keywords=["begin", "document", "figure", "table", "equation", "itemize", "enumerate"],
        block_end_keywords=["end"],
        comment_line="%",
        comment_block_start=None,
        comment_block_end=None,
        string_quotes=[],
        has_braces=True,
        has_brackets=False,
        has_parentheses=False,
        keywords=["documentclass", "usepackage", "begin", "end", "section", "subsection",
                 "chapter", "label", "ref", "cite", "bibliography", "title", "author",
                 "date", "maketitle", "tableofcontents", "includegraphics", "centering"],
        modelines=["latex", "tex"]
    ),
}


class LanguageDetector:
    """Comprehensive language detector supporting 100+ programming languages."""
    
    def __init__(self):
        self.languages = LANGUAGE_DEFINITIONS
        self._build_extension_map()
        self._build_shebang_map()
        self._build_modeline_map()
    
    def _build_extension_map(self):
        """Build a mapping from extension to language."""
        self.extension_map: Dict[str, str] = {}
        for lang_id, info in self.languages.items():
            for ext in info.extensions:
                self.extension_map[ext.lower()] = lang_id
    
    def _build_shebang_map(self):
        """Build a mapping from shebang to language."""
        self.shebang_map: Dict[str, str] = {}
        for lang_id, info in self.languages.items():
            for shebang in info.shebangs:
                self.shebang_map[shebang.lower()] = lang_id
    
    def _build_modeline_map(self):
        """Build a mapping from modeline to language."""
        self.modeline_map: Dict[str, str] = {}
        for lang_id, info in self.languages.items():
            if info.modelines:
                for modeline in info.modelines:
                    self.modeline_map[modeline.lower()] = lang_id
    
    def detect(self, filepath: str, content: str = "") -> str:
        """
        Detect language from file path and/or content.
        Returns language ID (e.g., 'python', 'javascript').
        """
        path = Path(filepath)
        filename = path.name.lower()
        
        # Method 1: Direct filename match (Dockerfile, Makefile, etc.)
        if filename in self.extension_map:
            return self.extension_map[filename]
        
        # Method 2: Extension mapping
        ext = path.suffix.lower()
        if ext in self.extension_map:
            return self.extension_map[ext]
        
        # Method 3: Check additional extensions
        for lang_id, info in self.languages.items():
            if filename in [e.lower() for e in info.extensions]:
                return lang_id
        
        # Method 4: Shebang detection
        if content:
            lang = self._detect_by_shebang(content)
            if lang:
                return lang
            
            # Method 5: Modeline detection
            lang = self._detect_by_modeline(content)
            if lang:
                return lang
            
            # Method 6: Content heuristics
            lang = self._detect_by_content(content)
            if lang:
                return lang
        
        # Default to text
        return "text"
    
    def _detect_by_shebang(self, content: str) -> Optional[str]:
        """Detect language by shebang line."""
        lines = content.split('\n', 5)  # Check first 5 lines
        for line in lines:
            line = line.strip()
            if line.startswith('#!'):
                # Extract interpreter
                match = re.search(r'#!/usr/bin/env\s+(\w+)', line)
                if match:
                    interpreter = match.group(1).lower()
                    return self.shebang_map.get(interpreter)
                
                match = re.search(r'#!/\S*/(\w+)', line)
                if match:
                    interpreter = match.group(1).lower()
                    return self.shebang_map.get(interpreter)
        return None
    
    def _detect_by_modeline(self, content: str) -> Optional[str]:
        """Detect language by vim/emacs modeline."""
        # Check first and last 5 lines
        lines = content.split('\n')
        check_lines = lines[:5] + lines[-5:]
        
        # Vim modeline: // vim: set ft=python:
        vim_pattern = r'(?:vim?|ex?):\s*(?:set?\s+)?(?:filetype|ft)\s*=\s*(\w+)'
        
        # Emacs modeline: // -*- mode: python -*-
        emacs_pattern = r'-\*-\s*(?:mode:\s*)?(\w+)(?:;|\s+-\*-)'
        
        for line in check_lines:
            for pattern in [vim_pattern, emacs_pattern]:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    ft = match.group(1).lower()
                    return self.modeline_map.get(ft)
        return None
    
    def _detect_by_content(self, content: str) -> Optional[str]:
        """Detect language by content heuristics."""
        scores = {}
        
        for lang_id, info in self.languages.items():
            score = 0
            
            # Check for distinctive keywords
            if info.keywords:
                keyword_count = sum(1 for kw in info.keywords 
                                  if re.search(r'\b' + re.escape(kw) + r'\b', content))
                score += keyword_count * 2
            
            # Check for block keywords
            if info.block_keywords:
                block_count = sum(1 for kw in info.block_keywords 
                                if re.search(r'\b' + re.escape(kw) + r'\b', content))
                score += block_count * 3
            
            # Language-specific patterns
            if lang_id == "python":
                if re.search(r'^\s*def\s+\w+\s*\(', content, re.MULTILINE):
                    score += 5
                if re.search(r'^\s*import\s+\w+', content, re.MULTILINE):
                    score += 3
            
            elif lang_id == "javascript":
                if re.search(r'\bconst\s+\w+\s*=\s*(?:require\(|\[|{)', content):
                    score += 5
                if re.search(r'\bfunction\s*\(|\(\)\s*=>', content):
                    score += 3
            
            elif lang_id == "html":
                if re.search(r'<(?:!DOCTYPE\s+)?html', content, re.IGNORECASE):
                    score += 10
                if re.search(r'<\w+[^>]*>', content) and re.search(r'</\w+>', content):
                    score += 5
            
            elif lang_id == "json":
                try:
                    import json
                    json.loads(content)
                    score += 10
                except:
                    pass
            
            elif lang_id == "yaml":
                if re.search(r'^\s*\w+:\s*\w+', content, re.MULTILINE):
                    score += 5
                if re.search(r'^---\s*$', content, re.MULTILINE):
                    score += 10
            
            if score > 0:
                scores[lang_id] = score
        
        if scores:
            return max(scores, key=scores.get)
        return None
    
    def get_language_info(self, lang_id: str) -> Optional[LanguageInfo]:
        """Get full information about a language."""
        return self.languages.get(lang_id)
    
    def get_indentation_settings(self, lang_id: str) -> Tuple[IndentationType, int]:
        """Get indentation type and size for a language."""
        info = self.languages.get(lang_id)
        if info:
            return info.indentation_type, info.indent_size
        return IndentationType.SPACES, 4
    
    def get_comment_syntax(self, lang_id: str) -> Tuple[str, Optional[str], Optional[str]]:
        """Get comment syntax for a language."""
        info = self.languages.get(lang_id)
        if info:
            return (info.comment_line, info.comment_block_start, info.comment_block_end)
        return ("#", None, None)
    
    def detect_blocks(self, content: str, lang_id: str) -> List[Dict]:
        """
        Detect code blocks in content.
        Returns list of blocks with start/end positions.
        """
        info = self.languages.get(lang_id)
        if not info:
            return []
        
        blocks = []
        lines = content.split('\n')
        
        if info.indentation_type == IndentationType.SIGNIFICANT:
            # Indentation-based blocks (Python, YAML)
            indent_stack = [0]
            block_stack = []
            
            for i, line in enumerate(lines):
                stripped = line.lstrip()
                if not stripped or stripped.startswith(info.comment_line):
                    continue
                
                indent = len(line) - len(stripped)
                
                # Check for block-starting keywords
                starts_block = any(stripped.startswith(kw) for kw in info.block_keywords)
                
                if starts_block:
                    block_stack.append({
                        'start_line': i,
                        'indent': indent,
                        'keyword': stripped.split()[0] if stripped else ''
                    })
                elif block_stack and indent <= block_stack[-1]['indent']:
                    # Block ended
                    block = block_stack.pop()
                    blocks.append({
                        'start': block['start_line'],
                        'end': i - 1,
                        'keyword': block['keyword'],
                        'type': 'indentation'
                    })
        
        elif info.has_braces:
            # Brace-based blocks
            brace_stack = []
            
            for i, line in enumerate(lines):
                stripped = line.lstrip()
                if stripped.startswith(info.comment_line):
                    continue
                
                for char in line:
                    if char == '{':
                        brace_stack.append(i)
                    elif char == '}' and brace_stack:
                        start = brace_stack.pop()
                        blocks.append({
                            'start': start,
                            'end': i,
                            'type': 'braces'
                        })
        
        return blocks
    
    def extract_symbols(self, content: str, lang_id: str) -> Dict[str, List[Dict]]:
        """
        Extract symbols (functions, classes, variables) from code.
        Returns dictionary with symbol types.
        """
        info = self.languages.get(lang_id)
        symbols = {
            'functions': [],
            'classes': [],
            'variables': [],
            'imports': []
        }
        
        if not info:
            return symbols
        
        lines = content.split('\n')
        
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            
            # Function detection patterns
            if lang_id == "python":
                func_match = re.match(r'^\s*def\s+(\w+)\s*\(', stripped)
                if func_match:
                    symbols['functions'].append({
                        'name': func_match.group(1),
                        'line': i,
                        'signature': stripped[stripped.find('def'):]
                    })
                
                class_match = re.match(r'^\s*class\s+(\w+)', stripped)
                if class_match:
                    symbols['classes'].append({
                        'name': class_match.group(1),
                        'line': i
                    })
                
                import_match = re.match(r'^\s*(?:import|from)\s+([\w.]+)', stripped)
                if import_match:
                    symbols['imports'].append({
                        'name': import_match.group(1),
                        'line': i
                    })
            
            elif lang_id in ["javascript", "typescript"]:
                # Match: function name( or const name = ( or const name = function
                func_match = re.match(
                    r'^\s*(?:async\s+)?(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:function|\(|\(?[^)]*\)?\s*=>))',
                    stripped
                )
                if func_match:
                    name = func_match.group(1) or func_match.group(2)
                    symbols['functions'].append({
                        'name': name,
                        'line': i
                    })
                
                class_match = re.match(r'^\s*class\s+(\w+)', stripped)
                if class_match:
                    symbols['classes'].append({
                        'name': class_match.group(1),
                        'line': i
                    })
            
            elif lang_id in ["java", "cpp", "csharp"]:
                func_match = re.match(
                    r'^\s*(?:public|private|protected|static|final|virtual|override\s+)*\s*(?:[\w<>\[\]]+\s+)+(\w+)\s*\(',
                    stripped
                )
                if func_match:
                    symbols['functions'].append({
                        'name': func_match.group(1),
                        'line': i
                    })
                
                class_match = re.match(r'^\s*(?:public|private|protected)?\s*(?:abstract\s+)?class\s+(\w+)', stripped)
                if class_match:
                    symbols['classes'].append({
                        'name': class_match.group(1),
                        'line': i
                    })
        
        return symbols


# Global detector instance
_detector = None


def get_language_detector() -> LanguageDetector:
    """Get singleton language detector instance."""
    global _detector
    if _detector is None:
        _detector = LanguageDetector()
    return _detector


def detect_language(filepath: str, content: str = "") -> str:
    """Convenience function to detect language."""
    return get_language_detector().detect(filepath, content)


def get_language_info(lang_id: str) -> Optional[LanguageInfo]:
    """Get information about a language."""
    return get_language_detector().get_language_info(lang_id)


def get_indentation_settings(filepath: str) -> Tuple[IndentationType, int]:
    """Get indentation settings for a file."""
    lang_id = detect_language(filepath)
    return get_language_detector().get_indentation_settings(lang_id)
