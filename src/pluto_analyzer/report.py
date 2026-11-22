"""Static report export (matplotlib) and optional Touchstone output."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .sweep import SweepResult  # noqa: E402


def _values(result: SweepResult) -> tuple[np.ndarray, np.ndarray, bool]:
    if not result.points:
        raise ValueError("cannot export an empty sweep")
    x = result.frequencies / 1e6
    calibrated = all(p.s21_db is not None for p in result.points)
    if calibrated:
        y = np.asarray([p.s21_db for p in result.points], dtype=float)
    else:
        y = result.db
    return x, y, calibrated


def write_report(
    path: Path,
    result: SweepResult,
    title: str = "S21",
    uri: str = "",
) -> Path:
    """Matplotlib PNG/PDF report (suffix decides the format)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    x, y, calibrated = _values(result)
    label = "S21" if calibrated else "raw level (dBFS)"

    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=140)
    ax.plot(x, y, color="#1f77b4", lw=1.4, label=label)
    ax.scatter(x, y, s=10, color="#d62728", zorder=3, label="sweep points")
    ax.set_xlabel("Frequency, MHz")
    ax.set_ylabel("S21, dB" if calibrated else "Level, dBFS")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=8)

    finite = np.isfinite(y)
    if np.any(finite):
        yy = y[finite]
        xx = x[finite]
        i_min = int(np.argmin(yy))
        i_max = int(np.argmax(yy))
        ax.annotate(
            f"min {yy[i_min]:+.2f} dB @ {xx[i_min]:.1f} MHz",
            (xx[i_min], yy[i_min]),
            textcoords="offset points", xytext=(6, -14), fontsize=8,
        )
        ax.annotate(
            f"max {yy[i_max]:+.2f} dB @ {xx[i_max]:.1f} MHz",
            (xx[i_max], yy[i_max]),
            textcoords="offset points", xytext=(6, 10), fontsize=8,
        )

    summary = result.summary()
    footer = (
        f"points: {summary.get('points')}   "
        f"calibrated: {'yes' if calibrated else 'no'}   "
        f"URI: {uri or '-'}   "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    fig.text(0.01, 0.01, footer, fontsize=7, color="#555555")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path)
    plt.close(fig)
    return path


def write_touchstone(path: Path, result: SweepResult) -> Path:
    """Write an .s2p with measured |S21| and explicit placeholder warnings.

    Touchstone cannot store magnitude only, so the phase is written as 0 deg
    and S11/S12/S22 as -100 dB. The header states this explicitly.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.suffix:
        path = path.with_suffix(".s2p")
    x_hz = result.frequencies
    _, s21_db, calibrated = _values(result)
    lines = [
        "! Pluto S21 Analyzer",
        "! WARNING: magnitude-only measurement.",
        "! WARNING: phase is NOT measured and is written as 0 deg.",
        "! WARNING: S11, S12 and S22 are placeholders (-100 dB).",
        f"! calibrated: {'yes' if calibrated else 'no'}",
        f"! created: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "# Hz S DB R 50",
    ]
    for freq, value in zip(x_hz, s21_db):
        lines.append(
            f"{freq:.0f} -100 0 {value:.4f} 0 -100 0 -100 0"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
