"""Spreadsheet -> JSON model reader for the in-editor grid preview.

Pure Python (no Qt). Runs on a worker thread from main_window and produces a
JSON-serialisable model that editor.html renders as a Google-Sheets-like,
strictly read-only grid:

  {
    "name":  "file.xlsx",
    "sheets": [
      {
        "name": "Sheet1",
        "rows": [ [cell, cell, ...], ... ],     # cell = str OR dict
        "merges": [{"r":0,"c":0,"rs":1,"cs":2}, ...],
        "n_rows": 12, "n_cols": 8,
        "trunc_rows": false, "trunc_cols": false
      }, ...
    ],
    "formulas": {"evaluated": 3, "fallback": 1}
  }

A cell is a plain string when it carries no styling; otherwise a dict:
  {"t": text, "bg": "#1F4E78", "fg": "#FFFFFF", "b": 1, "i": 1,
   "al": "l"|"c"|"r", "fx": 1}
"fx" marks a cell whose displayed text is the *formula itself* (the value
could not be computed) so the UI can render it muted.

Why this module exists (field bugs it fixes):
  * openpyxl data_only=True returns None for formula cells that were never
    cached (file saved by a script) -> the old viewer showed BLANK cells
    where Excel shows numbers. Here a small safe AST evaluator computes
    common formulas (=B2*2, =SUM(B2:B9), IF/ROUND/...) so the grid shows
    values like Excel; anything exotic falls back to showing the formula
    text instead of nothing.
  * The old viewer stacked every sheet into one long page. The model keeps
    sheets separate so the UI can offer Excel-like bottom tabs.
  * The old viewer dropped fill/font/merge info. The model carries it.
"""

from __future__ import annotations

import ast
import csv
import logging
import re
import threading
from datetime import date, datetime, time
from pathlib import Path

log = logging.getLogger(__name__)

# View caps: enough to read real analysis sheets, small enough that the
# Chromium table stays snappy on low-end office hardware.
MAX_ROWS = 500
MAX_COLS = 80

_REF_RE = re.compile(r'^\$?([A-Za-z]{1,3})\$?([0-9]+)$')
_RANGE_RE = re.compile(r'(\$?[A-Za-z]{1,3}\$?[0-9]+)\s*:\s*(\$?[A-Za-z]{1,3}\$?[0-9]+)')


class FormulaError(Exception):
    """Raised when the mini evaluator cannot compute a formula."""


# ── column letter helpers ────────────────────────────────────────────────

def col_to_index(letters: str) -> int:
    """'A'->0, 'Z'->25, 'AA'->26 ..."""
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def index_to_col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def parse_ref(token: str):
    m = _REF_RE.match(token.strip())
    if not m:
        return None
    return int(m.group(2)) - 1, col_to_index(m.group(1))   # (row0, col0)


# ── value formatting ─────────────────────────────────────────────────────

def format_value(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return format(v, ".10g")
    if isinstance(v, datetime):
        if v.hour == 0 and v.minute == 0 and v.second == 0:
            return v.strftime("%Y-%m-%d")
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, time):
        return v.strftime("%H:%M")
    return str(v)


def _num(v):
    """Coerce to float the way Excel does; empty -> 0."""
    if v is None or v == "":
        return 0.0
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            raise FormulaError(f"non-numeric text: {v!r}")
    raise FormulaError(f"non-numeric value: {v!r}")


def _text(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return str(v)


# ── safe mini formula evaluator ──────────────────────────────────────────
# AST-walk only: no exec/eval of user code. Unknown syntax raises
# FormulaError and the caller falls back to showing the formula text.

class _Range:
    __slots__ = ("cells",)

    def __init__(self, cells):
        self.cells = cells          # list of raw values


class FormulaEvaluator:
    """Evaluates simple Excel formulas against one sheet's cells.

    getter(row0, col0) -> cached value OR formula string of another cell.
    Results are memoised; reference cycles raise FormulaError.
    """

    _MAX_DEPTH = 24

    def __init__(self, getter):
        self._get = getter
        self._memo: dict = {}
        self._busy: set = set()

    # public ------------------------------------------------------------
    def eval_formula(self, formula: str):
        expr = formula.strip()
        if expr.startswith("="):
            expr = expr[1:].strip()
        if not expr or "!" in expr:          # cross-sheet refs unsupported
            raise FormulaError("unsupported reference")
        # Rewrite A1:B2 range arguments into _rng("A1","B2") calls so the
        # expression becomes valid Python grammar for ast.parse.
        expr = _RANGE_RE.sub(lambda m: '_rng("%s","%s")' % (m.group(1), m.group(2)), expr)
        # Drop $ absolute markers (we ignore absoluteness).
        expr = expr.replace("$", "")
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as e:
            raise FormulaError(f"syntax: {e}")
        return self._node(tree.body, 0)

    def cell_value(self, row0: int, col0: int, depth: int = 0):
        """Value of a referenced cell, evaluating its formula if needed."""
        key = (row0, col0)
        if key in self._memo:
            return self._memo[key]
        if key in self._busy or depth > self._MAX_DEPTH:
            raise FormulaError("circular reference")
        raw = self._get(row0, col0)
        val = raw
        if isinstance(raw, str) and raw.startswith("="):
            self._busy.add(key)
            try:
                val = self.eval_formula(raw)
            finally:
                self._busy.discard(key)
        self._memo[key] = val
        return val

    # AST walk ----------------------------------------------------------
    def _node(self, n, depth):
        if depth > self._MAX_DEPTH:
            raise FormulaError("too deep")
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            ref = parse_ref(n.id)
            if ref is None:
                raise FormulaError(f"unknown name {n.id}")
            return self.cell_value(ref[0], ref[1], depth + 1)
        if isinstance(n, ast.UnaryOp):
            v = self._node(n.operand, depth + 1)
            if isinstance(n.op, ast.USub):
                return -_num(v)
            if isinstance(n.op, ast.UAdd):
                return _num(v)
            if isinstance(n.op, ast.Not):
                return not _truthy(v)
            raise FormulaError("unary op")
        if isinstance(n, ast.BinOp):
            if isinstance(n.op, ast.BitAnd):          # Excel '&' concat
                return _text(self._node(n.left, depth + 1)) + \
                    _text(self._node(n.right, depth + 1))
            a = _num(self._node(n.left, depth + 1))
            b = _num(self._node(n.right, depth + 1))
            op = n.op
            if isinstance(op, ast.Add):
                return a + b
            if isinstance(op, ast.Sub):
                return a - b
            if isinstance(op, ast.Mult):
                return a * b
            if isinstance(op, ast.Div):
                if b == 0:
                    raise FormulaError("#DIV/0!")
                return a / b
            if isinstance(op, ast.Mod):
                if b == 0:
                    raise FormulaError("#DIV/0!")
                return a % b
            if isinstance(op, ast.Pow):
                return a ** b
            if isinstance(op, ast.FloorDiv):
                if b == 0:
                    raise FormulaError("#DIV/0!")
                return a // b
            raise FormulaError("binary op")
        if isinstance(n, ast.Compare):
            left = self._node(n.left, depth + 1)
            for op, right in zip(n.ops, n.comparators):
                rv = self._node(right, depth + 1)
                if not self._cmp(op, left, rv):
                    return False
                left = rv
            return True
        if isinstance(n, ast.BoolOp):
            if isinstance(n.op, ast.And):
                v = True
                for x in n.values:
                    v = self._node(x, depth + 1)
                    if not _truthy(v):
                        return v
                return v
            for x in n.values:
                v = self._node(x, depth + 1)
                if _truthy(v):
                    return v
            return v
        if isinstance(n, ast.IfExp):
            return self._node(n.body if _truthy(self._node(n.test, depth + 1))
                              else n.orelse, depth + 1)
        if isinstance(n, ast.Call):
            return self._call(n, depth)
        raise FormulaError(f"unsupported syntax {type(n).__name__}")

    @staticmethod
    def _cmp(op, a, b):
        try:
            if isinstance(op, ast.Eq):
                return _loose_eq(a, b)
            if isinstance(op, ast.NotEq):
                return not _loose_eq(a, b)
            an, bn = _num(a), _num(b)
            if isinstance(op, ast.Lt):
                return an < bn
            if isinstance(op, ast.LtE):
                return an <= bn
            if isinstance(op, ast.Gt):
                return an > bn
            if isinstance(op, ast.GtE):
                return an >= bn
        except FormulaError:
            return False
        raise FormulaError("compare op")

    def _call(self, n, depth):
        if not isinstance(n.func, ast.Name):
            raise FormulaError("indirect call")
        name = n.func.id.upper()

        def args():
            return [self._node(a, depth + 1) for a in n.args]

        def nums():
            out = []
            for a in args():
                if isinstance(a, _Range):
                    out.extend(_num(c) for c in a.cells)
                else:
                    out.append(_num(a))
            return out

        if name == "_RNG":
            if len(n.args) != 2 or not all(isinstance(a, ast.Constant)
                                           and isinstance(a.value, str) for a in n.args):
                raise FormulaError("bad range")
            r1, r2 = parse_ref(n.args[0].value), parse_ref(n.args[1].value)
            if r1 is None or r2 is None:
                raise FormulaError("bad range")
            r_lo, r_hi = min(r1[0], r2[0]), max(r1[0], r2[0])
            c_lo, c_hi = min(r1[1], r2[1]), max(r1[1], r2[1])
            if (r_hi - r_lo + 1) * (c_hi - c_lo + 1) > 5000:
                raise FormulaError("range too large")
            cells = [self.cell_value(rr, cc, depth + 1)
                     for rr in range(r_lo, r_hi + 1)
                     for cc in range(c_lo, c_hi + 1)]
            return _Range(cells)
        if name == "SUM":
            return sum(nums())
        if name == "AVERAGE":
            v = nums()
            if not v:
                raise FormulaError("#DIV/0!")
            return sum(v) / len(v)
        if name == "MIN":
            v = nums()
            return min(v) if v else 0
        if name == "MAX":
            v = nums()
            return max(v) if v else 0
        if name == "COUNT":
            return float(len(nums()))
        if name == "ABS":
            return abs(_num(self._node(n.args[0], depth + 1)))
        if name == "INT":
            return float(int(_num(self._node(n.args[0], depth + 1))))
        if name == "SQRT":
            v = _num(self._node(n.args[0], depth + 1))
            if v < 0:
                raise FormulaError("#NUM!")
            return v ** 0.5
        if name == "MOD":
            a = _num(self._node(n.args[0], depth + 1))
            b = _num(self._node(n.args[1], depth + 1))
            if b == 0:
                raise FormulaError("#DIV/0!")
            return a % b
        if name == "ROUND":
            a = _num(self._node(n.args[0], depth + 1))
            b = int(_num(self._node(n.args[1], depth + 1))) if len(n.args) > 1 else 0
            return round(a, b)
        if name == "IF":
            cond = _truthy(self._node(n.args[0], depth + 1))
            if len(n.args) < 3:
                return cond
            return self._node(n.args[1] if cond else n.args[2], depth + 1)
        if name == "IFERROR":
            try:
                return self._node(n.args[0], depth + 1)
            except FormulaError:
                return self._node(n.args[1], depth + 1) if len(n.args) > 1 else ""
        if name in ("CONCATENATE", "CONCAT"):
            return "".join(_text(a) for a in args()
                           if not isinstance(a, _Range))
        if name == "LEN":
            return float(len(_text(self._node(n.args[0], depth + 1))))
        raise FormulaError(f"unsupported function {name}")


def _truthy(v):
    if v is None or v == "" or v == 0 or v is False:
        return False
    if isinstance(v, str):
        return v.strip().upper() not in ("", "FALSE", "0")
    return True


def _loose_eq(a, b):
    if isinstance(a, str) or isinstance(b, str):
        return _text(a).lower() == _text(b).lower()
    return _num(a) == _num(b)


# ── style extraction (openpyxl) ──────────────────────────────────────────

def _rgb_hex(color) -> str | None:
    """Return '#RRGGBB' for concrete rgb colors, else None (theme colors
    are skipped: resolving them needs the workbook theme XML)."""
    try:
        if color is None or getattr(color, "type", None) != "rgb":
            return None
        rgb = (color.rgb or "")
        if isinstance(rgb, str) and len(rgb) == 8 and rgb != "00000000":
            return "#" + rgb[2:]
    except Exception:
        pass
    return None


def _contrast_text(hex_bg: str) -> str:
    """Pick a readable text color for a filled cell (Excel-like behavior).

    Theme font colors are not resolved (that needs the workbook theme XML),
    so a cell with a light fill and no explicit font color would otherwise
    inherit the viewer's theme text color — light gray on cream is
    invisible.  Derive the text color from the fill's WCAG luminance
    instead: dark text on light fills, light text on dark fills."""
    try:
        r = int(hex_bg[1:3], 16) / 255.0
        g = int(hex_bg[3:5], 16) / 255.0
        b = int(hex_bg[5:7], 16) / 255.0
    except (ValueError, IndexError):
        return "#1A1A1A"

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return "#1A1A1A" if lum > 0.45 else "#FFFFFF"


def _cell_style(cell) -> dict:
    st = {}
    bg = None
    try:
        fill = cell.fill
        if fill is not None and str(fill.fill_type) in ("solid", "patterned"):
            bg = _rgb_hex(fill.fgColor)
            if bg:
                st["bg"] = bg
        font = cell.font
        if font is not None:
            if font.bold:
                st["b"] = 1
            if font.italic:
                st["i"] = 1
            fg = _rgb_hex(font.color)
            if fg:
                st["fg"] = fg
        if bg and "fg" not in st:
            # Filled cell, theme/unset font color -> readable text either way
            st["fg"] = _contrast_text(bg)
        al = cell.alignment
        if al is not None and al.horizontal:
            h = str(al.horizontal)
            st["al"] = {"center": "c", "right": "r", "left": "l"}.get(h, "")
            if not st["al"]:
                st.pop("al", None)
    except Exception:
        pass
    return st


# ── model builders per format ────────────────────────────────────────────

def _build_xlsx(filepath: str) -> dict:
    import openpyxl

    # Two loads: data_only=True gives cached values, data_only=False gives
    # formula strings. Run them in parallel so the wall time stays ~one load.
    holder = {}

    def _load_vals():
        holder["vals"] = openpyxl.load_workbook(filepath, data_only=True, read_only=False)

    def _load_forms():
        holder["forms"] = openpyxl.load_workbook(filepath, data_only=False, read_only=False)

    t1 = threading.Thread(target=_load_vals)
    t2 = threading.Thread(target=_load_forms)
    t1.start(); t2.start()
    t1.join(); t2.join()
    wb_v, wb_f = holder["vals"], holder["forms"]

    stats = {"evaluated": 0, "fallback": 0}
    sheets = []
    for name in wb_v.sheetnames:
        ws_v = wb_v[name]
        ws_f = wb_f[name]

        # raw grid getters over the FULL sheet (formulas may reference rows
        # beyond the view cap) but bounded so a million-row sheet can't hang
        full_rows = min(ws_v.max_row or 0, 5000)
        full_cols = min(ws_v.max_column or 0, 200)

        val_grid = {}
        form_grid = {}
        for r in range(1, full_rows + 1):
            for c in range(1, full_cols + 1):
                v = ws_v.cell(row=r, column=c).value
                if v is not None:
                    val_grid[(r - 1, c - 1)] = v
            for c in range(1, full_cols + 1):
                f = ws_f.cell(row=r, column=c).value
                if isinstance(f, str) and f.startswith("="):
                    form_grid[(r - 1, c - 1)] = f

        def getter(r0, c0, vg=val_grid, fg=form_grid):
            if (r0, c0) in vg:
                return vg[(r0, c0)]
            return fg.get((r0, c0))

        ev = FormulaEvaluator(getter)

        n_rows = min(ws_v.max_row or 0, MAX_ROWS)
        n_cols = min(ws_v.max_column or 0, MAX_COLS)
        trunc_rows = (ws_v.max_row or 0) > n_rows
        trunc_cols = (ws_v.max_column or 0) > n_cols

        rows = []
        for r in range(n_rows):
            row = []
            for c in range(n_cols):
                v = val_grid.get((r, c))
                f = form_grid.get((r, c))
                text = format_value(v)
                cell_st = _cell_style(ws_v.cell(row=r + 1, column=c + 1))
                if v is None and f:
                    # Formula without cached value: compute it like Excel,
                    # fall back to showing the formula text (never blank).
                    try:
                        text = format_value(ev.eval_formula(f))
                        stats["evaluated"] += 1
                    except FormulaError:
                        text = f
                        cell_st["fx"] = 1
                        stats["fallback"] += 1
                if cell_st:
                    cell_st["t"] = text
                    row.append(cell_st)
                else:
                    row.append(text)
            rows.append(row)

        merges = []
        try:
            for mr in ws_v.merged_cells.ranges:
                r0, c0 = mr.min_row - 1, mr.min_col - 1
                if r0 >= n_rows or c0 >= n_cols:
                    continue
                merges.append({
                    "r": r0, "c": c0,
                    "rs": min(mr.max_row - mr.min_row + 1, n_rows - r0),
                    "cs": min(mr.max_col - mr.min_col + 1, n_cols - c0),
                })
        except Exception:
            pass

        sheets.append({
            "name": name, "rows": rows, "merges": merges,
            "n_rows": n_rows, "n_cols": n_cols,
            "trunc_rows": trunc_rows, "trunc_cols": trunc_cols,
        })
    return {"sheets": sheets, "formulas": stats}


def _build_xls(filepath: str) -> dict:
    import xlrd
    wb = xlrd.open_workbook(filepath)
    sheets = []
    for idx in range(wb.nsheets):
        ws = wb.sheet_by_index(idx)
        n_rows = min(ws.nrows, MAX_ROWS)
        n_cols = min(ws.ncols, MAX_COLS)
        rows = []
        for r in range(n_rows):
            row = []
            for c in range(n_cols):
                row.append(format_value(ws.cell_value(r, c)))
            rows.append(row)
        sheets.append({
            "name": ws.name, "rows": rows, "merges": [],
            "n_rows": n_rows, "n_cols": n_cols,
            "trunc_rows": ws.nrows > n_rows, "trunc_cols": ws.ncols > n_cols,
        })
    return {"sheets": sheets, "formulas": {"evaluated": 0, "fallback": 0}}


def _build_csv(filepath: str) -> dict:
    rows = []
    n_cols = 0
    with open(filepath, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        for i, row in enumerate(reader):
            if i >= MAX_ROWS:
                break
            n_cols = max(n_cols, len(row))
            rows.append(list(row))
    n_cols = min(n_cols, MAX_COLS)
    rows = [r[:n_cols] for r in rows]
    return {
        "sheets": [{
            "name": Path(filepath).stem or "Sheet1", "rows": rows, "merges": [],
            "n_rows": len(rows), "n_cols": n_cols,
            "trunc_rows": False, "trunc_cols": False,
        }],
        "formulas": {"evaluated": 0, "fallback": 0},
    }


def build_spreadsheet_model(filepath: str, ext: str) -> dict:
    """Parse a spreadsheet into the JSON model. Worker-thread only."""
    if ext == ".csv":
        model = _build_csv(filepath)
    elif ext == ".xls":
        model = _build_xls(filepath)
    else:
        model = _build_xlsx(filepath)
    model["name"] = Path(filepath).name
    return model
