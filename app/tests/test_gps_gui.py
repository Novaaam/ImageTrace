import io
import urllib.request

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from imagetrace.gps import NO_GPS_MESSAGE
from imagetrace.tools_status import find_exiftool
from imagetrace.ui.main_window import MainWindow
from synthetic_images import make_exif_jpeg_with_exiftool, make_valid_jpeg

SCOPE = "Synthetic GUI GPS test scope."


@pytest.fixture
def exiftool_exe():
    exe = find_exiftool()
    assert exe is not None and exe.is_file()
    return exe


def _fake_png_tile() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (190, 215, 235)).save(buf, format="PNG")
    return buf.getvalue()


def test_gps_tab_valid_fixture_offline_and_opt_in_map(
    qtbot, tmp_path, exiftool_exe, dialog_log, monkeypatch
):
    # Strictly forbid real network calls across the entire test
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Unexpected real network call!")),
    )

    w = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w)
    w.show()
    case = w.cases.create("Synthetic GPS GUI case", SCOPE)
    w.set_active_case(case.id)

    src = make_exif_jpeg_with_exiftool(
        tmp_path / "synth_gps.jpg",
        exiftool_exe,
        {
            "GPSLatitude": "40.712776",
            "GPSLatitudeRef": "N",
            "GPSLongitude": "74.005974",
            "GPSLongitudeRef": "W",
            "GPSAltitude": "15.0",
            "GPSAltitudeRef": "Above Sea Level",
            "GPSDateStamp": "2026:05:10",
            "GPSTimeStamp": "14:30:05",
        },
    )
    ev = w.evidence.import_file(case.id, src)
    w.navigate("Evidence")
    w.evidence_page.reload(select_id=ev.id)
    w.evidence_page.details.tabs.setCurrentIndex(3)  # GPS tab

    tab = w.evidence_page.details.gps_tab
    calls: list[tuple[int, int, int, bool]] = []
    png_bytes = _fake_png_tile()

    def fake_fetcher(z: int, x: int, y: int, allow: bool) -> bytes:
        calls.append((z, x, y, allow))
        return png_bytes

    tab.tile_fetcher = fake_fetcher

    # Parse GPS (100% offline)
    tab.parse_btn.click()
    assert not tab.coords_host.isHidden()
    assert "40.712776, -74.005974" in tab.dec_edit.text()
    assert "15.00 m" in tab.alt_label.text()
    assert tab.ts_label.text() == "2026-05-10T14:30:05Z"
    assert tab.raw_table.rowCount() >= 4
    assert tab.copy_btn.isEnabled()
    assert calls == []  # ZERO tile fetches on parse or display!

    # Copy coordinates
    tab.copy_btn.click()
    assert QApplication.clipboard().text() == "40.712776, -74.005974"

    # Decline external map disclosure -> ZERO fetches
    dialog_log.confirm_answer = False
    tab.load_map_btn.click()
    assert calls == []
    assert tab.map_image.isHidden()
    assert "No network request was sent" in tab.map_status.text()

    # Accept external map disclosure -> fetches 2x2 tiles in memory, logs audit event
    dialog_log.confirm_answer = True
    tab.load_map_btn.click()
    assert len(calls) == 4
    assert all(c[3] is True for c in calls)
    assert not tab.map_image.isHidden()
    assert not tab.map_image.pixmap().isNull()

    # Clear removes preview
    tab.clear_map_btn.click()
    assert tab.map_image.isHidden()

    # Audit trail recorded both GPS check and opt-in external map preview
    hist = w.evidence_page.details.history
    hist_text = "\n".join(hist.item(i).text() for i in range(hist.count()))
    assert "GPS checked (valid GPS)" in hist_text
    assert "External map preview loaded (opt-in)" in hist_text


def test_gps_tab_missing_gps_exact_message(qtbot, tmp_path):
    w = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w)
    w.show()
    case = w.cases.create("No GPS case", SCOPE)
    w.set_active_case(case.id)
    ev = w.evidence.import_file(case.id, make_valid_jpeg(tmp_path / "plain.jpg"))
    w.navigate("Evidence")
    w.evidence_page.reload(select_id=ev.id)
    w.evidence_page.details.tabs.setCurrentIndex(3)

    tab = w.evidence_page.details.gps_tab
    tab.parse_btn.click()
    assert NO_GPS_MESSAGE in tab.status_label.text()
    assert tab.coords_host.isHidden()
    assert tab.raw_table.isHidden()
    assert not tab.copy_btn.isEnabled()


def test_settings_external_map_opt_in_and_status_bar(qtbot, tmp_path, dialog_log):
    w = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w)
    sp = w.pages["Settings"]
    assert not w.settings.allow_external_map
    assert "Offline" in w.offline_label.text()

    # Declining disclosure leaves it disabled
    dialog_log.confirm_answer = False
    sp.ext_map_checkbox.click()
    assert not w.settings.allow_external_map
    assert not sp.ext_map_checkbox.isChecked()

    # Accepting disclosure enables opt-in and persists across restart
    dialog_log.confirm_answer = True
    sp.ext_map_checkbox.click()
    assert w.settings.allow_external_map
    assert "opt-in enabled" in w.offline_label.text()
    w.close()

    w2 = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w2)
    assert w2.settings.allow_external_map
    assert "opt-in enabled" in w2.offline_label.text()
