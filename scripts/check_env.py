#!/usr/bin/env python3
"""Environment / dependency / Pluto capability check (M0).

Run with the project venv:
    .venv/bin/python scripts/check_env.py [--uri ip:192.168.2.1]
"""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _pkg_version(module: str, attr: str = "__version__") -> str:
    try:
        mod = __import__(module, fromlist=["*"])
        value = getattr(mod, attr, None)
        if value is None and module == "iio":
            version = getattr(mod, "version", None)
            if isinstance(version, tuple):
                return version[2]
        return str(value if value is not None else "?")
    except Exception as exc:
        return f"MISSING ({exc.__class__.__name__}: {exc})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the Pluto S21 environment.")
    parser.add_argument("--uri", default=None, help="IIO URI to probe (optional)")
    args = parser.parse_args(argv)

    print("=== Python ===")
    print(f"interpreter: {sys.executable}")
    print(f"version:     {sys.version.split()[0]}")
    print(f"platform:    {platform.platform()}")

    print("\n=== Packages ===")
    for label, module, attr in (
        ("numpy", "numpy", "__version__"),
        ("scipy", "scipy", "__version__"),
        ("matplotlib", "matplotlib", "__version__"),
        ("pyqtgraph", "pyqtgraph", "__version__"),
        ("PyQt6", "PyQt6.QtCore", "PYQT_VERSION_STR"),
        ("pyadi-iio", "adi", "__version__"),
        ("libiio", "iio", "__version__"),
    ):
        print(f"{label:12s} {_pkg_version(module, attr)}")

    try:
        import iio

        try:
            contexts = iio.scan_contexts()
        except Exception as exc:
            print(f"scan_contexts failed: {exc}")
            contexts = {}
        print("contexts:")
        for uri, description in contexts.items():
            print(f"  {uri:16s} {description}")
    except Exception as exc:
        print(f"\nERROR: pylibiio import failed: {exc}")
        return 1

    if args.uri is None:
        print("\n(no --uri given, hardware query skipped)")
        return 0

    try:
        from pluto_analyzer.hwcheck import check_span, format_info, query_hardware
    except Exception as exc:
        print(f"\nERROR: cannot import pluto_analyzer: {exc}")
        return 1

    print("\n=== Hardware ===")
    try:
        info = query_hardware(args.uri)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(format_info(info))

    errors, warnings = check_span(info, 70e6, 6e9, 500e3)
    print(
        "\nSweep 70.000..6000.000 MHz, IF 500.0 kHz: "
        f"{'FAIL' if errors else 'OK'}"
    )
    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error:   {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
