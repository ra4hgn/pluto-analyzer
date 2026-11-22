"""Thru calibration: build from a reference sweep, save/load, apply.

The calibrated value is

    S21_dB(f) = dBFS_dut(f) - dBFS_thru(f)

where the thru reference already includes cables and any fixed attenuator,
so the reference path reads 0 dB after calibration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import __version__
from .hwcheck import HardwareInfo
from .sweep import SweepResult


@dataclass
class Calibration:
    freqs: np.ndarray
    thru_db: np.ndarray
    meta: dict = field(default_factory=dict)

    # --- construction -------------------------------------------------
    @classmethod
    def from_result(
        cls,
        result: SweepResult,
        uri: str,
        hardware: HardwareInfo | None = None,
    ) -> "Calibration":
        if not result.points:
            raise ValueError("cannot calibrate from an empty sweep")
        freqs = result.frequencies
        thru_db = result.db
        meta = {
            "app": "pluto-analyzer",
            "version": __version__,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "uri": uri,
            "hardware": None if hardware is None else hardware.to_dict(),
            "config": {
                "start_hz": result.config.start_hz,
                "stop_hz": result.config.stop_hz,
                "points": result.config.points,
                "if_hz": result.config.if_hz,
                "tx_gain_db": result.config.tx_gain_db,
                "rx_gain_db": result.config.rx_gain_db,
                "tone_amplitude": result.config.tone_amplitude,
                "tone_db": float(
                    20.0 * np.log10(max(result.config.tone_amplitude, 1) / 32768.0)
                ),
                "sample_rate": result.config.sample_rate,
            },
            "summary": result.summary(),
        }
        return cls(freqs=freqs, thru_db=thru_db, meta=meta)

    # --- math ----------------------------------------------------------
    def correction(self, freqs: np.ndarray) -> np.ndarray:
        """Interpolate the thru response (log-frequency) onto ``freqs``."""
        freqs = np.asarray(freqs, dtype=float)
        log_ref = np.log10(np.clip(self.freqs, 1.0, None))
        log_new = np.log10(np.clip(freqs, 1.0, None))
        return np.interp(log_new, log_ref, self.thru_db)

    def apply(self, freqs: np.ndarray, db: np.ndarray) -> np.ndarray:
        return np.asarray(db, dtype=float) - self.correction(freqs)

    def coverage(self, freqs: np.ndarray) -> np.ndarray:
        """Boolean mask: True where the point is inside the calibrated span."""
        freqs = np.asarray(freqs, dtype=float)
        return (freqs >= self.freqs.min()) & (freqs <= self.freqs.max())

    def quality_ok(self) -> bool:
        """False if the calibration sweep had CLIP/LOW/UNSTABLE points."""
        summary = self.meta.get("summary", {}) or {}
        return not any(
            summary.get(key)
            for key in ("clip_count", "low_count", "unstable_count")
        )

    def settings_mismatch(self, config) -> list[str]:
        """Differences between the calibration settings and a sweep config."""
        meta_config = self.meta.get("config", {}) or {}
        checks = (
            ("rx_gain_db", "RX gain", float(config.rx_gain_db)),
            ("tx_gain_db", "TX gain", float(config.tx_gain_db)),
            ("tone_amplitude", "Tone", float(config.tone_amplitude)),
            ("if_hz", "IF", float(config.if_hz)),
            ("sample_rate", "sample rate", float(config.sample_rate)),
        )
        result: list[str] = []
        for key, label, current in checks:
            previous = meta_config.get(key)
            if previous is None:
                continue
            if abs(float(previous) - current) > 1e-6:
                result.append(f"{label}: calibration {float(previous):g}, now {current:g}")
        return result

    def warnings(self, freqs: np.ndarray) -> list[str]:
        result: list[str] = []
        outside = ~self.coverage(freqs)
        if np.any(outside):
            result.append(
                f"{int(outside.sum())} point(s) outside the calibrated range "
                f"{self.freqs.min()/1e6:.3f}..{self.freqs.max()/1e6:.3f} MHz"
            )
        summary = self.meta.get("summary", {}) or {}
        bad = [
            f"{summary[key]} {label}"
            for key, label in (
                ("clip_count", "CLIP"),
                ("low_count", "LOW"),
                ("unstable_count", "UNSTABLE"),
            )
            if summary.get(key)
        ]
        if bad:
            result.append(
                "calibration sweep had " + ", ".join(bad)
                + "; recalibrate with a clean reference"
            )
        created = self.meta.get("created")
        if created:
            try:
                when = datetime.fromisoformat(created)
                age_days = (datetime.now(timezone.utc) - when).days
                if age_days > 30:
                    result.append(f"calibration is {age_days} days old")
            except ValueError:
                pass
        return result

    # --- storage -------------------------------------------------------
    def save(self, path: Path) -> tuple[Path, Path]:
        path = Path(path)
        if path.suffix in (".npz", ".json"):
            path = path.with_suffix("")
        path.parent.mkdir(parents=True, exist_ok=True)
        npz_path = path.with_suffix(".npz")
        json_path = path.with_suffix(".json")
        np.savez(
            npz_path, freqs_hz=self.freqs, thru_db=self.thru_db
        )
        json_path.write_text(
            json.dumps(self.meta, indent=2), encoding="utf-8"
        )
        return npz_path, json_path

    @classmethod
    def load(cls, path: Path) -> "Calibration":
        path = Path(path)
        if path.suffix == ".json":
            path = path.with_suffix("")
        npz_path = path.with_suffix(".npz")
        if not npz_path.exists():
            raise FileNotFoundError(f"calibration not found: {npz_path}")
        with np.load(npz_path) as data:
            freqs = np.asarray(data["freqs_hz"], dtype=float)
            thru_db = np.asarray(data["thru_db"], dtype=float)
        json_path = path.with_suffix(".json")
        meta = {}
        if json_path.exists():
            meta = json.loads(json_path.read_text(encoding="utf-8"))
        if freqs.size < 2 or freqs.size != thru_db.size:
            raise ValueError(f"invalid calibration file: {npz_path}")
        return cls(freqs=freqs, thru_db=thru_db, meta=meta)
