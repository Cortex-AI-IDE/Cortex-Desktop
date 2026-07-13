"""
syntax_highlight.py — Code syntax highlighting for Qt Rich Text
================================================================

Converts code blocks to syntax-highlighted HTML using inline styles
that Qt's QTextBrowser can render.

Supports: Python, JavaScript, TypeScript, JSON, HTML, CSS, Bash, Rust, Go,
          C/C++, SQL, YAML, TOML, Dockerfile, PowerShell, Ruby, PHP, Swift,
          Kotlin, Java, C#, Lua, Perl, R, Scala, Haskell, Elixir, Zig, Nim

Colors sourced from OpenCode OC-2 dark theme design tokens.
Style: italic keywords, bold types, bold+italic type keywords (OpenCode TUI).
"""

from __future__ import annotations
import re

# ── Color tokens — theme-aware ──
# Two palettes; _sync_palette() points the module-level C_* names at the
# ACTIVE theme's palette before each highlight run.
#
# Bug history: only the OpenCode OC-2 DARK palette existed — comments were
# rgba(255,255,255,0.42) white-translucent, variables near-white, types pale
# yellow. Rendered on the LIGHT warm background those spans were invisible
# ("#commenting still using light fonts in light mode").

# DARK — OpenCode OC-2 (reference: opencode packages/ui/src/styles/theme.css)
_DARK_PALETTE = {
    "C_KEYWORD":     "#c678dd",                   # Purple — control flow keywords
    "C_STRING":      "#00ceb9",                   # Teal — string literals
    "C_COMMENT":     "rgba(255,255,255,0.422)",   # Muted italic — comments
    "C_FUNCTION":    "#61afef",                   # Blue — function/method names
    "C_NUMBER":      "#ffba92",                   # Peach — numbers, booleans
    "C_TYPE":        "#ecf58c",                   # Yellow-green — types, classes, decorators
    "C_OPERATOR":    "#abb2bf",                   # Light gray — operators
    "C_PROPERTY":    "#ff9ae2",                   # Pink — object properties, JSON keys
    "C_BUILTIN":     "#f85149",                   # Red — builtins
    "C_PUNCTUATION": "rgba(255,255,255,0.618)",   # Muted — brackets, delimiters
    "C_VARIABLE":    "rgba(255,255,255,0.936)",   # Bright — variables, parameters
    "C_CONSTANT":    "#d19a66",                   # Orange — constants
    "C_REGEX":       "#56b6c2",                   # Cyan — regex patterns
    "C_ESCAPE":      "#56b6c2",                   # Cyan — escape sequences
    "C_TAG":         "#e06c75",                   # Coral — HTML/XML tags
    "C_ATTR":        "#d19a66",                   # Orange — HTML attributes
    "C_SELECTOR":    "#61afef",                   # Blue — CSS selectors
    "C_VALUE":       "#00ceb9",                   # Teal — CSS values
    "C_HEADING":     "#e5c07b",                   # Gold — markdown headings
    "C_decorator":   "#ecf58c",                   # Yellow-green — decorators/annotations
    "C_namespace":   "#ecf58c",                   # Yellow-green — namespaces/modules
}

# LIGHT — dark inks readable on the warm Claude page (#ECE9E0 / #F4F1EA)
_LIGHT_PALETTE = {
    "C_KEYWORD":     "#8250df",                   # Purple — control flow keywords
    "C_STRING":      "#0f7b6c",                   # Deep teal — string literals
    "C_COMMENT":     "rgba(26,24,20,0.48)",       # Muted warm-dark italic — comments
    "C_FUNCTION":    "#0550ae",                   # Deep blue — function/method names
    "C_NUMBER":      "#953800",                   # Burnt orange — numbers, booleans
    "C_TYPE":        "#9a6700",                   # Dark gold — types, classes, decorators
    "C_OPERATOR":    "#57606a",                   # Slate gray — operators
    "C_PROPERTY":    "#bf3989",                   # Deep pink — object properties, JSON keys
    "C_BUILTIN":     "#cf222e",                   # Red — builtins
    "C_PUNCTUATION": "rgba(26,24,20,0.62)",       # Muted — brackets, delimiters
    "C_VARIABLE":    "rgba(26,24,20,0.92)",       # Warm near-black — variables, parameters
    "C_CONSTANT":    "#953800",                   # Burnt orange — constants
    "C_REGEX":       "#0c7189",                   # Deep cyan — regex patterns
    "C_ESCAPE":      "#0c7189",                   # Deep cyan — escape sequences
    "C_TAG":         "#cf222e",                   # Red — HTML/XML tags
    "C_ATTR":        "#953800",                   # Burnt orange — HTML attributes
    "C_SELECTOR":    "#0550ae",                   # Deep blue — CSS selectors
    "C_VALUE":       "#0f7b6c",                   # Deep teal — CSS values
    "C_HEADING":     "#9a6700",                   # Dark gold — markdown headings
    "C_decorator":   "#9a6700",                   # Dark gold — decorators/annotations
    "C_namespace":   "#9a6700",                   # Dark gold — namespaces/modules
}


def _sync_palette() -> None:
    """Bind the C_* module globals to the ACTIVE theme's palette.

    Called at the top of every highlight entry point so inline colors are
    baked from the theme that is live at RENDER time. Falls back to dark
    when tokens are unavailable (e.g. standalone/testing use).
    """
    try:
        from src.ui.tokens import TOKENS as _T, DARK as _D
        pal = _DARK_PALETTE if _T['bg'] == _D['bg'] else _LIGHT_PALETTE
    except Exception:
        pal = _DARK_PALETTE
    globals().update(pal)


# Default binding (dark) so the names always exist at import time.
globals().update(_DARK_PALETTE)


def _wrap(color: str, text: str, extra_style: str = "") -> str:
    style = f"color:{color}"
    if extra_style:
        style += ";" + extra_style
    return f'<span style="{style}">{text}</span>'

def _wrap_italic(color: str, text: str) -> str:
    """OpenCode style: keywords get italic."""
    return _wrap(color, text, "font-style:italic")

def _wrap_bold(color: str, text: str) -> str:
    """OpenCode style: types get bold."""
    return _wrap(color, text, "font-weight:600")

def _wrap_bold_italic(color: str, text: str) -> str:
    """OpenCode style: type keywords get bold+italic."""
    return _wrap(color, text, "font-weight:600;font-style:italic")


# ── Language-specific patterns ──
_LANG_PATTERNS: dict[str, dict[str, re.Pattern]] = {
    "python": {
        "keywords": re.compile(
            r'\b(def|class|if|elif|else|for|while|return|import|from|as'
            r'|try|except|finally|with|yield|async|await|raise|pass'
            r'|break|continue|and|or|not|in|is|lambda'
            r'|None|True|False|global|nonlocal|del|assert)\b'),
        "type_keywords": re.compile(
            r'\b(class)\b'),
        "builtins": re.compile(
            r'\b(print|len|range|int|str|float|list|dict|set|tuple|bool'
            r'|type|isinstance|hasattr|getattr|setattr|super|enumerate'
            r'|zip|map|filter|sorted|reversed|open|input|round|abs|min|max'
            r'|sum|any|all|dir|vars|id|hex|oct|bin|chr|ord|repr|format'
            r'|bytes|bytearray|memoryview|frozenset|property|staticmethod'
            r'|classmethod|Exception|ValueError|TypeError|KeyError'
            r'|IndexError|AttributeError|RuntimeError|OSError|IOError'
            r'|StopIteration|FileNotFoundError|PermissionError'
            r'|ImportError|ModuleNotFoundError)\b'),
        "strings": re.compile(
            r'(f?"[^"\\]*(?:\\.[^"\\]*)*"|f?\'[^\'\\]*(?:\\.[^\'\\]*)*\')'),
        "comments": re.compile(r'(#.*?)(?=\n|$)'),
        "functions": re.compile(r'\b([a-zA-Z_]\w*)\s*(?=\()'),
        "numbers": re.compile(r'\b(\d+\.?\d*(?:e[+-]?\d+)?)\b'),
        "decorators": re.compile(r'(@\w+)'),
        "operators": re.compile(r'(->|>>|<<|[+\-*/%&|^~<>!=]=?|:=)'),
    },
    "javascript": {
        "keywords": re.compile(
            r'\b(const|let|var|function|return|if|else|for|while|do'
            r'|switch|case|break|continue|class|extends|new|this'
            r'|import|export|from|default|async|await|try|catch|finally'
            r'|throw|typeof|instanceof|void|delete|in|of|yield'
            r'|null|undefined|true|false|NaN|Infinity)\b'),
        "type_keywords": re.compile(
            r'\b(class|extends|implements|interface)\b'),
        "builtins": re.compile(
            r'\b(console|document|window|Math|JSON|Promise|Array|Object'
            r'|String|Number|Boolean|Date|RegExp|Error|Map|Set'
            r'|WeakMap|WeakSet|Symbol|parseInt|parseFloat|isNaN|isFinite'
            r'|setTimeout|setInterval|clearTimeout|clearInterval|fetch'
            r'|require|module|exports|process)\b'),
        "strings": re.compile(
            r'(`[^`]*`|"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')'),
        "comments": re.compile(r'(//.*?)(?=\n|$)|(/\*[\s\S]*?\*/)'),
        "functions": re.compile(r'\b([a-zA-Z_]\w*)\s*(?=\()'),
        "numbers": re.compile(r'\b(\d+\.?\d*(?:e[+-]?\d+)?)\b'),
        "operators": re.compile(r'(===|!==|=>|>>|<<|[+\-*/%&|^~<>!=?:]=?)'),
    },
    "typescript": {
        "keywords": re.compile(
            r'\b(const|let|var|function|return|if|else|for|while|do'
            r'|switch|case|break|continue|class|extends|new|this'
            r'|import|export|from|default|async|await|try|catch|finally'
            r'|throw|typeof|instanceof|void|delete|in|of|yield'
            r'|interface|type|enum|implements|abstract|readonly|private'
            r'|public|protected|static|declare|namespace|module'
            r'|keyof|infer|never|unknown|any|null|undefined|true|false)\b'),
        "type_keywords": re.compile(
            r'\b(interface|type|enum|implements|abstract|class|extends)\b'),
        "builtins": re.compile(
            r'\b(console|document|window|Math|JSON|Promise|Array|Object'
            r'|String|Number|Boolean|Date|RegExp|Error|Map|Set'
            r'|WeakMap|WeakSet|Symbol|parseInt|parseFloat|isNaN|isFinite'
            r'|setTimeout|setInterval|fetch|require)\b'),
        "strings": re.compile(
            r'(`[^`]*`|"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')'),
        "comments": re.compile(r'(//.*?)(?=\n|$)|(/\*[\s\S]*?\*/)'),
        "functions": re.compile(r'\b([a-zA-Z_]\w*)\s*(?=\()'),
        "numbers": re.compile(r'\b(\d+\.?\d*(?:e[+-]?\d+)?)\b'),
        "operators": re.compile(r'(===|!==|=>|>>|<<|[+\-*/%&|^~<>!=?:]=?)'),
    },
    "java": {
        "keywords": re.compile(
            r'\b(abstract|assert|boolean|break|byte|case|catch|char|class'
            r'|const|continue|default|do|double|else|enum|extends|final'
            r'|finally|float|for|goto|if|implements|import|instanceof|int'
            r'|interface|long|native|new|package|private|protected|public'
            r'|return|short|static|strictfp|super|switch|synchronized|this'
            r'|throw|throws|transient|try|void|volatile|while'
            r'|true|false|null|var|yield|record|sealed|permits)\b'),
        "type_keywords": re.compile(
            r'\b(class|interface|enum|extends|implements|permits|sealed)\b'),
        "builtins": re.compile(
            r'\b(System|String|Integer|Long|Double|Float|Boolean|Character'
            r'|Byte|Short|Object|Class|Math|Arrays|Collections|List|ArrayList'
            r'|Map|HashMap|Set|HashSet|Queue|LinkedList|Stack|Vector'
            r'|Thread|Runnable|Exception|RuntimeException|IOException'
            r'|StringBuilder|StringBuffer|Comparable|Iterable|Iterator)\b'),
        "strings": re.compile(r'("[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')'),
        "comments": re.compile(r'(//.*?)(?=\n|$)|(/\*[\s\S]*?\*/)'),
        "functions": re.compile(r'\b([a-zA-Z_]\w*)\s*(?=\()'),
        "numbers": re.compile(r'\b(\d+\.?\d*(?:e[+-]?\d+)?[fFdDlL]?)\b'),
        "decorators": re.compile(r'(@\w+)'),
        "operators": re.compile(r'(->|>>|<<|[+\-*/%&|^~<>!=?:]=?)'),
    },
    "csharp": {
        "keywords": re.compile(
            r'\b(abstract|as|base|bool|break|byte|case|catch|char|checked'
            r'|class|const|continue|decimal|default|delegate|do|double'
            r'|else|enum|event|explicit|extern|false|finally|fixed|float'
            r'|for|foreach|goto|if|implicit|in|int|interface|internal|is'
            r'|lock|long|namespace|new|null|object|operator|out|override'
            r'|params|private|protected|public|readonly|ref|return|sbyte'
            r'|sealed|short|sizeof|stackalloc|static|string|struct|switch'
            r'|this|throw|true|try|typeof|uint|ulong|unchecked|unsafe'
            r'|ushort|using|var|virtual|void|volatile|while'
            r'|async|await|when|where|yield|record|init|required|scoped)\b'),
        "type_keywords": re.compile(
            r'\b(class|interface|enum|struct|record|delegate)\b'),
        "builtins": re.compile(
            r'\b(Console|String|Int32|Int64|Double|Float|Boolean|Object'
            r'|Array|List|Dictionary|IEnumerable|Task|Exception'
            r'|Math|DateTime|Guid|Uri|Regex|StringBuilder)\b'),
        "strings": re.compile(r'(@"[^"]*"|"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')'),
        "comments": re.compile(r'(//.*?)(?=\n|$)|(/\*[\s\S]*?\*/)'),
        "functions": re.compile(r'\b([a-zA-Z_]\w*)\s*(?=\()'),
        "numbers": re.compile(r'\b(\d+\.?\d*(?:e[+-]?\d+)?[fFdDmM]?)\b'),
        "decorators": re.compile(r'\[(\w+)\]'),
        "operators": re.compile(r'(=>|>>|<<|[+\-*/%&|^~<>!=?:]=?)'),
    },
    "json": {
        "keys": re.compile(r'"([^"]+)"\s*(?=:)'),
        "strings": re.compile(r':\s*"([^"]*)"'),
        "numbers": re.compile(r':\s*(\d+\.?\d*)'),
        "bools": re.compile(r':\s*(true|false|null)'),
    },
    "bash": {
        "keywords": re.compile(
            r'\b(if|then|else|elif|fi|for|while|do|done|case|esac'
            r'|function|return|exit|echo|export|source|alias|cd|ls|grep'
            r'|sed|awk|cat|mkdir|rm|cp|mv|chmod|chown|sudo|apt|pip|npm'
            r'|git|docker|curl|wget|ssh|scp|rsync|tar|gzip|find|xargs'
            r'|head|tail|sort|uniq|wc|cut|tr|tee|env|set|unset|readonly'
            r'|shift|trap|kill|sleep|wait|bg|fg|jobs|disown)\b'),
        "strings": re.compile(r'("[^"]*"|\'[^\']*\')'),
        "comments": re.compile(r'(#.*?)(?=\n|$)'),
        "variables": re.compile(r'(\$\{?\w+\}?)'),
        "operators": re.compile(r'([|&><;]=?|&&|\|\|)'),
    },
    "powershell": {
        "keywords": re.compile(
            r'\b(Begin|Break|Catch|Class|Continue|Data|Define|Do|DynamicParam'
            r'|Else|ElseIf|End|Exit|Filter|Finally|For|ForEach|From|Function'
            r'|If|In|InlineScript|Param|Process|Return|Switch|Throw|Trap'
            r'|Try|Until|Using|While|Workflow)\b', re.IGNORECASE),
        "builtins": re.compile(
            r'\b(Write-Host|Write-Output|Write-Error|Write-Warning|Write-Verbose'
            r'|Get-Content|Set-Content|Add-Content|Get-ChildItem|Get-Item'
            r'|Set-Item|New-Item|Remove-Item|Copy-Item|Move-Item'
            r'|Get-Process|Stop-Process|Start-Process|Invoke-Command'
            r'|Invoke-Expression|Import-Module|Export-Module'
            r'|\$true|\$false|\$null)\b', re.IGNORECASE),
        "strings": re.compile(r'("[^"]*"|\'[^\']*\')'),
        "comments": re.compile(r'(#.*?)(?=\n|$)|(<#[\s\S]*?#>)'),
        "variables": re.compile(r'(\$\w+)'),
        "operators": re.compile(r'(-[a-zA-Z]+|[|&><;]=?|&&|\|\|)'),
    },
    "rust": {
        "keywords": re.compile(
            r'\b(fn|let|mut|const|if|else|for|while|loop|match|return'
            r'|struct|enum|impl|trait|pub|use|mod|crate|self|super'
            r'|async|await|move|ref|type|where|as|in|unsafe|extern'
            r'|static|true|false|dyn|box|macro_rules|union)\b'),
        "type_keywords": re.compile(
            r'\b(struct|enum|trait|type|impl|union)\b'),
        "builtins": re.compile(
            r'\b(Some|None|Ok|Err|Vec|String|Option|Result|HashMap'
            r'|HashSet|Box|Rc|Arc|Cell|RefCell|Mutex|RwLock|println'
            r'|format|panic|assert|assert_eq|assert_ne|dbg|include'
            r'|include_str|include_bytes|env|cfg|todo|unimplemented'
            r'|unreachable|eprintln|write|writeln|thread|spawn)\b'),
        "strings": re.compile(r'("[^"]*"|r#"[\\s\\S]*?"#)'),
        "comments": re.compile(r'(//.*?)(?=\n|$)|(/\*[\s\S]*?\*/)'),
        "functions": re.compile(r'\b([a-zA-Z_]\w*)\s*(?=\()'),
        "types": re.compile(r'\b([A-Z]\w*)\b'),
        "numbers": re.compile(
            r'\b(\d+\.?\d*(?:_\d+)*(?:f32|f64|u8|u16|u32|u64|i8|i16|i32|i64|usize|isize)?)\b'),
        "operators": re.compile(r'(->|=>|::|>>|<<|[+\-*/%&|^~<>!=?:]=?)'),
    },
    "go": {
        "keywords": re.compile(
            r'\b(func|package|import|return|if|else|for|range|switch'
            r'|case|default|break|continue|go|defer|chan|select|struct'
            r'|interface|map|type|var|const|nil|true|false|make|new'
            r'|append|len|cap|copy|delete|close|panic|recover'
            r'|fallthrough|goto)\b'),
        "type_keywords": re.compile(
            r'\b(struct|interface|type)\b'),
        "builtins": re.compile(
            r'\b(fmt|Println|Printf|Sprintf|Errorf|Fprintf|Fprintln'
            r'|Scan|Scanf|Scanln|Sscan|Sscanf|Sscanln|Fscan|Fscanf'
            r'|Fscanln|strings|strconv|json|http|os|io|ioutil|bufio'
            r'|context|time|sync|math|sort|errors|log|net|path|filepath'
            r'|regexp|runtime|crypto|encoding|flag|reflect|testing'
            r'|unicode|unsafe|bytes|compress|container|database|debug'
            r'|embed|expvar|hash|html|image|index|mime|plugin|text'
            r'|archive)\b'),
        "strings": re.compile(
            r'("[^"\\]*(?:\\.[^"\\]*)*"|`[^`]*`'
            r'|\'[^\'\\]*(?:\\.[^\'\\]*)*\')'),
        "comments": re.compile(r'(//.*?)(?=\n|$)|(/\*[\s\S]*?\*/)'),
        "functions": re.compile(r'\b([a-zA-Z_]\w*)\s*(?=\()'),
        "numbers": re.compile(r'\b(\d+\.?\d*(?:e[+-]?\d+)?)\b'),
        "operators": re.compile(r'(:=|>>|<<|[+\-*/%&|^~<>!=?:]=?)'),
    },
    "c": {
        "keywords": re.compile(
            r'\b(int|char|float|double|void|long|short|unsigned|signed'
            r'|struct|union|enum|typedef|sizeof|return|if|else|for|while'
            r'|do|switch|case|default|break|continue|goto|static|extern'
            r'|const|volatile|register|auto|inline|restrict'
            r'|NULL|true|false|bool|complex|imaginary)\b'),
        "type_keywords": re.compile(
            r'\b(struct|union|enum|typedef)\b'),
        "builtins": re.compile(
            r'\b(printf|scanf|fprintf|sprintf|snprintf|fscanf|sscanf'
            r'|malloc|calloc|realloc|free|memcpy|memset|memmove|memcmp'
            r'|strlen|strcpy|strncpy|strcmp|strncmp|strcat|strncat'
            r'|strchr|strrchr|strstr|strtok|fopen|fclose|fread|fwrite'
            r'|fseek|ftell|rewind|fflush|fprintf|fscanf|fgets|fputs'
            r'|getc|putc|getchar|putchar|gets|puts|ungetc|perror'
            r'|assert|exit|atexit|abort|system|qsort|bsearch|rand|srand'
            r'|clock|time|difftime|mktime|strftime|signal|raise)\b'),
        "strings": re.compile(
            r'("[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')'),
        "comments": re.compile(r'(//.*?)(?=\n|$)|(/\*[\s\S]*?\*/)'),
        "functions": re.compile(r'\b([a-zA-Z_]\w*)\s*(?=\()'),
        "numbers": re.compile(
            r'\b(0x[0-9a-fA-F]+|0b[01]+|\d+\.?\d*(?:e[+-]?\d+)?[fFLuUlL]*)\b'),
        "operators": re.compile(r'(->|>>|<<|[+\-*/%&|^~<>!=?:]=?)'),
        "preprocessor": re.compile(r'(#\s*(?:include|define|ifdef|ifndef|endif'
                                  r'|if|else|elif|pragma|error|warning|undef|line)\b)'),
    },
    "css": {
        "properties": re.compile(r'([a-z-]+)\s*(?=:)'),
        "values": re.compile(r':\s*([^;{}]+)'),
        "selectors": re.compile(r'([.#@:][a-zA-Z][\w-]*)'),
        "numbers": re.compile(r'(\d+\.?\d*(?:px|em|rem|%|vh|vw|s|ms|deg|fr)?)'),
        "comments": re.compile(r'(/\*[\s\S]*?\*/)'),
    },
    "html": {
        "tags": re.compile(r'(</?)([\w-]+)([\s>])'),
        "attrs": re.compile(r'\s([\w-]+)(?=\s*=)'),
        "strings": re.compile(r'=\s*("[^"]*"|\'[^\']*\')'),
        "comments": re.compile(r'(<!--[\s\S]*?-->)'),
    },
    "sql": {
        "keywords": re.compile(
            r'\b(SELECT|FROM|WHERE|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP'
            r'|TABLE|INDEX|INTO|VALUES|SET|JOIN|LEFT|RIGHT|INNER|OUTER'
            r'|ON|AND|OR|NOT|NULL|IS|IN|LIKE|BETWEEN|ORDER|BY|GROUP'
            r'|HAVING|LIMIT|OFFSET|UNION|ALL|DISTINCT|AS|CASE|WHEN|THEN'
            r'|ELSE|END|EXISTS|COUNT|SUM|AVG|MIN|MAX|PRIMARY|KEY|FOREIGN'
            r'|REFERENCES|CONSTRAINT|DEFAULT|CHECK|UNIQUE|AUTO_INCREMENT'
            r'|CASCADE|TRUNCATE|BEGIN|COMMIT|ROLLBACK|TRANSACTION'
            r'|ASC|DESC|GRANT|REVOKE|EXEC|EXECUTE|PROCEDURE|FUNCTION'
            r'|TRIGGER|VIEW|IF|REPLACE|RENAME|ADD|COLUMN|MODIFY|CHANGE'
            r'|DATABASE|SCHEMA|USE|SHOW|DESCRIBE|EXPLAIN)\b', re.IGNORECASE),
        "strings": re.compile(r"('[^']*')"),
        "numbers": re.compile(r'\b(\d+\.?\d*)\b'),
        "comments": re.compile(r'(--.*?)(?=\n|$)|(/\*[\s\S]*?\*/)'),
    },
    "yaml": {
        "keys": re.compile(r'^(\s*[\w.-]+)\s*(?=:)', re.MULTILINE),
        "strings": re.compile(r':\s*(".*?"|\'.*?\'|[^\n]+)'),
        "numbers": re.compile(r':\s*(\d+\.?\d*)'),
        "bools": re.compile(r':\s*(true|false|null|yes|no|on|off)\b', re.IGNORECASE),
        "comments": re.compile(r'(#.*?)(?=\n|$)'),
        "operators": re.compile(r'(-{3}|\.{3})'),
    },
    "toml": {
        "keys": re.compile(r'^(\s*[\w.-]+)\s*(?==)', re.MULTILINE),
        "sections": re.compile(r'^(\[[\w.-]+\])', re.MULTILINE),
        "strings": re.compile(r'=\s*(".*?"|\'.*?\')'),
        "numbers": re.compile(r'=\s*(\d+\.?\d*)'),
        "bools": re.compile(r'=\s*(true|false)\b', re.IGNORECASE),
        "comments": re.compile(r'(#.*?)(?=\n|$)'),
    },
    "dockerfile": {
        "keywords": re.compile(
            r'^(FROM|RUN|CMD|LABEL|MAINTAINER|EXPOSE|ENV|ADD|COPY'
            r'|ENTRYPOINT|VOLUME|USER|WORKDIR|ARG|ONBUILD|STOPSIGNAL'
            r'|HEALTHCHECK|SHELL)\b', re.MULTILINE),
        "strings": re.compile(r'("[^"]*"|\'[^\']*\')'),
        "comments": re.compile(r'(#.*?)(?=\n|$)'),
        "variables": re.compile(r'(\$\{?\w+\}?)'),
    },
    "ruby": {
        "keywords": re.compile(
            r'\b(alias|and|begin|break|case|class|def|defined\?|do|else'
            r'|elsif|end|ensure|false|for|if|in|module|next|nil|not|or'
            r'|redo|rescue|retry|return|self|super|then|true|undef|unless'
            r'|until|when|while|yield)\b'),
        "builtins": re.compile(
            r'\b(Array|Hash|String|Integer|Float|Numeric|NilClass|TrueClass'
            r'|FalseClass|Object|Kernel|Math|IO|File|Dir|Regexp|Range'
            r'|Proc|Method|Class|Module|Exception|StandardError'
            r'|puts|print|p|require|require_relative|include|extend|attr_reader'
            r'|attr_writer|attr_accessor|lambda|proc|raise|fail|catch|throw)\b'),
        "strings": re.compile(
            r'("[^"]*"|\'[^\']*\'|%[qQwW]\{[^}]*\})'),
        "comments": re.compile(r'(#.*?)(?=\n|$)'),
        "functions": re.compile(r'\b([a-zA-Z_]\w*)\s*(?=\()'),
        "numbers": re.compile(r'\b(\d+\.?\d*(?:e[+-]?\d+)?)\b'),
        "operators": re.compile(r'(=>|<<|>>|[+\-*/%&|^~<>!=?:]=?)'),
    },
    "php": {
        "keywords": re.compile(
            r'\b(abstract|and|array|as|break|callable|case|catch|class'
            r'|clone|const|continue|declare|default|die|do|echo|else'
            r'|elseif|empty|enddeclare|endfor|endforeach|endif|endswitch'
            r'|endwhile|eval|exit|extends|final|finally|fn|for|foreach'
            r'|function|global|goto|if|implements|include|include_once'
            r'|instanceof|insteadof|interface|isset|list|match|namespace'
            r'|new|or|print|private|protected|public|readonly|require'
            r'|require_once|return|static|switch|throw|trait|try|unset'
            r'|use|var|while|xor|yield)\b'),
        "builtins": re.compile(
            r'\b(array_|str_|preg_|file_|json_|curl_|date_|time|isset'
            r'|empty|count|sizeof|is_array|is_string|is_int|is_numeric'
            r'|strlen|strpos|substr|strtolower|strtoupper|trim|ltrim|rtrim'
            r'|explode|implode|sprintf|printf|number_format|round|ceil|floor'
            r'|abs|min|max|sort|rsort|asort|arsort|ksort|krsort'
            r'|array_push|array_pop|array_shift|array_unshift|array_merge'
            r'|array_keys|array_values|array_unique|array_filter|array_map'
            r'|in_array|array_search|array_slice|array_splice'
            r'|foreach|echo|print|var_dump|print_r)\b'),
        "strings": re.compile(
            r'("[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')'),
        "comments": re.compile(r'(//.*?)(?=\n|$)|(#.*?)(?=\n|$)|(/\*[\s\S]*?\*/)'),
        "variables": re.compile(r'(\$\w+)'),
        "functions": re.compile(r'\b([a-zA-Z_]\w*)\s*(?=\()'),
        "numbers": re.compile(r'\b(\d+\.?\d*(?:e[+-]?\d+)?)\b'),
        "operators": re.compile(r'(===|!==|=>|->|<<|>>|[+\-*/%&|^~<>!=?:]=?)'),
    },
    "swift": {
        "keywords": re.compile(
            r'\b(associatedtype|class|deinit|enum|extension|func|import'
            r'|init|inout|internal|let|operator|private|protocol|public'
            r'|static|struct|subscript|typealias|var|break|case|continue'
            r'|default|do|else|fallthrough|for|guard|if|in|repeat|return'
            r'|switch|where|while|as|catch|defer|else|false|is|nil|rethrows'
            r'|super|self|Self|throw|throws|true|try)\b'),
        "builtins": re.compile(
            r'\b(Array|Dictionary|Set|Optional|String|Int|Double|Float'
            r'|Bool|Character|Any|AnyObject|Error|Result|Sequence'
            r'|Collection|Iterator|Comparable|Equatable|Hashable'
            r'|Codable|Decodable|Encodable|Identifiable|CustomStringConvertible'
            r'|print|debugPrint|fatalError|precondition|assert)\b'),
        "strings": re.compile(r'("[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')'),
        "comments": re.compile(r'(//.*?)(?=\n|$)|(/\*[\s\S]*?\*/)'),
        "functions": re.compile(r'\b([a-zA-Z_]\w*)\s*(?=\()'),
        "numbers": re.compile(r'\b(\d+\.?\d*(?:e[+-]?\d+)?)\b'),
        "operators": re.compile(r'(->|=>|<<|>>|[+\-*/%&|^~<>!=?:]=?)'),
    },
    "kotlin": {
        "keywords": re.compile(
            r'\b(abstract|actual|annotation|as|break|by|catch|class'
            r'|companion|const|constructor|continue|crossinline|data'
            r'|delegate|do|dynamic|else|enum|expect|external|false|final'
            r'|finally|for|fun|get|if|import|in|infix|init|inline'
            r'|inner|interface|internal|is|it|lateinit|lazy|noinline'
            r'|null|object|open|operator|out|override|package|private'
            r'|protected|public|reified|return|sealed|set|super|suspend'
            r'|tailrec|this|throw|true|try|typealias|val|var|vararg'
            r'|when|where|while)\b'),
        "builtins": re.compile(
            r'\b(Any|Boolean|Byte|Char|Double|Float|Int|Long|Nothing'
            r'|Short|String|Unit|Array|List|MutableList|Map|MutableMap'
            r'|Set|MutableSet|Pair|Triple|Sequence|Iterable|Collection'
            r'|println|print|require|check|error|TODO)\b'),
        "strings": re.compile(r'("[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')'),
        "comments": re.compile(r'(//.*?)(?=\n|$)|(/\*[\s\S]*?\*/)'),
        "functions": re.compile(r'\b([a-zA-Z_]\w*)\s*(?=\()'),
        "numbers": re.compile(r'\b(\d+\.?\d*(?:e[+-]?\d+)?[fFL]?)\b'),
        "decorators": re.compile(r'(@\w+)'),
        "operators": re.compile(r'(->|=>|!!|\?\.|::|<<|>>|[+\-*/%&|^~<>!=?:]=?)'),
    },
    "lua": {
        "keywords": re.compile(
            r'\b(and|break|do|else|elseif|end|false|for|function|goto|if'
            r'|in|local|nil|not|or|repeat|return|then|true|until|while)\b'),
        "builtins": re.compile(
            r'\b(print|type|tostring|tonumber|error|assert|pcall|xpcall'
            r'|require|module|setmetatable|getmetatable|rawget|rawset'
            r'|rawequal|rawlen|select|ipairs|pairs|next|unpack|pack'
            r'|table|string|math|io|os|coroutine|debug|bit32|utf8)\b'),
        "strings": re.compile(r'(\[\[[\s\S]*?\]\]|"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')'),
        "comments": re.compile(r'(--\[\[[\s\S]*?\]\]|--.*?)(?=\n|$)'),
        "functions": re.compile(r'\b([a-zA-Z_]\w*)\s*(?=\()'),
        "numbers": re.compile(r'\b(\d+\.?\d*(?:e[+-]?\d+)?)\b'),
        "operators": re.compile(r'(~=|==|<<|>>|[+\-*/%&|^~<>!=]=?)'),
    },
    "markdown": {
        "headings": re.compile(r'^(#{1,6}\s+.*)$', re.MULTILINE),
        "bold": re.compile(r'(\*\*[^*]+\*\*|__[^_]+__)'),
        "italic": re.compile(r'(\*[^*]+\*|_[^_]+_)'),
        "code_inline": re.compile(r'(`[^`]+`)'),
        "links": re.compile(r'(\[([^\]]+)\]\(([^)]+)\))'),
        "lists": re.compile(r'^(\s*[-*+]\s+.*|\s*\d+\.\s+.*)$', re.MULTILINE),
    },
}


def _highlight_generic(code: str) -> str:
    """Generic highlighting: strings and comments only."""
    _sync_palette()  # colors must come from the ACTIVE theme
    code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    code = re.sub(
        r'("[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')',
        lambda m: _wrap(C_STRING, m.group(1)), code)
    code = re.sub(
        r'(//.*?)(?=\n|$)',
        lambda m: _wrap_italic(C_COMMENT, m.group(1)), code)
    code = re.sub(
        r'(#.*?)(?=\n|$)',
        lambda m: _wrap_italic(C_COMMENT, m.group(1)), code)
    return code


def highlight_code(code: str, lang: str = "") -> str:
    """Apply syntax highlighting to code. Returns HTML with inline styles.

    Uses a token-first approach: all tokens are collected with positions first,
    then HTML is generated in a single pass. This prevents HTML tags from being
    re-processed by subsequent regex patterns.

    OpenCode style:
    - Keywords: italic (purple)
    - Type keywords: bold+italic (yellow-green)
    - Types: bold (yellow-green)
    - Functions: normal (blue)
    - Strings: normal (teal)
    - Comments: italic (muted)
    - Builtins: red (OpenCode convention)
    - Numbers: peach
    """
    lang = lang.lower().strip()
    if not code:
        return code

    _sync_palette()  # bake colors from the theme live at RENDER time

    # Map language aliases
    lang_map = {
        "py": "python", "python3": "python",
        "js": "javascript", "jsx": "javascript", "mjs": "javascript",
        "ts": "typescript", "tsx": "typescript",
        "sh": "bash", "zsh": "bash", "shell": "bash",
        "c": "c", "cpp": "c", "c++": "c", "cc": "c", "h": "c", "hpp": "c",
        "rs": "rust",
        "go": "go", "golang": "go",
        "css": "css", "scss": "css", "less": "css",
        "html": "html", "xml": "html", "htm": "html",
        "sql": "sql", "mysql": "sql", "postgresql": "sql", "psql": "sql",
        "md": "markdown", "markdown": "markdown",
        "json": "json", "jsonc": "json",
        "yml": "yaml", "yaml": "yaml",
        "toml": "toml",
        "dockerfile": "dockerfile", "docker": "dockerfile",
        "ps1": "powershell", "powershell": "powershell", "pwsh": "powershell",
        "rb": "ruby",
        "php": "php",
        "swift": "swift",
        "kt": "kotlin", "kotlin": "kotlin",
        "lua": "lua",
        "cs": "csharp", "csharp": "csharp",
        "java": "java",
    }
    lang = lang_map.get(lang, lang)

    # Escape HTML entities FIRST
    code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    patterns = _LANG_PATTERNS.get(lang)
    if not patterns:
        return _highlight_generic(code)

    # ── TOKEN-FIRST APPROACH ──
    # Collect all tokens as (start, end, color, style) tuples first,
    # then generate HTML in a single pass. This prevents generated HTML tags
    # from being re-processed by subsequent regex patterns.

    tokens: list[tuple[int, int, str, str]] = []  # (start, end, color, extra_style)

    def _collect(color: str, extra_style: str = ""):
        def _inner(m: re.Match) -> str:
            tokens.append((m.start(), m.end(), color, extra_style))
            return m.group(0)  # don't modify the code yet
        return _inner

    # PHASE 1: Collect protected tokens (comments, strings)
    # These take priority over everything else
    if "comments" in patterns:
        patterns["comments"].sub(_collect(C_COMMENT, "font-style:italic"), code)
    if "strings" in patterns:
        patterns["strings"].sub(_collect(C_STRING), code)

    # Build a set of protected ranges
    protected = set()
    for start, end, _, _ in tokens:
        for i in range(start, end):
            protected.add(i)

    # PHASE 2: Collect remaining tokens (only if not inside protected ranges)
    def _collect_if_free(color: str, extra_style: str = ""):
        def _inner(m: re.Match) -> str:
            # Skip if any part of this match is inside a protected range
            for i in range(m.start(), m.end()):
                if i in protected:
                    return m.group(0)
            tokens.append((m.start(), m.end(), color, extra_style))
            return m.group(0)
        return _inner

    # Type keywords first (bold+italic) — more specific than keywords
    if "type_keywords" in patterns:
        patterns["type_keywords"].sub(_collect_if_free(C_TYPE, "font-weight:600;font-style:italic"), code)

    # Builtins before keywords (more specific) — OpenCode: builtins are RED
    if "builtins" in patterns:
        patterns["builtins"].sub(_collect_if_free(C_BUILTIN), code)

    # Keywords: italic (OpenCode style)
    if "keywords" in patterns:
        patterns["keywords"].sub(_collect_if_free(C_KEYWORD, "font-style:italic"), code)

    # Types: bold (OpenCode style)
    if "types" in patterns:
        patterns["types"].sub(_collect_if_free(C_TYPE, "font-weight:600"), code)

    # Decorators: same as types (yellow-green)
    if "decorators" in patterns:
        patterns["decorators"].sub(_collect_if_free(C_decorator), code)

    # Preprocessor: coral-red, bold
    if "preprocessor" in patterns:
        patterns["preprocessor"].sub(_collect_if_free(C_PROPERTY, "font-weight:600"), code)

    # Functions: blue (normal weight)
    if "functions" in patterns:
        patterns["functions"].sub(_collect_if_free(C_FUNCTION), code)

    # Numbers: peach
    if "numbers" in patterns:
        patterns["numbers"].sub(_collect_if_free(C_NUMBER), code)

    # Operators: light gray (skip operators containing > < & since they get HTML-escaped)
    if "operators" in patterns:
        def _collect_op(m):
            # Skip if inside protected range
            for i in range(m.start(), m.end()):
                if i in protected:
                    return m.group(0)
            # Skip operators containing > < & (they get HTML-escaped to &gt; &lt; &amp;)
            op = m.group(0)
            if '>' in op or '<' in op or '&' in op:
                return m.group(0)
            tokens.append((m.start(), m.end(), C_OPERATOR, ""))
            return m.group(0)
        patterns["operators"].sub(_collect_op, code)

    # Variables: bright
    if "variables" in patterns:
        patterns["variables"].sub(_collect_if_free(C_VARIABLE), code)

    # JSON-specific
    if "keys" in patterns:
        patterns["keys"].sub(_collect_if_free(C_PROPERTY), code)
    if "bools" in patterns:
        patterns["bools"].sub(_collect_if_free(C_CONSTANT), code)
    if "sections" in patterns:
        patterns["sections"].sub(_collect_if_free(C_HEADING, "font-weight:600"), code)

    # HTML/CSS-specific
    if "tags" in patterns:
        patterns["tags"].sub(_collect_if_free(C_TAG), code)
    if "attrs" in patterns:
        patterns["attrs"].sub(_collect_if_free(C_ATTR), code)
    if "selectors" in patterns:
        patterns["selectors"].sub(_collect_if_free(C_SELECTOR), code)
    if "properties" in patterns:
        patterns["properties"].sub(_collect_if_free(C_PROPERTY), code)
    if "values" in patterns:
        patterns["values"].sub(_collect_if_free(C_VALUE), code)

    # PHASE 3: Sort tokens by position and build HTML in a single pass
    tokens.sort(key=lambda t: t[0])

    # Merge overlapping tokens (keep the first one — highest priority)
    merged: list[tuple[int, int, str, str]] = []
    for token in tokens:
        start, end, color, style = token
        # Skip if overlaps with any existing token
        overlaps = False
        for m_start, m_end, _, _ in merged:
            if start < m_end and end > m_start:
                overlaps = True
                break
        if not overlaps:
            merged.append((start, end, color, style))

    # Build HTML: walk through code, inserting spans at token boundaries
    result = []
    pos = 0
    for start, end, color, style in merged:
        if start > pos:
            result.append(code[pos:start])
        result.append(_wrap(color, code[start:end], style))
        pos = end
    if pos < len(code):
        result.append(code[pos:])

    return ''.join(result)


def get_supported_languages() -> list[str]:
    """Return list of all supported language identifiers."""
    langs = set(_LANG_PATTERNS.keys())
    # Add aliases
    aliases = {
        "py", "python3", "js", "jsx", "mjs", "ts", "tsx",
        "sh", "zsh", "shell", "cpp", "c++", "cc", "h", "hpp",
        "rs", "golang", "scss", "less", "xml", "htm",
        "mysql", "postgresql", "psql", "md", "jsonc",
        "yml", "docker", "ps1", "pwsh", "rb", "kt", "cs",
    }
    return sorted(langs | aliases)


def guess_language_from_filename(filename: str) -> str:
    """Guess language from filename for code block rendering."""
    ext_map = {
        ".py": "python", ".js": "javascript", ".jsx": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".java": "java",
        ".c": "c", ".cpp": "c", ".h": "c", ".hpp": "c",
        ".rs": "rust", ".go": "go", ".rb": "ruby", ".php": "php",
        ".swift": "swift", ".kt": "kotlin", ".cs": "csharp",
        ".lua": "lua", ".sql": "sql", ".html": "html", ".xml": "html",
        ".css": "css", ".scss": "css", ".json": "json", ".yaml": "yaml",
        ".yml": "yaml", ".toml": "toml", ".md": "markdown",
        ".sh": "bash", ".zsh": "bash", ".ps1": "powershell",
        ".dockerfile": "dockerfile",
    }
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
        return ext_map.get(ext, "")
    if "dockerfile" in filename.lower():
        return "dockerfile"
    return ""
