"""Manual GUI smoke check. Run with QT_QPA_PLATFORM=offscreen on a headless box."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from pixelpack.ui.main_window import MainWindow  # noqa: E402
from pixelpack.ui.result_dialog import ResultDialog  # noqa: E402
from pixelpack.ui.style import build_stylesheet  # noqa: E402
from pixelpack.workers import OptimizationTask, OptimizationWorker  # noqa: E402

app = QApplication([])
qss, theme = build_stylesheet(app)
app.setStyleSheet(qss)
print(f"theme={theme.name} qss={len(qss)} chars")

window = MainWindow(restore=False)
window.show()
print(f"title={window.windowTitle()!r}")
print(f"target_bytes={window.target_bytes()}")
print(f"output(before source)={window.output_path()}")

folder = Path(tempfile.gettempdir()) / "pixelpack gui demo"
folder.mkdir(exist_ok=True)
window.set_source_dir(folder)
print(f"output(after source)={window.output_path()}")
print(f"source_dir={window.source_dir()}")

window.unit_combo.setCurrentText("GB")
window.size_spin.setValue(1.5)
print(f"1.5 GB -> {window.target_bytes()} bytes")
window.unit_combo.setCurrentText("MB")
window.size_spin.setValue(20.0)
print(f"20 MB -> {window.target_bytes()} bytes")
print(f"hint={window.target_hint.text()!r}")

window.advanced_toggle.setChecked(True)
print(f"advanced visible={window.advanced_panel.isVisible()}")

settings = window.build_settings()
print(f"settings={settings}")

from pixelpack.models.settings import CompressionMode  # noqa: E402

print("modes:", [(m.label, m.jpeg_quality, m.value) for m in CompressionMode])
print("worker/task importable:", OptimizationWorker.__name__, OptimizationTask.__name__)
print("ResultDialog importable:", ResultDialog.__name__)
print("OK")
