"""Background evidence import worker and progress dialog with cancel & duplicate confirmation."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QProgressBar, QPushButton, QVBoxLayout, QWidget

from imagetrace.cases import ValidationError
from imagetrace.db import Database, DatabaseError
from imagetrace.evidence import (
    DuplicateEvidenceError,
    Evidence,
    EvidenceError,
    EvidenceRepository,
    ImportCancelledError,
)
from imagetrace.ui import dialogs
from imagetrace.ui.widgets import label


@dataclass
class ImportReport:
    imported: list[Evidence] = field(default_factory=list)
    skipped_duplicates: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False


class ImportWorker(QThread):
    """Imports files sequentially on a worker thread with its own SQLite connection."""

    progress_changed = Signal(int, int, str)  # current_value, max_value, status_text
    duplicate_prompt = Signal(str, str, str)  # filename, existing_summary, sha256
    completed = Signal(object)  # ImportReport

    def __init__(
        self,
        db_path: Path,
        data_root: Path,
        case_id: str,
        paths: list[Path],
        *,
        actor_label: str,
        source_note: str,
        max_size_bytes: int,
        max_pixels: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.db_path = db_path
        self.data_root = data_root
        self.case_id = case_id
        self.paths = list(paths)
        self.actor_label = actor_label
        self.source_note = source_note
        self.max_size_bytes = max_size_bytes
        self.max_pixels = max_pixels
        self.cancel_event = threading.Event()
        self._dup_event = threading.Event()
        self._dup_allow = False

    def cancel(self) -> None:
        self.cancel_event.set()
        self._dup_allow = False
        self._dup_event.set()

    def answer_duplicate(self, allow: bool) -> None:
        self._dup_allow = bool(allow)
        self._dup_event.set()

    def _ask_duplicate(self, filename: str, existing: list[Evidence], sha256: str) -> bool:
        self._dup_event.clear()
        self._dup_allow = False
        summary = "\n".join(f"  - {e.id} ({e.original_filename})" for e in existing[:5])
        if len(existing) > 5:
            summary += f"\n  - …and {len(existing) - 5} more"
        self.duplicate_prompt.emit(filename, summary, sha256)
        self._dup_event.wait()
        return self._dup_allow and not self.cancel_event.is_set()

    def run(self) -> None:
        report = ImportReport()
        total_files = max(len(self.paths), 1)
        worker_db = Database(self.db_path)
        try:
            repo = EvidenceRepository(worker_db, self.data_root, actor=lambda: self.actor_label)
            for idx, path in enumerate(self.paths, start=1):
                if self.cancel_event.is_set():
                    report.cancelled = True
                    break

                def _cb(done: int, total: int, stage: str, idx: int = idx, name: str = path.name):
                    frac = (done / total) if total > 0 else 0.0
                    pct = int(((idx - 1 + min(frac, 1.0)) / total_files) * 100)
                    self.progress_changed.emit(
                        pct, 100, f"File {idx} of {total_files}: {name} - {stage}"
                    )

                self.progress_changed.emit(
                    int(((idx - 1) / total_files) * 100),
                    100,
                    f"File {idx} of {total_files}: {path.name}",
                )
                try:
                    ev = repo.import_file(
                        self.case_id,
                        path,
                        source_note=self.source_note,
                        max_size_bytes=self.max_size_bytes,
                        max_pixels=self.max_pixels,
                        allow_duplicate=False,
                        cancel_event=self.cancel_event,
                        on_progress=_cb,
                    )
                    report.imported.append(ev)
                except DuplicateEvidenceError as dup:
                    if self._ask_duplicate(path.name, dup.existing, dup.sha256):
                        try:
                            ev = repo.import_file(
                                self.case_id,
                                path,
                                source_note=self.source_note,
                                max_size_bytes=self.max_size_bytes,
                                max_pixels=self.max_pixels,
                                allow_duplicate=True,
                                cancel_event=self.cancel_event,
                                on_progress=_cb,
                            )
                            report.imported.append(ev)
                        except ImportCancelledError:
                            report.cancelled = True
                            break
                        except (
                            EvidenceError,
                            ValidationError,
                            DatabaseError,
                            sqlite3.Error,
                            OSError,
                        ) as exc:
                            report.rejected.append((path.name, str(exc)))
                    elif self.cancel_event.is_set():
                        report.cancelled = True
                        break
                    else:
                        report.skipped_duplicates.append(path.name)
                except ImportCancelledError:
                    report.cancelled = True
                    break
                except (
                    EvidenceError,
                    ValidationError,
                    DatabaseError,
                    sqlite3.Error,
                    OSError,
                ) as exc:
                    report.rejected.append((path.name, str(exc)))
            self.progress_changed.emit(100, 100, "Done")
        finally:
            worker_db.close()
        self.completed.emit(report)


class ImportProgressDialog(QDialog):
    def __init__(self, worker: ImportWorker, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.worker = worker
        self.report: ImportReport = ImportReport()
        self.setWindowTitle("Importing evidence")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        self.status_label = label("Starting import…")
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
        worker.duplicate_prompt.connect(self._on_duplicate)
        worker.completed.connect(self._on_completed)

    def start(self) -> ImportReport:
        self.worker.start()
        self.exec()
        self.worker.wait()
        return self.report

    def _on_progress(self, value: int, maximum: int, text: str) -> None:
        self.bar.setMaximum(maximum)
        self.bar.setValue(value)
        self.status_label.setText(text)

    def _on_duplicate(self, filename: str, existing_summary: str, sha256: str) -> None:
        allow = dialogs.confirm(
            self,
            "Duplicate evidence detected",
            f"'{filename}' has the same SHA-256 hash as existing evidence in this case:\n\n"
            f"{existing_summary}\n\nSHA-256: {sha256}\n\n"
            "Import it again as a separate evidence item?",
        )
        self.worker.answer_duplicate(allow)

    def _on_cancel(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Cancelling…")
        self.worker.cancel()

    def _on_completed(self, report: ImportReport) -> None:
        self.report = report
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
