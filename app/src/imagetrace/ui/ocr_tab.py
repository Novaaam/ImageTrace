"""OCR tab on the Evidence details screen (Phase 6: optional offline Tesseract OCR).

- User-triggered only: never runs automatically on import or tab switch.
- Labels all output as Automated result and treats extracted text strictly as untrusted data
  (plain text only; never rendered as HTML, never executed).
- Displays full extracted text, language, engine/version, timestamp, confidence and word
  bounding boxes, plus JSON and TXT export with provenance.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from imagetrace.cases import ValidationError
from imagetrace.db import DatabaseError
from imagetrace.evidence import Evidence
from imagetrace.ocr import (
    OCR_DISCLAIMER,
    TESSERACT_INSTALL_GUIDE,
    OcrError,
    OcrItem,
    OcrRun,
    OcrService,
    compute_mean_confidence,
)
from imagetrace.settings import Settings
from imagetrace.timefmt import format_local
from imagetrace.ui import dialogs
from imagetrace.ui.batch_workers import BatchProgressDialog, SingleOcrWorker
from imagetrace.ui.widgets import clear_layout, label, set_state

OCR_COLUMNS = ("Text (untrusted)", "Confidence", "Bounding box (x, y, w x h)")
_ERRORS = (OcrError, ValidationError, DatabaseError, sqlite3.Error, OSError)


class OcrTab(QWidget):
    ocr_changed = Signal()

    def __init__(
        self,
        service: OcrService,
        get_settings: Callable[[], Settings],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.get_settings = get_settings
        self.current: Evidence | None = None
        self._run: OcrRun | None = None
        self._items: list[OcrItem] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        body = QWidget()
        body.setObjectName("TabBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        lay.addWidget(label(OCR_DISCLAIMER, obj="Banner"))

        # Controls row 1: language + run + setup guide
        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(label("Language:", wrap=False, muted=True))
        self.lang_combo = QComboBox()
        self.lang_combo.setEditable(True)
        self.lang_combo.setAccessibleName("OCR language code")
        self.lang_combo.setFixedWidth(100)
        self.lang_combo.setToolTip("Tesseract language code (e.g. eng or eng+deu)")
        ctrl_row.addWidget(self.lang_combo)

        self.run_btn = QPushButton("Run OCR")
        self.run_btn.setObjectName("Primary")
        self.run_btn.clicked.connect(self.run_current)
        ctrl_row.addWidget(self.run_btn)

        self.guide_btn = QPushButton("Setup guide…")
        self.guide_btn.clicked.connect(self._show_guide)
        ctrl_row.addWidget(self.guide_btn)
        ctrl_row.addStretch(1)
        lay.addLayout(ctrl_row)

        # Controls row 2: export + copy actions
        export_row = QHBoxLayout()
        self.export_json_btn = QPushButton("Export JSON…")
        self.export_json_btn.setEnabled(False)
        self.export_json_btn.clicked.connect(lambda: self.export_current("json"))
        export_row.addWidget(self.export_json_btn)

        self.export_txt_btn = QPushButton("Export TXT…")
        self.export_txt_btn.setEnabled(False)
        self.export_txt_btn.clicked.connect(lambda: self.export_current("txt"))
        export_row.addWidget(self.export_txt_btn)

        self.copy_text_btn = QPushButton("Copy text")
        self.copy_text_btn.setEnabled(False)
        self.copy_text_btn.clicked.connect(self._copy_text)
        export_row.addWidget(self.copy_text_btn)
        export_row.addStretch(1)
        lay.addLayout(export_row)

        self.engine_hint = label("", muted=True)
        lay.addWidget(self.engine_hint)

        self.status_label = label("OCR has not been run for this item.", muted=True)
        lay.addWidget(self.status_label)

        # Warnings
        self.warnings_title = label("OCR warnings", obj="CardTitle")
        self.warnings_title.setVisible(False)
        self.warnings_box = QVBoxLayout()
        self.warnings_box.setSpacing(4)
        lay.addWidget(self.warnings_title)
        lay.addLayout(self.warnings_box)

        # Results section
        self.results_host = QWidget()
        self.results_host.setObjectName("FormHost")
        r_lay = QVBoxLayout(self.results_host)
        r_lay.setContentsMargins(0, 0, 0, 0)
        r_lay.setSpacing(6)

        r_lay.addWidget(
            label("Extracted text (AUTOMATED RESULT - untrusted data)", obj="CardTitle")
        )

        # QPlainTextEdit renders strictly plain text (never evaluates HTML/scripts)
        self.text_view = QPlainTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setAccessibleName("Extracted OCR text (untrusted)")
        self.text_view.setMinimumHeight(110)
        self.text_view.setMaximumHeight(180)
        r_lay.addWidget(self.text_view)

        r_lay.addWidget(label("Detected words, confidence & bounding boxes", obj="CardTitle"))
        self.items_table = QTableWidget(0, len(OCR_COLUMNS))
        self.items_table.setHorizontalHeaderLabels(OCR_COLUMNS)
        self.items_table.setAccessibleName("OCR detected word items")
        self.items_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.items_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.items_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.items_table.verticalHeader().setVisible(False)
        self.items_table.setShowGrid(False)
        self.items_table.setMinimumHeight(150)
        hdr = self.items_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        self.items_table.setColumnWidth(0, 180)
        self.items_table.setColumnWidth(1, 90)
        r_lay.addWidget(self.items_table, 1)

        self.results_host.setVisible(False)
        lay.addWidget(self.results_host, 1)
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        self._refresh_languages()

    # --- helpers ----------------------------------------------------------------------
    def _refresh_languages(self) -> None:
        current_text = self.lang_combo.currentText().strip() or "eng"
        langs = self.service.available_languages()
        self.lang_combo.blockSignals(True)
        self.lang_combo.clear()
        for lang in langs:
            self.lang_combo.addItem(lang)
        idx = self.lang_combo.findText(current_text)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        else:
            self.lang_combo.setEditText(current_text)
        self.lang_combo.blockSignals(False)

        exe = self.service.resolve_tesseract()
        if exe is not None:
            self.engine_hint.setText(f"Offline OCR engine detected: {exe}")
            set_state(self.engine_hint, "ok")
        elif self.service._custom_runner is not None:
            self.engine_hint.setText("OCR engine configured.")
            set_state(self.engine_hint, "ok")
        else:
            self.engine_hint.setText(
                "Tesseract OCR is not installed yet (optional). Click “Setup guide…” for "
                "offline D-drive installation and language-pack instructions, or configure its "
                "path in Settings -> External tools."
            )
            set_state(self.engine_hint, "warn")

    def show_evidence(self, item: Evidence | None) -> None:
        self.current = item
        self._refresh_languages()
        if item is None:
            self._run = None
            self._items = []
            self._render_empty()
            return
        run, items = self.service.latest_ocr(item.id)
        self._run = run
        self._items = items
        if run is None:
            self._render_empty()
        else:
            self._render_run(run, items)

    def _render_empty(self) -> None:
        self.run_btn.setText("Run OCR")
        self.run_btn.setEnabled(self.current is not None)
        self.export_json_btn.setEnabled(False)
        self.export_txt_btn.setEnabled(False)
        self.copy_text_btn.setEnabled(False)
        self.status_label.setText(
            "OCR has not been run on this evidence item. OCR only runs when you click “Run OCR”."
        )
        set_state(self.status_label, None)
        self.warnings_title.setVisible(False)
        clear_layout(self.warnings_box)
        self.results_host.setVisible(False)
        self.text_view.clear()
        self.items_table.setRowCount(0)

    def _render_run(self, run: OcrRun, items: list[OcrItem]) -> None:
        self.run_btn.setText("Re-run OCR")
        self.run_btn.setEnabled(True)
        self.export_json_btn.setEnabled(True)
        self.export_txt_btn.setEnabled(True)

        mean_conf = compute_mean_confidence(items)
        conf_note = f" · mean confidence {mean_conf:.1f}%" if mean_conf is not None else ""
        self.status_label.setText(
            f"Automated OCR run {format_local(run.run_at)} via {run.engine} "
            f"{run.engine_version} (lang: {run.language}) · {len(items)} item(s){conf_note}"
        )
        set_state(self.status_label, "ok" if run.status == "ok" else "warn")

        clear_layout(self.warnings_box)
        self.warnings_title.setVisible(bool(run.warnings))
        for w in run.warnings:
            self.warnings_box.addWidget(label(f"• {w}", state="warn"))

        self.text_view.setPlainText(run.full_text)
        self.copy_text_btn.setEnabled(bool(run.full_text))

        self.items_table.setRowCount(len(items))
        for row, it in enumerate(items):
            conf_s = f"{it.confidence:.1f}%" if it.confidence is not None else "n/a"
            bb = it.bounding_box
            if all(k in bb for k in ("left", "top", "width", "height")):
                bb_s = f"({bb['left']}, {bb['top']}, {bb['width']} x {bb['height']})"
            else:
                bb_s = "n/a"
            for col, text in enumerate((it.text, conf_s, bb_s)):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)
                self.items_table.setItem(row, col, cell)

        self.results_host.setVisible(True)

    # --- actions ----------------------------------------------------------------------
    def _show_guide(self) -> None:
        dialogs.info(self, "Offline Tesseract OCR setup", TESSERACT_INSTALL_GUIDE)

    def _copy_text(self) -> None:
        if self._run is None or not self._run.full_text:
            return
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self._run.full_text)
        self.status_label.setText("Copied automated OCR text to clipboard (verify against image).")
        set_state(self.status_label, "ok")

    def run_current(self) -> None:
        if self.current is None:
            return
        lang = self.lang_combo.currentText().strip() or "eng"
        settings = self.get_settings()
        max_px = settings.max_image_megapixels * 1_000_000
        # Run live Tesseract off the UI thread; keep injected mock runners synchronous for tests.
        if self.service._custom_runner is None and self.service.resolve_tesseract() is not None:
            worker = SingleOcrWorker(
                self.service.db.path,
                self.service.evidence.data_root,
                self.current.id,
                language=lang,
                max_pixels=max_px,
                actor_label=self.service.actor(),
                tesseract_path=settings.tesseract_path,
                tessdata_dir=settings.tessdata_dir,
                parent=self,
            )
            dlg = BatchProgressDialog(
                worker,
                "Running offline OCR",
                f"Running offline Tesseract OCR ({lang}) on {self.current.id}…",
                parent=self,
            )
            outcome = dlg.start()
            if isinstance(outcome, tuple) and outcome[0] == "ok":
                res = outcome[1]
            else:
                err = outcome[1] if isinstance(outcome, tuple) else "OCR did not complete."
                self.status_label.setText(f"OCR failed: {err}")
                set_state(self.status_label, "error")
                dialogs.error(self, "OCR failed", str(err))
                return
        else:
            try:
                res = self.service.run_ocr(
                    self.current.id,
                    language=lang,
                    max_pixels=max_px,
                )
            except _ERRORS as exc:
                self.status_label.setText(f"OCR failed: {exc}")
                set_state(self.status_label, "error")
                dialogs.error(self, "OCR failed", str(exc))
                return
        self._run = res.run
        self._items = res.items
        self._render_run(res.run, res.items)
        self.ocr_changed.emit()

    def export_current(self, fmt: str) -> None:
        if self.current is None or self._run is None:
            return
        ext = "json" if fmt == "json" else "txt"
        name_filter = "JSON file (*.json)" if fmt == "json" else "Text file (*.txt)"
        default_dest = self.service.reports_dir(self.current.case_id) / (
            f"{self.current.id}-ocr.{ext}"
        )
        default_dest.parent.mkdir(parents=True, exist_ok=True)
        chosen = dialogs.choose_save_file(
            self, f"Export OCR ({ext.upper()})", default_dest, name_filter
        )
        if chosen is None:
            return
        try:
            res = self.service.export_ocr(self.current.id, chosen, fmt=fmt)
        except _ERRORS as exc:
            dialogs.error(self, "OCR export failed", str(exc))
            return
        dialogs.info(
            self,
            "OCR exported",
            f"Saved automated OCR result ({res.item_count} item(s)) to:\n{res.path}\n\n"
            f"SHA-256: {res.sha256}",
        )
        self.ocr_changed.emit()
