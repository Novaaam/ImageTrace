"""Cases page: list, filter, create, edit, archive/reopen, set active, details and history."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from imagetrace.audit import list_events, verify_chain
from imagetrace.cases import Case, CaseNotFoundError, CaseRepository, ValidationError
from imagetrace.db import DatabaseError
from imagetrace.notes import APPEND_ONLY_LIMITATION
from imagetrace.timefmt import format_local
from imagetrace.ui import dialogs
from imagetrace.ui.case_dialog import CaseDialog
from imagetrace.ui.widgets import Card, Page, label, set_state

COLUMNS = ("Case ID", "Title", "Status", "Tags", "Updated")
EVENT_LABELS = {
    "case_created": "Case created",
    "case_updated": "Case edited",
    "case_archived": "Case archived",
    "case_reopened": "Case reopened",
    "evidence_imported": "Evidence imported",
    "evidence_integrity_checked": "Evidence integrity checked",
    "derived_copy_created": "Derived copy created",
    "metadata_extracted": "Metadata extracted",
    "metadata_exported": "Metadata exported",
    "origin_traced": "Origin & Trace analyzed",
    "reverse_search_link_opened": "External reverse-search site opened (manual)",
    "gps_parsed": "GPS checked",
    "external_map_preview_loaded": "External map preview loaded (opt-in)",
    "ocr_completed": "OCR run (automated)",
    "ocr_exported": "OCR exported",
    "note_created": "Analyst note added",
    "note_revised": "Analyst note revised",
    "note_retracted": "Analyst note retracted",
    "report_exported": "Report exported",
    "case_bundle_exported": "Verifiable case bundle (.zip) exported",
    "export_verified": "Export verified",
}
_ERRORS = (ValidationError, CaseNotFoundError, DatabaseError, sqlite3.Error, OSError)


class CaseDetails(QWidget):
    def __init__(self, repo: CaseRepository, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = repo
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = Card("Case details")
        self.placeholder = label("Select a case to see its details.", muted=True)
        self.card.body.addWidget(self.placeholder)

        self.form_host = QWidget()
        self.form_host.setObjectName("FormHost")
        form = QFormLayout(self.form_host)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.fields: dict[str, object] = {}
        for key, caption in (
            ("id", "Case ID"),
            ("status", "Status"),
            ("created", "Created"),
            ("updated", "Updated"),
            ("tags", "Tags"),
            ("evidence", "Evidence items"),
            ("notes_count", "Analyst notes"),
            ("scope", "Scope / authorization"),
            ("notes", "Notes"),
            ("exports", "Export history"),
        ):
            value = label("")
            self.fields[key] = value
            form.addRow(label(caption + ":", wrap=False, muted=True), value)
        self.card.body.addWidget(self.form_host)

        self.card.body.addWidget(label("History", obj="CardTitle"))
        chain_row = QHBoxLayout()
        self.verify_chain_btn = QPushButton("Verify audit chain")
        self.verify_chain_btn.clicked.connect(self._verify_chain)
        chain_row.addWidget(self.verify_chain_btn)
        self.chain_status = label("", muted=True)
        chain_row.addWidget(self.chain_status, 1)
        self.card.body.addLayout(chain_row)

        self.history = QListWidget()
        self.history.setObjectName("History")
        self.history.setAccessibleName("Case history")
        self.history.setMinimumHeight(120)
        self.history.setWordWrap(True)
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.card.body.addWidget(self.history)
        self.card.body.addWidget(label(APPEND_ONLY_LIMITATION, muted=True))
        outer.addWidget(self.card)
        outer.addStretch(1)
        self.show_case(None)

    def show_case(self, case: Case | None, active: bool = False) -> None:
        self.placeholder.setVisible(case is None)
        self.form_host.setVisible(case is not None)
        self.history.setVisible(case is not None)
        self.history.clear()
        if case is None:
            return
        ev_count = int(
            self.repo.db.con.execute(
                "SELECT COUNT(*) FROM evidence WHERE case_id = ?", (case.id,)
            ).fetchone()[0]
        )
        notes_count = int(
            self.repo.db.con.execute(
                "SELECT COUNT(*) FROM notes WHERE case_id = ? AND status = 'active'", (case.id,)
            ).fetchone()[0]
        )
        export_rows = self.repo.db.con.execute(
            "SELECT export_type, created_at FROM exports WHERE case_id = ? ORDER BY id DESC",
            (case.id,),
        ).fetchall()
        if not export_rows:
            exports_txt = "None yet (use the Reports page to export PDF or JSON)"
        else:
            latest_t = "PDF" if export_rows[0]["export_type"] == "pdf_report" else "JSON"
            exports_txt = (
                f"{len(export_rows)} report{'s' if len(export_rows) != 1 else ''} "
                f"(latest: {latest_t} at {format_local(export_rows[0]['created_at'])})"
            )
        status = case.status + (" · active case" if active else "")
        values = {
            "id": case.id,
            "status": status,
            "created": format_local(case.created_at),
            "updated": format_local(case.updated_at),
            "tags": ", ".join(case.tags) or "none",
            "evidence": f"{ev_count} item{'s' if ev_count != 1 else ''}",
            "notes_count": f"{notes_count} active note{'s' if notes_count != 1 else ''}",
            "scope": case.scope_text,
            "notes": case.notes or "none",
            "exports": exports_txt,
        }
        for key, text in values.items():
            self.fields[key].setText(text)
        self.chain_status.setText("")
        set_state(self.chain_status, None)
        for ev in list_events(self.repo.db.con, case.id):
            what = EVENT_LABELS.get(ev.event_type, ev.event_type)
            fields = ev.details.get("fields")
            if fields:
                what += f" ({', '.join(map(str, fields))})"
            self.history.addItem(f"{format_local(ev.event_time)}\n{what}  ·  {ev.actor_label}")

    def _verify_chain(self) -> None:
        rep = verify_chain(self.repo.db.con)
        self.chain_status.setText(rep.message)
        set_state(self.chain_status, "ok" if rep.ok else "error")


class CasesPage(Page):
    active_case_changed = Signal(object)  # str | None
    cases_changed = Signal()

    title = "Cases"
    subtitle = (
        "Cases group evidence under a stated scope. Cases are archived, never deleted, and can "
        "be reopened."
    )

    def __init__(
        self,
        repo: CaseRepository | None,
        get_active: Callable[[], str | None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.repo = repo
        self.get_active = get_active
        if repo is None:
            card = Card("Cases unavailable")
            card.body.addWidget(
                label(
                    "The case database isn't available. Check the storage status in the "
                    "status bar.",
                    state="error",
                )
            )
            self.layout_.addWidget(card)
            self.layout_.addStretch(1)
            return

        toolbar = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by case ID, title, tag or notes")
        self.filter_edit.setAccessibleName("Filter cases")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(lambda _t: self.reload())
        toolbar.addWidget(self.filter_edit, 1)
        self.show_archived = QCheckBox("Show archived")
        self.show_archived.toggled.connect(lambda _c: self.reload())
        toolbar.addWidget(self.show_archived)
        toolbar.addSpacing(12)
        self.new_btn = QPushButton("New case…")
        self.new_btn.setObjectName("Primary")
        self.new_btn.setShortcut("Ctrl+N")
        self.new_btn.setToolTip("Create a case (Ctrl+N)")
        self.edit_btn = QPushButton("Edit…")
        self.archive_btn = QPushButton("Archive")
        self.active_btn = QPushButton("Set as active case")
        for btn, slot in (
            (self.new_btn, self.new_case),
            (self.edit_btn, self.edit_case),
            (self.archive_btn, self.toggle_archive),
            (self.active_btn, self.set_active),
        ):
            btn.clicked.connect(slot)
            toolbar.addWidget(btn)
        self.layout_.addLayout(toolbar)

        self.empty_label = label("", muted=True)
        self.layout_.addWidget(self.empty_label)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAccessibleName("Cases")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.doubleClicked.connect(lambda _i: self.edit_case())

        self.details = CaseDetails(repo)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(320)
        scroll.setWidget(self.details)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.table)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setChildrenCollapsible(False)
        self.layout_.addWidget(splitter, 1)
        self.reload()

    # --- data -------------------------------------------------------------------------
    def refresh(self) -> None:
        if self.repo is not None:
            self.reload()

    def set_filter(self, text: str) -> None:
        if self.repo is not None:
            self.filter_edit.setText(text)

    def reload(self, select_id: str | None = None) -> None:
        if self.repo is None:
            return
        keep = select_id or self.selected_case_id()
        active = self.get_active()
        try:
            cases = self.repo.list(self.filter_edit.text(), self.show_archived.isChecked())
            total = self.repo.count(include_archived=True)
        except _ERRORS as exc:
            dialogs.error(self, "Could not load cases", str(exc))
            return
        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(cases))
        for row, case in enumerate(cases):
            status = case.status + (" · active" if case.id == active else "")
            cells = (
                case.id,
                case.title,
                status,
                ", ".join(case.tags),
                format_local(case.updated_at),
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, case.id)
                if col == 1:
                    item.setToolTip(case.title)
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)

        if total == 0:
            self.empty_label.setText("No cases yet. Use “New case…” to create one.")
        elif not cases:
            self.empty_label.setText("No cases match the current filter.")
        self.empty_label.setVisible(not cases)

        self.table.clearSelection()
        if keep:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == keep:
                    self.table.selectRow(row)
                    break
        self._on_selection()

    def selected_case_id(self) -> str | None:
        if self.repo is None:
            return None
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def selected_case(self) -> Case | None:
        case_id = self.selected_case_id()
        return self.repo.get(case_id) if (self.repo and case_id) else None

    def _on_selection(self) -> None:
        case = self.selected_case()
        active = self.get_active()
        self.details.show_case(case, active=case is not None and case.id == active)
        has = case is not None
        self.edit_btn.setEnabled(has)
        self.archive_btn.setEnabled(has)
        self.archive_btn.setText("Reopen" if has and case.archived else "Archive")
        self.active_btn.setEnabled(has and not case.archived and case.id != active)

    # --- actions ----------------------------------------------------------------------
    def run_case_dialog(self, case: Case | None) -> dict[str, object] | None:
        dlg = CaseDialog(self, case)
        return dlg.values() if dlg.exec() == QDialog.DialogCode.Accepted else None

    def new_case(self) -> None:
        values = self.run_case_dialog(None)
        if values is None:
            return
        try:
            case = self.repo.create(**values)
        except _ERRORS as exc:
            dialogs.error(self, "Could not create case", str(exc))
            return
        if self.show_archived.isChecked() is False and self.filter_edit.text():
            self.filter_edit.clear()
        self.reload(select_id=case.id)
        self.cases_changed.emit()

    def edit_case(self) -> None:
        case = self.selected_case()
        if case is None:
            return
        values = self.run_case_dialog(case)
        if values is None:
            return
        try:
            self.repo.update(case.id, **values)
        except _ERRORS as exc:
            dialogs.error(self, "Could not save case", str(exc))
            return
        self.reload(select_id=case.id)
        self.cases_changed.emit()

    def toggle_archive(self) -> None:
        case = self.selected_case()
        if case is None:
            return
        try:
            if case.archived:
                self.repo.reopen(case.id)
            else:
                if not dialogs.confirm(
                    self,
                    "Archive case",
                    f"Archive case {case.id}?\n\nIt will be hidden from the default list and "
                    "can be reopened at any time. Nothing is deleted.",
                ):
                    return
                self.repo.archive(case.id)
                if case.id == self.get_active():
                    self.active_case_changed.emit(None)
        except _ERRORS as exc:
            dialogs.error(self, "Could not change case status", str(exc))
            return
        self.reload(select_id=case.id)
        self.cases_changed.emit()

    def set_active(self) -> None:
        case = self.selected_case()
        if case is None or case.archived:
            return
        self.active_case_changed.emit(case.id)
        self.reload(select_id=case.id)
