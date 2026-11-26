"""GUI tests for the v0.2.0 Origin & Trace tab and batch worker integration."""

from __future__ import annotations

import struct

from PySide6.QtGui import QDesktopServices

from imagetrace.audit import list_events
from imagetrace.provenance import inspect_binary_structure
from imagetrace.ui import dialogs
from imagetrace.ui.batch_workers import BatchAnalysisWorker
from imagetrace.ui.main_window import MainWindow
from synthetic_images import make_valid_jpeg

SCOPE = "Authorized synthetic scope for Origin & Trace GUI test."


def _inject_xmp_and_adobe(jpeg_path):
    raw = jpeg_path.read_bytes()
    xmp = (
        b"http://ns.adobe.com/xap/1.0/\x00"
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"'
        b' xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"'
        b' xmlns:stEvt="http://ns.adobe.com/xap/1.0/sType/ResourceEvent#">'
        b'<rdf:Description xmpMM:DocumentID="xmp.did:gui-test-01">'
        b"<xmpMM:History><rdf:Seq>"
        b'<rdf:li stEvt:action="saved" stEvt:when="2026-05-10T15:00:00Z"'
        b' stEvt:softwareAgent="Adobe Photoshop 25.0 (Windows)"/>'
        b"</rdf:Seq></xmpMM:History>"
        b"</rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    seg1 = b"\xff\xe1" + struct.pack(">H", len(xmp) + 2) + xmp
    adobe = b"Photoshop 3.0\x008BIM\x04\x04\x00\x00\x00\x00"
    seg2 = b"\xff\xed" + struct.pack(">H", len(adobe) + 2) + adobe
    jpeg_path.write_bytes(raw[:2] + seg1 + seg2 + raw[2:])


def test_origin_trace_tab_run_ela_strip_and_manual_reverse_search(
    qtbot, tmp_path, monkeypatch, dialog_log
):
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    win.show()

    case = win.cases.create("Origin GUI case", SCOPE)
    win.set_active_case(case.id)
    src = make_valid_jpeg(tmp_path / "edited_in_ps.jpg", (220, 160))
    _inject_xmp_and_adobe(src)
    ev = win.evidence.import_file(case.id, src)

    win.navigate("Evidence")
    win.evidence_page.reload(select_id=ev.id)
    details = win.evidence_page.details
    assert details.tabs.tabText(1).replace("&&", "&") == "Origin & Trace"
    details.tabs.setCurrentIndex(1)

    tab = details.provenance_tab
    assert tab.summary_host.isHidden()
    assert tab.ela_host.isHidden()

    # 1. Run Origin & Trace
    tab.run_btn.click()
    assert not tab.summary_host.isHidden()
    assert "Adobe Photoshop" in tab.fields["software"].text()
    assert "xmp.did:gui-test-01" in tab.fields["doc_ids"].text()
    assert tab.timeline_table.rowCount() >= 2
    assert tab.signals_table.rowCount() >= 2

    # 2. On-demand ELA preview
    tab.ela_btn.click()
    assert not tab.ela_host.isHidden()
    assert not tab.ela_image.pixmap().isNull()
    assert "Mean diff:" in tab.ela_stats.text()

    # 3. Export metadata-stripped copy
    dest = tmp_path / "safe_stripped.jpg"
    monkeypatch.setattr(dialogs, "choose_save_file", lambda *a, **k: dest)
    tab.strip_btn.click()
    assert dest.is_file()
    assert inspect_binary_structure(dest)["raw_xmp_packets"] == []

    # 4. External reverse search: strictly manual + disclosure confirmation
    opened_urls: list[str] = []
    monkeypatch.setattr(
        QDesktopServices, "openUrl", lambda u: opened_urls.append(u.toString()) or True
    )

    dialog_log.confirm_answer = False
    tab.reverse_buttons[0].click()  # Google Lens declined
    assert opened_urls == []

    dialog_log.confirm_answer = True
    tab.reverse_buttons[0].click()  # Google Lens accepted
    assert opened_urls == ["https://lens.google.com/"]

    events = list_events(win.db.con, evidence_id=ev.id)
    types = [e.event_type for e in events]
    assert "origin_traced" in types
    assert "reverse_search_link_opened" in types


def test_batch_analysis_worker_also_runs_origin_trace(qtbot, tmp_path):
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    case = win.cases.create("Batch origin case", SCOPE)
    win.set_active_case(case.id)
    ev = win.evidence.import_file(case.id, make_valid_jpeg(tmp_path / "b1.jpg", (120, 90)))

    worker = BatchAnalysisWorker(
        win.db.path,
        win.data_root,
        [ev.id],
        actor_label="Analyst",
        exiftool_path=win.settings.exiftool_path,
    )
    with qtbot.waitSignal(worker.completed, timeout=20000) as sig:
        worker.start()
    worker.wait(5000)

    report = sig.args[0]
    assert report.succeeded == [ev.id]
    rep = win.provenance.latest_report(ev.id)
    assert rep is not None
    assert len(rep.timeline) >= 1
