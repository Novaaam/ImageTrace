"""Metadata tab on the Evidence details screen (Phase 4: ExifTool)."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from imagetrace.db import DatabaseError
from imagetrace.evidence import Evidence
from imagetrace.metadata import (
    METADATA_DISCLAIMER,
    ORIGIN_LABELS,
    AnalysisRun,
    MetadataError,
    MetadataRecord,
    MetadataService,
)
from imagetrace.timefmt import format_local
from imagetrace.ui import dialogs
from imagetrace.ui.widgets import clear_layout, label, set_state

META_COLUMNS = ("Origin", "Group", "Field", "Normalized value", "Raw value")
_ERRORS = (MetadataError, DatabaseError, sqlite3.Error, OSError)


class MetadataTab(QWidget):
    metadata_changed = Signal()

    def __init__(self, service: MetadataService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.current: Evidence | None = None
        self._records: list[MetadataRecord] = []
        self._run: AnalysisRun | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        body = QWidget()
        body.setObjectName("TabBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        lay.addWidget(label(METADATA_DISCLAIMER, obj="Banner"))

        # Actions bar
        action_row = QHBoxLayout()
        self.extract_btn = QPushButton("Extract metadata")
        self.extract_btn.setObjectName("Primary")
        self.extract_btn.clicked.connect(self.extract_current)
        action_row.addWidget(self.extract_btn)

        self.export_btn = QPushButton("Export JSON…")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_current)
        action_row.addWidget(self.export_btn)
        action_row.addStretch(1)
        lay.addLayout(action_row)

        self.status_label = label("No metadata extracted yet.", muted=True)
        lay.addWidget(self.status_label)

        # Compact embedded summary (camera + dates)
        self.summary_host = QWidget()
        self.summary_host.setObjectName("FormHost")
        sum_form = QFormLayout(self.summary_host)
        sum_form.setContentsMargins(0, 0, 0, 0)
        sum_form.setVerticalSpacing(2)
        sum_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.sum_fields: dict[str, QLabel] = {}
        for key, caption in (
            ("camera", "Camera (embedded)"),
            ("lens", "Lens (embedded)"),
            ("orientation", "Orientation"),
            ("software", "Software / tool"),
            ("date_original", "DateTimeOriginal"),
            ("date_create", "CreateDate"),
            ("date_modify", "ModifyDate"),
        ):
            val = label("")
            self.sum_fields[key] = val
            sum_form.addRow(label(caption + ":", wrap=False, muted=True), val)
        self.summary_host.setVisible(False)
        lay.addWidget(self.summary_host)

        # Warnings box
        self.warnings_title = label("Metadata warnings & discrepancies", obj="CardTitle")
        self.warnings_title.setVisible(False)
        self.warnings_box = QVBoxLayout()
        self.warnings_box.setSpacing(4)
        lay.addWidget(self.warnings_title)
        lay.addLayout(self.warnings_box)

        # Filter bar
        self.filter_host = QWidget()
        self.filter_host.setObjectName("FormHost")
        f_row = QHBoxLayout(self.filter_host)
        f_row.setContentsMargins(0, 0, 0, 0)
        f_row.setSpacing(8)
        self.origin_combo = QComboBox()
        self.origin_combo.setAccessibleName("Filter metadata by origin")
        self.origin_combo.addItem("All origins", "all")
        self.origin_combo.addItem("Embedded only", "embedded")
        self.origin_combo.addItem("File / container", "file_container")
        self.origin_combo.addItem("Tool / system", "application_system")
        self.origin_combo.currentIndexChanged.connect(lambda _i: self._populate_table())
        f_row.addWidget(self.origin_combo)

        self.group_combo = QComboBox()
        self.group_combo.setAccessibleName("Filter metadata by group")
        self.group_combo.addItem("All groups", "all")
        self.group_combo.currentIndexChanged.connect(lambda _i: self._populate_table())
        f_row.addWidget(self.group_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter fields or values")
        self.search_edit.setAccessibleName("Filter metadata fields")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(lambda _t: self._populate_table())
        f_row.addWidget(self.search_edit, 1)
        self.filter_host.setVisible(False)
        lay.addWidget(self.filter_host)

        # Metadata table
        self.table = QTableWidget(0, len(META_COLUMNS))
        self.table.setHorizontalHeaderLabels(META_COLUMNS)
        self.table.setAccessibleName("Extracted metadata fields")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setMinimumHeight(220)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        self.table.setColumnWidth(0, 96)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 180)
        self.table.setVisible(False)
        lay.addWidget(self.table, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        outer.addWidget(scroll)

    def show_evidence(self, item: Evidence | None) -> None:
        self.current = item
        self._records = []
        self._run = None
        if item is None:
            self._render_empty()
            return
        run, records = self.service.latest_metadata(item.id)
        self._run = run
        self._records = records
        if run is None:
            self._render_empty()
        else:
            self._render_run(run, records)

    def _render_empty(self) -> None:
        self.extract_btn.setText("Extract metadata")
        self.extract_btn.setEnabled(self.current is not None)
        self.export_btn.setEnabled(False)
        self.status_label.setText(
            "Metadata has not been extracted for this evidence item yet. "
            "Click “Extract metadata” to run ExifTool locally on the stored original (read-only)."
        )
        set_state(self.status_label, None)
        self.summary_host.setVisible(False)
        self.warnings_title.setVisible(False)
        clear_layout(self.warnings_box)
        self.filter_host.setVisible(False)
        self.table.setVisible(False)
        self.table.setRowCount(0)

    def _render_run(self, run: AnalysisRun, records: list[MetadataRecord]) -> None:
        self.extract_btn.setText("Re-extract metadata")
        self.extract_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        embedded = int(run.output.get("embedded_records", 0))
        total = int(run.output.get("total_records", len(records)))
        sidecar = run.output.get("sidecar_filename", "")
        self.status_label.setText(
            f"Extracted {format_local(run.finished_at)} via {run.version}  ·  "
            f"{embedded} embedded / {total} total fields"
            + (f"  ·  Sidecar: {sidecar}" if sidecar else "")
        )
        set_state(self.status_label, "ok" if run.status == "ok" else "warn")

        summary = run.output.get("summary") or {}
        cam = " ".join(p for p in (summary.get("make", ""), summary.get("model", "")) if p).strip()
        self.sum_fields["camera"].setText(cam or "None embedded")
        self.sum_fields["lens"].setText(summary.get("lens") or "None embedded")
        self.sum_fields["orientation"].setText(summary.get("orientation") or "None embedded")
        self.sum_fields["software"].setText(summary.get("software") or "None embedded")
        self.sum_fields["date_original"].setText(summary.get("date_original") or "None embedded")
        self.sum_fields["date_create"].setText(summary.get("date_create") or "None embedded")
        self.sum_fields["date_modify"].setText(summary.get("date_modify") or "None embedded")
        self.summary_host.setVisible(True)

        clear_layout(self.warnings_box)
        self.warnings_title.setVisible(bool(run.warnings))
        for w in run.warnings:
            self.warnings_box.addWidget(label(f"• {w}", state="warn"))

        # Rebuild group filter without firing redundant refreshes
        self.group_combo.blockSignals(True)
        prev_group = self.group_combo.currentData()
        self.group_combo.clear()
        self.group_combo.addItem("All groups", "all")
        categories = sorted({r.category for r in records})
        for cat in categories:
            self.group_combo.addItem(cat, cat)
        idx = self.group_combo.findData(prev_group)
        self.group_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.group_combo.blockSignals(False)

        self.filter_host.setVisible(True)
        self.table.setVisible(True)
        self._populate_table()

    def _populate_table(self) -> None:
        origin_filter = self.origin_combo.currentData()
        group_filter = self.group_combo.currentData()
        query = self.search_edit.text().strip().casefold()

        shown: list[MetadataRecord] = []
        for r in self._records:
            if origin_filter != "all" and r.origin != origin_filter:
                continue
            if group_filter != "all" and r.category != group_filter:
                continue
            if query:
                hay = f"{r.group_name} {r.field_name} {r.normalized_value} {r.raw_value}".casefold()
                if query not in hay:
                    continue
            shown.append(r)

        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(shown))
        for row, r in enumerate(shown):
            cells = (
                ORIGIN_LABELS.get(r.origin, r.origin),
                r.group_name,
                r.field_name,
                r.normalized_value,
                r.raw_value,
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)

    # --- actions ----------------------------------------------------------------------
    def extract_current(self) -> None:
        if self.current is None:
            return
        try:
            res = self.service.extract(self.current.id)
        except _ERRORS as exc:
            self.status_label.setText(f"Metadata extraction failed: {exc}")
            set_state(self.status_label, "error")
            dialogs.error(self, "Metadata extraction failed", str(exc))
            return
        self._run = res.run
        self._records = res.records
        self._render_run(res.run, res.records)
        self.metadata_changed.emit()

    def export_current(self) -> None:
        if self.current is None or self._run is None:
            return
        default_dest = self.service.reports_dir(self.current.case_id) / (
            f"{self.current.id}-metadata.json"
        )
        default_dest.parent.mkdir(parents=True, exist_ok=True)
        chosen = dialogs.choose_save_file(
            self, "Export metadata JSON", default_dest, "JSON file (*.json)"
        )
        if chosen is None:
            return
        try:
            res = self.service.export_json(self.current.id, chosen)
        except _ERRORS as exc:
            dialogs.error(self, "Export failed", str(exc))
            return
        dialogs.info(
            self,
            "Metadata exported",
            f"Saved {res.record_count} metadata record(s) to:\n{res.path}\n\nSHA-256: {res.sha256}",
        )
        self.metadata_changed.emit()
