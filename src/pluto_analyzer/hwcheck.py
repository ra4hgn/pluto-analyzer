"""Hardware discovery and validation for the Pluto SDR.

Reads the actual capabilities from the device (hw_model, firmware, LO and
gain ranges) instead of relying on constants, so the same code works on a
stock AD9363 (325 MHz..3.8 GHz) and on a range-extended unit.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from typing import Iterable

import iio

from .config import DEFAULT_URI, plan_lo

RATED_MIN_HZ = 325e6
RATED_MAX_HZ = 3.8e9


@dataclass(frozen=True)
class Range:
    minimum: float
    step: float
    maximum: float

    @classmethod
    def parse(cls, text: str | None) -> "Range | None":
        if not text:
            return None
        parts = text.strip().strip("[]").replace(",", " ").split()
        try:
            values = [float(p) for p in parts]
        except ValueError:
            return None
        if len(values) == 3:
            return cls(values[0], values[1], values[2])
        if len(values) == 2:
            return cls(values[0], 1.0, values[1])
        if len(values) == 1:
            return cls(values[0], 0.0, values[0])
        return None

    def __str__(self) -> str:
        if self.minimum == self.maximum:
            return f"{self.minimum:g}"
        return f"{self.minimum:g}..{self.maximum:g} (step {self.step:g})"


def _fmt_mhz(value: Range | None) -> str:
    if value is None:
        return "?"
    return (
        f"{value.minimum/1e6:.3f}..{value.maximum/1e6:.3f} MHz "
        f"(step {value.step:g} Hz)"
    )


def _fmt_db(value: Range | None) -> str:
    if value is None:
        return "?"
    if value.minimum == value.maximum:
        return f"{value.minimum:g} dB (single step)"
    return (
        f"{value.minimum:g}..{value.maximum:g} dB (step {value.step:g} dB)"
    )


@dataclass
class HardwareInfo:
    uri: str
    hw_model: str
    fw_version: str
    phy_model: str
    rx_lo: Range
    tx_lo: Range
    rx_gain: Range | None
    tx_gain: Range | None
    sample_rate: Range | None
    rx_bandwidth: Range | None
    rx_channels: tuple[str, ...]
    tx_channels: tuple[str, ...]

    @property
    def extended(self) -> bool:
        return self.rx_lo.minimum < RATED_MIN_HZ

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("rx_lo", "tx_lo", "rx_gain", "tx_gain",
                    "sample_rate", "rx_bandwidth"):
            value = getattr(self, key)
            data[key] = None if value is None else asdict(value)
        data["extended"] = self.extended
        return data


def _attr(channel, name: str) -> str | None:
    try:
        return channel.attrs[name].value
    except KeyError:
        return None


def _find_channel(device, channel_id: str, output: bool):
    for channel in device.channels:
        if channel.id == channel_id and bool(channel.output) == output:
            return channel
    return None


def _require_range(text: str | None, what: str) -> Range:
    value = Range.parse(text)
    if value is None:
        raise RuntimeError(f"cannot parse {what}: {text!r}")
    return value


def query_hardware(uri: str = DEFAULT_URI) -> HardwareInfo:
    """Open an IIO context and read the device capabilities."""
    try:
        ctx = iio.Context(uri)
    except Exception as exc:  # pylibiio raises OSError/RuntimeError
        raise RuntimeError(f"cannot open IIO context {uri!r}: {exc}") from exc

    phy = ctx.find_device("ad9361-phy")
    if phy is None:
        raise RuntimeError(f"'ad9361-phy' not found on {uri}")

    rx = _find_channel(phy, "voltage0", output=False)
    tx = _find_channel(phy, "voltage0", output=True)
    if rx is None or tx is None:
        raise RuntimeError("RX/TX voltage0 channels not found")

    rx_lo_ch = _find_channel(phy, "altvoltage0", output=True)
    tx_lo_ch = _find_channel(phy, "altvoltage1", output=True)
    if rx_lo_ch is None or tx_lo_ch is None:
        raise RuntimeError("RX_LO/TX_LO channels not found")

    attrs = ctx.attrs
    return HardwareInfo(
        uri=uri,
        hw_model=attrs.get("hw_model", "?"),
        fw_version=attrs.get("fw_version", "?"),
        phy_model=attrs.get("ad9361-phy,model", phy.name or "?"),
        rx_lo=_require_range(_attr(rx_lo_ch, "frequency_available"), "RX_LO"),
        tx_lo=_require_range(_attr(tx_lo_ch, "frequency_available"), "TX_LO"),
        rx_gain=Range.parse(_attr(rx, "hardwaregain_available")),
        tx_gain=Range.parse(_attr(tx, "hardwaregain_available")),
        sample_rate=Range.parse(_attr(rx, "sampling_frequency_available")),
        rx_bandwidth=Range.parse(_attr(rx, "rf_bandwidth_available")),
        rx_channels=tuple(
            c.id
            for c in phy.channels
            if not c.output
            and c.id.startswith("voltage")
            and "hardwaregain_available" in c.attrs
        ),
        tx_channels=tuple(
            c.id
            for c in phy.channels
            if c.output
            and c.id.startswith("voltage")
            and "hardwaregain_available" in c.attrs
        ),
    )


def check_span(
    info: HardwareInfo,
    start_hz: float,
    stop_hz: float,
    if_hz: float,
) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a requested sweep span."""
    errors: list[str] = []
    warnings: list[str] = []
    if not (0 < start_hz <= stop_hz):
        errors.append("start must be positive and not greater than stop")
        return errors, warnings

    for label, freq in (("start", start_hz), ("stop", stop_hz)):
        try:
            plan_lo(freq, if_hz, info.rx_lo.minimum, info.rx_lo.maximum)
        except ValueError as exc:
            errors.append(f"{label}: {exc}")

    if start_hz < RATED_MIN_HZ or stop_hz > RATED_MAX_HZ:
        warnings.append(
            "sweep extends outside the AD9363 rated range "
            f"({RATED_MIN_HZ/1e6:.0f}..{RATED_MAX_HZ/1e6:.0f} MHz); "
            "amplitude flatness and noise floor are not guaranteed there"
        )
    if stop_hz > 5.9e9:
        warnings.append("stop is close to the 6 GHz synthesizer limit")
    if start_hz < 4.0e9 < stop_hz:
        warnings.append(
            "sweep crosses the 4 GHz AD9361 gain-table boundary; "
            "the reference level steps there, so calibrate on the same "
            "(or denser) grid as the measurement"
        )
    if not info.extended:
        warnings.append(
            "range extension not detected (LO min is above 325 MHz): "
            "70..6000 MHz would not work without the fw_env patch"
        )
    return errors, warnings


def format_info(info: HardwareInfo) -> str:
    lines = [
        f"URI:              {info.uri}",
        f"hw_model:         {info.hw_model}",
        f"phy model:        {info.phy_model}",
        f"firmware:         {info.fw_version}",
        f"range extended:   {'yes' if info.extended else 'no'}",
        f"RX_LO:            {_fmt_mhz(info.rx_lo)}",
        f"TX_LO:            {_fmt_mhz(info.tx_lo)}",
        f"RX gain:          {_fmt_db(info.rx_gain)}",
        f"TX gain:          {_fmt_db(info.tx_gain)}",
        f"sample rate:      {_fmt_mhz(info.sample_rate)}",
        f"RX channels:      {', '.join(info.rx_channels)}",
        f"TX channels:      {', '.join(info.tx_channels)}",
    ]
    return "\n".join(lines)


def _print_list(title: str, items: Iterable[str]) -> None:
    for item in items:
        print(f"{title}: {item}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect PlutoSDR capabilities used by the S21 analyzer."
    )
    parser.add_argument("--uri", default=DEFAULT_URI)
    parser.add_argument("--start", type=float, default=70.0, help="start, MHz")
    parser.add_argument("--stop", type=float, default=6000.0, help="stop, MHz")
    parser.add_argument(
        "--if", dest="if_khz", type=float, default=500.0, help="IF tone, kHz"
    )
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    args = parser.parse_args(argv)

    try:
        info = query_hardware(args.uri)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(info.to_dict(), indent=2))
    else:
        print(format_info(info))

    start = args.start * 1e6
    stop = args.stop * 1e6
    if_hz = args.if_khz * 1e3
    errors, warnings = check_span(info, start, stop, if_hz)
    print(
        f"\nSweep {start/1e6:.3f}..{stop/1e6:.3f} MHz, IF {if_hz/1e3:.1f} kHz: "
        f"{'FAIL' if errors else 'OK'}"
    )
    _print_list("warning", warnings)
    _print_list("error", errors)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
