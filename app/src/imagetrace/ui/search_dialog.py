"""Global offline search dialog across Cases, Evidence, Origin & Trace, Metadata, OCR & Notes."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from imagetrace.correlation import SearchHit
from imagetrace.ui.widgets import label

SEARCH_COLS = ("Source", "Item", "Match snippet")
CATEGORIES = ("All", "Case", "Evidence", "Origin & Trace", "Metadata", "OCR", "Analyst note")


class GlobalSearchDialog(QDialog):
    """Unified Ctrl+K search dialog returning the selected SearchHit on acceptance."""

    def __init__(
        self,
        search_fn: Callable[[str, str], list[SearchHit]],
        *,
        initial_query: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._search_fn = search_fn
        self._hits: list[SearchHit] = []
        self.selected_hit: SearchHit | None = None

        self.setWindowTitle("Global Search (Offline)")
        self.resize(780, 480)
        self.setMinimumSize(560, 360)

        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.addWidget(
            label(
                "Search across Cases, Evidence filenames/hashes, Origin & Trace devices/software, "
                "Metadata tags, OCR text, and Analyst notes.",
                muted=True,
            )
        )

        top = QHBoxLayout()
        top.setSpacing(6)
        self.query_edit = QLineEdit(initial_query)
        self.query_edit.setPlaceholderText("Type keyword, camera model, Photoshop, SHA-256, note…")
        self.query_edit.setAccessibleName("Global search query")
        self.query_edit.setClearButtonEnabled(True)
        self.query_edit.returnPressed.connect(self.run_search)
        self.query_edit.textChanged.connect(lambda _t: self.run_search())
        self.cat_combo = QComboBox()
        self.cat_combo.setAccessibleName("Search category")
        for c in CATEGORIES:
            self.cat_combo.addItem(c, c)
        self.cat_combo.currentIndexChanged.connect(lambda _i: self.run_search())
        self.search_btn = QPushButton("Search")
        self.search_btn.setObjectName("Primary")
        self.search_btn.clicked.connect(self.run_search)
        top.addWidget(self.query_edit, 1)
        top.addWidget(self.cat_combo)
        top.addWidget(self.search_btn)
        lay.addLayout(top)

        self.count_label = label("", muted=True)
        lay.addWidget(self.count_label)

        self.table = QTableWidget(0, len(SEARCH_COLS))
        self.table.setHorizontalHeaderLabels(SEARCH_COLS)
        self.table.setAccessibleName("Search results")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(1, 220)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._open_selected())
        lay.addWidget(self.table, 1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        self.open_btn = QPushButton("Jump to result")
        self.open_btn.setObjectName("Primary")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_selected)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        foot.addWidget(self.open_btn)
        foot.addWidget(close_btn)
        lay.addLayout(foot)

        self.table.itemSelectionChanged.connect(
            lambda: self.open_btn.setEnabled(bool(self.table.selectionModel().selectedRows()))
        )
        if initial_query.strip():
            self.run_search()

    def run_search(self) -> None:
        q = self.query_edit.text().strip()
        cat = str(self.cat_combo.currentData() or "All")
        if not q:
            self._hits = []
            self.table.setRowCount(0)
            self.count_label.setText("Enter a search term to scan the workspace offline.")
            self.open_btn.setEnabled(False)
            return
        self._hits = self._search_fn(q, cat)
        self.table.clearContents()
        self.table.setRowCount(len(self._hits))
        for r, hit in enumerate(self._hits):
            for c, txt in enumerate((hit.kind, hit.title, hit.snippet)):
                item = QTableWidgetItem(txt)
                item.setData(Qt.ItemDataRole.UserRole, r)
                item.setToolTip(txt)
                self.table.setItem(r, c, item)
        self.count_label.setText(f"{len(self._hits)} matching result(s)")
        if self._hits:
            self.table.selectRow(0)
        else:
            self.open_btn.setEnabled(False)

    def _open_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        idx = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        if isinstance(idx, int) and 0 <= idx < len(self._hits):
            self.selected_hit = self._hits[idx]
            self.accept()
