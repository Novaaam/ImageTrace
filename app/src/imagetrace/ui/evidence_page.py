"""Evidence page: import (picker + drag/drop), evidence list and 8-tab details screen."""

from __future__ import annotations

import contextlib
import sqlite3
import stat
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from imagetrace.audit import list_events, verify_chain
from imagetrace.cases import Case, CaseRepository, ValidationError
from imagetrace.db import DatabaseError
from imagetrace.evidence import (
    MAX_SOURCE_NOTE,
    Evidence,
    EvidenceError,
    EvidenceRepository,
)
from imagetrace.gps import GpsError, GpsService
from imagetrace.image_analysis import SUPPORTED_FILE_FILTER
from imagetrace.metadata import MetadataService
from imagetrace.notes import APPEND_ONLY_LIMITATION, NotesRepository
from imagetrace.ocr import OcrService
from imagetrace.paths import format_bytes
from imagetrace.provenance import ProvenanceError, ProvenanceService
from imagetrace.settings import Settings
from imagetrace.timefmt import format_local
from imagetrace.ui import dialogs
from imagetrace.ui.evidence_import import ImportProgressDialog, ImportReport, ImportWorker
from imagetrace.ui.gps_tab import GpsTab
from imagetrace.ui.metadata_tab import MetadataTab
from imagetrace.ui.notes_tab import NotesTab
from imagetrace.ui.ocr_tab import OcrTab
from imagetrace.ui.provenance_tab import ProvenanceTab
from imagetrace.ui.viewer_dialog import ImageViewerDialog
from imagetrace.ui.widgets import Card, Page, clear_layout, label, set_state

COLUMNS = ("Evidence ID", "Original filename", "Format", "Size", "Status", "Imported")
EVENT_LABELS = {
    "evidence_imported": "Evidence imported",
    "evidence_integrity_checked": "Integrity verified",
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
_ERRORS = (EvidenceError, ValidationError, DatabaseError, sqlite3.Error, OSError)


def _scroll_tab(content: QWidget) -> QScrollArea:
    content.setObjectName("TabBody")
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    return scroll


class EvidenceDetails(QWidget):
    """8-tab Evidence details screen (blueprint section 4 + v0.2.0 Origin & Trace)."""

    integrity_changed = Signal()

    def __init__(
        self,
        repo: EvidenceRepository,
        metadata_service: MetadataService,
        gps_service: GpsService,
        ocr_service: OcrService,
        notes_repo: NotesRepository,
        provenance_service: ProvenanceService,
        get_settings: Callable[[], Settings],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.repo = repo
        self.metadata_service = metadata_service
        self.gps_service = gps_service
        self.ocr_service = ocr_service
        self.notes_repo = notes_repo
        self.provenance_service = provenance_service
        self.get_settings = get_settings
        self.current: Evidence | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.placeholder_card = Card("Evidence details")
        self.placeholder_card.body.addWidget(
            label(
                "Select an evidence item to inspect its overview, properties and history.",
                muted=True,
            )
        )
        outer.addWidget(self.placeholder_card)

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Evidence details tabs")
        self.tabs.addTab(self._build_overview_tab(), "Overview")
        self.provenance_tab = ProvenanceTab(provenance_service)
        self.provenance_tab.trace_changed.connect(self._on_provenance_changed)
        self.provenance_tab.open_viewer_requested.connect(self.open_image_viewer)
        self.tabs.addTab(self.provenance_tab, "Origin && Trace")
        self.metadata_tab = MetadataTab(metadata_service)
        self.metadata_tab.metadata_changed.connect(self._on_metadata_changed)
        self.tabs.addTab(self.metadata_tab, "Metadata")
        self.gps_tab = GpsTab(gps_service, get_settings)
        self.gps_tab.gps_changed.connect(self._on_gps_changed)
        self.tabs.addTab(self.gps_tab, "GPS")
        self.ocr_tab = OcrTab(ocr_service, get_settings)
        self.ocr_tab.ocr_changed.connect(self._on_ocr_changed)
        self.tabs.addTab(self.ocr_tab, "OCR")
        self.tabs.addTab(self._build_properties_tab(), "Image properties")
        self.notes_tab = NotesTab(notes_repo)
        self.notes_tab.notes_changed.connect(self._on_notes_changed)
        self.tabs.addTab(self.notes_tab, "Analyst notes")
        self.tabs.addTab(self._build_integrity_tab(), "Integrity && history")
        outer.addWidget(self.tabs, 1)
        self.show_evidence(None)

    # --- tab builders -----------------------------------------------------------------
    def _build_overview_tab(self) -> QWidget:
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        self.thumb_label = QLabel("No thumbnail available")
        self.thumb_label.setTextFormat(Qt.TextFormat.PlainText)
        self.thumb_label.setObjectName("ThumbBox")
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setMinimumHeight(170)
        self.thumb_label.setMaximumHeight(220)
        self.thumb_label.setMinimumWidth(0)
        lay.addWidget(self.thumb_label)

        form_host = QWidget()
        form_host.setObjectName("FormHost")
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 0, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.ov_fields: dict[str, QLabel] = {}
        for key, caption in (
            ("id", "Evidence ID"),
            ("original", "Original filename"),
            ("stored", "Stored filename"),
            ("format", "Detected format"),
            ("size", "File size"),
            ("imported", "Imported"),
            ("status", "Status"),
            ("metadata", "Metadata analysis"),
            ("ocr", "OCR analysis"),
            ("notes", "Analyst notes"),
            ("note", "Import note"),
        ):
            val = label("")
            self.ov_fields[key] = val
            form.addRow(label(caption + ":", wrap=False, muted=True), val)

        self.ov_hash = QLineEdit()
        self.ov_hash.setObjectName("Mono")
        self.ov_hash.setReadOnly(True)
        self.ov_hash.setAccessibleName("SHA-256 hash")
        hash_row = QHBoxLayout()
        hash_row.setContentsMargins(0, 0, 0, 0)
        hash_row.addWidget(self.ov_hash, 1)
        self.copy_hash_btn = QPushButton("Copy")
        self.copy_hash_btn.setToolTip("Copy SHA-256 to clipboard")
        self.copy_hash_btn.clicked.connect(self._copy_sha256)
        hash_row.addWidget(self.copy_hash_btn)
        form.addRow(label("SHA-256:", wrap=False, muted=True), hash_row)
        lay.addWidget(form_host)

        verify_row = QHBoxLayout()
        self.verify_btn = QPushButton("Verify file integrity")
        self.verify_btn.clicked.connect(self.verify_current)
        verify_row.addWidget(self.verify_btn)
        self.inspect_viewer_btn = QPushButton("Inspect / Zoom…")
        self.inspect_viewer_btn.setToolTip(
            "Open full interactive Zoom/Pan Loupe and Side-by-Side Comparison Viewer"
        )
        self.inspect_viewer_btn.clicked.connect(lambda: self.open_image_viewer("single"))
        verify_row.addWidget(self.inspect_viewer_btn)
        self.integrity_status = label("", muted=True)
        verify_row.addWidget(self.integrity_status, 1)
        lay.addLayout(verify_row)

        self.warnings_title = label("Warnings", obj="CardTitle")
        self.warnings_box = QVBoxLayout()
        self.warnings_box.setSpacing(4)
        lay.addWidget(self.warnings_title)
        lay.addLayout(self.warnings_box)
        lay.addStretch(1)
        return _scroll_tab(body)

    def _build_properties_tab(self) -> QWidget:
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        form_host = QWidget()
        form_host.setObjectName("FormHost")
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 0, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.prop_fields: dict[str, QLabel] = {}
        for key, caption in (
            ("dimensions", "Dimensions"),
            ("color_mode", "Color mode"),
            ("format", "Format / MIME"),
            ("size", "File size"),
            ("icc", "ICC profile"),
            ("dhash", "Perceptual hash (dHash)"),
        ):
            val = label("", obj="Mono" if key == "dhash" else None)
            self.prop_fields[key] = val
            form.addRow(label(caption + ":", wrap=False, muted=True), val)
        lay.addWidget(form_host)

        lay.addWidget(label("Visual similarity (dHash)", obj="CardTitle"))
        lay.addWidget(
            label(
                "Similarity note: perceptual hashing (dHash) is an automated visual-similarity "
                "heuristic, not proof that two images are identical or from the same source.",
                obj="Banner",
            )
        )
        self.similar_list = QListWidget()
        self.similar_list.setObjectName("History")
        self.similar_list.setAccessibleName("Visually similar evidence")
        self.similar_list.setMaximumHeight(110)
        self.similar_list.setWordWrap(True)
        lay.addWidget(self.similar_list)

        lay.addWidget(label("Derived viewing copy (original stays unchanged)", obj="CardTitle"))
        controls = QFormLayout()
        self.rotate_combo = QComboBox()
        for deg in (0, 90, 180, 270):
            self.rotate_combo.addItem(f"{deg}°" if deg else "0° (none)", deg)
        controls.addRow(label("Rotate CW:", wrap=False, muted=True), self.rotate_combo)

        self.contrast_spin = QDoubleSpinBox()
        self.contrast_spin.setRange(0.5, 2.0)
        self.contrast_spin.setSingleStep(0.1)
        self.contrast_spin.setValue(1.0)
        self.contrast_spin.setSuffix("x")
        controls.addRow(label("Contrast:", wrap=False, muted=True), self.contrast_spin)

        self.resize_combo = QComboBox()
        self.resize_combo.addItem("Original dimensions", None)
        self.resize_combo.addItem("Fit within 1024 px", 1024)
        self.resize_combo.addItem("Fit within 512 px", 512)
        controls.addRow(label("Resize:", wrap=False, muted=True), self.resize_combo)
        lay.addLayout(controls)

        btn_row = QHBoxLayout()
        self.derived_btn = QPushButton("Create derived copy")
        self.derived_btn.clicked.connect(self.create_derived)
        btn_row.addWidget(self.derived_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        self.derived_result = label("", muted=True)
        lay.addWidget(self.derived_result)
        self.derived_preview = QLabel("")
        self.derived_preview.setTextFormat(Qt.TextFormat.PlainText)
        self.derived_preview.setObjectName("ThumbBox")
        self.derived_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.derived_preview.setVisible(False)
        lay.addWidget(self.derived_preview)
        lay.addStretch(1)
        return _scroll_tab(body)

    def _build_integrity_tab(self) -> QWidget:
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        form_host = QWidget()
        form_host.setObjectName("FormHost")
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 0, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.int_fields: dict[str, QLabel | QLineEdit] = {}
        for key, caption in (
            ("sha256", "Original SHA-256"),
            ("stored_rel", "Stored location"),
            ("readonly", "Read-only attribute"),
            ("tools", "App & tool versions"),
            ("exports", "Export history"),
        ):
            if key in ("sha256", "stored_rel"):
                val: QLabel | QLineEdit = QLineEdit()
                val.setReadOnly(True)
                if key == "sha256":
                    val.setObjectName("Mono")
                val.setAccessibleName(caption)
            else:
                val = label("")
            self.int_fields[key] = val
            form.addRow(label(caption + ":", wrap=False, muted=True), val)
        lay.addWidget(form_host)

        btn_row = QHBoxLayout()
        self.verify_btn_2 = QPushButton("Verify file integrity")
        self.verify_btn_2.clicked.connect(self.verify_current)
        btn_row.addWidget(self.verify_btn_2)
        self.verify_chain_btn = QPushButton("Verify audit chain")
        self.verify_chain_btn.clicked.connect(self.verify_audit_chain)
        btn_row.addWidget(self.verify_chain_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        self.chain_status = label("", muted=True)
        lay.addWidget(self.chain_status)

        lay.addWidget(label("Chain-of-custody history", obj="CardTitle"))
        self.history = QListWidget()
        self.history.setObjectName("History")
        self.history.setAccessibleName("Evidence history")
        self.history.setMinimumHeight(140)
        self.history.setWordWrap(True)
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history.setResizeMode(QListWidget.ResizeMode.Adjust)
        lay.addWidget(self.history)
        lay.addWidget(label(APPEND_ONLY_LIMITATION, muted=True))
        lay.addStretch(1)
        return _scroll_tab(body)

    # --- population -------------------------------------------------------------------
    def show_evidence(self, item: Evidence | None) -> None:
        self.current = item
        self.placeholder_card.setVisible(item is None)
        self.tabs.setVisible(item is not None)
        self.derived_preview.setVisible(False)
        self.derived_result.setText("")
        self.integrity_status.setText("")
        set_state(self.integrity_status, None)
        self.chain_status.setText("")
        set_state(self.chain_status, None)
        if item is None:
            self.provenance_tab.show_evidence(None)
            self.metadata_tab.show_evidence(None)
            self.gps_tab.show_evidence(None)
            self.ocr_tab.show_evidence(None)
            self.notes_tab.show_evidence(None)
            return

        # Overview thumbnail
        thumb = self.repo.thumbnail_path(item)
        if thumb is not None and thumb.is_file():
            pix = QPixmap(str(thumb))
            if not pix.isNull():
                self.thumb_label.setPixmap(
                    pix.scaled(
                        280,
                        190,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:
                self.thumb_label.setPixmap(QPixmap())
                self.thumb_label.setText("Thumbnail could not be loaded.")
        else:
            self.thumb_label.setPixmap(QPixmap())
            self.thumb_label.setText("No thumbnail available (image could not be decoded).")

        self.ov_fields["id"].setText(item.id)
        self.ov_fields["original"].setText(item.original_filename)
        self.ov_fields["stored"].setText(item.stored_filename)
        self.ov_fields["format"].setText(f"{item.detected_format} ({item.detected_mime})")
        self.ov_fields["size"].setText(f"{format_bytes(item.file_size)} ({item.file_size:,} bytes)")
        self.ov_fields["imported"].setText(format_local(item.imported_at))
        self.ov_fields["status"].setText(item.status)
        set_state(self.ov_fields["status"], "ok" if item.status == "imported" else "warn")
        self._update_overview_metadata_status(item.id)
        self._update_overview_ocr_status(item.id)
        self._update_overview_notes_status(item.id)
        self.ov_fields["note"].setText(item.import_source_note or "none")
        self.provenance_tab.show_evidence(item)
        self.metadata_tab.show_evidence(item)
        self.gps_tab.show_evidence(item)
        self.ocr_tab.show_evidence(item)
        self.notes_tab.show_evidence(item)
        self.ov_hash.setText(item.sha256)
        self.ov_hash.setCursorPosition(0)
        self.ov_hash.setToolTip(item.sha256)

        clear_layout(self.warnings_box)
        self.warnings_title.setVisible(bool(item.warnings))
        for w in item.warnings:
            self.warnings_box.addWidget(label(f"• {w}", state="warn"))

        # Image properties
        dims = f"{item.width} x {item.height} px" if (item.width and item.height) else "unavailable"
        self.prop_fields["dimensions"].setText(dims)
        self.prop_fields["color_mode"].setText(item.color_mode or "unavailable")
        self.prop_fields["format"].setText(f"{item.detected_format} ({item.detected_mime})")
        self.prop_fields["size"].setText(
            f"{format_bytes(item.file_size)} ({item.file_size:,} bytes)"
        )
        self.prop_fields["icc"].setText(item.icc_profile_info or "unavailable")
        self.prop_fields["dhash"].setText(item.dhash or "unavailable")
        self.derived_btn.setEnabled(bool(item.width and item.height and item.dhash))

        self.similar_list.clear()
        if not item.dhash:
            self.similar_list.addItem("Perceptual hash unavailable for this item.")
        else:
            matches = self.repo.find_similar(item.id)
            if not matches:
                self.similar_list.addItem("No visually similar images found in this case.")
            for m in matches:
                exact = "exact SHA-256 duplicate" if m.exact_sha256_match else "different bytes"
                self.similar_list.addItem(
                    f"{m.evidence.id}  ·  {m.evidence.original_filename}  "
                    f"(dHash distance {m.hamming_distance}/64; {exact})"
                )

        # Integrity & history
        orig_path = self.repo.original_path(item)
        self.int_fields["sha256"].setText(item.sha256)
        self.int_fields["sha256"].setCursorPosition(0)
        self.int_fields["sha256"].setToolTip(item.sha256)
        stored_rel = f"cases/{item.case_id}/originals/{item.stored_filename}"
        self.int_fields["stored_rel"].setText(stored_rel)
        self.int_fields["stored_rel"].setCursorPosition(0)
        self.int_fields["stored_rel"].setToolTip(stored_rel)
        if orig_path.is_file():
            ro = not bool(orig_path.stat().st_mode & stat.S_IWRITE)
            self.int_fields["readonly"].setText("Yes (read-only)" if ro else "No (writable)")
            set_state(self.int_fields["readonly"], "ok" if ro else "warn")
        else:
            self.int_fields["readonly"].setText("Original file missing!")
            set_state(self.int_fields["readonly"], "error")
        self.int_fields["tools"].setText(item.analysis_tool_version or "unknown")
        exp_rows = self.repo.db.con.execute(
            "SELECT export_type, created_at FROM exports "
            "WHERE case_id = ? AND (evidence_id = ? OR evidence_id IS NULL) ORDER BY id DESC",
            (item.case_id, item.id),
        ).fetchall()
        if not exp_rows:
            self.int_fields["exports"].setText("None yet (use the Reports page)")
        else:
            latest_t = "PDF" if exp_rows[0]["export_type"] == "pdf_report" else "JSON"
            self.int_fields["exports"].setText(
                f"{len(exp_rows)} report{'s' if len(exp_rows) != 1 else ''} "
                f"(latest: {latest_t} at {format_local(exp_rows[0]['created_at'])})"
            )
        self._reload_history(item.id)

    def _reload_history(self, evidence_id: str) -> None:
        self.history.clear()
        for ev in list_events(self.repo.db.con, evidence_id=evidence_id):
            what = EVENT_LABELS.get(ev.event_type, ev.event_type)
            if ev.event_type == "evidence_integrity_checked":
                what += " (OK)" if ev.details.get("ok") else " (MISMATCH)"
            elif ev.event_type == "derived_copy_created":
                fname = ev.details.get("derived_filename", "")
                if fname:
                    what += f" ({fname})"
            elif ev.event_type == "metadata_extracted":
                ver = ev.details.get("source_version", "")
                emb = ev.details.get("embedded_records", 0)
                what += f" (ExifTool {ver}; {emb} embedded)"
            elif ev.event_type == "metadata_exported":
                fname = ev.details.get("export_filename", "")
                if fname:
                    what += f" ({fname})"
            elif ev.event_type == "origin_traced":
                cat = ev.details.get("primary_category", "")
                steps = ev.details.get("timeline_steps", 0)
                if cat:
                    what += f" ({cat}; {steps} steps)"
            elif ev.event_type == "reverse_search_link_opened":
                eng = ev.details.get("engine", "")
                if eng:
                    what += f" ({eng})"
            elif ev.event_type == "gps_parsed":
                has = ev.details.get("has_gps")
                valid = ev.details.get("is_valid")
                what += (
                    " (valid GPS)"
                    if (has and valid)
                    else (" (invalid/incomplete GPS)" if has else " (no GPS found)")
                )
            elif ev.event_type == "external_map_preview_loaded":
                z = ev.details.get("zoom", "")
                what += f" (zoom {z})"
            elif ev.event_type == "ocr_completed":
                eng = ev.details.get("engine", "")
                ver = ev.details.get("engine_version", "")
                lang = ev.details.get("language", "")
                cnt = ev.details.get("item_count", 0)
                what += f" ({eng} {ver}, {lang}, {cnt} items)"
            elif ev.event_type == "ocr_exported":
                fname = ev.details.get("export_filename", "")
                if fname:
                    what += f" ({fname})"
            elif ev.event_type == "note_created":
                nid = ev.details.get("note_id", "")
                conf = ev.details.get("confidence_label", "")
                what += f" (#{nid}; {conf})"
            elif ev.event_type == "note_revised":
                old_id = ev.details.get("previous_note_id", "")
                new_id = ev.details.get("new_note_id", "")
                conf = ev.details.get("confidence_label", "")
                what += f" (#{old_id} -> #{new_id}; {conf})"
            elif ev.event_type == "note_retracted":
                nid = ev.details.get("note_id", "")
                what += f" (#{nid})"
            elif ev.event_type == "report_exported":
                fname = ev.details.get("filename", "")
                if fname:
                    what += f" ({fname})"
            elif ev.event_type == "export_verified":
                what += " (OK)" if ev.details.get("ok") else " (MISMATCH)"
            self.history.addItem(f"{format_local(ev.event_time)}\n{what}  ·  {ev.actor_label}")

    def _update_overview_metadata_status(self, evidence_id: str) -> None:
        run = self.metadata_service.latest_run(evidence_id)
        if run is None:
            self.ov_fields["metadata"].setText("Not extracted yet (use the Metadata tab)")
            set_state(self.ov_fields["metadata"], None)
        else:
            emb = int(run.output.get("embedded_records", 0))
            ver = run.output.get("source_version", "")
            warn_note = f", {len(run.warnings)} warning(s)" if run.warnings else ""
            self.ov_fields["metadata"].setText(
                f"Extracted via ExifTool {ver} ({emb} embedded fields{warn_note})"
            )
            set_state(self.ov_fields["metadata"], "ok" if run.status == "ok" else "warn")

    def _on_metadata_changed(self) -> None:
        if self.current is not None:
            # Automatically parse embedded GPS and Origin & Trace from extracted metadata (offline).
            with contextlib.suppress(GpsError, DatabaseError, sqlite3.Error):
                self.gps_service.parse(self.current.id, extract_if_missing=False)
            with contextlib.suppress(ProvenanceError, DatabaseError, sqlite3.Error):
                self.provenance_service.analyze(self.current.id, extract_metadata_if_missing=False)
            self._update_overview_metadata_status(self.current.id)
            self.provenance_tab.show_evidence(self.current)
            self.gps_tab.show_evidence(self.current)
            self._reload_history(self.current.id)
        self.integrity_changed.emit()

    def _on_provenance_changed(self) -> None:
        if self.current is not None:
            with contextlib.suppress(GpsError, DatabaseError, sqlite3.Error):
                if self.metadata_service.latest_run(self.current.id) is not None:
                    self.gps_service.parse(self.current.id, extract_if_missing=False)
            self._update_overview_metadata_status(self.current.id)
            self.metadata_tab.show_evidence(self.current)
            self.gps_tab.show_evidence(self.current)
            self._reload_history(self.current.id)
        self.integrity_changed.emit()

    def _update_overview_ocr_status(self, evidence_id: str) -> None:
        run = self.ocr_service.latest_run(evidence_id)
        if run is None:
            self.ov_fields["ocr"].setText("Not run (user-triggered on the OCR tab)")
            set_state(self.ov_fields["ocr"], None)
        else:
            self.ov_fields["ocr"].setText(
                f"Automated OCR via {run.engine} {run.engine_version} (lang: {run.language})"
            )
            set_state(self.ov_fields["ocr"], "ok" if run.status == "ok" else "warn")

    def _on_ocr_changed(self) -> None:
        if self.current is not None:
            self._update_overview_ocr_status(self.current.id)
            self._reload_history(self.current.id)
        self.integrity_changed.emit()

    def _update_overview_notes_status(self, evidence_id: str) -> None:
        notes = self.notes_repo.list_for_evidence(evidence_id, include_history=False)
        if not notes:
            self.ov_fields["notes"].setText("0 active notes")
        else:
            self.ov_fields["notes"].setText(
                f"{len(notes)} active note{'s' if len(notes) != 1 else ''}"
            )

    def _on_notes_changed(self) -> None:
        if self.current is not None:
            self._update_overview_notes_status(self.current.id)
            self._reload_history(self.current.id)
        self.integrity_changed.emit()

    def verify_audit_chain(self) -> bool:
        rep = verify_chain(self.repo.db.con)
        self.chain_status.setText(rep.message)
        set_state(self.chain_status, "ok" if rep.ok else "error")
        if not rep.ok:
            dialogs.error(self, "Audit chain inconsistent", rep.message)
        return rep.ok

    def _on_gps_changed(self) -> None:
        if self.current is not None:
            self._update_overview_metadata_status(self.current.id)
            self.metadata_tab.show_evidence(self.current)
            self._reload_history(self.current.id)
        self.integrity_changed.emit()

    # --- actions ----------------------------------------------------------------------
    def open_image_viewer(self, initial_compare: str = "single") -> None:
        if self.current is None:
            return
        settings = self.get_settings()
        dlg = ImageViewerDialog(
            self.repo,
            self.current,
            initial_compare=initial_compare,
            max_pixels=settings.max_image_megapixels * 1_000_000,
            parent=self,
        )
        dlg.exec()

    def _copy_sha256(self) -> None:
        if self.current is None:
            return
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.current.sha256)
        self.integrity_status.setText("SHA-256 copied to clipboard.")
        set_state(self.integrity_status, "ok")

    def verify_current(self) -> None:
        if self.current is None:
            return
        try:
            res = self.repo.verify_integrity(self.current.id)
        except _ERRORS as exc:
            dialogs.error(self, "Verification failed", str(exc))
            return
        self.integrity_status.setText(res.message)
        set_state(self.integrity_status, "ok" if res.ok else "error")
        self._reload_history(self.current.id)
        self.integrity_changed.emit()
        if not res.ok:
            dialogs.error(self, "Integrity mismatch", res.message)

    def create_derived(self) -> None:
        if self.current is None:
            return
        settings = self.get_settings()
        try:
            res = self.repo.create_derived_view(
                self.current.id,
                rotate_deg=int(self.rotate_combo.currentData()),
                contrast=float(self.contrast_spin.value()),
                max_edge=self.resize_combo.currentData(),
                max_pixels=settings.max_image_megapixels * 1_000_000,
            )
        except _ERRORS as exc:
            self.derived_result.setText(f"Could not create derived copy: {exc}")
            set_state(self.derived_result, "error")
            dialogs.error(self, "Derived copy failed", str(exc))
            return

        self.derived_result.setText(
            f"Saved {res.filename} ({res.width} x {res.height} px; {res.summary}).\n"
            f"Derived SHA-256: {res.sha256}\n"
            "Original file SHA-256 verified unchanged."
        )
        set_state(self.derived_result, "ok")
        pix = QPixmap(str(res.path))
        if not pix.isNull():
            self.derived_preview.setPixmap(
                pix.scaled(
                    280,
                    180,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.derived_preview.setVisible(True)
        self._reload_history(self.current.id)
        self.integrity_changed.emit()


class EvidencePage(Page):
    evidence_changed = Signal()
    open_cases_requested = Signal()

    title = "Evidence"
    subtitle = (
        "Import images into the active case. Originals are copied and marked read-only; "
        "never modified or uploaded."
    )

    def __init__(
        self,
        repo: EvidenceRepository | None,
        cases_repo: CaseRepository | None,
        metadata_service: MetadataService | None,
        gps_service: GpsService | None,
        ocr_service: OcrService | None,
        notes_repo: NotesRepository | None,
        provenance_service: ProvenanceService | None,
        get_active_case_id: Callable[[], str | None],
        get_settings: Callable[[], Settings],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.repo = repo
        self.cases_repo = cases_repo
        self.metadata_service = metadata_service
        self.gps_service = gps_service
        self.ocr_service = ocr_service
        self.notes_repo = notes_repo
        self.provenance_service = provenance_service
        self.get_active_case_id = get_active_case_id
        self.get_settings = get_settings
        self.setAcceptDrops(repo is not None)

        if (
            repo is None
            or cases_repo is None
            or metadata_service is None
            or gps_service is None
            or ocr_service is None
            or notes_repo is None
            or provenance_service is None
        ):
            card = Card("Evidence unavailable")
            card.body.addWidget(
                label(
                    "The database isn't available. Check the storage status in the status bar.",
                    state="error",
                )
            )
            self.layout_.addWidget(card)
            self.layout_.addStretch(1)
            return

        # Shown when no active case is selected
        self.no_case_card = Card("No active case selected")
        self.no_case_card.body.addWidget(
            label(
                "Select or create an active case on the Cases page before importing evidence.",
                muted=True,
            )
        )
        go_row = QHBoxLayout()
        self.go_cases_btn = QPushButton("Go to Cases")
        self.go_cases_btn.setObjectName("Primary")
        self.go_cases_btn.clicked.connect(self.open_cases_requested.emit)
        go_row.addWidget(self.go_cases_btn)
        go_row.addStretch(1)
        self.no_case_card.body.addLayout(go_row)
        self.layout_.addWidget(self.no_case_card)

        # Workspace shown when an active case exists
        self.workspace = QWidget()
        ws = QVBoxLayout(self.workspace)
        ws.setContentsMargins(0, 0, 0, 0)
        ws.setSpacing(10)

        import_row = QHBoxLayout()
        self.import_btn = QPushButton("Import images…")
        self.import_btn.setObjectName("Primary")
        self.import_btn.setShortcut("Ctrl+I")
        self.import_btn.setToolTip("Choose image files to import into the active case (Ctrl+I)")
        self.import_btn.clicked.connect(self.choose_and_import)
        import_row.addWidget(self.import_btn)

        self.note_edit = QLineEdit()
        self.note_edit.setMaxLength(MAX_SOURCE_NOTE)
        self.note_edit.setPlaceholderText(
            "Optional provenance note (original folder path is not stored)"
        )
        self.note_edit.setAccessibleName("Import provenance note")
        self.note_edit.setClearButtonEnabled(True)
        import_row.addWidget(self.note_edit, 1)

        import_row.addWidget(label("or drag & drop files here", muted=True, wrap=False))
        ws.addLayout(import_row)

        filter_row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by evidence ID, filename, format or SHA-256")
        self.filter_edit.setAccessibleName("Filter evidence")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(lambda _t: self.reload())
        filter_row.addWidget(self.filter_edit, 1)
        self.toggle_list_btn = QPushButton("Expand details")
        self.toggle_list_btn.setCheckable(True)
        self.toggle_list_btn.setToolTip(
            "Collapse or show the evidence table to inspect wide details full-width"
        )
        self.toggle_list_btn.toggled.connect(self._toggle_list_pane)
        filter_row.addWidget(self.toggle_list_btn)
        ws.addLayout(filter_row)

        self.import_banner = label("", muted=True)
        self.import_banner.setVisible(False)
        ws.addWidget(self.import_banner)

        self.empty_label = label("", muted=True)
        ws.addWidget(self.empty_label)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAccessibleName("Evidence items")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.setColumnWidth(0, 130)
        self.table.setColumnWidth(1, 165)
        self.table.setColumnWidth(2, 60)
        self.table.setColumnWidth(3, 68)
        self.table.setColumnWidth(4, 72)
        self.table.itemSelectionChanged.connect(self._on_selection)

        self.details = EvidenceDetails(
            repo,
            metadata_service,
            gps_service,
            ocr_service,
            notes_repo,
            provenance_service,
            get_settings,
        )
        self.details.setMinimumWidth(420)
        self.details.integrity_changed.connect(self.evidence_changed.emit)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.details)
        self.splitter.setStretchFactor(0, 4)
        self.splitter.setStretchFactor(1, 6)
        self.splitter.setSizes([430, 550])
        self.splitter.setChildrenCollapsible(False)
        ws.addWidget(self.splitter, 1)

        self.layout_.addWidget(self.workspace, 1)
        self.refresh()

    # --- active case & data -----------------------------------------------------------
    def _toggle_list_pane(self, expanded: bool) -> None:
        self.table.setVisible(not expanded)
        self.toggle_list_btn.setText("Show list" if expanded else "Expand details")

    def _active_case(self) -> Case | None:
        if self.cases_repo is None:
            return None
        cid = self.get_active_case_id()
        if not cid:
            return None
        case = self.cases_repo.get(cid)
        return case if (case is not None and not case.archived) else None

    def refresh(self) -> None:
        if self.repo is None:
            return
        case = self._active_case()
        self.no_case_card.setVisible(case is None)
        self.workspace.setVisible(case is not None)
        if case is not None:
            self.reload()

    def reload(self, select_id: str | None = None) -> None:
        if self.repo is None:
            return
        case = self._active_case()
        if case is None:
            self.table.setRowCount(0)
            self.details.show_evidence(None)
            return
        keep = select_id or self.selected_evidence_id()
        try:
            items = self.repo.list_for_case(case.id, self.filter_edit.text())
            total = self.repo.count_for_case(case.id)
        except _ERRORS as exc:
            dialogs.error(self, "Could not load evidence", str(exc))
            return

        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(items))
        for row, ev in enumerate(items):
            cells = (
                ev.id,
                ev.original_filename,
                ev.detected_format,
                format_bytes(ev.file_size),
                ev.status,
                format_local(ev.imported_at),
            )
            for col, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, ev.id)
                if col == 1:
                    cell.setToolTip(ev.original_filename)
                self.table.setItem(row, col, cell)
        self.table.setSortingEnabled(True)

        if total == 0:
            self.empty_label.setText(
                "No evidence imported into this case yet. Use “Import images…” or drag files here."
            )
        elif not items:
            self.empty_label.setText("No evidence matches the current filter.")
        self.empty_label.setVisible(not items)

        self.table.clearSelection()
        if keep:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == keep:
                    self.table.selectRow(row)
                    break
        self._on_selection()

    def selected_evidence_id(self) -> str | None:
        if self.repo is None:
            return None
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def selected_evidence(self) -> Evidence | None:
        eid = self.selected_evidence_id()
        return self.repo.get(eid) if (self.repo and eid) else None

    def _on_selection(self) -> None:
        self.details.show_evidence(self.selected_evidence())

    # --- drag & drop ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # Qt API name
        if (
            self._active_case() is not None
            and event.mimeData().hasUrls()
            and any(u.isLocalFile() for u in event.mimeData().urls())
        ):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # Qt API name
        if self._active_case() is None or not event.mimeData().hasUrls():
            event.ignore()
            return
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.import_files(paths)

    # --- import workflow --------------------------------------------------------------
    def choose_and_import(self) -> None:
        if self._active_case() is None:
            return
        paths = dialogs.choose_open_files(
            self, "Import evidence images", None, SUPPORTED_FILE_FILTER
        )
        if paths:
            self.import_files(paths)

    def import_files(self, paths: list[Path]) -> ImportReport | None:
        if self.repo is None:
            return None
        case = self._active_case()
        if case is None:
            dialogs.error(
                self,
                "No active case",
                "Select an active case on the Cases page before importing evidence.",
            )
            return None
        settings = self.get_settings()
        worker = ImportWorker(
            self.repo.db.path,
            self.repo.data_root,
            case.id,
            paths,
            actor_label=settings.analyst_label,
            source_note=self.note_edit.text(),
            max_size_bytes=settings.max_import_mb * 1024**2,
            max_pixels=settings.max_image_megapixels * 1_000_000,
            parent=self,
        )
        dlg = ImportProgressDialog(worker, self)
        report = dlg.start()
        self._apply_import_report(report)
        return report

    def _apply_import_report(self, report: ImportReport) -> None:
        parts: list[str] = []
        if report.imported:
            parts.append(f"Imported {len(report.imported)} file(s).")
        if report.skipped_duplicates:
            parts.append(f"Skipped {len(report.skipped_duplicates)} duplicate(s).")
        if report.rejected:
            parts.append(f"Rejected {len(report.rejected)} file(s).")
        if report.cancelled:
            parts.append("Import cancelled.")
        summary = " ".join(parts) or "No files imported."
        self.import_banner.setText(summary)
        state = (
            "error"
            if (report.rejected and not report.imported)
            else ("warn" if (report.rejected or report.cancelled) else "ok")
        )
        set_state(self.import_banner, state)
        self.import_banner.setVisible(True)

        if report.rejected:
            detail_lines = [f"- {name}: {reason}" for name, reason in report.rejected[:10]]
            if len(report.rejected) > 10:
                detail_lines.append(f"- …and {len(report.rejected) - 10} more")
            dialogs.error(
                self,
                "Some files were rejected",
                summary + "\n\n" + "\n".join(detail_lines),
            )

        select_id = report.imported[-1].id if report.imported else None
        if report.imported:
            self.note_edit.clear()
        self.reload(select_id=select_id)
        if report.imported:
            self.evidence_changed.emit()
