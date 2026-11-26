"""Analyst notes & visual-clue workspace tab on the Evidence details screen (Phase 7)."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from imagetrace.cases import CaseNotFoundError, ValidationError
from imagetrace.db import DatabaseError
from imagetrace.evidence import Evidence, EvidenceNotFoundError
from imagetrace.notes import (
    ANALYST_NOTES_BANNER,
    APPEND_ONLY_LIMITATION,
    CONFIDENCE_LABELS,
    MAX_SOURCE_REF,
    NOTE_CATEGORIES,
    SOURCE_REQUIRED_LABELS,
    AnalystNote,
    NoteNotFoundError,
    NotesRepository,
    validate_note_inputs,
)
from imagetrace.timefmt import format_local
from imagetrace.ui import dialogs
from imagetrace.ui.widgets import elide, label, set_state

NOTE_COLUMNS = ("ID", "Category", "Confidence", "Observation", "Author", "Status")
_ERRORS = (
    ValidationError,
    CaseNotFoundError,
    EvidenceNotFoundError,
    NoteNotFoundError,
    DatabaseError,
    sqlite3.Error,
)


class NoteDialog(QDialog):
    def __init__(
        self,
        actor_label: str,
        parent: QWidget | None = None,
        existing: AnalystNote | None = None,
    ) -> None:
        super().__init__(parent)
        self.existing = existing
        self.setWindowTitle(
            f"Revise analyst note #{existing.id}" if existing else "New analyst note"
        )
        self.setMinimumWidth(560)

        lay = QVBoxLayout(self)
        lay.addWidget(label(ANALYST_NOTES_BANNER, obj="Banner"))

        form = QFormLayout()
        self.category_combo = QComboBox()
        self.category_combo.setAccessibleName("Note category")
        for cat in NOTE_CATEGORIES:
            self.category_combo.addItem(cat, cat)
        if existing and existing.category in NOTE_CATEGORIES:
            self.category_combo.setCurrentIndex(NOTE_CATEGORIES.index(existing.category))
        form.addRow(self._buddy("&Category:", self.category_combo), self.category_combo)

        self.confidence_combo = QComboBox()
        self.confidence_combo.setAccessibleName("Confidence label")
        for conf in CONFIDENCE_LABELS:
            self.confidence_combo.addItem(conf, conf)
        if existing and existing.confidence_label in CONFIDENCE_LABELS:
            self.confidence_combo.setCurrentIndex(
                CONFIDENCE_LABELS.index(existing.confidence_label)
            )
        else:
            # Default conservatively to 'Analyst observation' (never auto-default to Confirmed!)
            self.confidence_combo.setCurrentIndex(CONFIDENCE_LABELS.index("Analyst observation"))
        self.confidence_combo.currentIndexChanged.connect(self._update_source_label)
        form.addRow(self._buddy("C&onfidence label:", self.confidence_combo), self.confidence_combo)

        self.source_caption = label("", wrap=False)
        self.source_edit = QLineEdit(existing.source_reference if existing else "")
        self.source_edit.setMaxLength(MAX_SOURCE_REF)
        self.source_edit.setPlaceholderText(
            "Field name, visible region, or corroborating reference supporting this entry"
        )
        self.source_edit.setAccessibleName("Source or citation reference")
        self.source_caption.setBuddy(self.source_edit)
        form.addRow(self.source_caption, self.source_edit)

        self.text_edit = QPlainTextEdit(existing.note_text if existing else "")
        self.text_edit.setPlaceholderText(
            "Record observable details (signage, landmarks, environmental clues, technical "
            "findings). State hypotheses explicitly."
        )
        self.text_edit.setAccessibleName("Observation text")
        self.text_edit.setTabChangesFocus(True)
        self.text_edit.setFixedHeight(120)
        form.addRow(self._buddy("&Observation *:", self.text_edit), self.text_edit)
        lay.addLayout(form)

        revision_hint = f"Recorded as analyst '{actor_label}'. " + (
            f"Saving creates a new revision and marks note #{existing.id} as superseded "
            "(append-only)."
            if existing
            else "Notes are timestamped and append-only (never silently deleted)."
        )
        lay.addWidget(label(revision_hint, muted=True))

        self.error_label = label("", state="error")
        self.error_label.setVisible(False)
        lay.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Save revision" if existing else "Add note")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self._update_source_label()
        self.text_edit.setFocus()

    @staticmethod
    def _buddy(text: str, widget: QWidget):
        lbl = label(text, wrap=False)
        lbl.setBuddy(widget)
        return lbl

    def _update_source_label(self) -> None:
        conf = self.confidence_combo.currentData()
        req = conf in SOURCE_REQUIRED_LABELS
        self.source_caption.setText("&Source / citation *:" if req else "&Source / citation:")

    def values(self) -> dict[str, str]:
        return {
            "category": str(self.category_combo.currentData()),
            "confidence_label": str(self.confidence_combo.currentData()),
            "source_reference": self.source_edit.text(),
            "note_text": self.text_edit.toPlainText(),
        }

    def accept(self) -> None:
        vals = self.values()
        try:
            validate_note_inputs(
                vals["note_text"],
                vals["category"],
                vals["confidence_label"],
                vals["source_reference"],
            )
        except ValidationError as exc:
            self.error_label.setText(str(exc))
            set_state(self.error_label, "error")
            self.error_label.setVisible(True)
            return
        super().accept()


class NotesTab(QWidget):
    notes_changed = Signal()

    def __init__(self, repo: NotesRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo
        self.current: Evidence | None = None
        self._notes: list[AnalystNote] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        body = QWidget()
        body.setObjectName("TabBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        lay.addWidget(label(ANALYST_NOTES_BANNER, obj="Banner"))

        # Row 1: actions
        row1 = QHBoxLayout()
        self.add_btn = QPushButton("Add note…")
        self.add_btn.setObjectName("Primary")
        self.add_btn.clicked.connect(self.add_note)
        self.revise_btn = QPushButton("Revise…")
        self.revise_btn.setEnabled(False)
        self.revise_btn.clicked.connect(self.revise_selected)
        self.retract_btn = QPushButton("Retract")
        self.retract_btn.setEnabled(False)
        self.retract_btn.clicked.connect(self.retract_selected)
        for btn in (self.add_btn, self.revise_btn, self.retract_btn):
            row1.addWidget(btn)
        row1.addStretch(1)
        lay.addLayout(row1)

        # Row 2: filter + show history
        row2 = QHBoxLayout()
        self.conf_filter = QComboBox()
        self.conf_filter.setAccessibleName("Filter notes by confidence level")
        self.conf_filter.addItem("All confidence levels", "all")
        for conf in CONFIDENCE_LABELS:
            self.conf_filter.addItem(conf, conf)
        self.conf_filter.currentIndexChanged.connect(lambda _i: self._populate())
        row2.addWidget(self.conf_filter)

        self.show_history = QCheckBox("Show superseded / retracted")
        self.show_history.toggled.connect(lambda _c: self.reload())
        row2.addWidget(self.show_history)
        row2.addStretch(1)
        lay.addLayout(row2)

        self.empty_label = label("", muted=True)
        lay.addWidget(self.empty_label)

        self.table = QTableWidget(0, len(NOTE_COLUMNS))
        self.table.setHorizontalHeaderLabels(NOTE_COLUMNS)
        self.table.setAccessibleName("Analyst notes")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setMinimumHeight(140)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        self.table.setColumnWidth(0, 44)
        self.table.setColumnWidth(1, 125)
        self.table.setColumnWidth(2, 125)
        self.table.setColumnWidth(3, 175)
        self.table.setColumnWidth(4, 75)
        self.table.itemSelectionChanged.connect(self._on_selection)
        lay.addWidget(self.table)

        # Selected note detail panel
        self.detail_host = QWidget()
        self.detail_host.setObjectName("FormHost")
        d_lay = QVBoxLayout(self.detail_host)
        d_lay.setContentsMargins(0, 4, 0, 0)
        d_lay.setSpacing(4)
        d_lay.addWidget(label("Selected note details", obj="CardTitle"))

        d_form = QFormLayout()
        d_form.setContentsMargins(0, 0, 0, 0)
        d_form.setVerticalSpacing(3)
        d_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.d_meta = label("")
        self.d_source = label("")
        self.d_rev = label("", muted=True)
        d_form.addRow(label("Attribution:", wrap=False, muted=True), self.d_meta)
        d_form.addRow(label("Source / citation:", wrap=False, muted=True), self.d_source)
        d_form.addRow(label("Revision chain:", wrap=False, muted=True), self.d_rev)
        d_lay.addLayout(d_form)

        self.d_text = QPlainTextEdit()
        self.d_text.setReadOnly(True)
        self.d_text.setAccessibleName("Selected note full text")
        self.d_text.setMinimumHeight(80)
        self.d_text.setMaximumHeight(140)
        d_lay.addWidget(self.d_text)
        self.detail_host.setVisible(False)
        lay.addWidget(self.detail_host)

        lay.addWidget(label(APPEND_ONLY_LIMITATION, muted=True))
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        outer.addWidget(scroll)

    # --- data -------------------------------------------------------------------------
    def show_evidence(self, item: Evidence | None) -> None:
        self.current = item
        self.add_btn.setEnabled(item is not None)
        self.reload()

    def reload(self, select_id: int | None = None) -> None:
        keep = select_id if select_id is not None else self.selected_note_id()
        if self.current is None:
            self._notes = []
            self.table.setRowCount(0)
            self.empty_label.setText("Select an evidence item to view or add analyst notes.")
            self.empty_label.setVisible(True)
            self._on_selection()
            return
        self._notes = self.repo.list_for_evidence(
            self.current.id, include_history=self.show_history.isChecked()
        )
        self._populate(select_id=keep)

    def _populate(self, select_id: int | None = None) -> None:
        conf = self.conf_filter.currentData()
        shown = [n for n in self._notes if conf == "all" or n.confidence_label == conf]

        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(shown))
        for row, n in enumerate(shown):
            one_line = " ".join(n.note_text.splitlines())
            cells = (
                f"#{n.id}",
                n.category,
                n.confidence_label,
                elide(one_line, 60),
                n.author_label,
                n.status,
            )
            for col, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, n.id)
                cell.setToolTip(n.note_text if col == 3 else text)
                self.table.setItem(row, col, cell)
        self.table.setSortingEnabled(True)

        if not self._notes:
            self.empty_label.setText(
                "No analyst notes recorded for this item yet. Click “Add note…” to record an "
                "observation."
            )
        elif not shown:
            self.empty_label.setText("No notes match the selected confidence filter.")
        self.empty_label.setVisible(not shown)

        self.table.clearSelection()
        if select_id is not None:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == select_id:
                    self.table.selectRow(row)
                    break
        self._on_selection()

    def selected_note_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        val = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return int(val) if val is not None else None

    def selected_note(self) -> AnalystNote | None:
        nid = self.selected_note_id()
        return self.repo.get(nid) if nid is not None else None

    def _on_selection(self) -> None:
        note = self.selected_note()
        active = note is not None and note.is_active
        self.revise_btn.setEnabled(active)
        self.retract_btn.setEnabled(active)
        self.detail_host.setVisible(note is not None)
        if note is None:
            return
        self.d_meta.setText(
            f"Note #{note.id} ({note.status}) · {note.confidence_label} · {note.category} · "
            f"by {note.author_label} at {format_local(note.created_at)}"
        )
        self.d_source.setText(note.source_reference or "None specified")
        if note.supersedes_note_id is not None:
            self.d_rev.setText(f"Supersedes note #{note.supersedes_note_id}")
        else:
            self.d_rev.setText("Original entry")
        self.d_text.setPlainText(note.note_text)

    # --- actions ----------------------------------------------------------------------
    def run_note_dialog(self, existing: AnalystNote | None) -> dict[str, str] | None:
        dlg = NoteDialog(self.repo.actor(), self, existing=existing)
        return dlg.values() if dlg.exec() == QDialog.DialogCode.Accepted else None

    def add_note(self) -> None:
        if self.current is None:
            return
        vals = self.run_note_dialog(None)
        if vals is None:
            return
        try:
            note = self.repo.create(self.current.case_id, self.current.id, **vals)
        except _ERRORS as exc:
            dialogs.error(self, "Could not add note", str(exc))
            return
        self.reload(select_id=note.id)
        self.notes_changed.emit()

    def revise_selected(self) -> None:
        old = self.selected_note()
        if old is None or not old.is_active:
            return
        vals = self.run_note_dialog(old)
        if vals is None:
            return
        try:
            revised = self.repo.revise(old.id, **vals)
        except _ERRORS as exc:
            dialogs.error(self, "Could not save revision", str(exc))
            return
        self.reload(select_id=revised.id)
        self.notes_changed.emit()

    def retract_selected(self) -> None:
        old = self.selected_note()
        if old is None or not old.is_active:
            return
        if not dialogs.confirm(
            self,
            "Retract analyst note",
            f"Mark note #{old.id} as retracted?\n\n"
            "It will be hidden from the default list, but the original record remains preserved "
            "in history (append-only).",
        ):
            return
        try:
            self.repo.retract(old.id)
        except _ERRORS as exc:
            dialogs.error(self, "Could not retract note", str(exc))
            return
        self.reload()
        self.notes_changed.emit()
