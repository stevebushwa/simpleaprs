"""Render simpleaprs.svg to the icon files Windows (.ico) and macOS (.icns) want.

    python packaging/make_icons.py OUTDIR

Needs PySide6 (which the app needs anyway) and Pillow.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

here = Path(__file__).parent
out = Path(sys.argv[1] if len(sys.argv) > 1 else "build/icons")
out.mkdir(parents=True, exist_ok=True)
app = QGuiApplication([])
image = QImage(1024, 1024, QImage.Format.Format_ARGB32)
image.fill(Qt.GlobalColor.transparent)
painter = QPainter(image)
QSvgRenderer(str(here / "simpleaprs.svg")).render(painter)
painter.end()
image.save(str(out / "simpleaprs.png"))
big = Image.open(out / "simpleaprs.png").convert("RGBA")
big.save(out / "simpleaprs.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
big.save(out / "simpleaprs.icns")
print("wrote", ", ".join(sorted(p.name for p in out.iterdir())))
