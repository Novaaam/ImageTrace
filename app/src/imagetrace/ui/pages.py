"""Application pages. Everything shown is real state; no sample or demo data."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from imagetrace import APP_NAME, __version__
from imagetrace.audit import verify_chain
from imagetrace.backup import latest_backup_age_days, list_backups
from imagetrace.cases import Case
from imagetrace.correlation import correlate_case
from imagetrace.evidence import Evidence
from imagetrace.gps import EXTERNAL_MAP_DISCLOSURE
from imagetrace.image_analysis import pillow_version
from imagetrace.metadata import MetadataError, query_exiftool_version
from imagetrace.ocr import (
    TESSERACT_INSTALL_GUIDE,
    OcrError,
    list_tesseract_languages,
    query_tesseract_version,
)
from imagetrace.paths import check_storage, format_bytes
from imagetrace.reports import reportlab_version
from imagetrace.settings import (
    MAX_ANALYST_LABEL,
    MAX_BACKUP_REMINDER_DAYS,
    MAX_IMPORT_MB,
    MAX_LOCK_TIMEOUT_MINUTES,
    MAX_MEGAPIXELS,
    MIN_IMPORT_MB,
    MIN_MEGAPIXELS,
    THEMES,
)
from imagetrace.timefmt import format_local
from imagetrace.tools_status import detect_tools, find_exiftool, find_tesseract
from imagetrace.ui import dialogs
from imagetrace.ui.batch_workers import (
    BatchAnalysisReport,
    BatchAnalysisWorker,
    BatchIntegrityReport,
    BatchIntegrityWorker,
    BatchProgressDialog,
)
from imagetrace.ui.correlation_widget import CaseCorrelationWidget
from imagetrace.ui.widgets import Card, Page, clear_layout, elide, label, set_state

if TYPE_CHECKING:
    from imagetrace.ui.main_window import MainWindow


class DashboardPage(Page):
    title = "Dashboard"
    subtitle = "Overview of this workstation. Nothing shown here is sample or demo data."

    def __init__(
        self,
        data_root: Path,
        recent_cases: Callable[[], list[Case]] | None = None,
        recent_evidence: Callable[[], list[Evidence]] | None = None,
        exiftool_path_getter: Callable[[], str] = lambda: "",
        tesseract_path_getter: Callable[[], str] = lambda: "",
        navigate: Callable[[str], None] | None = None,
        backup_reminder_getter: Callable[[], int] = lambda: 7,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.data_root = data_root
        self.recent_cases = recent_cases
        self.recent_evidence = recent_evidence
        self.exiftool_path_getter = exiftool_path_getter
        self.tesseract_path_getter = tesseract_path_getter
        self.navigate_cb = navigate
        self.backup_reminder_getter = backup_reminder_getter

        if navigate is not None:
            quick_row = QHBoxLayout()
            for btn_text, target in (
                ("Open Cases", "Cases"),
                ("Open Evidence", "Evidence"),
                ("Run Analysis", "Analysis"),
                ("Generate Reports", "Reports"),
            ):
                btn = QPushButton(btn_text)
                btn.clicked.connect(lambda _checked=False, t=target: navigate(t))
                quick_row.addWidget(btn)
            quick_row.addStretch(1)
            self.layout_.addLayout(quick_row)

        grid = QGridLayout()
        grid.setSpacing(12)

        self.cases_card = Card("Recent cases")
        self.cases_empty = label("", muted=True)
        self.cases_card.body.addWidget(self.cases_empty)
        self.cases_box = QVBoxLayout()
        self.cases_box.setSpacing(2)
        self.cases_card.body.addLayout(self.cases_box)
        self.cases_card.body.addStretch(1)

        self.evidence_card = Card("Recent evidence")
        self.evidence_empty = label("", muted=True)
        self.evidence_card.body.addWidget(self.evidence_empty)
        self.evidence_box = QVBoxLayout()
        self.evidence_box.setSpacing(2)
        self.evidence_card.body.addLayout(self.evidence_box)
        self.evidence_card.body.addStretch(1)

        self.storage_card = Card("Storage and backup health")
        self.storage_root = label("")
        self.storage_state = label("")
        self.backup_state = label("", muted=True)
        self.storage_card.body.addWidget(self.storage_root)
        self.storage_card.body.addWidget(self.storage_state)
        self.storage_card.body.addWidget(self.backup_state)
        self.storage_card.body.addStretch(1)

        self.tools_card = Card("Tool status")
        self.tools_card.body.addWidget(
            label(f"{APP_NAME} {__version__} · Pillow {pillow_version()}", muted=True)
        )
        self.tools_box = QVBoxLayout()
        self.tools_card.body.addLayout(self.tools_box)
        self.tools_card.body.addStretch(1)

        grid.addWidget(self.cases_card, 0, 0)
        grid.addWidget(self.evidence_card, 0, 1)
        grid.addWidget(self.storage_card, 1, 0)
        grid.addWidget(self.tools_card, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.layout_.addLayout(grid)
        self.layout_.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        # recent cases
        clear_layout(self.cases_box)
        if self.recent_cases is None:
            self.cases_empty.setText("Case database unavailable.")
            cases: list[Case] = []
        else:
            cases = self.recent_cases()
            self.cases_empty.setText("No cases yet. Create one on the Cases page.")
        self.cases_empty.setVisible(not cases)
        for case in cases:
            self.cases_box.addWidget(label(f"{case.id}  ·  {elide(case.title, 60)}"))
            self.cases_box.addWidget(label(f"Updated {format_local(case.updated_at)}", muted=True))

        # recent evidence
        clear_layout(self.evidence_box)
        if self.recent_evidence is None:
            self.evidence_empty.setText("Evidence database unavailable.")
            items: list[Evidence] = []
        else:
            items = self.recent_evidence()
            self.evidence_empty.setText(
                "No evidence imported yet. Import images on the Evidence page."
            )
        self.evidence_empty.setVisible(not items)
        for ev in items:
            self.evidence_box.addWidget(
                label(f"{ev.id}  ·  {elide(ev.original_filename, 46)} ({ev.detected_format})")
            )
            self.evidence_box.addWidget(
                label(
                    f"Case {ev.case_id} · {format_bytes(ev.file_size)} · "
                    f"Imported {format_local(ev.imported_at)}",
                    muted=True,
                )
            )

        # storage
        st = check_storage(self.data_root)
        self.storage_root.setText(f"Data root: {st.root}")
        if not st.available:
            text, state = st.message, "error"
        elif st.low_space:
            text, state = f"{st.message} (of {format_bytes(st.total_bytes)})", "warn"
        else:
            text = f"OK: {format_bytes(st.free_bytes)} free of {format_bytes(st.total_bytes)}"
            state = "ok"
        self.storage_state.setText(text)
        set_state(self.storage_state, state)

        # backup health
        if not st.available:
            self.backup_state.setText("Backups: storage unavailable.")
            set_state(self.backup_state, "error")
        else:
            bdir = self.data_root / "backups"
            bfiles = list_backups(bdir)
            age = latest_backup_age_days(bdir)
            rem = self.backup_reminder_getter()
            if not bfiles or age is None:
                self.backup_state.setText(
                    "Backups: none yet (create a backup under Settings -> Database)."
                )
                set_state(self.backup_state, "warn" if rem > 0 else None)
            else:
                enc = sum(1 for f in bfiles if f.name.endswith(".itbak"))
                age_s = "today" if age < 1.0 else f"{age:.1f} day(s) ago"
                btxt = f"Backups: {len(bfiles)} ({enc} encrypted) · latest {age_s}."
                if rem > 0 and age >= rem:
                    self.backup_state.setText(f"{btxt} Reminder: older than {rem} day(s).")
                    set_state(self.backup_state, "warn")
                else:
                    self.backup_state.setText(btxt)
                    set_state(self.backup_state, "ok")

        # tools
        clear_layout(self.tools_box)
        for tool in detect_tools(self.exiftool_path_getter(), self.tesseract_path_getter()):
            if tool.found:
                text, state = f"{tool.name}: found at {tool.path}", "ok"
            else:
                text, state = (
                    f"{tool.name}: not installed (needed from {tool.needed_from})",
                    "warn",
                )
            self.tools_box.addWidget(label(text, state=state))


class AnalysisPage(Page):
    title = "Analysis"
    subtitle = "Run offline analysis, case-wide correlation, and integrity verification."

    def __init__(self, window: MainWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.window_ = window

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("SettingsBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(12)

        self.meta_card = Card("Metadata, GPS & Origin & Trace batch extraction")
        self.meta_status = label("")
        self.meta_card.body.addWidget(self.meta_status)
        btn_row = QHBoxLayout()
        self.batch_btn = QPushButton("Extract metadata, GPS && Origin for unanalyzed items")
        self.batch_btn.setObjectName("Primary")
        self.batch_btn.clicked.connect(self._extract_missing)
        btn_row.addWidget(self.batch_btn)
        self.open_ev_btn = QPushButton("Open Evidence")
        self.open_ev_btn.clicked.connect(lambda: window.navigate("Evidence"))
        btn_row.addWidget(self.open_ev_btn)
        btn_row.addStretch(1)
        self.meta_card.body.addLayout(btn_row)
        self.batch_result = label("", muted=True)
        self.meta_card.body.addWidget(self.batch_result)
        lay.addWidget(self.meta_card)

        self.corr_card = Card("Cross-evidence correlation, lineage & case timeline")
        self.correlation_widget = CaseCorrelationWidget()
        self.correlation_widget.correlate_btn.clicked.connect(self.run_case_correlation)
        self.correlation_widget.open_evidence_requested.connect(window.open_evidence_item)
        self.corr_card.body.addWidget(self.correlation_widget)
        lay.addWidget(self.corr_card)

        self.ocr_card = Card("Offline OCR (Tesseract - Phase 6)")
        self.ocr_status = label("")
        self.ocr_card.body.addWidget(self.ocr_status)
        self.ocr_card.body.addWidget(
            label(
                "OCR is intentionally user-triggered per evidence item on Evidence -> OCR so "
                "automated text extraction never runs unprompted.",
                muted=True,
            )
        )
        lay.addWidget(self.ocr_card)

        self.notes_card = Card("Analyst notes, integrity & chain of custody")
        self.notes_status = label("")
        self.notes_card.body.addWidget(self.notes_status)
        chain_row = QHBoxLayout()
        self.verify_all_btn = QPushButton("Verify all evidence integrity")
        self.verify_all_btn.clicked.connect(self._verify_all_evidence)
        chain_row.addWidget(self.verify_all_btn)
        self.verify_chain_btn = QPushButton("Verify audit chain")
        self.verify_chain_btn.clicked.connect(self._verify_chain)
        chain_row.addWidget(self.verify_chain_btn)
        chain_row.addStretch(1)
        self.notes_card.body.addLayout(chain_row)
        self.chain_result = label("", muted=True)
        self.notes_card.body.addWidget(self.chain_result)
        lay.addWidget(self.notes_card)
        lay.addStretch(1)

        scroll.setWidget(body)
        self.layout_.addWidget(scroll, 1)
        self.refresh()

    def refresh(self) -> None:
        w = self.window_
        cid = w.settings.active_case_id
        if w.evidence is None or w.metadata is None:
            self.meta_status.setText("Database unavailable.")
            set_state(self.meta_status, "error")
            self.ocr_status.setText("Database unavailable.")
            set_state(self.ocr_status, "error")
            self.notes_status.setText("Database unavailable.")
            set_state(self.notes_status, "error")
            self.batch_btn.setEnabled(False)
            self.verify_all_btn.setEnabled(False)
            self.verify_chain_btn.setEnabled(False)
            self.correlation_widget.correlate_btn.setEnabled(False)
            self.correlation_widget.clear_view("Database unavailable.")
            return
        self.verify_chain_btn.setEnabled(True)
        if not cid:
            self.meta_status.setText("No active case selected. Choose an active case first.")
            set_state(self.meta_status, None)
            self.ocr_status.setText("No active case selected.")
            set_state(self.ocr_status, None)
            self.notes_status.setText("No active case selected.")
            set_state(self.notes_status, None)
            self.batch_btn.setEnabled(False)
            self.verify_all_btn.setEnabled(False)
            self.correlation_widget.correlate_btn.setEnabled(False)
            self.correlation_widget.clear_view("No active case selected.")
            return
        self.correlation_widget.correlate_btn.setEnabled(True)
        items = w.evidence.list_for_case(cid)
        done = w.metadata.extracted_evidence_ids(cid)
        ocr_done = w.ocr.completed_evidence_ids(cid) if w.ocr is not None else set()
        note_counts = w.notes.counts_by_confidence(cid) if w.notes is not None else {}
        total_notes = sum(note_counts.values())
        pending = [e for e in items if e.id not in done]
        self.verify_all_btn.setEnabled(bool(items))
        if not items:
            self.meta_status.setText("No evidence items in the active case yet.")
            set_state(self.meta_status, None)
            self.ocr_status.setText("No evidence items in the active case yet.")
            set_state(self.ocr_status, None)
            self.batch_btn.setEnabled(False)
        else:
            self.meta_status.setText(
                f"Active case {cid}: {len(done)} of {len(items)} evidence item(s) have extracted "
                f"metadata ({len(pending)} pending)."
            )
            set_state(self.meta_status, "ok" if not pending else None)
            self.ocr_status.setText(
                f"Active case {cid}: {len(ocr_done)} of {len(items)} evidence item(s) have an "
                "automated OCR run."
            )
            set_state(self.ocr_status, None)
            self.batch_btn.setEnabled(bool(pending))
        breakdown = ", ".join(f"{k}: {v}" for k, v in note_counts.items())
        self.notes_status.setText(
            f"Active case {cid}: {total_notes} active analyst note(s)"
            + (f" ({breakdown})" if breakdown else "")
        )
        set_state(self.notes_status, None)
        if items:
            self.run_case_correlation()
        else:
            self.correlation_widget.clear_view("No evidence items in the active case yet.")

    def run_case_correlation(self) -> None:
        w = self.window_
        cid = w.settings.active_case_id
        if (
            not cid
            or w.evidence is None
            or w.metadata is None
            or w.gps is None
            or w.provenance is None
        ):
            self.correlation_widget.clear_view("Select an active case to correlate evidence.")
            return
        rep = correlate_case(w.evidence, w.metadata, w.gps, w.provenance, cid)
        self.correlation_widget.render_report(rep)

    def _verify_chain(self) -> None:
        if self.window_.db is None:
            return
        rep = verify_chain(self.window_.db.con)
        self.chain_result.setText(rep.message)
        set_state(self.chain_result, "ok" if rep.ok else "error")

    def _verify_all_evidence(self) -> None:
        w = self.window_
        cid = w.settings.active_case_id
        if not cid or w.db is None or w.evidence is None:
            return
        items = w.evidence.list_for_case(cid)
        if not items:
            return
        worker = BatchIntegrityWorker(
            w.db.path,
            w.data_root,
            [e.id for e in items],
            actor_label=w.actor_label(),
            parent=self,
        )
        dlg = BatchProgressDialog(
            worker,
            "Verifying evidence integrity",
            f"Verifying SHA-256 for {len(items)} evidence item(s)…",
            parent=self,
        )
        report = dlg.start()
        if not isinstance(report, BatchIntegrityReport):
            return
        if report.mismatched:
            summary = (
                f"Integrity mismatch on {len(report.mismatched)} of {len(items)} item(s) "
                f"({len(report.verified_ok)} OK): "
                + "; ".join(f"{eid}: {msg}" for eid, msg in report.mismatched[:3])
            )
            self.chain_result.setText(summary)
            set_state(self.chain_result, "error")
            dialogs.error(self, "Evidence integrity mismatch", summary)
        elif report.cancelled:
            self.chain_result.setText(
                f"Cancelled after verifying {len(report.verified_ok)} of {len(items)} item(s) OK."
            )
            set_state(self.chain_result, "warn")
        else:
            self.chain_result.setText(
                f"Verified all {len(report.verified_ok)} evidence original(s): SHA-256 matched."
            )
            set_state(self.chain_result, "ok")
        w.evidence_page.reload()

    def _extract_missing(self) -> None:
        w = self.window_
        cid = w.settings.active_case_id
        if not cid or w.db is None or w.evidence is None or w.metadata is None:
            return
        items = w.evidence.list_for_case(cid)
        done = w.metadata.extracted_evidence_ids(cid)
        pending = [e for e in items if e.id not in done]
        if not pending:
            return
        worker = BatchAnalysisWorker(
            w.db.path,
            w.data_root,
            [e.id for e in pending],
            actor_label=w.actor_label(),
            exiftool_path=w.settings.exiftool_path,
            parent=self,
        )
        dlg = BatchProgressDialog(
            worker,
            "Extracting metadata and checking GPS",
            f"Starting analysis for {len(pending)} evidence item(s)…",
            parent=self,
        )
        report = dlg.start()
        if not isinstance(report, BatchAnalysisReport):
            return
        if report.failed:
            msg = (
                f"Extracted {len(report.succeeded)} item(s); {len(report.failed)} failed: "
                + "; ".join(f"{eid}: {err}" for eid, err in report.failed[:3])
            )
            self.batch_result.setText(msg)
            set_state(self.batch_result, "error")
            dialogs.error(self, "Metadata extraction errors", msg)
        elif report.cancelled:
            self.batch_result.setText(
                f"Cancelled after extracting {len(report.succeeded)} of {len(pending)} item(s)."
            )
            set_state(self.batch_result, "warn")
        else:
            self.batch_result.setText(
                f"Extracted metadata for {len(report.succeeded)} evidence item(s)."
            )
            set_state(self.batch_result, "ok")
        self.refresh()
        w.evidence_page.refresh()
        w.reports_page.refresh()


class SettingsPage(Page):
    title = "Settings"
    subtitle = "More settings will be added as features arrive."

    def __init__(self, window: MainWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.window_ = window

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("SettingsBody")
        cards_layout = QVBoxLayout(body)
        cards_layout.setContentsMargins(0, 0, 4, 0)
        cards_layout.setSpacing(12)

        # appearance and identity
        appearance = Card("Appearance and analyst")
        form = QFormLayout()
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName("Theme")
        for t in THEMES:
            self.theme_combo.addItem(t.capitalize(), t)
        self.theme_combo.setCurrentIndex(THEMES.index(window.settings.theme))
        self.theme_combo.currentIndexChanged.connect(
            lambda i: window.set_theme(self.theme_combo.itemData(i))
        )
        theme_label = label("&Theme:", wrap=False)
        theme_label.setBuddy(self.theme_combo)
        form.addRow(theme_label, self.theme_combo)

        analyst_row = QHBoxLayout()
        self.analyst_edit = QLineEdit(window.settings.analyst_label)
        self.analyst_edit.setMaxLength(MAX_ANALYST_LABEL)
        self.analyst_edit.setAccessibleName("Analyst label")
        self.analyst_save = QPushButton("Save")
        self.analyst_save.clicked.connect(self._save_analyst)
        self.analyst_edit.returnPressed.connect(self._save_analyst)
        analyst_row.addWidget(self.analyst_edit, 1)
        analyst_row.addWidget(self.analyst_save)
        analyst_label = label("&Analyst label:", wrap=False)
        analyst_label.setBuddy(self.analyst_edit)
        form.addRow(analyst_label, analyst_row)
        appearance.body.addLayout(form)
        appearance.body.addWidget(
            label(
                "Recorded on history events. It can be a role or initials; a full name isn't "
                "required.",
                muted=True,
            )
        )

        # import safety limits
        limits = Card("Evidence import limits")
        limits_form = QFormLayout()
        self.max_mb_spin = QSpinBox()
        self.max_mb_spin.setRange(MIN_IMPORT_MB, MAX_IMPORT_MB)
        self.max_mb_spin.setSuffix(" MB")
        self.max_mb_spin.setFixedWidth(130)
        self.max_mb_spin.setValue(window.settings.max_import_mb)
        self.max_mb_spin.setAccessibleName("Maximum import file size in MB")
        mb_lbl = label("Max &file size:", wrap=False)
        mb_lbl.setBuddy(self.max_mb_spin)
        limits_form.addRow(mb_lbl, self.max_mb_spin)

        self.max_mp_spin = QSpinBox()
        self.max_mp_spin.setRange(MIN_MEGAPIXELS, MAX_MEGAPIXELS)
        self.max_mp_spin.setSuffix(" MP")
        self.max_mp_spin.setFixedWidth(130)
        self.max_mp_spin.setValue(window.settings.max_image_megapixels)
        self.max_mp_spin.setAccessibleName("Maximum image megapixels")
        mp_lbl = label("Max &image megapixels:", wrap=False)
        mp_lbl.setBuddy(self.max_mp_spin)
        limits_form.addRow(mp_lbl, self.max_mp_spin)
        limits.body.addLayout(limits_form)

        lim_row = QHBoxLayout()
        self.limits_save = QPushButton("Save limits")
        self.limits_save.clicked.connect(self._save_limits)
        lim_row.addWidget(self.limits_save)
        lim_row.addStretch(1)
        limits.body.addLayout(lim_row)
        limits.body.addWidget(
            label(
                "Conservative defaults protect against oversized files and decompression bombs. "
                "Archives and non-image files are always rejected without unpacking.",
                muted=True,
            )
        )

        # external tools
        tools = Card("External tools")
        et_form = QFormLayout()
        et_row = QHBoxLayout()
        self.exiftool_edit = QLineEdit(window.settings.exiftool_path)
        self.exiftool_edit.setPlaceholderText("Auto-detect (tools\\third-party\\exiftool or PATH)")
        self.exiftool_edit.setAccessibleName("ExifTool executable path")
        self.exiftool_browse = QPushButton("Browse…")
        self.exiftool_browse.clicked.connect(self._browse_exiftool)
        self.exiftool_save = QPushButton("Save")
        self.exiftool_save.clicked.connect(self._save_exiftool)
        self.exiftool_test = QPushButton("Test")
        self.exiftool_test.clicked.connect(self._test_exiftool)
        et_row.addWidget(self.exiftool_edit, 1)
        et_row.addWidget(self.exiftool_browse)
        et_row.addWidget(self.exiftool_save)
        et_row.addWidget(self.exiftool_test)
        et_lbl = label("&ExifTool path:", wrap=False)
        et_lbl.setBuddy(self.exiftool_edit)
        et_form.addRow(et_lbl, et_row)
        tools.body.addLayout(et_form)
        self.exiftool_status = label("", muted=True)
        tools.body.addWidget(self.exiftool_status)

        # Tesseract OCR (optional)
        tess_form = QFormLayout()
        t_row = QHBoxLayout()
        self.tess_edit = QLineEdit(window.settings.tesseract_path)
        self.tess_edit.setPlaceholderText("Auto-detect (tools\\third-party\\tesseract or PATH)")
        self.tess_edit.setAccessibleName("Tesseract executable path")
        self.tess_browse = QPushButton("Browse…")
        self.tess_browse.clicked.connect(self._browse_tesseract)
        self.tess_save = QPushButton("Save")
        self.tess_save.clicked.connect(self._save_tesseract)
        self.tess_test = QPushButton("Test")
        self.tess_test.clicked.connect(self._test_tesseract)
        self.tess_guide = QPushButton("Guide…")
        self.tess_guide.clicked.connect(
            lambda: dialogs.info(self, "Offline Tesseract OCR setup", TESSERACT_INSTALL_GUIDE)
        )
        t_row.addWidget(self.tess_edit, 1)
        for btn in (self.tess_browse, self.tess_save, self.tess_test, self.tess_guide):
            t_row.addWidget(btn)
        t_lbl = label("Tesseract &OCR:", wrap=False)
        t_lbl.setBuddy(self.tess_edit)
        tess_form.addRow(t_lbl, t_row)

        td_row = QHBoxLayout()
        self.tessdata_edit = QLineEdit(window.settings.tessdata_dir)
        self.tessdata_edit.setPlaceholderText("Optional custom tessdata folder (*.traineddata)")
        self.tessdata_edit.setAccessibleName("Tessdata directory")
        self.tessdata_browse = QPushButton("Browse…")
        self.tessdata_browse.clicked.connect(self._browse_tessdata)
        self.tessdata_save = QPushButton("Save")
        self.tessdata_save.clicked.connect(self._save_tesseract)
        td_row.addWidget(self.tessdata_edit, 1)
        td_row.addWidget(self.tessdata_browse)
        td_row.addWidget(self.tessdata_save)
        td_lbl = label("Tess&data folder:", wrap=False)
        td_lbl.setBuddy(self.tessdata_edit)
        tess_form.addRow(td_lbl, td_row)
        tools.body.addLayout(tess_form)
        self.tess_status = label("", muted=True)
        tools.body.addWidget(self.tess_status)

        # storage
        storage = Card("Storage")
        storage.body.addWidget(label(f"Data root: {window.data_root}"))
        storage.body.addWidget(
            label(
                "ImageTrace never falls back to another drive on its own. If the data folder is "
                "missing at start-up, you're asked to choose one.",
                muted=True,
            )
        )

        # database
        database = Card("Database and backups")
        self.db_info = label("")
        database.body.addWidget(self.db_info)
        self.backup_age_label = label("", muted=True)
        database.body.addWidget(self.backup_age_label)

        btn_row1 = QHBoxLayout()
        self.backup_btn = QPushButton("Back up now")
        self.enc_backup_btn = QPushButton("Encrypted backup…")
        self.verify_btn = QPushButton("Verify integrity")
        self.backup_btn.clicked.connect(lambda: window.backup_now())
        self.enc_backup_btn.clicked.connect(window.backup_encrypted_now)
        self.verify_btn.clicked.connect(window.verify_database)
        for b in (self.backup_btn, self.enc_backup_btn, self.verify_btn):
            btn_row1.addWidget(b)
        btn_row1.addStretch(1)
        database.body.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        self.restore_btn = QPushButton("Restore from backup…")
        self.recovery_guide_btn = QPushButton("Recovery guide…")
        self.restore_btn.clicked.connect(lambda: window.restore_from_backup())
        self.recovery_guide_btn.clicked.connect(self._show_recovery_guide)
        for b in (self.restore_btn, self.recovery_guide_btn):
            btn_row2.addWidget(b)
        btn_row2.addStretch(1)
        database.body.addLayout(btn_row2)

        rem_row = QHBoxLayout()
        self.backup_rem_spin = QSpinBox()
        self.backup_rem_spin.setRange(0, MAX_BACKUP_REMINDER_DAYS)
        self.backup_rem_spin.setSpecialValueText("Off")
        self.backup_rem_spin.setSuffix(" days")
        self.backup_rem_spin.setFixedWidth(130)
        self.backup_rem_spin.setAccessibleName("Backup reminder interval")
        self.backup_rem_save = QPushButton("Save")
        self.backup_rem_save.clicked.connect(self._save_backup_reminder)
        rem_lbl = label("Backup &reminder interval:", wrap=False)
        rem_lbl.setBuddy(self.backup_rem_spin)
        rem_row.addWidget(rem_lbl)
        rem_row.addWidget(self.backup_rem_spin)
        rem_row.addWidget(self.backup_rem_save)
        rem_row.addStretch(1)
        database.body.addLayout(rem_row)

        self.db_result = label("", muted=True)
        database.body.addWidget(self.db_result)
        database.body.addWidget(
            label(
                "Backups are saved in data\\backups\\ with a .sha256 sidecar and are never "
                "deleted automatically. Choose Encrypted backup (.sqlite.itbak) for AES-256-GCM "
                "protection; protect plain backups and evidence files with Windows account "
                "security and BitLocker.",
                muted=True,
            )
        )

        # privacy & session lock
        privacy = Card("Privacy, session lock and network")
        self.network_status = label("")
        privacy.body.addWidget(self.network_status)
        self.ext_map_checkbox = QCheckBox(
            "Allow optional on-demand OpenStreetMap tile preview (GPS tab)"
        )
        self.ext_map_checkbox.setChecked(window.settings.allow_external_map)
        self.ext_map_checkbox.clicked.connect(self._toggle_external_map)
        privacy.body.addWidget(self.ext_map_checkbox)
        privacy.body.addWidget(
            label(
                "ImageTrace has no telemetry, never uploads evidence, and never automatically "
                "reverse-geocodes addresses. Even when opted in, external map tiles are only "
                "fetched when you explicitly click Load on the GPS tab.",
                muted=True,
            )
        )

        lock_form = QFormLayout()
        lock_t_row = QHBoxLayout()
        self.lock_timeout_spin = QSpinBox()
        self.lock_timeout_spin.setRange(0, MAX_LOCK_TIMEOUT_MINUTES)
        self.lock_timeout_spin.setSpecialValueText("Manual only")
        self.lock_timeout_spin.setSuffix(" min")
        self.lock_timeout_spin.setFixedWidth(130)
        self.lock_timeout_spin.setAccessibleName("Idle lock timeout")
        self.lock_timeout_save = QPushButton("Save")
        self.lock_timeout_save.clicked.connect(self._save_lock_timeout)
        lock_t_row.addWidget(self.lock_timeout_spin)
        lock_t_row.addWidget(self.lock_timeout_save)
        lock_t_row.addStretch(1)
        lt_lbl = label("Idle &lock timeout:", wrap=False)
        lt_lbl.setBuddy(self.lock_timeout_spin)
        lock_form.addRow(lt_lbl, lock_t_row)
        privacy.body.addLayout(lock_form)

        lock_btns = QHBoxLayout()
        self.set_pw_btn = QPushButton("Set lock passphrase…")
        self.clear_pw_btn = QPushButton("Clear passphrase")
        self.lock_now_btn = QPushButton("Lock now (Ctrl+L)")
        self.set_pw_btn.clicked.connect(self._set_lock_passphrase)
        self.clear_pw_btn.clicked.connect(self._clear_lock_passphrase)
        self.lock_now_btn.clicked.connect(window.lock_session)
        for b in (self.set_pw_btn, self.clear_pw_btn, self.lock_now_btn):
            lock_btns.addWidget(b)
        lock_btns.addStretch(1)
        privacy.body.addLayout(lock_btns)
        self.lock_status = label("", muted=True)
        privacy.body.addWidget(self.lock_status)

        for c in (appearance, limits, tools, storage, database, privacy):
            cards_layout.addWidget(c)
        cards_layout.addStretch(1)
        scroll.setWidget(body)
        self.layout_.addWidget(scroll, 1)
        self.refresh()

    def _save_analyst(self) -> None:
        saved = self.window_.set_analyst_label(self.analyst_edit.text())
        self.analyst_edit.setText(saved)

    def _save_limits(self) -> None:
        mb, mp = self.window_.set_import_limits(self.max_mb_spin.value(), self.max_mp_spin.value())
        self.max_mb_spin.setValue(mb)
        self.max_mp_spin.setValue(mp)

    def _browse_exiftool(self) -> None:
        picked = dialogs.choose_open_file(
            self, "Select exiftool.exe", None, "Executable (*.exe);;All files (*)"
        )
        if picked is not None:
            self.exiftool_edit.setText(str(picked))
            self._save_exiftool()

    def _save_exiftool(self) -> None:
        saved = self.window_.set_exiftool_path(self.exiftool_edit.text())
        self.exiftool_edit.setText(saved)
        self._update_exiftool_status()

    def _test_exiftool(self) -> None:
        exe = find_exiftool(self.exiftool_edit.text().strip())
        if exe is None:
            self.exiftool_status.setText("ExifTool not found at that location.")
            set_state(self.exiftool_status, "error")
            return
        try:
            ver = query_exiftool_version(exe)
        except MetadataError as exc:
            self.exiftool_status.setText(str(exc))
            set_state(self.exiftool_status, "error")
            return
        self.exiftool_status.setText(f"OK: ExifTool {ver} at {exe}")
        set_state(self.exiftool_status, "ok")

    def _update_exiftool_status(self) -> None:
        exe = find_exiftool(self.window_.settings.exiftool_path)
        if exe is not None:
            self.exiftool_status.setText(f"Active ExifTool executable: {exe}")
            set_state(self.exiftool_status, "ok")
        else:
            self.exiftool_status.setText(
                "ExifTool not found. Leave blank to auto-detect or browse."
            )
            set_state(self.exiftool_status, "warn")

    def _browse_tesseract(self) -> None:
        picked = dialogs.choose_open_file(
            self, "Select tesseract.exe", None, "Executable (*.exe);;All files (*)"
        )
        if picked is not None:
            self.tess_edit.setText(str(picked))
            self._save_tesseract()

    def _browse_tessdata(self) -> None:
        picked = dialogs.choose_directory(self, "Select tessdata folder", None)
        if picked is not None:
            self.tessdata_edit.setText(str(picked))
            self._save_tesseract()

    def _save_tesseract(self) -> None:
        exe, td = self.window_.set_tesseract_settings(
            self.tess_edit.text(), self.tessdata_edit.text()
        )
        self.tess_edit.setText(exe)
        self.tessdata_edit.setText(td)
        self._update_tesseract_status()

    def _test_tesseract(self) -> None:
        exe = find_tesseract(self.tess_edit.text().strip())
        if exe is None:
            self.tess_status.setText("Tesseract not found at that location (optional).")
            set_state(self.tess_status, "warn")
            return
        try:
            ver = query_tesseract_version(exe)
        except OcrError as exc:
            self.tess_status.setText(str(exc))
            set_state(self.tess_status, "error")
            return
        langs = list_tesseract_languages(exe, self.tessdata_edit.text().strip())
        lang_note = f"; languages: {', '.join(langs[:10])}" if langs else ""
        self.tess_status.setText(f"OK: Tesseract {ver} at {exe}{lang_note}")
        set_state(self.tess_status, "ok")

    def _update_tesseract_status(self) -> None:
        exe = find_tesseract(self.window_.settings.tesseract_path)
        if exe is not None:
            self.tess_status.setText(f"Active Tesseract executable: {exe}")
            set_state(self.tess_status, "ok")
        else:
            self.tess_status.setText(
                "Tesseract OCR not installed (optional; install on D: per Guide - never "
                "downloaded silently)."
            )
            set_state(self.tess_status, "warn")

    def _toggle_external_map(self, checked: bool) -> None:
        if checked and not dialogs.confirm(
            self, "External map service disclosure", EXTERNAL_MAP_DISCLOSURE
        ):
            self.ext_map_checkbox.setChecked(False)
            return
        self.window_.set_allow_external_map(checked)
        self._update_network_status()

    def _update_network_status(self) -> None:
        if self.window_.settings.allow_external_map:
            self.network_status.setText(
                "External map preview: opt-in enabled (on-demand only; disclosure still shown)"
            )
            set_state(self.network_status, "warn")
        else:
            self.network_status.setText("Network features: disabled (strictly offline)")
            set_state(self.network_status, "ok")

    def _show_recovery_guide(self) -> None:
        dialogs.info(
            self,
            "Backup & Recovery Guide",
            "Database backups are stored in data\\backups\\ with a .sha256 sidecar.\n\n"
            "• Plain backups (*.sqlite): readable SQLite copies — protect with Windows account "
            "security and BitLocker.\n"
            "• Encrypted backups (*.sqlite.itbak): AES-256-GCM with scrypt KDF. ImageTrace never "
            "stores your passphrase; if lost, the backup cannot be recovered.\n"
            "• Restoring always creates a pre-restore backup first and never deletes existing "
            "backups automatically.\n\n"
            "Full recovery steps and standalone decryption instructions are in:\n"
            "docs\\user-guide\\backup-recovery.md",
        )

    def _save_backup_reminder(self) -> None:
        days = self.window_.set_backup_reminder_days(self.backup_rem_spin.value())
        self.backup_rem_spin.setValue(days)
        self._update_backup_age()

    def _save_lock_timeout(self) -> None:
        mins = self.window_.set_lock_timeout(self.lock_timeout_spin.value())
        self.lock_timeout_spin.setValue(mins)
        self._update_lock_status()

    def _set_lock_passphrase(self) -> None:
        pw = dialogs.ask_new_passphrase(
            self,
            "Set session lock passphrase",
            "Set a passphrase required to unlock ImageTrace after locking (Ctrl+L or idle "
            "timeout). Stored only as a salted scrypt hash — never plaintext.",
        )
        if pw is None:
            return
        self.window_.set_lock_passphrase(pw)
        self._update_lock_status()

    def _clear_lock_passphrase(self) -> None:
        if not self.window_.settings.lock_passphrase_hash:
            return
        if not dialogs.confirm(
            self,
            "Clear session lock passphrase",
            "Remove the unlock passphrase? Locking (Ctrl+L) will still hide case data behind "
            "a privacy screen, but anyone at this workstation can click Unlock.",
        ):
            return
        self.window_.set_lock_passphrase(None)
        self._update_lock_status()

    def _update_lock_status(self) -> None:
        s = self.window_.settings
        has_pw = bool(s.lock_passphrase_hash)
        self.clear_pw_btn.setEnabled(has_pw)
        pw_txt = "Passphrase enabled (scrypt hash)" if has_pw else "No passphrase (privacy screen)"
        t_txt = (
            f"auto-lock after {s.lock_timeout_minutes} min idle"
            if s.lock_timeout_minutes > 0
            else "manual lock only (Ctrl+L)"
        )
        self.lock_status.setText(f"Session lock: {pw_txt}; {t_txt}.")

    def _update_backup_age(self) -> None:
        bdir = self.window_.backups_dir
        files = list_backups(bdir)
        age = latest_backup_age_days(bdir)
        rem = self.window_.settings.backup_reminder_days
        if not files or age is None:
            if rem > 0:
                self.backup_age_label.setText(
                    "Backups: none yet (create a plain or encrypted backup regularly)."
                )
                set_state(self.backup_age_label, "warn")
            else:
                self.backup_age_label.setText("Backups: none yet.")
                set_state(self.backup_age_label, None)
            return
        enc_count = sum(1 for f in files if f.name.endswith(".itbak"))
        age_str = "today" if age < 1.0 else f"{age:.1f} day(s) ago"
        txt = f"Backups on disk: {len(files)} ({enc_count} encrypted) · latest backup {age_str}."
        if rem > 0 and age >= rem:
            self.backup_age_label.setText(f"{txt} Reminder: older than {rem} day(s).")
            set_state(self.backup_age_label, "warn")
        else:
            self.backup_age_label.setText(txt)
            set_state(self.backup_age_label, "ok")

    def show_result(self, text: str, state: str | None = None) -> None:
        self.db_result.setText(text)
        set_state(self.db_result, state)

    def refresh(self) -> None:
        w = self.window_
        enabled = w.db is not None
        for b in (self.backup_btn, self.enc_backup_btn, self.verify_btn, self.restore_btn):
            b.setEnabled(enabled)
        self.max_mb_spin.setValue(w.settings.max_import_mb)
        self.max_mp_spin.setValue(w.settings.max_image_megapixels)
        self.exiftool_edit.setText(w.settings.exiftool_path)
        self._update_exiftool_status()
        self.tess_edit.setText(w.settings.tesseract_path)
        self.tessdata_edit.setText(w.settings.tessdata_dir)
        self._update_tesseract_status()
        self.ext_map_checkbox.setChecked(w.settings.allow_external_map)
        self._update_network_status()
        self.backup_rem_spin.setValue(w.settings.backup_reminder_days)
        self._update_backup_age()
        self.lock_timeout_spin.setValue(w.settings.lock_timeout_minutes)
        self._update_lock_status()
        if w.db is not None:
            self.db_info.setText(f"File: {w.db.path}\nSchema version: {w.db.schema_version}")
            set_state(self.db_info, None)
        else:
            self.db_info.setText(w.db_error or "No database (storage unavailable).")
            set_state(self.db_info, "error")


class AboutPage(Page):
    title = "About"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        info = Card(f"{APP_NAME} {__version__}")
        info.body.addWidget(
            label(
                "Local-first desktop application for examining digital image evidence: "
                "metadata, embedded GPS (only when present), OCR, integrity hashes, analyst "
                "notes and reports."
            )
        )
        scope = Card("Scope and limits")
        scope.body.addWidget(
            label(
                "ImageTrace is not a person-tracking or deanonymization tool. It does not get IP "
                "addresses from images, identify private individuals, do facial recognition, infer "
                "home addresses or track anyone. Reports keep extracted facts, automated results, "
                "analyst observations and hypotheses separate."
            )
        )
        lic = Card("Third-party software")
        lic.body.addWidget(
            label(
                f"Built with Qt for Python (PySide6, LGPL-3.0), Pillow {pillow_version()} "
                f"(MIT-CMU), ReportLab {reportlab_version()} (BSD) and cryptography "
                "(Apache-2.0 / BSD-3). Uses ExifTool by Phil Harvey (Perl Artistic License / "
                "GPL v1+) and optional offline Tesseract OCR (Apache-2.0).",
                muted=True,
            )
        )
        for c in (info, scope, lic):
            self.layout_.addWidget(c)
        self.layout_.addStretch(1)
