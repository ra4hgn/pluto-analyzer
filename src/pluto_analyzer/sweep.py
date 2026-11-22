"""Sweep engine: frequency grid, per-point measurement, progress and stop."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .config import SweepConfig
from .sdr import PlutoVNA, SweepPoint

ProgressCallback = Callable[[int, int, SweepPoint], None]


@dataclass
class SweepResult:
    config: SweepConfig
    points: list[SweepPoint] = field(default_factory=list)
    stopped: bool = False
    duration_s: float = 0.0

    @property
    def frequencies(self) -> np.ndarray:
        return np.array([p.freq_hz for p in self.points], dtype=float)

    @property
    def db(self) -> np.ndarray:
        return np.array([p.dbfs for p in self.points], dtype=float)

    def summary(self) -> dict:
        if not self.points:
            return {"points": 0}
        db = self.db
        summary = {
            "points": len(self.points),
            "min_db": float(np.min(db)),
            "max_db": float(np.max(db)),
            "mean_db": float(np.mean(db)),
            "clip_count": int(sum(p.clip for p in self.points)),
            "low_count": int(sum(p.low for p in self.points)),
            "unstable_count": int(sum(p.unstable for p in self.points)),
            "duration_s": round(self.duration_s, 3),
            "stopped": self.stopped,
        }
        calibrated = [
            p.s21_db for p in self.points if p.s21_db is not None
        ]
        if calibrated:
            values = np.asarray(calibrated, dtype=float)
            summary["s21_min_db"] = float(np.min(values))
            summary["s21_max_db"] = float(np.max(values))
            summary["s21_mean_db"] = float(np.mean(values))
        return summary


class SweepEngine:
    """Runs a sweep over ``config.frequencies()`` using a PlutoVNA."""

    def __init__(self, sdr: PlutoVNA, config: SweepConfig) -> None:
        self.sdr = sdr
        self.config = config
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def reset_stop(self) -> None:
        self._stop_requested = False

    def run(self, progress: ProgressCallback | None = None) -> SweepResult:
        if self.sdr.config != self.config:
            self.sdr.configure(self.config)
        frequencies = self.config.frequencies()
        started = time.monotonic()
        result = SweepResult(config=self.config)

        for index, freq in enumerate(frequencies):
            if self._stop_requested:
                result.stopped = True
                break
            point = self.sdr.measure_point(freq)
            result.points.append(point)
            if progress is not None:
                progress(index + 1, len(frequencies), point)

        result.duration_s = time.monotonic() - started
        return result
