#!/usr/bin/env python3
"""Loopback test: Pluto TX -> cable/attenuator -> Pluto RX.

Transmits a CW tone, retunes LO/RX_LO over the requested frequencies and
reports the lock-in amplitude in dBFS together with the ADC peak (CLIP flag).
No calibration is applied, so this is a raw path-loss / tone-presence check.

    .venv/bin/python scripts/loopback_test.py --uri ip:192.168.2.1 \
        --freqs 70,100,433,900,1300,2400,3500,5800,6000 --csv results/loopback.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pluto_analyzer.config import DEFAULT_URI, SweepConfig, plan_lo
from pluto_analyzer.hwcheck import format_info, query_hardware
from pluto_analyzer.sdr import PlutoVNA


def parse_freqs(text: str) -> list[float]:
    values = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if part:
            values.append(float(part) * 1e6)
    if not values:
        raise argparse.ArgumentTypeError("empty frequency list")
    return values


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pluto S21 loopback smoke test.")
    parser.add_argument("--uri", default=DEFAULT_URI)
    parser.add_argument(
        "--freqs",
        type=parse_freqs,
        default=parse_freqs("70,100,433,900,1300,2400,3500,5800,6000"),
        help="comma separated frequencies, MHz",
    )
    parser.add_argument("--if-khz", type=float, default=500.0)
    parser.add_argument("--fs-mhz", type=float, default=8.0)
    parser.add_argument("--samples", type=int, default=1 << 18)
    parser.add_argument(
        "--captures", type=int, default=0, help="0 = automatic by band"
    )
    parser.add_argument("--tx-gain", type=float, default=0.0)
    parser.add_argument("--rx-gain", type=float, default=10.0)
    parser.add_argument("--dwell-ms", type=float, default=300.0)
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--allow-busy",
        action="store_true",
        help="measure even if another client reconfigures the Pluto",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    fs = int(args.fs_mhz * 1e6)
    cfg = SweepConfig(
        if_hz=args.if_khz * 1e3,
        sample_rate=fs,
        num_samples=args.samples,
        tx_gain_db=args.tx_gain,
        rx_gain_db=args.rx_gain,
        dwell_s=args.dwell_ms / 1e3,
        rf_bandwidth_hz=int(min(4e6, fs)),
        start_hz=min(args.freqs),
        stop_hz=max(args.freqs),
        points=len(args.freqs),
        captures_low_band=args.captures or 4,
        captures_high_band=args.captures or 1,
    )

    try:
        info = query_hardware(args.uri)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(format_info(info))

    for freq in args.freqs:
        try:
            plan_lo(freq, cfg.if_hz, info.rx_lo.minimum, info.rx_lo.maximum)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    results = []
    try:
        with PlutoVNA(
            args.uri, cfg, (info.rx_lo.minimum, info.rx_lo.maximum)
        ) as sdr:
            changed = sdr.check_quiet()
            if changed and not args.allow_busy:
                print(
                    "ERROR: the Pluto is being reconfigured by another client "
                    f"(changed: {', '.join(changed)}).\n"
                    "Free the device or use --allow-busy to measure anyway.",
                    file=sys.stderr,
                )
                return 4
            print(
                f"\n{'freq MHz':>10} {'LO MHz':>10} {'tone kHz':>9} "
                f"{'amp':>9} {'dBFS':>8} {'peak':>7} flags"
            )
            for freq in args.freqs:
                point = sdr.measure_point(freq, args.captures or None)
                flags = []
                if point.clip:
                    flags.append("CLIP")
                if point.low:
                    flags.append("LOW")
                if point.unstable:
                    flags.append("UNSTABLE")
                results.append(point.__dict__)
                print(
                    f"{point.freq_hz/1e6:10.3f} {point.lo_hz/1e6:10.3f} "
                    f"{point.tone_hz/1e3:9.1f} {point.amplitude:9.1f} "
                    f"{point.dbfs:8.2f} {point.peak:7.0f} "
                    f"{' '.join(flags) or 'ok'}"
                )
    except Exception as exc:
        print(f"ERROR during measurement: {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(results, indent=2))
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(results[0]))
            writer.writeheader()
            writer.writerows(results)
        print(f"\nCSV written: {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
