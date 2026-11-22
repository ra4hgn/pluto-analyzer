#!/usr/bin/env python3
"""Compare two S21 traces: insertion loss of a DUT or run-to-run stability.

    .venv/bin/python scripts/compare_traces.py reference.csv dut.csv [--png out.png]

Uses s21_db when present, otherwise dbfs. Reports delta = dut - reference.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np


def load_trace(path: Path) -> tuple[np.ndarray, np.ndarray]:
    freqs: list[float] = []
    values: list[float] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            value = row.get("s21_db") or row.get("dbfs") or ""
            if not value:
                continue
            freqs.append(float(row["freq_hz"]))
            values.append(float(value))
    if len(freqs) < 2:
        raise ValueError(f"{path}: not enough points")
    order = np.argsort(freqs)
    return np.asarray(freqs)[order], np.asarray(values)[order]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("dut", type=Path)
    parser.add_argument("--png", type=Path, default=None)
    parser.add_argument("--tolerance", type=float, default=None,
                        help="expected insertion loss, dB (e.g. -10)")
    args = parser.parse_args(argv)

    try:
        ref_f, ref_v = load_trace(args.reference)
        dut_f, dut_v = load_trace(args.dut)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    common = dut_f[(dut_f >= ref_f.min()) & (dut_f <= ref_f.max())]
    if common.size < 2:
        print("ERROR: traces do not overlap", file=sys.stderr)
        return 2
    ref_on_common = np.interp(common, ref_f, ref_v)
    dut_on_common = np.interp(common, dut_f, dut_v)
    delta = dut_on_common - ref_on_common

    print(f"{'freq MHz':>10} {'reference':>10} {'dut':>10} {'delta dB':>9}")
    for f, ref, dut, d in zip(common, ref_on_common, dut_on_common, delta):
        print(f"{f/1e6:10.1f} {ref:10.2f} {dut:10.2f} {d:9.2f}")
    print(
        f"\ndelta min/mean/max: {delta.min():.2f} / {delta.mean():.2f} / "
        f"{delta.max():.2f} dB  (spread {np.ptp(delta):.2f} dB)"
    )
    if args.tolerance is not None:
        error = delta - args.tolerance
        print(
            f"expected {args.tolerance:+.2f} dB: worst deviation "
            f"{np.max(np.abs(error)):.2f} dB"
        )

    if args.png:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(10, 6))
        ax1.plot(ref_f / 1e6, ref_v, label="reference")
        ax1.plot(dut_f / 1e6, dut_v, label="dut")
        ax1.set_ylabel("Level, dB")
        ax1.grid(True, alpha=0.3)
        ax1.legend()
        ax2.plot(common / 1e6, delta, color="#d62728")
        ax2.set_xlabel("Frequency, MHz")
        ax2.set_ylabel("Delta, dB")
        ax2.grid(True, alpha=0.3)
        fig.tight_layout()
        args.png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(args.png)
        plt.close(fig)
        print(f"PNG: {args.png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
