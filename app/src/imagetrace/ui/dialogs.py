"""Thin wrappers around modal dialogs.

Always call these through the module (``dialogs.confirm(...)``) so tests can replace them.
Message text is shown as plain text, never HTML.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget


def _box(parent: QWidget | None, icon: QMessageBox.Icon, title: str, text: str) -> QMessageBox:
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(text)
    return box


def confirm(parent: QWidget | None, title: str, text: str) -> bool:
    box = _box(parent, QMessageBox.Icon.Question, title, text)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


def info(parent: QWidget | None, title: str, text: str) -> None:
    _box(parent, QMessageBox.Icon.Information, title, text).exec()


def error(parent: QWidget | None, title: str, text: str) -> None:
    _box(parent, QMessageBox.Icon.Critical, title, text).exec()


def ask_storage_choice(parent: QWidget | None, message: str) -> str:
    """Return 'choose', 'limited' or 'quit'."""
    box = _box(
        parent,
        QMessageBox.Icon.Warning,
        "Data folder unavailable",
        f"{message}\n\nImageTrace never switches to another drive on its own. Choose a data "
        "folder, continue without storage (nothing is saved), or quit.",
    )
    choose = box.addButton("Choose folder…", QMessageBox.ButtonRole.AcceptRole)
    limited = box.addButton("Continue without storage", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton("Quit", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(choose)
    box.exec()
    clicked = box.clickedButton()
    if clicked is choose:
        return "choose"
    if clicked is limited:
        return "limited"
    return "quit"


def choose_directory(parent: QWidget | None, title: str, start: Path | None = None) -> Path | None:
    path = QFileDialog.getExistingDirectory(parent, title, str(start) if start else "")
    return Path(path) if path else None


def choose_open_file(
    parent: QWidget | None, title: str, start: Path | None, name_filter: str
) -> Path | None:
    path, _ = QFileDialog.getOpenFileName(parent, title, str(start) if start else "", name_filter)
    return Path(path) if path else None


def choose_open_files(
    parent: QWidget | None, title: str, start: Path | None, name_filter: str
) -> list[Path]:
    paths, _ = QFileDialog.getOpenFileNames(parent, title, str(start) if start else "", name_filter)
    return [Path(p) for p in paths]


def choose_save_file(
    parent: QWidget | None, title: str, start: Path | None, name_filter: str
) -> Path | None:
    path, _ = QFileDialog.getSaveFileName(parent, title, str(start) if start else "", name_filter)
    return Path(path) if path else None


def ask_new_passphrase(parent: QWidget | None, title: str, description: str) -> str | None:
    from imagetrace.ui.lock_ui import PassphraseDialog

    dlg = PassphraseDialog(title, description, confirm_mode=True, parent=parent)
    return dlg.passphrase() if dlg.exec() == dlg.DialogCode.Accepted else None


def ask_passphrase(parent: QWidget | None, title: str, description: str) -> str | None:
    from imagetrace.ui.lock_ui import PassphraseDialog

    dlg = PassphraseDialog(title, description, confirm_mode=False, parent=parent)
    return dlg.passphrase() if dlg.exec() == dlg.DialogCode.Accepted else None
