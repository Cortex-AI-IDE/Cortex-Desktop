"""
Icon factory — draws clean, thin-stroke VS Code-style icons via QPainter.
Returns QIcon objects ready to use in QPushButton or QAction.
"""
from PyQt6.QtGui import QPixmap, QIcon, QColor, QPainter, QPen, QPainterPath, QFont
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtSvg import QSvgRenderer
import os
from pathlib import Path


def _make_pixmap(size: int = 32) -> tuple[QPixmap, QPainter]:
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return px, p


def _pen(p: QPainter, color: str, width: float = 1.8):
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)


# ── Individual icon drawers ───────────────────────────────────────────────────

def _draw_folder(p: QPainter, s: int, color: str):
    """Solid VS Code style folder icon."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    # Main folder body
    p.drawRoundedRect(QRectF(s*0.05, s*0.25, s*0.9, s*0.65), 2, 2)
    # Folder tab
    tab = QPainterPath()
    tab.moveTo(s*0.1, s*0.25)
    tab.lineTo(s*0.1, s*0.15)
    tab.lineTo(s*0.4, s*0.15)
    tab.lineTo(s*0.45, s*0.25)
    p.drawPath(tab)


def _draw_new_file(p: QPainter, s: int, color: str):
    """VS Code-style New File: Document with border and plus."""
    border_color = color if color != "#ffffff" else "#555555"
    # Border/stroke first
    _pen(p, border_color, 1.5)
    p.setBrush(QColor(color))
    # Main Doc with border
    doc = QPainterPath()
    doc.moveTo(s*0.15, s*0.1)
    doc.lineTo(s*0.55, s*0.1)
    doc.lineTo(s*0.85, s*0.4)
    doc.lineTo(s*0.85, s*0.9)
    doc.lineTo(s*0.15, s*0.9)
    doc.closeSubpath()
    p.drawPath(doc)
    
    # Plus Shape with border
    plus_color = "#ffffff" if color != "#ffffff" else "#1e1e1e"
    _pen(p, plus_color, 1.0)
    p.setBrush(QColor(plus_color))
    pw, ph = s*0.08, s*0.25
    p.drawRect(QRectF(s*0.5, s*0.65, ph, pw)) # Horiz
    p.drawRect(QRectF(s*0.5 + ph/2 - pw/2, s*0.65 - (ph-pw)/2, pw, ph)) # Vert
    
    # Fold corner
    p.setBrush(QColor(color).lighter(125))
    p.setPen(Qt.PenStyle.NoPen)
    fold = QPainterPath()
    fold.moveTo(s*0.55, s*0.1)
    fold.lineTo(s*0.55, s*0.4)
    fold.lineTo(s*0.85, s*0.4)
    fold.closeSubpath()
    p.drawPath(fold)


def _draw_new_folder(p: QPainter, s: int, color: str):
    """VS Code-style New Folder: Folder with border and plus."""
    border_color = color if color != "#ffffff" else "#555555"
    # Border first
    _pen(p, border_color, 1.5)
    p.setBrush(QColor(color))
    # Body with border
    p.drawRoundedRect(QRectF(s*0.05, s*0.25, s*0.9, s*0.65), 1.5, 1.5)
    # Tab
    tab = QPainterPath()
    tab.moveTo(s*0.1, s*0.25)
    tab.lineTo(s*0.1, s*0.15)
    tab.lineTo(s*0.4, s*0.15)
    tab.lineTo(s*0.45, s*0.25)
    p.drawPath(tab)
    
    # Plus Shape with border
    plus_color = "#ffffff" if color != "#ffffff" else "#1e1e1e"
    _pen(p, plus_color, 1.0)
    p.setBrush(QColor(plus_color))
    pw, ph = s*0.08, s*0.25
    p.drawRect(QRectF(s*0.4, s*0.55, ph, pw)) # Horiz
    p.drawRect(QRectF(s*0.4 + ph/2 - pw/2, s*0.55 - (ph-pw)/2, pw, ph)) # Vert


def _draw_refresh(p: QPainter, s: int, color: str):
    """VS Code-style Refresh: Circular arrow with clean border."""
    # Thinner, cleaner stroke with slight border
    _pen(p, color, 2.0)
    rect = QRectF(s*0.2, s*0.2, s*0.6, s*0.6)
    p.drawArc(rect, 40 * 16, 280 * 16)
    # Arrow head
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    arrow = QPainterPath()
    arrow.moveTo(s*0.78, s*0.18)
    arrow.lineTo(s*0.95, s*0.38)
    arrow.lineTo(s*0.62, s*0.42)
    arrow.closeSubpath()
    p.drawPath(arrow)


def _draw_refresh_cw(p: QPainter, s: int, color: str):
    """Lucide-style Refresh CW: Circular arrow clockwise with chevron head."""
    _pen(p, color, 2.0)
    # Circular arc (clockwise)
    rect = QRectF(s*0.25, s*0.25, s*0.5, s*0.5)
    p.drawArc(rect, 0, 270 * 16)
    # Arrow head (chevron pointing clockwise)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    arrow = QPainterPath()
    arrow.moveTo(s*0.75, s*0.25)
    arrow.lineTo(s*0.85, s*0.15)
    arrow.lineTo(s*0.85, s*0.35)
    arrow.closeSubpath()
    p.drawPath(arrow)


def _draw_file_plus(p: QPainter, s: int, color: str):
    """Lucide-style File Plus: Document outline with plus sign."""
    _pen(p, color, 2.0)
    # Document body
    p.drawRoundedRect(QRectF(s*0.25, s*0.1, s*0.5, s*0.7), 2, 2)
    # Folded corner
    fold = QPainterPath()
    fold.moveTo(s*0.55, s*0.1)
    fold.lineTo(s*0.75, s*0.3)
    fold.lineTo(s*0.55, s*0.3)
    fold.closeSubpath()
    p.setBrush(QColor(color))
    p.drawPath(fold)
    # Plus sign at bottom
    _pen(p, color, 2.5)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawRect(QRectF(s*0.4, s*0.65, s*0.2, s*0.04))  # Horizontal
    p.drawRect(QRectF(s*0.48, s*0.57, s*0.04, s*0.2)) # Vertical


def _draw_folder_plus(p: QPainter, s: int, color: str):
    """Lucide-style Folder Plus: Folder outline with plus sign."""
    _pen(p, color, 2.0)
    # Folder outline
    path = QPainterPath()
    path.moveTo(s*0.15, s*0.35)
    path.lineTo(s*0.15, s*0.25)
    path.lineTo(s*0.35, s*0.25)
    path.lineTo(s*0.45, s*0.35)
    path.lineTo(s*0.85, s*0.35)
    path.lineTo(s*0.85, s*0.75)
    path.lineTo(s*0.15, s*0.75)
    path.closeSubpath()
    p.drawPath(path)
    # Plus sign inside
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawRect(QRectF(s*0.42, s*0.48, s*0.16, s*0.04))  # Horizontal
    p.drawRect(QRectF(s*0.48, s*0.42, s*0.04, s*0.16))  # Vertical


def _draw_collapse(p: QPainter, s: int, color: str):
    """Solid High-Fidelity Collapse icon."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    # Solid background with white cutout line
    p.drawRoundedRect(QRectF(s*0.15, s*0.15, s*0.7, s*0.7), 2, 2)
    p.setBrush(QColor("#ffffff") if color == "#c8c8c8" else QColor("#1e1e1e"))
    p.drawRect(QRectF(s*0.3, s*0.45, s*0.4, s*0.1)) # Horizontal minus cutout


def _draw_save(p: QPainter, s: int, color: str):
    """Save / floppy disk icon."""
    _pen(p, color, 1.8)
    m = s * 0.1
    # Outer square
    p.drawRoundedRect(QRectF(m, m, s - 2*m, s - 2*m), 2, 2)
    # Disk label area (top stripe)
    p.drawRect(QRectF(m + s*0.12, m, s*0.44, s*0.28))
    # Bottom storage area
    p.drawRect(QRectF(m + s*0.18, s*0.55, s*0.64, s*0.3))
    # Write notch (right side of label)
    _pen(p, color, 1.2)
    x = m + s*0.12 + s*0.44 - s*0.12
    p.drawLine(QPointF(x, m), QPointF(x, m + s*0.28))


def _draw_play(p: QPainter, s: int, color: str):
    """Play / run triangle icon."""
    path = QPainterPath()
    m = s * 0.2
    path.moveTo(m, m)
    path.lineTo(s - m, s * 0.5)
    path.lineTo(m, s - m)
    path.closeSubpath()
    pen = QPen(QColor(color))
    pen.setWidthF(1.8)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(QColor(color))
    p.drawPath(path)


def _draw_terminal(p: QPainter, s: int, color: str):
    """>_ terminal prompt icon."""
    _pen(p, color, 2.0)
    m = s * 0.12
    # Outer rectangle
    p.drawRoundedRect(QRectF(m, m, s - 2*m, s - 2*m), 3, 3)
    # Chevron >
    _pen(p, color, 2.0)
    cx, cy = s * 0.28, s * 0.5
    p.drawLine(QPointF(cx - s*0.08, cy - s*0.12), QPointF(cx + s*0.08, cy))
    p.drawLine(QPointF(cx + s*0.08, cy), QPointF(cx - s*0.08, cy + s*0.12))
    # Underscore _
    p.drawLine(QPointF(s*0.48, cy + s*0.13), QPointF(s*0.72, cy + s*0.13))


def _draw_search(p: QPainter, s: int, color: str):
    """Solid search icon - thicker stroke for 24px rendering."""
    _pen(p, color, 2.5)  # Thicker for small size
    r = s * 0.25
    p.drawEllipse(QRectF(s*0.45-r, s*0.45-r, r*2, r*2))
    p.drawLine(QPointF(s*0.65, s*0.65), QPointF(s*0.85, s*0.85))


def _draw_sparkles(p: QPainter, s: int, color: str):
    """Simple sparkles/star icon for AI."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    cx, cy = s*0.5, s*0.5
    # Draw a simple plus/star shape
    p.drawRect(QRectF(cx - s*0.06, s*0.2, s*0.12, s*0.6))   # Vertical
    p.drawRect(QRectF(s*0.2, cy - s*0.06, s*0.6, s*0.12))   # Horizontal


def _draw_git_branch(p: QPainter, s: int, color: str):
    """Simple git branch icon with circles and lines."""
    _pen(p, color, 2.5)
    r = s * 0.08
    # Top circle
    p.drawEllipse(QRectF(s*0.5-r, s*0.15-r, r*2, r*2))
    # Bottom left circle  
    p.drawEllipse(QRectF(s*0.25-r, s*0.75-r, r*2, r*2))
    # Bottom right circle
    p.drawEllipse(QRectF(s*0.75-r, s*0.75-r, r*2, r*2))
    # Vertical line
    p.drawLine(QPointF(s*0.5, s*0.23), QPointF(s*0.5, s*0.55))
    # Branch lines
    p.drawLine(QPointF(s*0.5, s*0.55), QPointF(s*0.25, s*0.67))
    p.drawLine(QPointF(s*0.5, s*0.55), QPointF(s*0.75, s*0.67))


def _draw_files(p: QPainter, s: int, color: str):
    """Document with plus icon for New File."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    # Document base
    p.drawRoundedRect(QRectF(s*0.15, s*0.1, s*0.7, s*0.8), 2, 2)
    # Folded corner
    p.setBrush(QColor("#ffffff" if color != "#ffffff" else "#858585"))
    corner = QPainterPath()
    corner.moveTo(s*0.55, s*0.1)
    corner.lineTo(s*0.85, s*0.1)
    corner.lineTo(s*0.85, s*0.4)
    corner.closeSubpath()
    p.drawPath(corner)
    
    # Plus sign (BIG and visible with stroke)
    plus_color = "#ffffff" if color != "#ffffff" else "#1e1e1e"
    _pen(p, plus_color, 1.0)  # Add stroke
    p.setBrush(QColor(plus_color))
    # Make plus much bigger and thicker
    p.drawRect(QRectF(s*0.25, s*0.47, s*0.5, s*0.06))  # Horizontal - THICK
    p.drawRect(QRectF(s*0.47, s*0.25, s*0.06, s*0.5))  # Vertical - THICK


def _draw_ai(p: QPainter, s: int, color: str):
    """Sparkle star / AI icon — 4-point star."""
    cx, cy = s * 0.5, s * 0.5
    path = QPainterPath()
    import math
    outer, inner = s * 0.38, s * 0.14
    for i in range(8):
        angle = math.radians(i * 45 - 90)
        r = outer if i % 2 == 0 else inner
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    pen = QPen(QColor(color))
    pen.setWidthF(1.5)
    p.setPen(pen)
    p.setBrush(QColor(color))
    p.drawPath(path)


def _draw_plus(p: QPainter, s: int, color: str):
    """Clean VS Code style plus (+) icon with integer alignment for sharpness."""
    _pen(p, color, 1.5)
    # Use integer calculations to prevent sub-pixel blur
    m = int(s * 0.3)
    c = s // 2
    # Horizontal line
    p.drawLine(m, c, s - m, c)
    # Vertical line
    p.drawLine(c, m, c, s - m)


def _draw_close(p: QPainter, s: int, color: str):
    """Clean VS Code style close (x) icon."""
    _pen(p, color, 1.5)
    m = int(s * 0.3)
    p.drawLine(m, m, s-m, s-m)
    p.drawLine(s-m, m, m, s-m)


def _draw_trash(p: QPainter, s: int, color: str):
    """Simple bin/trash icon."""
    _pen(p, color, 1.5)
    m = int(s * 0.25)
    # Lid
    p.drawLine(m, m+2, s-m, m+2)
    p.drawLine(s//2-2, m, s//2+2, m)
    # Body
    p.drawPolyline([
        QPointF(m+2, m+2), QPointF(m+2, s-m),
        QPointF(s-m-2, s-m), QPointF(s-m-2, m+2)
    ])
    # Vertical lines
    p.drawLine(s//2-1, m+5, s//2-1, s-m-3)
    p.drawLine(s//2+1, m+5, s//2+1, s-m-3)


def _draw_moon(p: QPainter, s: int, color: str):
    """Crescent moon icon."""
    m = s * 0.1
    # Full circle
    full = QPainterPath()
    full.addEllipse(QRectF(m, m, s - 2*m, s - 2*m))
    # Cutout circle (shifted right+up)
    cut = QPainterPath()
    cut.addEllipse(QRectF(m + s*0.2, m - s*0.05, s - 2*m + s*0.05, s - 2*m + s*0.05))
    crescent = full.subtracted(cut)
    pen = QPen(QColor(color))
    pen.setWidthF(0)
    p.setPen(pen)
    p.setBrush(QColor(color))
    p.drawPath(crescent)


def _draw_sun(p: QPainter, s: int, color: str):
    """Sun icon — circle + rays."""
    cx, cy = s * 0.5, s * 0.5
    _pen(p, color, 1.8)
    # Center circle
    p.drawEllipse(QPointF(cx, cy), s * 0.18, s * 0.18)
    # 8 rays
    import math
    for i in range(8):
        angle = math.radians(i * 45)
        x1 = cx + s * 0.26 * math.cos(angle)
        y1 = cy + s * 0.26 * math.sin(angle)
        x2 = cx + s * 0.4 * math.cos(angle)
        y2 = cy + s * 0.4 * math.sin(angle)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


# ── Language Icons ───────────────────────────────────────────────────────────

def _draw_python(p: QPainter, s: int, color: str):
    """Official Python logo — Multicolor Blue/Yellow snakes."""
    # Top Snake (Blue)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#3776AB"))
    path1 = QPainterPath()
    m = s * 0.1
    path1.moveTo(s*0.5, m)
    path1.lineTo(s*0.3, m)
    path1.arcTo(QRectF(m, m, s*0.4, s*0.4), 90, 180)
    path1.lineTo(s*0.5, s*0.4)
    path1.lineTo(s*0.5, s*0.5)
    path1.lineTo(s*0.7, s*0.5)
    path1.arcTo(QRectF(s*0.5, m, s*0.4, s*0.4), 270, -90)
    path1.lineTo(s*0.5, m)
    p.drawPath(path1)
    
    # Bottom Snake (Yellow)
    p.setBrush(QColor("#FFD43B"))
    path2 = QPainterPath()
    path2.moveTo(s*0.5, s-m)
    path2.lineTo(s*0.7, s-m)
    path2.arcTo(QRectF(s*0.5, s*0.5, s*0.4, s*0.4), 270, 180)
    path2.lineTo(s*0.5, s*0.6)
    path2.lineTo(s*0.5, s*0.5)
    path2.lineTo(s*0.3, s*0.5)
    path2.arcTo(QRectF(m, s*0.5, s*0.4, s*0.4), 90, -90)
    path2.lineTo(s*0.5, s-m)
    p.drawPath(path2)
    
    # Eyes
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(QRectF(s*0.22, s*0.2, s*0.08, s*0.08))
    p.drawEllipse(QRectF(s*0.7, s*0.72, s*0.08, s*0.08))


def _draw_js(p: QPainter, s: int, color: str):
    """Official JS icon — Yellow square with black text."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#F7DF1E"))
    p.drawRect(QRectF(0, 0, s, s))
    
    p.setPen(QColor("#000000"))
    font = QFont("Segoe UI", int(s * 0.38))
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(0, 0, s, s-s*0.05), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight, "JS ")


def _draw_ts(p: QPainter, s: int, color: str):
    """Official TS icon — Blue square with white text."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#3178C6"))
    p.drawRect(QRectF(0, 0, s, s))
    
    p.setPen(QColor("#ffffff"))
    font = QFont("Segoe UI", int(s * 0.38))
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(0, 0, s, s-s*0.05), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight, "TS ")


def _draw_js(p: QPainter, s: int, color: str):
    """JS icon — Square with 'JS'."""
    _pen(p, color, 1.5)
    m = s * 0.15
    p.drawRect(QRectF(m, m, s-2*m, s-2*m))
    font = QFont("Segoe UI", int(s * 0.35))
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(m, m, s-2*m, s-2*m), Qt.AlignmentFlag.AlignCenter, "JS")


def _draw_html(p: QPainter, s: int, color: str):
    """Official HTML5 icon — Orange shield."""
    p.setPen(Qt.PenStyle.NoPen)
    # Shield base
    p.setBrush(QColor("#E34F26"))
    path = QPainterPath()
    path.moveTo(s*0.1, s*0.05)
    path.lineTo(s*0.9, s*0.05)
    path.lineTo(s*0.82, s*0.85)
    path.lineTo(s*0.5, s*0.95)
    path.lineTo(s*0.18, s*0.85)
    path.closeSubpath()
    p.drawPath(path)
    # Light side
    p.setBrush(QColor("#F06529"))
    path2 = QPainterPath()
    path2.moveTo(s*0.5, s*0.1)
    path2.lineTo(s*0.85, s*0.1)
    path2.lineTo(s*0.78, s*0.88)
    path2.lineTo(s*0.5, s*0.95)
    path2.closeSubpath()
    p.drawPath(path2)
    # White 5
    p.setPen(QColor("#ffffff"))
    font = QFont("Segoe UI", int(s * 0.5))
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "5")


def _draw_css(p: QPainter, s: int, color: str):
    """Official CSS3 icon — Blue shield."""
    p.setPen(Qt.PenStyle.NoPen)
    # Shield base
    p.setBrush(QColor("#1572B6"))
    path = QPainterPath()
    path.moveTo(s*0.1, s*0.05)
    path.lineTo(s*0.9, s*0.05)
    path.lineTo(s*0.82, s*0.85)
    path.lineTo(s*0.5, s*0.95)
    path.lineTo(s*0.18, s*0.85)
    path.closeSubpath()
    p.drawPath(path)
    # Light side
    p.setBrush(QColor("#33A9DC"))
    path2 = QPainterPath()
    path2.moveTo(s*0.5, s*0.1)
    path2.lineTo(s*0.85, s*0.1)
    path2.lineTo(s*0.78, s*0.88)
    path2.lineTo(s*0.5, s*0.95)
    path2.closeSubpath()
    p.drawPath(path2)
    # White 3
    p.setPen(QColor("#ffffff"))
    font = QFont("Segoe UI", int(s * 0.5))
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "3")


def _draw_cpp(p: QPainter, s: int, color: str):
    """C++ icon."""
    _pen(p, color, 1.8)
    font = QFont("Segoe UI", int(s * 0.35))
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "C++")


def _draw_rust(p: QPainter, s: int, color: str):
    """Rust gear (simplified)."""
    _pen(p, color, 1.5)
    cx, cy = s*0.5, s*0.5
    r = s * 0.3
    p.drawEllipse(QPointF(cx, cy), r, r)
    # R inside
    font = QFont("Consolas", int(s * 0.3))
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "R")


def _draw_markdown(p: QPainter, s: int, color: str):
    """Official Markdown icon branding."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#007ACC"))
    p.drawRoundedRect(QRectF(s*0.05, s*0.2, s*0.9, s*0.6), 2, 2)
    
    p.setPen(QColor("#ffffff"))
    _pen(p, "#ffffff", 1.8)
    # M shape
    p.drawPolyline([
        QPointF(s*0.15, s*0.65), QPointF(s*0.15, s*0.35),
        QPointF(s*0.3, s*0.55), QPointF(s*0.45, s*0.35),
        QPointF(s*0.45, s*0.65)
    ])
    # Down arrow
    p.drawLine(QPointF(s*0.7, s*0.35), QPointF(s*0.7, s*0.6))
    p.drawPolyline([QPointF(s*0.6, s*0.5), QPointF(s*0.7, s*0.65), QPointF(s*0.8, s*0.5)])

def _draw_json(p: QPainter, s: int, color: str):
    """JSON icon (curly braces)."""
    _pen(p, color, 2.0)
    # Left {
    p.drawPolyline([
        QPointF(s*0.35, s*0.2), QPointF(s*0.2, s*0.2),
        QPointF(s*0.2, s*0.45), QPointF(s*0.1, s*0.5),
        QPointF(s*0.2, s*0.55), QPointF(s*0.2, s*0.8),
        QPointF(s*0.35, s*0.8)
    ])
    # Right }
    p.drawPolyline([
        QPointF(s*0.65, s*0.2), QPointF(s*0.8, s*0.2),
        QPointF(s*0.8, s*0.45), QPointF(s*0.9, s*0.5),
        QPointF(s*0.8, s*0.55), QPointF(s*0.8, s*0.8),
        QPointF(s*0.65, s*0.8)
    ])

def _draw_java(p: QPainter, s: int, color: str):
    """Official Java icon coffee cup."""
    # Steam (Red/Orange)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#ED8B00"))
    p.drawPolyline([QPointF(s*0.4, s*0.3), QPointF(s*0.5, s*0.1), QPointF(s*0.6, s*0.3)])
    p.setBrush(QColor("#E76F51"))
    p.drawPolyline([QPointF(s*0.3, s*0.35), QPointF(s*0.4, s*0.2), QPointF(s*0.5, s*0.35)])
    
    # Cup (Blue)
    p.setBrush(QColor("#0073B7"))
    path = QPainterPath()
    path.moveTo(s*0.2, s*0.4)
    path.lineTo(s*0.8, s*0.4)
    path.cubicTo(s*0.8, s*0.8, s*0.2, s*0.8, s*0.2, s*0.4)
    p.drawPath(path)
    # Handle
    hpath = QPainterPath()
    hpath.addEllipse(QRectF(s*0.7, s*0.45, s*0.2, s*0.2))
    p.drawPath(hpath)
    # Saucer
    p.drawEllipse(QRectF(s*0.2, s*0.8, s*0.6, s*0.1))

def _draw_php(p: QPainter, s: int, color: str):
    """PHP icon."""
    _pen(p, color, 1.8)
    font = QFont("Segoe UI", int(s * 0.4))
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "php")

def _draw_ruby(p: QPainter, s: int, color: str):
    """Ruby icon (diamond)."""
    _pen(p, color, 1.8)
    p.drawPolyline([
        QPointF(s*0.5, s*0.2), QPointF(s*0.8, s*0.45),
        QPointF(s*0.5, s*0.85), QPointF(s*0.2, s*0.45),
        QPointF(s*0.5, s*0.2)
    ])
    p.drawLine(QPointF(s*0.2, s*0.45), QPointF(s*0.8, s*0.45))
    p.drawLine(QPointF(s*0.35, s*0.2), QPointF(s*0.35, s*0.45))
    p.drawLine(QPointF(s*0.65, s*0.2), QPointF(s*0.65, s*0.45))

def _draw_database(p: QPainter, s: int, color: str):
    """Database icon (SQL)."""
    _pen(p, color, 1.8)
    p.drawEllipse(QRectF(s*0.2, s*0.2, s*0.6, s*0.2))
    p.drawArc(QRectF(s*0.2, s*0.4, s*0.6, s*0.2), 180 * 16, 180 * 16)
    p.drawArc(QRectF(s*0.2, s*0.6, s*0.6, s*0.2), 180 * 16, 180 * 16)
    p.drawLine(s*0.2, s*0.3, s*0.2, s*0.7)
    p.drawLine(s*0.8, s*0.3, s*0.8, s*0.7)

def _draw_go(p: QPainter, s: int, color: str):
    """Go icon."""
    _pen(p, color, 1.8)
    font = QFont("Segoe UI", int(s * 0.45))
    font.setBold(True)
    p.setFont(font)
    p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "Go")


# ── SVG-based Language Icons (VS Code style) ──────────────────────────────────

def _svg_icon(svg_data: str, size: int) -> QPixmap:
    """Render SVG data to a QPixmap."""
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(svg_data.encode('utf-8'))
    painter = QPainter(px)
    renderer.render(painter)
    painter.end()
    return px


# SVG icon data matching script.js FILE_ICONS
_SVG_PYTHON = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><defs><linearGradient id="pyg" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#387EB8"/><stop offset="100%" stop-color="#366994"/></linearGradient><linearGradient id="pyy" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#FFE052"/><stop offset="100%" stop-color="#FFC331"/></linearGradient></defs><path d="M15.9 5C10.3 5 10.7 7.4 10.7 7.4l.01 2.5h5.3v.7H8.7S5 10.1 5 15.8c0 5.7 3.2 5.5 3.2 5.5h1.9v-2.6s-.1-3.2 3.1-3.2h5.4s3 .05 3-2.9V8.5S22.1 5 15.9 5z" fill="url(#pyg)"/><circle cx="12.5" cy="8.2" r="1.1" fill="#fff" opacity=".8"/><path d="M16.1 27c5.6 0 5.2-2.4 5.2-2.4l-.01-2.5h-5.3v-.7h7.3S27 21.9 27 16.2c0-5.7-3.2-5.5-3.2-5.5h-1.9v2.6s.1 3.2-3.1 3.2h-5.4s-3-.05-3 2.9v4.6S9.9 27 16.1 27z" fill="url(#pyy)"/><circle cx="19.5" cy="23.8" r="1.1" fill="#fff" opacity=".8"/></svg>'''

_SVG_JAVASCRIPT = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="3" fill="#F7DF1E"/><path d="M20.8 24.3c.5.9 1.2 1.5 2.4 1.5 1 0 1.6-.5 1.6-1.2 0-.8-.7-1.1-1.8-1.6l-.6-.3c-1.8-.8-3-1.7-3-3.7 0-1.9 1.4-3.3 3.6-3.3 1.6 0 2.7.5 3.5 1.9l-1.9 1.2c-.4-.8-.9-1.1-1.6-1.1-.7 0-1.2.5-1.2 1.1 0 .8.5 1.1 1.6 1.5l.6.3c2.1.9 3.3 1.8 3.3 3.9 0 2.2-1.7 3.5-4 3.5-2.2 0-3.7-1.1-4.4-2.5l2-.1z" fill="#222"/><path d="M12.2 24.6c.4.6.7 1.2 1.6 1.2.8 0 1.3-.3 1.3-1.5V16h2.4v8.3c0 2.5-1.5 3.7-3.6 3.7-1.9 0-3-1-3.6-2.2l1.9-1.2z" fill="#222"/></svg>'''

_SVG_TYPESCRIPT = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="3" fill="#3178C6"/><path d="M18 17.4h3.4v.9H19v1.2h2.2v.9H19V23h-1V17.4zM9 17.4h5.8v1H12V23h-1v-4.6H9v-1z" fill="#fff"/><path d="M14.2 19.9c0-1.8 1.2-2.7 2.8-2.7.7 0 1.3.1 1.8.4l-.3.9c-.4-.2-.9-.3-1.4-.3-1 0-1.7.6-1.7 1.7 0 1.1.7 1.8 1.8 1.8.3 0 .6 0 .8-.1v-1.2H17v-.9h2v2.7c-.5.3-1.2.5-2 .5-1.8 0-2.8-1-2.8-2.8z" fill="#fff"/></svg>'''

_SVG_HTML = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M4 3l2.3 25.7L16 31l9.7-2.3L28 3z" fill="#E44D26"/><path d="M16 28.4V5.7l10.2 22.7z" fill="#F16529"/><path d="M9.4 13.5l.4 3.9H16v-3.9zM8.7 8H16V4.1H8.3zM16 21.5l-.05.01-4.1-1.1-.26-3h-3.9l.5 5.7 7.8 2.2z" fill="#EBEBEB"/><path d="M16 13.5v3.9h5.9l-.6 6.1-5.3 1.5v4l7.8-2.2.06-.6 1.2-13.1.12-1.6zm0-9.4v3.9h10.2l.08-1 .18-2.9z" fill="#fff"/></svg>'''

_SVG_CSS = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M4 3l2.3 25.7L16 31l9.7-2.3L28 3z" fill="#1572B6"/><path d="M16 28.4V5.7l10.2 22.7z" fill="#33A9DC"/><path d="M21.5 13.5H16v-3.9h6l.4-3.6H9.6L10 9.6h5.9v3.9H9.3l.4 3.6H16v4.1l-4.2-1.2-.3-3.1H7.7l.6 6.3 7.7 2.1z" fill="#fff"/><path d="M16 17.2v-3.7h5.1l-.5 5.2L16 19.9v4.1l7.7-2.1.1-.6 1-10.4.1-1.4H16v4zM16 5.7v3.9h5.7l.1-1 .2-2.9z" fill="#EBEBEB"/></svg>'''

_SVG_SCSS = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="13" fill="#CD6799"/><path d="M22.5 14.7c-.7-.3-1.1-.4-1.6-.6-.3-.1-.6-.2-.8-.3-.2-.1-.4-.2-.4-.4 0-.3.4-.6 1.2-.6.9 0 1.7.3 2.1.5l.8-1.8c-.5-.3-1.5-.7-2.9-.7-1.5 0-2.7.4-3.5 1.1-.7.7-1 1.5-.9 2.4.1.9.7 1.6 1.9 2.1.5.2 1 .3 1.4.5.3.1.5.2.7.3.2.2.3.4.2.7-.1.5-.7.8-1.5.8-1 0-1.9-.3-2.5-.7l-.8 1.9c.7.4 1.8.7 3 .7h.3c1.3-.05 2.4-.4 3.1-1.1.7-.7 1-1.5.9-2.5-.1-.9-.7-1.6-1.7-2.3zm-7.6-4.2c-1.5 0-2.8.5-3.7 1.3l-.8-1.2-2.1 1.2.9 1.4c-.6.9-1 2-1 3.2s.4 2.3 1.1 3.2l-1.1 1.2 1.6 1.4 1.2-1.3c.9.5 1.9.8 3.1.8 3.4 0 5.7-2.5 5.7-5.7-.1-3-2.1-5.5-4.9-5.5zm-.3 9c-1.9 0-3.2-1.4-3.2-3.3s1.3-3.3 3.2-3.3c.8 0 1.5.3 2 .8l-3.4 4.2c.4.4.9.6 1.4.6z" fill="#fff"/></svg>'''

_SVG_JAVA = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M12.2 22.1s-1.2.7.8 1c2.4.3 3.6.2 6.2-.2 0 0 .7.4 1.6.8-5.7 2.4-12.9-.1-8.6-1.6zM11.5 19s-1.3 1 .7 1.2c2.5.3 4.5.3 8-.4 0 0 .5.5 1.2.8-7.1 2.1-15-.2-9.9-1.6z" fill="#E76F00"/><path d="M17.2 13.4c1.4 1.7-.4 3.2-.4 3.2s3.6-1.9 2-4.2c-1.5-2.2-2.6-3.3 3.6-7.1 0 0-9.8 2.4-5.2 8.1z" fill="#E76F00"/><path d="M23.2 24.4s.9.7-.9 1.3c-3.4 1-14.1 1.3-17.1 0-1.1-.5.9-1.1 1.5-1.2.6-.1 1-.1 1-.1-1.1-.8-7.4 1.6-3.2 2.3 11.6 1.9 21.1-.8 18.7-2.3zM12.6 15.9s-5.3 1.3-1.9 1.8c1.5.2 4.4.2 7.1-.1 2.2-.3 4.5-.8 4.5-.8s-.8.3-1.3.7c-5.4 1.4-15.7.8-12.8-.7 2.5-1.3 4.4-1 4.4-.9zM20.6 20.8c5.4-2.8 2.9-5.6 1.2-5.2-.4.1-.6.2-.6.2s.2-.3.5-.4c3.6-1.3 6.4 3.8-1.1 5.8 0 0 .1-.1 0-.4z" fill="#E76F00"/><path d="M18.5 3s3 3-2.9 7.7c-4.7 3.8-1.1 5.9 0 8.3-2.7-2.5-4.7-4.7-3.4-6.7 2-3 7.5-4.4 6.3-9.3z" fill="#E76F00"/></svg>'''

_SVG_RUST = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M16 3L18.1 7.3 22.8 6.2 22.7 11 27.1 12.9 24.5 17 27.1 21.1 22.7 23 22.8 27.8 18.1 26.7 16 31 13.9 26.7 9.2 27.8 9.3 23 4.9 21.1 7.5 17 4.9 12.9 9.3 11 9.2 6.2 13.9 7.3z" fill="#DEA584"/><circle cx="16" cy="17" r="5" fill="none" stroke="#DEA584" stroke-width="2"/><circle cx="16" cy="17" r="2.5" fill="#DEA584"/></svg>'''

_SVG_GO = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M16 5C9.4 5 4 10.4 4 17s5.4 12 12 12 12-5.4 12-12S22.6 5 16 5zm0 21c-5 0-9-4-9-9s4-9 9-9 9 4 9 9-4 9-9 9z" fill="#00ACD7"/><circle cx="12.5" cy="14.5" r="1.3" fill="#00ACD7"/><circle cx="19.5" cy="14.5" r="1.3" fill="#00ACD7"/><path d="M13 19s.7 2 3 2 3-2 3-2H13z" fill="#00ACD7"/></svg>'''

_SVG_C = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="13" fill="#005B9F"/><path d="M22.5 20.4c-.8 2.5-3.1 4.3-5.8 4.3-3.4 0-6.1-2.7-6.1-6.1 0-3.4 2.7-6.1 6.1-6.1 2.8 0 5.1 1.9 5.9 4.4H20c-.6-1.3-1.9-2.1-3.3-2.1-2 0-3.7 1.6-3.7 3.7s1.7 3.7 3.7 3.7c1.5 0 2.7-.9 3.3-2.2h2.5z" fill="#fff"/></svg>'''

_SVG_CPP = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="13" fill="#00599C"/><path d="M18 20.4c-.8 2.5-3.1 4.3-5.8 4.3-3.4 0-6.1-2.7-6.1-6.1 0-3.4 2.7-6.1 6.1-6.1 2.8 0 5.1 1.9 5.9 4.4h-2.6c-.6-1.3-1.9-2.1-3.3-2.1-2 0-3.7 1.6-3.7 3.7s1.7 3.7 3.7 3.7c1.5 0 2.7-.9 3.3-2.2H18z" fill="#fff"/><path d="M21 13.3v1.5h-1.5V16H21v1.7h1.5V16H24v-1.2h-1.5v-1.5zm4.5 0v1.5H24V16h1.5v1.7H27V16h1.5v-1.2H27v-1.5z" fill="#fff"/></svg>'''

_SVG_CSHARP = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><defs><linearGradient id="csg2" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#9B4F96"/><stop offset="100%" stop-color="#68217A"/></linearGradient></defs><circle cx="16" cy="16" r="13" fill="url(#csg2)"/><path d="M10 19.8c-.8-2.1.1-4.6 2.1-5.8s4.5-1 6.3.5l-1 1.7c-1.2-.9-2.8-1.1-4.1-.3-1.3.7-1.9 2.2-1.5 3.6l-1.8.3zm12 0c-.5 1.4-1.7 2.5-3.1 2.8l-.4-1.9c.8-.2 1.4-.8 1.7-1.5l1.8.6z" fill="#fff"/><path d="M20 13.4h1.2v1.2H20zm0 2.4h1.2v1.2H20zm2.4-2.4h1.2v1.2h-1.2zm0 2.4h1.2v1.2h-1.2z" fill="#fff"/></svg>'''

_SVG_RUBY = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><defs><linearGradient id="rbg2" x1="0%" y1="100%" x2="100%" y2="0%"><stop offset="0%" stop-color="#FF0000"/><stop offset="100%" stop-color="#A30000"/></linearGradient></defs><path d="M22.9 5L27 9.1l.1 17.8-4.2 4.1H9L5 27.1 4.9 9.3 9 5z" fill="url(#rbg2)"/><path d="M11 10l-3 3v9l3 3h10l3-3v-9l-3-3zm.5 13l-2-2v-7l2-2h9l2 2v7l-2 2z" fill="#fff" opacity=".7"/><circle cx="16" cy="16" r="2.5" fill="#fff"/></svg>'''

_SVG_PHP = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><ellipse cx="16" cy="16" rx="14" ry="9" fill="#8892BF"/><path d="M10.5 12H8l-2 8h2l.5-2h2l.5 2h2zm-.5 4.5H9l.5-2h.5zm6.5-4.5h-3l-2 8h2l.5-2h1c1.7 0 3-1.3 3-3s-1.3-3-2.5-3zm-.5 4.5H16l.5-2h.5c.5 0 1 .5 1 1s-.5 1-1 1zm7.5-4.5h-3l-2 8h2l.5-2h2l.5 2h2zm-.5 4.5h-1l.5-2h.5z" fill="#fff"/></svg>'''

_SVG_JSON = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M12.7 6c-1.5 0-2.5.4-3 1.1-.5.7-.5 1.7-.5 2.5v2.2c0 .8-.2 1.5-.8 1.9-.3.2-.7.3-1.4.3v4c.7 0 1.1.1 1.4.3.6.4.8 1.1.8 1.9v2.2c0 .8 0 1.8.5 2.5.5.7 1.5 1.1 3 1.1H14v-2h-1.3c-.7 0-.9-.2-1-.4-.1-.2-.1-.7-.1-1.4v-2.2c0-1.2-.3-2.2-1.2-2.8-.2-.2-.5-.3-.8-.4.3-.1.5-.2.8-.4.9-.6 1.2-1.6 1.2-2.8V9.8c0-.7 0-1.2.1-1.4.1-.2.3-.4 1-.4H14V6h-1.3zm6.6 0v2h1.3c.7 0 .9.2 1 .4.1.2.1.7.1 1.4v2.2c0 1.2.3 2.2 1.2 2.8.2.2.5.3.8.4-.3.1-.5.2-.8.4-.9.6-1.2 1.6-1.2 2.8v2.2c0 .7 0 1.2-.1 1.4-.1.2-.3.4-1 .4H18v2h1.3c1.5 0 2.5-.4 3-1.1.5-.7.5-1.7.5-2.5v-2.2c0-.8.2-1.5.8-1.9.3-.2.7-.3 1.4-.3v-4c-.7 0-1.1-.1-1.4-.3-.6-.4-.8-1.1-.8-1.9V9.8c0-.8 0-1.8-.5-2.5C21.8 6.4 20.8 6 19.3 6z" fill="#F5A623"/></svg>'''

_SVG_MARKDOWN = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect x="2" y="7" width="28" height="18" rx="3" fill="#42A5F5"/><path d="M7 22V10h3l3 4 3-4h3v12h-3v-7l-3 4-3-4v7zm16 0l-4-6h2.5v-6h3v6H27z" fill="#fff"/></svg>'''

_SVG_SQL = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><ellipse cx="16" cy="10" rx="10" ry="4" fill="#4479A1"/><path d="M6 10v4c0 2.2 4.5 4 10 4s10-1.8 10-4v-4c0 2.2-4.5 4-10 4S6 12.2 6 10z" fill="#4479A1"/><path d="M6 14v4c0 2.2 4.5 4 10 4s10-1.8 10-4v-4c0 2.2-4.5 4-10 4S6 16.2 6 14z" fill="#336791"/><path d="M6 18v4c0 2.2 4.5 4 10 4s10-1.8 10-4v-4c0 2.2-4.5 4-10 4S6 20.2 6 18z" fill="#336791"/></svg>'''

_SVG_YAML = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="3" fill="#CC1018"/><path d="M7 9h2.5l3 5 3-5H18l-4.5 7v6h-2v-6zm11 4h7v2h-2.5v8h-2v-8H18z" fill="#fff"/></svg>'''

_SVG_DOCKER = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M28.8 14.5c-.5-.3-1.6-.5-2.5-.3-.1-.9-.7-1.7-1.6-2.3l-.5-.3-.4.4c-.5.6-.7 1.6-.6 2.3.1.5.3.9.6 1.3-.3.1-.8.3-1.5.3H4.1c-.3 1.3-.1 3 .9 4.2.9 1.2 2.3 1.9 4.3 1.9 4 0 7-1.8 8.9-5 1.1.1 3.4.1 4.6-2.2.1 0 .6-.3 1.6-.9l.5-.3-.1-.1z" fill="#2396ED"/><rect x="7" y="13" width="2" height="2" rx=".3" fill="#2396ED"/><rect x="9.7" y="13" width="2" height="2" rx=".3" fill="#2396ED"/><rect x="12.4" y="13" width="2" height="2" rx=".3" fill="#2396ED"/><rect x="15.1" y="13" width="2" height="2" rx=".3" fill="#2396ED"/><rect x="17.8" y="13" width="2" height="2" rx=".3" fill="#2396ED"/><rect x="12.4" y="11" width="2" height="2" rx=".3" fill="#2396ED"/><rect x="15.1" y="11" width="2" height="2" rx=".3" fill="#2396ED"/><rect x="17.8" y="11" width="2" height="2" rx=".3" fill="#2396ED"/><rect x="15.1" y="9" width="2" height="2" rx=".3" fill="#2396ED"/></svg>'''

_SVG_GIT = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M29.5 14.5L17.5 2.5c-.7-.7-1.8-.7-2.5 0L12.4 5l3 3c.7-.2 1.5 0 2 .6.6.5.8 1.3.6 2l2.9 2.9c.7-.2 1.5 0 2 .6.9.9.9 2.3 0 3.2-.9.9-2.3.9-3.2 0-.6-.6-.8-1.5-.5-2.2L16.5 12v8c.2.1.4.2.6.4.9.9.9 2.3 0 3.2-.9.9-2.3.9-3.2 0-.9-.9-.9-2.3 0-3.2.2-.2.5-.4.7-.5v-8c-.2-.1-.5-.3-.7-.5-.6-.6-.8-1.5-.5-2.2L10.5 6.1 2.5 14c-.7.7-.7 1.8 0 2.5l12 12c.7.7 1.8.7 2.5 0l12.5-12.5c.7-.7.7-1.8 0-2.5z" fill="#F34F29"/></svg>'''

_SVG_SHELL = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="3" fill="#1E1E1E"/><path d="M6 10l7 6-7 6" fill="none" stroke="#4EC9B0" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M16 22h10" stroke="#4EC9B0" stroke-width="2.5" stroke-linecap="round"/></svg>'''

_SVG_REACT = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="2.5" fill="#61DAFB"/><ellipse cx="16" cy="16" rx="11" ry="4.2" fill="none" stroke="#61DAFB" stroke-width="1.3"/><ellipse cx="16" cy="16" rx="11" ry="4.2" fill="none" stroke="#61DAFB" stroke-width="1.3" transform="rotate(60 16 16)"/><ellipse cx="16" cy="16" rx="11" ry="4.2" fill="none" stroke="#61DAFB" stroke-width="1.3" transform="rotate(120 16 16)"/></svg>'''

_SVG_VUE = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><polygon points="16,27 2,5 8.5,5 16,18.5 23.5,5 30,5" fill="#41B883"/><polygon points="16,20 9.5,9 13,9 16,14 19,9 22.5,9" fill="#35495E"/></svg>'''

_SVG_SVELTE = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M26.1 5.8c-2.8-4-8.4-5-12.4-2.3L7.2 7.7C5.3 9 4 11 3.8 13.3c-.2 1.9.3 3.8 1.4 5.3-.8 1.2-1.2 2.7-1.1 4.1.2 2.7 1.9 5.1 4.4 6.2 2.8 1.2 6 .7 8.3-1.2l6.5-4.2c1.9-1.3 3.2-3.3 3.4-5.6.2-1.9-.3-3.8-1.4-5.3.8-1.2 1.2-2.7 1.1-4.1-.1-1.1-.5-2.2-1.3-2.7z" fill="#FF3E00"/><path d="M13.7 27c-1.6.4-3.3 0-4.6-.9-1.8-1.3-2.5-3.5-1.8-5.5l.2-.5.4.3c1 .7 2 1.2 3.2 1.5l.3.1-.03.3c-.05.7.2 1.4.7 1.9.9.8 2.3.9 3.3.2l6.5-4.2c.6-.4 1-.9 1.1-1.6.1-.7-.1-1.4-.6-1.9-.9-.8-2.3-.9-3.3-.2l-2.5 1.6c-1.1.7-2.4 1-3.7.8-1.5-.2-2.8-1-3.6-2.2-1.4-2-1-4.7.9-6.2l6.5-4.2c1.6-1.1 3.7-1.3 5.5-.6 1.8.7 3 2.3 3.2 4.2.1.7 0 1.5-.3 2.2l-.2.5-.4-.3c-1-.7-2-1.2-3.2-1.5l-.3-.1.03-.3c.05-.7-.2-1.4-.7-1.9-.9-.8-2.3-.9-3.3-.2l-6.5 4.2c-.6.4-1 .9-1.1 1.6-.1.7.1 1.4.6 1.9.9.8 2.3.9 3.3.2l2.5-1.6c1.1-.7 2.4-1 3.7-.8 1.5.2 2.8 1 3.6 2.2 1.4 2 1 4.7-.9 6.2L18 26.3c-.8.5-1.5.8-2.3.7z" fill="#fff"/></svg>'''

_SVG_KOTLIN = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><defs><linearGradient id="kot" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#7F52FF"/><stop offset="50%" stop-color="#C811E1"/><stop offset="100%" stop-color="#E54857"/></linearGradient></defs><path d="M4 4h10l14 12-14 12H4L4 4z" fill="url(#kot)"/><path d="M18 4l14 12-14 12V4z" fill="url(#kot)" opacity=".6"/></svg>'''

_SVG_SWIFT = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><defs><linearGradient id="swf" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#F05138"/><stop offset="100%" stop-color="#F8981E"/></linearGradient></defs><path d="M16 4c-3 2-6 6-6 10s2 6 4 7c-1-3 1-7 4-9 2 2 4 5 3 9 2-1 4-3 4-7s-3-8-6-10h-3z" fill="url(#swf)"/><circle cx="16" cy="16" r="4" fill="#fff"/></svg>'''

_SVG_FOLDER = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M3 9c0-1.1.9-2 2-2h8l3 3h11c1.1 0 2 .9 2 2v13c0 1.1-.9 2-2 2H5c-1.1 0-2-.9-2-2V9z" fill="#4A90D9"/><path d="M3 13h26v11c0 1.1-.9 2-2 2H5c-1.1 0-2-.9-2-2V13z" fill="#5BA4E9"/></svg>'''

_SVG_ENV = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="3" fill="#4A9B4F"/><path d="M8 4h10l6 6v18H8V4z" fill="#5DBA5F"/><path d="M18 4v6h6" fill="none" stroke="#fff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><text x="16" y="22" font-family="Segoe UI,sans-serif" font-size="7" font-weight="bold" fill="#fff" text-anchor="middle">ENV</text></svg>'''

_SVG_TXT = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M8 4h10l6 6v18H8V4z" fill="#9AAABB"/><path d="M18 4v6h6" fill="none" stroke="#fff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><line x1="10" y1="13" x2="22" y2="13" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/><line x1="10" y1="17" x2="22" y2="17" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/><line x1="10" y1="21" x2="18" y2="21" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/></svg>'''

_SVG_ZIP = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="3" fill="#8E44AD"/><path d="M16 7l-2 2h-3l-1 3h3l-2 2 2 2h-3l1 3h3l2 2 2-2h3l1-3h-3l2-2-2-2h3l-1-3h-3z" fill="#F39C12"/><rect x="10" y="12" width="12" height="10" rx="1" fill="none" stroke="#fff" stroke-width="1.5"/></svg>'''

_SVG_DART = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><defs><linearGradient id="dart" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#0175C2"/><stop offset="100%" stop-color="#02569B"/></linearGradient></defs><path d="M16 4L4 12v12l12 8 12-8V12z" fill="url(#dart)"/><path d="M16 4v24l12-8V12z" fill="url(#dart)" opacity=".7"/><path d="M10 14h12v2H10zm2 4h8v2h-8z" fill="#fff"/></svg>'''

_SVG_LUA = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="13" fill="#000080"/><path d="M22 10c-2 0-3 1-3.5 2.5-.5-1-1.5-1.5-2.5-1.5-1.5 0-2.5 1-2.5 2.5 0 2 2 2.5 4 3 2 .5 4 1 4 3 0 1.5-1 2.5-2.5 2.5-2 0-3-1.5-4-3-.5 1.5-1.5 3-3 3v-2c1 0 2-.5 2.5-1.5.5 1 1.5 1.5 2.5 1.5 1.5 0 2.5-1 2.5-2.5 0-2-2-2.5-4-3-2-.5-4-1-4-3C12 8.5 13.5 7 16 7c1.5 0 2.5 1 3.5 2 .5-1.5 1.5-2.5 3-2.5v2z" fill="#fff"/></svg>'''

_SVG_R = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="3" fill="#276DC3"/><path d="M8 8h4v16h-4zM14 8h10l-2 5h-3l-1 3h3l-2 8H14z" fill="#fff"/></svg>'''

_SVG_JULIA = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="13" fill="#9558B2"/><circle cx="16" cy="16" r="9" fill="none" stroke="#fff" stroke-width="1.5"/><circle cx="16" cy="16" r="4" fill="#fff"/><path d="M16 7v4M16 21v4M7 16h4M21 16h4" stroke="#fff" stroke-width="1.5"/></svg>'''

_SVG_ZIG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><defs><linearGradient id="zig" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#F7A800"/><stop offset="100%" stop-color="#FF9500"/></linearGradient></defs><path d="M16 4L4 16l12 12 12-12z" fill="url(#zig)"/><path d="M16 10l-6 6 6 6 6-6z" fill="#000" opacity=".3"/></svg>'''

_SVG_ELIXIR = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><defs><linearGradient id="elx" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#4B275F"/><stop offset="100%" stop-color="#6E3A8E"/></linearGradient></defs><circle cx="16" cy="16" r="13" fill="url(#elx)"/><path d="M10 10c0-1 1-2 2-2h8c1 0 2 1 2 2v2l-6 8-6-8v-2z" fill="#fff"/><ellipse cx="16" cy="14" rx="4" ry="2" fill="#4B275F"/></svg>'''

_SVG_HASKELL = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><defs><linearGradient id="hs" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#5D4F85"/><stop offset="100%" stop-color="#453A6B"/></linearGradient></defs><path d="M8 4h10l6 6v18H8V4z" fill="url(#hs)"/><path d="M18 4v6h6" fill="none" stroke="#fff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><text x="16" y="22" font-family="Segoe UI,sans-serif" font-size="6" font-weight="bold" fill="#fff" text-anchor="middle">HS</text></svg>'''

_SVG_CLOJURE = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="13" fill="#588526"/><path d="M10 10l6 12 6-12H10z" fill="#96CA50"/><circle cx="16" cy="16" r="3" fill="#fff"/></svg>'''

_SVG_CONFIG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="3" fill="#607D8B"/><circle cx="16" cy="16" r="5" fill="none" stroke="#fff" stroke-width="2"/><path d="M16 5v4M16 23v4M5 16h4M23 16h4M8.5 8.5l2.8 2.8M20.7 20.7l2.8 2.8M8.5 23.5l2.8-2.8M20.7 11.3l2.8-2.8" stroke="#fff" stroke-width="2" stroke-linecap="round"/></svg>'''

_SVG_FILES = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M8 4h10l6 6v18H8V4z" fill="#90A4AE"/><path d="M18 4v6h6" fill="none" stroke="#fff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><line x1="10" y1="14" x2="22" y2="14" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/><line x1="10" y1="18" x2="22" y2="18" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/></svg>'''

_SVG_DEFAULT = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M8 4h10l6 6v18H8V4z" fill="#90A4AE"/><path d="M18 4v6h6" fill="none" stroke="#fff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'''


# SVG icon mapping
_SVG_FOLDER_BLUE = '''<svg viewBox="0 0 32 32"><defs><linearGradient id="fbg" x1="0%" y1="0%" x2="0%" y2="100%"><stop offset="0%" stop-color="#00BFFF"/><stop offset="100%" stop-color="#0099FF"/></linearGradient></defs><path d="M4 10a2 2 0 012-2h6l2 3h12a2 2 0 012 2v11a2 2 0 01-2 2H6a2 2 0 01-2-2V10z" fill="url(#fbg)"/><path d="M4 14.5a2 2 0 012-2h20a2 2 0 012 2v8.5a2 2 0 01-2 2H6a2 2 0 01-2-2v-8.5z" fill="#00AAFF" opacity="0.8"/></svg>'''

_SVG_ICONS = {
    'folder_blue': _SVG_FOLDER_BLUE,
    'python': _SVG_PYTHON,
    'javascript': _SVG_JAVASCRIPT,
    'typescript': _SVG_TYPESCRIPT,
    'html': _SVG_HTML,
    'css': _SVG_CSS,
    'scss': _SVG_SCSS,
    'java': _SVG_JAVA,
    'rust': _SVG_RUST,
    'go': _SVG_GO,
    'c': _SVG_C,
    'cpp': _SVG_CPP,
    'csharp': _SVG_CSHARP,
    'ruby': _SVG_RUBY,
    'php': _SVG_PHP,
    'dart': _SVG_DART,
    'lua': _SVG_LUA,
    'r': _SVG_R,
    'julia': _SVG_JULIA,
    'zig': _SVG_ZIG,
    'elixir': _SVG_ELIXIR,
    'haskell': _SVG_HASKELL,
    'clojure': _SVG_CLOJURE,
    'json': _SVG_JSON,
    'markdown': _SVG_MARKDOWN,
    'sql': _SVG_SQL,
    'yaml': _SVG_YAML,
    'docker': _SVG_DOCKER,
    'git': _SVG_GIT,
    'shell': _SVG_SHELL,
    'react': _SVG_REACT,
    'vue': _SVG_VUE,
    'svelte': _SVG_SVELTE,
    'kotlin': _SVG_KOTLIN,
    'swift': _SVG_SWIFT,
    'folder': _SVG_FOLDER,
    'env': _SVG_ENV,
    'txt': _SVG_TXT,
    'zip': _SVG_ZIP,
    'files': _SVG_FILES,
    'config': _SVG_CONFIG,
    'default': _SVG_DEFAULT,
    # Aliases — extensions mapped to an existing SVG icon
    'tsx': _SVG_REACT,       # React + TypeScript → React icon
    'jsx': _SVG_REACT,       # React + JavaScript → React icon
    'mjs': _SVG_JAVASCRIPT,  # ES module
    'cjs': _SVG_JAVASCRIPT,  # CommonJS module
    'sass': _SVG_SCSS,       # SASS = same icon as SCSS
    'less': _SVG_CSS,        # LESS → CSS-family
    'rs': _SVG_RUST,         # .rs extension → Rust
    'rb': _SVG_RUBY,         # .rb extension → Ruby
    'cs': _SVG_CSHARP,       # .cs extension → C#
    'ts': _SVG_TYPESCRIPT,   # .ts extension → TypeScript
    'py': _SVG_PYTHON,       # .py extension → Python
    'sh': _SVG_SHELL,        # shell scripts
    'bash': _SVG_SHELL,      # bash scripts
    'ps1': _SVG_SHELL,       # PowerShell (closest)
    'kt': _SVG_KOTLIN,       # .kt extension → Kotlin
    'jl': _SVG_JULIA,        # .jl extension → Julia
    'ex': _SVG_ELIXIR,       # .ex extension → Elixir
    'exs': _SVG_ELIXIR,      # .exs scripts → Elixir
    'hs': _SVG_HASKELL,      # .hs extension → Haskell
    'clj': _SVG_CLOJURE,     # .clj extension → Clojure
    'yml': _SVG_YAML,        # .yml extension → YAML
    'xml': _SVG_CONFIG,      # .xml → config icon
    'toml': _SVG_CONFIG,     # .toml → config icon
    'ini': _SVG_CONFIG,      # .ini → config icon
    'cfg': _SVG_CONFIG,      # .cfg → config icon
    'log': _SVG_TXT,         # log files
    'lock': _SVG_CONFIG,     # lock files
    'pdf': _SVG_FILES,        # PDF
    'gz': _SVG_ZIP,          # gzip → zip icon
    'tar': _SVG_ZIP,         # tarball → zip icon
    'rar': _SVG_ZIP,         # rar → zip icon
}


# ── High-Quality UI SVG Templates (Codicon/Lucide style, color-aware) ────────
# These use {color} placeholder for theme-aware monochrome rendering
_UI_SVG_TEMPLATES = {
    # ═══════════════════════════════════════════════════════════════════════════
    # Activity Bar Icons
    # ═══════════════════════════════════════════════════════════════════════════

    # Explorer: Two overlapping document pages
    "explorer": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
        <path d="M9 2h5l4 4v10a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" stroke="{color}" stroke-width="1.5"/>
        <path d="M14 2v4h4" stroke="{color}" stroke-width="1.5" stroke-linejoin="round" fill="none"/>
        <path d="M5 8v12a2 2 0 0 0 2 2h8" stroke="{color}" stroke-width="1.5" stroke-linecap="round" fill="none"/>
    </svg>''',

    # Search: Clean magnifying glass
    "search-panel": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
        <circle cx="10.5" cy="10.5" r="6.5" stroke="{color}" stroke-width="2"/>
        <line x1="15.5" y1="15.5" x2="21" y2="21" stroke="{color}" stroke-width="2.5" stroke-linecap="round"/>
    </svg>''',

    # Source Control: Git branch with nodes
    "source-control": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="6" y1="3" x2="6" y2="15"/>
        <circle cx="18" cy="6" r="3"/>
        <circle cx="6" cy="18" r="3"/>
        <path d="M18 9a9 9 0 0 1-9 9"/>
    </svg>''',

    # AI Tools: Four-point sparkle star
    "ai-panel": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}">
        <path d="M12 2l2.4 7.2L22 12l-7.6 2.8L12 22l-2.4-7.2L2 12l7.6-2.8z"/>
        <circle cx="19" cy="5" r="1.5"/>
    </svg>''',

    # Git Review: Branch with nodes (same as source-control)
    "git-review": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="6" y1="3" x2="6" y2="15"/>
        <circle cx="18" cy="6" r="3"/>
        <circle cx="6" cy="18" r="3"/>
        <path d="M18 9a9 9 0 0 1-9 9"/>
    </svg>''',

    # Chat History: Speech bubbles / comments icon
    "chat-history": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
    </svg>''',

    # Changed Files: File with modification dot
    "changed-files-panel": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" stroke="{color}" stroke-width="1.5"/>
        <path d="M14 2v6h6" stroke="{color}" stroke-width="1.5" stroke-linejoin="round" fill="none"/>
        <circle cx="12" cy="15" r="3" fill="{color}"/>
    </svg>''',

    # ═══════════════════════════════════════════════════════════════════════════
    # Explorer Toolbar Icons
    # ═══════════════════════════════════════════════════════════════════════════

    # New File: Document outline with plus sign
    "new-file": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
        <line x1="12" y1="18" x2="12" y2="12"/>
        <line x1="9" y1="15" x2="15" y2="15"/>
    </svg>''',

    # New Folder: Folder outline with plus sign
    "new-folder": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
        <line x1="12" y1="11" x2="12" y2="17"/>
        <line x1="9" y1="14" x2="15" y2="14"/>
    </svg>''',

    # Refresh: Circular arrows
    "refresh-explorer": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="23 4 23 10 17 10"/>
        <polyline points="1 20 1 14 7 14"/>
        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
    </svg>''',

    # Settings / Gear icon
    "settings": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="3"/>
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
    </svg>''',

    # Collapse All: Two chevrons pointing inward with center line
    "collapse-all": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 4 12 9 18 4"/>
        <line x1="4" y1="12" x2="20" y2="12"/>
        <polyline points="6 20 12 15 18 20"/>
    </svg>''',

    # ═══════════════════════════════════════════════════════════════════════════
    # Additional VS Code-style UI Icons
    # ═══════════════════════════════════════════════════════════════════════════

    # Close/X icon
    "close-icon": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round">
        <line x1="18" y1="6" x2="6" y2="18"/>
        <line x1="6" y1="6" x2="18" y2="18"/>
    </svg>''',

    # Ellipsis/More (three dots)
    "more-actions": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}">
        <circle cx="12" cy="5" r="2"/>
        <circle cx="12" cy="12" r="2"/>
        <circle cx="12" cy="19" r="2"/>
    </svg>''',

    # Docs/Book icon
    "docs-panel": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>
        <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
        <line x1="8" y1="7" x2="16" y2="7"/>
        <line x1="8" y1="11" x2="14" y2="11"/>
    </svg>''',

    # Debug/Play-Bug icon
    "debug-panel": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 2C9.24 2 7 4.24 7 7v1H5v3h2v2H5v3h2v1c0 2.76 2.24 5 5 5s5-2.24 5-5v-1h2v-3h-2v-2h2V8h-2V7c0-2.76-2.24-5-5-5z"/>
        <line x1="12" y1="2" x2="12" y2="22"/>
    </svg>''',

    # Extensions/Puzzle icon
    "extensions-panel": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.5">
        <rect x="3" y="3" width="8" height="8" rx="1.5"/>
        <rect x="13" y="3" width="8" height="8" rx="1.5"/>
        <rect x="3" y="13" width="8" height="8" rx="1.5"/>
        <rect x="13" y="13" width="8" height="8" rx="1.5"/>
    </svg>''',

    # Testing/Flask icon
    "testing-panel": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M9 3h6M10 3v5.2L5 19a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3L14 8.2V3"/>
        <path d="M7 15h10"/>
    </svg>''',

    # ═══════════════════════════════════════════════════════════════════════════
    # Panel Toggle Icons — 4 buttons, one per panel group
    # Each icon switches between visible (panel shown) ↔ hidden (panel collapsed)
    # ═══════════════════════════════════════════════════════════════════════════

    # ── 1. Left Sidebar: Layout with left pane ──
    "panel-left-sidebar-visible": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
        <rect x="3" y="3" width="18" height="18" rx="2" stroke="{color}" stroke-width="1.8"/>
        <rect x="3" y="3" width="7" height="18" rx="2" fill="{color}" opacity="0.35"/>
        <line x1="10" y1="3" x2="10" y2="21" stroke="{color}" stroke-width="1.2"/>
    </svg>''',
    "panel-left-sidebar-hidden": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
        <rect x="3" y="3" width="18" height="18" rx="2" stroke="{color}" stroke-width="1.8" stroke-dasharray="3,2"/>
        <polyline points="10,7 6,12 10,17" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>''',

    # ── 2. Right Sidebar (Explore): Layout with right pane ──
    "panel-right-sidebar-visible": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
        <rect x="3" y="3" width="18" height="18" rx="2" stroke="{color}" stroke-width="1.8"/>
        <rect x="14" y="3" width="7" height="18" rx="2" fill="{color}" opacity="0.35"/>
        <line x1="14" y1="3" x2="14" y2="21" stroke="{color}" stroke-width="1.2"/>
    </svg>''',
    "panel-right-sidebar-hidden": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
        <rect x="3" y="3" width="18" height="18" rx="2" stroke="{color}" stroke-width="1.8" stroke-dasharray="3,2"/>
        <polyline points="14,7 18,12 14,17" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>''',

    # ── 3. AI Chat: Speech bubble ──
    "panel-ai-chat-visible": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        <line x1="8" y1="9" x2="16" y2="9" stroke-width="1.5"/>
        <line x1="8" y1="13" x2="13" y2="13" stroke-width="1.5"/>
    </svg>''',
    "panel-ai-chat-hidden": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" stroke-dasharray="4,2"/>
        <line x1="9" y1="10" x2="15" y2="10" stroke-width="2"/>
        <line x1="12" y1="7" x2="12" y2="13" stroke-width="2"/>
    </svg>''',

    # ── 4. Review/Summary/Git Panel: Clipboard with checkmark ──
    "panel-review-visible": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/>
        <rect x="9" y="3" width="6" height="4" rx="1"/>
        <polyline points="9 14 11 16 15 12" stroke="#4ec94e" stroke-width="2"/>
        <line x1="9" y1="20" x2="15" y2="20" stroke-width="1.3"/>
    </svg>''',
    "panel-review-hidden": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" stroke-dasharray="4,2"/>
        <rect x="9" y="3" width="6" height="4" rx="1"/>
        <line x1="9" y1="14" x2="15" y2="14" stroke-width="1.5"/>
    </svg>''',

    # ── 5. Code Editor (Webview/Monaco): Angle brackets in a file ──
    "panel-code-visible": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
        <polyline points="9 15 7 12 9 9"/>
        <polyline points="15 9 17 12 15 15"/>
    </svg>''',
    "panel-code-hidden": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" stroke-dasharray="4,2"/>
        <polyline points="14 2 14 8 20 8"/>
        <line x1="9" y1="9" x2="15" y2="15" stroke-width="2"/>
        <line x1="15" y1="9" x2="9" y2="15" stroke-width="2"/>
    </svg>''',

    # ── 6. Play / Run: Triangle play button ──
    "play": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color}">
        <path d="M8 5v14l11-7z"/>
    </svg>''',

    # ── 7. Terminal Panel: Console prompt icon ──
    "panel-terminal-visible": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="4 17 10 11 4 5"/>
        <line x1="12" y1="19" x2="20" y2="19" stroke-width="1.5"/>
        <rect x="2" y="3" width="20" height="18" rx="2"/>
    </svg>''',
    "panel-terminal-hidden": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <rect x="2" y="3" width="20" height="18" rx="2" stroke-dasharray="4,2"/>
        <line x1="9" y1="9" x2="15" y2="15" stroke-width="2"/>
        <line x1="15" y1="9" x2="9" y2="15" stroke-width="2"/>
    </svg>''',

    # ═══════════════════════════════════════════════════════════════════════════
    # Chat List Action Icons — Pencil (rename) & Trash (delete)
    # ═══════════════════════════════════════════════════════════════════════════

    # Pencil icon — clean edit/rename icon (gold/amber color expected)
    "chat-pencil": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
    </svg>''',

    # Trash icon — clean wastebasket (red color expected)
    "chat-trash": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="3 6 5 6 21 6"/>
        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>
        <line x1="10" y1="11" x2="10" y2="17"/>
        <line x1="14" y1="11" x2="14" y2="17"/>
        <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
    </svg>''',
}


# ── Public API ────────────────────────────────────────────────────────────────

_DRAWERS = {
    "folder":   _draw_folder,
    "save":     _draw_save,
    "play":     _draw_play,
    "terminal": _draw_terminal,
    "search":   _draw_search,
    "sparkles": _draw_sparkles,
    "git-branch": _draw_git_branch,
    "moon":     _draw_moon,
    "sun":      _draw_sun,
    "python":   _draw_python,
    "javascript": _draw_js,
    "typescript": _draw_ts,
    "html":     _draw_html,
    "css":      _draw_css,
    "cpp":      _draw_cpp,
    "rust":     _draw_rust,
    "go":       _draw_go,
    "markdown": _draw_markdown,
    "json":     _draw_json,
    "java":     _draw_java,
    "php":      _draw_php,
    "ruby":     _draw_ruby,
    "sql":      _draw_database,
    "new_file": _draw_new_file,
    "new_folder": _draw_new_folder,
    "refresh":    _draw_refresh,
    # Lucide-style professional icons for sidebar
    "file-plus":  _draw_file_plus,
    "folder-plus": _draw_folder_plus,
    "refresh-cw": _draw_refresh_cw,
    "collapse":   _draw_collapse,
    "plus":       _draw_plus,
    "close":      _draw_close,
    "trash":      _draw_trash,
}


# Global cache to prevent redundant drawing operations
_ICON_CACHE: dict[tuple[str, str, int], QIcon] = {}


# ── OpenCode Sprite-based Icon Loader ────────────────────────────────────────
# Loads individual symbol SVGs from the file-icons/sprite.svg bundled with the app.

import xml.etree.ElementTree as _ET
import re as _re

# Parse the sprite XML only once and cache the tree
_SPRITE_TREE: object = None

_SPRITE_PATH: str | None = None
_SPRITE_SYMBOL_CACHE: dict[str, str] = {}   # symbol_id → standalone SVG string
_SPRITE_ICON_CACHE: dict[tuple[str, int], QIcon] = {}


def _find_sprite_path() -> str | None:
    """Locate sprite.svg relative to this file or inside a PyInstaller bundle."""
    import sys
    candidates = [
        # Dev layout: src/utils/icons.py  →  src/ui/html/ai_chat/file-icons/sprite.svg
        Path(__file__).parent.parent / "ui" / "html" / "ai_chat" / "file-icons" / "sprite.svg",
        # PyInstaller: sys._MEIPASS layout mirrors the datas spec
        Path(getattr(sys, '_MEIPASS', '')) / "src" / "ui" / "html" / "ai_chat" / "file-icons" / "sprite.svg",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _get_sprite_tree(sprite_path: str):
    """Parse sprite.svg once and cache the ElementTree globally."""
    global _SPRITE_TREE
    if _SPRITE_TREE is None:
        _ET.register_namespace('', 'http://www.w3.org/2000/svg')
        _ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
        _SPRITE_TREE = _ET.parse(sprite_path)
    return _SPRITE_TREE


def _extract_symbol(sprite_path: str, symbol_id: str) -> str | None:
    """
    Extract <symbol id='symbol_id'> from sprite.svg as a self-contained SVG.

    Key fixes:
    - Only include <defs> entries actually referenced by this symbol
    - Scope all id="X" → id="SYM_X" and url(#X) → url(#SYM_X)
      so Qt never sees duplicate style ids across icons → no black gradients.
    """
    cache_key = f"{sprite_path}#{symbol_id}"
    if cache_key in _SPRITE_SYMBOL_CACHE:
        cached = _SPRITE_SYMBOL_CACHE[cache_key]
        return cached if cached else None

    try:
        tree = _get_sprite_tree(sprite_path)
        root = tree.getroot()

        # ── 1. Find the target <symbol> ────────────────────────────────────
        sym = None
        for el in root.iter():
            local = el.tag.split('}')[-1] if '}' in el.tag else el.tag
            if local == 'symbol' and el.get('id') == symbol_id:
                sym = el
                break

        if sym is None:
            _SPRITE_SYMBOL_CACHE[cache_key] = ''
            return None

        vb = sym.get('viewBox', '0 0 32 32')

        # ── 2. Serialize symbol children ───────────────────────────────────
        body = ''.join(
            _ET.tostring(child, encoding='unicode') for child in sym
        )

        # ── 3. Find which def IDs this symbol actually uses ────────────────
        used_ids: set[str] = set()
        used_ids |= set(_re.findall(r'url\(#([^)]+)\)', body))
        used_ids |= set(_re.findall(r'href=["\']#([^"\']+)["\']', body))

        # ── 4. Collect only the relevant <defs> from the sprite root ────────
        needed_defs: list[str] = []
        if used_ids:
            for el in root:
                local = el.tag.split('}')[-1] if '}' in el.tag else el.tag
                if local == 'defs':
                    for def_child in el:
                        def_str = _ET.tostring(def_child, encoding='unicode')
                        # Keep this def only if it defines a used ID
                        def_ids = set(_re.findall(r'\bid=["\']([^"\']+)["\']', def_str))
                        if def_ids & used_ids:
                            needed_defs.append(def_str)

        # ── 5. Scope all IDs with symbol-unique prefix ─────────────────────
        prefix = f"x{symbol_id}_"

        def scope(text: str) -> str:
            text = _re.sub(r'\bid="([^"]+)"',
                           lambda m: f'id="{prefix}{m.group(1)}"', text)
            text = _re.sub(r'url\(#([^)]+)\)',
                           lambda m: f'url(#{prefix}{m.group(1)})', text)
            text = _re.sub(r'href="#([^"]+)"',
                           lambda m: f'href="#{prefix}{m.group(1)}"', text)
            return text

        scoped_defs = scope(''.join(needed_defs))
        scoped_body = scope(body)

        defs_block = f'<defs>{scoped_defs}</defs>' if scoped_defs else ''

        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'viewBox="{vb}">'
            + defs_block
            + scoped_body
            + '</svg>'
        )
        _SPRITE_SYMBOL_CACHE[cache_key] = svg
        return svg

    except Exception:
        _SPRITE_SYMBOL_CACHE[cache_key] = ''
        return None




def _svg_icon_hq(svg_data: str, size: int) -> QPixmap:
    """
    Render SVG crisp on any display density.

    Technique:
      1. Detect the screen device-pixel-ratio (DPR) — e.g. 1.5× on a 150% Windows display.
      2. Render at  (size × DPR × oversample)  pixels — large enough for perfect edges.
      3. Scale down to  (size × DPR)  with smooth filtering.
      4. Tag the pixmap with setDevicePixelRatio(dpr) so Qt maps
         that physical pixel count back to the correct logical size.
    Result: the icon appears at exactly `size` logical pixels but uses
    all available physical pixels — identical sharpness to a browser icon.
    """
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    dpr = app.devicePixelRatio() if app else 1.0

    oversample  = 4                           # render 4× bigger than physical px
    phys_render = int(size * dpr * oversample)
    phys_target = int(size * dpr)

    # ── Render at high resolution ──────────────────────────────────────────
    big = QPixmap(phys_render, phys_render)
    big.fill(Qt.GlobalColor.transparent)

    renderer = QSvgRenderer(svg_data.encode('utf-8'))
    if not renderer.isValid():
        fallback = QPixmap(phys_target, phys_target)
        fallback.fill(Qt.GlobalColor.transparent)
        fallback.setDevicePixelRatio(dpr)
        return fallback

    painter = QPainter(big)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()

    # ── Downsample to physical target size ────────────────────────────────
    out = big.scaled(phys_target, phys_target,
                     Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    out.setDevicePixelRatio(dpr)   # tell Qt: this many physical px = `size` logical px
    return out


def make_sprite_icon(symbol_id: str, size: int = 20) -> QIcon:
    """
    Return a high-quality QIcon rendered from the OpenCode file-icons/sprite.svg.
    Falls back to make_icon() (existing SVGs) if the sprite is not found.

    symbol_id examples: 'Python', 'Typescript', 'FolderSrc', 'FolderSrcOpen'
    """
    cache_key = (symbol_id, size)
    if cache_key in _SPRITE_ICON_CACHE:
        return _SPRITE_ICON_CACHE[cache_key]

    global _SPRITE_PATH
    if _SPRITE_PATH is None:
        _SPRITE_PATH = _find_sprite_path() or ''

    icon = None
    if _SPRITE_PATH:
        svg_data = _extract_symbol(_SPRITE_PATH, symbol_id)
        if svg_data:
            try:
                px = _svg_icon_hq(svg_data, size)
                if not px.isNull():
                    icon = QIcon(px)
            except Exception:
                icon = None

    if icon is None:
        # ── Graceful fallback to existing inline SVG icons ──────────────────
        _SPRITE_TO_LEGACY = {
            'Python': 'python', 'Javascript': 'javascript', 'Typescript': 'typescript',
            'React': 'react', 'React_ts': 'react', 'Html': 'html',
            'Css': 'css', 'Sass': 'scss', 'Less': 'scss',
            'Vue': 'vue', 'Svelte': 'svelte',
            'Java': 'java', 'Kotlin': 'kotlin', 'Csharp': 'csharp',
            'Cpp': 'cpp', 'C': 'c', 'Rust': 'rust', 'Go': 'go',
            'Ruby': 'ruby', 'Php': 'php', 'Dart': 'dart', 'Swift': 'swift',
            'Lua': 'lua', 'R': 'r', 'Julia': 'julia', 'Zig': 'zig',
            'Elixir': 'elixir', 'Haskell': 'haskell', 'Clojure': 'clojure',
            'Console': 'shell', 'Powershell': 'shell',
            'Json': 'json', 'Yaml': 'yaml', 'Markdown': 'markdown',
            'Database': 'sql', 'Docker': 'docker', 'Git': 'git',
            'Document': 'default', 'Log': 'files', 'Lock': 'config',
            'Zip': 'zip', 'Image': 'files', 'Nodejs': 'javascript',
            'Tune': 'env', 'Toml': 'config', 'Settings': 'config',
            'Xml': 'config', 'Graphql': 'default', 'Svg': 'image',
        }
        if symbol_id.startswith('Folder'):
            # Default to blue folder instead of generic if possible
            legacy = 'folder_blue' if symbol_id == 'FolderBlue' else 'folder'
        else:
            legacy = _SPRITE_TO_LEGACY.get(symbol_id, 'default')
        icon = make_icon(legacy, '#c0c0c0', size)

    _SPRITE_ICON_CACHE[cache_key] = icon
    return icon



def make_icon(name: str, color: str = "#c0c0c0", size: int = 32) -> QIcon:
    """Return a QIcon. Prioritizes UI templates, SVG icons, PNG assets, then QPainter with caching."""
    # 0. Check cache first
    cache_key = (name, color, size)
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]

    # 0.5. Try UI SVG templates (color-aware, high quality)
    template = _UI_SVG_TEMPLATES.get(name)
    if template:
        svg_data = template.replace("{color}", color)
        px = _svg_icon_hq(svg_data, size)
        if not px.isNull():
            icon = QIcon(px)
            _ICON_CACHE[cache_key] = icon
            return icon

    # 1. Try SVG icons first (VS Code style)
    svg_data = _SVG_ICONS.get(name)
    if svg_data:
        px = _svg_icon(svg_data, size)
        icon = QIcon(px)
        _ICON_CACHE[cache_key] = icon
        return icon

    # 2. Try to load from official assets
    asset_path = Path(__file__).parent.parent / "assets" / "icons" / f"{name}.png"
    if asset_path.exists():
        icon = QIcon(str(asset_path))
        _ICON_CACHE[cache_key] = icon
        return icon

    # 3. Fallback to programmatic drawing
    px, p = _make_pixmap(size)
    drawer = _DRAWERS.get(name)
    if drawer:
        drawer(p, size, color)
    p.end()
    
    icon = QIcon(px)
    _ICON_CACHE[cache_key] = icon
    return icon


def make_button_icon(name: str, is_dark: bool = True, size: int = 28) -> QIcon:
    """Icon with theme-appropriate default color."""
    color = "#c8c8c8" if is_dark else "#444444"
    return make_icon(name, color, size)
