import re

import pytest
from PySide6.QtCore import Qt

from imagetrace.db import database_path
from imagetrace.ui.main_window import NAV_ITEMS, MainWindow

FAKE_DATA_PATTERNS = re.compile(
    r"lorem|ipsum|john doe|jane doe|sample case|demo case"
    r"|\b[0-9a-f]{64}\b"  # a SHA-256-looking value
    r"|\d+\.\d{4,},\s*-?\d+\.\d{4,}",  # a coordinate-looking pair
    re.IGNORECASE,
)


@pytest.fixture
def window(qtbot, tmp_path):
    w = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w)
    w.show()
    return w


def test_launches_with_title(window):
    assert window.windowTitle().startswith("ImageTrace")
    assert "9va.net" in window.windowTitle()
    assert not window.brand_logo_label.pixmap().isNull()
    assert not window.windowIcon().isNull()
    assert window.current_page_name() == "Dashboard"
    assert window.db is not None


def test_sidebar_has_all_sections(window):
    items = [window.sidebar.item(i).text() for i in range(window.sidebar.count())]
    assert items == list(NAV_ITEMS)
    assert items == ["Dashboard", "Cases", "Evidence", "Analysis", "Reports", "Settings", "About"]


@pytest.mark.parametrize("name", NAV_ITEMS)
def test_navigation_by_click(window, qtbot, name):
    row = NAV_ITEMS.index(name)
    rect = window.sidebar.visualItemRect(window.sidebar.item(row))
    qtbot.mouseClick(window.sidebar.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert window.current_page_name() == name


def test_navigation_by_keyboard_shortcut(window, qtbot):
    for i, name in enumerate(NAV_ITEMS):
        qtbot.keyClick(window, Qt.Key(Qt.Key.Key_1 + i), Qt.KeyboardModifier.ControlModifier)
        assert window.current_page_name() == name


def test_navigation_by_arrow_keys(window, qtbot):
    window.sidebar.setFocus()
    qtbot.keyClick(window.sidebar, Qt.Key.Key_Down)
    assert window.current_page_name() == "Cases"


def _all_text(widget):
    from PySide6.QtWidgets import QLabel, QLineEdit

    texts = [lbl.text() for lbl in widget.findChildren(QLabel)]
    texts += [e.placeholderText() for e in widget.findChildren(QLineEdit)]
    return "\n".join(texts)


def test_no_fake_forensic_data(window):
    text = _all_text(window)
    assert not FAKE_DATA_PATTERNS.search(text), FAKE_DATA_PATTERNS.search(text)


def test_dashboard_empty_states(window):
    dash = window.pages["Dashboard"]
    assert "No cases yet" in dash.cases_empty.text()
    assert "No evidence imported" in dash.evidence_empty.text()
    assert "OK" in dash.storage_state.text()


def test_all_sidebar_pages_implemented(window):
    from imagetrace.ui.widgets import PlaceholderPage

    for name, page in window.pages.items():
        assert not isinstance(page, PlaceholderPage), f"{name} is still a PlaceholderPage"
    reports_text = _all_text(window.pages["Reports"])
    assert "Extracted facts" in reports_text


def test_top_bar_shows_case_and_storage(window, tmp_path):
    assert window.active_case_label.text() == "Active case: none"
    assert str(tmp_path) in window.storage_label.text()
    assert "Ready" in window.status_indicator.text()
    assert window.search.isEnabled()


def test_theme_change_persists(qtbot, window, tmp_path):
    combo = window.pages["Settings"].theme_combo
    combo.setCurrentIndex(combo.findData("dark"))
    assert window.resolved_theme == "dark"
    window.close()
    w2 = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w2)
    assert w2.resolved_theme == "dark"
    assert w2.pages["Settings"].theme_combo.currentData() == "dark"


def test_analyst_label_saved(qtbot, window, tmp_path):
    page = window.pages["Settings"]
    page.analyst_edit.setText("  Examiner 2 ")
    page.analyst_save.click()
    assert page.analyst_edit.text() == "Examiner 2"
    window.close()
    w2 = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w2)
    assert w2.settings.analyst_label == "Examiner 2"


def test_missing_storage_shows_error_and_writes_nothing(qtbot, tmp_path):
    missing = tmp_path / "missing-root"
    w = MainWindow(data_root=missing)
    qtbot.addWidget(w)
    assert w.db is None
    assert "unavailable" in w.status_indicator.text().lower()
    assert "unavailable" in w.storage_label.text()
    assert "not found" in w.pages["Dashboard"].storage_state.text()
    assert "unavailable" in w.pages["Dashboard"].cases_empty.text()
    assert not w.search.isEnabled()
    assert not w.pages["Settings"].backup_btn.isEnabled()
    w.set_theme("dark")  # must not create the missing root
    assert not missing.exists()


def test_corrupt_database_shows_error_state(qtbot, tmp_path):
    (tmp_path / "database").mkdir()
    database_path(tmp_path).write_bytes(b"not sqlite" * 500)
    w = MainWindow(data_root=tmp_path)
    qtbot.addWidget(w)
    assert w.db is None and w.db_error
    assert "Database error" in w.status_indicator.text()
    assert database_path(tmp_path).read_bytes().startswith(b"not sqlite")  # left untouched
