"""
chat_text.py — Text/markdown cleaning pipeline for native chat
==============================================================

Ported from script.js functions:
  _cleanAssistantResponse, _preprocessThoughtBlocks, _stripAllControlTags,
  cleanBrokenWindowsPaths, cleanMarkdownUrls, autoFixBrokenCodeFences,
  detectContentType, escapeCurrencyDollars

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

    # 3. Markdown links ([text](url)) — angle brackets in URLs get mangled
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

    # Pass 2: Catch-all paired XML tags — only match tags with actual attribute
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

    # Pass 3: Self-closing tags — only known HTML or tags with attributes
    clean = re.sub(rf'<({_COMMON_RE})\s*\/>', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'<(\w[\w-]+)\s+[^>]*/>', '', clean)

    # Pass 4: Orphan closing tags — only known HTML
    clean = re.sub(rf'</({_COMMON_RE})\s*>', '', clean, flags=re.IGNORECASE)

    # Pass 5: Orphan opening tags — ONLY known HTML tags (not tree chars like <src/)
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

    # Pass 8: Raw JSON leaks — task_summary, file_edited, and other structured data
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
    # Fix fragmented filenames — ONLY match real Windows paths with backslashes
    # Must have at least one backslash after the drive letter to be a real path.
    # Without this guard, normal text like "C:\Users" followed by "but each"
    # gets matched and spaces are stripped: "but each" → "buteach".
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


def auto_fix_broken_code_fences(text: str) -> str:
    """Repair malformed markdown code fences."""
    if not text:
        return text

    fixed = text
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

    # ── Wrap bare indented code blocks in markdown fences ──
    # When AI outputs code indented with 4+ spaces (not in a list), Qt's
    # setMarkdown treats it as a <blockquote>, not <pre><code>. Detect
    # blocks of 3+ consecutive indented lines and wrap them in ``` fences.
    def _wrap_indented_code(m):
        block = m.group(0)
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
        body = '\n'.join(dedented).strip()
        if not body:
            return block
        return f'\n```\n{body}\n```\n'

    # Match blocks: 3+ consecutive lines all indented with 4+ spaces or 1+ tabs
    # Requires blank line before and after (or start/end of text)
    fixed = re.sub(
        r'(?<=\n)([ \t]{4,}[^\n]*\n){3,}(?=\n|$)',
        _wrap_indented_code,
        fixed,
    )

    # Auto-wrap plain Mermaid payloads
    if not re.search(r'```mermaid', fixed, re.IGNORECASE) and \
       re.match(r'^(?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|erDiagram|journey|gantt|pie|mindmap|timeline)\b', trimmed, re.IGNORECASE):
        fixed = f'```mermaid\n{trimmed}\n```'

    # Fix ```language async fn() → ```language\nasync fn()
    fixed = re.sub(r'```([a-zA-Z0-9_#+-]+)\s+(?=\S)', r'```\1\n', fixed)
    # Normalize ALL CRLF → LF inside code fence blocks (openings, content, closings)
    def _normalize_fence_crlf(m):
        return m.group(0).replace('\r\n', '\n')
    fixed = re.sub(r'```[a-zA-Z0-9_#+-]*[\s\S]*?```', _normalize_fence_crlf, fixed)
    # Ensure bare ``` (no lang) with content on same line gets newline
    fixed = re.sub(r'```\s+(?=\S)', r'```\n', fixed)

    # ── Move an opening fence that shares a line with preceding text onto its
    #    own line: "- **Web Browser**: ```bash" → "- **Web Browser**:\n```bash"
    #    so the markdown parser treats it as a block, not inline text.
    fixed = re.sub(r'(\S)[ \t]+(```[a-zA-Z0-9_#+-]*[ \t]*(?:\n|$))', r'\1\n\2', fixed)

    # ── Balance fences so NO code ever leaks as plain text ──
    # A fence WITH a language tag (```bash) is always an OPENING. If we hit one
    # while already inside a block, the previous block was never closed — insert
    # a closing ``` first. A bare ``` toggles. Any block still open at the end
    # gets a closing ``` appended. This guarantees every code region is wrapped.
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

    # ── Merge CONSECUTIVE fenced blocks into ONE block ──
    # The AI often emits each command as its own ```…``` fence, which renders as
    # fragmented dark rows with gaps. Collapse a closing fence followed by blank
    # lines and another opening fence so back-to-back code becomes a single block
    # (one <pre> / one code card). Runs after balancing so fences sit on own lines.
    fixed = re.sub(
        r'(?m)^```[ \t]*\n(?:[ \t]*\n)*^```[a-zA-Z0-9_#+-]*[ \t]*\n',
        '',
        fixed,
    )

    return fixed


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
    """Remove todo/checklist blocks from prose — they render in the TodoSection widget."""
    if not text:
        return text
    text = _TODO_BLOCK.sub('\n', text)
    text = _TODO_LINES.sub('', text)
    return text


def streaming_clean(text: str) -> str:
    """Lightweight cleaning for streaming flush — only essential passes.

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
    # Stash tables FIRST — before inline code stashing, so backtick content
    # inside table cells (e.g. `QMenuBar`, `#141414`) is protected.  Without
    # this order, the inline regex consumes table backticks, the table stash
    # captures \x00INLINE{n}\x00 markers, and those markers leak as visible
    # text when tables are restored after inline restore.
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
    # Strip known control tags (task_summary, file_edited, etc.) even when
    # the closing tag hasn't arrived yet during streaming.  Without this,
    # <task_summary>{"title":...}</task_summary> leaks as raw text because
    # the paired-tag regex below only matches when both tags are present.
    #
    # IMPORTANT: Only strip paired tags for AGENT CONTROL tags (tool output,
    # file operations).  Tags like 'summary', 'analysis', 'plan', 'thinking'
    # can appear in normal AI prose — stripping paired versions would delete
    # the AI's actual response.  Those are handled by full_clean on turn_done.
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
    # Strip HTML-like tags — only known HTML tags to protect tree chars like <src/
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
    # MUST run before strip_all_control_tags — its catch-all regex
    # destroys <pre><code> tags before we can convert them to fences.
    text = auto_fix_broken_code_fences(text)

    # ── Protect fenced code blocks from the prose-cleaning passes below ──
    # strip_all_control_tags (deletes <html> tags / JSON inside code),
    # clean_assistant_response (strips code comments that look like reasoning),
    # and escape_currency_dollars (injects \$ into shell code) all operate on
    # raw text and would CORRUPT code content. Stash each balanced ``` block
    # behind an inert placeholder, clean the prose, then restore verbatim.
    _code_blocks: list[str] = []
    def _stash(m):
        _code_blocks.append(m.group(0))
        return f'\x00CODEBLOCK{len(_code_blocks) - 1}\x00'
    text = re.sub(r'```[\s\S]*?```', _stash, text)

    # ── Protect markdown tables from destructive cleaners ──
    # strip_all_control_tags and clean_broken_windows_paths mangle table
    # cell content (hex codes like #1e1e1e, pipe chars, file paths with spaces).
    # Stash contiguous pipe-delimited blocks before cleaning.
    # Pattern matches: header row(s) + separator + data row(s), with
    # optional blank lines between separator and data rows (common LLM output).
    _tables: list[str] = []
    _TABLE_HEADER_RE = r'^[ \t]*\|(?![\s\-:]+\|?\s*$).+[ \t]*\n'
    _TABLE_SEP_RE_CL = r'^[ \t]*\|[\s\-:]+(?:\|[\s\-:]+)*\|?[ \t]*\n'
    _TABLE_DAT_RE_CL = r'(?:^[ \t]*\|.+[ \t]*\n?\n?)+'
    # Primary: table with separator (header + --- + data)
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
