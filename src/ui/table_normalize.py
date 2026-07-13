"""
table_normalize.py — Markdown table normalizer for native chat
==============================================================

Ported from tablerender.js. Provides 10+ normalizers for
malformed markdown tables, tab-delimited conversion, and
box-drawing character handling.

Phase 4 of the native chat migration.
"""

from __future__ import annotations
import re


def normalize_tabs_to_pipes(text: str) -> str:
    """Convert tab-delimited lines into pipe-delimited tables."""
    lines = text.split('\n')
    result = []
    i = 0
    while i < len(lines):
        block = []
        j = i
        while j < len(lines) and len(lines[j].split('\t')) >= 2:
            block.append(lines[j])
            j += 1
        if len(block) >= 2:
            for k, row in enumerate(block):
                cells = [c.strip() for c in row.split('\t')]
                result.append('| ' + ' | '.join(cells) + ' |')
                if k == 0:
                    result.append('| ' + ' | '.join(['---'] * len(cells)) + ' |')
            i = j
        else:
            result.append(lines[i])
            i += 1
    return '\n'.join(result)


def convert_unicode_box_drawing(text: str) -> str:
    """Convert Unicode box-drawing tables to pipe-delimited."""
    if '\u250c' not in text and '\u2502' not in text and \
       '\u251c' not in text and '\u2514' not in text:
        return text

    lines = text.split('\n')
    result = []
    in_box = False
    header_done = False

    for line in lines:
        # Top border: ┌──┬──┐
        if re.match(r'^[\u250c\u252c\u2510\u2500\u2502\s]+$', line) and '\u250c' in line:
            in_box = True
            header_done = False
            continue
        # Middle separator: ├──┼──┤
        if re.match(r'^[\u251c\u253c\u2524\u2500\u2502\s]+$', line) and '\u251c' in line:
            header_done = True
            continue
        # Bottom border: └──┴──┘
        if re.match(r'^[\u2514\u2534\u2518\u2500\u2502\s]+$', line) and '\u2514' in line:
            in_box = False
            continue
        # Pure separator lines
        if in_box and re.match(r'^[\u2500\u2502\s]+$', line):
            continue
        # Data row
        if in_box and '\u2502' in line:
            cells = [c.strip() for c in line.split('\u2502') if c.strip()]
            result.append('| ' + ' | '.join(cells) + ' |')
            if not header_done:
                result.append('| ' + ' | '.join(['---'] * len(cells)) + ' |')
                header_done = True
        else:
            result.append(line)
            if line.strip():
                in_box = False

    return '\n'.join(result)


def normalize_rows_without_leading_pipe(text: str) -> str:
    """Wrap rows like 'Cell A | Cell B' with leading/trailing pipes."""
    def _is_separator_line(s: str) -> bool:
        """Check if a line is a table separator (e.g., |---|---|)."""
        stripped = s.strip()
        # Must contain at least one - and one |
        if '-' not in stripped or '|' not in stripped:
            return False
        # After removing |, -, :, spaces — should be empty
        return bool(re.match(r'^[\|\-:\s]+$', stripped)) and bool(re.search(r'-{2,}', stripped))

    lines = text.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        tl = line.strip()
        is_sep_like = _is_separator_line(line)

        if tl and '|' in line and (tl[0] != '|' or is_sep_like) and tl[0] != '#':
            block = []
            j = i
            while j < len(lines):
                l = lines[j]
                lt = l.strip()
                l_is_sep = _is_separator_line(l)
                if lt and '|' in l and (lt[0] != '|' or l_is_sep) and lt[0] != '#' and \
                   not (re.match(r'^[\u2500\-\|=\\\s]+$', lt) and not l_is_sep):
                    # Skip separator lines — don't add to block
                    if l_is_sep:
                        j += 1
                        continue
                    block.append(l)
                    j += 1
                else:
                    break

            if len(block) >= 2:
                for k, row in enumerate(block):
                    cells = [c.strip() for c in row.split('|') if c.strip()]
                    if not cells:
                        continue
                    result.append('| ' + ' | '.join(cells) + ' |')
                    if k == 0:
                        result.append('| ' + ' | '.join(['---'] * len(cells)) + ' |')
                i = j
                continue
        result.append(line)
        i += 1
    return '\n'.join(result)


def normalize_table_block(lines: list[str]) -> list[str]:
    """Normalize a single table block: missing separators, alignment, stray pipes."""
    lines = [l for l in lines if l.replace('|', '').strip()]
    if len(lines) < 1:
        return lines

    # Find separator line
    sep_idx = -1
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.split('|') if c.strip()]
        if cells and all(re.match(r'^:?-{1,}:?$', c) for c in cells):
            sep_idx = i
            break

    # Insert separator if missing
    if sep_idx == -1 and len(lines) >= 2:
        first_cells = [c.strip() for c in lines[0].split('|') if c.strip()]
        sep = '| ' + ' | '.join(['---'] * len(first_cells)) + ' |'
        lines.insert(1, sep)

    return lines


def split_inline_tables(text: str) -> str:
    """Split collapsed inline tables (all rows on one line) into proper multi-line tables.

    Handles the common AI pattern where an entire table is on one line:
        Title| H1 | H2 | |---|---| |d1 | d2 | |d3 | d4 |

    Strategy: find the full separator substring (|---|---|...|), use it
    to split header from data, then split data rows by pipe count.
    """
    _FULL_SEP = re.compile(r'\|[\s:\-]+\|(?:[\s:\-]+\|)+')
    result = []
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        tl = line.lstrip()
        if tl.startswith('#') or tl.startswith('>') or tl.startswith('```'):
            result.append(line)
            i += 1
            continue

        sep_m = _FULL_SEP.search(line)
        if not sep_m or line.count('|') < 4:
            result.append(line)
            i += 1
            continue

        sep = sep_m.group(0).strip()
        n_cols = sep.count('|') - 1
        if n_cols < 2:
            result.append(line)
            i += 1
            continue

        before = line[:sep_m.start()].rstrip()
        after = line[sep_m.end():].lstrip()

        if not before and result and '|' in result[-1] and not result[-1].lstrip().startswith('#'):
            before = result.pop().rstrip()
            if before.endswith('|'):
                before = before[:-1].rstrip()

        if not before or not after:
            # CRITICAL: If we popped the header from result, restore it
            # before giving up — otherwise the header is silently eaten.
            if before and not after:
                if not before.startswith('|'):
                    before = '| ' + before
                if not before.endswith('|'):
                    before = before + ' |'
                result.append(before)
                # Also keep the separator line — it's a valid table separator
                result.append(line)
            elif not before and after:
                result.append(line)  # just the current line
            else:
                result.append(line)
            i += 1
            continue

        before = before.rstrip()
        if before.endswith('|'):
            before = before[:-1].rstrip()
        if after.startswith('|'):
            after = after[1:].lstrip()

        lines_out = []
        label = ''
        header = before
        if not header.lstrip().startswith('|'):
            idx = header.find('|')
            if idx > 0:
                label = header[:idx].rstrip()
                header = header[idx:]
        if not header.startswith('|'):
            header = '| ' + header
        if not header.endswith('|'):
            header = header + ' |'

        hdr_cols = header.count('|') - 1
        if hdr_cols != n_cols:
            result.append(line)
            continue

        if label:
            lines_out.append(label)
            lines_out.append('')
        lines_out.append(header)
        lines_out.append(sep)

        all_data = after
        if not all_data.endswith('|'):
            all_data = all_data + ' |'
        if not all_data.startswith('|'):
            all_data = '| ' + all_data
        dp = [pi for pi, ch in enumerate(all_data) if ch == '|']
        stride = n_cols + 1
        row_start = 0
        for k in range(stride - 1, len(dp), stride):
            end = dp[k] + 1
            row = all_data[row_start:end].strip()
            if row:
                lines_out.append(row)
            row_start = end
        tail = all_data[row_start:].strip()
        if tail and tail not in ('', '|'):
            if not tail.startswith('|'):
                tail = '| ' + tail
            if not tail.endswith('|'):
                tail += ' |'
            lines_out.append(tail)

        result.append('\n'.join(lines_out))
        i += 1
    return '\n'.join(result)


def stitch_orphan_table_rows(text: str) -> str:
    """Reattach a table row separated from its table by blank lines.

    Markdown table parsing requires contiguous `|...` rows. Models often emit a
    trailing summary row after one or more blank lines, e.g.:

        | Files   | 20 ... |

        | Changes | +974 / -2,371 |

    The blank line(s) break the table, so the last row renders as raw text with
    visible pipes. This removes ALL consecutive blank lines that sit strictly
    between two pipe rows — but NOT when the following row begins a new table
    (i.e. it is itself followed by a dash separator), so genuinely separate
    tables are never merged.

    Also handles multiple blank lines (e.g., 2-3 blank lines between data rows).
    """
    lines = text.split('\n')
    n = len(lines)
    result: list[str] = []
    # A separator row: pipes/dashes/colons only, and contains at least one dash.
    _sep_re = re.compile(r'^\s*\|?[\s\-:|]+\|?\s*$')

    def _is_sep(s: str) -> bool:
        return bool(_sep_re.match(s)) and '-' in s

    i = 0
    while i < n:
        line = lines[i]
        result.append(line)
        if line.strip().startswith('|'):
            # Skip ALL consecutive blank lines, then check for another pipe row
            j = i + 1
            while j < n and lines[j].strip() == '':
                j += 1
            if j < n and lines[j].strip().startswith('|'):
                # Don't stitch if the next row starts a NEW table (own separator)
                starts_new_table = (j + 1 < n and _is_sep(lines[j + 1]))
                if not starts_new_table:
                    i = j  # drop all blank lines; continue from the orphan row
                    continue
        i += 1
    return '\n'.join(result)


def normalize_blank_line_before_table(text: str) -> str:
    """Ensure a blank line precedes every table (line starting with |).

    Also inserts a blank line between two adjacent tables that have no blank
    line separator. Without this, the markdown parser merges them into one
    broken table. We detect a new table by checking if the current pipe row
    is followed by a separator row (--- pattern) — that means it's a table
    HEADER, not a continuation of the previous table's data.
    """
    lines = text.split('\n')
    n = len(lines)
    result = []

    def _is_sep(s: str) -> bool:
        return bool(re.match(r'^\s*\|?[\s\-:|]+\|?\s*$', s)) and '-' in s

    for i, line in enumerate(lines):
        stripped = line.strip()
        if i > 0 and stripped.startswith('|') and result and result[-1].strip():
            prev_stripped = result[-1].strip()
            prev_is_pipe = prev_stripped.startswith('|')
            prev_is_sep = bool(re.match(r'^[-\u2014\u2013\u2500=]+$', prev_stripped))

            # Case 1: pipe row follows non-pipe text → standard blank line insert
            if not prev_is_pipe and not prev_is_sep:
                result.append('')
            # Case 2: pipe row follows ANOTHER table's pipe row (adjacent tables)
            # Check if THIS pipe row is a new table HEADER (followed by separator)
            elif prev_is_pipe and not _is_sep(prev_stripped):
                is_new_table_header = (i + 1 < n and _is_sep(lines[i + 1]))
                if is_new_table_header:
                    result.append('')

        result.append(line)
    return '\n'.join(result)


def normalize_dash_separator_tables(text: str) -> str:
    """Convert tables using dash-only separators (like ------ ) to proper pipe format.

    Detects: header row with pipes but no leading |, optional blank lines,
    a dash-only separator line, then data rows with leading |.

    Example input:
        Biggest Changes | File | Added |

        ------------------------------
        | main_window.py | +1,113 | -17 |

    Example output:
        | Biggest Changes | File | Added |
        |------|------|------|
        | main_window.py | +1,113 | -17 |
    """
    lines = text.split('\n')
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # Strip heading prefix if present (e.g., "### Biggest Changes | File | Added")
        heading_prefix = ''
        m_heading = re.match(r'^(#{1,6}\s+)', stripped)
        if m_heading:
            heading_prefix = m_heading.group(1)
            stripped = stripped[m_heading.end():]

        # Candidate header: contains |, no leading |, not a quote
        if ('|' in line and not stripped.startswith('|')
                and not stripped.startswith('>')):
            # Look ahead for dash-only separator
            j = i + 1
            # Skip blank lines between header and separator
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines) or not re.match(r'^-{3,}$', lines[j].strip()):
                result.append(line)
                i += 1
                continue
            # Found dash separator \u2014 look for data rows
            k = j + 1
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k >= len(lines) or not lines[k].strip().startswith('|'):
                result.append(line)
                i += 1
                continue
            # Collect all consecutive data rows (skip blank lines between them)
            data_rows: list[str] = []
            while k < len(lines) and lines[k].strip().startswith('|'):
                data_rows.append(lines[k])
                k += 1
                while k < len(lines) and not lines[k].strip():
                    k += 1
            if not data_rows:
                result.append(line)
                i += 1
                continue
            # Build properly-formatted table
            header_cells = [c.strip() for c in stripped.split('|') if c.strip()]
            n_cols = len(header_cells)
            if heading_prefix:
                result.append(heading_prefix.rstrip())
            result.append('| ' + ' | '.join(header_cells) + ' |')
            result.append('| ' + ' | '.join(['---'] * n_cols) + ' |')
            result.extend(data_rows)
            result.append('')  # blank line after table
            i = k
            continue
        else:
            result.append(line)
            i += 1
    return '\n'.join(result)


def normalize_headerless_pipe_tables(text: str) -> str:
    """Fix tables where header row lacks leading | but separator/data rows have it.

    Catches patterns like:
        Git Status Summary| Status | Count |
                                              (optional blank lines)
        |--------|-------|
        | data1  | data2 |

    Also catches header rows directly followed by |---| separator (no blank line).
    """
    _SEP_RE = re.compile(r'^\s*\|(?:[-:\s]+\|)+\s*$')
    lines = text.split('\n')
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # Strip heading prefix if present (e.g., "### Audit Summary | Category | Status")
        # so "### Col1 | Col2 | Col3" becomes "Col1 | Col2 | Col3" for pipe table detection.
        heading_prefix = ''
        m_heading = re.match(r'^(#{1,6}\s+)', stripped)
        if m_heading:
            heading_prefix = m_heading.group(1)
            stripped = stripped[m_heading.end():]

        # Candidate: contains | but doesn't start with |, not quote/code
        if ('|' in stripped and not stripped.startswith('|')
                and not stripped.startswith('>')
                and not stripped.startswith('```')):
            # Look ahead past blank lines for a pipe separator
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and _SEP_RE.match(lines[j]):
                # This header needs a leading |
                cells = [c.strip() for c in stripped.split('|')]
                # Remove empty strings from split edges
                cells = [c for c in cells if c]
                if cells:
                    if heading_prefix:
                        # Use first cell as heading text, rest as table header cells.
                        # Need at least 2 remaining cells for a valid table.
                        if len(cells) >= 3:
                            heading_text = cells[0]
                            table_cells = cells[1:]
                            result.append(heading_prefix.rstrip() + ' ' + heading_text)
                            result.append('| ' + ' | '.join(table_cells) + ' |')
                        else:
                            # Not enough pipe segments for heading+table — leave line as-is
                            result.append(line)
                    else:
                        result.append('| ' + ' | '.join(cells) + ' |')
                    i += 1
                    # Skip blank lines between header and separator
                    while i < j:
                        i += 1
                    continue
        result.append(line)
        i += 1
    return '\n'.join(result)


def fix_table_column_counts(text: str) -> str:
    """Ensure all rows in a table have the same column count.

    Strict markdown parsers (mistune) reject tables where any row has
    a different column count than the header. This pads short rows with
    empty cells and rebuilds separators to match.
    """
    _SEP_RE = re.compile(r'^\s*\|(?:[-:\s]+\|)+\s*$')

    def _pipe_col_count(line: str) -> int:
        stripped = line.strip()
        if not stripped.startswith('|'):
            return 0
        parts = stripped.split('|')
        # Leading and trailing empty strings from split on |...|
        return len(parts) - 2 if stripped.endswith('|') else len(parts) - 1

    lines = text.split('\n')
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # Detect table start: pipe-delimited row followed by separator
        if stripped.startswith('|') and (i + 1) < len(lines) and _SEP_RE.match(lines[i + 1]):
            header_cols = _pipe_col_count(line)
            if header_cols < 2:
                result.append(line)
                i += 1
                continue
            # Collect the entire table block
            table_lines = [line]
            j = i + 1
            while j < len(lines):
                jl = lines[j].strip()
                if jl.startswith('|') or _SEP_RE.match(lines[j]):
                    table_lines.append(lines[j])
                    j += 1
                elif not jl:
                    # Blank line might be within table or end it
                    if j + 1 < len(lines) and lines[j + 1].strip().startswith('|'):
                        table_lines.append(lines[j])
                        j += 1
                    else:
                        break
                else:
                    break
            # Normalize all rows to header_cols
            for tl in table_lines:
                if _SEP_RE.match(tl):
                    result.append('| ' + ' | '.join(['---'] * header_cols) + ' |')
                elif tl.strip().startswith('|'):
                    cols = _pipe_col_count(tl)
                    if cols < header_cols:
                        cells = [c.strip() for c in tl.strip().strip('|').split('|')]
                        while len(cells) < header_cols:
                            cells.append('')
                        result.append('| ' + ' | '.join(cells) + ' |')
                    else:
                        result.append(tl)
                else:
                    result.append(tl)
            i = j
            continue
        result.append(line)
        i += 1
    return '\n'.join(result)


def fix_separator_first_tables(text: str) -> str:
    """Fix tables that start with a separator line and have no header row.

    Pattern:
        |---------|--------|---------|
        | data1   | data2  | data3  |
        | data4   | data5  | data6  |

    Generates a blank header so mistune recognizes it as a table.
    Also strips orphan trailing empty columns (e.g. ``| val | |``).
    """
    _SEP_RE = re.compile(r'^\s*\|(?:[-:\s]+\|)+\s*$')
    lines = text.split('\n')
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _SEP_RE.match(line):
            prev = result[-1].strip() if result else ''
            prev_is_pipe_row = prev.startswith('|') and not _SEP_RE.match(prev)
            if not prev_is_pipe_row:
                n_cols = line.strip().count('|') - 1
                if n_cols >= 2:
                    j = i + 1
                    data_rows = []
                    while j < len(lines):
                        rl = lines[j].strip()
                        if rl.startswith('|') and not _SEP_RE.match(rl):
                            data_rows.append(lines[j])
                            j += 1
                        elif not rl:
                            j += 1
                        else:
                            break
                    if data_rows:
                        first = data_rows[0].strip()
                        while first.endswith('| |'):
                            first = first[:-2].rstrip()
                        result.append(first)
                        sep = '| ' + ' | '.join(['---'] * n_cols) + ' |'
                        result.append(sep)
                        for dr in data_rows[1:]:
                            stripped_dr = dr.strip()
                            while stripped_dr.endswith('| |'):
                                stripped_dr = stripped_dr[:-2].rstrip()
                            result.append(stripped_dr)
                        i = j
                        continue
        result.append(line)
        i += 1
    return '\n'.join(result)


def _merge_excess_pipes(text: str) -> str:
    """Re-align table rows to the separator row's column count.

    The separator row (|---|---|...) is the *source of truth* for how many
    columns the table is supposed to have.  When an LLM emits cell content
    that itself contains ``|`` (e.g. ``FramelessWindowHint | WindowStaysOnTopHint``),
    the header / data rows end up with MORE pipe-splits than the separator.
    Mistune then creates phantom columns and the table breaks.

    Strategy: find the separator, count its columns (N), then merge excess
    cells in every other row back into the Nth cell so all rows have exactly
    N columns.
    """
    _SEP_RE = re.compile(r'^\s*\|([\s\-:]+\|)+\s*$')
    lines = text.split('\n')

    def _pipe_col_count(line: str) -> int:
        s = line.strip()
        if not s.startswith('|'):
            return 0
        parts = s.split('|')
        return len(parts) - 2 if s.endswith('|') else len(parts) - 1

    def _rebuild_row(cells: list[str], target: int) -> str:
        """Rebuild a row with exactly *target* columns, merging extras."""
        if len(cells) <= target:
            while len(cells) < target:
                cells.append('')
        else:
            good = cells[:target - 1]
            # Escape inner pipes so they don't create phantom columns
            remainder_parts = [c.strip() for c in cells[target - 1:]]
            remainder = ' &#124; '.join(remainder_parts)
            cells = good + [remainder]
        return '| ' + ' | '.join(c.strip() for c in cells) + ' |'

    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Detect table start: a pipe row followed by a separator row
        if (stripped.startswith('|') and not _SEP_RE.match(stripped)
                and i + 1 < len(lines) and _SEP_RE.match(lines[i + 1])):
            n_cols = _pipe_col_count(lines[i + 1])
            if n_cols < 1:
                result.append(line)
                i += 1
                continue

            # Fix header row
            hdr_parts = [p.strip() for p in stripped.split('|')]
            if stripped.endswith('|'):
                hdr_parts = hdr_parts[1:-1]
            else:
                hdr_parts = hdr_parts[1:]
            result.append(_rebuild_row(hdr_parts, n_cols))

            # Append separator as-is
            result.append(lines[i + 1])
            i += 2

            # Fix data rows
            while i < len(lines):
                dl = lines[i].strip()
                if not dl or not dl.startswith('|'):
                    break
                if _SEP_RE.match(lines[i]):
                    break
                parts = [p.strip() for p in dl.split('|')]
                if dl.endswith('|'):
                    parts = parts[1:-1]
                else:
                    parts = parts[1:]
                result.append(_rebuild_row(parts, n_cols))
                i += 1
            continue

        result.append(line)
        i += 1
    return '\n'.join(result)


def escape_pipes_in_table_cells(text: str) -> str:
    """Escape | characters inside table cell content that would break parsing.

    When an LLM emits table cell content containing literal pipe characters
    (e.g. CSS like ``border:1px solid |rgba(…)|`` or values like ``rgba(255,255,255,0.85)|
    from a mis-split row``), the extra pipes create phantom columns and shift
    all subsequent cells.  This function detects rows that have MORE pipe
    separators than the table's header row and merges excess cells back.

    Strategy:  Walk through consecutive pipe rows.  The first non-separator
    row sets the column count (N).  Any data row with more than N cells gets
    its excess cells merged back into the last real cell.
    """
    lines = text.split('\n')
    result: list[str] = []

    def _is_sep(s: str) -> bool:
        stripped = s.strip()
        if not stripped.startswith('|') or '-' not in stripped:
            return False
        parts = [p.strip() for p in stripped.split('|') if p.strip()]
        return bool(parts) and all(re.match(r'^:?-{1,}:?$', p) for p in parts)

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Detect a table: pipe row followed by separator
        if (stripped.startswith('|') and not _is_sep(stripped)
                and i + 1 < len(lines) and _is_sep(lines[i + 1])):
            # Count columns from header
            hdr_cells = [c.strip() for c in stripped.split('|') if c.strip() != '']
            # Actually, split on | for proper count: "|a|b|c|" → ['', 'a', 'b', 'c', '']
            # So real count = total_parts - 2 (leading/trailing empty)
            hdr_parts = stripped.split('|')
            n_cols = len(hdr_parts) - 2  # subtract leading + trailing empty
            if n_cols < 1:
                n_cols = len(hdr_cells)

            result.append(line)  # header
            i += 1
            # Separator row
            if i < len(lines):
                result.append(lines[i])
                i += 1
            # Data rows
            while i < len(lines):
                dl = lines[i].strip()
                if not dl or not dl.startswith('|'):
                    break
                if _is_sep(dl):
                    break
                parts = dl.split('|')
                # "|a|b|c|" → ['', 'a', 'b', 'c', ''] → 5 parts → n_cols = 3
                actual_parts = len(parts) - 2 if dl.endswith('|') else len(parts) - 1
                if actual_parts > n_cols:
                    # Merge excess cells back into the last cell
                    # Rebuild: first n_cols cells + remainder joined
                    data_parts = [p.strip() for p in parts[1:]]  # skip leading empty
                    if dl.endswith('|'):
                        data_parts = data_parts[:-1]  # skip trailing empty
                    good = data_parts[:n_cols - 1]
                    remainder = '|'.join(data_parts[n_cols - 1:])
                    merged = good + [remainder]
                    result.append('| ' + ' | '.join(merged) + ' |')
                else:
                    result.append(lines[i])
                i += 1
            continue

        result.append(line)
        i += 1

    return '\n'.join(result)


def normalize_table_markdown(text: str) -> str:
    """Run the full table normalization pipeline.
    
    CRITICAL: This runs on the main thread during streaming. For very large
    responses (>50KB), the 11 regex passes can block the UI for seconds.
    Skip normalization for large text to keep the UI responsive.
    
    A hard timeout (default 2 s) prevents a single buggy normalizer from
    freezing the entire UI — something that *has* happened when the AI agent
    edits this file and introduces catastrophic regex backtracking.
    """
    import threading

    # Size guard: skip table normalization for large text to prevent UI freeze.
    # Large responses rarely have inline tables that need splitting.
    # During streaming, setHtml() is called every 150ms — keep this fast.
    if len(text) > 50_000:
        return text

    _TIMEOUT_SEC = 1.0  # hard ceiling per normalization pass (reduced from 2.0s)

    def _run_pipeline(t: str) -> str:
        t = t.replace('\r\n', '\n')
        for fn in (
            normalize_tabs_to_pipes,
            convert_unicode_box_drawing,
            split_inline_tables,
            normalize_dash_separator_tables,
            normalize_headerless_pipe_tables,
            normalize_rows_without_leading_pipe,
            fix_separator_first_tables,
            stitch_orphan_table_rows,
            _merge_excess_pipes,
            fix_table_column_counts,
            normalize_blank_line_before_table,
            escape_pipes_in_table_cells,
        ):
            t = fn(t)
        return t

    # Use a worker thread + Event to implement a portable timeout.
    # signal.alarm is Unix-only; QTimer would require a QEventLoop.
    _result: list[str] = [text]
    _done = threading.Event()

    def _worker():
        try:
            _result[0] = _run_pipeline(text)
        except Exception:
            pass  # keep original text on failure
        finally:
            _done.set()

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    if not _done.wait(timeout=_TIMEOUT_SEC):
        # Timeout — return original text un-normalized rather than freeze UI
        import logging
        logging.getLogger(__name__).warning(
            "normalize_table_markdown TIMED OUT after %.1fs — "
            "returning unnormalized text (len=%d)", _TIMEOUT_SEC, len(text)
        )
        return text
    return _result[0]
