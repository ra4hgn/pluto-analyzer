"""Single-owner Pluto SDR wrapper.

All IIO access goes through this class. It configures the 1x1 Pluto with
pyadi (`adi.Pluto`, not `adi.ad9361`), keeps a CW tone in the cyclic TX
buffer, measures one frequency point with the lock-in detector and restores
the device settings on close (the Pluto may be shared with other users).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from .config import SweepConfig, plan_lo
from .dsp import (
    adc_peak,
    is_clipping,
    lockin_amplitude,
    make_tone,
    to_dbfs,
)

LOW_LEVEL_DBFS = -90.0

STATE_ATTRS = (
    "sample_rate",
    "rx_lo",
    "tx_lo",
    "rx_rf_bandwidth",
    "tx_rf_bandwidth",
    "gain_control_mode_chan0",
    "rx_hardwaregain_chan0",
    "tx_hardwaregain_chan0",
)


@dataclass
class SweepPoint:
    freq_hz: float
    lo_hz: float
    tone_hz: float
    captures: int
    amplitude: float
    dbfs: float
    peak: float
    clip: bool
    low: bool
    spread_db: float = 0.0
    unstable: bool = False
    s21_db: float | None = None


class PlutoVNA:
    """Owns the IIO context for the whole measurement session."""

    def __init__(
        self,
        uri: str,
        config: SweepConfig,
        lo_bounds: tuple[float, float] | None = None,
    ) -> None:
        self.uri = uri
        self.config = config
        self.lo_bounds = lo_bounds or (70e6, 6e9)
        self._sdr = None
        self._original: dict = {}
        self._tone_hz: float | None = None

    # --- lifecycle ---------------------------------------------------
    def open(self) -> "PlutoVNA":
        import adi  # imported lazily so unit tests need no hardware

        self._sdr = adi.Pluto(uri=self.uri)
        self._original = self._read_state()
        self.configure(self.config)
        return self

    def close(self) -> None:
        if self._sdr is None:
            return
        for method in ("tx_destroy_buffer", "rx_destroy_buffer"):
            try:
                getattr(self._sdr, method)()
            except Exception:
                pass
        try:
            self._restore_state(self._original)
        finally:
            self._sdr = None
            self._tone_hz = None

    def __enter__(self) -> "PlutoVNA":
        return self.open()

    def __exit__(self, *exc_info) -> None:
        self.close()

    # --- configuration ----------------------------------------------
    def _require(self):
        if self._sdr is None:
            raise RuntimeError("PlutoVNA is not open")
        return self._sdr

    def configure(self, config: SweepConfig) -> None:
        sdr = self._require()
        if getattr(sdr, "_txbuf", None):
            try:
                sdr.tx_destroy_buffer()
            except Exception:
                pass
            self._tone_hz = None
        self.config = config
        sdr.sample_rate = int(config.sample_rate)
        sdr.tx_enabled_channels = [0]
        sdr.rx_enabled_channels = [0]
        sdr.rx_rf_bandwidth = int(config.rf_bandwidth_hz)
        sdr.tx_rf_bandwidth = int(config.rf_bandwidth_hz)
        sdr.rx_buffer_size = int(config.num_samples)
        sdr.tx_buffer_size = int(config.num_samples)
        sdr.gain_control_mode_chan0 = "manual"
        sdr.tx_cyclic_buffer = True
        sdr.tx_hardwaregain_chan0 = float(config.tx_gain_db)
        sdr.rx_hardwaregain_chan0 = float(config.rx_gain_db)
        self._tone_hz = None

    def _read_state(self) -> dict:
        state: dict = {}
        for attr in STATE_ATTRS:
            try:
                state[attr] = getattr(self._sdr, attr)
            except Exception:
                pass
        try:
            state["fir_en"] = int(
                self._sdr._get_iio_attr("out", "voltage_filter_fir_en", False)
            )
        except Exception:
            pass
        return state

    def _restore_state(self, state: dict) -> None:
        sdr = self._sdr
        for attr in ("sample_rate", "rx_rf_bandwidth", "tx_rf_bandwidth"):
            if attr in state:
                try:
                    setattr(sdr, attr, int(state[attr]))
                except Exception:
                    pass
        for attr in (
            "gain_control_mode_chan0",
            "rx_hardwaregain_chan0",
            "tx_hardwaregain_chan0",
        ):
            if attr in state:
                try:
                    setattr(sdr, attr, state[attr])
                except Exception:
                    pass
        for attr in ("rx_lo", "tx_lo"):
            if attr in state:
                try:
                    setattr(sdr, attr, int(state[attr]))
                except Exception:
                    pass
        # Writing sample_rate enables the AD9361 FIR; put the original back.
        if state.get("fir_en") == 0:
            try:
                sdr._set_iio_attr("out", "voltage_filter_fir_en", False, 0)
            except Exception:
                pass

    # --- shared-device protection -------------------------------------
    def device_signature(self) -> dict:
        """Snapshot of settings another client would change."""
        sdr = self._require()
        return {
            "dds_scales": tuple(sdr.dds_scales),
            "dds_frequencies": tuple(sdr.dds_frequencies),
            "tx_lo": sdr.tx_lo,
            "rx_lo": sdr.rx_lo,
            "sample_rate": sdr.sample_rate,
            "rx_gain": sdr.rx_hardwaregain_chan0,
            "tx_gain": sdr.tx_hardwaregain_chan0,
        }

    def check_quiet(self, attempts: int = 5, interval_s: float = 0.25) -> list[str]:
        """Return attributes changed by someone else during a short watch.

        The Pluto may be shared: another client retuning LO, gains or the DDS
        makes measurements meaningless. An empty list means the settings were
        stable for ``attempts * interval_s`` seconds.
        """
        first = self.device_signature()
        changed: set[str] = set()
        for _ in range(max(0, attempts - 1)):
            time.sleep(interval_s)
            current = self.device_signature()
            changed.update(key for key in first if current[key] != first[key])
        return sorted(changed)

    # --- measurement --------------------------------------------------
    def set_tone(self, tone_hz: float) -> None:
        """Load a CW tone into the cyclic TX buffer (only when it changes)."""
        if self._tone_hz == tone_hz:
            return
        sdr = self._require()
        if self._tone_hz is not None:
            try:
                sdr.tx_destroy_buffer()
            except Exception:
                pass
        cfg = self.config
        sdr.tx(
            make_tone(
                tone_hz, cfg.sample_rate, cfg.num_samples, cfg.tone_amplitude
            )
        )
        self._tone_hz = float(tone_hz)
        # A freshly created cyclic buffer needs time before the first capture.
        if cfg.tone_settle_s > 0:
            time.sleep(cfg.tone_settle_s)

    def _capture(self) -> np.ndarray:
        data = self._require().rx()
        if isinstance(data, list):
            data = data[0]
        return np.asarray(data)

    def measure_point(
        self,
        freq_hz: float,
        captures: int | None = None,
    ) -> SweepPoint:
        cfg = self.config
        lo, detect = plan_lo(freq_hz, cfg.if_hz, *self.lo_bounds)
        self.set_tone(detect)

        sdr = self._require()
        sdr.tx_lo = int(lo)
        sdr.rx_lo = int(lo)
        time.sleep(cfg.dwell_s)
        for _ in range(cfg.flush_reads):
            self._capture()

        # Re-apply the gain after retuning: the driver's gain table can change
        # at band boundaries, and manual gain must be written again.
        sdr.gain_control_mode_chan0 = "manual"
        sdr.rx_hardwaregain_chan0 = float(cfg.rx_gain_db)
        sdr.tx_hardwaregain_chan0 = float(cfg.tx_gain_db)
        time.sleep(0.05)
        self._capture()

        shots = int(captures or cfg.captures_for(freq_hz))
        amplitudes = []
        peaks = []
        for _ in range(shots):
            samples = self._capture()
            amplitudes.append(
                lockin_amplitude(samples, detect, cfg.sample_rate)
            )
            peaks.append(adc_peak(samples))

        amplitude = float(np.median(amplitudes))
        peak = float(np.max(peaks))
        spread_db = float(
            20.0 * np.log10(max(amplitudes) / max(min(amplitudes), 1e-15))
        )
        dbfs = to_dbfs(amplitude)
        return SweepPoint(
            freq_hz=float(freq_hz),
            lo_hz=float(lo),
            tone_hz=float(detect),
            captures=shots,
            amplitude=amplitude,
            dbfs=float(dbfs),
            peak=peak,
            clip=bool(is_clipping(peak)),
            low=bool(dbfs < LOW_LEVEL_DBFS),
            spread_db=spread_db,
            unstable=bool(spread_db > 0.5),
        )
