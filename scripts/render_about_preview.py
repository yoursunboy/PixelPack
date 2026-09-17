"""Render the About dialog offscreen and report what actually landed on screen.

Not part of the test suite: a one-off check that the layout has no clipped or
collapsed widgets. Run with QT_QPA_PLATFORM=offscreen.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# This one is read by a human on a UTF-8 terminal, unlike the build scripts.
sys.stdout.reconfigure(encoding="utf-8")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from pixelpack.ui.about_dialog import AboutDialog  # noqa: E402
from pixelpack.ui.style import build_stylesheet  # noqa: E402

app = QApplication.instance() or QApplication([])
sheet, theme = build_stylesheet(app)
app.setStyleSheet(sheet)

about = AboutDialog()
about.adjustSize()
about.show()
app.processEvents()

print(f"theme            {theme.name}")
print(f"dialog           {about.width()} x {about.height()}")
print(f"sizeHint         {about.sizeHint().width()} x {about.sizeHint().height()}")
print()

print("widgets:")
for widget in about.findChildren(QLabel) + about.findChildren(QPushButton):
    geometry = widget.geometry()
    kind = type(widget).__name__
    name = widget.objectName() or "-"
    text = ""
    if isinstance(widget, (QLabel, QPushButton)):
        text = widget.text().replace("\n", " ")
        if len(text) > 34:
            text = text[:31] + "..."
    # Geometry is in the parent's coordinates, so walk up to the dialog.
    top_left = widget.mapTo(about, geometry.topLeft() - geometry.topLeft())
    clipped = ""
    if top_left.x() + geometry.width() > about.width() or top_left.y() + geometry.height() > about.height():
        clipped = "  <-- OUTSIDE THE DIALOG"
    print(
        f"  {kind:<12} {name:<12} "
        f"{top_left.x():>4},{top_left.y():>4} {geometry.width():>4}x{geometry.height():<4} {text}{clipped}"
    )

collapsed = [
    w.objectName() or type(w).__name__
    for w in about.findChildren(QLabel)
    if w.text() and (w.width() <= 1 or w.height() <= 1)
]
print()
print(f"collapsed labels: {collapsed or 'none'}")

logo = about.findChild(QLabel, "AboutLogo")
print(f"logo loaded:      {about.logo_loaded}")
print(f"logo pixmap:      {logo.pixmap().width()} x {logo.pixmap().height()}"
      f"  dpr={logo.pixmap().devicePixelRatio()}")

image = about.grab().toImage()

# The logo's artwork has a near-white background, so a region that is uniformly
# one colour would mean the pixmap never painted. Count what is actually there.
logo_rect = logo.mapTo(about, logo.rect().topLeft())
logo_colours = {}
inner = 0
for y in range(logo_rect.y(), logo_rect.y() + logo.height()):
    for x in range(logo_rect.x(), logo_rect.x() + logo.width()):
        logo_colours[image.pixel(x, y)] = logo_colours.get(image.pixel(x, y), 0) + 1
        inner += 1
print(f"logo region:      {len(logo_colours)} distinct colours over {inner} px")
print(f"logo top 3:       {sorted(logo_colours.items(), key=lambda kv: -kv[1])[:3]}")

out = ROOT / "build" / "about-preview.png"
out.parent.mkdir(parents=True, exist_ok=True)
image.save(str(out))
print(f"saved:            {out}  ({image.width()}x{image.height()})")

# Did anything actually paint? A blank render would be one uniform colour.
colours = {}
for y in range(0, image.height(), 4):
    for x in range(0, image.width(), 4):
        colours[image.pixel(x, y)] = colours.get(image.pixel(x, y), 0) + 1
print(f"distinct colours: {len(colours)} (sampled every 4px)")
print(f"most common:      {sorted(colours.items(), key=lambda kv: -kv[1])[:3]}")

about.deleteLater()
