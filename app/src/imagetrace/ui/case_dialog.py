"""Create/edit case dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from imagetrace.cases import (
    MAX_TITLE,
    SCOPE_FIELD,
    Case,
    ValidationError,
    normalize_tags,
    validate_text,
    validate_title,
)
from imagetrace.ui.widgets import label, set_state

MINIMISATION_WARNING = (
    "Data minimisation: don't enter personal data (names, addresses, phone numbers, account "
    "IDs) unless the authorized case actually requires it. The case ID is generated and contains "
    "no personal data."
)


class CaseDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, case: Case | None = None) -> None:
        super().__init__(parent)
        self.case = case
        self.setWindowTitle(f"Edit case {case.id}" if case else "New case")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        banner = label(MINIMISATION_WARNING, obj="Banner")
        layout.addWidget(banner)

        form = QFormLayout()
        self.title_edit = QLineEdit(case.title if case else "")
        self.title_edit.setMaxLength(MAX_TITLE)
        self.title_edit.setPlaceholderText("Short label for the case")
        self.title_edit.setAccessibleName("Title")
        form.addRow(self._buddy("&Title *", self.title_edit), self.title_edit)

        self.tags_edit = QLineEdit(", ".join(case.tags) if case else "")
        self.tags_edit.setPlaceholderText("Comma-separated, optional")
        self.tags_edit.setAccessibleName("Tags")
        form.addRow(self._buddy("T&ags", self.tags_edit), self.tags_edit)

        self.scope_edit = QPlainTextEdit(case.scope_text if case else "")
        self.scope_edit.setPlaceholderText(
            "What you are authorized to examine, on whose request or under which authority, "
            "and for what purpose."
        )
        self.scope_edit.setAccessibleName(SCOPE_FIELD)
        self.scope_edit.setTabChangesFocus(True)
        self.scope_edit.setFixedHeight(90)
        form.addRow(self._buddy("&Scope / authorization *", self.scope_edit), self.scope_edit)

        self.authorized = QCheckBox(
            "I confirm the scope above and that I am authorized to examine this material."
        )
        self.authorized.setChecked(case is not None)
        form.addRow("", self.authorized)

        self.notes_edit = QPlainTextEdit(case.notes if case else "")
        self.notes_edit.setAccessibleName("Notes")
        self.notes_edit.setTabChangesFocus(True)
        self.notes_edit.setFixedHeight(90)
        form.addRow(self._buddy("&Notes", self.notes_edit), self.notes_edit)
        layout.addLayout(form)

        self.error_label = label("", state="error")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Save changes" if case else "Create case")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        for signal in (
            self.title_edit.textChanged,
            self.scope_edit.textChanged,
            self.authorized.toggled,
        ):
            signal.connect(self._update_ok)
        self._update_ok()
        self.title_edit.setFocus()

    @staticmethod
    def _buddy(text: str, widget: QWidget):
        lbl = label(text, wrap=False)
        lbl.setBuddy(widget)
        return lbl

    def _update_ok(self) -> None:
        ready = (
            bool(self.title_edit.text().strip())
            and bool(self.scope_edit.toPlainText().strip())
            and self.authorized.isChecked()
        )
        self.ok_button.setEnabled(ready)

    def values(self) -> dict[str, object]:
        return {
            "title": self.title_edit.text(),
            "tags": self.tags_edit.text(),
            "scope_text": self.scope_edit.toPlainText(),
            "notes": self.notes_edit.toPlainText(),
        }

    def validate(self) -> str | None:
        try:
            validate_title(self.title_edit.text())
            normalize_tags(self.tags_edit.text())
            validate_text(self.scope_edit.toPlainText(), SCOPE_FIELD, required=True)
            validate_text(self.notes_edit.toPlainText(), "Notes")
        except ValidationError as exc:
            return str(exc)
        if not self.authorized.isChecked():
            return "Please confirm the scope and your authorization."
        return None

    def accept(self) -> None:
        problem = self.validate()
        if problem:
            self.error_label.setText(problem)
            set_state(self.error_label, "error")
            self.error_label.setVisible(True)
            return
        super().accept()
