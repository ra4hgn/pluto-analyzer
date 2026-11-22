"""Sweep engine and export tests with a fake SDR (no hardware required)."""

import json
import tempfile
import unittest
from pathlib import Path

from pluto_analyzer.config import SweepConfig
from pluto_analyzer.export import save_session, write_csv
from pluto_analyzer.sdr import SweepPoint
from pluto_analyzer.sweep import SweepEngine


class FakeSDR:
    def __init__(self, config: SweepConfig) -> None:
        self.config = config
        self.calls: list[float] = []
        self.configured: list[SweepConfig] = []

    def configure(self, config: SweepConfig) -> None:
        self.config = config
        self.configured.append(config)

    def measure_point(self, freq_hz: float, captures=None) -> SweepPoint:
        self.calls.append(freq_hz)
        return SweepPoint(
            freq_hz=freq_hz,
            lo_hz=freq_hz - self.config.if_hz,
            tone_hz=self.config.if_hz,
            captures=1,
            amplitude=100.0,
            dbfs=-26.0,
            peak=150.0,
            clip=False,
            low=False,
        )


class TestSweepEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SweepConfig(start_hz=100e6, stop_hz=500e6, points=5)
        self.sdr = FakeSDR(self.config)

    def test_grid_and_progress(self):
        engine = SweepEngine(self.sdr, self.config)
        seen = []
        result = engine.run(lambda done, total, point: seen.append((done, total)))
        self.assertEqual(len(result.points), 5)
        self.assertEqual(result.points[0].freq_hz, 100e6)
        self.assertEqual(result.points[-1].freq_hz, 500e6)
        self.assertEqual(seen[0], (1, 5))
        self.assertEqual(seen[-1], (5, 5))
        self.assertFalse(result.stopped)

    def test_stop_request(self):
        engine = SweepEngine(self.sdr, self.config)
        engine.request_stop()
        result = engine.run()
        self.assertTrue(result.stopped)
        self.assertEqual(result.points, [])

    def test_reconfigures_when_needed(self):
        other = SweepConfig(start_hz=100e6, stop_hz=500e6, points=3)
        engine = SweepEngine(self.sdr, other)
        engine.run()
        self.assertEqual(len(self.sdr.configured), 1)

    def test_summary(self):
        result = SweepEngine(self.sdr, self.config).run()
        summary = result.summary()
        self.assertEqual(summary["points"], 5)
        self.assertAlmostEqual(summary["mean_db"], -26.0)
        self.assertEqual(summary["clip_count"], 0)


class TestExport(unittest.TestCase):
    def test_csv_and_meta(self):
        config = SweepConfig(start_hz=100e6, stop_hz=200e6, points=2)
        sdr = FakeSDR(config)
        result = SweepEngine(sdr, config).run()
        with tempfile.TemporaryDirectory() as tmp:
            csv_path, meta_path = save_session(
                Path(tmp), result, "test:uri", warnings=["w1"]
            )
            lines = csv_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                lines[0],
                "freq_hz,lo_hz,tone_hz,captures,amplitude,dbfs,s21_db,peak,"
                "spread_db,clip,low,unstable",
            )
            self.assertEqual(len(lines), 3)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(meta["uri"], "test:uri")
            self.assertEqual(meta["summary"]["points"], 2)
            self.assertEqual(meta["warnings"], ["w1"])

    def test_write_csv_creates_parents(self):
        point = SweepPoint(100e6, 99.5e6, 500e3, 1, 10.0, -46.0, 20.0, False, True)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_csv(Path(tmp) / "nested" / "trace.csv", [point])
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
