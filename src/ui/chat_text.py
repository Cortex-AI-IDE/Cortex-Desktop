"""
chat_text.py, Text/markdown cleaning pipeline for native chat
==============================================================

Text/markdown cleaning helpers used by the native chat panel:
  clean_assistant_response, preprocess_thought_blocks, strip_control_tags,
  clean_broken_windows_paths, clean_markdown_urls, auto_fix_code_fences,
  detect_content_type, escape_currency_dollars

Phase 4 of the native chat migration.
"""

from __future__ import annotations
import re


# ── Reasoning leak patterns (agent meta-commentary that shouldn't reach user) ──
_REASONING_PATTERNS = [
    re.compile(r'^\s*(?:I will|I\'ll|Let me|Now I\'ll|Now I need to|I need to|I understand|I can see|I see that|I notice|I have)\s.{20,200}\.\s*$', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*(?:First,|Second,|Third,|Next,|Then,|After that,|Finally,)\s*(?:let me|I\'ll|I will|I need to)\s.{10,200}\.\s*$', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*(?:Searching for|Reading|Looking at|Checking|Finding|Locating)\s.{10,200}\.\s*$', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*(?:Based on|Since|Because|As I)\s.{10,200}\.\s*$', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*\*{1,2}(?:Summary|Step \d+|Fix \d+|Change \d+|What I know)\*{0,2}:?\s*$', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*(?:Now I have|Now I see|Now I know|Now I can)\s.{10,200}\.\s*$', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*(?:The mismatch|The issue|The problem|The root cause)\s.{10,200}\.\s*$', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*(?:Plus missing|Also missing|Additionally missing)\s.{5,200}\.?\s*$', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*(?:Formatting glitch|Rendering issue|Display problem)\s.{5,200}\.?\s*$', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*(?:I see|There are|There is|There\'s)\s\d+\s(?:remaining|more|additional)\s.{5,200}\.?\s*$', re.IGNORECASE | re.MULTILINE),
    re.compile(r'^\s*(?:Let me fix|Let me verify|Let me check|Let me confirm)\s.{10,200}\.\s*$', re.IGNORECASE | re.MULTILINE),
]

# ── Known control tags to strip ──
_KNOWN_TAGS = [
    'file_edited', 'exploration', 'task_summary', 'tasklist', 'plan',
    'permission', 'search', 'grep', 'glob', 'read_file', 'write_file',
    'edit_file', 'tool_result', 'tool_call', 'analysis', 'summary',
    'terminal_output', 'agent_response', 'agent_instruction',
    'thinking', 'think', 'THINK', 'cortex_thought',
]

# Control tags never meant for visible prose. A lone opening/closing tag on its
# own line is removed without its partner (Pass 1c of strip_all_control_tags).
# Narrower than _KNOWN_TAGS: 'summary'/'plan' are real HTML/prose.
_ORPHAN_CONTROL_TAGS = (
    'task_summary', 'file_edited', 'exploration', 'tasklist',
    'tool_result', 'tool_call', 'terminal_output',
    'agent_response', 'agent_instruction', 'cortex_thought',
)
_ORPHAN_CONTROL_TAG_LINE = re.compile(
    r'^[ \t]*</?(?:' + '|'.join(_ORPHAN_CONTROL_TAGS) + r')(?:\s[^>\n]*)?>[ \t]*$',
    re.MULTILINE | re.IGNORECASE)

# JSON file-entry fragments left when a <task_summary> block is cleaned in
# pieces: Pass 8 only removes JSON starting '{' and ending '}', so pieces cut
# mid-entry keep fragments visible (2026-09-18 screenshot). Each alternative
# is a shape only JSON fragments produce; code fences are stashed first, so
# real code is untouched.
_JSON_ENTRY_FRAG_LINE = re.compile(
    r'^[ \t]*(?:'
    # any line carrying an entry action, incl. dangling tails like "}, ...
    r'[^\n]*?"action"\s*:\s*"(?:modified|created|deleted)"[^\n]*$'
    # "files": [ opener, whole or with first entries glued on
    r'|"(?:files)"\s*:\s*\[[^\n]*$'
    # dangling value then a key: "chat_panel.py", "path": "...
    r'|"[^"]*"\s*,\s*"(?:name|path)"\s*:[^\n]*$'
    # a lone key line: "name": "x.py", / "title": "...",
    r'|"(?:name|path|title)"\s*:\s*"[^"]*"\s*,?\s*$'
    # lone bracket/brace lines: ]  ],  {  }
    r'|[\]{}][ \t]*,?\s*$'
    # lone dangling tail: "},
    r'|"\}[ \t]*,?\s*$'
    r')',
    re.MULTILINE)

# Signature of a task_summary "files" entry. Two+ such lines in one indented
# run mean leaked JSON, not code, so auto_fix_broken_code_fences must not fence
# it (fencing would render it as a raw JSON code card).
_JSON_ENTRY_STRONG = re.compile(r'"action"\s*:\s*"(?:modified|created|deleted)"')


def clean_assistant_response(text: str) -> str:
    """Strip agent reasoning leaks and meta-commentary from response text."""
    if not text:
        return text

    cleaned = text
    for pattern in _REASONING_PATTERNS:
        cleaned = pattern.sub('', cleaned)

    # Single-line compact cleanup
    if '\n' not in cleaned and len(cleaned) < 500:
        compact_patterns = [
            r'(?:^|\.\s+)(?:I will|I\'ll|Let me|Now I\'ll|Now I need to|I need to|I understand|I can see|I see that|I notice|I have)\s.{15,200}\.\s*',
            r'(?:^|\.\s+)(?:First,|Second,|Third,|Next,|Then,|After that,|Finally,)\s*(?:let me|I\'ll|I will|I need to)\s.{10,200}\.\s*',
            r'(?:^|\.\s+)(?:Searching for|Reading|Looking at|Checking|Finding|Locating)\s.{10,200}\.\s*',
            r'(?:^|\.\s+)(?:Now I have|Now I see|Now I know|Now I can)\s.{10,200}\.\s*',
            r'(?:^|\.\s+)(?:Let me fix|Let me verify|Let me check|Let me confirm)\s.{10,200}\.\s*',
        ]
        for p in compact_patterns:
            cleaned = re.sub(p, '', cleaned, flags=re.IGNORECASE)

    # Strip raw JSON tool results
    cleaned = re.sub(r'\{[^}]*"command"[^}]*"stdout"[^}]*"returncode"[^}]*\}\s*', '', cleaned)
    # Collapse excessive newlines
    cleaned = re.sub(r'\n{3,}', '\n', cleaned)
    return cleaned.strip() or text


def preprocess_thought_blocks(text: str) -> str:
    """Convert <think>...</think> tags to thought markers for rendering."""
    if not text:
        return text
    return re.sub(
        r'<think[^>]*>([\s\S]*?)</think>',
        lambda m: '\n\n﹅think﹅\n' + m.group(1).strip() + '\n﹅/think﹅\n\n',
        text,
        flags=re.IGNORECASE,
    )


def strip_all_control_tags(text: str) -> str:
    """Remove all XML control tags from text before markdown rendering.

    PROTECTS: inline code (`...`), fenced code blocks (```...```),
    and markdown links ([text](url)) from destructive tag stripping.
    Characters like '<' in tree output (e.g. `<src/`) are preserved
    when inside code spans or code blocks.
    """
    if not text:
        return ''

    clean = text

    # ── Stash protected regions before tag stripping ──
    # 1. Fenced code blocks (```...```)
    _fenced: list[str] = []
    def _save_fence(m):
        _fenced.append(m.group(0))
        return f'\x00FENCED{len(_fenced) - 1}\x00'
    clean = re.sub(r'```[\s\S]*?```', _save_fence, clean)

    # 2. Inline code spans (`...`)
    _inline: list[str] = []
    def _save_inline(m):
        _inline.append(m.group(0))
        return f'\x00INLINE{len(_inline) - 1}\x00'
    clean = re.sub(r'`[^`\n]+`', _save_inline, clean)

    # 3. Markdown links ([text](url)), angle brackets in URLs get mangled
    _links: list[str] = []
    def _save_link(m):
        _links.append(m.group(0))
        return f'\x00LINK{len(_links) - 1}\x00'
    clean = re.sub(r'\[[^\]]*\]\([^)]*\)', _save_link, clean)

    # Pass 1: Remove known tag pairs
    for tag in _KNOWN_TAGS:
        clean = re.sub(rf'<{tag}[\s\S]*?</{tag}>', '', clean, flags=re.IGNORECASE)

    # Pass 1b: Partial known tag openings without closing > (streaming artifacts
    # like '<task_summary' before the '>' arrives in the next chunk)
    for tag in _KNOWN_TAGS:
        clean = re.sub(rf'<{tag}(?:[^>\n]*)?$', '', clean, flags=re.MULTILINE | re.IGNORECASE)

    # Pass 1c removes orphan control tags on their own line: answers are cleaned
    # in pieces (thinking/tool interleaves, seals), so <task_summary> can land in
    # one piece and </task_summary> in the next (2026-09-10 both survived as text).
    # Only whole-line tags go, since the prompt puts them on their own lines.
    clean = _ORPHAN_CONTROL_TAG_LINE.sub('', clean)

    # Pass 2: Catch-all paired XML tags, only match tags with actual attribute
    # syntax (space after tag name) or common HTML tags, to avoid destroying
    # tree output like "<src/" or "<module>" in prose.
    _COMMON_HTML_TAGS = (
        'div', 'span', 'p', 'br', 'hr', 'img', 'a', 'b', 'i', 'u', 'em', 'strong',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'table', 'tr', 'td',
        'th', 'thead', 'tbody', 'pre', 'code', 'blockquote', 'dl', 'dt', 'dd',
        'button', 'input', 'select', 'option', 'textarea', 'form', 'label',
        'section', 'article', 'nav', 'header', 'footer', 'aside', 'main',
        'figure', 'figcaption', 'details', 'summary', 'mark', 'small', 'sub', 'sup',
        'del', 'ins', 'abbr', 'cite', 'q', 'var', 'samp', 'kbd', 'time',
    )
    _COMMON_RE = '|'.join(_COMMON_HTML_TAGS)
    prev = None
    while prev != clean:
        prev = clean
        # Only strip tags that are: (a) known HTML tags, or (b) have attributes
        clean = re.sub(
            rf'<({_COMMON_RE})(?:\s[^>]*)?>([\s\S]*?)</\1>',
            '', clean, flags=re.IGNORECASE
        )
    # Second pass: catch ANY tag with attributes (e.g. <div class="...">)
    # but NOT bare tags without attributes (to protect tree chars like <src/>)
    prev = None
    while prev != clean:
        prev = clean
        clean = re.sub(r'<(\w[\w-]+)\s+[^>]+>([\s\S]*?)</\1>', '', clean)

    # Pass 3: Self-closing tags, only known HTML or tags with attributes
    clean = re.sub(rf'<({_COMMON_RE})\s*\/>', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'<(\w[\w-]+)\s+[^>]*/>', '', clean)

    # Pass 4: Orphan closing tags, only known HTML
    clean = re.sub(rf'</({_COMMON_RE})\s*>', '', clean, flags=re.IGNORECASE)

    # Pass 5: Orphan opening tags, ONLY known HTML tags (not tree chars like <src/)
    clean = re.sub(
        rf'<({_COMMON_RE})(?:\s[^>]*)?>\s*$',
        '', clean, flags=re.MULTILINE | re.IGNORECASE
    )

    # Pass 6: Stray data attributes
    clean = re.sub(r'\bdata-[a-zA-Z_][a-zA-Z0-9_-]*="[^"]*"', '', clean)

    # Pass 7: Model reasoning artifacts
    clean = re.sub(r'^<h\[\d[^>]*>.*?</h\[\d[^>]*>\s*$', '', clean, flags=re.MULTILINE)
    clean = re.sub(r'^\s*\d+\s+(?:file|files|search|searches|thought|thoughts|command|commands)\s*[·\xB7]\s*$', '', clean, flags=re.MULTILINE)
    clean = re.sub(r'^\s*\d+\.?\d*s\s*$', '', clean, flags=re.MULTILINE)

    # Pass 8: Raw JSON leaks, task_summary, file_edited, and other structured data
    # Remove full JSON blocks (well-formed)
    clean = re.sub(r'\{\s*"title"\s*:\s*"[^"]*"[\s\S]*?"message"\s*:\s*"[^"]*"\s*\}', '', clean)
    clean = re.sub(r'\{\s*"name"\s*:\s*"[^"]*".*?"action"\s*:\s*"(?:modified|created|deleted)".*?\}', '', clean)
    clean = re.sub(r'\[\s*\{\s*"name"\s*:\s*"[^"]*".*?"path"\s*:\s*"[^"]*"\s*\}[\s,]*\]', '', clean)
    clean = re.sub(r'All tasks complete\. Here\'s what was done:[\s\S]*?\}\s*$', '', clean, flags=re.MULTILINE)
    clean = re.sub(r'^All tasks complete\. Here\'s what was done:\s*$', '', clean, flags=re.MULTILINE)
    # Remove any JSON-like object with "title"/"files"/"message"/"name"/"action"/"path" keys
    clean = re.sub(r'\{\s*"(?:title|files|message|name|action|path|description|type)"\s*:[\s\S]*?\}', '', clean)
    # Remove JSON arrays of objects with "name"/"path"/"action" keys
    clean = re.sub(r'\[\s*\{[\s\S]*?"(?:name|path|action)"\s*:[\s\S]*?\}\s*\]', '', clean)
    # Catch partial/orphan JSON: standalone { or } with JSON-like context
    clean = re.sub(r'\{\s*"(?:title|name|files|path|action|message|description)"[\s\S]*$', '', clean, flags=re.MULTILINE)
    # Remove orphan closing braces on their own line that are JSON remnants
    clean = re.sub(r'^\s*\}\s*$', '', clean, flags=re.MULTILINE)
    # Remove lines that are just a bare { or } 
    clean = re.sub(r'^\s*[{}]\s*$', '', clean, flags=re.MULTILINE)
    # Remove residual "key": "value", patterns on a line (broken JSON fragments)
    clean = re.sub(r'(?:^|\n)\s*"[a-z_]+"\s*:\s*(?:"[^"]*"|\[[\s\S]*?\]|\{[\s\S]*?\})\s*,?\s*(?=\n|$)', '', clean)

    # JSON file-entry fragments left when a <task_summary> block is cleaned in
    # pieces: Pass 8 only removes JSON starting '{' and ending '}', so pieces cut
    # mid-entry keep fragments visible (2026-09-18 screenshot). Each alternative
    # is a shape only JSON fragments produce; code fences are stashed first, so
    # real code is untouched.
    clean = _JSON_ENTRY_FRAG_LINE.sub('', clean)

    # Pass 9: Topic/status headings that precede prose
    clean = re.sub(r'^###?\s*Topic:.*\n?', '', clean, flags=re.MULTILINE)
    clean = re.sub(r'^###?\s*Status:.*\n?', '', clean, flags=re.MULTILINE)
    clean = re.sub(r'^###?\s*Plan:.*\n?', '', clean, flags=re.MULTILINE)

    # ── Restore protected regions ──
    for i, link in enumerate(_links):
        clean = clean.replace(f'\x00LINK{i}\x00', link)
    for i, inline in enumerate(_inline):
        clean = clean.replace(f'\x00INLINE{i}\x00', inline)
    for i, fence in enumerate(_fenced):
        clean = clean.replace(f'\x00FENCED{i}\x00', fence)

    return clean.strip()


def clean_broken_windows_paths(text: str) -> str:
    """Fix broken Windows paths and wrap paths in backticks."""
    if not text:
        return text

    # Protect code blocks
    code_blocks = []
    def _save_code(m):
        code_blocks.append(m.group(0))
        return f'%%PATHCODE_{len(code_blocks)-1}%%'
    t = re.sub(r'```+[\s\S]*?```+', _save_code, text)

    inline_codes = []
    def _save_inline(m):
        inline_codes.append(m.group(0))
        return f'%%PATHINLINE_{len(inline_codes)-1}%%'
    t = re.sub(r'`+[^`]+`+', _save_inline, t)

    # Fix "C : \" gaps
    t = re.sub(r'([A-Z])\s+:\s+\\', r'\1:\\', t)
    # Fix fragmented paths
    t = re.sub(r'\\([A-Z])\s+([a-z]+)', r'\\\1\2', t)
    # Match only real Windows paths with a backslash after the drive letter:
    # otherwise "C:\Users" followed by "but each" was matched and spaces stripped
    # ("buteach").
    def _fix_frag(m):
        s = m.group(0)
        # Only strip spaces inside path segments (between backslashes),
        # not in regular prose text after the path ends.
        # Split by backslashes, fix only segments that look like filenames
        parts = s.split('\\')
        for i, part in enumerate(parts):
            if i == 0:
                continue  # Skip drive letter (e.g., "C:")
            # Only fix segments that are short (filename-like, ≤20 chars)
            # and don't contain spaces followed by lowercase prose words
            if len(part) <= 20:
                part = re.sub(r'([A-Z])\s+([a-z]{2,})', r'\1\2', part)
                part = re.sub(r'([a-z])\s+([a-z]{3,})', r'\1\2', part)
            parts[i] = part
        return '\\'.join(parts)
    # Require at least one backslash after drive letter to match
    t = re.sub(r'[a-zA-Z]:\\[\w\\._]+(?:\\[\w\\._\s]+)*', _fix_frag, t)
    # Wrap file paths in backticks
    t = re.sub(r'(?<!`)\b([a-zA-Z]:\\[\w\\._]+\.\w+)\b(?!`)', r'`\1`', t)
    # Wrap directory paths
    t = re.sub(r'(?<!`)\b([a-zA-Z]:\\(?:[\w.-]+\\){1,}[\w.-]+)\b(?!`)', r'`\1`', t)

    # Restore
    for i, code in enumerate(inline_codes):
        t = t.replace(f'%%PATHINLINE_{i}%%', code)
    for i, code in enumerate(code_blocks):
        t = t.replace(f'%%PATHCODE_{i}%%', code)

    return t


def clean_markdown_urls(text: str) -> str:
    """Fix backtick artifacts in markdown URLs."""
    if not text:
        return text
    text = re.sub(r'\[`([^\]]+)`\]\(`([^)]+)`\)', r'[\1](\2)', text)
    text = re.sub(r'\[`([^\]]+)`\]\(([^)]+)\)', r'[\1](\2)', text)
    text = re.sub(r'\[([^\]]+)\]\(`([^)]+)`\)', r'[\1](\2)', text)
    return text


# A markdown list item at any indent: "- x", "* x", "+ x", "1. x", "2) x".
_LIST_ITEM_RE = re.compile(r'^[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+\S')

# Glued fence openers: DeepSeek v4 emits "```mermaidflowchart TB" on one line.
# Streaming stripper copes, but turn-end segmentation needs a real newline or
# the diagram card is discarded and raw fence text leaks into prose. Match the
# longest known-language prefix so "csharp" isn't split as "c"+"sharp".
_FENCE_LANGS = frozenset({
    "mermaid", "graph", "flowchart", "sequencediagram", "classdiagram",
    "statediagram", "erdiagram", "gantt", "pie", "mindmap", "timeline",
    "journey", "gitgraph", "xychart", "block", "architecture",
    "python", "py", "python3", "javascript", "js", "jsx", "typescript", "ts",
    "tsx", "json", "jsonc", "yaml", "yml", "toml", "ini", "env", "dotenv",
    "bash", "sh", "shell", "zsh", "fish", "powershell", "ps1", "bat", "cmd",
    "sql", "html", "htm", "xml", "css", "scss", "sass", "less", "svg",
    "java", "kotlin", "kts", "scala", "groovy", "gradle", "c", "h", "cpp",
    "c++", "cc", "hpp", "csharp", "cs", "fsharp", "fs", "go", "golang",
    "rust", "rs", "php", "ruby", "rb", "swift", "objc", "objectivec",
    "dart", "lua", "perl", "r", "julia", "matlab", "vim", "elixir", "ex",
    "erlang", "haskell", "clojure", "zig", "nim", "solidity", "asm",
    "markdown", "md", "mdx", "text", "txt", "plaintext", "log", "csv", "tsv",
    "diff", "patch", "dockerfile", "docker", "makefile", "make", "cmake",
    "nginx", "apache", "graphql", "gql", "proto", "protobuf", "regex", "re",
    "http", "promql", "terraform", "hcl", "vue", "svelte", "astro",
    "prisma", "cypher", "mongo", "redis", "crontab", "systemd", "tcl",
})

# Anchored at line start, so an inline "```lang" inside a sentence is never
# touched. Bounded quantifier: a fence opener cannot be thousands of chars.
_GLUED_FENCE_RE = re.compile(r'^([ \t]*)```([A-Za-z0-9_#+.-]{1,64})(.*)$',
                             re.MULTILINE)


def split_glued_fence_languages(text: str) -> str:
    """Insert the missing newline in a fence opener glued to its code.

    "```mermaidflowchart TB ..." becomes "```mermaid\\nflowchart TB ...".
    Only fires when the leading token starts with a KNOWN language name and
    carries extra characters; a clean opener, inline backticks and prose are
    returned unchanged. Idempotent: a repaired fence no longer matches.
    """
    if not text or '```' not in text:
        return text

    def _split(m: re.Match) -> str:
        indent, token, rest = m.group(1), m.group(2), m.group(3)
        low = token.lower()
        # Longest known-language prefix wins; n == len(token) means clean.
        for n in range(len(low), 0, -1):
            if low[:n] in _FENCE_LANGS:
                if n == len(token):
                    return m.group(0)
                return f"{indent}```{token[:n]}\n{token[n:]}{rest}"
        return m.group(0)

    return _GLUED_FENCE_RE.sub(_split, text)


# Only unfenced payloads unambiguously diagrams may be wrapped: timeline,
# journey, gantt, pie, mindmap are ordinary English words, and the old
# re.IGNORECASE test turned "Timeline is decisive: spec fixed 6:36 PM..." into
# a blank Mermaid canvas (2026-10-22 screenshot). Require a case-sensitive head
# (real payloads start lowercase) and family syntax in the body.
_RE_MERMAID_HEAD = re.compile(
    r'^(?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?'
    r'|erDiagram|journey|gantt|pie|mindmap|timeline|quadrantChart'
    r'|requirementDiagram|gitGraph|sankey-beta)\b'
)
# "graph" and "flowchart" are only valid with a direction token.
_RE_MERMAID_DIRECTION = re.compile(r'^(?:TD|TB|LR|RL|BT)\b', re.IGNORECASE)
# Edges and message arrows. Deliberately excludes loose tokens such as "--"
# or "-x", which appear in prose ("the -x flag"). The trailing alternatives
# are ER cardinality joins ("||--o{"), which have no three-dash run.
_RE_MERMAID_EDGE = re.compile(
    r'-->|->>|-->>|->|--x|-.->|--->|==>|---|<\|--|\.\.>|:::'
    r'|\|\|--|--o\{|--\{|--\|'
)
# Section-style families have no edges; they are keywords plus "key: value".
_RE_MERMAID_SECTION = re.compile(
    r'(?m)^\s*(?:title|section|dateFormat|axisFormat|accTitle|accDescr)\b'
    r'|:\s*\S'
)
_FLOW_HEADS = frozenset({'graph', 'flowchart'})
_SECTION_HEADS = frozenset({'timeline', 'gantt', 'pie', 'journey', 'mindmap'})


def looks_like_mermaid_payload(text: str) -> bool:
    """Return True only when ``text`` is an unfenced mermaid diagram.

    Prose that merely starts with a word also used as a diagram keyword
    ("Timeline is decisive: ...") returns False, so it stays prose.
    """
    if not text:
        return False
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    head_line = lines[0]
    if not _RE_MERMAID_HEAD.match(head_line):
        return False
    keyword = head_line.split()[0]
    if keyword in _FLOW_HEADS:
        rest = head_line[len(keyword):].strip()
        if not _RE_MERMAID_DIRECTION.match(rest):
            return False
        return bool(_RE_MERMAID_EDGE.search(text))
    if keyword in _SECTION_HEADS:
        return bool(_RE_MERMAID_SECTION.search('\n'.join(lines[1:])))
    return bool(_RE_MERMAID_EDGE.search(text))


# Fence opener must START a line (CommonMark: up to 3 leading spaces). The old
# DOTALL patterns matched inline "```mermaid" and the literal inside a code
# block, swallowing the REST of the message and building a diagram card from
# prose (UnknownDiagramError, 2026-10-22). All callers now use this scanner so
# the streaming and turn-end paths can't disagree.
_FENCE_OPEN_RE = re.compile(r'^([ \t]{0,3})(`{3,})([^\s`]*)(.*)$')
_FENCE_CLOSE_RE = re.compile(r'^[ \t]{0,3}`{3,}[ \t]*$')

# FENCED bodies only use a case-insensitive keyword set: "```mermaid" already
# declares the language, so "Graph TD" is fine. The unfenced gate stays case-
# sensitive to keep the English word "Timeline" out of the viewer.
_FENCE_HEAD_KEYWORDS = frozenset({
    'graph', 'flowchart', 'sequencediagram', 'classdiagram', 'statediagram',
    'statediagram-v2', 'erdiagram', 'journey', 'gantt', 'pie', 'mindmap',
    'timeline', 'quadrantchart', 'requirementdiagram', 'gitgraph',
    'sankey-beta',
})


def _mermaid_fence_body_ok(code: str) -> bool:
    """Return True when a fenced body is diagram syntax rather than prose.

    Defence in depth: even inside a genuine ```mermaid fence, prose stays a
    code block. Showing the author's text is right; drawing it is not.

    Deliberately permissive about body shape, because an edge-less flowchart
    and a mindmap are both valid mermaid. Only the ambiguous heads are checked,
    and only for syntax mermaid itself requires:

    * ``graph`` / ``flowchart`` must carry a direction token, which mermaid
      requires anyway. "graph is a data structure" is rejected.
    * ``timeline`` / ``gantt`` / ``pie`` / ``journey`` / ``mindmap`` are also
      ordinary English words, so anything after them must be empty or ``title``.
      "timeline is decisive: spec fixed" is rejected, ``pie title Pets`` is not.
    * every other declared diagram type is trusted as written.
    """
    lines = [ln.strip() for ln in code.splitlines() if ln.strip()]
    if not lines:
        return False
    keyword, _, rest = lines[0].partition(' ')
    keyword = keyword.lower()
    if keyword not in _FENCE_HEAD_KEYWORDS:
        return False
    rest = rest.strip()
    if keyword in _FLOW_HEADS:
        return bool(_RE_MERMAID_DIRECTION.match(rest))
    if keyword in _SECTION_HEADS:
        return not rest or rest.lower().startswith('title')
    return True


def find_mermaid_spans(text: str) -> list[tuple[int, int, str, bool]]:
    """Return ``(start, end, code, complete)`` for every real mermaid fence.

    ``start``/``end`` are offsets into ``text`` covering the whole fence (opener
    through closing fence, or through the end of the text when the stream
    stopped mid-diagram). Inline backtick mentions and the literal token inside
    another code block are never returned, and a fence whose body is prose is
    skipped so it renders as the code block the author wrote.

    Handles the glued form this project's providers emit
    ("```mermaidflowchart TB ... end```") in both directions: language glued to
    the first statement, and the closing fence glued to the last line.
    """
    if not text or 'mermaid' not in text.lower():
        return []

    spans: list[tuple[int, int, str, bool]] = []
    offset = 0
    open_start = -1
    body: list[str] = []
    in_other_fence = False

    for line in text.splitlines(keepends=True):
        stripped = line.rstrip('\r\n')

        if open_start >= 0:
            if _FENCE_CLOSE_RE.match(stripped):
                code = '\n'.join(body).strip()
                if _mermaid_fence_body_ok(code):
                    spans.append((open_start, offset + len(line), code, True))
                open_start, body = -1, []
            elif '```' in stripped:
                # Closing fence glued to the last statement ("... end```").
                body.append(stripped.split('```', 1)[0])
                code = '\n'.join(body).strip()
                if _mermaid_fence_body_ok(code):
                    spans.append((open_start, offset + len(line), code, True))
                open_start, body = -1, []
            else:
                body.append(stripped)
            offset += len(line)
            continue

        if in_other_fence:
            if _FENCE_CLOSE_RE.match(stripped):
                in_other_fence = False
            offset += len(line)
            continue

        match = _FENCE_OPEN_RE.match(stripped)
        if not match:
            offset += len(line)
            continue

        token = match.group(3)
        if not token.lower().startswith('mermaid'):
            in_other_fence = True
            offset += len(line)
            continue

        # The opener may carry the first statement on the same line: either
        # glued ("```mermaidflowchart TB") or spaced ("```mermaid flowchart TB").
        first = (token[7:] + match.group(4)).strip()
        open_start = offset
        if first and '```' in first:
            body = [first.split('```', 1)[0]]
            code = '\n'.join(body).strip()
            if _mermaid_fence_body_ok(code):
                spans.append((open_start, offset + len(line), code, True))
            open_start, body = -1, []
        else:
            body = [first] if first else []
        offset += len(line)

    if open_start >= 0:
        code = '\n'.join(body).strip()
        if _mermaid_fence_body_ok(code):
            spans.append((open_start, len(text), code, False))
    return spans


def auto_fix_broken_code_fences(text: str) -> str:
    """Repair malformed markdown code fences."""
    if not text:
        return text

    # Glued opener first: every rule below expects a well-formed opener.
    fixed = split_glued_fence_languages(text)
    trimmed = fixed.strip()

    # Convert literal <pre><code>...</code></pre> HTML to fenced code blocks.
    # LLMs sometimes output raw HTML code tags instead of ``` fences.
    # These leak as visible "<pre><code>" text because Qt's markdown strips them.
    def _pre_code_to_fence(m):
        import html as _html
        tag = m.group(1)  # the full <code ...> opening tag
        body = m.group(2)  # code content
        # Extract language from class="language-python" or class="python"
        lang = ""
        cls_m = re.search(r'class="(?:language-)?(\w+)"', tag or "")
        if cls_m:
            lang = cls_m.group(1)
        # Unescape HTML entities (&amp; → &, &lt; → <, etc.)
        body = _html.unescape(body)
        return f"```{lang}\n{body}\n```"
    fixed = re.sub(
        r'<pre[^>]*>\s*<code([^>]*)>(.*?)</code>\s*</pre>',
        _pre_code_to_fence,
        fixed,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Wrap bare 4+ space indented blocks (not in a list): Qt setMarkdown treats them
    # as <blockquote>, not <pre><code>. Wrap runs of 3+ indented lines in ```.
    def _dedent_indented_block(block: str) -> str:
        # Dedent: remove leading 4 spaces or 1 tab from each line
        lines = block.split('\n')
        dedented = []
        for line in lines:
            if line.startswith('\t'):
                dedented.append(line[1:])
            elif line.startswith('    '):
                dedented.append(line[4:])
            elif line.strip() == '':
                dedented.append('')
            else:
                dedented.append(line)
        return '\n'.join(dedented).strip()

    # Linear scan replaces a catastrophic regex where [ \t]{4,} and [^\n]*
    # overlapped on leading spaces, backtracking 45.9s of GUI time on indented
    # blocks (Not Responding). O(n) scan wraps the same blocks (3+ indented lines,
    # not at start, followed by blank/end), tracks fence state, and leaves runs of
    # 3+ nested bullets to mistune (CommonMark list content).
    _ind_lines = fixed.split('\n')

    def _run_is_list_content(start: int, end: int) -> bool:
        if any(_LIST_ITEM_RE.match(_l) for _l in _ind_lines[start:end]):
            return True
        # Nearest line above that is not indented 4+: the block owning the run.
        _k = start - 1
        while _k >= 0 and (not _ind_lines[_k].strip()
                           or _ind_lines[_k].startswith(('    ', '\t'))):
            _k -= 1
        return _k >= 0 and bool(_LIST_ITEM_RE.match(_ind_lines[_k]))

    _ind_out: list[str] = []
    _i = 0
    _n = len(_ind_lines)
    _in_fence = False  # track whether we're inside a ``` block
    while _i < _n:
        _ln = _ind_lines[_i]
        # Track fence state: ```lang opens, bare ``` toggles
        _stripped = _ln.lstrip()
        if _stripped.startswith('```'):
            _after = _stripped[3:].strip()
            _has_lang = bool(re.match(r'[a-zA-Z0-9_#+-]+$', _after))
            if _has_lang:
                _in_fence = True
            else:
                _in_fence = not _in_fence
            _ind_out.append(_ln)
            _i += 1
            continue
        # Only wrap indented blocks that are OUTSIDE any code fence
        if (_i > 0 and not _in_fence and _ln.strip()
                and (_ln.startswith('    ') or _ln.startswith('\t'))):
            _j = _i
            while _j < _n and (_ind_lines[_j].startswith('    ')
                               or _ind_lines[_j].startswith('\t')):
                _j += 1
            if ((_j - _i) >= 3 and (_j == _n or not _ind_lines[_j].strip())
                    and not _run_is_list_content(_i, _j)):
                _run = _ind_lines[_i:_j]
                # An indented run of task_summary "files" entries is leaked JSON, not code:
                # fencing hides it from _JSON_ENTRY_FRAG_LINE and renders a raw JSON code card
                # (2026-09-18 screenshot). Two+ entry-action lines never occur in real code.
                if sum(1 for _l in _run
                       if _JSON_ENTRY_STRONG.search(_l)) < 2:
                    _body = _dedent_indented_block('\n'.join(_run))
                    if _body:
                        # Trailing \n only at end-of-text: mid-text the join()
                        # separator supplies the newline before the next line,
                        # matching the old regex replacement byte-for-byte.
                        _ind_out.append('\n```\n' + _body + '\n```'
                                        + ('\n' if _j == _n else ''))
                        _i = _j
                        continue
        _ind_out.append(_ln)
        _i += 1
    fixed = '\n'.join(_ind_out)

    # Auto-wrap plain Mermaid payloads
    if not re.search(r'```mermaid', fixed, re.IGNORECASE) and \
       looks_like_mermaid_payload(trimmed):
        fixed = f'```mermaid\n{trimmed}\n```'

# Fence rules apply only where a fence can start its line: inline mentions
# ("That stray ``` lands", "| A fenced ```json example |") used to be split
# into a real fence, swallowing 1,854/2,122 chars (2026-10-21). Rule 3 skips
# inline/table fences; use [ \t]+ not \s so blank lines aren't eaten.
    fixed = re.sub(r'(?m)^([ \t]*```[a-zA-Z0-9_#+-]+)[ \t]+(?=\S)', r'\1\n', fixed)
    # Normalize ALL CRLF → LF inside code fence blocks (openings, content, closings)
    def _normalize_fence_crlf(m):
        return m.group(0).replace('\r\n', '\n')
    fixed = re.sub(r'```[a-zA-Z0-9_#+-]*[\s\S]*?```', _normalize_fence_crlf, fixed)
    # Bare ``` with same-line content needs a newline. The old \s+ matched NEWLINES,
    # so a closing fence followed by blank line + prose lost the blank line, shifting
    # later fence pairing and swallowing prose into code cards (the "THREE card"
    # bug). [ \t]+ restricts it to real same-line content.
    fixed = re.sub(r'(?m)^([ \t]*```)[ \t]+(?=\S)', r'\1\n', fixed)

    # ── Move an opening fence that shares a line with preceding text onto its
    #    own line: "- **Web Browser**: ```bash" → "- **Web Browser**:\n```bash"
    #    so the markdown parser treats it as a block, not inline text.
    def _fence_to_own_line(line: str) -> str:
        m = re.search(r'(\S)[ \t]+(```[a-zA-Z0-9_#+-]*[ \t]*)$', line)
        if not m:
            return line
        if line.lstrip().startswith(('|', '```')):
            return line                      # a table row, or already a fence
        if m.group(1) == '`' or line[:m.start(2)].count('`') % 2:
            return line                      # inside an inline code span
        return line[:m.start(2)].rstrip() + '\n' + m.group(2)
    fixed = '\n'.join(_fence_to_own_line(_l) for _l in fixed.split('\n'))

    # Balance fences so code never leaks: a language-tagged fence (```bash) is
    # always an opening, so insert a close if one is already open; bare ``` toggles;
    # append a close for any block open at end. Guarantees every code region wraps.
    out_lines = []
    in_fence = False
    for line in fixed.split('\n'):
        stripped = line.lstrip()
        if stripped.startswith('```'):
            after = stripped[3:].strip()
            has_lang = bool(re.match(r'[a-zA-Z0-9_#+-]+$', after))
            if has_lang:           # opening fence with language
                if in_fence:       # previous block never closed → close it
                    out_lines.append('```')
                out_lines.append(line)
                in_fence = True
            else:                  # bare ``` → toggle open/close
                out_lines.append(line)
                in_fence = not in_fence
        else:
            out_lines.append(line)
    if in_fence:
        out_lines.append('```')
    fixed = '\n'.join(out_lines)

    # Removed "merge consecutive fenced blocks" regex: it deleted a close + next
    # open to fuse back-to-back code, and on partial buffers deleted REAL fences,
    # shifting pairing and mangling commands (the "THREE card" bug). ChatPanel.
    # _merge_consecutive_code_blocks already merges safely after parsing.

    return fixed


# A table cell containing a ``` fence is illegal markdown: the fence lands alone
# inside the table and mistune treats the rest of the answer as one <pre> (4,660
# of 4,564 chars in a real saved block, 2026-10-21). The tag stripper stashes
# fenced regions, so <task_summary> JSON inside the fake block also survived.
# Rejoin the row first and both symptoms disappear.
_FENCE_LINE_RE = re.compile(r'^[ \t]*```')


def _is_fence_line(line: str) -> bool:
    return bool(_FENCE_LINE_RE.match(line))


def _is_table_line(line: str) -> bool:
    return line.lstrip().startswith('|')


def _escape_fence_token(line: str) -> str:
    """Neutralise the backticks of a fence that has to live inside a cell.

    A markdown table cell cannot hold a fenced block. Leaving the raw ``` in
    the rejoined row lets the next table pass split the row again, which is how
    the original corruption was produced. Backslash-escaped backticks render as
    literal ``` and can never open a block.
    """
    return _FENCE_LINE_RE.sub(lambda m: '\\`\\`\\`', line, count=1)


def _rejoin_table_rows_split_by_fence(text: str, max_passes: int = 4) -> str:
    """Fold a fence line stuck inside a table back into its table row.

    Only acts when the fence is preceded by a table row AND the joined row can
    be completed by the following lines (ending in ``|``). A fence that merely
    follows a table, or sits inside real prose, is left untouched.
    """
    for _ in range(max_passes):
        lines = text.split('\n')
        changed = False
        i = 0
        while i < len(lines):
            if not (_is_fence_line(lines[i]) and i > 0):
                i += 1
                continue
            # Previous non-blank line must be a table row.
            p = i - 1
            while p >= 0 and lines[p].strip() == '':
                p -= 1
            if p < 0 or not _is_table_line(lines[p]):
                i += 1
                continue
            merged = lines[p].rstrip() + ' ' + _escape_fence_token(lines[i].strip())
            n = i + 1
            while not merged.rstrip().endswith('|') and n < len(lines):
                if lines[n].strip() == '':
                    break
                merged = merged.rstrip() + ' ' + lines[n].strip()
                n += 1
            if not merged.rstrip().endswith('|'):
                # Could not complete the row; refuse to guess, leave it alone.
                i += 1
                continue
            lines[p] = merged
            del lines[i:n]
            changed = True
            # Resume just after the row we rewrote.
            i = p + 1
        text = '\n'.join(lines)
        if not changed:
            break
    return text


def _balance_fences(text: str) -> str:
    """Ensure every fence is closed so the tail is never swallowed.

    A trailing lone fence is dropped (it would open an empty code block);
    an opening fence anywhere else gets a closing fence appended.
    """
    lines = text.split('\n')
    fence_idx = [i for i, ln in enumerate(lines) if _is_fence_line(ln)]
    if len(fence_idx) % 2 == 0:
        return text
    last_non_blank = max((i for i, ln in enumerate(lines) if ln.strip()), default=-1)
    if last_non_blank >= 0 and _is_fence_line(lines[last_non_blank]):
        del lines[last_non_blank]
        return '\n'.join(lines)
    return text.rstrip('\n') + '\n```\n'


_CODE_SPAN_IN_ROW = re.compile(r'(`+)(.+?)(?<!`)\1(?!`)')


def _repair_pipe_entity_scars(text: str) -> str:
    """Undo " &#124; " the old table normaliser saved into table rows.

    It split rows on every "|", including "\\|" and pipes inside `code`, and
    glued the pieces back with " &#124; " (table_normalize before
    2026-10-21). Saved replies keep that text, and a code span shows it
    literally: `rpm2cpio \\ &#124; cpio`. In a row, "\\ &#124; " was "\\|", and
    inside code " &#124; " was "|"; today's normaliser escapes that one again.
    """
    out = []
    for line in text.split('\n'):
        if line.lstrip().startswith('|') and '&#124;' in line:
            line = line.replace('\\ &#124; ', '\\| ')
            line = _CODE_SPAN_IN_ROW.sub(
                lambda m: m.group(1) + m.group(2).replace(' &#124; ', ' | ') + m.group(1),
                line)
        out.append(line)
    return '\n'.join(out)


def sanitize_prose_markdown(text: str) -> str:
    """Make saved assistant markdown safe to store and to re-render.

    Runs, in this order (the order matters):
      1. rejoin table rows broken by a stray fence (and escape its backticks),
      2. close any unbalanced fence,
      3. strip control tags (<task_summary> and friends).

    Step 2 must precede step 3: the tag stripper protects everything between
    two fences, so a closing fence left dangling at the end of an answer makes
    it pair with an inline fence earlier on and hide the task JSON from the
    stripper. Balancing first removes that accidental protection, which is also
    what makes this function idempotent.

    Idempotent: sanitising an already-clean block returns it unchanged.
    """
    if not text:
        return text
    # A glued opener ("```mermaidflowchart TB") must be repaired before the
    # fence balancer below, which counts openers and would otherwise pair a
    # glued one with a later closer and mangle the tail.
    text = split_glued_fence_languages(text)
    # Undo the old fence-rule scar on inline mentions: "` ``` `" was split into
    # three lines. A lone ``` between a line ending in a backtick and one starting
    # with a backtick is never a real block.
    text = re.sub(r'`[ \t]*\n([ \t]*```[a-zA-Z0-9_#+-]*)[ \t]*\n[ \t]*`',
                  lambda m: '` ' + m.group(1).strip() + ' `', text)
    if '&#124;' in text:
        text = _repair_pipe_entity_scars(text)
    text = _rejoin_table_rows_split_by_fence(text)
    text = _balance_fences(text)
    text = strip_all_control_tags(text)
    return text


def detect_content_type(text: str) -> str:
    """Detect the content type of AI response text."""
    if not text:
        return 'default'
    t = text.strip()

    if re.search(r'(?:^|\n)(?:Subject|To|From|Dear|Cc|Bcc)\s*:', t, re.IGNORECASE) and \
       re.search(r'(?:Regards|Sincerely|Best regards|Yours|Cordially)', t, re.IGNORECASE):
        return 'email'

    if re.search(r'(?:^|\s)#[\w]{2,}', t) and re.search(r'@[\w]{2,}', t):
        return 'social'

    if re.search(r'(?:^|\n)(?:Once upon|Chapter|Story|Poem|Narrative|Prologue|Epilogue)\b', t, re.IGNORECASE):
        return 'creative'

    has_md = bool(re.search(r'^#{1,6}\s', t, re.MULTILINE)) or '```' in t
    if re.match(r'^\s*(?:\\[a-zA-Z]+\{|\\sum|\\int|\\frac|\\sqrt|\\prod|\\lim|\\partial|\\nabla)', t) and not has_md:
        return 'equation'

    if len(t) < 150 and re.search(r'^\s*(?:The (?:result|answer|value) is|=)\s*[\d.]+\s*$', t, re.IGNORECASE | re.MULTILINE):
        return 'math'

    return 'default'


def escape_currency_dollars(text: str) -> str:
    """Escape bare $ that look like currency."""
    if not text:
        return text
    text = re.sub(r'(?<!\\)\$(?=\d)', r'\\$', text)
    return text


# ── Todo block patterns (strip from prose, route to widget) ──
# Matches a "# Todos" / "## Todos" / "Todos:" block followed by [ ]/[x]/[✓]/[•] lines
_TODO_BLOCK = re.compile(
    r'(?:^|\n)\s*#{0,3}\s*todos?\s*:?\s*\n'          # the "Todos" header
    r'(?:\s*[-*]?\s*\[[ xX✓✔•·]\].*\n?)+',           # one or more checkbox lines
    re.IGNORECASE
)

# Also catch loose checkbox lines that appear without a header
_TODO_LINES = re.compile(
    r'(?:^[ \t]*[-*]?\s*\[[ xX✓✔•·]\]\s.*$\n?){2,}',  # 2+ consecutive checkbox lines
    re.MULTILINE
)


def strip_todo_blocks(text: str) -> str:
    """Remove todo/checklist blocks from prose, they render in the TodoSection widget."""
    if not text:
        return text
    text = _TODO_BLOCK.sub('\n', text)
    text = _TODO_LINES.sub('', text)
    return text


def streaming_clean(text: str) -> str:
    """Lightweight cleaning for streaming flush, only essential passes.

    Unlike full_clean(), this skips expensive passes (URL cleaning,
    currency escaping) to avoid blocking the UI thread every 80ms.
    Full cleaning runs once in on_turn_done.
    """
    if not text:
        return text
    # Fix partial code fences (essential for streaming)
    text = auto_fix_broken_code_fences(text)
    # Stash code blocks to protect from tag stripping
    _code_blocks: list[str] = []
    def _stash(m):
        _code_blocks.append(m.group(0))
        return f'\x00CODEBLOCK{len(_code_blocks) - 1}\x00'
    text = re.sub(r'```[\s\S]*?```', _stash, text)
    # Stash tables BEFORE inline code so backticks in cells (`QMenuBar`, `#141414`)
    # stay protected. Otherwise inline stashing consumes them, the table stash
    # captures \x00INLINE{n}\x00 markers, and those leak as visible text on restore.
    _tables: list[str] = []
    def _stash_table(m):
        _tables.append(m.group(0))
        return f'\x00TABLE{len(_tables) - 1}\x00'
    text = re.sub(r'(?:^[ \t]*\|.+[ \t]*\n){2,}', _stash_table, text, flags=re.MULTILINE)
    # Stash inline code spans (`...`) to protect tree chars like `<src/`
    _inline_spans: list[str] = []
    def _stash_inline(m):
        _inline_spans.append(m.group(0))
        return f'\x00INLINE{len(_inline_spans) - 1}\x00'
    text = re.sub(r'`[^`\n]+`', _stash_inline, text)
    # Stash markdown links ([text](url))
    _links: list[str] = []
    def _stash_link(m):
        _links.append(m.group(0))
        return f'\x00LINK{len(_links) - 1}\x00'
    text = re.sub(r'\[[^\]]*\]\([^)]*\)', _stash_link, text)
    # Strip paired control tags (task_summary, file_edited, ...) during streaming:
    # without this the raw block leaks because the regex needs both tags. Only
    # AGENT control tags: 'summary'/'analysis'/'plan'/'thinking' can be real prose,
    # so their paired forms are handled by full_clean on turn_done instead.
    _STREAM_CONTROL_TAGS = (
        'file_edited', 'exploration', 'task_summary', 'tasklist',
        'permission', 'search', 'grep', 'glob', 'read_file', 'write_file',
        'edit_file', 'tool_result', 'tool_call',
        'terminal_output', 'agent_response', 'agent_instruction',
        'cortex_thought',
    )
    for tag in _STREAM_CONTROL_TAGS:
        text = re.sub(rf'<{tag}[\s\S]*?</{tag}>', '', text, flags=re.IGNORECASE)
    # Incomplete: opening tag present but closing tag not yet streamed
    # Strip ALL known tags (including prose-like ones) only when incomplete,
    # so partial <think> or <summary doesn't leak as visible text.
    for tag in _KNOWN_TAGS:
        text = re.sub(rf'<{tag}(?:\s[^>]*)?>[\s\S]*$', '', text, flags=re.IGNORECASE)
    # A live block starting mid-control-block ends with a lone closing tag: drop it
    # (Pass 1c). Runs after the rule above so an opening tag still hides the rest
    # while streaming.
    text = _ORPHAN_CONTROL_TAG_LINE.sub('', text)
    # A piece sealed mid-"files" array holds entry lines cut mid-object that no
    # paired regex matches; drop those fragments here too, not only in
    # strip_all_control_tags.
    text = _JSON_ENTRY_FRAG_LINE.sub('', text)
    # Strip HTML-like tags, only known HTML tags to protect tree chars like <src/
    _COMMON_HTML_TAGS = (
        'div|span|p|br|hr|img|a|b|i|u|em|strong|h[1-6]|ul|ol|li|table|tr|td|th|'
        'thead|tbody|pre|code|blockquote|button|input|select|option|textarea|form|'
        'label|section|article|nav|header|footer|aside|main|details|summary'
    )
    for _ in range(3):
        prev = text
        text = re.sub(rf'<({_COMMON_HTML_TAGS})(?:\s[^>]*)?>([\s\S]*?)</\1>', '', text, flags=re.IGNORECASE)
        if text == prev:
            break
    # Catch ANY tag with attributes (e.g. <div class="...">) but NOT bare <word
    text = re.sub(r'<(\w[\w-]+)\s+[^>]+>([\s\S]*?)</\1>', '', text)
    text = re.sub(rf'<({_COMMON_HTML_TAGS})\s*/>', '', text, flags=re.IGNORECASE)
    text = re.sub(rf'</({_COMMON_HTML_TAGS})\s*>', '', text, flags=re.IGNORECASE)
    # Restore protected regions
    for i, link in enumerate(_links):
        text = text.replace(f'\x00LINK{i}\x00', link)
    for i, span in enumerate(_inline_spans):
        text = text.replace(f'\x00INLINE{i}\x00', span)
    for i, block in enumerate(_code_blocks):
        text = text.replace(f'\x00CODEBLOCK{i}\x00', block)
    for i, tbl in enumerate(_tables):
        text = text.replace(f'\x00TABLE{i}\x00', tbl)
    return text.strip()


def full_clean(text: str) -> str:
    """Run the full cleaning pipeline on assistant response text."""
    if not text:
        return text
    # MUST run before strip_all_control_tags, its catch-all regex
    # destroys <pre><code> tags before we can convert them to fences.
    text = auto_fix_broken_code_fences(text)

    # Protect fenced blocks from prose cleaners: strip_all_control_tags deletes
    # HTML/JSON inside code, clean_assistant_response strips comment-like text, and
    # escape_currency_dollars injects \$ into shell code. Stash each balanced ```
    # block behind an inert placeholder, clean prose, restore verbatim.
    _code_blocks: list[str] = []
    def _stash(m):
        _code_blocks.append(m.group(0))
        return f'\x00CODEBLOCK{len(_code_blocks) - 1}\x00'
    text = re.sub(r'```[\s\S]*?```', _stash, text)

    # Protect tables from destructive cleaners: strip_all_control_tags and
    # clean_broken_windows_paths mangle cell content (hex codes, pipes, paths with
    # spaces). Stash contiguous pipe-delimited blocks: header row(s) + separator +
    # data row(s), optional blank lines between separator and data (common LLM
    # output), before cleaning.
    _tables: list[str] = []
    _TABLE_HEADER_RE = r'^[ \t]*\|(?![\s\-:]+\|?\s*$).+[ \t]*\n'
    _TABLE_SEP_RE_CL = r'^[ \t]*\|[\s\-:]+(?:\|[\s\-:]+)*\|?[ \t]*\n'
    _TABLE_DAT_RE_CL = r'(?:^[ \t]*\|.+[ \t]*\n?\n?)+'
# Primary: table with separator (header data)
    _TABLE_BLOCK_RE = re.compile(
        r'(?:' + _TABLE_HEADER_RE + r')+'
        + _TABLE_SEP_RE_CL + r'\n*'
        + _TABLE_DAT_RE_CL + r'\n*'
        , re.MULTILINE)
    # Fallback: any block of 2+ consecutive pipe rows (tables without separators)
    _TABLE_ANY_RE = re.compile(
        r'(?:^[ \t]*\|.+[ \t]*\n){2,}'
        , re.MULTILINE)
    def _stash_table(mm):
        _tables.append(mm.group(0))
        return f'\x00TABLE{len(_tables) - 1}\x00'
    text = _TABLE_BLOCK_RE.sub(_stash_table, text)
    # Fallback: stash remaining pipe blocks not caught by primary pattern
    text = _TABLE_ANY_RE.sub(_stash_table, text)

    text = strip_all_control_tags(text)
    text = strip_todo_blocks(text)          # Remove todo blocks (routed to widget)
    text = preprocess_thought_blocks(text)
    text = clean_assistant_response(text)
    text = clean_broken_windows_paths(text)
    text = clean_markdown_urls(text)
    text = escape_currency_dollars(text)

    # Restore the protected code blocks verbatim.
    for i, block in enumerate(_code_blocks):
        text = text.replace(f'\x00CODEBLOCK{i}\x00', block)

    # Restore the protected tables verbatim.
    for i, tbl in enumerate(_tables):
        text = text.replace(f'\x00TABLE{i}\x00', tbl)

    return text.strip()
