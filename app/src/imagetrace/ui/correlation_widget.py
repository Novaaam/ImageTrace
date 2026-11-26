"""Cross-evidence Case Correlation, Timeline, and Multi-Image Offline GPS Map widget (v0.3.0)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from imagetrace.correlation import CaseCorrelationReport, CaseGpsPoint
from imagetrace.ui.widgets import label, set_state

CLUSTER_COLS = ("Cluster type", "Classification", "Key", "Items", "Details")
TIMELINE_COLS = ("Evidence ID", "Filename", "Capture time", "Device", "Shutter", "Anomaly")


class CaseGpsMapPlot(QWidget):
    """100% offline QPainter world coordinate graticule plotting all geotagged case items."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: tuple[CaseGpsPoint, ...] = ()
        self.setMinimumHeight(175)
        self.setMaximumHeight(220)

    def set_points(self, points: tuple[CaseGpsPoint, ...]) -> None:
        self._points = points
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # Qt API name
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        bg = pal.base().color()
        fg = pal.text().color()
        muted = pal.placeholderText().color()
        accent = pal.highlight().color()

        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, bg)
        p.setPen(QPen(muted, 1))
        p.drawRect(0, 0, w - 1, h - 1)

        if not self._points:
            p.setPen(muted)
            p.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "No embedded GPS coordinates found across items in this case.",
            )
            p.end()
            return

        left, right, top, bottom = 36, 16, 16, 22
        pw = max(w - left - right, 20)
        ph = max(h - top - bottom, 20)

        # Equirectangular graticule (-180..180, -90..90)
        grid = QPen(muted, 1, Qt.PenStyle.DotLine)
        p.setPen(grid)
        for lon in (-120, -60, 0, 60, 120):
            x = left + ((lon + 180.0) / 360.0) * pw
            p.drawLine(int(x), top, int(x), top + ph)
        for lat in (-60, -30, 0, 30, 60):
            y = top + ((90.0 - lat) / 180.0) * ph
            p.drawLine(left, int(y), left + pw, int(y))

        p.setPen(QPen(fg, 1))
        p.drawRect(left, top, pw, ph)

        # Plot points with index markers
        for idx, pt in enumerate(self._points, start=1):
            x = left + ((pt.longitude + 180.0) / 360.0) * pw
            y = top + ((90.0 - pt.latitude) / 180.0) * ph
            p.setBrush(QColor("#D97706") if idx == 1 else accent)
            p.setPen(QPen(fg, 1))
            p.drawEllipse(QPointF(x, y), 5.0, 5.0)
            p.setPen(fg)
            p.drawText(int(x + 7), int(y + 4), f"#{idx} {pt.evidence_id[-6:]}")

        p.setPen(muted)
        p.drawText(
            left, h - 6, f"Offline world lat/lon plot · {len(self._points)} geotagged item(s)"
        )
        p.end()


class CaseCorrelationWidget(QWidget):
    """Cross-evidence Lineage, Hardware/DQT/dHash Clusters, Timeline & Multi-Image GPS."""

    open_evidence_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        hdr = QHBoxLayout()
        self.summary_label = label("Click “Correlate case” to connect evidence items.", muted=True)
        hdr.addWidget(self.summary_label, 1)
        self.correlate_btn = QPushButton("Correlate case")
        self.correlate_btn.setObjectName("Primary")
        hdr.addWidget(self.correlate_btn)
        lay.addLayout(hdr)

        self.anomaly_label = label("", state="warn")
        self.anomaly_label.setVisible(False)
        lay.addWidget(self.anomaly_label)

        lay.addWidget(label("XMP Document Lineage & Derivative Links", obj="CardTitle"))
        self.lineage_list = QListWidget()
        self.lineage_list.setObjectName("History")
        self.lineage_list.setAccessibleName("Document lineage links")
        self.lineage_list.setMaximumHeight(95)
        self.lineage_list.setWordWrap(True)
        lay.addWidget(self.lineage_list)

        lay.addWidget(
            label("Shared Device, Serial, DQT & Visual Similarity Clusters", obj="CardTitle")
        )
        self.clusters_table = QTableWidget(0, len(CLUSTER_COLS))
        self.clusters_table.setHorizontalHeaderLabels(CLUSTER_COLS)
        self.clusters_table.setAccessibleName("Correlation clusters")
        self.clusters_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.clusters_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.clusters_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.clusters_table.verticalHeader().setVisible(False)
        self.clusters_table.setShowGrid(False)
        self.clusters_table.setMinimumHeight(110)
        c_hdr = self.clusters_table.horizontalHeader()
        c_hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        c_hdr.setStretchLastSection(True)
        for col, width in ((0, 145), (1, 145), (2, 130), (3, 150)):
            self.clusters_table.setColumnWidth(col, width)
        lay.addWidget(self.clusters_table)

        lay.addWidget(
            label("Chronological Case Timeline (double-click row to inspect)", obj="CardTitle")
        )
        self.timeline_table = QTableWidget(0, len(TIMELINE_COLS))
        self.timeline_table.setHorizontalHeaderLabels(TIMELINE_COLS)
        self.timeline_table.setAccessibleName("Case chronological timeline")
        self.timeline_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.timeline_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.timeline_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.timeline_table.verticalHeader().setVisible(False)
        self.timeline_table.setShowGrid(False)
        self.timeline_table.setMinimumHeight(120)
        t_hdr = self.timeline_table.horizontalHeader()
        t_hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        t_hdr.setStretchLastSection(True)
        for col, width in ((0, 130), (1, 160), (2, 150), (3, 140), (4, 68)):
            self.timeline_table.setColumnWidth(col, width)
        self.timeline_table.cellDoubleClicked.connect(self._on_timeline_double_click)
        lay.addWidget(self.timeline_table)

        self.gps_title = label("Case Embedded GPS Overview (Offline Plot)", obj="CardTitle")
        self.gps_plot = CaseGpsMapPlot()
        self.gps_title.setVisible(False)
        self.gps_plot.setVisible(False)
        lay.addWidget(self.gps_title)
        lay.addWidget(self.gps_plot)

    def clear_view(self, message: str = "") -> None:
        self.summary_label.setText(message or "Select an active case to run correlation.")
        set_state(self.summary_label, None)
        self.anomaly_label.setVisible(False)
        self.lineage_list.clear()
        self.clusters_table.setRowCount(0)
        self.timeline_table.setRowCount(0)
        self.gps_title.setVisible(False)
        self.gps_plot.setVisible(False)

    def render_report(self, rep: CaseCorrelationReport) -> None:
        self.summary_label.setText(
            f"Case {rep.case_id}: {rep.evidence_count} item(s) · "
            f"{len(rep.lineage_links)} lineage link(s) · {len(rep.clusters)} cluster(s) · "
            f"{len(rep.gps_points)} geotagged · {len(rep.anomalies)} anomaly/anomalies"
        )
        set_state(self.summary_label, "warn" if rep.anomalies else "ok")

        if rep.anomalies:
            self.anomaly_label.setText("Timeline anomalies: " + " | ".join(rep.anomalies[:4]))
            self.anomaly_label.setVisible(True)
        else:
            self.anomaly_label.setVisible(False)

        self.lineage_list.clear()
        if not rep.lineage_links:
            self.lineage_list.addItem(
                "No XMP DocumentID / OriginalDocumentID derivative links detected across items."
            )
        for lnk in rep.lineage_links:
            self.lineage_list.addItem(
                f"{lnk.source_id} ({lnk.source_name})  ->  {lnk.target_id} ({lnk.target_name})  "
                f"[{lnk.relationship}: {lnk.shared_id}]"
            )

        self.clusters_table.clearContents()
        self.clusters_table.setRowCount(len(rep.clusters))
        for r, cl in enumerate(rep.clusters):
            cells = (
                cl.cluster_type,
                cl.classification,
                cl.label,
                ", ".join(cl.evidence_ids),
                cl.detail,
            )
            for c, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setToolTip(txt)
                self.clusters_table.setItem(r, c, item)

        self.timeline_table.clearContents()
        self.timeline_table.setRowCount(len(rep.timeline))
        for r, te in enumerate(rep.timeline):
            cells = (
                te.evidence_id,
                te.filename,
                te.capture_time,
                te.device,
                str(te.shutter_count) if te.shutter_count is not None else "—",
                te.anomaly or "OK",
            )
            for c, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setData(Qt.ItemDataRole.UserRole, te.evidence_id)
                item.setToolTip(txt)
                self.timeline_table.setItem(r, c, item)

        has_gps = bool(rep.gps_points)
        self.gps_title.setVisible(has_gps)
        self.gps_plot.setVisible(has_gps)
        self.gps_plot.set_points(rep.gps_points)

    def _on_timeline_double_click(self, row: int, _col: int) -> None:
        cell = self.timeline_table.item(row, 0)
        if cell is not None:
            eid = cell.data(Qt.ItemDataRole.UserRole)
            if isinstance(eid, str) and eid:
                self.open_evidence_requested.emit(eid)
