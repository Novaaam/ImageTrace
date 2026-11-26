"""Application entry point."""

from __future__ import annotations

import contextlib
import ctypes
import logging
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from imagetrace import APP_NAME, __version__
from imagetrace.logging_setup import setup_logging
from imagetrace.paths import (
    check_storage,
    default_data_root,
    resources_dir,
    save_configured_data_root,
)
from imagetrace.ui import dialogs
from imagetrace.ui.main_window import MainWindow

log = logging.getLogger("imagetrace")


def _set_windows_app_user_model_id() -> None:
    """Tell Windows taskbar to use ImageTrace's own icon instead of python.exe/pythonw.exe."""
    if sys.platform == "win32":
        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("net.9va.imagetrace")


def _load_app_icon() -> QIcon:
    for name in ("imagetrace.ico", "imagetrace.png"):
        icon_file = resources_dir() / name
        if icon_file.is_file():
            icon = QIcon(str(icon_file))
            if not icon.isNull():
                return icon
    return QIcon()


def create_app(argv: list[str] | None = None) -> QApplication:
    _set_windows_app_user_model_id()
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    icon = _load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    return app


def resolve_data_root(initial: Path) -> Path | None:
    """Return a usable data root, the original (limited mode), or None to quit.

    Never falls back to another location silently: the user chooses.
    """
    root = initial
    while True:
        status = check_storage(root)
        if status.available:
            return root
        choice = dialogs.ask_storage_choice(None, status.message)
        if choice == "limited":
            return root
        if choice != "choose":
            return None
        picked = dialogs.choose_directory(None, "Choose the ImageTrace data folder", root.parent)
        if picked is None:
            continue
        if picked.drive.upper() == "C:" and not dialogs.confirm(
            None,
            "Use the C: drive?",
            f"{picked} is on the C: drive. ImageTrace is set up to keep its data on D:. "
            "Use this folder anyway?",
        ):
            continue
        root = picked
        if check_storage(root).available and dialogs.confirm(
            None, "Remember this folder?", f"Use {root} as the data folder from now on?"
        ):
            save_configured_data_root(root)


def _install_excepthook() -> None:
    def hook(exc_type, exc, tb) -> None:
        log.critical("Unhandled error: %s", exc_type.__name__, exc_info=(exc_type, exc, tb))
        if QApplication.instance() is not None:
            msg = str(exc)
            if len(msg) > 400:
                msg = msg[:397] + "..."
            dialogs.error(None, "Unexpected error", f"{exc_type.__name__}: {msg}")

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    app = create_app(argv)
    data_root = resolve_data_root(default_data_root())
    if data_root is None:
        return 1
    setup_logging(data_root if check_storage(data_root).available else None)
    _install_excepthook()
    log.info("%s %s starting", APP_NAME, __version__)
    window = MainWindow(data_root=data_root)
    window.show()
    code = app.exec()
    log.info("%s exiting (code %s)", APP_NAME, code)
    return code
