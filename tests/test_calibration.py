"""Calibration tests: build, interpolate, apply, save/load (no hardware)."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from pluto_analyzer.calibration import Calibration
from pluto_analyzer.config import SweepConfig
from pluto_analyzer.sdr import SweepPoint
from pluto_analyzer.sweep import SweepResult


def make_result(config: SweepConfig, slope_db_per_decade=10.0) -> SweepResult:
    result = SweepResult(config=config)
    for freq in config.frequencies():
        db = -10.0 + slope_db_per_decade * np.log10(freq / 1e6)
        result.points.append(
            SweepPoint(
                freq_hz=freq,
                lo_hz=freq - config.if_hz,
                tone_hz=config.if_hz,
                captures=2,
                amplitude=100.0,
                dbfs=float(db),
                peak=150.0,
                clip=False,
                low=False,
                spread_db=0.01,
                unstable=False,
            )
        )
    return result


class TestCalibration(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SweepConfig(
            start_hz=100e6, stop_hz=1000e6, points=5, rx_gain_db=10
        )
        self.result = make_result(self.config)
        self.cal = Calibration.from_result(self.result, "test:uri")

    def test_build_from_result(self):
        self.assertEqual(len(self.cal.freqs), 5)
        np.testing.assert_allclose(self.cal.thru_db, self.result.db)
        self.assertEqual(self.cal.meta["uri"], "test:uri")

    def test_apply_at_calibration_points_is_zero(self):
        corrected = self.cal.apply(self.cal.freqs, self.cal.thru_db)
        np.testing.assert_allclose(corrected, 0.0, atol=1e-9)

    def test_log_interpolation_between_points(self):
        freq = 300e6
        expected = np.interp(
            np.log10(freq),
            np.log10(self.cal.freqs),
            self.cal.thru_db,
        )
        self.assertAlmostEqual(self.cal.correction([freq])[0], expected, places=9)

    def test_constant_reference_subtracts_exactly(self):
        cal = Calibration(
            freqs=np.array([100e6, 1000e6]),
            thru_db=np.array([-10.0, -10.0]),
        )
        corrected = cal.apply(np.array([200e6, 500e6]), np.array([-3.0, -4.0]))
        np.testing.assert_allclose(corrected, [7.0, 6.0], atol=1e-9)

    def test_coverage_and_warnings(self):
        inside = self.cal.coverage(np.array([150e6, 500e6]))
        self.assertTrue(inside.all())
        outside = self.cal.coverage(np.array([50e6, 2000e6]))
        self.assertEqual(outside.tolist(), [False, False])
        warnings = self.cal.warnings(np.array([50e6]))
        self.assertTrue(any("outside" in w for w in warnings))

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            stem = Path(tmp) / "cal" / "s21_thru"
            npz_path, json_path = self.cal.save(stem)
            self.assertTrue(npz_path.exists())
            self.assertTrue(json_path.exists())
            loaded = Calibration.load(stem)
            np.testing.assert_allclose(loaded.freqs, self.cal.freqs)
            np.testing.assert_allclose(loaded.thru_db, self.cal.thru_db)
            self.assertEqual(loaded.meta["uri"], "test:uri")
            loaded2 = Calibration.load(npz_path)
            np.testing.assert_allclose(loaded2.thru_db, self.cal.thru_db)

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            Calibration.load(Path("/tmp/does-not-exist-cal.npz"))

    def test_quality_ok(self):
        self.assertTrue(self.cal.quality_ok())
        self.cal.meta["summary"] = {"clip_count": 1}
        self.assertFalse(self.cal.quality_ok())

    def test_settings_mismatch(self):
        self.cal.meta["config"] = {
            "rx_gain_db": 10.0,
            "tx_gain_db": 0.0,
            "tone_amplitude": 16384,
            "if_hz": 500e3,
            "sample_rate": 8e6,
        }
        config = SweepConfig(
            start_hz=100e6, stop_hz=200e6, points=3,
            rx_gain_db=-3, tx_gain_db=0,
            tone_amplitude=5200,
            if_hz=500e3, sample_rate=8_000_000,
        )
        mismatch = self.cal.settings_mismatch(config)
        self.assertTrue(any("RX gain" in item for item in mismatch))
        self.assertTrue(any("Tone" in item for item in mismatch))
        self.assertFalse(any("TX gain" in item for item in mismatch))

    def test_quality_warnings(self):
        self.cal.meta["summary"] = {
            "clip_count": 2, "low_count": 0, "unstable_count": 1
        }
        warnings = self.cal.warnings(np.array([200e6]))
        joined = "; ".join(warnings)
        self.assertIn("CLIP", joined)
        self.assertIn("UNSTABLE", joined)

    def test_empty_result_rejected(self):
        empty = SweepResult(config=self.config)
        with self.assertRaises(ValueError):
            Calibration.from_result(empty, "test:uri")


if __name__ == "__main__":
    unittest.main()
