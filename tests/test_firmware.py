# SPDX-License-Identifier: Unlicense
"""devices.phoenix.tooling's firmware default, which device.mk, env.example and firmware.json repeat."""

import json
import unittest
from pathlib import Path

from devices.phoenix.tooling import FIRMWARE, doctor, localconfig

ROOT = Path(__file__).resolve().parents[1]


class FirmwareDefaultTests(unittest.TestCase):
    """One default: Python reads tooling's FIRMWARE, and these files have to repeat it."""

    def test_python_reads_the_constant(self):
        version = FIRMWARE.split("-")[0]
        self.assertEqual(localconfig.Settings().firmware, FIRMWARE)
        self.assertEqual(doctor.FIRMWARE_VERSION, version)
        self.assertIn(FIRMWARE, json.loads((ROOT / "tooling/firmware.json").read_text(encoding="utf-8"))["builds"])

    def test_the_makefile_repeats_it(self):
        mk = (ROOT / "device.mk").read_text(encoding="utf-8")
        self.assertIn(f"FIRMWARE ?= {FIRMWARE}\n", mk)

    def test_the_example_settings_repeat_it(self):
        example = (ROOT / "env.example").read_text(encoding="utf-8")
        self.assertIn(f"PICO4_FIRMWARE={FIRMWARE}\n", example)


if __name__ == "__main__":
    unittest.main()
