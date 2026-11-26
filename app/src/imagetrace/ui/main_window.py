"""Main window: top bar, sidebar navigation, page stack, status bar; owns the database."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from imagetrace import APP_NAME, __version__
from imagetrace.audit import append_event, verify_chain
from imagetrace.backup import (
    BackupError,
    BackupResult,
    backup_database,
    is_encrypted_backup,
    restore_database,
    verify_backup,
)
from imagetrace.cases import CaseRepository
from imagetrace.correlation import SearchHit, search_workspace
from imagetrace.db import Database, DatabaseError, open_database
from imagetrace.evidence import EvidenceRepository
from imagetrace.gps import GpsService
from imagetrace.metadata import MetadataService
from imagetrace.notes import NotesRepository
from imagetrace.ocr import OcrService
from imagetrace.paths import check_storage, format_bytes, resources_dir
from imagetrace.provenance import ProvenanceService
from imagetrace.reports import ReportService
from imagetrace.security import hash_passphrase, verify_passphrase
from imagetrace.settings import SettingsStore, import_legacy_settings
from imagetrace.ui import dialogs, pages
from imagetrace.ui.cases_page import CasesPage
from imagetrace.ui.evidence_page import EvidencePage
from imagetrace.ui.lock_ui import LockOverlay
from imagetrace.ui.reports_page import ReportsPage
from imagetrace.ui.search_dialog import GlobalSearchDialog
from imagetrace.ui.theme import apply_theme
from imagetrace.ui.widgets import elide

log = logging.getLogger(__name__)

NAV_ITEMS = ("Dashboard", "Cases", "Evidence", "Analysis", "Reports", "Settings", "About")


class MainWindow(QMainWindow):
    def __init__(self, data_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.data_root = data_root
        self.storage = check_storage(data_root)
        self.db: Database | None = None
        self.db_error: str | None = None
        if self.storage.available:
            try:
                self.db = open_database(data_root)
            except (DatabaseError, sqlite3.Error, OSError) as exc:
                self.db_error = f"Database unavailable: {exc}"
                log.error("%s", self.db_error)
        self.settings_store = SettingsStore(self.db)
        if self.db is not None:
            import_legacy_settings(self.settings_store, data_root)
        self.settings = self.settings_store.load()
        self.cases: CaseRepository | None = (
            CaseRepository(self.db, data_root / "cases", actor=self.actor_label)
            if self.db is not None
            else None
        )
        self.evidence: EvidenceRepository | None = (
            EvidenceRepository(self.db, data_root, actor=self.actor_label)
            if self.db is not None
            else None
        )
        self.metadata: MetadataService | None = (
            MetadataService(
                self.db,
                self.evidence,
                actor=self.actor_label,
                exiftool_path_getter=lambda: self.settings.exiftool_path,
            )
            if self.db is not None and self.evidence is not None
            else None
        )
        self.gps: GpsService | None = (
            GpsService(self.db, self.evidence, self.metadata, actor=self.actor_label)
            if self.db is not None and self.evidence is not None and self.metadata is not None
            else None
        )
        self.ocr: OcrService | None = (
            OcrService(
                self.db,
                self.evidence,
                actor=self.actor_label,
                tesseract_path_getter=lambda: self.settings.tesseract_path,
                tessdata_dir_getter=lambda: self.settings.tessdata_dir,
            )
            if self.db is not None and self.evidence is not None
            else None
        )
        self.notes: NotesRepository | None = (
            NotesRepository(self.db, actor=self.actor_label) if self.db is not None else None
        )
        self.provenance: ProvenanceService | None = (
            ProvenanceService(self.db, self.evidence, self.metadata, actor=self.actor_label)
            if self.db is not None and self.evidence is not None and self.metadata is not None
            else None
        )
        self.reports: ReportService | None = (
            ReportService(
                self.db,
                data_root,
                self.cases,
                self.evidence,
                self.metadata,
                self.gps,
                self.ocr,
                self.notes,
                provenance_service=self.provenance,
                actor=self.actor_label,
            )
            if (
                self.db is not None
                and self.cases is not None
                and self.evidence is not None
                and self.metadata is not None
                and self.gps is not None
                and self.ocr is not None
                and self.notes is not None
            )
            else None
        )
        self._validate_active_case()

        self.setWindowTitle(f"{APP_NAME} {__version__} — 9va.net")
        for icon_name in ("imagetrace.ico", "imagetrace.png"):
            icon_file = resources_dir() / icon_name
            if icon_file.is_file():
                icon = QIcon(str(icon_file))
                if not icon.isNull():
                    self.setWindowIcon(icon)
                    break
        self.resize(1200, 760)
        self.setMinimumSize(900, 560)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_top_bar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setAccessibleName("Navigation")
        self.sidebar.setFixedWidth(190)
        self.sidebar.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.stack = QStackedWidget()

        self.cases_page = CasesPage(self.cases, lambda: self.settings.active_case_id)
        self.cases_page.active_case_changed.connect(self.set_active_case)
        self.cases_page.cases_changed.connect(self._on_cases_changed)

        self.evidence_page = EvidencePage(
            self.evidence,
            self.cases,
            self.metadata,
            self.gps,
            self.ocr,
            self.notes,
            self.provenance,
            lambda: self.settings.active_case_id,
            lambda: self.settings,
        )
        self.evidence_page.evidence_changed.connect(self._on_evidence_changed)
        self.evidence_page.open_cases_requested.connect(lambda: self.navigate("Cases"))

        self.reports_page = ReportsPage(
            self.reports,
            self.cases,
            self.evidence,
            lambda: self.settings.active_case_id,
        )
        self.reports_page.exports_changed.connect(self._on_exports_changed)

        self.pages: dict[str, pages.Page] = {
            "Dashboard": pages.DashboardPage(
                data_root,
                self.cases.recent if self.cases is not None else None,
                self.evidence.recent if self.evidence is not None else None,
                lambda: self.settings.exiftool_path,
                lambda: self.settings.tesseract_path,
                navigate=self.navigate,
                backup_reminder_getter=lambda: self.settings.backup_reminder_days,
            ),
            "Cases": self.cases_page,
            "Evidence": self.evidence_page,
            "Analysis": pages.AnalysisPage(self),
            "Reports": self.reports_page,
            "Settings": pages.SettingsPage(self),
            "About": pages.AboutPage(),
        }
        for i, name in enumerate(NAV_ITEMS):
            item = QListWidgetItem(name)
            item.setToolTip(f"{name} (Ctrl+{i + 1})")
            self.sidebar.addItem(item)
            self.stack.addWidget(self.pages[name])
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, lambda i=i: self.navigate_index(i))

        self.sidebar.currentRowChanged.connect(self._on_nav)
        body.addWidget(self.sidebar)
        body.addWidget(self.stack, 1)

        workspace = QWidget()
        workspace.setLayout(body)
        self.lock_overlay = LockOverlay()
        self.lock_overlay.unlock_requested.connect(self.unlock_session)
        self.root_stack = QStackedWidget()
        self.root_stack.addWidget(workspace)
        self.root_stack.addWidget(self.lock_overlay)
        outer.addWidget(self.root_stack, 1)
        self.setCentralWidget(central)

        self._locked = False
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.timeout.connect(self.lock_session)
        QShortcut(QKeySequence("Ctrl+L"), self, self.lock_session)
        QShortcut(QKeySequence("Ctrl+K"), self, self.open_global_search)
        QShortcut(QKeySequence("F5"), self, self.refresh_current_page)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self._build_status_bar()
        self.set_theme(self.settings.theme, persist=False)
        self._update_active_case_label()
        self._reset_idle_timer()
        self.sidebar.setCurrentRow(0)

    # --- construction -----------------------------------------------------------------
    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(10)

        self.brand_logo_label = QLabel()
        self.brand_logo_label.setTextFormat(Qt.TextFormat.PlainText)
        self.brand_logo_label.setAccessibleName("9VA logo")
        self.brand_logo_label.setToolTip("9va.net")
        logo_path = resources_dir() / "9va-badge.png"
        if not logo_path.is_file():
            logo_path = resources_dir() / "imagetrace.png"
        if logo_path.is_file():
            pix = QPixmap(str(logo_path))
            if not pix.isNull():
                self.brand_logo_label.setPixmap(
                    pix.scaled(
                        78,
                        28,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        lay.addWidget(self.brand_logo_label)

        title = QLabel(APP_NAME)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setObjectName("AppTitle")
        lay.addWidget(title)

        self.active_case_label = QLabel("Active case: none")
        self.active_case_label.setTextFormat(Qt.TextFormat.PlainText)
        self.active_case_label.setAccessibleName("Active case")
        lay.addWidget(self.active_case_label)
        lay.addStretch(1)

        self.search = QLineEdit()
        self.search.setAccessibleName("Search cases")
        self.search.setFixedWidth(260)
        self.search.setClearButtonEnabled(True)
        if self.cases is not None:
            self.search.setPlaceholderText("Search cases (Enter)")
            self.search.returnPressed.connect(self._on_search)
        else:
            self.search.setPlaceholderText("Search unavailable (no database)")
            self.search.setEnabled(False)
        lay.addWidget(self.search)

        self.global_search_btn = QPushButton("Find…")
        self.global_search_btn.setToolTip(
            "Global offline search across Cases, Evidence, Origin & Trace, Metadata, OCR & Notes "
            "(Ctrl+K)"
        )
        self.global_search_btn.setEnabled(self.cases is not None)
        self.global_search_btn.clicked.connect(lambda: self.open_global_search(self.search.text()))
        lay.addWidget(self.global_search_btn)

        self.lock_btn = QPushButton("Lock")
        self.lock_btn.setToolTip("Lock session privacy screen (Ctrl+L)")
        self.lock_btn.clicked.connect(self.lock_session)
        lay.addWidget(self.lock_btn)

        self.status_indicator = QLabel()
        self.status_indicator.setTextFormat(Qt.TextFormat.PlainText)
        self.status_indicator.setAccessibleName("Status")
        lay.addWidget(self.status_indicator)
        self._update_status_indicator()
        return bar

    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        self.storage_label = QLabel()
        self.storage_label.setTextFormat(Qt.TextFormat.PlainText)
        self.storage_label.setAccessibleName("Storage location")
        sb.addPermanentWidget(self.storage_label)
        self.offline_label = QLabel()
        self.offline_label.setTextFormat(Qt.TextFormat.PlainText)
        sb.addPermanentWidget(self.offline_label)
        self._update_storage_label()
        self._update_offline_label()

    def _update_offline_label(self) -> None:
        if self.settings.allow_external_map:
            self.offline_label.setText("Local-first · external map opt-in enabled (on demand)")
        else:
            self.offline_label.setText("Offline · no network features")

    def _update_status_indicator(self) -> None:
        if not self.storage.available:
            text, state, tip = "● Storage unavailable", "error", self.storage.message
        elif self.db is None:
            text, state, tip = "● Database error", "error", self.db_error or ""
        elif self.storage.low_space:
            text, state, tip = "● Low disk space", "warn", self.storage.message
        else:
            text, state, tip = "● Ready", "ok", "Storage and database OK"
        self.status_indicator.setText(text)
        self.status_indicator.setProperty("state", state)
        self.status_indicator.setToolTip(tip)

    def _update_storage_label(self) -> None:
        st = self.storage
        if st.available:
            self.storage_label.setText(f"Storage: {st.root}  ({format_bytes(st.free_bytes)} free)")
        else:
            self.storage_label.setText(f"Storage: unavailable ({st.root})")
            self.statusBar().showMessage(st.message)
        if self.db_error:
            self.statusBar().showMessage(self.db_error)

    # --- navigation -------------------------------------------------------------------
    def _on_nav(self, row: int) -> None:
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        self.pages[NAV_ITEMS[row]].refresh()

    def navigate(self, name: str) -> None:
        self.navigate_index(NAV_ITEMS.index(name))

    def navigate_index(self, index: int) -> None:
        if self.sidebar.currentRow() == index:
            self._on_nav(index)
        else:
            self.sidebar.setCurrentRow(index)

    def current_page_name(self) -> str:
        return NAV_ITEMS[self.stack.currentIndex()]

    def refresh_current_page(self) -> None:
        if not self._locked:
            self.pages[self.current_page_name()].refresh()

    def _on_search(self) -> None:
        text = self.search.text()
        self.navigate("Cases")
        self.cases_page.set_filter(text)

    def open_evidence_item(self, evidence_id: str, tab_index: int = 0) -> None:
        self.navigate("Evidence")
        self.evidence_page.reload(select_id=evidence_id)
        if 0 <= tab_index < self.evidence_page.details.tabs.count():
            self.evidence_page.details.tabs.setCurrentIndex(tab_index)

    def run_global_search_query(self, query: str, category: str = "All") -> list[SearchHit]:
        if (
            self.db is None
            or self.cases is None
            or self.evidence is None
            or self.metadata is None
            or self.ocr is None
            or self.notes is None
            or self.provenance is None
        ):
            return []
        return search_workspace(
            self.db,
            self.cases,
            self.evidence,
            self.metadata,
            self.ocr,
            self.notes,
            self.provenance,
            query,
            active_case_id=self.settings.active_case_id,
            category_filter=category,
        )

    def jump_to_search_hit(self, hit: SearchHit) -> None:
        if hit.kind == "Case":
            self.navigate("Cases")
            self.cases_page.reload(select_id=hit.case_id)
            return
        if self.settings.active_case_id != hit.case_id:
            self.set_active_case(hit.case_id)
        if hit.evidence_id:
            self.open_evidence_item(hit.evidence_id, tab_index=hit.tab_index)

    def open_global_search(self, initial_query: str = "") -> None:
        if self._locked or self.cases is None:
            return
        dlg = GlobalSearchDialog(
            self.run_global_search_query,
            initial_query=initial_query if isinstance(initial_query, str) else "",
            parent=self,
        )
        if dlg.exec() == GlobalSearchDialog.DialogCode.Accepted and dlg.selected_hit is not None:
            self.jump_to_search_hit(dlg.selected_hit)

    # --- settings ---------------------------------------------------------------------
    def actor_label(self) -> str:
        return self.settings.analyst_label

    def _save_settings(self) -> None:
        try:
            self.settings = self.settings_store.save(self.settings)
        except (DatabaseError, sqlite3.Error) as exc:
            self.statusBar().showMessage(f"Settings not saved: {exc}")

    def set_theme(self, name: str, persist: bool = True) -> None:
        app = QApplication.instance()
        if app is not None:
            self.resolved_theme = apply_theme(app, name)
        self.settings.theme = name
        if persist:
            self._save_settings()
            if not self.settings_store.persistent:
                self.statusBar().showMessage("Theme changed for this session only (no storage).")

    def set_analyst_label(self, text: str) -> str:
        self.settings.analyst_label = text
        self._save_settings()
        self.statusBar().showMessage(f"Analyst label saved: {self.settings.analyst_label}", 4000)
        return self.settings.analyst_label

    def set_import_limits(self, max_mb: int, max_mp: int) -> tuple[int, int]:
        self.settings.max_import_mb = max_mb
        self.settings.max_image_megapixels = max_mp
        self._save_settings()
        self.statusBar().showMessage(
            f"Import limits saved: {self.settings.max_import_mb} MB / "
            f"{self.settings.max_image_megapixels} MP",
            4000,
        )
        return self.settings.max_import_mb, self.settings.max_image_megapixels

    def set_exiftool_path(self, path: str) -> str:
        self.settings.exiftool_path = path
        self._save_settings()
        self.pages["Dashboard"].refresh()
        self.statusBar().showMessage(
            f"ExifTool path saved: {self.settings.exiftool_path or 'auto-detect'}", 4000
        )
        return self.settings.exiftool_path

    def set_tesseract_settings(self, exe_path: str, tessdata_dir: str) -> tuple[str, str]:
        self.settings.tesseract_path = exe_path
        self.settings.tessdata_dir = tessdata_dir
        self._save_settings()
        self.pages["Dashboard"].refresh()
        self.statusBar().showMessage(
            f"Tesseract settings saved: {self.settings.tesseract_path or 'auto-detect'}", 4000
        )
        return self.settings.tesseract_path, self.settings.tessdata_dir

    def set_allow_external_map(self, enabled: bool) -> bool:
        self.settings.allow_external_map = bool(enabled)
        self._save_settings()
        self._update_offline_label()
        self.statusBar().showMessage(
            "External map preview opt-in enabled (on-demand only)."
            if self.settings.allow_external_map
            else "External map preview disabled (strictly offline).",
            4000,
        )
        return self.settings.allow_external_map

    def set_lock_timeout(self, minutes: int) -> int:
        self.settings.lock_timeout_minutes = int(minutes)
        self._save_settings()
        self._reset_idle_timer()
        msg = (
            f"Idle session lock set to {self.settings.lock_timeout_minutes} minute(s)."
            if self.settings.lock_timeout_minutes > 0
            else "Idle session auto-lock disabled (manual Ctrl+L lock still available)."
        )
        self.statusBar().showMessage(msg, 4000)
        return self.settings.lock_timeout_minutes

    def set_lock_passphrase(self, passphrase: str | None) -> bool:
        self.settings.lock_passphrase_hash = hash_passphrase(passphrase) if passphrase else ""
        self._save_settings()
        self.statusBar().showMessage(
            "Session lock passphrase saved (stored as salted scrypt hash)."
            if self.settings.lock_passphrase_hash
            else "Session lock passphrase cleared (privacy-screen mode).",
            4000,
        )
        return bool(self.settings.lock_passphrase_hash)

    def set_backup_reminder_days(self, days: int) -> int:
        self.settings.backup_reminder_days = int(days)
        self._save_settings()
        self.statusBar().showMessage(
            f"Backup reminder interval set to {self.settings.backup_reminder_days} day(s)."
            if self.settings.backup_reminder_days > 0
            else "Backup age reminder disabled.",
            4000,
        )
        return self.settings.backup_reminder_days

    # --- session lock & idle timeout --------------------------------------------------
    @property
    def is_locked(self) -> bool:
        return self._locked

    def _reset_idle_timer(self) -> None:
        self.idle_timer.stop()
        if not self._locked and self.settings.lock_timeout_minutes > 0:
            self.idle_timer.start(self.settings.lock_timeout_minutes * 60 * 1000)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # Qt API name
        if (
            not self._locked
            and self.settings.lock_timeout_minutes > 0
            and event.type()
            in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.KeyPress,
                QEvent.Type.Wheel,
            )
        ):
            self._reset_idle_timer()
        return super().eventFilter(watched, event)

    def lock_session(self) -> None:
        if self._locked:
            return
        self._locked = True
        self.idle_timer.stop()
        self.lock_overlay.configure(has_passphrase=bool(self.settings.lock_passphrase_hash))
        self.root_stack.setCurrentIndex(1)
        self.active_case_label.setText("Session locked")
        self.active_case_label.setToolTip("")
        self.search.setEnabled(False)
        self.global_search_btn.setEnabled(False)
        self.lock_btn.setEnabled(False)
        log.info("Session locked")

    def unlock_session(self, passphrase: str = "") -> bool:
        if not self._locked:
            return True
        if self.settings.lock_passphrase_hash and not verify_passphrase(
            passphrase, self.settings.lock_passphrase_hash
        ):
            self.lock_overlay.show_error("Incorrect passphrase.")
            return False
        self._locked = False
        self.root_stack.setCurrentIndex(0)
        self.search.setEnabled(self.cases is not None)
        self.global_search_btn.setEnabled(self.cases is not None)
        self.lock_btn.setEnabled(True)
        self._update_active_case_label()
        self._reset_idle_timer()
        log.info("Session unlocked")
        return True

    # --- cases & evidence -------------------------------------------------------------
    def _validate_active_case(self) -> None:
        case_id = self.settings.active_case_id
        if case_id is None:
            return
        case = self.cases.get(case_id) if self.cases is not None else None
        if case is None or case.archived:
            self.settings.active_case_id = None
            if self.cases is not None:
                self._save_settings()

    def set_active_case(self, case_id: str | None) -> None:
        self.settings.active_case_id = case_id
        self._save_settings()
        self._update_active_case_label()
        self.evidence_page.refresh()
        self.reports_page.refresh()
        self.statusBar().showMessage(
            f"Active case: {case_id}" if case_id else "No active case", 4000
        )

    def _update_active_case_label(self) -> None:
        case_id = self.settings.active_case_id
        case = self.cases.get(case_id) if (self.cases is not None and case_id) else None
        if case is None:
            self.active_case_label.setText("Active case: none")
            self.active_case_label.setToolTip("")
        else:
            self.active_case_label.setText(f"Active case: {case.id} · {elide(case.title)}")
            self.active_case_label.setToolTip(case.title)

    def _on_cases_changed(self) -> None:
        self._validate_active_case()
        self._update_active_case_label()
        self._update_offline_label()
        self.evidence_page.refresh()
        self.reports_page.refresh()

    def _on_evidence_changed(self) -> None:
        self.pages["Dashboard"].refresh()
        self.cases_page.reload()
        self.pages["Analysis"].refresh()
        self.reports_page.refresh()

    def _on_exports_changed(self) -> None:
        self.cases_page.reload()
        self.evidence_page.reload()

    # --- database maintenance ---------------------------------------------------------
    @property
    def backups_dir(self) -> Path:
        return self.data_root / "backups"

    def _settings_page(self) -> pages.SettingsPage:
        return self.pages["Settings"]  # type: ignore[return-value]

    def backup_now(self, *, passphrase: str | None = None) -> BackupResult | None:
        if self.db is None:
            return None
        try:
            result = backup_database(
                self.db,
                self.backups_dir,
                label="encrypted" if passphrase else "manual",
                passphrase=passphrase,
            )
            with self.db.transaction() as con:
                append_event(
                    con,
                    "database_backup_created",
                    actor_label=self.actor_label(),
                    details={
                        "backup_file": result.path.name,
                        "sha256": result.sha256,
                        "encrypted": result.encrypted,
                    },
                )
        except (BackupError, DatabaseError, sqlite3.Error, OSError) as exc:
            self._settings_page().show_result(f"Backup failed: {exc}", "error")
            dialogs.error(self, "Backup failed", str(exc))
            return None
        kind = "Encrypted backup" if result.encrypted else "Backup"
        msg = f"{kind} saved: {result.path}\nSHA-256: {result.sha256}"
        self._settings_page().show_result(msg, "ok")
        self._settings_page().refresh()
        dialogs.info(self, f"{kind} created", msg)
        return result

    def backup_encrypted_now(self) -> BackupResult | None:
        if self.db is None:
            return None
        pw = dialogs.ask_new_passphrase(
            self,
            "Create encrypted backup",
            "Choose a strong passphrase to encrypt this database backup with AES-256-GCM "
            "(scrypt KDF). If you lose the passphrase, this backup cannot be recovered.",
        )
        if pw is None:
            return None
        return self.backup_now(passphrase=pw)

    def verify_database(self) -> bool:
        if self.db is None:
            return False
        problems = self.db.integrity_check()
        chain = verify_chain(self.db.con)
        ok = not problems and chain.ok
        lines = ["Database integrity: OK" if not problems else "Database integrity problems:"]
        lines += [f"  - {p}" for p in problems[:10]]
        lines.append(chain.message)
        msg = "\n".join(lines)
        self._settings_page().show_result(msg, "ok" if ok else "error")
        (dialogs.info if ok else dialogs.error)(self, "Integrity check", msg)
        return ok

    def restore_from_backup(self, path: Path | None = None, passphrase: str | None = None) -> bool:
        if self.db is None:
            return False
        if path is None:
            path = dialogs.choose_open_file(
                self,
                "Choose a database backup",
                self.backups_dir,
                "ImageTrace backup (*.sqlite *.itbak);;All files (*)",
            )
            if path is None:
                return False
        pw = passphrase
        if pw is None and (
            is_encrypted_backup(path) or path.name.lower().endswith(".sqlite.itbak")
        ):
            pw = dialogs.ask_passphrase(
                self,
                "Decrypt backup",
                f"Enter the passphrase for encrypted backup:\n{path.name}",
            )
            if pw is None:
                return False
        check = verify_backup(path, passphrase=pw, temp_dir=self.data_root / "temp")
        if not check.ok:
            dialogs.error(self, "Backup can't be restored", check.message)
            return False
        if not dialogs.confirm(
            self,
            "Restore database",
            f"Replace the current database with this backup?\n\n{path.name}\n{check.message}\n\n"
            "The current database is backed up first, so this can be undone.",
        ):
            return False
        try:
            pre = restore_database(
                self.db,
                path,
                self.backups_dir,
                self.actor_label(),
                passphrase=pw,
                temp_dir=self.data_root / "temp",
            )
        except (BackupError, DatabaseError, sqlite3.Error, OSError) as exc:
            dialogs.error(self, "Restore failed", str(exc))
            return False
        self.settings = self.settings_store.load()
        self._validate_active_case()
        self._update_active_case_label()
        self._update_offline_label()
        self._reset_idle_timer()
        self.cases_page.reload()
        self.evidence_page.refresh()
        self.reports_page.refresh()
        self.pages["Dashboard"].refresh()
        msg = f"Database restored from {path.name}.\nThe previous database was saved as {pre.name}."
        self._settings_page().show_result(msg, "ok")
        self._settings_page().refresh()
        dialogs.info(self, "Restore complete", msg)
        return True

    # --- lifecycle --------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # Qt API name
        self.idle_timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        if self.db is not None:
            self.db.close()
        super().closeEvent(event)
