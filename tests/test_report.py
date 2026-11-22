"""Tests for static report and Touchstone export."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from pluto_analyzer.config import SweepConfig
from pluto_analyzer.report import write_report, write_touchstone
from pluto_analyzer.sdr import SweepPoint
from pluto_analyzer.sweep import SweepResult


def make_result(calibrated=True) -> SweepResult:
    config = SweepConfig(start_hz=100e6, stop_hz=300e6, points=3)
    result = SweepResult(config=config)
    for index, freq in enumerate(config.frequencies()):
        result.points.append(
            SweepPoint(
                freq_hz=freq,
                lo_hz=freq - config.if_hz,
                tone_hz=config.if_hz,
                captures=2,
                amplitude=100.0,
                dbfs=-20.0 - index,
                peak=150.0,
                clip=False,
                low=False,
                s21_db=(-1.5 * index if calibrated else None),
            )
        )
    return result


class TestReport(unittest.TestCase):
    def test_png_and_pdf(self):
        result = make_result()
        with tempfile.TemporaryDirectory() as tmp:
            png = write_report(Path(tmp) / "report.png", result, uri="test:uri")
            pdf = write_report(Path(tmp) / "report.pdf", result, uri="test:uri")
            self.assertGreater(png.stat().st_size, 1000)
            self.assertGreater(pdf.stat().st_size, 1000)

    def test_empty_rejected(self):
        config = SweepConfig(start_hz=100e6, stop_hz=200e6, points=2)
        with self.assertRaises(ValueError):
            write_report(Path("/tmp/empty.png"), SweepResult(config=config))


class TestTouchstone(unittest.TestCase):
    def test_format_and_values(self):
        result = make_result()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_touchstone(Path(tmp) / "trace", result)
            self.assertEqual(path.suffix, ".s2p")
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertTrue(lines[0].startswith("!"))
            self.assertIn("WARNING", "\n".join(lines))
            header = [line for line in lines if line.startswith("#")][0]
            self.assertEqual(header, "# Hz S DB R 50")
            data = [line for line in lines if not line.startswith(("!", "#"))]
            self.assertEqual(len(data), 3)
            first = data[0].split()
            self.assertEqual(len(first), 9)
            self.assertAlmostEqual(float(first[0]), 100e6, places=0)
            self.assertAlmostEqual(float(first[3]), 0.0, places=3)
            self.assertAlmostEqual(float(first[1]), -100.0)
            second = data[1].split()
            self.assertAlmostEqual(float(second[3]), -1.5, places=3)

    def test_raw_result_uses_dbfs(self):
        result = make_result(calibrated=False)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_touchstone(Path(tmp) / "raw.s2p", result)
            lines = path.read_text(encoding="utf-8").splitlines()
            data = [line for line in lines if not line.startswith(("!", "#"))]
            self.assertAlmostEqual(float(data[0].split()[3]), -20.0, places=3)


if __name__ == "__main__":
    unittest.main()
