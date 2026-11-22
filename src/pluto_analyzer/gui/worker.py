"""GUI worker: owns the PlutoVNA and runs sweeps off the Qt main thread."""

from __future__ import annotations

import queue
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from ..calibration import Calibration
from ..config import SweepConfig
from ..hwcheck import query_hardware
from ..sdr import PlutoVNA
from ..sweep import SweepEngine, SweepResult


class SweepWorker(QThread):
    device_opened = pyqtSignal(object)      # HardwareInfo dict
    device_closed = pyqtSignal()
    point_ready = pyqtSignal(object)        # SweepPoint
    progress = pyqtSignal(int, int)
    sweep_started = pyqtSignal(int)
    sweep_finished = pyqtSignal(object)     # SweepResult
    calibration_saved = pyqtSignal(str, object)
    error = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._commands: queue.Queue = queue.Queue()
        self._shutdown = False
        self._stop = False
        self._vna: PlutoVNA | None = None
        self._info = None
        self._uri = ""
        self._config: SweepConfig | None = None
        self._calibration: Calibration | None = None

    # --- GUI-facing API (called from the main thread) ------------------
    def open_device(self, uri: str, config: SweepConfig) -> None:
        self._commands.put(("open", uri, config))

    def close_device(self) -> None:
        self._commands.put(("close",))

    def start_sweep(
        self,
        config: SweepConfig,
        continuous: bool,
        calibration: Calibration | None,
    ) -> None:
        self._commands.put(("sweep", config, continuous, calibration))

    def start_calibration(self, config: SweepConfig, path: Path) -> None:
        self._commands.put(("cal", config, path))

    def request_stop(self) -> None:
        self._stop = True

    def shutdown(self) -> None:
        self._stop = True
        self._shutdown = True
        self._commands.put(("__wake__",))

    # --- thread body ----------------------------------------------------
    def run(self) -> None:
        while not self._shutdown:
            try:
                command = self._commands.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                kind = command[0]
                if kind == "open":
                    self._handle_open(command[1], command[2])
                elif kind == "close":
                    self._close_device()
                elif kind == "sweep":
                    self._handle_sweep(command[1], command[2], command[3])
                elif kind == "cal":
                    self._handle_calibration(command[1], command[2])
            except Exception as exc:  # keep the worker alive
                self.error.emit(str(exc))
                self.status.emit("error")
        self._close_device()

    def _handle_open(self, uri: str, config: SweepConfig) -> None:
        self._close_device()
        self._info = query_hardware(uri)
        bounds = (self._info.rx_lo.minimum, self._info.rx_lo.maximum)
        self._vna = PlutoVNA(uri, config, bounds).open()
        self._uri = uri
        self._config = config
        changed = self._vna.check_quiet()
        if changed:
            self.status.emit(
                "warning: Pluto is being reconfigured by another client "
                f"({', '.join(changed)})"
            )
        self.device_opened.emit(self._info.to_dict())
        self.status.emit("connected")

    def _close_device(self) -> None:
        if self._vna is not None:
            self._vna.close()
            self._vna = None
            self.device_closed.emit()
            self.status.emit("disconnected")

    def _ensure_device(self, config: SweepConfig) -> PlutoVNA:
        if self._vna is None:
            raise RuntimeError("Pluto is not connected")
        if self._config != config:
            self._vna.configure(config)
            self._config = config
        return self._vna

    def _handle_sweep(
        self,
        config: SweepConfig,
        continuous: bool,
        calibration: Calibration | None,
    ) -> None:
        vna = self._ensure_device(config)
        self._calibration = calibration
        self._stop = False
        while not self._stop:
            engine = SweepEngine(vna, config)
            self.sweep_started.emit(config.points)
            result = self._run_engine(engine, calibration)
            self.sweep_finished.emit(result)
            if not continuous or self._stop or result.stopped:
                break
        self.status.emit("idle")

    def _run_engine(
        self, engine: SweepEngine, calibration: Calibration | None
    ) -> SweepResult:
        def on_point(done: int, total: int, point) -> None:
            if self._stop:
                engine.request_stop()
            if calibration is not None:
                point.s21_db = float(
                    point.dbfs
                    - calibration.correction([point.freq_hz])[0]
                    + engine.config.offset_db
                )
            self.point_ready.emit(point)
            self.progress.emit(done, total)

        return engine.run(on_point)

    def _handle_calibration(self, config: SweepConfig, path: Path) -> None:
        vna = self._ensure_device(config)
        self._stop = False
        engine = SweepEngine(vna, config)
        self.status.emit("calibrating")
        result = self._run_engine(engine, None)
        if result.stopped or not result.points:
            self.status.emit("calibration cancelled")
            return
        summary = result.summary()
        if (
            summary.get("clip_count")
            or summary.get("low_count")
            or summary.get("unstable_count")
        ):
            hints = []
            if summary.get("clip_count"):
                hints.append(
                    "lower RX gain further (down to -3 dB), "
                    "lower the Tone level, or insert the 10 dB attenuator"
                )
            if summary.get("low_count"):
                hints.append("check the cable/attenuator connection")
            if summary.get("unstable_count"):
                hints.append("check that the beacon is off and nobody else uses the Pluto")
            self.error.emit(
                "Calibration rejected: CLIP/LOW/UNSTABLE points. "
                + "; ".join(hints)
            )
            return
        calibration = Calibration.from_result(result, self._uri, self._info)
        npz_path, _ = calibration.save(path)
        self.calibration_saved.emit(str(npz_path), summary)
        self.status.emit("calibrated")
