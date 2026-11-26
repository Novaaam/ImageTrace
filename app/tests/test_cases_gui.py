import pytest
from PySide6.QtCore import Qt

from imagetrace import app as appmod
from imagetrace import paths
from imagetrace.ui import dialogs
from imagetrace.ui.case_dialog import CaseDialog
from imagetrace.ui.main_window import MainWindow

SCOPE = "Synthetic GUI test scope."


@pytest.fixture
def window(qtbot, tmp_path):
    w = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w)
    w.show()
    w.navigate("Cases")
    return w


def _create(window, monkeypatch, title="GUI case", **extra):
    page = window.cases_page
    values = {"title": title, "scope_text": SCOPE, "notes": "", "tags": "", **extra}
    monkeypatch.setattr(page, "run_case_dialog", lambda case: values)
    page.new_case()
    return page.selected_case()


def test_empty_state(window):
    page = window.cases_page
    assert page.table.rowCount() == 0
    assert "No cases yet" in page.empty_label.text()
    assert not page.edit_btn.isEnabled()


def test_create_case_appears_selected_with_details(window, monkeypatch):
    case = _create(window, monkeypatch, tags="alpha, beta", notes="note text")
    page = window.cases_page
    assert page.table.rowCount() == 1
    assert page.table.item(0, 1).text() == "GUI case"
    assert page.details.fields["id"].text() == case.id
    assert page.details.fields["tags"].text() == "alpha, beta"
    assert page.details.history.count() == 1
    assert "Case created" in page.details.history.item(0).text()
    assert page.empty_label.isHidden()


def test_cancelled_dialog_creates_nothing(window, monkeypatch):
    page = window.cases_page
    monkeypatch.setattr(page, "run_case_dialog", lambda case: None)
    page.new_case()
    assert window.cases.count() == 0


def test_validation_error_shown(window, monkeypatch, dialog_log):
    page = window.cases_page
    monkeypatch.setattr(
        page, "run_case_dialog", lambda c: {"title": "x", "scope_text": "", "notes": "", "tags": ""}
    )
    page.new_case()
    assert dialog_log.kinds() == ["error"]
    assert window.cases.count() == 0


def test_edit_case(window, monkeypatch):
    case = _create(window, monkeypatch)
    page = window.cases_page
    monkeypatch.setattr(
        page,
        "run_case_dialog",
        lambda c: {"title": "Renamed", "scope_text": SCOPE, "notes": "n", "tags": "t"},
    )
    page.edit_case()
    assert window.cases.get(case.id).title == "Renamed"
    assert page.details.history.count() == 2
    assert "Case edited" in page.details.history.item(0).text()


def test_set_active_updates_top_bar_and_persists(qtbot, window, monkeypatch, tmp_path):
    case = _create(window, monkeypatch, title="Active one")
    page = window.cases_page
    page.active_btn.click()
    assert window.settings.active_case_id == case.id
    assert case.id in window.active_case_label.text()
    assert "Active one" in window.active_case_label.text()
    assert not page.active_btn.isEnabled()
    window.close()
    w2 = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w2)
    assert case.id in w2.active_case_label.text()


def test_archive_requires_confirmation(window, monkeypatch, dialog_log):
    case = _create(window, monkeypatch)
    page = window.cases_page
    dialog_log.confirm_answer = False
    page.archive_btn.click()
    assert window.cases.get(case.id).status == "open"
    dialog_log.confirm_answer = True
    page.archive_btn.click()
    assert window.cases.get(case.id).status == "archived"
    assert page.table.rowCount() == 0  # hidden by default
    page.show_archived.setChecked(True)
    assert page.table.rowCount() == 1


def test_archiving_active_case_clears_it(window, monkeypatch):
    case = _create(window, monkeypatch)
    page = window.cases_page
    page.active_btn.click()
    page.archive_btn.click()
    assert window.settings.active_case_id is None
    assert window.active_case_label.text() == "Active case: none"
    page.show_archived.setChecked(True)
    page.reload(select_id=case.id)
    assert page.archive_btn.text() == "Reopen"
    assert not page.active_btn.isEnabled()
    page.archive_btn.click()
    assert window.cases.get(case.id).status == "open"


def test_filter_and_top_search(window, monkeypatch):
    _create(window, monkeypatch, title="Harbour set")
    _create(window, monkeypatch, title="Receipt scan")
    page = window.cases_page
    page.set_filter("harb")
    assert page.table.rowCount() == 1
    page.set_filter("zzz")
    assert page.table.rowCount() == 0
    assert "No cases match" in page.empty_label.text()
    window.navigate("Dashboard")
    window.search.setText("receipt")
    window._on_search()
    assert window.current_page_name() == "Cases"
    assert page.table.rowCount() == 1
    assert page.table.item(0, 1).text() == "Receipt scan"


def test_dashboard_lists_recent_cases(window, monkeypatch):
    case = _create(window, monkeypatch, title="Recent one")
    window.navigate("Dashboard")
    dash = window.pages["Dashboard"]
    assert dash.cases_empty.isHidden()
    texts = [dash.cases_box.itemAt(i).widget().text() for i in range(dash.cases_box.count())]
    assert any(case.id in t and "Recent one" in t for t in texts)


def test_html_in_title_is_shown_as_plain_text(window, monkeypatch):
    _create(window, monkeypatch, title="<b>bold</b> <img src=x>")
    page = window.cases_page
    assert page.details.fields["status"].textFormat() == Qt.TextFormat.PlainText
    assert window.cases.list()[0].title == "<b>bold</b> <img src=x>"


def test_backup_verify_restore_via_window(window, monkeypatch, dialog_log, tmp_path):
    first = _create(window, monkeypatch, title="Before backup")
    result = window.backup_now()
    assert result is not None and result.path.is_file()
    assert dialog_log.kinds()[-1] == "info"
    assert window.verify_database() is True
    _create(window, monkeypatch, title="After backup")
    assert window.cases.count() == 2
    dialog_log.open_file = result.path
    assert window.restore_from_backup() is True
    assert [c.id for c in window.cases.list(include_archived=True)] == [first.id]
    assert window.cases_page.table.rowCount() == 1
    assert list((tmp_path / "backups").glob("imagetrace-pre-restore-*.sqlite"))


def test_restore_cancelled_changes_nothing(window, monkeypatch, dialog_log):
    _create(window, monkeypatch)
    result = window.backup_now()
    _create(window, monkeypatch, title="second")
    dialog_log.confirm_answer = False
    assert window.restore_from_backup(result.path) is False
    assert window.cases.count() == 2


# --- CaseDialog ---------------------------------------------------------------------------
def test_case_dialog_requires_title_scope_and_confirmation(qtbot):
    dlg = CaseDialog()
    qtbot.addWidget(dlg)
    assert not dlg.ok_button.isEnabled()
    dlg.title_edit.setText("A title")
    dlg.scope_edit.setPlainText("Scope text")
    assert not dlg.ok_button.isEnabled()
    dlg.authorized.setChecked(True)
    assert dlg.ok_button.isEnabled()
    assert dlg.values()["title"] == "A title"
    assert "minimisation" in dlg.findChild(type(dlg.error_label), "Banner").text().lower()


def test_case_dialog_shows_validation_error(qtbot):
    dlg = CaseDialog()
    qtbot.addWidget(dlg)
    dlg.title_edit.setText("ok")
    dlg.scope_edit.setPlainText("scope")
    dlg.authorized.setChecked(True)
    dlg.tags_edit.setText("x" * 50)
    dlg.accept()
    assert dlg.result() == 0
    assert "too long" in dlg.error_label.text()


# --- start-up storage prompt -------------------------------------------------------------
def test_resolve_data_root_available(tmp_path):
    assert appmod.resolve_data_root(tmp_path) == tmp_path


def test_resolve_data_root_quit(monkeypatch, tmp_path):
    monkeypatch.setattr(dialogs, "ask_storage_choice", lambda p, m: "quit")
    assert appmod.resolve_data_root(tmp_path / "missing") is None


def test_resolve_data_root_limited_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(dialogs, "ask_storage_choice", lambda p, m: "limited")
    missing = tmp_path / "missing"
    assert appmod.resolve_data_root(missing) == missing
    assert not missing.exists()


def test_resolve_data_root_choose_and_remember(monkeypatch, tmp_path, dialog_log):
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    monkeypatch.setattr(dialogs, "ask_storage_choice", lambda p, m: "choose")
    monkeypatch.setattr(dialogs, "choose_directory", lambda *a: chosen)
    assert appmod.resolve_data_root(tmp_path / "missing") == chosen
    assert paths.configured_data_root() == chosen
