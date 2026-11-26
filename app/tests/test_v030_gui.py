"""GUI tests for v0.3.0 Visual Forensics Lab, Zoom/Compare Viewer, Correlation & Global Search."""

from __future__ import annotations

import io
import struct

from PIL import Image, ImageDraw

from imagetrace.ui import dialogs
from imagetrace.ui.main_window import MainWindow
from imagetrace.ui.search_dialog import GlobalSearchDialog
from imagetrace.ui.viewer_dialog import ImageViewerDialog
from synthetic_images import make_valid_jpeg

SCOPE = "Authorized synthetic GUI scope for ImageTrace v0.3.0."


def _make_jpeg_with_thumb_and_mp4(path):
    im = Image.new("RGB", (260, 160), (40, 80, 130))
    ImageDraw.Draw(im).rectangle([20, 20, 120, 140], fill=(230, 210, 120))
    im.save(path, format="JPEG", quality=88)

    th = Image.new("RGB", (80, 80), (220, 60, 60))
    tb = io.BytesIO()
    th.save(tb, format="JPEG", quality=80)
    raw = path.read_bytes()
    exif_seg = b"Exif\x00\x00II*\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00" + tb.getvalue()
    seg = b"\xff\xe1" + struct.pack(">H", len(exif_seg) + 2) + exif_seg
    mp4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 32
    path.write_bytes(raw[:2] + seg + raw[2:] + mp4)
    return path


def test_forensics_lab_modes_thumb_strip_and_collapsible_workspace(qtbot, tmp_path, dialog_log):
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    win.show()

    case = win.cases.create("v030 GUI lab case", SCOPE)
    win.set_active_case(case.id)
    src = _make_jpeg_with_thumb_and_mp4(tmp_path / "lab_item.jpg")
    ev = win.evidence.import_file(case.id, src)

    win.navigate("Evidence")
    win.evidence_page.reload(select_id=ev.id)

    # Collapsible Evidence list toggle expands details pane full-width
    assert not win.evidence_page.table.isHidden()
    win.evidence_page.toggle_list_btn.click()
    assert win.evidence_page.table.isHidden()
    assert win.evidence_page.toggle_list_btn.text() == "Show list"
    win.evidence_page.toggle_list_btn.click()
    assert not win.evidence_page.table.isHidden()

    # Origin & Trace tab shows EXIF thumbnail strip + Motion Photo extraction button
    win.evidence_page.details.tabs.setCurrentIndex(1)
    tab = win.evidence_page.details.provenance_tab
    tab.run_btn.click()
    assert not tab.thumb_compare_host.isHidden()
    assert not tab.exif_mini_label.pixmap().isNull()
    assert not tab.extract_trailing_btn.isHidden()
    assert "Motion Photo" in tab.extract_trailing_btn.text()

    # Exercise Visual Forensics Lab modes (ELA, Noise Residual, Luminance Gradient, Cb, Ghost Curve)
    tab.ela_btn.click()
    assert not tab.ela_host.isHidden()
    assert not tab.ela_image.pixmap().isNull()

    for mode_key in ("noise", "gradient", "ch_cb"):
        idx = tab.lab_mode_combo.findData(mode_key)
        assert idx >= 0
        tab.lab_mode_combo.setCurrentIndex(idx)
        assert not tab.ela_image.pixmap().isNull()

    ghost_idx = tab.lab_mode_combo.findData("ghost")
    tab.lab_mode_combo.setCurrentIndex(ghost_idx)
    assert not tab.ghost_chart.isHidden()
    assert tab.ela_image.isHidden()
    assert "Q=" in tab.ela_stats.text()


def test_interactive_zoom_and_side_by_side_viewer_dialog(qtbot, tmp_path):
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    case = win.cases.create("Viewer case", SCOPE)
    win.set_active_case(case.id)
    ev1 = win.evidence.import_file(case.id, _make_jpeg_with_thumb_and_mp4(tmp_path / "v1.jpg"))
    ev2 = win.evidence.import_file(case.id, make_valid_jpeg(tmp_path / "v2.jpg", (220, 150)))

    dlg = ImageViewerDialog(win.evidence, ev1, initial_compare="single", parent=win)
    qtbot.addWidget(dlg)
    dlg.show()

    assert dlg.right_canvas.isHidden()
    dlg.zoom_100_btn.click()
    assert dlg.left_canvas.zoom_factor == 1.0
    dlg.zoom_in_btn.click()
    assert dlg.left_canvas.zoom_factor > 1.0

    # Switch to Embedded EXIF thumbnail compare
    idx_et = dlg.compare_combo.findData("exif_thumb")
    assert idx_et >= 0
    dlg.compare_combo.setCurrentIndex(idx_et)
    assert not dlg.right_canvas.isHidden()

    # Switch to ELA map compare
    dlg.compare_combo.setCurrentIndex(dlg.compare_combo.findData("ela"))
    assert not dlg.right_canvas.isHidden()

    # Switch to another evidence item in the same case
    dlg.compare_combo.setCurrentIndex(dlg.compare_combo.findData(f"ev:{ev2.id}"))
    assert not dlg.right_canvas.isHidden()
    dlg.close()


def test_correlation_global_search_and_bundle_export_gui(qtbot, tmp_path, monkeypatch, dialog_log):
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    win.show()

    case = win.cases.create("Correlate & Search case", SCOPE)
    win.set_active_case(case.id)
    ev1 = win.evidence.import_file(
        case.id, make_valid_jpeg(tmp_path / "alpha_scene.jpg", (200, 140))
    )
    ev2 = win.evidence.import_file(
        case.id, make_valid_jpeg(tmp_path / "beta_scene.jpg", (202, 140))
    )
    win.provenance.analyze(ev1.id, extract_metadata_if_missing=False)
    win.provenance.analyze(ev2.id, extract_metadata_if_missing=False)
    win.notes.create(
        case.id,
        ev2.id,
        category="Environmental detail",
        confidence_label="Analyst observation",
        note_text="Unique cobalt crane visible along dock.",
    )

    # 1. Analysis -> Case Correlation widget populates & double-click opens Evidence
    win.navigate("Analysis")
    analysis = win.pages["Analysis"]
    cw = analysis.correlation_widget
    assert cw.timeline_table.rowCount() == 2
    assert cw.clusters_table.rowCount() >= 1  # shared DQT + visual dHash cluster!
    cw._on_timeline_double_click(0, 0)
    assert win.current_page_name() == "Evidence"

    # 2. Global Search dialog finds Analyst note and jumps straight to Analyst notes tab
    dlg = GlobalSearchDialog(win.run_global_search_query, initial_query="cobalt crane", parent=win)
    qtbot.addWidget(dlg)
    assert dlg.table.rowCount() >= 1
    dlg._open_selected()
    assert dlg.selected_hit is not None
    win.jump_to_search_hit(dlg.selected_hit)
    assert win.current_page_name() == "Evidence"
    assert win.evidence_page.selected_evidence_id() == ev2.id
    assert win.evidence_page.details.tabs.currentIndex() == 6  # Analyst notes tab

    # 3. Reports -> Export verifiable case bundle (.zip)
    win.navigate("Reports")
    dest_zip = tmp_path / "case_export.zip"
    monkeypatch.setattr(dialogs, "choose_save_file", lambda *a, **k: dest_zip)
    win.reports_page.bundle_btn.click()
    assert dest_zip.is_file()
    assert dest_zip.with_name("case_export.zip.sha256").is_file()
