import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent

from imagetrace.hashing import sha256_file
from imagetrace.ui.main_window import MainWindow
from synthetic_images import make_valid_jpeg, make_valid_png, make_zip_file

SCOPE = "Synthetic GUI evidence test scope."


@pytest.fixture
def window_with_case(qtbot, tmp_path):
    w = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w)
    w.show()
    case = w.cases.create("Synthetic evidence case", SCOPE)
    w.set_active_case(case.id)
    w.navigate("Evidence")
    return w, case


def test_no_active_case_shows_prompt_and_navigates(qtbot, tmp_path):
    w = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w)
    w.show()
    w.navigate("Evidence")
    page = w.evidence_page
    assert not page.no_case_card.isHidden()
    assert page.workspace.isHidden()
    page.go_cases_btn.click()
    assert w.current_page_name() == "Cases"


def test_import_valid_image_shows_all_seven_tabs_and_details(window_with_case, tmp_path):
    w, case = window_with_case
    page = w.evidence_page
    src = make_valid_jpeg(tmp_path / "scene.jpg", (200, 140), with_icc=True)
    src_sha = sha256_file(src)

    page.note_edit.setText("Synthetic batch 1")
    report = page.import_files([src])
    assert report is not None and len(report.imported) == 1
    assert page.table.rowCount() == 1
    assert page.empty_label.isHidden()
    assert page.note_edit.text() == ""

    details = page.details
    tab_names = [details.tabs.tabText(i).replace("&&", "&") for i in range(details.tabs.count())]
    assert tab_names == [
        "Overview",
        "Origin & Trace",
        "Metadata",
        "GPS",
        "OCR",
        "Image properties",
        "Analyst notes",
        "Integrity & history",
    ]
    assert details.ov_fields["original"].text() == "scene.jpg"
    assert details.ov_hash.text() == src_sha
    assert not details.thumb_label.pixmap().isNull()
    assert "200 x 140 px" in details.prop_fields["dimensions"].text()
    assert details.history.count() == 1

    # Dashboard recent evidence updated
    w.navigate("Dashboard")
    dash = w.pages["Dashboard"]
    assert dash.evidence_empty.isHidden()
    texts = [dash.evidence_box.itemAt(i).widget().text() for i in range(dash.evidence_box.count())]
    assert any("scene.jpg" in t for t in texts)

    # Case details evidence count updated
    w.navigate("Cases")
    w.cases_page.reload(select_id=case.id)
    assert w.cases_page.details.fields["evidence"].text() == "1 item"


def test_duplicate_confirmation_in_gui(window_with_case, tmp_path, dialog_log):
    w, _case = window_with_case
    page = w.evidence_page
    src = make_valid_png(tmp_path / "same.png")

    page.import_files([src])
    assert page.table.rowCount() == 1

    # Decline duplicate
    dialog_log.confirm_answer = False
    r1 = page.import_files([src])
    assert len(r1.skipped_duplicates) == 1
    assert page.table.rowCount() == 1

    # Confirm duplicate
    dialog_log.confirm_answer = True
    r2 = page.import_files([src])
    assert len(r2.imported) == 1
    assert page.table.rowCount() == 2


def test_unsupported_file_rejected_in_gui(window_with_case, tmp_path, dialog_log):
    w, _case = window_with_case
    page = w.evidence_page
    bad = make_zip_file(tmp_path / "not_an_image.zip")
    report = page.import_files([bad])
    assert len(report.rejected) == 1
    assert page.table.rowCount() == 0
    assert "error" in dialog_log.kinds()


def test_verify_integrity_and_derived_copy_via_gui(window_with_case, tmp_path):
    w, _case = window_with_case
    page = w.evidence_page
    src = make_valid_jpeg(tmp_path / "photo.jpg", (160, 120))
    page.import_files([src])

    details = page.details
    details.verify_btn.click()
    assert "Verified" in details.integrity_status.text()
    assert details.history.count() == 2

    details.rotate_combo.setCurrentIndex(1)  # 90 deg CW
    details.contrast_spin.setValue(1.2)
    details.derived_btn.click()
    assert "Original file SHA-256 verified unchanged" in details.derived_result.text()
    assert not details.derived_preview.isHidden()
    assert details.history.count() == 3


def test_drag_and_drop_imports_file(window_with_case, tmp_path):
    w, _case = window_with_case
    page = w.evidence_page
    src = make_valid_png(tmp_path / "dropped.png")

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(src))])
    event = QDropEvent(
        QPointF(20, 20),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    page.dropEvent(event)
    assert page.table.rowCount() == 1
    assert page.table.item(0, 1).text() == "dropped.png"


def test_settings_import_limits_persist(qtbot, tmp_path):
    w = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w)
    sp = w.pages["Settings"]
    sp.max_mb_spin.setValue(25)
    sp.max_mp_spin.setValue(40)
    sp.limits_save.click()
    w.close()

    w2 = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w2)
    assert w2.settings.max_import_mb == 25
    assert w2.settings.max_image_megapixels == 40
    assert w2.pages["Settings"].max_mb_spin.value() == 25
    assert w2.pages["Settings"].max_mp_spin.value() == 40
