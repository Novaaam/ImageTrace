from PySide6.QtWidgets import QDialog

from imagetrace.audit import list_events
from imagetrace.ui import dialogs
from imagetrace.ui.main_window import MainWindow
from imagetrace.ui.notes_tab import NoteDialog
from synthetic_images import make_valid_jpeg

SCOPE = "Synthetic GUI notes test scope."


def test_note_dialog_defaults_and_source_requirement(qtbot):
    dlg = NoteDialog("Examiner 1")
    qtbot.addWidget(dlg)
    dlg.show()

    # Defaults conservatively to 'Analyst observation' (never 'Confirmed from file')
    assert dlg.confidence_combo.currentData() == "Analyst observation"
    assert "*" not in dlg.source_caption.text()
    assert dlg.source_caption.buddy() is dlg.source_edit

    # Empty observation blocked with inline error
    dlg.accept()
    assert dlg.result() != QDialog.DialogCode.Accepted
    assert dlg.error_label.isVisible()

    # Switching to 'Confirmed from file' requires source reference
    dlg.confidence_combo.setCurrentIndex(dlg.confidence_combo.findData("Confirmed from file"))
    assert "*" in dlg.source_caption.text()
    dlg.text_edit.setPlainText("EXIF Make is SYNTH-Cam")
    dlg.accept()
    assert dlg.result() != QDialog.DialogCode.Accepted
    assert "source / citation" in dlg.error_label.text().lower()

    # Providing source allows accept
    dlg.source_edit.setText("EXIF:IFD0:Make")
    dlg.accept()
    assert dlg.result() == QDialog.DialogCode.Accepted
    vals = dlg.values()
    assert vals["confidence_label"] == "Confirmed from file"
    assert vals["source_reference"] == "EXIF:IFD0:Make"
    assert vals["note_text"] == "EXIF Make is SYNTH-Cam"


def test_notes_tab_add_revise_retract_filter_and_privacy(qtbot, tmp_path, monkeypatch):
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    win.show()
    case = win.cases.create("Notes GUI case", SCOPE)
    win.set_active_case(case.id)
    ev = win.evidence.import_file(case.id, make_valid_jpeg(tmp_path / "img.jpg"))

    win.navigate("Evidence")
    win.evidence_page.reload(select_id=ev.id)
    details = win.evidence_page.details
    details.tabs.setCurrentIndex(6)  # Analyst notes tab
    tab = details.notes_tab
    assert tab.table.rowCount() == 0
    assert tab.add_btn.isEnabled()
    assert not tab.revise_btn.isEnabled()
    assert not tab.retract_btn.isEnabled()

    marker = "SENSITIVE_GUI_NOTE_5544"
    monkeypatch.setattr(
        tab,
        "run_note_dialog",
        lambda _existing: {
            "category": "Signage / visible text",
            "confidence_label": "Confirmed from file",
            "source_reference": "Upper-left painted sign",
            "note_text": f"<b>UNTRUSTED</b> {marker}",
        },
    )
    tab.add_btn.click()

    assert tab.table.rowCount() == 1
    assert tab.detail_host.isVisible()
    assert tab.revise_btn.isEnabled()
    assert tab.retract_btn.isEnabled()
    assert tab.d_text.toPlainText() == f"<b>UNTRUSTED</b> {marker}"
    assert "Confirmed from file" in tab.d_meta.text()
    assert tab.d_source.text() == "Upper-left painted sign"
    assert "Original entry" in tab.d_rev.text()
    assert details.ov_fields["notes"].text() == "1 active note"

    # Integrity & history logs creation without leaking raw note text
    hist_text = "\n".join(details.history.item(i).text() for i in range(details.history.count()))
    assert "Analyst note added" in hist_text
    assert marker not in hist_text
    events = list_events(win.db.con, evidence_id=ev.id)
    assert marker not in str([e.details for e in events])

    # Revise selected note -> new active row supersedes #1
    monkeypatch.setattr(
        tab,
        "run_note_dialog",
        lambda _existing: {
            "category": "Signage / visible text",
            "confidence_label": "Unverified hypothesis",
            "source_reference": "",
            "note_text": "Revised hypothesis about sign wording.",
        },
    )
    tab.revise_btn.click()
    assert tab.table.rowCount() == 1
    assert "Supersedes note #1" in tab.d_rev.text()
    assert "Unverified hypothesis" in tab.d_meta.text()

    # Show superseded / retracted reveals both rows
    tab.show_history.setChecked(True)
    assert tab.table.rowCount() == 2

    # Confidence filter isolates each entry
    tab.conf_filter.setCurrentIndex(tab.conf_filter.findData("Confirmed from file"))
    assert tab.table.rowCount() == 1
    tab.table.selectRow(0)
    assert not tab.revise_btn.isEnabled()  # superseded row is read-only
    assert not tab.retract_btn.isEnabled()

    # Back to active revision and test retract (declined, then accepted)
    tab.conf_filter.setCurrentIndex(tab.conf_filter.findData("all"))
    tab.show_history.setChecked(False)
    assert tab.table.rowCount() == 1
    tab.table.selectRow(0)
    assert tab.retract_btn.isEnabled()

    monkeypatch.setattr(dialogs, "confirm", lambda *a, **k: False)
    tab.retract_btn.click()
    assert tab.table.rowCount() == 1

    monkeypatch.setattr(dialogs, "confirm", lambda *a, **k: True)
    tab.retract_btn.click()
    assert tab.table.rowCount() == 0
    assert details.ov_fields["notes"].text() == "0 active notes"

    # Retracted entry still preserved when history checkbox is enabled
    tab.show_history.setChecked(True)
    assert tab.table.rowCount() == 2


def test_audit_chain_verify_buttons_and_analysis_notes_summary(qtbot, tmp_path):
    win = MainWindow(data_root=tmp_path)
    qtbot.addWidget(win)
    case = win.cases.create("Chain verify case", SCOPE)
    win.set_active_case(case.id)
    ev = win.evidence.import_file(case.id, make_valid_jpeg(tmp_path / "c.jpg"))
    win.notes.create(
        case.id,
        ev.id,
        category="Metadata / technical finding",
        confidence_label="Confirmed from file",
        source_reference="EXIF:Orientation",
        note_text="Orientation tag is 1 (Horizontal).",
    )
    win.notes.create(
        case.id,
        ev.id,
        category="Environmental detail",
        confidence_label="Analyst observation",
        note_text="Overcast lighting with soft shadows.",
    )

    # 1. Evidence -> Integrity & history chain verification
    win.navigate("Evidence")
    win.evidence_page.reload(select_id=ev.id)
    details = win.evidence_page.details
    details.tabs.setCurrentIndex(7)
    details.verify_chain_btn.click()
    assert "consistent" in details.chain_status.text().lower()
    assert details.chain_status.property("state") == "ok"

    # 2. Cases page active notes count & chain verification
    win.navigate("Cases")
    win.cases_page.reload(select_id=case.id)
    assert win.cases_page.details.fields["notes_count"].text() == "2 active notes"
    win.cases_page.details.verify_chain_btn.click()
    assert "consistent" in win.cases_page.details.chain_status.text().lower()
    assert win.cases_page.details.chain_status.property("state") == "ok"

    # 3. Analysis page notes summary & chain verification
    win.navigate("Analysis")
    analysis = win.pages["Analysis"]
    assert "2 active analyst note(s)" in analysis.notes_status.text()
    assert "Confirmed from file: 1" in analysis.notes_status.text()
    assert "Analyst observation: 1" in analysis.notes_status.text()
    analysis.verify_chain_btn.click()
    assert "consistent" in analysis.chain_result.text().lower()
    assert analysis.chain_result.property("state") == "ok"
