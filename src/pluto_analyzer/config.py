"""Configuration defaults and LO tuning helpers.

The Pluto board used here carries an AD9363 with the range-extension patch:
RX_LO covers 70 MHz..6 GHz, TX_LO covers 46.875 MHz..6 GHz. Limits are always
read from the device, not hard-coded, so the code also works on a stock unit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ADC_FULL_SCALE = 2048.0  # 12-bit ADC
TX_FULL_SCALE = 32768.0  # 16-bit DAC


def tone_amplitude_from_dbfs(db: float) -> int:
    """Convert a TX tone level in dBFS (16-bit full scale) to DAC counts."""
    amplitude = int(round(TX_FULL_SCALE * 10 ** (float(db) / 20.0)))
    return max(1, min(32767, amplitude))

DEFAULT_URI = os.environ.get("PLUTO_URI", "ip:192.168.2.1")
# USB bus/address changes after re-enumeration, so use the generic URI.
USB_URI = "usb:"
URI_PRESETS = (DEFAULT_URI, USB_URI, "ip:pluto.local")


@dataclass(frozen=True)
class SweepConfig:
    """Parameters of one S21 sweep."""

    start_hz: float = 70e6
    stop_hz: float = 6.0e9
    points: int = 500
    if_hz: float = 500e3
    sample_rate: int = 8_000_000
    num_samples: int = 1 << 18
    tone_amplitude: int = 1 << 14
    tx_gain_db: float = 0.0
    rx_gain_db: float = 10.0
    rf_bandwidth_hz: int = 4_000_000
    dwell_s: float = 0.3
    flush_reads: int = 2
    tone_settle_s: float = 0.3
    offset_db: float = 0.0
    captures_low_band: int = 3
    captures_high_band: int = 2
    low_band_split_hz: float = 1e9
    near_limit_hz: float = 5.9e9

    def frequencies(self) -> list[float]:
        if self.points < 1:
            raise ValueError("points must be >= 1")
        if not (self.start_hz > 0 and self.start_hz <= self.stop_hz):
            raise ValueError("start_hz must be positive and <= stop_hz")
        if self.points == 1:
            return [self.start_hz]
        if self.start_hz == self.stop_hz:
            raise ValueError("start_hz must be < stop_hz for a multi-point sweep")
        step = (self.stop_hz - self.start_hz) / (self.points - 1)
        return [self.start_hz + i * step for i in range(self.points)]

    def captures_for(self, freq_hz: float) -> int:
        return (
            self.captures_low_band
            if freq_hz < self.low_band_split_hz
            else self.captures_high_band
        )


def plan_lo(
    freq_hz: float,
    if_hz: float,
    lo_min_hz: float,
    lo_max_hz: float,
) -> tuple[float, float]:
    """Return (LO, detection frequency) for probing ``freq_hz``.

    Normally LO = f - IF and the tone is detected at +IF. Near the lower LO
    limit (e.g. f = 70 MHz with IF = 500 kHz) the LO would fall below the
    synthesizer minimum, so the offset sign is flipped: LO = f + IF and the
    tone is detected at -IF. The probed RF frequency stays exactly ``f``.
    The returned detection frequency is also the required TX tone frequency
    (sign included).
    """
    if not (lo_min_hz <= freq_hz <= lo_max_hz + if_hz):
        raise ValueError(
            f"frequency {freq_hz/1e6:.3f} MHz is outside the usable range "
            f"({lo_min_hz/1e6:.3f}..{lo_max_hz/1e6:.3f} MHz LO)"
        )
    lo = freq_hz - if_hz
    detect = if_hz
    if lo < lo_min_hz:
        lo = freq_hz + if_hz
        detect = -if_hz
    if not (lo_min_hz <= lo <= lo_max_hz):
        raise ValueError(
            f"cannot place LO for {freq_hz/1e6:.3f} MHz: "
            f"LO={lo/1e6:.3f} MHz is outside "
            f"{lo_min_hz/1e6:.3f}..{lo_max_hz/1e6:.3f} MHz"
        )
    return lo, detect


def nearest_supported(
    freq_hz: float,
    supported: tuple[float, float],
) -> float:
    """Clamp a frequency into a supported (min, max) tuple."""
    lo, hi = supported
    return min(max(freq_hz, lo), hi)
