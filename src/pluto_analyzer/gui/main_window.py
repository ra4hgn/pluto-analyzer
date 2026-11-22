"""Main window: PyQt6 + pyqtgraph front-end for the S21 analyzer."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QAction, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..calibration import Calibration
from ..config import (
    DEFAULT_URI,
    USB_URI,
    SweepConfig,
    tone_amplitude_from_dbfs,
)
from ..export import write_csv, write_meta
from ..help import GUIDES, load_guide
from ..report import write_report, write_touchstone
from .plot import SpectrumPlot
from .worker import SweepWorker

HELP_TITLES = {
    "measurement": "Measurement guide",
    "calibration": "Calibration guide",
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Pluto S21 Analyzer")
        self.resize(1280, 820)

        self.worker = SweepWorker()
        self.calibration: Calibration | None = None
        self.last_result = None
        self.hw_info: dict | None = None
        self.uri = DEFAULT_URI
        self._connected = False
        self._active_config: SweepConfig | None = None

        self._build_ui()
        self._connect_signals()
        self.plot.reset(self.sp_start.value(), self.sp_stop.value())
        self.sp_start.valueChanged.connect(self._update_x_range)
        self.sp_stop.valueChanged.connect(self._update_x_range)
        self._autoload_calibration()
        self._set_running(False)
        self.worker.start()

    # --- UI construction -------------------------------------------------
    def _build_menu(self) -> None:
        help_menu = self.menuBar().addMenu("&Help")

        measure = QAction("&Measurement guide", self)
        measure.setShortcut(QKeySequence.StandardKey.HelpContents)
        measure.triggered.connect(lambda: self._show_guide("measurement"))
        help_menu.addAction(measure)

        calibration = QAction("&Calibration guide", self)
        calibration.triggered.connect(lambda: self._show_guide("calibration"))
        help_menu.addAction(calibration)

        help_menu.addSeparator()
        about = QAction("&About", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

    def _show_guide(self, name: str) -> None:
        dialog = QDialog(self)
        dialog.resize(900, 700)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser(dialog)
        browser.setOpenLinks(False)
        layout.addWidget(browser, stretch=1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close, dialog
        )
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def show(guide: str) -> None:
            dialog.setWindowTitle(HELP_TITLES[guide])
            browser.setMarkdown(load_guide(guide))
            browser.verticalScrollBar().setValue(0)

        def follow(url: QUrl) -> None:
            target = Path(url.toString()).name
            for guide, filename in GUIDES.items():
                if target == filename:
                    show(guide)
                    return
            QDesktopServices.openUrl(url)

        browser.anchorClicked.connect(follow)
        try:
            show(name)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, "Help", f"Cannot load the {name} guide: {exc}"
            )
            return
        dialog.exec()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Pluto S21 Analyzer",
            f"<b>Pluto S21 Analyzer</b> {__version__}<br><br>"
            "Scalar |S21| measurement with ADALM-PLUTO (AD9363).<br>"
            "Magnitude only: no phase, S11, S12 or S22.<br><br>"
            "Open <i>Help → Measurement guide</i> and "
            "<i>Help → Calibration guide</i> for the workflow.<br>"
            'Project: <a href="https://github.com/ra4hgn/pluto-analyzer">'
            "github.com/ra4hgn/pluto-analyzer</a><br>"
            "License: MIT.",
        )

    def _build_ui(self) -> None:
        self._build_menu()
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        self.plot = SpectrumPlot()
        root.addWidget(self._build_connection_row())
        root.addWidget(self._build_sweep_row())
        root.addWidget(self._build_display_row())
        root.addWidget(self.plot, stretch=1)

        self.status = self.statusBar()
        self.lbl_conn = QLabel("disconnected")
        self.lbl_cal = QLabel("cal: none")
        self.lbl_progress = QLabel("idle")
        self.lbl_point = QLabel("")
        self.lbl_stats = QLabel("")
        self.lbl_cursor = QLabel("")
        for widget in (
            self.lbl_conn, self.lbl_cal, self.lbl_progress,
            self.lbl_point, self.lbl_stats, self.lbl_cursor,
        ):
            self.status.addPermanentWidget(widget)

    def _build_connection_row(self) -> QGroupBox:
        box = QGroupBox("Connection")
        row = QHBoxLayout(box)

        self.cmb_uri = QComboBox()
        self.cmb_uri.setEditable(True)
        self.cmb_uri.addItems([DEFAULT_URI, USB_URI, "ip:pluto.local"])
        self.cmb_uri.setMinimumWidth(190)
        row.addWidget(QLabel("URI:"))
        row.addWidget(self.cmb_uri)

        self.btn_connect = QPushButton("Connect")
        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_connect.clicked.connect(self._connect_device)
        self.btn_disconnect.clicked.connect(self._disconnect_device)
        row.addWidget(self.btn_connect)
        row.addWidget(self.btn_disconnect)

        self.lbl_device = QLabel("")
        row.addWidget(self.lbl_device, stretch=1)

        self.btn_run = QPushButton("Run")
        self.btn_stop = QPushButton("Stop")
        self.chk_continuous = QCheckBox("Continuous")
        self.btn_run.clicked.connect(self._run)
        self.btn_stop.clicked.connect(self._stop)
        row.addWidget(self.chk_continuous)
        row.addWidget(self.btn_run)
        row.addWidget(self.btn_stop)

        self.btn_cal = QPushButton("Cal Thru")
        self.btn_load_cal = QPushButton("Load Cal")
        self.btn_clear_cal = QPushButton("Clear Cal")
        self.btn_cal.clicked.connect(self._calibrate)
        self.btn_load_cal.clicked.connect(self._load_calibration)
        self.btn_clear_cal.clicked.connect(self._clear_calibration)
        row.addWidget(self.btn_cal)
        row.addWidget(self.btn_load_cal)
        row.addWidget(self.btn_clear_cal)
        return box

    def _dspin(self, minimum, maximum, value, decimals=2, suffix="") -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        return spin

    def _build_sweep_row(self) -> QGroupBox:
        box = QGroupBox("Sweep")
        row = QHBoxLayout(box)

        self.sp_start = self._dspin(46.875, 6000.0, 70.0, 3)
        self.sp_stop = self._dspin(46.875, 6000.0, 6000.0, 3)
        self.sp_points = QSpinBox()
        self.sp_points.setRange(1, 5000)
        self.sp_points.setValue(500)
        self.sp_if = self._dspin(20.0, 2000.0, 500.0, 1)
        self.sp_dwell = QSpinBox()
        self.sp_dwell.setRange(50, 2000)
        self.sp_dwell.setValue(300)
        self.sp_dwell.setSuffix(" ms")
        self.sp_rx = QSpinBox()
        self.sp_rx.setRange(-3, 71)
        self.sp_rx.setValue(10)
        self.sp_tx = self._dspin(-89.75, 0.0, 0.0, 2)
        self.sp_tone = self._dspin(-60.0, 0.0, -6.0, 1, " dBFS")
        self.sp_offset = self._dspin(-60.0, 60.0, 0.0, 1, " dB")
        self.sp_captures = QSpinBox()
        self.sp_captures.setRange(0, 10)
        self.sp_captures.setValue(0)
        self.sp_captures.setToolTip("0 = automatic (3 below 1 GHz, 2 above)")

        for label, widget, unit in (
            ("Start", self.sp_start, "MHz"),
            ("Stop", self.sp_stop, "MHz"),
            ("Points", self.sp_points, ""),
            ("IF", self.sp_if, "kHz"),
            ("Dwell", self.sp_dwell, ""),
            ("RX gain", self.sp_rx, "dB"),
            ("TX gain", self.sp_tx, "dB"),
            ("Tone", self.sp_tone, ""),
            ("Offset", self.sp_offset, ""),
            ("Captures", self.sp_captures, ""),
        ):
            column = QVBoxLayout()
            column.addWidget(QLabel(label + (f" ({unit})" if unit else "")))
            column.addWidget(widget)
            row.addLayout(column)

        row.addStretch(1)
        return box

    def _build_display_row(self) -> QGroupBox:
        box = QGroupBox("View and Export")
        row = QHBoxLayout(box)

        self.sp_smooth = QSpinBox()
        self.sp_smooth.setRange(1, 101)
        self.sp_smooth.setValue(self.plot.smooth_window)
        self.sp_smooth.setSuffix(" pts")
        self.sp_smooth.setMaximumWidth(90)
        self.sp_smooth.setToolTip(
            "Moving-average window over frequency points (1 = no smoothing). "
            "Affects the displayed Smoothed curve only; CSV/report/PNG use raw data."
        )
        self.sp_smooth.valueChanged.connect(self.plot.set_smooth_window)
        row.addWidget(QLabel("Smoothing window:"))
        row.addWidget(self.sp_smooth)

        self.chk_raw = QCheckBox("Raw")
        self.chk_raw.setChecked(True)
        self.chk_smooth = QCheckBox("Smoothed")
        self.chk_smooth.setChecked(True)
        self.chk_cal = QCheckBox("Calibrated")
        self.chk_cal.setChecked(False)
        self.chk_cal.setEnabled(False)
        self.chk_raw.toggled.connect(self._plot_set_raw)
        self.chk_smooth.toggled.connect(self._plot_set_smoothed)
        self.chk_smooth.toggled.connect(self.sp_smooth.setEnabled)
        self.sp_smooth.setEnabled(self.chk_smooth.isChecked())
        self.chk_cal.toggled.connect(self._plot_set_calibrated)
        row.addSpacing(16)
        row.addWidget(self.chk_raw)
        row.addWidget(self.chk_smooth)
        row.addWidget(self.chk_cal)
        row.addStretch(1)

        self.btn_csv = QPushButton("Export CSV")
        self.btn_png = QPushButton("Export PNG")
        self.btn_report = QPushButton("Report PNG")
        self.btn_touch = QPushButton("Touchstone")
        self.btn_csv.clicked.connect(self._export_csv)
        self.btn_png.clicked.connect(self._export_png)
        self.btn_report.clicked.connect(self._export_report)
        self.btn_touch.clicked.connect(self._export_touchstone)
        row.addWidget(self.btn_csv)
        row.addWidget(self.btn_png)
        row.addWidget(self.btn_report)
        row.addWidget(self.btn_touch)
        return box

    # --- worker wiring ---------------------------------------------------
    def _connect_signals(self) -> None:
        self.worker.device_opened.connect(self._on_device_opened)
        self.worker.device_closed.connect(self._on_device_closed)
        self.worker.point_ready.connect(self._on_point)
        self.worker.progress.connect(self._on_progress)
        self.worker.sweep_started.connect(self._on_sweep_started)
        self.worker.sweep_finished.connect(self._on_sweep_finished)
        self.worker.calibration_saved.connect(self._on_calibration_saved)
        self.worker.error.connect(self._on_error)
        self.worker.status.connect(self._on_status)
        self.plot.stats_changed.connect(self._on_stats)
        self.plot.cursor_moved.connect(self._on_cursor)

    # --- plot toggles ----------------------------------------------------
    def _plot_set_raw(self, flag: bool) -> None:
        self.plot.set_raw_visible(flag)

    def _plot_set_smoothed(self, flag: bool) -> None:
        self.plot.set_smoothed_visible(flag)

    def _plot_set_calibrated(self, flag: bool) -> None:
        self.plot.set_calibrated(flag and self.calibration is not None)

    # --- config ----------------------------------------------------------
    def _update_x_range(self) -> None:
        start = self.sp_start.value()
        stop = self.sp_stop.value()
        if stop > start:
            self.plot.set_x_range(start, stop)

    def _autoload_calibration(self) -> None:
        default = Path("cal/s21_thru.npz")
        if not default.exists():
            return
        try:
            calibration = Calibration.load(default)
        except Exception:
            return
        if not calibration.quality_ok():
            self.status.showMessage(
                "cal/s21_thru.npz is invalid (CLIP/LOW/UNSTABLE); "
                "not loaded, recalibrate", 10000
            )
            return
        self.calibration = calibration
        self.chk_cal.setEnabled(True)
        self.chk_cal.setChecked(True)
        self.plot.set_calibrated(True)
        self.lbl_cal.setText("cal: s21_thru.npz")
        warns = self.calibration.warnings(self.calibration.freqs)
        if warns:
            self.status.showMessage("calibration warning: " + "; ".join(warns), 10000)

    def _config(self) -> SweepConfig:
        captures = self.sp_captures.value()
        return SweepConfig(
            start_hz=self.sp_start.value() * 1e6,
            stop_hz=self.sp_stop.value() * 1e6,
            points=self.sp_points.value(),
            if_hz=self.sp_if.value() * 1e3,
            tx_gain_db=self.sp_tx.value(),
            rx_gain_db=float(self.sp_rx.value()),
            tone_amplitude=tone_amplitude_from_dbfs(self.sp_tone.value()),
            offset_db=self.sp_offset.value(),
            dwell_s=self.sp_dwell.value() / 1e3,
            captures_low_band=captures or 3,
            captures_high_band=captures or 2,
        )

    # --- actions ---------------------------------------------------------
    def _connect_device(self) -> None:
        uri = self.cmb_uri.currentText().strip()
        if not uri:
            QMessageBox.warning(self, "URI", "Enter an IIO URI")
            return
        try:
            config = self._config()
            config.frequencies()
        except ValueError as exc:
            QMessageBox.warning(self, "Sweep", str(exc))
            return
        self.uri = uri
        self.lbl_progress.setText("connecting...")
        self.lbl_device.setText(uri)
        self._set_running(True)
        self.worker.open_device(uri, config)

    def _disconnect_device(self) -> None:
        self.worker.close_device()

    def _run(self) -> None:
        if not self._connected:
            QMessageBox.warning(self, "Pluto", "Connect to the Pluto first")
            return
        try:
            config = self._config()
            config.frequencies()
        except ValueError as exc:
            QMessageBox.warning(self, "Sweep", str(exc))
            return

        self.plot.reset(config.start_hz / 1e6, config.stop_hz / 1e6)
        self.plot.set_calibrated(
            self.chk_cal.isChecked() and self.calibration is not None
        )
        if (
            self.calibration is not None
            and len(self.calibration.freqs) * 3 < config.points
        ):
            self.status.showMessage(
                "warning: calibration grid is much coarser than the sweep; "
                "recalibrate with more points for accurate results",
                8000,
            )
        if self.calibration is not None:
            mismatch = self.calibration.settings_mismatch(config)
            if mismatch:
                self.status.showMessage(
                    "warning: calibration settings differ: "
                    + "; ".join(mismatch), 12000
                )
        self._active_config = config
        self.last_result = None
        self._set_running(True)
        self.lbl_progress.setText("starting...")
        self.worker.start_sweep(
            config, self.chk_continuous.isChecked(), self.calibration
        )

    def _stop(self) -> None:
        self.worker.request_stop()
        self.lbl_progress.setText("stopping...")

    def _calibrate(self) -> None:
        if not self._connected:
            QMessageBox.warning(self, "Pluto", "Connect to the Pluto first")
            return
        try:
            config = self._config()
            config.frequencies()
        except ValueError as exc:
            QMessageBox.warning(self, "Calibration", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save thru calibration", "cal/s21_thru.npz",
            "Calibration (*.npz *.json)",
        )
        if not path:
            return
        self.plot.reset(config.start_hz / 1e6, config.stop_hz / 1e6)
        self._set_running(True)
        self.lbl_progress.setText("calibrating...")
        self.worker.start_calibration(config, Path(path).with_suffix(""))

    def _load_calibration(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load calibration", "cal",
            "Calibration (*.npz *.json);;All files (*)",
        )
        if not path:
            return
        try:
            calibration = Calibration.load(Path(path))
        except Exception as exc:
            QMessageBox.critical(self, "Calibration", str(exc))
            return
        if not calibration.quality_ok():
            QMessageBox.critical(
                self, "Calibration",
                "This calibration is invalid: the sweep had CLIP/LOW/UNSTABLE "
                "points. Recalibrate with a clean reference (direct cable at "
                "RX -3 and Tone -16, or 10 dB attenuator at RX 10 and Tone -6).",
            )
            return
        self.calibration = calibration
        self.chk_cal.setChecked(True)
        self.chk_cal.setEnabled(True)
        self.plot.set_calibrated(True)
        self.lbl_cal.setText(f"cal: {Path(path).name}")
        warns = self.calibration.warnings(self.calibration.freqs)
        if warns:
            QMessageBox.warning(self, "Calibration", "\n".join(warns))
        self.status.showMessage(
            f"calibration loaded: {self.calibration.freqs.min()/1e6:.1f}"
            f"..{self.calibration.freqs.max()/1e6:.1f} MHz", 5000
        )

    def _clear_calibration(self) -> None:
        self.calibration = None
        self.chk_cal.setChecked(False)
        self.chk_cal.setEnabled(False)
        self.plot.set_calibrated(False)
        self.lbl_cal.setText("cal: none")
        self.plot.refresh()

    # --- exports ---------------------------------------------------------
    def _export_csv(self) -> None:
        if self.last_result is None or not self.last_result.points:
            QMessageBox.warning(self, "Export", "Run a sweep first")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", "trace.csv", "CSV (*.csv)"
        )
        if not path:
            return
        csv_path = Path(path)
        write_csv(csv_path, self.last_result.points)
        write_meta(
            csv_path.with_suffix(".json"),
            self.last_result,
            self.uri,
            hardware=None,
            warnings=[],
            extra={
                "hardware": self.hw_info,
                "calibration": (
                    None
                    if self.calibration is None
                    else {"meta": self.calibration.meta}
                ),
            },
        )
        self.status.showMessage(f"saved {csv_path}", 5000)

    def _export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PNG", "trace.png", "PNG (*.png)"
        )
        if not path:
            return
        try:
            self.plot.export_png(path)
        except Exception as exc:
            QMessageBox.critical(self, "Export", str(exc))
            return
        self.status.showMessage(f"saved {path}", 5000)

    def _export_report(self) -> None:
        if self.last_result is None or not self.last_result.points:
            QMessageBox.warning(self, "Export", "Run a sweep first")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export report", "trace_report.png",
            "PNG (*.png);;PDF (*.pdf)",
        )
        if not path:
            return
        config = self.last_result.config
        title = f"S21 {config.start_hz/1e6:.1f}-{config.stop_hz/1e6:.1f} MHz"
        try:
            write_report(Path(path), self.last_result, title=title, uri=self.uri)
        except Exception as exc:
            QMessageBox.critical(self, "Export", str(exc))
            return
        self.status.showMessage(f"saved {path}", 5000)

    def _export_touchstone(self) -> None:
        if self.last_result is None or not self.last_result.points:
            QMessageBox.warning(self, "Export", "Run a sweep first")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Touchstone", "trace.s2p", "Touchstone (*.s2p)"
        )
        if not path:
            return
        try:
            written = write_touchstone(Path(path), self.last_result)
        except Exception as exc:
            QMessageBox.critical(self, "Export", str(exc))
            return
        QMessageBox.information(
            self, "Touchstone",
            f"{written}\n\nMagnitude only: phase = 0 deg, "
            "S11/S12/S22 are placeholders (-100 dB). "
            "Do not use for phase-sensitive analysis.",
        )

    # --- worker callbacks ------------------------------------------------
    def _on_device_opened(self, info: dict) -> None:
        self._connected = True
        self.hw_info = info
        self.lbl_conn.setText("connected")
        self.lbl_device.setText(
            f"{info.get('hw_model', '?')} / {info.get('fw_version', '?')}"
        )
        self.lbl_progress.setText("idle")
        self._set_running(False)

    def _on_device_closed(self) -> None:
        self._connected = False
        self.lbl_conn.setText("disconnected")
        self.lbl_device.setText("")
        self.lbl_progress.setText("idle")
        self._set_running(False)

    def _on_point(self, point) -> None:
        self.plot.add_point(point)
        value = (
            point.s21_db
            if self.chk_cal.isChecked() and point.s21_db is not None
            else point.dbfs
        )
        unit = "S21" if point.s21_db is not None else "dBFS"
        flags = []
        if point.clip:
            flags.append("CLIP")
        if point.low:
            flags.append("LOW")
        if point.unstable:
            flags.append("UNSTABLE")
        self.lbl_point.setText(
            f"{point.freq_hz/1e6:.1f} MHz  {value:+.2f} {unit}"
            + ("  " + " ".join(flags) if flags else "")
        )

    def _on_progress(self, done: int, total: int) -> None:
        self.lbl_progress.setText(f"{done}/{total}")

    def _on_sweep_started(self, total: int) -> None:
        if self._active_config is not None:
            self.plot.reset(
                self._active_config.start_hz / 1e6,
                self._active_config.stop_hz / 1e6,
            )
            self.plot.set_calibrated(
                self.chk_cal.isChecked() and self.calibration is not None
            )

    def _on_sweep_finished(self, result) -> None:
        self.last_result = result
        summary = result.summary()
        self.lbl_progress.setText(
            f"done: {summary.get('points', 0)} pts, "
            f"{summary.get('duration_s', 0):.1f} s"
        )
        if not self.chk_continuous.isChecked() or result.stopped:
            self._set_running(False)

    def _on_calibration_saved(self, path: str, summary: dict) -> None:
        try:
            self.calibration = Calibration.load(Path(path))
        except Exception as exc:
            QMessageBox.critical(self, "Calibration", str(exc))
            return
        self.chk_cal.setChecked(True)
        self.chk_cal.setEnabled(True)
        self.plot.set_calibrated(True)
        self.lbl_cal.setText(f"cal: {Path(path).name}")
        self.lbl_progress.setText("calibrated")
        self._set_running(False)
        QMessageBox.information(
            self, "Calibration saved",
            f"{path}\npoints: {summary.get('points')}\n"
            f"min/max: {summary.get('min_db', 0):.2f}"
            f"..{summary.get('max_db', 0):.2f} dBFS",
        )

    def _on_error(self, message: str) -> None:
        self.lbl_progress.setText("error")
        self._set_running(False)
        QMessageBox.critical(self, "Pluto S21 Analyzer", message)

    def _on_status(self, message: str) -> None:
        self.status.showMessage(message, 4000)

    def _on_stats(self, stats: dict) -> None:
        if not stats:
            self.lbl_stats.setText("")
            return
        self.lbl_stats.setText(
            f"min {stats['min']:+.2f} @ {stats['at_min']:.1f} MHz  "
            f"max {stats['max']:+.2f} @ {stats['at_max']:.1f} MHz  "
            f"Δ {stats['delta']:.2f} dB"
        )

    def _on_cursor(self, freq_mhz: float, value: float) -> None:
        self.lbl_cursor.setText(f"cursor {freq_mhz:.1f} MHz {value:+.2f} dB")

    # --- state -----------------------------------------------------------
    def _set_running(self, running: bool) -> None:
        self.btn_connect.setEnabled(not running and not self._connected)
        self.btn_disconnect.setEnabled(not running and self._connected)
        self.btn_run.setEnabled(self._connected and not running)
        self.btn_stop.setEnabled(running)
        self.btn_cal.setEnabled(self._connected and not running)
        self.btn_load_cal.setEnabled(not running)
        self.btn_clear_cal.setEnabled(not running)
        self.btn_csv.setEnabled(not running and self.last_result is not None)
        self.btn_png.setEnabled(not running)
        self.btn_report.setEnabled(not running and self.last_result is not None)
        self.btn_touch.setEnabled(not running and self.last_result is not None)
        self.chk_cal.setEnabled(self.calibration is not None)

    def closeEvent(self, event) -> None:
        self.worker.shutdown()
        self.worker.wait(3000)
        super().closeEvent(event)


def _prepare_qt_environment() -> None:
    """Avoid Qt's GTK3 platform theme on newer GNOME.

    Qt 6 auto-selects the gtk3 platform theme on GNOME. That plugin reads
    ``org.gnome.settings-daemon.plugins.xsettings antialiasing``, which was
    removed in recent GNOME releases; GLib aborts the process with
    "does not contain a key named 'antialiasing'". Dropping the GNOME desktop
    hints makes Qt fall back to the built-in theme.

    Set ``PLUTO_NA_KEEP_THEME=1`` to keep the system theme if it works.
    """
    import os
    import subprocess

    if os.environ.get("PLUTO_NA_KEEP_THEME") == "1":
        return
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    if "GNOME" not in desktop:
        return
    try:
        probe = subprocess.run(
            [
                "gsettings", "get",
                "org.gnome.settings-daemon.plugins.xsettings",
                "antialiasing",
            ],
            capture_output=True,
            timeout=2,
            check=False,
        )
    except Exception:
        # Cannot probe; still avoid the gtk3 theme on GNOME.
        os.environ.pop("XDG_CURRENT_DESKTOP", None)
        os.environ.pop("GNOME_DESKTOP_SESSION_ID", None)
        return
    if probe.returncode != 0:
        os.environ.pop("XDG_CURRENT_DESKTOP", None)
        os.environ.pop("GNOME_DESKTOP_SESSION_ID", None)


def main() -> None:
    _prepare_qt_environment()
    import signal

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
