"""Session lock overlay and passphrase dialogs (Phase 9 hardening).

Passphrases are masked in the UI and never logged or stored in plaintext.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from imagetrace.security import (
    MAX_PASSPHRASE_LENGTH,
    MIN_PASSPHRASE_LENGTH,
    PassphraseError,
    validate_new_passphrase,
)
from imagetrace.ui.widgets import Card, label, set_state


class PassphraseDialog(QDialog):
    """Modal passphrase entry dialog (create-with-confirmation or single unlock/decrypt)."""

    def __init__(
        self,
        title: str,
        description: str,
        *,
        confirm_mode: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.confirm_mode = confirm_mode
        self.setWindowTitle(title)
        self.setMinimumWidth(460)

        lay = QVBoxLayout(self)
        lay.addWidget(label(description, muted=True))

        form = QFormLayout()
        self.pw_edit = QLineEdit()
        self.pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw_edit.setMaxLength(MAX_PASSPHRASE_LENGTH)
        self.pw_edit.setAccessibleName("Passphrase")
        pw_lbl = label("&Passphrase:", wrap=False)
        pw_lbl.setBuddy(self.pw_edit)
        form.addRow(pw_lbl, self.pw_edit)

        self.confirm_edit: QLineEdit | None = None
        if confirm_mode:
            self.confirm_edit = QLineEdit()
            self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.confirm_edit.setMaxLength(MAX_PASSPHRASE_LENGTH)
            self.confirm_edit.setAccessibleName("Confirm passphrase")
            c_lbl = label("&Confirm passphrase:", wrap=False)
            c_lbl.setBuddy(self.confirm_edit)
            form.addRow(c_lbl, self.confirm_edit)
            lay.addLayout(form)
            lay.addWidget(
                label(
                    f"Minimum {MIN_PASSPHRASE_LENGTH} characters. ImageTrace never stores "
                    "passphrases in plaintext and cannot recover a lost passphrase.",
                    muted=True,
                )
            )
        else:
            lay.addLayout(form)

        self.error_label = label("", state="error")
        self.error_label.setVisible(False)
        lay.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.pw_edit.setFocus()

    def passphrase(self) -> str:
        return self.pw_edit.text()

    def accept(self) -> None:
        pw = self.pw_edit.text()
        if self.confirm_mode:
            confirm = self.confirm_edit.text() if self.confirm_edit is not None else ""
            try:
                validate_new_passphrase(pw, confirm)
            except PassphraseError as exc:
                self.error_label.setText(str(exc))
                set_state(self.error_label, "error")
                self.error_label.setVisible(True)
                return
        elif not pw:
            self.error_label.setText("Please enter the passphrase.")
            set_state(self.error_label, "error")
            self.error_label.setVisible(True)
            return
        super().accept()


class LockOverlay(QWidget):
    """Privacy screen covering all case/evidence content while the session is locked."""

    unlock_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._has_passphrase = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 40, 40, 40)
        outer.addStretch(1)

        center_row = QHBoxLayout()
        center_row.addStretch(1)
        card = Card("Session locked")
        card.setMinimumWidth(440)
        card.setMaximumWidth(540)
        self.info_label = label(
            "Case titles, evidence images, OCR text and analyst notes are hidden while locked.",
            muted=True,
        )
        card.body.addWidget(self.info_label)

        self.pw_edit = QLineEdit()
        self.pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw_edit.setMaxLength(MAX_PASSPHRASE_LENGTH)
        self.pw_edit.setPlaceholderText("Enter unlock passphrase")
        self.pw_edit.setAccessibleName("Unlock passphrase")
        self.pw_edit.returnPressed.connect(self._submit)
        card.body.addWidget(self.pw_edit)

        self.error_label = label("", state="error")
        self.error_label.setVisible(False)
        card.body.addWidget(self.error_label)

        btn_row = QHBoxLayout()
        self.unlock_btn = QPushButton("Unlock session")
        self.unlock_btn.setObjectName("Primary")
        self.unlock_btn.clicked.connect(self._submit)
        btn_row.addWidget(self.unlock_btn)
        btn_row.addStretch(1)
        card.body.addLayout(btn_row)

        card.body.addWidget(
            label(
                "Note: session lock is an application-level privacy screen. Windows account "
                "security and full-disk encryption (e.g. BitLocker) remain essential.",
                muted=True,
            )
        )
        center_row.addWidget(card)
        center_row.addStretch(1)
        outer.addLayout(center_row)
        outer.addStretch(2)

    def configure(self, *, has_passphrase: bool) -> None:
        self._has_passphrase = has_passphrase
        self.pw_edit.clear()
        self.pw_edit.setVisible(has_passphrase)
        self.error_label.setText("")
        self.error_label.setVisible(False)
        if has_passphrase:
            self.pw_edit.setFocus()
        else:
            self.unlock_btn.setFocus()

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        set_state(self.error_label, "error")
        self.error_label.setVisible(True)
        self.pw_edit.clear()
        self.pw_edit.setFocus()

    def _submit(self) -> None:
        self.unlock_requested.emit(self.pw_edit.text() if self._has_passphrase else "")
