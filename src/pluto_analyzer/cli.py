"""Command line interface: `pluto-na-cli info` and `pluto-na-cli measure`."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from . import __version__
from .calibration import Calibration
from .config import (
    DEFAULT_URI,
    SweepConfig,
    tone_amplitude_from_dbfs,
)
from .export import save_session
from .hwcheck import check_span, format_info, query_hardware
from .report import write_report, write_touchstone
from .sdr import PlutoVNA
from .sweep import SweepEngine


def _add_uri(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--uri",
        default=DEFAULT_URI,
        help="IIO URI, e.g. ip:192.168.2.1 or usb:3.3.5",
    )


def _add_sweep_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start", type=float, default=70.0, help="start, MHz")
    parser.add_argument("--stop", type=float, default=6000.0, help="stop, MHz")
    parser.add_argument("--points", type=int, default=500)
    parser.add_argument("--if-khz", type=float, default=500.0)
    parser.add_argument("--fs-mhz", type=float, default=8.0)
    parser.add_argument("--samples", type=int, default=1 << 18)
    parser.add_argument(
        "--captures", type=int, default=0, help="0 = automatic by band"
    )
    parser.add_argument("--tx-gain", type=float, default=0.0)
    parser.add_argument("--rx-gain", type=float, default=10.0)
    parser.add_argument(
        "--tone-db", type=float, default=-6.0,
        help="TX tone level, dBFS (lower it if the ADC clips)",
    )
    parser.add_argument(
        "--offset-db", type=float, default=0.0,
        help="reference offset added to S21 (e.g. -10 when the "
             "calibration path had a 10 dB pad)",
    )
    parser.add_argument("--dwell-ms", type=float, default=300.0)


def _config_from_args(args) -> SweepConfig:
    fs = int(args.fs_mhz * 1e6)
    return SweepConfig(
        start_hz=args.start * 1e6,
        stop_hz=args.stop * 1e6,
        points=args.points,
        if_hz=args.if_khz * 1e3,
        sample_rate=fs,
        num_samples=args.samples,
        tx_gain_db=args.tx_gain,
        rx_gain_db=args.rx_gain,
        tone_amplitude=tone_amplitude_from_dbfs(args.tone_db),
        offset_db=args.offset_db,
        dwell_s=args.dwell_ms / 1e3,
        rf_bandwidth_hz=int(min(4e6, fs)),
        captures_low_band=args.captures or 3,
        captures_high_band=args.captures or 2,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pluto-na-cli",
        description="Scalar S21 analyzer for ADALM-PLUTO (AD9363).",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="show Pluto capabilities")
    _add_uri(info)
    info.add_argument("--start", type=float, default=70.0, help="MHz")
    info.add_argument("--stop", type=float, default=6000.0, help="MHz")
    info.add_argument("--if-khz", type=float, default=500.0)
    info.add_argument("--json", action="store_true")

    measure = sub.add_parser("measure", help="run one S21 sweep")
    _add_uri(measure)
    _add_sweep_args(measure)
    measure.add_argument(
        "--out", type=Path, default=None, help="output directory"
    )
    measure.add_argument(
        "--cal", type=Path, default=None, help="thru calibration (.npz/.json)"
    )
    measure.add_argument(
        "--allow-bad-cal",
        action="store_true",
        help="apply a calibration marked CLIP/LOW/UNSTABLE",
    )
    measure.add_argument("--png", type=Path, default=None,
                         help="report PNG (matplotlib)")
    measure.add_argument("--pdf", type=Path, default=None,
                         help="report PDF (matplotlib)")
    measure.add_argument(
        "--touchstone", type=Path, default=None,
        help="optional .s2p (phase is 0, see header warnings)",
    )
    measure.add_argument("--json", action="store_true", help="print all points")
    measure.add_argument("--quiet", action="store_true")
    measure.add_argument(
        "--allow-busy",
        action="store_true",
        help="measure even if another client reconfigures the Pluto",
    )

    cal = sub.add_parser("cal", help="run a thru calibration sweep")
    _add_uri(cal)
    _add_sweep_args(cal)
    cal.add_argument(
        "--out",
        type=Path,
        default=Path("cal/s21_thru"),
        help="calibration file stem (.npz + .json)",
    )
    cal.add_argument("--quiet", action="store_true")
    cal.add_argument(
        "--allow-bad-cal",
        action="store_true",
        help="save even if the calibration sweep has CLIP/LOW/UNSTABLE points",
    )
    cal.add_argument(
        "--allow-busy",
        action="store_true",
        help="calibrate even if another client reconfigures the Pluto",
    )
    return parser


def _cmd_info(args) -> int:
    try:
        info = query_hardware(args.uri)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(info.to_dict(), indent=2))
        return 0
    print(format_info(info))
    errors, warnings = check_span(
        info, args.start * 1e6, args.stop * 1e6, args.if_khz * 1e3
    )
    print(
        f"\nSweep {args.start:.3f}..{args.stop:.3f} MHz: "
        f"{'FAIL' if errors else 'OK'}"
    )
    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error:   {error}")
    return 1 if errors else 0


def _progress(done: int, total: int, point) -> None:
    step = max(1, total // 10)
    if done == total or done % step == 0:
        flags = []
        if point.clip:
            flags.append("CLIP")
        if point.low:
            flags.append("LOW")
        if point.unstable:
            flags.append("UNSTABLE")
        print(
            f"[{done:4d}/{total}] {point.freq_hz/1e6:8.3f} MHz  "
            f"{point.dbfs:8.2f} dBFS  "
            f"{' '.join(flags) or 'ok'}",
            file=sys.stderr,
        )


def _prepare(args):
    config = _config_from_args(args)
    info = query_hardware(args.uri)
    errors, warnings = check_span(
        info, config.start_hz, config.stop_hz, config.if_hz
    )
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return config, info, warnings, False
    if not args.quiet:
        print(format_info(info))
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
    return config, info, warnings, True


def _run(args, config, info):
    with PlutoVNA(
        args.uri, config, (info.rx_lo.minimum, info.rx_lo.maximum)
    ) as sdr:
        changed = sdr.check_quiet()
        if changed and not args.allow_busy:
            print(
                "ERROR: the Pluto is being reconfigured by another client "
                f"(changed: {', '.join(changed)}).\n"
                "Free the device or use --allow-busy to measure anyway.",
                file=sys.stderr,
            )
            return None
        if changed:
            print(
                f"warning: shared device is busy ({', '.join(changed)}); "
                "measurements may be invalid",
                file=sys.stderr,
            )
        return SweepEngine(sdr, config).run(_progress)


def _cmd_measure(args) -> int:
    try:
        config, info, warnings, ok = _prepare(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not ok:
        return 2

    calibration = None
    cal_info = None
    if args.cal:
        try:
            calibration = Calibration.load(args.cal)
        except Exception as exc:
            print(f"ERROR: cannot load calibration: {exc}", file=sys.stderr)
            return 2
        if not calibration.quality_ok() and not args.allow_bad_cal:
            print(
                "ERROR: calibration is invalid (CLIP/LOW/UNSTABLE); "
                "recalibrate or use --allow-bad-cal",
                file=sys.stderr,
            )
            return 2
        cal_warnings = calibration.warnings(config.frequencies())
        cal_warnings.extend(calibration.settings_mismatch(config))
        if len(calibration.freqs) * 3 < config.points:
            cal_warnings.append(
                "calibration grid is much coarser than the sweep; "
                "recalibrate with more points"
            )
        for warning in cal_warnings:
            print(f"calibration warning: {warning}", file=sys.stderr)
        cal_info = {"path": str(args.cal), "warnings": cal_warnings}

    try:
        result = _run(args, config, info)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR during measurement: {exc}", file=sys.stderr)
        return 3
    if result is None:
        return 4

    if calibration is not None and result.points:
        corrected = calibration.apply(result.frequencies, result.db)
        corrected = corrected + config.offset_db
        for point, value in zip(result.points, corrected):
            point.s21_db = float(value)

    outdir = args.out or Path("results") / datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )
    extra = {"calibration": cal_info} if cal_info else None
    csv_path, meta_path = save_session(
        outdir, result, args.uri, info, warnings, extra
    )
    print("\nSummary:", json.dumps(result.summary()))
    print(f"CSV:  {csv_path}")
    print(f"meta: {meta_path}")
    title = f"S21 {args.start:.1f}-{args.stop:.1f} MHz"
    if args.png:
        path = write_report(args.png, result, title=title, uri=args.uri)
        print(f"PNG:  {path}")
    if args.pdf:
        path = write_report(args.pdf, result, title=title, uri=args.uri)
        print(f"PDF:  {path}")
    if args.touchstone:
        path = write_touchstone(args.touchstone, result)
        print(f"S2P:  {path}")
        print(
            "note: Touchstone is magnitude-only: phase=0, "
            "S11/S12/S22 are placeholders",
            file=sys.stderr,
        )
    if args.json:
        print(json.dumps([point.__dict__ for point in result.points], indent=2))
    return 0


def _cmd_cal(args) -> int:
    try:
        config, info, warnings, ok = _prepare(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not ok:
        return 2

    try:
        result = _run(args, config, info)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR during calibration: {exc}", file=sys.stderr)
        return 3
    if result is None:
        return 4
    if not result.points:
        print("ERROR: calibration sweep produced no points", file=sys.stderr)
        return 3

    calibration = Calibration.from_result(result, args.uri, info)
    summary = result.summary()
    if (
        summary.get("clip_count")
        or summary.get("low_count")
        or summary.get("unstable_count")
    ) and not args.allow_bad_cal:
        hints = []
        if summary.get("clip_count"):
            hints.append(
                "lower --rx-gain further (down to -3), lower --tone-db, "
                "or insert the 10 dB attenuator"
            )
        if summary.get("low_count"):
            hints.append("check the cable/attenuator connection")
        if summary.get("unstable_count"):
            hints.append("check that the beacon is off and nobody else uses the Pluto")
        print(
            "ERROR: calibration rejected (CLIP/LOW/UNSTABLE): "
            + "; ".join(hints),
            file=sys.stderr,
        )
        return 3
    npz_path, json_path = calibration.save(args.out)
    print("\nCalibration summary:", json.dumps(summary))
    print(f"NPZ:  {npz_path}")
    print(f"meta: {json_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "info":
        return _cmd_info(args)
    if args.command == "cal":
        return _cmd_cal(args)
    return _cmd_measure(args)


if __name__ == "__main__":
    raise SystemExit(main())
