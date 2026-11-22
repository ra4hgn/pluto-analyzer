"""Tests for the bundled user guides."""

from __future__ import annotations

import unittest

from pluto_analyzer.help import GUIDES, load_guide


class TestHelpGuides(unittest.TestCase):
    def test_all_guides_load(self) -> None:
        for name in GUIDES:
            text = load_guide(name)
            self.assertGreater(len(text), 500)
            self.assertTrue(text.startswith("# "))

    def test_measurement_contents(self) -> None:
        text = load_guide("measurement")
        self.assertIn("pluto-na-cli measure", text)
        self.assertIn("CLIP", text)
        self.assertIn("calibration.md", text)

    def test_calibration_contents(self) -> None:
        text = load_guide("calibration")
        self.assertIn("S21_dB(f) = dBFS_dut(f) − dBFS_thru(f)", text)
        self.assertIn("Cal Thru", text)
        self.assertIn("measurement.md", text)

    def test_unknown_guide(self) -> None:
        with self.assertRaises(ValueError):
            load_guide("does-not-exist")


if __name__ == "__main__":
    unittest.main()
