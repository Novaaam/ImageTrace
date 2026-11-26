"""Advanced Origin & Trace + Visual Forensics Lab tab on Evidence details (v0.3.0)."""

from __future__ import annotations

import contextlib
import io
import sqlite3

from PySide6.QtCore import QPointF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from imagetrace.db import DatabaseError
from imagetrace.evidence import Evidence, EvidenceError
from imagetrace.forensics import (
    FORENSICS_LAB_DISCLAIMER,
    ForensicsError,
    JpegGhostResult,
    compute_channel_view,
    compute_jpeg_ghost_curve,
    compute_luminance_gradient,
    compute_noise_residual,
    extract_embedded_thumbnail_bytes,
)
from imagetrace.provenance import (
    PROVENANCE_BANNER,
    REVERSE_SEARCH_DISCLOSURE,
    REVERSE_SEARCH_ENGINES,
    ProvenanceError,
    ProvenanceReport,
    ProvenanceService,
    compute_ela,
    save_stripped_copy,
)
from imagetrace.timefmt import format_local
from imagetrace.ui import dialogs
from imagetrace.ui.widgets import elide, label, set_state

TIMELINE_COLS = ("#", "Stage", "Device / Tool", "Platform", "Timestamp", "Action", "Source")
SIGNAL_COLS = ("Layer", "Classification", "Finding")
_ERRORS = (ProvenanceError, ForensicsError, EvidenceError, DatabaseError, sqlite3.Error, OSError)

LAB_MODES: tuple[tuple[str, str], ...] = (
    ("ela", "ELA (Error Level Analysis)"),
    ("noise", "Noise Residual (Median 3x3)"),
    ("gradient", "Luminance Gradient"),
    ("ch_r", "Channel: Red (R)"),
    ("ch_g", "Channel: Green (G)"),
    ("ch_b", "Channel: Blue (B)"),
    ("ch_cb", "Channel: Chroma (Cb)"),
    ("ch_cr", "Channel: Chroma (Cr)"),
    ("ghost", "JPEG Ghost Curve (Q=50..98)"),
)


class JpegGhostChart(QWidget):
    """Offline QPainter chart plotting JPEG recompression mean error vs. quality factor."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._result: JpegGhostResult | None = None
        self.setMinimumHeight(155)
        self.setMaximumHeight(195)

    def set_result(self, result: JpegGhostResult | None) -> None:
        self._result = result
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

        if self._result is None or not self._result.points:
            p.setPen(muted)
            p.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Run JPEG Ghost Curve to plot recompression error across Q=50..98.",
            )
            p.end()
            return

        left, right, top, bottom = 44, 16, 16, 28
        plot_w = max(w - left - right, 20)
        plot_h = max(h - top - bottom, 20)
        pts = self._result.points
        min_q = pts[0][0]
        max_q = max(pts[-1][0], min_q + 1)
        max_err = max(self._result.max_error, 0.5)

        # Horizontal gridlines
        grid_pen = QPen(muted, 1, Qt.PenStyle.DotLine)
        for step in (0.0, 0.5, 1.0):
            y = top + plot_h - step * plot_h
            p.setPen(grid_pen)
            p.drawLine(int(left), int(y), int(left + plot_w), int(y))
            p.setPen(muted)
            p.drawText(4, int(y + 4), f"{step * max_err:.1f}")

        # Axes
        p.setPen(QPen(fg, 1))
        p.drawLine(left, top, left, top + plot_h)
        p.drawLine(left, top + plot_h, left + plot_w, top + plot_h)

        # Curve polyline & points
        screen_pts: list[tuple[int, float, QPointF]] = []
        for q, err in pts:
            x = left + ((q - min_q) / (max_q - min_q)) * plot_w
            y = top + plot_h - (err / max_err) * plot_h
            screen_pts.append((q, err, QPointF(x, y)))

        p.setPen(QPen(accent, 2))
        for i in range(len(screen_pts) - 1):
            p.drawLine(screen_pts[i][2], screen_pts[i + 1][2])

        for q, _err, pt in screen_pts:
            is_min = q == self._result.min_quality
            p.setBrush(QColor("#D97706") if is_min else accent)
            p.setPen(QPen(fg if is_min else accent, 1))
            r = 5.0 if is_min else 3.0
            p.drawEllipse(pt, r, r)
            if q in (pts[0][0], pts[-1][0], self._result.min_quality):
                p.setPen(fg if is_min else muted)
                p.drawText(int(pt.x() - 12), h - 8, f"Q{q}")
        p.end()


class ProvenanceTab(QWidget):
    trace_changed = Signal()
    open_viewer_requested = Signal(str)  # compare mode key ("single", "exif_thumb", "ela", etc.)

    def __init__(self, service: ProvenanceService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.current: Evidence | None = None
        self.report: ProvenanceReport | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        body = QWidget()
        body.setObjectName("TabBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        lay.addWidget(label(PROVENANCE_BANNER, obj="Banner"))

        # Row 1: primary actions (compact labels so narrow split-pane never clips)
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self.run_btn = QPushButton("Run Origin && Trace")
        self.run_btn.setObjectName("Primary")
        self.run_btn.setToolTip(
            "Analyze camera/software tags, XMP history, DQT fingerprint, C2PA, & EXIF thumbnail"
        )
        self.run_btn.clicked.connect(self.run_trace)
        self.ela_btn = QPushButton("Forensics lab")
        self.ela_btn.setToolTip(
            "Open interactive Visual Forensics Lab (ELA, Noise Residual, Gradient, Channels, Ghost)"
        )
        self.ela_btn.clicked.connect(self.run_ela)
        self.strip_btn = QPushButton("Stripped copy…")
        self.strip_btn.setToolTip(
            "Export a metadata-stripped JPEG copy (original evidence stays unchanged)"
        )
        self.strip_btn.clicked.connect(self.export_stripped_copy)
        self.inspect_btn = QPushButton("Zoom / Compare…")
        self.inspect_btn.setToolTip(
            "Open full interactive Zoom/Pan & Side-by-Side comparison viewer"
        )
        self.inspect_btn.clicked.connect(lambda: self.open_viewer_requested.emit("single"))
        for b in (self.run_btn, self.ela_btn, self.strip_btn, self.inspect_btn):
            row1.addWidget(b)
        row1.addStretch(1)
        lay.addLayout(row1)

        self.status_label = label("", muted=True)
        lay.addWidget(self.status_label)

        # Summary host
        self.summary_host = QWidget()
        self.summary_host.setObjectName("FormHost")
        s_lay = QVBoxLayout(self.summary_host)
        s_lay.setContentsMargins(0, 0, 0, 0)
        s_lay.setSpacing(6)

        s_lay.addWidget(label("Origin & Software Assessment", obj="CardTitle"))
        self.category_label = label("", obj="CardTitle")
        s_lay.addWidget(self.category_label)
        self.verdict_label = label("")
        s_lay.addWidget(self.verdict_label)
        self.confidence_label = label("", muted=True)
        s_lay.addWidget(self.confidence_label)

        form = QFormLayout()
        form.setContentsMargins(0, 2, 0, 2)
        form.setVerticalSpacing(3)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.fields: dict[str, QLabel] = {}
        for key, caption in (
            ("device", "Capture device"),
            ("platform", "Device / OS class"),
            ("software", "Editing / export software"),
            ("hardware", "Hardware / MakerNotes"),
            ("ai_c2pa", "AI markers / C2PA"),
            ("binary", "Container & encoding"),
            ("dqt", "DQT fingerprint"),
            ("thumb", "EXIF thumbnail check"),
            ("trailing", "Trailing bytes after end"),
            ("doc_ids", "XMP Document IDs"),
        ):
            val = label("")
            self.fields[key] = val
            form.addRow(label(caption + ":", wrap=False, muted=True), val)
        s_lay.addLayout(form)

        # Conditional extraction row (EXIF thumbnail & trailing Motion Photo / payload)
        extract_row = QHBoxLayout()
        extract_row.setSpacing(6)
        self.export_thumb_btn = QPushButton("Save EXIF thumbnail…")
        self.export_thumb_btn.clicked.connect(self.export_exif_thumbnail)
        self.compare_thumb_btn = QPushButton("Compare EXIF thumb")
        self.compare_thumb_btn.clicked.connect(
            lambda: self.open_viewer_requested.emit("exif_thumb")
        )
        self.extract_trailing_btn = QPushButton("Extract trailing payload…")
        self.extract_trailing_btn.clicked.connect(self.export_trailing_payload)
        for b in (self.export_thumb_btn, self.compare_thumb_btn, self.extract_trailing_btn):
            b.setVisible(False)
            extract_row.addWidget(b)
        extract_row.addStretch(1)
        s_lay.addLayout(extract_row)

        # Side-by-side Main vs Embedded EXIF thumbnail preview (shown only when EXIF thumb exists)
        self.thumb_compare_host = QWidget()
        self.thumb_compare_host.setObjectName("FormHost")
        tc_lay = QHBoxLayout(self.thumb_compare_host)
        tc_lay.setContentsMargins(0, 2, 0, 2)
        tc_lay.setSpacing(8)
        main_col = QVBoxLayout()
        main_col.addWidget(label("Main image", muted=True))
        self.main_mini_label = QLabel("")
        self.main_mini_label.setTextFormat(Qt.TextFormat.PlainText)
        self.main_mini_label.setObjectName("ThumbBox")
        self.main_mini_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.main_mini_label.setMinimumHeight(105)
        self.main_mini_label.setMaximumHeight(130)
        main_col.addWidget(self.main_mini_label)
        exif_col = QVBoxLayout()
        exif_col.addWidget(label("Embedded EXIF thumbnail", muted=True))
        self.exif_mini_label = QLabel("")
        self.exif_mini_label.setTextFormat(Qt.TextFormat.PlainText)
        self.exif_mini_label.setObjectName("ThumbBox")
        self.exif_mini_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.exif_mini_label.setMinimumHeight(105)
        self.exif_mini_label.setMaximumHeight(130)
        exif_col.addWidget(self.exif_mini_label)
        tc_lay.addLayout(main_col, 1)
        tc_lay.addLayout(exif_col, 1)
        self.thumb_compare_host.setVisible(False)
        s_lay.addWidget(self.thumb_compare_host)

        # Timeline table (promoted high for immediate visibility)
        s_lay.addWidget(label("Embedded History Timeline (Step 1 -> End)", obj="CardTitle"))
        self.timeline_table = QTableWidget(0, len(TIMELINE_COLS))
        self.timeline_table.setHorizontalHeaderLabels(TIMELINE_COLS)
        self.timeline_table.setAccessibleName("Origin and edit timeline")
        self.timeline_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.timeline_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.timeline_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.timeline_table.verticalHeader().setVisible(False)
        self.timeline_table.setShowGrid(False)
        self.timeline_table.setMinimumHeight(125)
        t_hdr = self.timeline_table.horizontalHeader()
        t_hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        t_hdr.setStretchLastSection(True)
        for col, width in ((0, 32), (1, 105), (2, 120), (3, 95), (4, 115), (5, 165)):
            self.timeline_table.setColumnWidth(col, width)
        s_lay.addWidget(self.timeline_table)

        # Forensic signals table
        s_lay.addWidget(label("Forensic Signals & Binary Clues", obj="CardTitle"))
        self.signals_table = QTableWidget(0, len(SIGNAL_COLS))
        self.signals_table.setHorizontalHeaderLabels(SIGNAL_COLS)
        self.signals_table.setAccessibleName("Forensic signals")
        self.signals_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.signals_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.signals_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.signals_table.verticalHeader().setVisible(False)
        self.signals_table.setShowGrid(False)
        self.signals_table.setMinimumHeight(110)
        s_hdr = self.signals_table.horizontalHeader()
        s_hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        s_hdr.setStretchLastSection(True)
        self.signals_table.setColumnWidth(0, 115)
        self.signals_table.setColumnWidth(1, 130)
        s_lay.addWidget(self.signals_table)

        self.summary_host.setVisible(False)
        lay.addWidget(self.summary_host)

        # Interactive Visual Forensics Lab (replaces static ELA box; preserves ela_* attributes)
        self.ela_host = QWidget()
        self.ela_host.setObjectName("FormHost")
        e_lay = QVBoxLayout(self.ela_host)
        e_lay.setContentsMargins(0, 4, 0, 0)
        e_lay.setSpacing(6)
        e_lay.addWidget(label("Visual Forensics Lab (In-Memory Diagnostic)", obj="CardTitle"))

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(6)
        self.lab_mode_combo = QComboBox()
        self.lab_mode_combo.setAccessibleName("Forensics lab mode")
        for key, title in LAB_MODES:
            self.lab_mode_combo.addItem(title, key)
        self.lab_mode_combo.currentIndexChanged.connect(self._on_lab_mode_changed)
        ctrl_row.addWidget(self.lab_mode_combo, 1)
        self.lab_apply_btn = QPushButton("Update")
        self.lab_apply_btn.clicked.connect(self.run_ela)
        ctrl_row.addWidget(self.lab_apply_btn)
        self.lab_zoom_btn = QPushButton("Open in Viewer…")
        self.lab_zoom_btn.clicked.connect(self._open_lab_in_viewer)
        ctrl_row.addWidget(self.lab_zoom_btn)
        e_lay.addLayout(ctrl_row)

        sliders = QHBoxLayout()
        sliders.setSpacing(8)
        self.lab_q_label = label("Q: 90", wrap=False, muted=True)
        self.lab_quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.lab_quality_slider.setRange(50, 98)
        self.lab_quality_slider.setValue(90)
        self.lab_quality_slider.setAccessibleName("ELA quality")
        self.lab_quality_slider.valueChanged.connect(lambda v: self.lab_q_label.setText(f"Q: {v}"))
        self.lab_b_label = label("Boost: 15x", wrap=False, muted=True)
        self.lab_boost_slider = QSlider(Qt.Orientation.Horizontal)
        self.lab_boost_slider.setRange(1, 40)
        self.lab_boost_slider.setValue(15)
        self.lab_boost_slider.setAccessibleName("Visual boost")
        self.lab_boost_slider.valueChanged.connect(
            lambda v: self.lab_b_label.setText(f"Boost: {v}x")
        )
        sliders.addWidget(self.lab_q_label)
        sliders.addWidget(self.lab_quality_slider, 1)
        sliders.addWidget(self.lab_b_label)
        sliders.addWidget(self.lab_boost_slider, 1)
        e_lay.addLayout(sliders)

        self.ela_stats = label("", muted=True)
        e_lay.addWidget(self.ela_stats)
        self.ela_image = QLabel("")
        self.ela_image.setTextFormat(Qt.TextFormat.PlainText)
        self.ela_image.setObjectName("ThumbBox")
        self.ela_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ela_image.setMinimumHeight(150)
        e_lay.addWidget(self.ela_image)
        self.ghost_chart = JpegGhostChart()
        self.ghost_chart.setVisible(False)
        e_lay.addWidget(self.ghost_chart)
        e_lay.addWidget(label(FORENSICS_LAB_DISCLAIMER, muted=True))
        self.ela_host.setVisible(False)
        lay.addWidget(self.ela_host)

        # Manual public reverse image search (strictly opt-in; never auto-uploads)
        lay.addWidget(label("Public Reverse Image Search (Manual — External)", obj="CardTitle"))
        lay.addWidget(
            label(
                "ImageTrace never uploads evidence automatically. If authorized for a public "
                "image, open a search site below and upload manually (or save a stripped copy).",
                muted=True,
            )
        )
        rev_grid = QGridLayout()
        rev_grid.setContentsMargins(0, 0, 0, 0)
        rev_grid.setHorizontalSpacing(8)
        rev_grid.setVerticalSpacing(6)
        self.reverse_buttons: list[QPushButton] = []
        for idx, (name, url) in enumerate(REVERSE_SEARCH_ENGINES):
            btn = QPushButton(name)
            btn.clicked.connect(lambda _c=False, n=name, u=url: self._open_reverse_search(n, u))
            rev_grid.addWidget(btn, idx // 2, idx % 2)
            self.reverse_buttons.append(btn)
        lay.addLayout(rev_grid)

        lay.addWidget(
            label(
                "Honest technical limit: image files never store IP addresses, router logs, or "
                "who forwarded the file. Messaging apps (WhatsApp, Telegram, Instagram) usually "
                "strip metadata and re-encode pixels.",
                muted=True,
            )
        )
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        outer.addWidget(scroll)

    # --- display ----------------------------------------------------------------------
    def show_evidence(self, item: Evidence | None) -> None:
        self.current = item
        self.ela_host.setVisible(False)
        self.ela_image.setPixmap(QPixmap())
        self.ghost_chart.set_result(None)
        self.thumb_compare_host.setVisible(False)
        self.main_mini_label.setPixmap(QPixmap())
        self.exif_mini_label.setPixmap(QPixmap())
        for b in (self.export_thumb_btn, self.compare_thumb_btn, self.extract_trailing_btn):
            b.setVisible(False)

        enabled = item is not None
        for b in (
            self.run_btn,
            self.ela_btn,
            self.strip_btn,
            self.inspect_btn,
            *self.reverse_buttons,
        ):
            b.setEnabled(enabled)
        if item is None:
            self.report = None
            self.summary_host.setVisible(False)
            self.status_label.setText("Select an evidence item to run Origin & Trace.")
            set_state(self.status_label, None)
            return

        rep = self.service.latest_report(item.id)
        self.report = rep
        if rep is None:
            self.summary_host.setVisible(False)
            self.status_label.setText(
                "Origin & Trace has not been run yet. Click “Run Origin & Trace” to inspect "
                "device/software origin, XMP edit history, DQT fingerprint and embedded thumbnail."
            )
            set_state(self.status_label, None)
            return
        self._render_report(rep)

    def _render_report(self, rep: ProvenanceReport) -> None:
        self.summary_host.setVisible(True)
        self.status_label.setText(
            f"Analyzed with {rep.engine_version} at {format_local(rep.analyzed_at)}"
        )
        set_state(self.status_label, "warn" if rep.warnings else "ok")

        self.category_label.setText(rep.primary_category)
        cat_state = (
            "warn" if (rep.metadata_stripped_likely or rep.ai_indicators or rep.warnings) else "ok"
        )
        set_state(self.category_label, cat_state)
        self.verdict_label.setText(rep.primary_verdict)
        self.confidence_label.setText(rep.confidence_note)

        self.fields["device"].setText(rep.capture_device)
        self.fields["platform"].setText(rep.capture_os_or_platform)
        self.fields["software"].setText(
            ", ".join(rep.editing_software) if rep.editing_software else "None detected"
        )
        if rep.hardware_ids:
            hw_txt = "; ".join(f"{k}={v}" for k, v in rep.hardware_ids.items())
            self.fields["hardware"].setText(hw_txt)
        else:
            self.fields["hardware"].setText("None embedded")

        ai_parts: list[str] = []
        if rep.ai_indicators:
            ai_parts.append("AI: " + ", ".join(rep.ai_indicators))
        if rep.has_c2pa:
            gen = rep.c2pa_claims.get("claim_generator", "")
            c2pa_s = f"C2PA ({gen})" if gen and gen != "Not parsed from raw JUMBF" else "C2PA"
            ai_parts.append(f"{c2pa_s} [signature unverified offline]")
        self.fields["ai_c2pa"].setText(" · ".join(ai_parts) if ai_parts else "None detected")
        set_state(self.fields["ai_c2pa"], "warn" if rep.ai_indicators else None)

        bf = rep.binary_findings
        b_parts = [bf.get("container", "other")]
        if bf.get("jpeg_estimated_quality") is not None:
            mode = "Progressive" if bf.get("jpeg_progressive") else "Baseline"
            sub = bf.get("jpeg_subsampling") or "?"
            b_parts.append(f"{mode}, ~Q{bf['jpeg_estimated_quality']}, {sub}")
        segs = bf.get("segments") or []
        if segs:
            b_parts.append("Markers: " + ", ".join(segs[:8]) + ("…" if len(segs) > 8 else ""))
        self.fields["binary"].setText(" · ".join(b_parts))

        if rep.dqt_info:
            self.fields["dqt"].setText(
                f"{rep.dqt_info.get('dqt_short', '')}… · {rep.dqt_info.get('profile_hint', '')} "
                "[Heuristic clue]"
            )
        else:
            self.fields["dqt"].setText("Not applicable (non-JPEG or no DQT)")

        tc = rep.thumbnail_check
        self.fields["thumb"].setText(tc.get("message", "Not checked"))
        set_state(
            self.fields["thumb"],
            "error"
            if tc.get("status") == "mismatch"
            else ("ok" if tc.get("status") == "consistent" else None),
        )
        has_exif_thumb = bool(tc.get("present"))
        self.export_thumb_btn.setVisible(has_exif_thumb)
        self.compare_thumb_btn.setVisible(has_exif_thumb)
        self._load_exif_thumb_strip(has_exif_thumb)

        t_bytes = int(bf.get("trailing_bytes") or 0)
        if t_bytes > 0:
            self.fields["trailing"].setText(
                f"{t_bytes:,} bytes — {bf.get('trailing_signature', 'unknown')}"
            )
            set_state(self.fields["trailing"], "warn")
            self.extract_trailing_btn.setText(
                "Extract Motion Photo MP4…"
                if bf.get("trailing_is_mp4")
                else "Extract trailing payload…"
            )
            self.extract_trailing_btn.setVisible(True)
        else:
            self.fields["trailing"].setText("None")
            set_state(self.fields["trailing"], None)
            self.extract_trailing_btn.setVisible(False)

        if rep.document_ids:
            doc_txt = "; ".join(f"{k}={elide(v, 36)}" for k, v in rep.document_ids.items())
            self.fields["doc_ids"].setText(doc_txt)
        else:
            self.fields["doc_ids"].setText("None embedded")

        # Timeline table
        self.timeline_table.clearContents()
        self.timeline_table.setRowCount(len(rep.timeline))
        for r, step in enumerate(rep.timeline):
            cells = (
                str(step.step_no),
                step.stage,
                step.device_or_tool,
                step.platform_hint,
                step.timestamp,
                step.action,
                f"{step.evidence_source} [{step.classification}]",
            )
            for c, txt in enumerate(cells):
                cell = QTableWidgetItem(txt)
                cell.setToolTip(txt)
                self.timeline_table.setItem(r, c, cell)

        # Signals table
        self.signals_table.clearContents()
        self.signals_table.setRowCount(len(rep.evidence_signals))
        for r, sig in enumerate(rep.evidence_signals):
            cells = (
                sig.get("layer", ""),
                sig.get("classification", ""),
                sig.get("finding", ""),
            )
            for c, txt in enumerate(cells):
                cell = QTableWidgetItem(txt)
                cell.setToolTip(txt)
                self.signals_table.setItem(r, c, cell)

    def _load_exif_thumb_strip(self, has_exif_thumb: bool) -> None:
        if not has_exif_thumb or self.current is None:
            self.thumb_compare_host.setVisible(False)
            return
        orig = self.service.evidence.original_path(self.current)
        raw_thumb = extract_embedded_thumbnail_bytes(orig)
        if not raw_thumb:
            self.thumb_compare_host.setVisible(False)
            return
        q_exif = QImage.fromData(raw_thumb)
        if q_exif.isNull():
            self.thumb_compare_host.setVisible(False)
            return
        self.exif_mini_label.setPixmap(
            QPixmap.fromImage(q_exif).scaled(
                180,
                120,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        main_thumb = self.service.evidence.thumbnail_path(self.current)
        if main_thumb is not None and main_thumb.is_file():
            mpix = QPixmap(str(main_thumb))
            if not mpix.isNull():
                self.main_mini_label.setPixmap(
                    mpix.scaled(
                        180,
                        120,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        self.thumb_compare_host.setVisible(True)

    # --- actions ----------------------------------------------------------------------
    def _on_lab_mode_changed(self) -> None:
        mode = self.lab_mode_combo.currentData()
        self.lab_quality_slider.setEnabled(mode == "ela")
        self.lab_boost_slider.setEnabled(mode in ("ela", "noise", "gradient"))
        if self.ela_host.isVisible():
            self.run_ela()

    def _open_lab_in_viewer(self) -> None:
        mode = str(self.lab_mode_combo.currentData() or "ela")
        self.open_viewer_requested.emit(mode if mode != "ghost" else "ela")

    def run_trace(self) -> None:
        if self.current is None:
            return
        try:
            rep = self.service.analyze(self.current.id, extract_metadata_if_missing=True)
        except _ERRORS as exc:
            dialogs.error(self, "Origin & Trace failed", str(exc))
            return
        self.report = rep
        self._render_report(rep)
        self.trace_changed.emit()

    def run_ela(self) -> None:
        """Compute active Visual Forensics Lab diagnostic (default: ELA Q=90, 15x)."""
        if self.current is None:
            return
        orig = self.service.evidence.original_path(self.current)
        # Fallback to derived thumbnail when original is a RAW/HEIC preview-only image
        target_path = orig
        if not orig.is_file():
            dialogs.error(self, "Forensics lab failed", "Original file is missing.")
            return
        mode = str(self.lab_mode_combo.currentData() or "ela")
        quality = int(self.lab_quality_slider.value())
        boost = float(self.lab_boost_slider.value())

        try:
            if mode == "ghost":
                ghost = compute_jpeg_ghost_curve(target_path)
                self.ghost_chart.set_result(ghost)
                self.ghost_chart.setVisible(True)
                self.ela_image.setVisible(False)
                self.ela_stats.setText(ghost.summary)
                self.ela_host.setVisible(True)
                return

            self.ghost_chart.setVisible(False)
            self.ela_image.setVisible(True)
            if mode == "ela":
                ela = compute_ela(target_path, quality=quality, scale=boost)
                pil_img = ela.image
                stats_txt = (
                    f"JPEG Q={ela.quality} · Visual boost {ela.scale}x · "
                    f"Mean diff: {ela.mean_diff} · Max diff: {ela.max_diff}/255 · "
                    f"High-energy channels: {ela.high_energy_pct}%"
                )
            elif mode == "noise":
                res = compute_noise_residual(target_path, strength=boost)
                pil_img, stats_txt = res.image, res.summary
            elif mode == "gradient":
                res = compute_luminance_gradient(target_path, strength=max(1.0, boost / 3.0))
                pil_img, stats_txt = res.image, res.summary
            else:
                ch_key = mode.removeprefix("ch_")
                res = compute_channel_view(target_path, ch_key)
                pil_img, stats_txt = res.image, res.summary
        except _ERRORS as exc:
            dialogs.error(self, "Visual Forensics Lab failed", str(exc))
            return

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        qimg = QImage.fromData(buf.getvalue(), "PNG")
        if not qimg.isNull():
            pix = QPixmap.fromImage(qimg).scaled(
                420,
                250,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.ela_image.setPixmap(pix)
        self.ela_stats.setText(stats_txt)
        self.ela_host.setVisible(True)

    def export_exif_thumbnail(self) -> None:
        if self.current is None:
            return
        default_dir = self.service.evidence.derived_dir(self.current.case_id)
        dest = dialogs.choose_save_file(
            self,
            "Save embedded EXIF thumbnail",
            default_dir / f"{self.current.id}-exif-thumb.jpg",
            "JPEG image (*.jpg)",
        )
        if dest is None:
            return
        try:
            out_path, sha = self.service.export_embedded_thumbnail(self.current.id, dest)
        except _ERRORS as exc:
            dialogs.error(self, "Could not save EXIF thumbnail", str(exc))
            return
        self.trace_changed.emit()
        dialogs.info(
            self,
            "Embedded EXIF thumbnail saved",
            f"Saved: {out_path}\nSHA-256: {sha}\n\nOriginal evidence file verified unchanged.",
        )

    def export_trailing_payload(self) -> None:
        if self.current is None or self.report is None:
            return
        is_mp4 = bool(self.report.binary_findings.get("trailing_is_mp4"))
        default_dir = self.service.evidence.derived_dir(self.current.case_id)
        ext = ".mp4" if is_mp4 else ".bin"
        filt = (
            "MP4 video (*.mp4);;All files (*)" if is_mp4 else "Binary file (*.bin);;All files (*)"
        )
        dest = dialogs.choose_save_file(
            self,
            "Extract trailing payload (read-only)",
            default_dir / f"{self.current.id}-trailing{ext}",
            filt,
        )
        if dest is None:
            return
        try:
            out_path, sha, nbytes = self.service.export_trailing_media(self.current.id, dest)
        except _ERRORS as exc:
            dialogs.error(self, "Could not extract trailing payload", str(exc))
            return
        self.trace_changed.emit()
        dialogs.info(
            self,
            "Trailing payload extracted",
            f"Saved: {out_path} ({nbytes:,} bytes)\nSHA-256: {sha}\n\n"
            "Treat extracted bytes as untrusted data; original evidence verified unchanged.",
        )

    def export_stripped_copy(self) -> None:
        if self.current is None:
            return
        default_dir = self.service.evidence.derived_dir(self.current.case_id)
        dest = dialogs.choose_save_file(
            self,
            "Save metadata-stripped JPEG copy",
            default_dir / f"{self.current.id}-stripped.jpg",
            "JPEG image (*.jpg)",
        )
        if dest is None:
            return
        orig = self.service.evidence.original_path(self.current)
        try:
            out_path, sha = save_stripped_copy(orig, dest)
        except _ERRORS as exc:
            dialogs.error(self, "Could not save stripped copy", str(exc))
            return
        dialogs.info(
            self,
            "Metadata-stripped copy saved",
            f"Saved: {out_path}\nSHA-256: {sha}\n\n"
            "All EXIF, IPTC, XMP and GPS tags were omitted. Your original evidence file was "
            "not modified.",
        )

    def _open_reverse_search(self, engine_name: str, url: str) -> None:
        if self.current is None:
            return
        if not dialogs.confirm(
            self, f"Open {engine_name} (external website)", REVERSE_SEARCH_DISCLOSURE
        ):
            return
        with contextlib.suppress(_ERRORS):
            self.service.log_reverse_search_opened(self.current.id, engine_name)
        QDesktopServices.openUrl(QUrl(url))
        self.trace_changed.emit()
