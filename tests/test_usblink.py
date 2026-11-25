# SPDX-License-Identifier: Unlicense
"""The PICO 4's USB network gadget for overpressure's usblink: what its systems set up, and where it is checked.

overpressure tests the link's check itself (its tests/test_usblink.py); here: the gadget is the one stage 1's
usb-gadget makes, and the doctor, the installer's waits and the dev loop take it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from devices.phoenix import tooling
from devices.phoenix.tooling import doctor as checks
from devices.phoenix.tooling import install as plan
from overpressure.api.v1 import doctor, usblink

ROOT = Path(__file__).resolve().parent.parent
STAGE1 = ROOT / "initramfs" / "usr" / "libexec" / "pico4"
LINK = usblink.Link("enp6s0f3u4", "06:aa:bb:cc:dd:ee", ("192.0.2.2/24",), "Wired connection 1")


class GadgetTests(unittest.TestCase):
    def test_the_gadget_is_the_one_stage_1_makes(self):
        script = (STAGE1 / "usb-gadget").read_text(encoding="utf-8")
        self.assertIn(f'write 0x{tooling.GADGET.vendor} "$G/idVendor"', script)
        self.assertIn(f'write 0x{tooling.GADGET.product} "$G/idProduct"', script)
        self.assertIn(f'write "{tooling.GADGET.label}" "$G/strings/0x409/product"', script)
        lib = (STAGE1 / "lib.sh").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"^P4_HOST_IP=(\S+)$", lib, re.MULTILINE), [tooling.GADGET.host_ip])
        (headset,) = re.findall(r"^P4_IP=(\S+)$", lib, re.MULTILINE)
        self.assertTrue(tooling.GADGET.in_network(f"{headset}/32"))

    def test_the_doctor_checks_the_pico_4s_link(self):
        host = doctor.Host(platform="linux", which=lambda name: f"/usr/bin/{name}")
        with mock.patch.object(usblink, "links", return_value=[LINK]) as links:
            (check,) = checks.link_checks(host)
        links.assert_called_once_with(tooling.GADGET)
        self.assertEqual(check.status, doctor.MISSING)
        self.assertIn("con-name overpressure-phoenix", check.fix)

    def test_the_installers_waits_name_the_pico_4s_link(self):
        with mock.patch.object(usblink, "problem", return_value="up without its address") as problem:
            self.assertEqual(plan.link_problem(), "up without its address")
        problem.assert_called_once_with(tooling.GADGET)


if __name__ == "__main__":
    unittest.main()
