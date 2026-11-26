import json

import pytest

from imagetrace.tools_status import find_exiftool
from imagetrace.ui.main_window import MainWindow
from synthetic_images import make_exif_jpeg_with_exiftool, make_valid_jpeg

SCOPE = "Synthetic GUI metadata test scope."


@pytest.fixture
def exiftool_exe():
    exe = find_exiftool()
    assert exe is not None and exe.is_file()
    return exe


@pytest.fixture
def window_with_evidence(qtbot, tmp_path, exiftool_exe):
    w = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w)
    w.show()
    case = w.cases.create("Synthetic metadata case", SCOPE)
    w.set_active_case(case.id)
    src = make_exif_jpeg_with_exiftool(
        tmp_path / "synth_gui.jpg",
        exiftool_exe,
        {
            "IFD0:Make": "SynthCam",
            "IFD0:Model": "<b>Untrusted</b>",
            "ExifIFD:LensModel": "Synth 50mm",
            "IFD0:Orientation#": "6",
            "ExifIFD:DateTimeOriginal": "2026:05:10 14:30:00",
            "IFD0:ModifyDate": "2026:05:08 10:00:00",
        },
    )
    ev = w.evidence.import_file(case.id, src)
    w.navigate("Evidence")
    w.evidence_page.reload(select_id=ev.id)
    w.evidence_page.details.tabs.setCurrentIndex(2)  # Metadata tab
    return w, case, ev


def test_metadata_tab_extract_filter_and_export(window_with_evidence, tmp_path, dialog_log):
    w, _case, ev = window_with_evidence
    tab = w.evidence_page.details.metadata_tab
    assert not tab.export_btn.isEnabled()

    tab.extract_btn.click()
    assert tab.export_btn.isEnabled()
    assert tab.table.rowCount() > 0
    assert "SynthCam" in tab.sum_fields["camera"].text()
    # Untrusted HTML stays plain text
    assert "<b>Untrusted</b>" in tab.sum_fields["camera"].text()
    assert "50mm" in tab.sum_fields["lens"].text()
    assert "2026-05-10T14:30:00" in tab.sum_fields["date_original"].text()
    assert not tab.warnings_title.isHidden()

    # Origin filter separates embedded vs tool/system metadata
    total_rows = tab.table.rowCount()
    tab.origin_combo.setCurrentIndex(tab.origin_combo.findData("embedded"))
    embedded_rows = tab.table.rowCount()
    assert 0 < embedded_rows < total_rows
    tab.origin_combo.setCurrentIndex(tab.origin_combo.findData("application_system"))
    sys_rows = tab.table.rowCount()
    assert sys_rows > 0
    tab.origin_combo.setCurrentIndex(0)

    # Search filter
    tab.search_edit.setText("LensModel")
    assert tab.table.rowCount() == 1
    assert tab.table.item(0, 2).text() == "LensModel"
    tab.search_edit.clear()

    # Overview tab updated
    assert "ExifTool" in w.evidence_page.details.ov_fields["metadata"].text()

    # Export JSON via mocked save dialog
    export_dest = tmp_path / "gui_meta.json"
    dialog_log.save_file = export_dest
    tab.export_btn.click()
    assert export_dest.is_file()
    data = json.loads(export_dest.read_text(encoding="utf-8"))
    assert data["evidence_id"] == ev.id
    assert "info" in dialog_log.kinds()

    # Integrity & history tab recorded extract + export
    hist = w.evidence_page.details.history
    hist_text = "\n".join(hist.item(i).text() for i in range(hist.count()))
    assert "Metadata extracted" in hist_text
    assert "Metadata exported" in hist_text


def test_analysis_page_batch_extracts_pending(qtbot, tmp_path):
    w = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w)
    case = w.cases.create("Batch case", SCOPE)
    w.set_active_case(case.id)
    w.evidence.import_file(case.id, make_valid_jpeg(tmp_path / "one.jpg"))
    w.evidence.import_file(case.id, make_valid_jpeg(tmp_path / "two.jpg", variant=1))

    w.navigate("Analysis")
    ap = w.pages["Analysis"]
    assert "2 pending" in ap.meta_status.text()
    assert ap.batch_btn.isEnabled()
    ap.batch_btn.click()
    assert "0 pending" in ap.meta_status.text()
    assert not ap.batch_btn.isEnabled()


def test_settings_exiftool_test_and_persistence(qtbot, tmp_path, exiftool_exe):
    w = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w)
    sp = w.pages["Settings"]
    sp.exiftool_test.click()
    assert "OK: ExifTool 13." in sp.exiftool_status.text()

    sp.exiftool_edit.setText(str(exiftool_exe))
    sp.exiftool_save.click()
    w.close()

    w2 = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w2)
    assert w2.settings.exiftool_path == str(exiftool_exe)
