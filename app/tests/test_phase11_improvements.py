import os
import stat

from PySide6.QtWidgets import QPushButton

from imagetrace.backup import backup_database
from imagetrace.paths import resources_dir
from imagetrace.tools_status import find_exiftool
from imagetrace.ui.batch_workers import BatchAnalysisReport, BatchAnalysisWorker
from imagetrace.ui.main_window import MainWindow
from synthetic_images import make_valid_jpeg, make_valid_png

SCOPE = "Synthetic Phase 11 improvement test scope."


def test_app_icon_assets_and_window_icon(qtbot, tmp_path):
    assert (resources_dir() / "imagetrace.ico").is_file()
    assert (resources_dir() / "imagetrace.png").is_file()
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    assert not win.windowIcon().isNull()


def test_batch_integrity_worker_ok_and_mismatch(qtbot, tmp_path):
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    win.show()
    case = win.cases.create("Batch integrity case", SCOPE)
    win.set_active_case(case.id)
    ev1 = win.evidence.import_file(case.id, make_valid_jpeg(tmp_path / "a.jpg"))
    win.evidence.import_file(case.id, make_valid_png(tmp_path / "b.png"))

    win.navigate("Analysis")
    analysis = win.pages["Analysis"]
    assert analysis.verify_all_btn.isEnabled()

    # 1. All originals intact
    analysis.verify_all_btn.click()
    assert analysis.chain_result.property("state") == "ok"
    assert "Verified all 2 evidence original(s)" in analysis.chain_result.text()

    # 2. Tamper with one original on disk -> batch check detects mismatch
    orig1 = win.evidence.original_path(ev1)
    os.chmod(orig1, stat.S_IWRITE | stat.S_IREAD)
    orig1.write_bytes(orig1.read_bytes() + b"TAMPERED")
    analysis.verify_all_btn.click()
    assert analysis.chain_result.property("state") == "error"
    assert "Integrity mismatch on 1 of 2" in analysis.chain_result.text()


def test_batch_analysis_worker_cancel_and_reports_readiness(qtbot, tmp_path):
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    win.show()
    case = win.cases.create("Batch analysis case", SCOPE)
    win.set_active_case(case.id)
    ev1 = win.evidence.import_file(case.id, make_valid_jpeg(tmp_path / "a.jpg"))
    ev2 = win.evidence.import_file(case.id, make_valid_png(tmp_path / "b.png"))

    # Before metadata extraction, ReportsPage displays helpful non-blocking readiness hint
    win.navigate("Reports")
    assert win.reports_page.readiness_label.isVisible()
    assert "2 of 2" in win.reports_page.readiness_label.text()

    # Worker respects pre-cancelled flag cleanly without modifying DB
    worker = BatchAnalysisWorker(
        win.db.path,
        tmp_path,
        [ev1.id, ev2.id],
        actor_label="Tester",
    )
    got: list[BatchAnalysisReport] = []
    worker.completed.connect(got.append)
    worker.cancel()
    worker.run()
    assert len(got) == 1
    assert got[0].cancelled is True
    assert got[0].succeeded == []

    # Real background batch extraction via AnalysisPage when ExifTool is available
    if find_exiftool() is not None:
        win.navigate("Analysis")
        analysis = win.pages["Analysis"]
        analysis.batch_btn.click()
        assert analysis.batch_result.property("state") == "ok"
        assert "2 evidence item(s)" in analysis.batch_result.text()
        # Readiness warning clears automatically once all items have metadata
        win.navigate("Reports")
        assert not win.reports_page.readiness_label.isVisible()


def test_dashboard_backup_health_quick_actions_and_f5_refresh(qtbot, tmp_path):
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    win.show()

    dash = win.pages["Dashboard"]
    assert dash.backup_state.property("state") == "warn"
    assert "none yet" in dash.backup_state.text().lower()

    # Create backup and trigger F5 refresh
    backup_database(win.db, tmp_path / "backups", label="manual")
    win.refresh_current_page()
    assert dash.backup_state.property("state") == "ok"
    assert "1 (0 encrypted)" in dash.backup_state.text()

    # Quick-action button navigates cleanly
    report_btns = [b for b in dash.findChildren(QPushButton) if b.text() == "Generate Reports"]
    assert len(report_btns) == 1
    report_btns[0].click()
    assert win.current_page_name() == "Reports"
