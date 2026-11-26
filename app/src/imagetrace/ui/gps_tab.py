"""GPS tab on the Evidence details screen (Phase 5).

- Strictly offline by default: zero network traffic when viewing or parsing GPS.
- Displays normalized coordinates, altitude, GPS timestamp, source field, parser version,
  raw embedded GPS fields, and an offline coordinate graticule plot.
- When absent, states exactly: "No GPS coordinates found in the examined file."
- Optional external OpenStreetMap tile preview is OFF by default and requires explicit user
  consent after a clear disclosure before any request is made. Tiles stay in memory only.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from imagetrace.db import DatabaseError
from imagetrace.evidence import Evidence
from imagetrace.gps import (
    DEFAULT_MAP_ZOOM,
    EXTERNAL_MAP_DISCLOSURE,
    GPS_WARNING_BANNER,
    NO_GPS_MESSAGE,
    GpsError,
    GpsRecord,
    GpsService,
    build_offline_summary_text,
    fetch_osm_tile,
    format_dms,
    lat_lon_to_tile,
)
from imagetrace.metadata import AnalysisRun, MetadataError
from imagetrace.settings import Settings
from imagetrace.timefmt import format_local
from imagetrace.ui import dialogs
from imagetrace.ui.widgets import clear_layout, label, set_state

TileFetcher = Callable[[int, int, int, bool], bytes]
_ERRORS = (GpsError, MetadataError, DatabaseError, sqlite3.Error, OSError)


def _default_tile_fetcher(z: int, x: int, y: int, allow: bool) -> bytes:
    return fetch_osm_tile(z, x, y, allow_external_map=allow)


class LocalCoordinatePlot(QWidget):
    """100% offline world lat/lon graticule painted with QPainter (zero network traffic)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lat: float | None = None
        self._lon: float | None = None
        self.setFixedHeight(150)
        self.setMinimumWidth(260)
        self.setAccessibleName("Offline latitude and longitude plot")

    def set_coordinates(self, lat: float | None, lon: float | None) -> None:
        self._lat = lat
        self._lon = lon
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # Qt API name
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        bg = self.palette().window().color()
        dark = bg.lightness() < 128

        canvas = QColor("#182028" if dark else "#edf2f7")
        grid_c = QColor("#2d3945" if dark else "#cbd5e1")
        axis_c = QColor("#4f6174" if dark else "#94a3b8")
        text_c = QColor("#9aa4af" if dark else "#475569")
        pin_c = QColor("#ff6b6b" if dark else "#c92a2a")

        p.fillRect(rect, canvas)
        p.setPen(QPen(axis_c, 1))
        p.drawRect(rect)

        pad_l, pad_r, pad_t, pad_b = 34, 14, 14, 22
        plot = QRectF(
            rect.left() + pad_l,
            rect.top() + pad_t,
            max(rect.width() - pad_l - pad_r, 10),
            max(rect.height() - pad_t - pad_b, 10),
        )

        # Grid lines every 30 deg lat, 60 deg lon
        p.setPen(QPen(grid_c, 1, Qt.PenStyle.DotLine))
        for lon in (-120, -60, 60, 120):
            x = plot.left() + (lon + 180.0) / 360.0 * plot.width()
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        for lat in (-60, -30, 30, 60):
            y = plot.top() + (90.0 - lat) / 180.0 * plot.height()
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        # Equator & Prime Meridian
        p.setPen(QPen(axis_c, 1, Qt.PenStyle.DashLine))
        eq_y = plot.top() + plot.height() / 2.0
        pm_x = plot.left() + plot.width() / 2.0
        p.drawLine(QPointF(plot.left(), eq_y), QPointF(plot.right(), eq_y))
        p.drawLine(QPointF(pm_x, plot.top()), QPointF(pm_x, plot.bottom()))

        # Axis ticks
        p.setPen(text_c)
        font = p.font()
        font.setPointSize(7)
        p.setFont(font)
        p.drawText(QRectF(2, plot.top() - 5, pad_l - 4, 14), Qt.AlignmentFlag.AlignRight, "90°N")
        p.drawText(QRectF(2, eq_y - 7, pad_l - 4, 14), Qt.AlignmentFlag.AlignRight, "0°")
        p.drawText(QRectF(2, plot.bottom() - 9, pad_l - 4, 14), Qt.AlignmentFlag.AlignRight, "90°S")
        p.drawText(
            QRectF(plot.left(), plot.bottom() + 2, 50, 16), Qt.AlignmentFlag.AlignLeft, "180°W"
        )
        p.drawText(QRectF(pm_x - 20, plot.bottom() + 2, 40, 16), Qt.AlignmentFlag.AlignCenter, "0°")
        p.drawText(
            QRectF(plot.right() - 50, plot.bottom() + 2, 50, 16),
            Qt.AlignmentFlag.AlignRight,
            "180°E",
        )

        # Marker
        if self._lat is not None and self._lon is not None:
            px = plot.left() + (self._lon + 180.0) / 360.0 * plot.width()
            py = plot.top() + (90.0 - self._lat) / 180.0 * plot.height()
            p.setPen(QPen(pin_c, 1.5))
            p.drawLine(QPointF(px - 8, py), QPointF(px + 8, py))
            p.drawLine(QPointF(px, py - 8), QPointF(px, py + 8))
            p.setBrush(pin_c)
            p.drawEllipse(QPointF(px, py), 3.5, 3.5)
        p.end()


def render_tile_preview(
    lat: float, lon: float, zoom: int, fetcher: TileFetcher, allow_external_map: bool
) -> QPixmap:
    """Assemble a 2x2 in-memory OpenStreetMap preview centered near (lat, lon) with pin."""
    fx, fy = lat_lon_to_tile(lat, lon, zoom)
    tx0 = math.floor(fx - 0.5)
    ty0 = math.floor(fy - 0.5)

    canvas = QImage(512, 512, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#dde4ec"))
    painter = QPainter(canvas)
    try:
        for dy in range(2):
            for dx in range(2):
                raw_png = fetcher(zoom, tx0 + dx, ty0 + dy, allow_external_map)
                tile = QImage.fromData(raw_png, "PNG")
                if tile.isNull():
                    raise GpsError("Received an unreadable PNG map tile.")
                painter.drawImage(dx * 256, dy * 256, tile)

        # Pin at exact fractional coordinate offset inside the 2x2 grid
        pin_x = (fx - tx0) * 256.0
        pin_y = (fy - ty0) * 256.0
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#ffffff"), 3))
        painter.drawEllipse(QPointF(pin_x, pin_y), 7, 7)
        painter.setPen(QPen(QColor("#c92a2a"), 2))
        painter.setBrush(QColor("#e03131"))
        painter.drawEllipse(QPointF(pin_x, pin_y), 5, 5)
    finally:
        painter.end()
    return QPixmap.fromImage(canvas)


class GpsTab(QWidget):
    gps_changed = Signal()

    def __init__(
        self,
        service: GpsService,
        get_settings: Callable[[], Settings],
        *,
        tile_fetcher: TileFetcher = _default_tile_fetcher,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.get_settings = get_settings
        self.tile_fetcher = tile_fetcher
        self.current: Evidence | None = None
        self._run: AnalysisRun | None = None
        self._record: GpsRecord | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        body = QWidget()
        body.setObjectName("TabBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        lay.addWidget(label(GPS_WARNING_BANNER, obj="Banner"))

        # Actions bar
        action_row = QHBoxLayout()
        self.parse_btn = QPushButton("Check embedded GPS")
        self.parse_btn.setObjectName("Primary")
        self.parse_btn.clicked.connect(self.parse_current)
        action_row.addWidget(self.parse_btn)

        self.copy_btn = QPushButton("Copy coordinates")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_coordinates)
        action_row.addWidget(self.copy_btn)
        action_row.addStretch(1)
        lay.addLayout(action_row)

        self.status_label = label("GPS has not been checked yet.", muted=True)
        lay.addWidget(self.status_label)

        # Valid coordinates section (only shown when valid GPS coordinates exist)
        self.coords_host = QWidget()
        self.coords_host.setObjectName("FormHost")
        c_lay = QVBoxLayout(self.coords_host)
        c_lay.setContentsMargins(0, 0, 0, 0)
        c_lay.setSpacing(8)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(4)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.dec_edit = QLineEdit()
        self.dec_edit.setObjectName("Mono")
        self.dec_edit.setReadOnly(True)
        self.dec_edit.setAccessibleName("Normalized decimal coordinates")
        form.addRow(label("Decimal (lat, lon):", wrap=False, muted=True), self.dec_edit)

        self.dms_label = label("")
        form.addRow(label("DMS:", wrap=False, muted=True), self.dms_label)

        self.alt_label = label("")
        form.addRow(label("Altitude:", wrap=False, muted=True), self.alt_label)

        self.ts_label = label("")
        form.addRow(label("GPS timestamp (UTC):", wrap=False, muted=True), self.ts_label)

        self.source_label = label("")
        form.addRow(label("Source field:", wrap=False, muted=True), self.source_label)

        self.parser_label = label("")
        form.addRow(label("Parser version:", wrap=False, muted=True), self.parser_label)
        c_lay.addLayout(form)

        # Offline local plot
        c_lay.addWidget(label("Local offline coordinate plot", obj="CardTitle"))
        self.offline_plot = LocalCoordinatePlot()
        c_lay.addWidget(self.offline_plot)
        c_lay.addWidget(
            label(
                "Rendered locally with zero network requests. No coordinates are transmitted.",
                muted=True,
            )
        )

        # Optional opt-in external map preview
        c_lay.addWidget(label("Optional external map preview (opt-in)", obj="CardTitle"))
        map_row = QHBoxLayout()
        self.zoom_combo = QComboBox()
        self.zoom_combo.setAccessibleName("External map zoom level")
        self.zoom_combo.addItem("Zoom 8 (region)", 8)
        self.zoom_combo.addItem("Zoom 12 (city)", DEFAULT_MAP_ZOOM)
        self.zoom_combo.addItem("Zoom 14 (neighborhood)", 14)
        self.zoom_combo.setCurrentIndex(1)
        map_row.addWidget(self.zoom_combo)

        self.load_map_btn = QPushButton("Load external map preview…")
        self.load_map_btn.clicked.connect(self.load_external_map)
        map_row.addWidget(self.load_map_btn)

        self.clear_map_btn = QPushButton("Clear")
        self.clear_map_btn.setVisible(False)
        self.clear_map_btn.clicked.connect(self._clear_map)
        map_row.addWidget(self.clear_map_btn)
        map_row.addStretch(1)
        c_lay.addLayout(map_row)

        self.map_status = label(
            "OFF by default. External tiles (OpenStreetMap) are fetched only after explicit "
            "consent; addresses are never reverse-geocoded.",
            muted=True,
        )
        c_lay.addWidget(self.map_status)

        self.map_image = QLabel("")
        self.map_image.setTextFormat(Qt.TextFormat.PlainText)
        self.map_image.setObjectName("ThumbBox")
        self.map_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_image.setVisible(False)
        c_lay.addWidget(self.map_image)

        self.map_attrib = label(
            "Map tiles © OpenStreetMap contributors (in-memory preview only; not saved to disk).",
            muted=True,
        )
        self.map_attrib.setVisible(False)
        c_lay.addWidget(self.map_attrib)

        self.coords_host.setVisible(False)
        lay.addWidget(self.coords_host)

        # Warnings box
        self.warnings_title = label("GPS warnings & discrepancies", obj="CardTitle")
        self.warnings_title.setVisible(False)
        self.warnings_box = QVBoxLayout()
        self.warnings_box.setSpacing(4)
        lay.addWidget(self.warnings_title)
        lay.addLayout(self.warnings_box)

        # Raw embedded GPS metadata table (shown whenever any embedded GPS tags exist)
        self.raw_title = label("Raw embedded GPS metadata fields", obj="CardTitle")
        self.raw_title.setVisible(False)
        lay.addWidget(self.raw_title)

        self.raw_table = QTableWidget(0, 3)
        self.raw_table.setHorizontalHeaderLabels(("Embedded tag", "Formatted value", "Raw value"))
        self.raw_table.setAccessibleName("Raw embedded GPS fields")
        self.raw_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.raw_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.raw_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.raw_table.verticalHeader().setVisible(False)
        self.raw_table.setShowGrid(False)
        self.raw_table.setMinimumHeight(140)
        hdr = self.raw_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        self.raw_table.setColumnWidth(0, 175)
        self.raw_table.setColumnWidth(1, 145)
        self.raw_table.setVisible(False)
        lay.addWidget(self.raw_table)
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        outer.addWidget(scroll)

    # --- rendering --------------------------------------------------------------------
    def show_evidence(self, item: Evidence | None) -> None:
        self.current = item
        self._clear_map()
        if item is None:
            self._run = None
            self._record = None
            self._render_unchecked()
            return
        run, rec = self.service.latest_state(item.id)
        self._run = run
        self._record = rec
        if run is None:
            self._render_unchecked()
        else:
            self._render_state(run, rec)

    def _render_unchecked(self) -> None:
        self.parse_btn.setText("Check embedded GPS")
        self.parse_btn.setEnabled(self.current is not None)
        self.copy_btn.setEnabled(False)
        self.status_label.setText(
            "GPS has not been checked for this evidence item yet. "
            "Click “Check embedded GPS” to inspect embedded tags locally (offline)."
        )
        set_state(self.status_label, None)
        self.coords_host.setVisible(False)
        self.warnings_title.setVisible(False)
        clear_layout(self.warnings_box)
        self.raw_title.setVisible(False)
        self.raw_table.setVisible(False)
        self.raw_table.setRowCount(0)

    def _render_state(self, run: AnalysisRun, rec: GpsRecord | None) -> None:
        self.parse_btn.setText("Re-parse GPS")
        self.parse_btn.setEnabled(True)
        clear_layout(self.warnings_box)

        if rec is None:
            # No GPS present: use the exact blueprint wording.
            self.copy_btn.setEnabled(False)
            self.status_label.setText(
                f"{NO_GPS_MESSAGE} (Checked {format_local(run.finished_at)} via "
                f"{run.output.get('parser_version', run.version)})"
            )
            set_state(self.status_label, None)
            self.coords_host.setVisible(False)
            self.warnings_title.setVisible(False)
            self.raw_title.setVisible(False)
            self.raw_table.setVisible(False)
            self.raw_table.setRowCount(0)
            return

        # Populate raw embedded GPS table
        self.raw_title.setVisible(bool(rec.raw_fields))
        self.raw_table.setVisible(bool(rec.raw_fields))
        self.raw_table.setRowCount(len(rec.raw_fields))
        for row, (tag, vals) in enumerate(sorted(rec.raw_fields.items())):
            for col, text in enumerate(
                (tag, str(vals.get("formatted", "")), str(vals.get("raw", "")))
            ):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)
                self.raw_table.setItem(row, col, cell)

        # Warnings
        self.warnings_title.setVisible(bool(rec.warnings))
        for w in rec.warnings:
            self.warnings_box.addWidget(label(f"• {w}", state="warn"))

        if rec.is_valid and rec.latitude is not None and rec.longitude is not None:
            self.copy_btn.setEnabled(True)
            self.status_label.setText(
                f"Embedded GPS coordinates found · parsed {format_local(rec.extracted_at)} "
                f"via {rec.parser_version}"
            )
            set_state(self.status_label, "ok" if not rec.warnings else "warn")

            coord_str = build_offline_summary_text(rec)
            self.dec_edit.setText(coord_str)
            self.dec_edit.setCursorPosition(0)
            self.dms_label.setText(
                f"{format_dms(rec.latitude, 'lat')}, {format_dms(rec.longitude, 'lon')}"
            )
            if rec.altitude is not None:
                ref_note = "below sea level" if rec.altitude < 0 else "above sea level"
                self.alt_label.setText(f"{rec.altitude:.2f} m ({ref_note})")
            else:
                self.alt_label.setText("Not embedded")
            self.ts_label.setText(rec.gps_timestamp or "Not embedded")
            self.source_label.setText(rec.source_field)
            self.parser_label.setText(rec.parser_version)
            self.offline_plot.set_coordinates(rec.latitude, rec.longitude)
            self.coords_host.setVisible(True)
        else:
            self.copy_btn.setEnabled(False)
            self.status_label.setText(
                f"Embedded GPS tags present, but coordinates are invalid or incomplete "
                f"(checked {format_local(rec.extracted_at)} via {rec.parser_version})."
            )
            set_state(self.status_label, "warn")
            self.coords_host.setVisible(False)

    # --- actions ----------------------------------------------------------------------
    def parse_current(self) -> None:
        if self.current is None:
            return
        try:
            outcome = self.service.parse(self.current.id, extract_if_missing=True)
        except _ERRORS as exc:
            self.status_label.setText(f"GPS check failed: {exc}")
            set_state(self.status_label, "error")
            dialogs.error(self, "GPS check failed", str(exc))
            return
        self._run = outcome.run
        self._record = outcome.record
        self._render_state(outcome.run, outcome.record)
        self.gps_changed.emit()

    def _copy_coordinates(self) -> None:
        if self._record is None or not self._record.is_valid:
            return
        text = build_offline_summary_text(self._record)
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(text)
        self.status_label.setText(f"Copied coordinates to clipboard: {text}")
        set_state(self.status_label, "ok")

    def _clear_map(self) -> None:
        self.map_image.setPixmap(QPixmap())
        self.map_image.setVisible(False)
        self.map_attrib.setVisible(False)
        self.clear_map_btn.setVisible(False)
        self.map_status.setText(
            "OFF by default. External tiles (OpenStreetMap) are fetched only after explicit "
            "consent; addresses are never reverse-geocoded."
        )
        set_state(self.map_status, None)

    def load_external_map(self) -> None:
        if (
            self.current is None
            or self._record is None
            or not self._record.is_valid
            or self._record.latitude is None
            or self._record.longitude is None
        ):
            return
        settings = self.get_settings()
        # Always show explicit external-service disclosure before transmitting tile coordinates.
        prompt = EXTERNAL_MAP_DISCLOSURE
        if not settings.allow_external_map:
            prompt = (
                "External map preview is currently disabled in Settings.\n\n"
                + EXTERNAL_MAP_DISCLOSURE
            )
        if not dialogs.confirm(self, "External map service disclosure", prompt):
            self.map_status.setText("External map preview cancelled. No network request was sent.")
            set_state(self.map_status, None)
            return

        zoom = int(self.zoom_combo.currentData())
        try:
            pix = render_tile_preview(
                self._record.latitude,
                self._record.longitude,
                zoom,
                self.tile_fetcher,
                allow_external_map=True,
            )
            self.service.record_external_map_load(self.current.id, zoom)
        except _ERRORS as exc:
            self.map_status.setText(f"External map preview failed: {exc}")
            set_state(self.map_status, "error")
            dialogs.error(self, "External map preview failed", str(exc))
            return

        self.map_image.setPixmap(
            pix.scaled(
                360,
                260,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.map_image.setVisible(True)
        self.map_attrib.setVisible(True)
        self.clear_map_btn.setVisible(True)
        self.map_status.setText(
            f"Loaded OpenStreetMap tiles (zoom {zoom}) in memory after your confirmation."
        )
        set_state(self.map_status, "ok")
        self.gps_changed.emit()
