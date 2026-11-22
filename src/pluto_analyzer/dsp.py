"""DSP helpers: CW tone generation, complex lock-in detector, dB math."""

from __future__ import annotations

import numpy as np

from .config import ADC_FULL_SCALE


def make_tone(
    tone_hz: float,
    sample_rate: float,
    num_samples: int,
    amplitude: int,
) -> np.ndarray:
    """Complex baseband tone for the cyclic TX buffer.

    ``tone_hz`` may be negative: the sign must match the detection frequency
    returned by :func:`pluto_analyzer.config.plan_lo` so that the probed RF
    frequency stays exactly on the requested grid point.
    """
    n = np.arange(num_samples, dtype=np.float64)
    return (amplitude * np.exp(2j * np.pi * tone_hz * n / sample_rate)).astype(
        np.complex64
    )


def _decimation(sample_rate: float, detect_hz: float) -> int:
    """Integer decimation factor if fs/f_detect is (almost) integer."""
    ratio = sample_rate / abs(detect_hz)
    k = int(round(ratio))
    if k >= 1 and abs(ratio - k) < 1e-6:
        return k
    return 1


def lockin_amplitude(
    samples: np.ndarray,
    detect_hz: float,
    sample_rate: float,
    decim: int | None = None,
    window: str = "hann",
) -> float:
    """Amplitude of the tone at ``detect_hz`` (complex lock-in).

    Mixing to zero, optional block decimation by ``fs/|detect_hz|`` (sinc
    nulls at multiples of the detection frequency) and windowed coherent
    averaging. Returns the amplitude in ADC counts.
    """
    x = np.asarray(samples, dtype=np.complex128)
    if x.size == 0:
        raise ValueError("empty capture")
    n = np.arange(x.size, dtype=np.float64)
    y = x * np.exp(-2j * np.pi * detect_hz * n / sample_rate)

    k = _decimation(sample_rate, detect_hz) if decim is None else int(decim)
    if k > 1 and x.size % k == 0:
        y = y.reshape(-1, k).mean(axis=1)

    m = y.size
    if window == "hann" and m >= 8:
        w = np.hanning(m)
    else:
        w = np.ones(m)
    return float(abs(np.sum(y * w)) / np.sum(w))


def adc_peak(samples: np.ndarray) -> float:
    """Peak absolute value of the raw capture (ADC counts)."""
    return float(np.max(np.abs(np.asarray(samples))))


def is_clipping(peak: float, full_scale: float = ADC_FULL_SCALE) -> bool:
    return peak >= 0.8 * full_scale


def to_db(amplitude: float, eps: float = 1e-15) -> float:
    return 20.0 * np.log10(max(amplitude, eps))


def to_dbfs(
    amplitude: float,
    full_scale: float = ADC_FULL_SCALE,
    eps: float = 1e-15,
) -> float:
    return to_db(amplitude / full_scale, eps)


def smooth_trace(y: np.ndarray, k: int) -> np.ndarray:
    """NaN-aware moving average (used by the GUI later)."""
    y = np.asarray(y, dtype=float)
    if k <= 1 or y.size < k:
        return y
    mask = np.isfinite(y).astype(float)
    win = np.ones(k)
    num = np.convolve(np.nan_to_num(y), win, "same")
    den = np.convolve(mask, win, "same")
    out = np.full_like(y, np.nan)
    good = den > 0
    out[good] = num[good] / den[good]
    return out
