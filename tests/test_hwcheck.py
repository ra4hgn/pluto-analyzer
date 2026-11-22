"""Unit tests for hardware limits and span validation (no hardware required)."""

import unittest

from pluto_analyzer.hwcheck import HardwareInfo, Range, check_span


def make_info(lo_min=70e6, lo_max=6e9) -> HardwareInfo:
    return HardwareInfo(
        uri="test",
        hw_model="test",
        fw_version="test",
        phy_model="ad9364",
        rx_lo=Range(lo_min, 1.0, lo_max),
        tx_lo=Range(46.875e6, 1.0, 6e9),
        rx_gain=Range(-3.0, 1.0, 71.0),
        tx_gain=Range(-89.75, 0.0, -89.75),
        sample_rate=Range(2.083e6, 1.0, 61.44e6),
        rx_bandwidth=Range(200e3, 1.0, 56e6),
        rx_channels=("voltage0",),
        tx_channels=("voltage0",),
    )


class TestRangeParsing(unittest.TestCase):
    def test_three_values(self):
        value = Range.parse("[-3 1 71]")
        self.assertEqual((value.minimum, value.step, value.maximum), (-3, 1, 71))

    def test_single_value(self):
        value = Range.parse("[-89.750000]")
        self.assertEqual(value.minimum, -89.75)
        self.assertEqual(value.maximum, -89.75)

    def test_invalid(self):
        self.assertIsNone(Range.parse(None))
        self.assertIsNone(Range.parse("not a range"))


class TestSpanCheck(unittest.TestCase):
    def test_extended_device_accepts_full_span(self):
        errors, warnings = check_span(make_info(), 70e6, 6e9, 500e3)
        self.assertEqual(errors, [])
        self.assertTrue(any("rated range" in w for w in warnings))
        self.assertFalse(any("extension not detected" in w for w in warnings))

    def test_stock_device_rejects_low_edge(self):
        errors, warnings = check_span(make_info(325e6, 3.8e9), 70e6, 6e9, 500e3)
        self.assertTrue(errors)
        self.assertTrue(any("extension not detected" in w for w in warnings))

    def test_invalid_order(self):
        errors, _ = check_span(make_info(), 6e9, 70e6, 500e3)
        self.assertTrue(errors)

    def test_single_point_allowed(self):
        errors, _ = check_span(make_info(), 900e6, 900e6, 500e3)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
