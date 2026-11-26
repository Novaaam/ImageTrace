"""Light/dark themes: restrained forensic-workstation style with readable contrast."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from imagetrace.paths import resources_dir

PALETTES = {
    "light": {
        "bg": "#f4f5f7",
        "panel": "#ffffff",
        "sidebar": "#e8eaee",
        "text": "#1b1f24",
        "muted": "#57606a",
        "border": "#d0d7de",
        "accent": "#0b5cad",
        "accent_text": "#ffffff",
        "warn": "#8a5a00",
        "warn_bg": "#fff4d6",
        "ok": "#1a7f37",
        "err": "#b42318",
        "input": "#ffffff",
        "hover": "#dde1e7",
    },
    "dark": {
        "bg": "#16191d",
        "panel": "#1f2328",
        "sidebar": "#111316",
        "text": "#e6e8eb",
        "muted": "#9aa4af",
        "border": "#363c44",
        "accent": "#4c9be8",
        "accent_text": "#0b0d10",
        "warn": "#f0c060",
        "warn_bg": "#3a3020",
        "ok": "#56d364",
        "err": "#ff7b72",
        "input": "#2a2f36",
        "hover": "#262b31",
    },
}


def resolve_theme(name: str) -> str:
    if name in ("light", "dark"):
        return name
    scheme = QGuiApplication.styleHints().colorScheme()
    return "dark" if scheme == Qt.ColorScheme.Dark else "light"


def _check_icon(theme: str) -> str:
    # Qt stylesheet url() wants forward slashes.
    return (resources_dir() / f"check-{theme}.svg").as_posix()


def stylesheet(theme: str) -> str:
    p = PALETTES[theme]
    return f"""
    QWidget {{ background: {p["bg"]}; color: {p["text"]}; font-size: 10pt;
               font-family: "Segoe UI", "Arial", sans-serif; }}
    QToolTip {{ background: {p["panel"]}; color: {p["text"]}; border: 1px solid {p["border"]}; }}
    #TopBar {{ background: {p["panel"]}; border-bottom: 1px solid {p["border"]}; }}
    #TopBar QLabel {{ background: transparent; }}
    #Sidebar {{ background: {p["sidebar"]}; border: none; border-right: 1px solid {p["border"]};
               padding-top: 8px; outline: 0; }}
    #Sidebar::item {{ padding: 9px 16px; margin: 1px 6px; border-radius: 4px; }}
    #Sidebar::item:hover {{ background: {p["hover"]}; }}
    #Sidebar::item:selected {{ background: {p["accent"]}; color: {p["accent_text"]}; }}
    #Sidebar::item:focus {{ border: 1px dashed {p["accent"]}; }}
    #AppTitle {{ font-size: 13pt; font-weight: 600; background: transparent; }}
    #PageTitle {{ font-size: 16pt; font-weight: 600; }}
    #Muted, QLabel[muted="true"] {{ color: {p["muted"]}; }}
    #Card {{ background: {p["panel"]}; border: 1px solid {p["border"]}; border-radius: 6px; }}
    #Card QLabel {{ background: transparent; }}
    #FormHost {{ background: transparent; }}
    #CardTitle {{ font-weight: 600; font-size: 11pt; }}
    #Banner {{ background: {p["warn_bg"]}; color: {p["warn"]}; border: 1px solid {p["warn"]};
              border-radius: 4px; padding: 6px 10px; }}
    QLabel[state="ok"] {{ color: {p["ok"]}; }}
    QLabel[state="warn"] {{ color: {p["warn"]}; }}
    QLabel[state="error"] {{ color: {p["err"]}; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {p["input"]};
        border: 1px solid {p["border"]}; border-radius: 4px; padding: 4px 8px; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {p["accent"]}; }}
    QLineEdit:disabled {{ color: {p["muted"]}; }}
    QComboBox QAbstractItemView {{ background: {p["panel"]}; color: {p["text"]};
        selection-background-color: {p["accent"]}; selection-color: {p["accent_text"]}; }}
    QPushButton {{ background: {p["panel"]}; border: 1px solid {p["border"]}; border-radius: 4px;
                   padding: 5px 12px; }}
    QPushButton:hover {{ background: {p["hover"]}; }}
    QPushButton:focus {{ border: 1px solid {p["accent"]}; }}
    QStatusBar {{ background: {p["panel"]}; border-top: 1px solid {p["border"]}; }}
    QStatusBar QLabel {{ background: transparent; }}
    QScrollArea {{ border: none; }}
    QTableWidget, QListWidget#History, QPlainTextEdit {{ background: {p["input"]};
        border: 1px solid {p["border"]}; border-radius: 4px; gridline-color: {p["border"]};
        selection-background-color: {p["accent"]}; selection-color: {p["accent_text"]}; }}
    QTableWidget:focus, QPlainTextEdit:focus {{ border: 1px solid {p["accent"]}; }}
    QHeaderView::section {{ background: {p["panel"]}; color: {p["muted"]}; border: none;
        border-bottom: 1px solid {p["border"]}; padding: 5px 6px; font-weight: 600; }}
    QTableCornerButton::section {{ background: {p["panel"]}; border: none; }}
    QPushButton:disabled {{ color: {p["muted"]}; background: {p["bg"]}; }}
    QPushButton#Primary {{ background: {p["accent"]}; color: {p["accent_text"]};
        border: 1px solid {p["accent"]}; font-weight: 600; }}
    QPushButton#Primary:hover {{ background: {p["accent"]}; border: 1px solid {p["text"]}; }}
    QCheckBox {{ background: transparent; spacing: 6px; }}
    QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {p["muted"]};
        border-radius: 3px; background: {p["input"]}; }}
    QCheckBox::indicator:hover {{ border: 1px solid {p["accent"]}; }}
    QCheckBox::indicator:checked {{ background: {p["accent"]}; border: 1px solid {p["accent"]};
        image: url({_check_icon(theme)}); }}
    QCheckBox:focus {{ outline: none; }}
    QCheckBox::indicator:focus {{ border: 2px solid {p["accent"]}; }}
    QSplitter::handle {{ background: {p["border"]}; }}
    QMessageBox QLabel {{ background: transparent; }}
    QTabWidget::pane {{ border: 1px solid {p["border"]}; background: {p["panel"]};
        border-radius: 4px; top: -1px; }}
    QTabBar::tab {{ background: {p["sidebar"]}; color: {p["muted"]};
        border: 1px solid {p["border"]}; border-bottom: none;
        padding: 6px 10px; margin-right: 2px; border-top-left-radius: 4px;
        border-top-right-radius: 4px; }}
    QTabBar::tab:selected {{ background: {p["panel"]}; color: {p["text"]}; font-weight: 600; }}
    QTabBar::tab:hover {{ color: {p["text"]}; }}
    QProgressBar {{ background: {p["input"]}; border: 1px solid {p["border"]};
        border-radius: 4px; text-align: center; height: 18px; }}
    QProgressBar::chunk {{ background: {p["accent"]}; border-radius: 3px; }}
    #ThumbBox {{ background: {p["bg"]}; border: 1px solid {p["border"]}; border-radius: 4px; }}
    #Mono {{ font-family: "Consolas", "Courier New", monospace; font-size: 9pt; }}
    #TabBody {{ background: {p["panel"]}; }}
    #TabBody QLabel {{ background: transparent; }}
    #TabBody QWidget#FormHost {{ background: transparent; }}
    #SettingsBody {{ background: transparent; }}
    """


def apply_theme(app: QApplication, name: str) -> str:
    """Apply the theme by name ('system' | 'light' | 'dark'). Returns the resolved theme."""
    resolved = resolve_theme(name)
    sheet = stylesheet(resolved)
    if app.styleSheet() != sheet:  # re-applying an identical sheet re-polishes every widget
        app.setStyleSheet(sheet)
    return resolved
