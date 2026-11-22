"""Measurement export: CSV trace and JSON metadata."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import __version__
from .config import SweepConfig
from .hwcheck import HardwareInfo
from .sdr import SweepPoint
from .sweep import SweepResult

CSV_FIELDS = (
    "freq_hz",
    "lo_hz",
    "tone_hz",
    "captures",
    "amplitude",
    "dbfs",
    "s21_db",
    "peak",
    "spread_db",
    "clip",
    "low",
    "unstable",
)


def write_csv(path: Path, points: Iterable[SweepPoint]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for point in points:
            writer.writerow({name: getattr(point, name) for name in CSV_FIELDS})
    return path


def write_meta(
    path: Path,
    result: SweepResult,
    uri: str,
    hardware: HardwareInfo | None = None,
    warnings: Iterable[str] = (),
    extra: dict | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "app": "pluto-analyzer",
        "version": __version__,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "uri": uri,
        "config": asdict(result.config),
        "hardware": None if hardware is None else hardware.to_dict(),
        "warnings": list(warnings),
        "summary": result.summary(),
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def save_session(
    directory: Path,
    result: SweepResult,
    uri: str,
    hardware: HardwareInfo | None = None,
    warnings: Iterable[str] = (),
    extra: dict | None = None,
) -> tuple[Path, Path]:
    csv_path = write_csv(directory / "trace.csv", result.points)
    meta_path = write_meta(
        directory / "meta.json", result, uri, hardware, warnings, extra
    )
    return csv_path, meta_path
