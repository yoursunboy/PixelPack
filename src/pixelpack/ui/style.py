"""A quiet, Windows 11-ish stylesheet.

One stylesheet string per theme, built from a small token table so the light
and dark variants stay in step. Nothing here is load-bearing: if Qt rejects a
property the widget simply falls back to the platform default.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette


@dataclass(frozen=True, slots=True)
class Theme:
    name: str
    dark: bool
    window: str
    surface: str
    surface_alt: str
    border: str
    text: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_disabled: str
    on_accent: str
    hover: str
    pressed: str
    danger: str
    success: str
    warning: str
    track: str


LIGHT = Theme(
    name="light",
    dark=False,
    window="#f3f3f3",
    surface="#fbfbfb",
    surface_alt="#ffffff",
    border="#dcdcdc",
    text="#1b1b1b",
    text_muted="#6b6b6b",
    accent="#0f6cbd",
    accent_hover="#1a7fd4",
    accent_pressed="#0c5896",
    accent_disabled="#b9d5ec",
    on_accent="#ffffff",
    hover="#eaeaea",
    pressed="#e0e0e0",
    danger="#c42b1c",
    success="#0f7b0f",
    warning="#9d5d00",
    track="#e2e2e2",
)

DARK = Theme(
    name="dark",
    dark=True,
    window="#202020",
    surface="#2b2b2b",
    surface_alt="#323232",
    border="#3d3d3d",
    text="#f2f2f2",
    text_muted="#a0a0a0",
    accent="#4cc2ff",
    accent_hover="#5fcaff",
    accent_pressed="#3aa8e0",
    accent_disabled="#2f5a72",
    on_accent="#10222c",
    hover="#383838",
    pressed="#414141",
    danger="#ff99a4",
    success="#6ccb5f",
    warning="#fce100",
    track="#3a3a3a",
)

THEMES = {LIGHT.name: LIGHT, DARK.name: DARK}


def theme_for(dark: bool) -> Theme:
    return DARK if dark else LIGHT


def stylesheet(theme: Theme) -> str:
    """The full QSS for *theme*."""
    t = theme
    return f"""
* {{
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 13px;
}}

QWidget {{
    color: {t.text};
    background-color: {t.window};
}}

QMainWindow, QDialog {{
    background-color: {t.window};
}}

QToolTip {{
    background-color: {t.surface_alt};
    color: {t.text};
    border: 1px solid {t.border};
    padding: 4px 6px;
}}

/* ---------------------------------------------------------------- cards */
QGroupBox {{
    background-color: {t.surface};
    border: 1px solid {t.border};
    border-radius: 8px;
    margin-top: 14px;
    padding: 14px 14px 10px 14px;
    font-weight: 600;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: {t.text_muted};
    font-weight: 600;
}}

QFrame#Card {{
    background-color: {t.surface};
    border: 1px solid {t.border};
    border-radius: 8px;
}}

QFrame#Divider {{
    background-color: {t.border};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}

/* -------------------------------------------------------------- heading */
QLabel#Title {{
    font-size: 21px;
    font-weight: 700;
}}

QLabel#Subtitle {{
    color: {t.text_muted};
    font-size: 12px;
}}

QLabel#SectionHint, QLabel#Muted {{
    color: {t.text_muted};
    font-size: 12px;
}}

QLabel#StageLabel {{
    font-size: 14px;
    font-weight: 600;
}}

QLabel#MetricKey {{
    color: {t.text_muted};
    font-size: 12px;
}}

QLabel#MetricValue {{
    font-size: 15px;
    font-weight: 600;
}}

QLabel#MetricValueAccent {{
    font-size: 15px;
    font-weight: 700;
    color: {t.accent};
}}

QLabel#ErrorLabel {{
    color: {t.danger};
    font-weight: 600;
}}

QLabel#SuccessLabel {{
    color: {t.success};
    font-weight: 600;
}}

/* --------------------------------------------------------------- inputs */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {t.surface_alt};
    border: 1px solid {t.border};
    border-radius: 5px;
    padding: 6px 8px;
    selection-background-color: {t.accent};
    selection-color: {t.on_accent};
    min-height: 20px;
}}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {t.accent};
}}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {t.text_muted};
    background-color: {t.window};
}}

QLineEdit[readOnly="true"] {{
    color: {t.text_muted};
}}

QLineEdit#DropZone {{
    border: 1px dashed {t.border};
    border-radius: 8px;
    padding: 12px;
    background-color: {t.surface};
}}

QLineEdit#DropZone[dragActive="true"] {{
    border: 1px dashed {t.accent};
    background-color: {t.surface_alt};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {t.surface_alt};
    border: 1px solid {t.border};
    selection-background-color: {t.accent};
    selection-color: {t.on_accent};
    outline: none;
}}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 18px;
    border: none;
    background: transparent;
}}

QCheckBox, QRadioButton {{
    spacing: 8px;
    background: transparent;
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {t.border};
    background-color: {t.surface_alt};
}}

QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {t.accent};
    border: 1px solid {t.accent};
}}

QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background-color: {t.window};
}}

/* -------------------------------------------------------------- buttons */
QPushButton {{
    background-color: {t.surface_alt};
    border: 1px solid {t.border};
    border-radius: 5px;
    padding: 7px 16px;
    min-height: 20px;
}}

QPushButton:hover {{ background-color: {t.hover}; }}
QPushButton:pressed {{ background-color: {t.pressed}; }}

QPushButton:disabled {{
    color: {t.text_muted};
    background-color: {t.window};
    border-color: {t.border};
}}

QPushButton#Primary {{
    background-color: {t.accent};
    color: {t.on_accent};
    border: 1px solid {t.accent};
    font-weight: 600;
    padding: 8px 22px;
}}

QPushButton#Primary:hover {{ background-color: {t.accent_hover}; border-color: {t.accent_hover}; }}
QPushButton#Primary:pressed {{ background-color: {t.accent_pressed}; border-color: {t.accent_pressed}; }}

QPushButton#Primary:disabled {{
    background-color: {t.accent_disabled};
    border-color: {t.accent_disabled};
    color: {t.surface};
}}

QPushButton#Danger {{
    color: {t.danger};
    border: 1px solid {t.border};
}}

QPushButton#Danger:hover {{ background-color: {t.hover}; }}

QPushButton#Link {{
    background: transparent;
    border: none;
    color: {t.accent};
    padding: 2px 4px;
    text-align: left;
}}

QPushButton#Link:hover {{ text-decoration: underline; background: transparent; }}

/* ------------------------------------------------------------- progress */
QProgressBar {{
    background-color: {t.track};
    border: none;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    color: transparent;
}}

QProgressBar::chunk {{
    background-color: {t.accent};
    border-radius: 5px;
}}

QProgressBar#Indeterminate::chunk {{
    background-color: {t.accent};
}}

/* ---------------------------------------------------------------- table */
QTableView, QTableWidget {{
    background-color: {t.surface_alt};
    alternate-background-color: {t.surface};
    border: 1px solid {t.border};
    border-radius: 6px;
    gridline-color: {t.border};
    selection-background-color: {t.accent};
    selection-color: {t.on_accent};
    outline: none;
}}

QTableView::item, QTableWidget::item {{
    padding: 4px 6px;
    border: none;
}}

QHeaderView::section {{
    background-color: {t.surface};
    color: {t.text_muted};
    border: none;
    border-bottom: 1px solid {t.border};
    border-right: 1px solid {t.border};
    padding: 6px 8px;
    font-weight: 600;
}}

QHeaderView::section:last {{ border-right: none; }}
QTableCornerButton::section {{ background-color: {t.surface}; border: none; }}

/* -------------------------------------------------------------- scrolls */
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {t.border};
    border-radius: 6px;
    min-height: 28px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{ background: {t.text_muted}; }}

QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background: {t.border};
    border-radius: 6px;
    min-width: 28px;
    margin: 2px;
}}

QScrollBar::handle:horizontal:hover {{ background: {t.text_muted}; }}

QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ------------------------------------------------------------ structure */
QTabWidget::pane {{
    border: 1px solid {t.border};
    border-radius: 6px;
    background-color: {t.surface};
    top: -1px;
}}

QTabBar::tab {{
    background: transparent;
    color: {t.text_muted};
    padding: 7px 14px;
    border: none;
    border-bottom: 2px solid transparent;
}}

QTabBar::tab:selected {{
    color: {t.text};
    border-bottom: 2px solid {t.accent};
    font-weight: 600;
}}

QTabBar::tab:hover:!selected {{ color: {t.text}; }}

QStatusBar {{
    background-color: {t.surface};
    border-top: 1px solid {t.border};
    color: {t.text_muted};
}}

QStatusBar::item {{ border: none; }}

QSplitter::handle {{ background-color: {t.border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
"""


def apply_palette(app, theme: Theme) -> None:
    """Give Qt's own drawing (menus, tooltips, native bits) the same colours."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(theme.window))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(theme.text))
    palette.setColor(QPalette.ColorRole.Base, QColor(theme.surface_alt))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(theme.surface))
    palette.setColor(QPalette.ColorRole.Text, QColor(theme.text))
    palette.setColor(QPalette.ColorRole.Button, QColor(theme.surface_alt))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(theme.text))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(theme.accent))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(theme.on_accent))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(theme.surface_alt))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(theme.text))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(theme.text_muted))
    app.setPalette(palette)


def detect_dark(app) -> bool:
    """Follow the OS light/dark preference when Qt can tell us."""
    try:
        hints = app.styleHints()
        scheme = hints.colorScheme()
        from PySide6.QtCore import Qt

        return scheme == Qt.ColorScheme.Dark
    except Exception:  # pragma: no cover - older Qt or platform without hints
        return False


def build_stylesheet(app, *, dark: bool | None = None) -> tuple[str, Theme]:
    """Pick a theme, apply the palette and return ``(qss, theme)``."""
    if dark is None:
        dark = detect_dark(app)
    theme = theme_for(dark)
    apply_palette(app, theme)
    return stylesheet(theme), theme
