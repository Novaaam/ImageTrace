"""Interactive Zoom/Pan Loupe & Side-by-Side Comparison Viewer (v0.3.0 Milestone 2).

Strictly read-only on original evidence files:
- Smooth zoom (25%..800%), mouse-wheel zoom, click-drag pan, 1:1 pixel zoom, fit-to-window.
- Nearest-neighbor rendering above 200% zoom so 8x8 JPEG blocks and fine details stay sharp.
- Live pixel coordinates (x, y) and RGB values on hover.
- Synchronized side-by-side comparison against Embedded EXIF Thumbnail, Visual Forensics maps
  (ELA, Noise Residual, Luminance Gradient, Cb/Cr chroma), or another evidence item in the case.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from imagetrace.evidence import Evidence, EvidenceRepository
from imagetrace.forensics import (
    ForensicsError,
    compute_channel_view,
    compute_luminance_gradient,
    compute_noise_residual,
    extract_embedded_thumbnail_bytes,
)
from imagetrace.image_analysis import _PIL_LOCK, dhash_distance
from imagetrace.provenance import ProvenanceError, compute_ela
from imagetrace.ui.widgets import label


def _pil_to_qimage(pil_im: Image.Image) -> QImage:
    buf = io.BytesIO()
    pil_im.convert("RGB").save(buf, format="PNG")
    return QImage.fromData(buf.getvalue(), "PNG")


def _load_qimage_read_only(path: Path, max_pixels: int = 50_000_000) -> QImage | None:
    if not path.is_file():
        return None
    with _PIL_LOCK:
        prev = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = max_pixels
        try:
            with Image.open(path) as im:
                im.load()
                rgb = ImageOps.exif_transpose(im).convert("RGB")
                # Cap viewer backing bitmap at 4096 px max edge so zoom stays responsive
                if max(rgb.size) > 4096:
                    rgb.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
                return _pil_to_qimage(rgb)
        except (UnidentifiedImageError, OSError, ValueError):
            return None
        finally:
            Image.MAX_IMAGE_PIXELS = prev


class ImageCanvas(QWidget):
    """Zoomable, pannable QPainter image canvas with normalized pan state for sync."""

    view_changed = Signal(float, float, float)  # zoom, norm_cx, norm_cy
    pixel_hovered = Signal(int, int, int, int, int)  # x, y, r, g, b

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._image: QImage | None = None
        self._zoom: float = 1.0
        self._cx: float = 0.5  # normalized center in image coordinates (0..1)
        self._cy: float = 0.5
        self._fit_mode: bool = True
        self._dragging: bool = False
        self._last_pos = QPointF()
        self.setMouseTracking(True)
        self.setMinimumSize(220, 220)

    def set_image(self, image: QImage | None, title: str | None = None) -> None:
        self._image = image
        if title is not None:
            self._title = title
        self._fit_mode = True
        self._cx = 0.5
        self._cy = 0.5
        self._recompute_fit_zoom()
        self.update()

    @property
    def zoom_factor(self) -> float:
        return self._zoom

    def _recompute_fit_zoom(self) -> None:
        if self._image is None or self._image.isNull():
            self._zoom = 1.0
            return
        w = max(self.width() - 16, 1)
        h = max(self.height() - 16, 1)
        self._zoom = max(0.05, min(w / self._image.width(), h / self._image.height()))

    def fit_to_window(self, *, emit: bool = True) -> None:
        self._fit_mode = True
        self._cx = 0.5
        self._cy = 0.5
        self._recompute_fit_zoom()
        self.update()
        if emit:
            self.view_changed.emit(self._zoom, self._cx, self._cy)

    def set_zoom(self, zoom: float, *, emit: bool = True) -> None:
        self._fit_mode = False
        self._zoom = max(0.1, min(8.0, float(zoom)))
        self.update()
        if emit:
            self.view_changed.emit(self._zoom, self._cx, self._cy)

    def apply_sync_view(self, zoom: float, cx: float, cy: float) -> None:
        self._fit_mode = False
        self._zoom = max(0.1, min(8.0, float(zoom)))
        self._cx = max(0.0, min(1.0, float(cx)))
        self._cy = max(0.0, min(1.0, float(cy)))
        self.update()

    def _target_rect(self) -> QRectF:
        if self._image is None or self._image.isNull():
            return QRectF()
        if self._fit_mode:
            self._recompute_fit_zoom()
        dw = self._image.width() * self._zoom
        dh = self._image.height() * self._zoom
        x = (self.width() / 2.0) - (self._cx * dw)
        y = (self.height() / 2.0) - (self._cy * dh)
        return QRectF(x, y, dw, dh)

    def paintEvent(self, _event: QPaintEvent) -> None:  # Qt API name
        p = QPainter(self)
        pal = self.palette()
        p.fillRect(self.rect(), pal.base().color())
        if self._image is None or self._image.isNull():
            p.setPen(pal.placeholderText().color())
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No image loaded")
            p.end()
            return

        # Crisp pixel edges above 200% zoom so JPEG 8x8 blocks & artifacts are clearly visible
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._zoom < 2.0)
        rect = self._target_rect()
        p.drawImage(rect, self._image)

        if self._title:
            badge_h = 22
            p.fillRect(0, 0, self.width(), badge_h, QColor(0, 0, 0, 150))
            p.setPen(QColor("#FFFFFF"))
            p.drawText(
                8,
                15,
                f"{self._title}  ({self._image.width()}x{self._image.height()} px · "
                f"{round(self._zoom * 100)}%)",
            )
        p.end()

    def wheelEvent(self, event: QWheelEvent) -> None:  # Qt API name
        if self._image is None or self._image.isNull():
            return
        delta = event.angleDelta().y()
        factor = 1.25 if delta > 0 else 0.8
        self.set_zoom(self._zoom * factor, emit=True)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # Qt API name
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # Qt API name
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # Qt API name
        if self._image is None or self._image.isNull():
            return
        pos = event.position()
        if self._dragging:
            dx = pos.x() - self._last_pos.x()
            dy = pos.y() - self._last_pos.y()
            self._last_pos = pos
            self._fit_mode = False
            dw = max(self._image.width() * self._zoom, 1.0)
            dh = max(self._image.height() * self._zoom, 1.0)
            self._cx = max(0.0, min(1.0, self._cx - dx / dw))
            self._cy = max(0.0, min(1.0, self._cy - dy / dh))
            self.update()
            self.view_changed.emit(self._zoom, self._cx, self._cy)
            return

        rect = self._target_rect()
        if rect.contains(pos):
            ix = int((pos.x() - rect.x()) / max(self._zoom, 1e-6))
            iy = int((pos.y() - rect.y()) / max(self._zoom, 1e-6))
            if 0 <= ix < self._image.width() and 0 <= iy < self._image.height():
                c = self._image.pixelColor(ix, iy)
                self.pixel_hovered.emit(ix, iy, c.red(), c.green(), c.blue())


class ImageViewerDialog(QDialog):
    """Interactive zoom/pan loupe and synchronized side-by-side forensic comparison dialog."""

    def __init__(
        self,
        repo: EvidenceRepository,
        item: Evidence,
        *,
        initial_compare: str = "single",
        max_pixels: int = 50_000_000,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.repo = repo
        self.item = item
        self.max_pixels = max_pixels
        self._syncing = False

        self.setWindowTitle(f"Image Inspector — {item.id} ({item.original_filename})")
        self.resize(1080, 720)
        self.setMinimumSize(720, 480)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        # Toolbar
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.zoom_out_btn = QPushButton("-")
        self.zoom_out_btn.setFixedWidth(32)
        self.zoom_out_btn.clicked.connect(lambda: self._scale_zoom(0.8))
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFixedWidth(32)
        self.zoom_in_btn.clicked.connect(lambda: self._scale_zoom(1.25))
        self.zoom_100_btn = QPushButton("100%")
        self.zoom_100_btn.clicked.connect(self._set_100)
        self.fit_btn = QPushButton("Fit")
        self.fit_btn.clicked.connect(self._fit_all)
        self.zoom_label = label("Fit", wrap=False, muted=True)

        self.compare_combo = QComboBox()
        self.compare_combo.setAccessibleName("Compare mode")
        self.compare_combo.addItem("Single view (Original only)", "single")
        orig = self.repo.original_path(item)
        self._exif_thumb_bytes = extract_embedded_thumbnail_bytes(orig)
        if self._exif_thumb_bytes:
            self.compare_combo.addItem("Embedded EXIF Thumbnail", "exif_thumb")
        self.compare_combo.addItem("ELA Map (Q=90, 15x)", "ela")
        self.compare_combo.addItem("Noise Residual (Median 3x3)", "noise")
        self.compare_combo.addItem("Luminance Gradient", "gradient")
        self.compare_combo.addItem("Chroma Cb", "ch_cb")
        self.compare_combo.addItem("Chroma Cr", "ch_cr")

        # Offer other evidence items in the same case for side-by-side comparison
        others = [e for e in self.repo.list_for_case(item.case_id) if e.id != item.id]
        others.sort(
            key=lambda e: dhash_distance(item.dhash, e.dhash) if (item.dhash and e.dhash) else 99
        )
        for other in others[:25]:
            dist = dhash_distance(item.dhash, other.dhash)
            hint = f" [dHash {dist}/64]" if dist is not None and dist <= 16 else ""
            self.compare_combo.addItem(
                f"Evidence: {other.id} · {other.original_filename}{hint}",
                f"ev:{other.id}",
            )

        self.sync_check = QCheckBox("Sync pan && zoom")
        self.sync_check.setChecked(True)

        for w in (
            self.zoom_out_btn,
            self.zoom_in_btn,
            self.zoom_100_btn,
            self.fit_btn,
            self.zoom_label,
        ):
            bar.addWidget(w)
        bar.addSpacing(12)
        bar.addWidget(label("Compare:", wrap=False, muted=True))
        bar.addWidget(self.compare_combo, 1)
        bar.addWidget(self.sync_check)
        lay.addLayout(bar)

        # Dual canvas splitter
        self.left_canvas = ImageCanvas(f"Original: {item.original_filename}")
        self.right_canvas = ImageCanvas("Comparison")
        self.right_canvas.setVisible(False)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.left_canvas)
        self.splitter.addWidget(self.right_canvas)
        self.splitter.setChildrenCollapsible(False)
        lay.addWidget(self.splitter, 1)

        # Footer pixel readout & note
        foot = QHBoxLayout()
        self.pixel_label = label("Move cursor over image for (x, y) and RGB values.", muted=True)
        foot.addWidget(self.pixel_label, 1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        foot.addWidget(close_btn)
        lay.addLayout(foot)

        # Load primary image (original, or safe derived preview fallback for RAW/HEIC)
        q_main = _load_qimage_read_only(orig, max_pixels=max_pixels)
        self._main_source_path = orig
        if q_main is None:
            thumb = self.repo.thumbnail_path(item)
            if thumb is not None and thumb.is_file():
                q_main = _load_qimage_read_only(thumb, max_pixels=max_pixels)
                self._main_source_path = thumb
        self.left_canvas.set_image(q_main, f"Original: {item.original_filename}")

        self.left_canvas.view_changed.connect(self._on_left_view)
        self.right_canvas.view_changed.connect(self._on_right_view)
        self.left_canvas.pixel_hovered.connect(
            lambda x, y, r, g, b: self.pixel_label.setText(
                f"Left ({x}, {y}) · RGB({r}, {g}, {b}) · #{r:02X}{g:02X}{b:02X}"
            )
        )
        self.right_canvas.pixel_hovered.connect(
            lambda x, y, r, g, b: self.pixel_label.setText(
                f"Right ({x}, {y}) · RGB({r}, {g}, {b}) · #{r:02X}{g:02X}{b:02X}"
            )
        )
        self.compare_combo.currentIndexChanged.connect(self._on_compare_changed)

        idx = self.compare_combo.findData(initial_compare)
        if idx >= 0:
            self.compare_combo.setCurrentIndex(idx)
        self._on_compare_changed()

    def _scale_zoom(self, mult: float) -> None:
        self.left_canvas.set_zoom(self.left_canvas.zoom_factor * mult, emit=True)

    def _set_100(self) -> None:
        self.left_canvas.set_zoom(1.0, emit=True)

    def _fit_all(self) -> None:
        self.left_canvas.fit_to_window(emit=False)
        if self.right_canvas.isVisible():
            self.right_canvas.fit_to_window(emit=False)
        self.zoom_label.setText(f"{round(self.left_canvas.zoom_factor * 100)}%")

    def _on_left_view(self, zoom: float, cx: float, cy: float) -> None:
        self.zoom_label.setText(f"{round(zoom * 100)}%")
        if self._syncing or not self.sync_check.isChecked() or not self.right_canvas.isVisible():
            return
        self._syncing = True
        try:
            self.right_canvas.apply_sync_view(zoom, cx, cy)
        finally:
            self._syncing = False

    def _on_right_view(self, zoom: float, cx: float, cy: float) -> None:
        if self._syncing or not self.sync_check.isChecked():
            return
        self._syncing = True
        try:
            self.left_canvas.apply_sync_view(zoom, cx, cy)
            self.zoom_label.setText(f"{round(zoom * 100)}%")
        finally:
            self._syncing = False

    def _on_compare_changed(self) -> None:
        key = str(self.compare_combo.currentData() or "single")
        if key == "single":
            self.right_canvas.setVisible(False)
            return
        q_right: QImage | None = None
        title = self.compare_combo.currentText()
        try:
            if key == "exif_thumb" and self._exif_thumb_bytes:
                q_right = QImage.fromData(self._exif_thumb_bytes)
            elif key == "ela":
                res = compute_ela(self._main_source_path, quality=90, scale=15.0)
                q_right = _pil_to_qimage(res.image)
            elif key == "noise":
                res = compute_noise_residual(self._main_source_path, strength=15.0)
                q_right = _pil_to_qimage(res.image)
            elif key == "gradient":
                res = compute_luminance_gradient(self._main_source_path, strength=4.0)
                q_right = _pil_to_qimage(res.image)
            elif key.startswith("ch_"):
                res = compute_channel_view(self._main_source_path, key.removeprefix("ch_"))
                q_right = _pil_to_qimage(res.image)
            elif key.startswith("ev:"):
                other = self.repo.get(key.removeprefix("ev:"))
                if other is not None:
                    q_right = _load_qimage_read_only(
                        self.repo.original_path(other), max_pixels=self.max_pixels
                    )
                    if q_right is None:
                        tp = self.repo.thumbnail_path(other)
                        if tp is not None:
                            q_right = _load_qimage_read_only(tp, max_pixels=self.max_pixels)
        except (ProvenanceError, ForensicsError, OSError):
            q_right = None

        self.right_canvas.set_image(q_right, title)
        self.right_canvas.setVisible(True)
        self.splitter.setSizes([520, 520])
