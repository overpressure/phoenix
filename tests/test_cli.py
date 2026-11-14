# SPDX-License-Identifier: Unlicense
"""The PICO 4 as overpressure finds it: registered, its commands and steps, what touches it, its .env."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from devices.phoenix.tooling import HEADSET, unlockfiles
from devices.phoenix.tooling import localconfig as pico4
from overpressure.api.v1.headset import discover

ROOT = Path(__file__).resolve().parents[1]


def step(key: str) -> list[str]:
    """Return the command line of the step KEY, as the window runs it."""
    return next(s for s in HEADSET.steps() if s.key == key).command({})


class TouchesTests(unittest.TestCase):
    def test_what_touches_the_headset_and_what_does_not(self):
        touching = [
            ["dev", "install"],
            ["dev", "restore", "--installed"],
            ["dev", "ssh", "true"],
            ["device", "backup", "--identity", "x"],
            ["device", "unlock"],
            ["cycle", "--restore-only"],
            ["hwtest", "preflight"],
            step("install"),
            step("restore-abl"),
        ]
        not_touching = [
            ["build", "--firmware", "x"],
            ["assets"],
            ["dev", "image"],
            ["dev", "boot-image"],
            ["device", "verify-backup", "--backup", "x"],
            ["device", "fetch-unlock-files"],
            step("userspace"),
            step("tools"),
        ]
        for argv in touching:
            self.assertTrue(HEADSET.touches(argv), argv)
        for argv in not_touching:
            self.assertFalse(HEADSET.touches(argv), argv)


class RegistryTests(unittest.TestCase):
    def test_the_pico_4_is_registered_with_its_commands_and_steps(self):
        registry = discover()
        self.assertEqual(registry.get("phoenix").info.name, "PICO 4")
        self.assertEqual(registry.refused, {})
        self.assertIn("build", HEADSET.commands())
        self.assertEqual(step("stage1")[:2], ["build", "--firmware"])


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_the_pico4_env_example_keeps_the_known_engineering_abl_hash(self):
        env = self.root / ".env"
        env.write_text((ROOT / "env.example").read_text(encoding="utf-8"), encoding="utf-8")
        s = pico4.load(env, {})
        self.assertEqual(s.engineering_abl_sha256, unlockfiles.ENGINEERING_ABL_SHA256)
        s.require("engineering_abl_sha256")  # what dev stage-abl requires: there on a new computer


if __name__ == "__main__":
    unittest.main()
