"""S21 plot widget: live curve, raw points, markers, cursor readout."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
import pyqtgraph.exporters  # noqa: F401  (registers pg.exporters)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ..dsp import smooth_trace

SMOOTH_WINDOW = 7


class SpectrumPlot(QWidget):
    stats_changed = pyqtSignal(dict)
    cursor_moved = pyqtSignal(float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.plot = pg.PlotWidget(background="w")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        # No pyqtgraph SI units here: the X values are already in MHz, and
        # units="MHz" made pyqtgraph render them as "kMHz".
        self.plot.setLabel("bottom", "Frequency, MHz")
        self.plot.setLabel("left", "S21, dB")
        self.plot.getAxis("left").setTextPen("k")
        self.plot.getAxis("bottom").setTextPen("k")
        self.plot.getAxis("left").setPen("k")
        self.plot.getAxis("bottom").setPen("k")
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)

        self.curve = self.plot.plot([], [], pen=pg.mkPen("#1f77b4", width=2))
        self.dots = pg.ScatterPlotItem(size=4, brush=pg.mkBrush("#d62728"))
        self.plot.addItem(self.dots)

        self.cursor = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen("k", style=Qt.PenStyle.DashLine),
        )
        self.cursor.setVisible(False)
        self.plot.addItem(self.cursor, ignoreBounds=True)

        self.markers: list = []
        self.show_raw = True
        self.show_smoothed = True
        self.smooth_window = SMOOTH_WINDOW
        self.calibrated = False

        self._freqs: list[float] = []
        self._dbfs: list[float] = []
        self._s21: list[float | None] = []
        self._dirty = True
        self._auto_y = False

        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

        self.plot.scene().sigMouseClicked.connect(self._on_click)
        self.plot.scene().sigMouseMoved.connect(self._on_move)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot)

    # --- data -----------------------------------------------------------
    def reset(self, start_mhz: float, stop_mhz: float) -> None:
        self._freqs.clear()
        self._dbfs.clear()
        self._s21.clear()
        self.curve.setData([], [])
        self.dots.setData([], [])
        self._clear_markers()
        self._auto_y = False
        self.plot.setXRange(start_mhz, stop_mhz, padding=0.0)
        self.plot.setYRange(-40.0, 5.0, padding=0.0)
        self._dirty = True
        self._refresh()

    def set_x_range(self, start_mhz: float, stop_mhz: float) -> None:
        self.plot.setXRange(start_mhz, stop_mhz, padding=0.0)

    def add_point(self, point) -> None:
        self._freqs.append(point.freq_hz / 1e6)
        self._dbfs.append(point.dbfs)
        self._s21.append(point.s21_db)
        self._dirty = True

    def set_calibrated(self, flag: bool) -> None:
        self.calibrated = bool(flag)
        self._dirty = True

    def set_raw_visible(self, flag: bool) -> None:
        self.show_raw = bool(flag)
        self._dirty = True

    def set_smoothed_visible(self, flag: bool) -> None:
        self.show_smoothed = bool(flag)
        self._dirty = True

    def set_smooth_window(self, points: int) -> None:
        """Moving-average window over frequency points (1 = no smoothing)."""
        window = max(1, int(points))
        if window == self.smooth_window:
            return
        self.smooth_window = window
        self._dirty = True

    def values(self) -> tuple[np.ndarray, np.ndarray]:
        x = np.asarray(self._freqs, dtype=float)
        if self.calibrated and any(v is not None for v in self._s21):
            y = np.asarray(
                [np.nan if v is None else v for v in self._s21], dtype=float
            )
        else:
            y = np.asarray(self._dbfs, dtype=float)
        return x, y

    def stats(self) -> dict:
        x, y = self.values()
        if y.size == 0:
            return {}
        good = np.isfinite(y)
        if not np.any(good):
            return {}
        yy = y[good]
        return {
            "points": int(y.size),
            "min": float(np.min(yy)),
            "max": float(np.max(yy)),
            "mean": float(np.mean(yy)),
            "delta": float(np.max(yy) - np.min(yy)),
            "at_min": float(x[good][np.argmin(yy)]),
            "at_max": float(x[good][np.argmax(yy)]),
        }

    # --- rendering -------------------------------------------------------
    def _refresh(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        x, y = self.values()
        if y.size == 0:
            return
        if self.show_smoothed:
            smooth = smooth_trace(y, self.smooth_window)
            self.curve.setData(x, smooth)
        else:
            self.curve.setData([], [])
        if self.show_raw:
            finite = np.isfinite(y)
            self.dots.setData(x[finite], y[finite])
        else:
            self.dots.setData([], [])
        if not self._auto_y and np.any(np.isfinite(y)):
            self.plot.getViewBox().enableAutoRange(axis=pg.ViewBox.YAxis)
            self._auto_y = True
        self.stats_changed.emit(self.stats())

    # --- mouse -----------------------------------------------------------
    def _on_move(self, pos) -> None:
        if not self.plot.sceneBoundingRect().contains(pos):
            return
        point = self.plot.getViewBox().mapSceneToView(pos)
        self.cursor.setPos(point.x())
        self.cursor.setVisible(True)
        self.cursor_moved.emit(point.x(), point.y())

    def _on_click(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._clear_markers()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x, y = self.values()
        good = np.isfinite(y)
        if not np.any(good):
            return
        pos = event.scenePos()
        if not self.plot.sceneBoundingRect().contains(pos):
            return
        target = self.plot.getViewBox().mapSceneToView(pos).x()
        xs = x[good]
        ys = y[good]
        index = int(np.argmin(np.abs(xs - target)))
        mx, my = float(xs[index]), float(ys[index])
        marker = pg.ScatterPlotItem(
            [mx], [my], symbol="x", size=11, pen=pg.mkPen("k", width=2)
        )
        text = pg.TextItem(
            f"{mx:.1f} MHz\n{my:+.2f} dB",
            color="k",
            anchor=(0, 1),
            fill=pg.mkBrush(255, 255, 255, 200),
        )
        text.setPos(mx, my)
        self.plot.addItem(marker)
        self.plot.addItem(text)
        self.markers.extend([marker, text])

    def _clear_markers(self) -> None:
        for item in self.markers:
            self.plot.removeItem(item)
        self.markers.clear()

    # --- export ----------------------------------------------------------
    def refresh(self) -> None:
        self._dirty = True
        self._refresh()

    def export_png(self, path: str) -> None:
        exporter = pg.exporters.ImageExporter(self.plot.plotItem)
        exporter.export(path)
