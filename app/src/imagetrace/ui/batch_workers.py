"""Cancellable background workers for batch metadata/GPS analysis and case-wide integrity checks.

Follows ADR-018: each QThread opens its own short-lived SQLite Database connection inside
``run()`` and closes it in ``finally:``, never sharing sqlite3 connections across threads.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QProgressBar, QPushButton, QVBoxLayout, QWidget

from imagetrace.cases import CaseRepository
from imagetrace.db import Database, DatabaseError
from imagetrace.evidence import EvidenceError, EvidenceRepository
from imagetrace.gps import GpsError, GpsService
from imagetrace.metadata import MetadataError, MetadataService
from imagetrace.notes import NotesRepository
from imagetrace.ocr import OcrError, OcrService
from imagetrace.provenance import ProvenanceError, ProvenanceService
from imagetrace.reports import ReportError, ReportOptions, ReportService
from imagetrace.ui.widgets import label


@dataclass
class BatchAnalysisReport:
    succeeded: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False


@dataclass
class BatchIntegrityReport:
    verified_ok: list[str] = field(default_factory=list)
    mismatched: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False


class BatchAnalysisWorker(QThread):
    """Run ExifTool metadata extraction + offline GPS parsing over a list of evidence IDs."""

    progress_changed = Signal(int, int, str)
    completed = Signal(object)

    def __init__(
        self,
        db_path: Path,
        data_root: Path,
        evidence_ids: list[str],
        *,
        actor_label: str,
        exiftool_path: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.data_root = data_root
        self.evidence_ids = list(evidence_ids)
        self.actor_label = actor_label
        self.exiftool_path = exiftool_path
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        report = BatchAnalysisReport()
        total = max(len(self.evidence_ids), 1)
        worker_db = Database(self.db_path)
        try:
            ev_repo = EvidenceRepository(worker_db, self.data_root, actor=lambda: self.actor_label)
            meta = MetadataService(
                worker_db,
                ev_repo,
                actor=lambda: self.actor_label,
                exiftool_path_getter=lambda: self.exiftool_path,
            )
            gps = GpsService(worker_db, ev_repo, meta, actor=lambda: self.actor_label)
            prov = ProvenanceService(worker_db, ev_repo, meta, actor=lambda: self.actor_label)

            for idx, eid in enumerate(self.evidence_ids, start=1):
                if self.cancel_event.is_set():
                    report.cancelled = True
                    break
                self.progress_changed.emit(
                    idx - 1,
                    total,
                    f"Analyzing metadata, GPS & origin ({idx} of {total}): {eid}",
                )
                try:
                    meta.extract(eid)
                    gps.parse(eid, extract_if_missing=False)
                    prov.analyze(eid, extract_metadata_if_missing=False)
                    report.succeeded.append(eid)
                except (
                    MetadataError,
                    GpsError,
                    ProvenanceError,
                    EvidenceError,
                    DatabaseError,
                    sqlite3.Error,
                    OSError,
                ) as exc:
                    report.failed.append((eid, str(exc)))
            self.progress_changed.emit(total, total, "Done")
        finally:
            worker_db.close()
        self.completed.emit(report)


class BatchIntegrityWorker(QThread):
    """Verify stored original SHA-256 digests across all evidence items in a case."""

    progress_changed = Signal(int, int, str)
    completed = Signal(object)

    def __init__(
        self,
        db_path: Path,
        data_root: Path,
        evidence_ids: list[str],
        *,
        actor_label: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.data_root = data_root
        self.evidence_ids = list(evidence_ids)
        self.actor_label = actor_label
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        report = BatchIntegrityReport()
        total = max(len(self.evidence_ids), 1)
        worker_db = Database(self.db_path)
        try:
            ev_repo = EvidenceRepository(worker_db, self.data_root, actor=lambda: self.actor_label)
            for idx, eid in enumerate(self.evidence_ids, start=1):
                if self.cancel_event.is_set():
                    report.cancelled = True
                    break
                self.progress_changed.emit(
                    idx - 1,
                    total,
                    f"Verifying SHA-256 ({idx} of {total}): {eid}",
                )
                try:
                    res = ev_repo.verify_integrity(eid)
                    if res.ok:
                        report.verified_ok.append(eid)
                    else:
                        report.mismatched.append((eid, res.message))
                except (EvidenceError, DatabaseError, sqlite3.Error, OSError) as exc:
                    report.mismatched.append((eid, str(exc)))
            self.progress_changed.emit(total, total, "Done")
        finally:
            worker_db.close()
        self.completed.emit(report)


class SingleOcrWorker(QThread):
    """Run offline Tesseract OCR off the UI thread with a dedicated SQLite connection."""

    progress_changed = Signal(int, int, str)
    completed = Signal(object)

    def __init__(
        self,
        db_path: Path,
        data_root: Path,
        evidence_id: str,
        *,
        language: str,
        max_pixels: int,
        actor_label: str,
        tesseract_path: str = "",
        tessdata_dir: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.data_root = data_root
        self.evidence_id = evidence_id
        self.language = language
        self.max_pixels = max_pixels
        self.actor_label = actor_label
        self.tesseract_path = tesseract_path
        self.tessdata_dir = tessdata_dir
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        self.progress_changed.emit(
            0, 1, f"Running offline OCR ({self.language}) on {self.evidence_id}…"
        )
        worker_db = Database(self.db_path)
        try:
            ev_repo = EvidenceRepository(worker_db, self.data_root, actor=lambda: self.actor_label)
            ocr = OcrService(
                worker_db,
                ev_repo,
                actor=lambda: self.actor_label,
                tesseract_path_getter=lambda: self.tesseract_path,
                tessdata_dir_getter=lambda: self.tessdata_dir,
            )
            res = ocr.run_ocr(
                self.evidence_id,
                language=self.language,
                max_pixels=self.max_pixels,
            )
            self.progress_changed.emit(1, 1, "Done")
            self.completed.emit(("ok", res))
        except (OcrError, EvidenceError, DatabaseError, sqlite3.Error, OSError) as exc:
            self.completed.emit(("error", str(exc)))
        finally:
            worker_db.close()


class PdfReportWorker(QThread):
    """Generate offline PDF report off the UI thread using a dedicated SQLite connection."""

    progress_changed = Signal(int, int, str)
    completed = Signal(object)

    def __init__(
        self,
        db_path: Path,
        data_root: Path,
        case_id: str,
        *,
        evidence_id: str | None,
        dest_path: Path | None,
        options: ReportOptions,
        actor_label: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.data_root = data_root
        self.case_id = case_id
        self.evidence_id = evidence_id
        self.dest_path = dest_path
        self.options = options
        self.actor_label = actor_label
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        scope = self.evidence_id or self.case_id
        self.progress_changed.emit(0, 1, f"Generating offline PDF report for {scope}…")
        worker_db = Database(self.db_path)
        try:
            cases = CaseRepository(
                worker_db, self.data_root / "cases", actor=lambda: self.actor_label
            )
            ev_repo = EvidenceRepository(worker_db, self.data_root, actor=lambda: self.actor_label)
            meta = MetadataService(worker_db, ev_repo, actor=lambda: self.actor_label)
            gps = GpsService(worker_db, ev_repo, meta, actor=lambda: self.actor_label)
            ocr = OcrService(worker_db, ev_repo, actor=lambda: self.actor_label)
            notes = NotesRepository(worker_db, actor=lambda: self.actor_label)
            prov = ProvenanceService(worker_db, ev_repo, meta, actor=lambda: self.actor_label)
            reports = ReportService(
                worker_db,
                self.data_root,
                cases,
                ev_repo,
                meta,
                gps,
                ocr,
                notes,
                provenance_service=prov,
                actor=lambda: self.actor_label,
            )
            rec = reports.generate_pdf(
                self.case_id,
                evidence_id=self.evidence_id,
                dest_path=self.dest_path,
                options=self.options,
            )
            self.progress_changed.emit(1, 1, "Done")
            self.completed.emit(("ok", rec))
        except (
            ReportError,
            EvidenceError,
            DatabaseError,
            sqlite3.Error,
            OSError,
            ValueError,
        ) as exc:
            self.completed.emit(("error", str(exc)))
        finally:
            worker_db.close()


class BatchProgressDialog(QDialog):
    """Modal progress dialog with working Cancel button for background QThread workers."""

    def __init__(
        self,
        worker: BatchAnalysisWorker | BatchIntegrityWorker | SingleOcrWorker | PdfReportWorker,
        title: str,
        initial_text: str = "Starting…",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.worker = worker
        self.result_report: object = None
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        self.status_label = label(initial_text)
        lay.addWidget(self.status_label)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        lay.addWidget(self.bar)

        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel)
        row.addWidget(self.cancel_btn)
        lay.addLayout(row)

        worker.progress_changed.connect(self._on_progress)
        worker.completed.connect(self._on_completed)

    def start(self) -> object:
        self.worker.start()
        self.exec()
        self.worker.wait()
        return self.result_report

    def _on_progress(self, value: int, maximum: int, text: str) -> None:
        self.bar.setMaximum(max(maximum, 1))
        self.bar.setValue(value)
        self.status_label.setText(text)

    def _on_cancel(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Cancelling after current item…")
        self.worker.cancel()

    def _on_completed(self, report: object) -> None:
        self.result_report = report
        self.accept()

    def reject(self) -> None:
        if self.worker.isRunning():
            self._on_cancel()
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # Qt API name
        if self.worker.isRunning():
            self._on_cancel()
            event.ignore()
            return
        super().closeEvent(event)
