"""Map icon glyphs, shared by the map page (served as /glyphs.js) and the Settings symbol preview."""
from __future__ import annotations

# 24x24 paths drawn white on a coloured badge. Strokes are explicit attributes rather than CSS
# classes so Qt's SVG renderer and the browser agree.
GLYPHS = {
    'house': '<path d="M12 4 2 13h3v8h5v-5h4v5h5v-8h3z"/>',
    'car': '<path d="M4 13l2-5c.3-.7.9-1 1.6-1h8.8c.7 0 1.3.3 1.6 1l2 5v5h-3v-2H7v2H4z"/><circle cx="7.5" cy="14" r="1.2" fill="#000" opacity=".35"/><circle cx="16.5" cy="14" r="1.2" fill="#000" opacity=".35"/>',
    'truck': '<path d="M2 6h12v10H2zM14 9h4l4 4v3h-8z"/><circle cx="7" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>',
    'bus': '<path d="M4 4h16v13H4z"/><rect x="6" y="6" width="12" height="5" fill="#000" opacity=".3"/><circle cx="8" cy="19" r="1.8"/><circle cx="16" cy="19" r="1.8"/>',
    'plane': '<path d="M12 2c.8 0 1.3.8 1.3 2v6l8.2 4.6v2.2l-8.2-2.4v4.2l2.2 1.6V22L12 21l-3.5 1v-1.8l2.2-1.6v-4.2l-8.2 2.4v-2.2l8.2-4.6V4c0-1.2.5-2 1.3-2z"/>',
    'boat': '<path d="M2 15h20l-4 5H6zM11 3v10H5zM13 6l6 7h-6z"/>',
    'bike': '<g fill="none" stroke-width="2"><circle cx="6" cy="16" r="3.5"/><circle cx="18" cy="16" r="3.5"/><path d="M6 16l4-7h5l3 7M10 9l3 7"/></g>',
    'person': '<circle cx="13" cy="5" r="2.3"/><path fill="none" stroke-width="2" d="M12 9l-2 5 3 2v5M12 9l3 3 3-1M10 14l-3 3"/>',
    'tower': '<path fill="none" stroke-width="2" d="M12 10v11M8 21h8M7 7a7 7 0 0 1 10 0M9.5 9.5a3.5 3.5 0 0 1 5 0"/><circle cx="12" cy="10" r="1.5"/>',
    'igate': '<path fill="none" stroke-width="2" d="M12 12v9M8 21h8M5.5 5.5a9 9 0 0 1 13 0M8.3 8.3a5 5 0 0 1 7.4 0"/><circle cx="12" cy="12" r="1.8"/>',
    'weather': '<path d="M7 19a4.5 4.5 0 0 1-.5-9A6 6 0 0 1 18 8.5 5 5 0 0 1 17.5 19z"/>',
}

COLORS = {"other": "#d33", "wx": "#1e6fe8", "igate": "#1f9d3a", "digi": "#e08a00", "me": "#8a3ffc"}

SVG_ATTRS = 'viewBox="0 0 24 24" fill="#fff" stroke="#fff" stroke-width="0" stroke-linecap="round" stroke-linejoin="round"'


def svg(icon: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" {SVG_ATTRS}>{GLYPHS.get(icon, GLYPHS["house"])}</svg>'


def badge(icon: str, cls: str, size: int = 44):
    """The map marker as a QPixmap, for previews in the UI."""
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
    from PySide6.QtSvg import QSvgRenderer

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("white"), size / 14))
    p.setBrush(QColor(COLORS.get(cls, COLORS["other"])))
    pad = size / 14
    p.drawEllipse(QRectF(pad, pad, size - 2 * pad, size - 2 * pad))
    inner = size * 0.24
    QSvgRenderer(QByteArray(svg(icon).encode())).render(p, QRectF(inner, inner, size - 2 * inner, size - 2 * inner))
    p.end()
    return pm
