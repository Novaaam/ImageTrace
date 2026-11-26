"""Reports screen: offline PDF/JSON generation, export hashes and verification (Phase 8)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from imagetrace.cases import Case, CaseNotFoundError, CaseRepository, ValidationError
from imagetrace.db import DatabaseError
from imagetrace.evidence import EvidenceNotFoundError, EvidenceRepository
from imagetrace.reports import (
    REPORTS_BANNER,
    ExportNotFoundError,
    ExportRecord,
    ReportError,
    ReportOptions,
    ReportService,
)
from imagetrace.timefmt import format_local
from imagetrace.ui import dialogs
from imagetrace.ui.batch_workers import BatchProgressDialog, PdfReportWorker
from imagetrace.ui.widgets import Card, Page, elide, label, set_state

EXPORT_COLUMNS = ("ID", "Type", "Scope", "Filename", "Created", "SHA-256")
_ERRORS = (
    ReportError,
    ValidationError,
    CaseNotFoundError,
    EvidenceNotFoundError,
    ExportNotFoundError,
    DatabaseError,
    sqlite3.Error,
    OSError,
)


class ReportsPage(Page):
    exports_changed = Signal()

    title = "Reports"
    subtitle = (
        "Generate offline PDF and JSON reports with provenance, SHA-256 hashes, limitations "
        "and verified export history."
    )

    def __init__(
        self,
        report_service: ReportService | None,
        cases_repo: CaseRepository | None,
        evidence_repo: EvidenceRepository | None,
        get_active_case_id: Callable[[], str | None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.reports = report_service
        self.cases = cases_repo
        self.evidence = evidence_repo
        self.get_active_case_id = get_active_case_id
        self._exports: list[ExportRecord] = []

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("SettingsBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(12)

        lay.addWidget(label(REPORTS_BANNER, obj="Banner"))

        # --- Card 1: Generate report ---
        gen_card = Card("Generate report")
        self.case_label = label("", muted=True)
        gen_card.body.addWidget(self.case_label)
        self.readiness_label = label("", muted=True)
        self.readiness_label.setVisible(False)
        gen_card.body.addWidget(self.readiness_label)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.scope_combo = QComboBox()
        self.scope_combo.setAccessibleName("Report scope")
        scope_caption = label("&Report scope:", wrap=False)
        scope_caption.setBuddy(self.scope_combo)
        form.addRow(scope_caption, self.scope_combo)
        gen_card.body.addLayout(form)

        self.include_ocr_cb = QCheckBox("Include full OCR text (labelled as automated / untrusted)")
        self.include_ocr_cb.setChecked(True)
        self.include_meta_cb = QCheckBox("Include full embedded metadata table")
        self.include_meta_cb.setChecked(False)
        self.include_note_history_cb = QCheckBox("Include superseded / retracted analyst notes")
        self.include_note_history_cb.setChecked(False)
        self.include_visuals_cb = QCheckBox(
            "Include evidence thumbnails && EXIF mismatch comparisons in PDF"
        )
        self.include_visuals_cb.setChecked(True)
        for cb in (
            self.include_ocr_cb,
            self.include_meta_cb,
            self.include_note_history_cb,
            self.include_visuals_cb,
        ):
            gen_card.body.addWidget(cb)

        btn_row = QHBoxLayout()
        self.pdf_btn = QPushButton("Generate PDF report")
        self.pdf_btn.setObjectName("Primary")
        self.pdf_btn.clicked.connect(lambda: self.generate("pdf_report", choose_dest=False))
        self.json_btn = QPushButton("Generate JSON report")
        self.json_btn.clicked.connect(lambda: self.generate("json_report", choose_dest=False))
        self.pdf_as_btn = QPushButton("Save PDF as…")
        self.pdf_as_btn.clicked.connect(lambda: self.generate("pdf_report", choose_dest=True))
        self.json_as_btn = QPushButton("Save JSON as…")
        self.json_as_btn.clicked.connect(lambda: self.generate("json_report", choose_dest=True))
        self.bundle_btn = QPushButton("Export case bundle (.zip)…")
        self.bundle_btn.setToolTip(
            "Export verifiable ZIP with PDF + JSON reports, metadata sidecars & MANIFEST.sha256"
        )
        self.bundle_btn.clicked.connect(self.export_case_bundle)
        for b in (
            self.pdf_btn,
            self.json_btn,
            self.pdf_as_btn,
            self.json_as_btn,
            self.bundle_btn,
        ):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        gen_card.body.addLayout(btn_row)

        self.gen_result = label("", muted=True)
        gen_card.body.addWidget(self.gen_result)
        lay.addWidget(gen_card)

        # --- Card 2: Export history & verification ---
        hist_card = Card("Export history and verification")
        self.empty_label = label("", muted=True)
        hist_card.body.addWidget(self.empty_label)

        self.table = QTableWidget(0, len(EXPORT_COLUMNS))
        self.table.setHorizontalHeaderLabels(EXPORT_COLUMNS)
        self.table.setAccessibleName("Export history")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setMinimumHeight(150)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        self.table.setColumnWidth(0, 48)
        self.table.setColumnWidth(1, 95)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 240)
        self.table.setColumnWidth(4, 155)
        self.table.itemSelectionChanged.connect(self._on_selection)
        hist_card.body.addWidget(self.table)

        act_row = QHBoxLayout()
        self.verify_btn = QPushButton("Verify selected export")
        self.verify_btn.setEnabled(False)
        self.verify_btn.clicked.connect(self.verify_selected)
        self.copy_sha_btn = QPushButton("Copy SHA-256")
        self.copy_sha_btn.setEnabled(False)
        self.copy_sha_btn.clicked.connect(self._copy_sha)
        self.open_dir_btn = QPushButton("Open reports folder")
        self.open_dir_btn.setEnabled(False)
        self.open_dir_btn.clicked.connect(self._open_reports_dir)
        for b in (self.verify_btn, self.copy_sha_btn, self.open_dir_btn):
            act_row.addWidget(b)
        act_row.addStretch(1)
        hist_card.body.addLayout(act_row)

        self.verify_status = label("", muted=True)
        hist_card.body.addWidget(self.verify_status)
        self.detail_label = label("", muted=True)
        hist_card.body.addWidget(self.detail_label)
        lay.addWidget(hist_card)
        lay.addStretch(1)

        scroll.setWidget(body)
        self.layout_.addWidget(scroll, 1)
        self.refresh()

    # --- helpers ----------------------------------------------------------------------
    def _active_case(self) -> Case | None:
        cid = self.get_active_case_id()
        if not cid or self.cases is None:
            return None
        return self.cases.get(cid)

    def current_options(self) -> ReportOptions:
        return ReportOptions(
            include_ocr_text=self.include_ocr_cb.isChecked(),
            include_full_metadata=self.include_meta_cb.isChecked(),
            include_note_history=self.include_note_history_cb.isChecked(),
            include_visuals=self.include_visuals_cb.isChecked(),
        )

    def refresh(self, select_id: int | None = None) -> None:
        keep_id = select_id if select_id is not None else self.selected_export_id()
        case = self._active_case()
        enabled = self.reports is not None and case is not None
        for b in (
            self.pdf_btn,
            self.json_btn,
            self.pdf_as_btn,
            self.json_as_btn,
            self.bundle_btn,
            self.open_dir_btn,
        ):
            b.setEnabled(enabled)
        self.scope_combo.setEnabled(enabled)

        prev_scope = self.scope_combo.currentData()
        self.scope_combo.clear()
        if self.reports is None or self.cases is None or self.evidence is None:
            self.case_label.setText("Database unavailable. Resolve storage first.")
            set_state(self.case_label, "error")
            self.readiness_label.setVisible(False)
            self._exports = []
            self.table.setRowCount(0)
            self.empty_label.setText("Reports unavailable while database is offline.")
            self.empty_label.setVisible(True)
            self._on_selection()
            return
        if case is None:
            self.case_label.setText(
                "No active case selected. Choose an active case on the Cases page to generate "
                "reports."
            )
            set_state(self.case_label, None)
            self.readiness_label.setVisible(False)
            self._exports = []
            self.table.setRowCount(0)
            self.empty_label.setText("Select an active case to see its export history.")
            self.empty_label.setVisible(True)
            self._on_selection()
            return

        items = self.evidence.list_for_case(case.id)
        self.case_label.setText(
            f"Active case: {case.id} · {case.title} ({len(items)} evidence item(s))"
        )
        set_state(self.case_label, None)
        done_meta = self.reports.metadata.extracted_evidence_ids(case.id)
        pending_meta = len([e for e in items if e.id not in done_meta])
        if pending_meta > 0:
            self.readiness_label.setText(
                f"Note: {pending_meta} of {len(items)} evidence item(s) do not have extracted "
                "metadata yet (run Analysis first if you want metadata included)."
            )
            set_state(self.readiness_label, "warn")
            self.readiness_label.setVisible(True)
        else:
            self.readiness_label.setVisible(False)

        self.scope_combo.addItem(f"Entire active case ({len(items)} evidence item(s))", None)
        for ev in items:
            self.scope_combo.addItem(
                f"{ev.id} · {elide(ev.original_filename, 44)}",
                ev.id,
            )
        if prev_scope is not None:
            idx = self.scope_combo.findData(prev_scope)
            if idx >= 0:
                self.scope_combo.setCurrentIndex(idx)

        self._exports = self.reports.list_exports(case.id)
        self._populate_table(select_id=keep_id)

    def _populate_table(self, select_id: int | None = None) -> None:
        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(self._exports))
        for row, rec in enumerate(self._exports):
            scope = rec.evidence_id or "Entire case"
            cells = (
                f"#{rec.id}",
                rec.type_label,
                scope,
                rec.filename,
                format_local(rec.created_at),
                rec.sha256[:16] + "…",
            )
            for col, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, rec.id)
                cell.setToolTip(rec.sha256 if col == 5 else text)
                self.table.setItem(row, col, cell)
        self.table.setSortingEnabled(True)

        if not self._exports:
            self.empty_label.setText(
                "No reports exported for this case yet. Use “Generate PDF report” or "
                "“Generate JSON report” above."
            )
        self.empty_label.setVisible(not self._exports)

        self.table.clearSelection()
        if select_id is not None:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == select_id:
                    self.table.selectRow(row)
                    break
        self._on_selection()

    def selected_export_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        val = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return int(val) if val is not None else None

    def selected_export(self) -> ExportRecord | None:
        eid = self.selected_export_id()
        if eid is None or self.reports is None:
            return None
        return self.reports.get(eid)

    def _on_selection(self) -> None:
        rec = self.selected_export()
        has = rec is not None
        self.verify_btn.setEnabled(has)
        self.copy_sha_btn.setEnabled(has)
        self.verify_status.setText("")
        set_state(self.verify_status, None)
        if rec is None:
            self.detail_label.setText("")
            return
        self.detail_label.setText(
            f"Export #{rec.id} ({rec.type_label}) · by {rec.actor_label} · "
            f"SHA-256: {rec.sha256}\nPath: {rec.path}"
        )

    # --- actions ----------------------------------------------------------------------
    def generate(self, export_type: str, *, choose_dest: bool) -> ExportRecord | None:
        if self.reports is None:
            return None
        case = self._active_case()
        if case is None:
            return None
        evidence_id = self.scope_combo.currentData()
        dest: Path | None = None
        if choose_dest:
            ext = "pdf" if export_type == "pdf_report" else "json"
            filt = "PDF report (*.pdf)" if ext == "pdf" else "JSON report (*.json)"
            default_name = f"ImageTrace-{evidence_id or case.id}-report.{ext}"
            dest = dialogs.choose_save_file(
                self,
                f"Save {ext.upper()} report",
                self.reports.reports_dir(case.id) / default_name,
                filt,
            )
            if dest is None:
                return None

        opts = self.current_options()
        try:
            if export_type == "pdf_report":
                ev_count = (
                    len(self.evidence.list_for_case(case.id))
                    if (self.evidence is not None and evidence_id is None)
                    else 1
                )
                if ev_count >= 5:
                    worker = PdfReportWorker(
                        self.reports.db.path,
                        self.reports.data_root,
                        case.id,
                        evidence_id=evidence_id,
                        dest_path=dest,
                        options=opts,
                        actor_label=self.reports.actor(),
                        parent=self,
                    )
                    dlg = BatchProgressDialog(
                        worker,
                        "Generating offline PDF report",
                        f"Rendering visual PDF report for {ev_count} evidence item(s)…",
                        parent=self,
                    )
                    outcome = dlg.start()
                    if isinstance(outcome, tuple) and outcome[0] == "ok":
                        rec = outcome[1]
                    else:
                        err = outcome[1] if isinstance(outcome, tuple) else "PDF export cancelled."
                        raise ReportError(str(err))
                else:
                    rec = self.reports.generate_pdf(
                        case.id, evidence_id=evidence_id, dest_path=dest, options=opts
                    )
            else:
                rec = self.reports.generate_json(
                    case.id, evidence_id=evidence_id, dest_path=dest, options=opts
                )
        except _ERRORS as exc:
            self.gen_result.setText(f"Report generation failed: {exc}")
            set_state(self.gen_result, "error")
            dialogs.error(self, "Report generation failed", str(exc))
            return None

        self.gen_result.setText(
            f"Saved {rec.type_label}: {rec.filename}\n"
            f"SHA-256: {rec.sha256} (.sha256 sidecar written)"
        )
        set_state(self.gen_result, "ok")
        self.refresh(select_id=rec.id)
        self.exports_changed.emit()
        return rec

    def export_case_bundle(self) -> None:
        if self.reports is None:
            return
        case = self._active_case()
        if case is None:
            return
        dest = dialogs.choose_save_file(
            self,
            "Export verifiable case bundle (.zip)",
            self.reports.reports_dir(case.id) / f"ImageTrace-{case.id}-bundle.zip",
            "ZIP archive (*.zip)",
        )
        if dest is None:
            return
        try:
            res = self.reports.generate_case_bundle(
                case.id, dest_path=dest, options=self.current_options()
            )
        except _ERRORS as exc:
            self.gen_result.setText(f"Case bundle export failed: {exc}")
            set_state(self.gen_result, "error")
            dialogs.error(self, "Case bundle export failed", str(exc))
            return
        msg = (
            f"Saved verifiable bundle: {res.filename} "
            f"({res.file_count} files incl. MANIFEST.sha256)\n"
            f"Bundle SHA-256: {res.sha256} (.sha256 sidecar written)"
        )
        self.gen_result.setText(msg)
        set_state(self.gen_result, "ok")
        dialogs.info(self, "Verifiable case bundle exported", msg)
        self.exports_changed.emit()

    def verify_selected(self) -> bool:
        if self.reports is None:
            return False
        rec = self.selected_export()
        if rec is None:
            return False
        try:
            res = self.reports.verify_export(rec.id)
        except _ERRORS as exc:
            self.verify_status.setText(f"Verification failed: {exc}")
            set_state(self.verify_status, "error")
            dialogs.error(self, "Export verification failed", str(exc))
            return False
        self.verify_status.setText(res.message)
        set_state(self.verify_status, "ok" if res.ok else "error")
        if not res.ok:
            dialogs.error(self, "Export verification mismatch", res.message)
        self.exports_changed.emit()
        return res.ok

    def _copy_sha(self) -> None:
        rec = self.selected_export()
        if rec is None:
            return
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(rec.sha256)
        self.verify_status.setText("Export SHA-256 copied to clipboard.")
        set_state(self.verify_status, "ok")

    def _open_reports_dir(self) -> None:
        if self.reports is None:
            return
        case = self._active_case()
        if case is None:
            return
        folder = self.reports.reports_dir(case.id)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
