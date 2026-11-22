#!/usr/bin/env python3
"""Plot a stored thru calibration (the reference path response).

    .venv/bin/python scripts/plot_calibration.py cal/s21_thru.npz \
        --png cal/s21_thru.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pluto_analyzer.calibration import Calibration  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plot a thru calibration.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--png", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        cal = Calibration.load(args.path)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    x = cal.freqs / 1e6
    y = cal.thru_db
    print(
        f"points: {len(x)}  min/mean/max: "
        f"{y.min():.2f} / {y.mean():.2f} / {y.max():.2f} dBFS"
    )
    meta = cal.meta or {}
    print("created:", meta.get("created", "?"), "uri:", meta.get("uri", "?"))
    print("quality_ok:", cal.quality_ok())

    fig, ax = plt.subplots(figsize=(10, 5.4), dpi=140)
    ax.plot(x, y, color="#1f77b4", lw=1.4)
    ax.set_xlabel("Frequency, MHz")
    ax.set_ylabel("Reference path level, dBFS")
    ax.set_title("Thru calibration: reference path response")
    ax.grid(True, alpha=0.3)
    ax.annotate(
        f"min {y.min():.2f} @ {x[np.argmin(y)]:.1f} MHz",
        (x[np.argmin(y)], y.min()),
        textcoords="offset points", xytext=(6, -12), fontsize=8,
    )
    ax.annotate(
        f"max {y.max():.2f} @ {x[np.argmax(y)]:.1f} MHz",
        (x[np.argmax(y)], y.max()),
        textcoords="offset points", xytext=(6, 10), fontsize=8,
    )
    fig.tight_layout()

    output = args.png or args.path.with_suffix(".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)
    print(f"PNG: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
