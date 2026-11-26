from imagetrace.ui import dialogs
from imagetrace.ui.main_window import MainWindow
from synthetic_images import make_valid_jpeg

SCOPE = "Synthetic GUI reports test scope."


def test_reports_page_generate_verify_and_cross_page_history(qtbot, tmp_path, monkeypatch):
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    win.show()

    # Without an active case, generation buttons are disabled
    win.navigate("Reports")
    page = win.reports_page
    assert not page.pdf_btn.isEnabled()
    assert not page.json_btn.isEnabled()
    assert "No active case" in page.case_label.text()

    # Create case, import image, add note
    case = win.cases.create("Reports GUI case", SCOPE)
    win.set_active_case(case.id)
    ev = win.evidence.import_file(case.id, make_valid_jpeg(tmp_path / "img.jpg"))
    win.notes.create(
        case.id,
        ev.id,
        category="Object / structure",
        confidence_label="Analyst observation",
        note_text="Observed rectangular structure on left.",
    )

    win.navigate("Reports")
    assert page.pdf_btn.isEnabled()
    assert page.json_btn.isEnabled()
    assert page.scope_combo.count() == 2  # entire case + 1 evidence item

    # 1. Generate PDF report into default cases/<id>/reports/
    page.pdf_btn.click()
    assert page.table.rowCount() == 1
    assert page.gen_result.property("state") == "ok"
    assert ".sha256 sidecar written" in page.gen_result.text()
    assert page.verify_btn.isEnabled()

    # Verify the selected PDF export
    page.verify_btn.click()
    assert page.verify_status.property("state") == "ok"
    assert "verified OK" in page.verify_status.text()

    # 2. Generate JSON report scoped to single evidence item via Save JSON as...
    page.scope_combo.setCurrentIndex(1)
    custom_dest = tmp_path / "custom_report.json"
    monkeypatch.setattr(dialogs, "choose_save_file", lambda *a, **k: custom_dest)
    page.json_as_btn.click()
    assert custom_dest.is_file()
    assert custom_dest.with_name("custom_report.json.sha256").is_file()
    assert page.table.rowCount() == 2

    # 3. Cases page and Evidence Integrity & history reflect export history
    win.navigate("Cases")
    win.cases_page.reload(select_id=case.id)
    assert "2 reports" in win.cases_page.details.fields["exports"].text()

    win.navigate("Evidence")
    win.evidence_page.reload(select_id=ev.id)
    details = win.evidence_page.details
    details.tabs.setCurrentIndex(7)
    assert "2 reports" in details.int_fields["exports"].text()
    hist_text = "\n".join(details.history.item(i).text() for i in range(details.history.count()))
    assert "Report exported" in hist_text

    # 4. About page lists ReportLab
    win.navigate("About")
    from PySide6.QtWidgets import QLabel

    about_text = "\n".join(lbl.text() for lbl in win.pages["About"].findChildren(QLabel))
    assert "ReportLab" in about_text
