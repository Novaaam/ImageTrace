import json

from PySide6.QtWidgets import QApplication

from imagetrace.ui.main_window import MainWindow
from synthetic_images import make_valid_jpeg

SCOPE = "Synthetic GUI OCR test scope."

TSV_SYNTH = "\n".join(
    [
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
        "5\t1\t1\t1\t1\t1\t12\t18\t80\t22\t95.0\tPIER",
        "5\t1\t1\t1\t1\t2\t98\t18\t30\t22\t92.5\t42",
        "5\t1\t1\t1\t2\t1\t12\t48\t150\t22\t88.0\t<script>IGNORE_ALL</script>",
    ]
)


def test_ocr_tab_user_triggered_untrusted_text_and_exports(qtbot, tmp_path, dialog_log):
    w = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w)
    w.show()
    case = w.cases.create("Synthetic OCR GUI case", SCOPE)
    w.set_active_case(case.id)
    ev = w.evidence.import_file(case.id, make_valid_jpeg(tmp_path / "sign.jpg"))

    # Inject mock offline runner (verifying OCR NEVER auto-runs on import or tab switch)
    called = 0

    def fake_runner(_img, lang, _td):
        nonlocal called
        called += 1
        return "Tesseract", "5.4.0-synth", TSV_SYNTH

    w.ocr._custom_runner = fake_runner
    w.navigate("Evidence")
    w.evidence_page.reload(select_id=ev.id)
    w.evidence_page.details.tabs.setCurrentIndex(4)  # OCR tab

    tab = w.evidence_page.details.ocr_tab
    assert called == 0
    assert not tab.export_json_btn.isEnabled()
    assert not tab.export_txt_btn.isEnabled()
    assert tab.results_host.isHidden()

    # Setup guide dialog
    tab.guide_btn.click()
    assert dialog_log.calls[-1][0] == "info"
    assert "traineddata" in dialog_log.calls[-1][2]

    # User clicks Run OCR
    tab.run_btn.click()
    assert called == 1
    assert not tab.results_host.isHidden()
    assert tab.export_json_btn.isEnabled()
    assert tab.export_txt_btn.isEnabled()
    assert "Tesseract 5.4.0-synth" in tab.status_label.text()
    assert "PIER 42" in tab.text_view.toPlainText()
    # Untrusted HTML/prompt-injection text stays strictly plain text
    assert "<script>IGNORE_ALL</script>" in tab.text_view.toPlainText()
    assert tab.items_table.rowCount() == 3
    assert "(12, 18, 80 x 22)" in tab.items_table.item(0, 2).text()

    # Copy text
    tab.copy_text_btn.click()
    assert "PIER 42" in QApplication.clipboard().text()

    # Export JSON and TXT
    json_out = tmp_path / "gui_ocr.json"
    dialog_log.save_file = json_out
    tab.export_json_btn.click()
    assert json_out.is_file()
    assert json.loads(json_out.read_text(encoding="utf-8"))["evidence_id"] == ev.id

    txt_out = tmp_path / "gui_ocr.txt"
    dialog_log.save_file = txt_out
    tab.export_txt_btn.click()
    assert txt_out.is_file()
    assert "AUTOMATED RESULT" in txt_out.read_text(encoding="utf-8")

    # Overview & Integrity history updated (and history NEVER leaks raw OCR text!)
    assert "Automated OCR" in w.evidence_page.details.ov_fields["ocr"].text()
    hist = w.evidence_page.details.history
    hist_text = "\n".join(hist.item(i).text() for i in range(hist.count()))
    assert "OCR run (automated)" in hist_text
    assert "OCR exported" in hist_text
    assert "IGNORE_ALL" not in hist_text

    # Invalid language code handled with clean error dialog
    tab.lang_combo.setEditText("--bad-flag")
    tab.run_btn.click()
    assert dialog_log.calls[-1][0] == "error"


def test_settings_tesseract_paths_persist(qtbot, tmp_path):
    w = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w)
    sp = w.pages["Settings"]
    sp.tess_edit.setText(str(tmp_path / "tesseract.exe"))
    sp.tessdata_edit.setText(str(tmp_path / "tessdata"))
    sp.tess_save.click()
    sp.tess_test.click()
    assert "not found" in sp.tess_status.text().lower()
    w.close()

    w2 = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w2)
    assert w2.settings.tesseract_path == str(tmp_path / "tesseract.exe")
    assert w2.settings.tessdata_dir == str(tmp_path / "tessdata")
