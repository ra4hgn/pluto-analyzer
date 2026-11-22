"""Unit tests for the lock-in DSP (no hardware required)."""

import unittest

import numpy as np

from pluto_analyzer.config import (
    ADC_FULL_SCALE,
    plan_lo,
    tone_amplitude_from_dbfs,
)
from pluto_analyzer.dsp import (
    adc_peak,
    is_clipping,
    lockin_amplitude,
    make_tone,
    smooth_trace,
    to_db,
    to_dbfs,
)

FS = 8e6
N = 1 << 16
IF = 500e3


class TestLockin(unittest.TestCase):
    def test_amplitude_is_recovered(self):
        tone = make_tone(IF, FS, N, 1000)
        self.assertAlmostEqual(lockin_amplitude(tone, IF, FS), 1000.0, places=3)

    def test_image_is_rejected(self):
        tone = make_tone(IF, FS, N, 1000)
        self.assertLess(lockin_amplitude(tone, -IF, FS), 1e-6)

    def test_negative_tone_is_recovered(self):
        tone = make_tone(-IF, FS, N, 500)
        self.assertAlmostEqual(lockin_amplitude(tone, -IF, FS), 500.0, places=3)
        self.assertLess(lockin_amplitude(tone, IF, FS), 1e-6)

    def test_db_math(self):
        self.assertAlmostEqual(to_dbfs(ADC_FULL_SCALE), 0.0, places=9)
        self.assertAlmostEqual(to_dbfs(ADC_FULL_SCALE / 10), -20.0, places=9)
        self.assertAlmostEqual(to_db(10.0), 20.0, places=9)

    def test_adc_peak_and_clip(self):
        tone = make_tone(IF, FS, N, 1000)
        self.assertAlmostEqual(adc_peak(tone), 1000.0, places=3)
        self.assertFalse(is_clipping(adc_peak(tone)))
        self.assertTrue(is_clipping(0.8 * ADC_FULL_SCALE))


class TestPlanLo(unittest.TestCase):
    def test_normal_point(self):
        lo, detect = plan_lo(900e6, IF, 70e6, 6e9)
        self.assertEqual(lo, 899.5e6)
        self.assertEqual(detect, IF)
        self.assertEqual(lo + detect, 900e6)

    def test_lower_edge_uses_reverse_if(self):
        lo, detect = plan_lo(70e6, IF, 70e6, 6e9)
        self.assertEqual(lo, 70.5e6)
        self.assertEqual(detect, -IF)
        self.assertEqual(lo + detect, 70e6)

    def test_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            plan_lo(50e6, IF, 70e6, 6e9)
        with self.assertRaises(ValueError):
            plan_lo(6.1e9, IF, 70e6, 6e9)


class TestToneLevel(unittest.TestCase):
    def test_dbfs_to_amplitude(self):
        self.assertEqual(tone_amplitude_from_dbfs(0.0), 32767)
        self.assertTrue(16000 <= tone_amplitude_from_dbfs(-6.02) <= 16600)
        self.assertGreaterEqual(tone_amplitude_from_dbfs(-200.0), 1)


class TestSmooth(unittest.TestCase):
    def test_nan_aware(self):
        y = np.array([0.0, 2.0, np.nan, 4.0, 6.0])
        out = smooth_trace(y, 3)
        self.assertEqual(out.shape, y.shape)
        self.assertTrue(np.isfinite(out[1]))
        self.assertTrue(np.isfinite(out[3]))

    def test_window_reduces_noise(self):
        rng = np.random.default_rng(1234)
        y = 0.5 * rng.standard_normal(200)
        out = smooth_trace(y, 15)
        self.assertLess(np.std(out[10:-10]), np.std(y[10:-10]))

    def test_window_of_one_is_passthrough(self):
        y = np.array([1.0, -2.0, 3.5])
        np.testing.assert_allclose(smooth_trace(y, 1), y)
        # a window wider than the data also leaves it unchanged
        np.testing.assert_allclose(smooth_trace(y, 10), y)


if __name__ == "__main__":
    unittest.main()
