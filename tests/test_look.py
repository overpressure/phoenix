# SPDX-License-Identifier: Unlicense
"""tooling/look.py: the PICO 4 on USB, looked at without changing it, from fakes of the bus, adb, SSH and fastboot."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from devices.phoenix.tooling import USB_MODES, localconfig, qualcomm
from devices.phoenix.tooling.devloop import INSTALLED
from devices.phoenix.tooling.look import ALLOW_DEBUGGING, NO_KEY, NOT_ANSWERING, REBOOT_FROM_RECOVERY, Look
from overpressure.api.v1 import headset as headsets

FINGERPRINT = "Pico/Phoenix_ovs/PICOA8110:10/5.13.7/1:user/dev-keys"
EDL = (0x05C6, 0x9008)
ADB = (0x2D40, 0x00B7)
FASTBOOT = (0x18D1, 0xD00D)


class Clock:
    """A clock whose sleeps pass at once."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class Fakes:
    """The programs' answers by their first words, the bus's devices and the SSH port; what ran is kept."""

    def __init__(self) -> None:
        self.answers: dict[str, str] = {}
        self.bus: dict[tuple[int, int], int] = {}
        self.port = False
        self.ran: list[list[str]] = []

    def run(self, argv: list, _timeout: float = 0, **_kw: object) -> str:
        words = [str(w) for w in argv]
        self.ran.append(words)
        name = Path(words[0]).stem
        for key, answer in self.answers.items():
            if f"{name} {' '.join(words[1:])}".startswith(key) or (key.startswith("ssh") and name == "ssh"):
                return answer
        return ""

    def count(self, vendor: int, product: int) -> int:
        return self.bus.get((vendor, product), 0)

    def pico_os(self, *, model: str = "PICO 4", memory: str = "DDR4", version: str = "5.13.7") -> None:
        """Answer as PICO OS with USB debugging allowed."""
        self.bus[ADB] = 1
        self.answers["adb devices"] = "List of devices attached\nSERIAL1\tdevice\n"
        fingerprint = FINGERPRINT.replace("5.13.7", version)
        self.answers["adb -s SERIAL1 shell"] = f"{fingerprint}\ntrue\n0\n{model}\n{memory}\n  level: 77\n"


class LookTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name)
        self.fakes = Fakes()
        settings = localconfig.Settings(platform_tools="tools", adb_serial="", fastboot_serial="")
        self.look = Look(
            settings,
            self.work,
            self.work / "install",
            "phoenix",
            runner=self.fakes.run,
            port_open=lambda _host, _port, _seconds: self.fakes.port,
            bus=self.fakes.count,
            clock=Clock(),
        )

    def found(self) -> headsets.Found:
        return self.look.found(USB_MODES, qualcomm.EDL_MODE)

    def test_nothing_on_usb_is_absent(self):
        self.assertEqual(self.found(), headsets.Found(headsets.ABSENT))

    def test_edl_mode_is_seen_on_the_bus_alone(self):
        self.fakes.bus[EDL] = 1
        found = self.found()
        self.assertEqual((found.state, found.workflows), (headsets.EDL, ("restore",)))
        self.assertEqual(self.fakes.ran, [])  # no loader, no tool: the bus only

    def test_supported_pico_os_offers_the_install_with_its_facts(self):
        self.fakes.pico_os()
        found = self.found()
        self.assertEqual((found.state, found.system, found.version), (headsets.MAKER, "PICO OS", "5.13.7"))
        self.assertEqual(found.facts, (("Model", "PICO 4"), ("Bootloader", "Unlocked"), ("Battery", "77 %")))
        self.assertEqual(found.workflows, ("install",))
        self.assertFalse(any("ssh" in Path(w[0]).stem for w in self.fakes.ran))  # adb answered: no wait on SSH

    def test_a_pico_4_pro_or_an_unsupported_build_offers_nothing_and_says_why(self):
        self.fakes.pico_os(model="PICO 4 Pro", memory="DDR5")
        found = self.found()
        self.assertEqual(found.workflows, ())
        self.assertIn("only the original PICO 4", found.note)
        self.fakes.pico_os(version="5.14.0")
        found = self.found()
        self.assertEqual(found.workflows, ())
        self.assertIn("5.14.0 (global) is not supported", found.note)

    def test_pico_os_without_usb_debugging_allowed_asks_for_it(self):
        self.fakes.answers["adb devices"] = "List of devices attached\nSERIAL1\tunauthorized\n"
        found = self.found()
        self.assertEqual((found.state, found.workflows, found.note), (headsets.MAKER, (), ALLOW_DEBUGGING))

    def test_pico_os_recovery_says_how_to_leave_it(self):
        self.fakes.answers["adb devices"] = "List of devices attached\nSERIAL1\trecovery\n"
        found = self.found()
        self.assertEqual((found.state, found.note), (headsets.RECOVERY, REBOOT_FROM_RECOVERY))

    def test_the_installed_system_offers_the_upgrade_the_check_and_the_way_back(self):
        self.fakes.port = True
        self.fakes.answers["ssh"] = "systemd\nboot\n80\n"
        (self.work / INSTALLED).write_text("{}", encoding="utf-8")
        found = self.found()
        self.assertEqual((found.state, found.system), (headsets.OVERPRESSURE, "overpressure"))
        self.assertEqual(found.workflows, ("upgrade", "check", "restore"))
        self.assertEqual(
            found.facts, (("Running", "Installed system"), ("Battery", "80 %"), ("Installed by", "This computer"))
        )

    def test_the_maintenance_system_offers_the_install_and_the_way_back(self):
        self.fakes.port = True
        self.fakes.answers["ssh"] = "pico4-init\nrecovery\n50\n"
        found = self.found()
        self.assertEqual(found.workflows, ("install", "restore"))
        self.assertEqual(found.facts[0], ("Running", "Maintenance system"))

    def test_a_system_this_computer_has_no_key_for_offers_only_the_way_back(self):
        self.fakes.port = True
        found = self.found()
        self.assertEqual((found.state, found.workflows, found.note), (headsets.OVERPRESSURE, ("restore",), NO_KEY))

    def test_fastboot_is_asked_only_when_the_bus_holds_it_and_tells_the_bootloader(self):
        self.found()
        self.assertFalse(any(Path(w[0]).stem == "fastboot" for w in self.fakes.ran))
        self.fakes.bus[FASTBOOT] = 1
        self.fakes.answers["fastboot devices"] = "FB1\tfastboot\n"
        self.fakes.answers["fastboot -s FB1 oem device-info"] = "(bootloader) Device unlocked: yes\nOKAY\n"
        found = self.found()
        self.assertEqual((found.state, found.workflows), (headsets.BOOTLOADER, ("install", "restore")))
        self.assertEqual(found.facts, (("Bootloader", "Engineering, unlocked"),))
        self.fakes.answers["fastboot -s FB1 oem device-info"] = "(bootloader) DeviceUnlocked=true\nOKAY\n"
        self.assertEqual(self.found().facts, (("Bootloader", "Stock"),))

    def test_a_headset_on_usb_that_answers_nothing_is_unknown(self):
        self.fakes.bus[ADB] = 1
        found = self.found()
        self.assertEqual((found.state, found.note), (headsets.UNKNOWN, NOT_ANSWERING))

    def test_nothing_it_runs_writes_or_restarts(self):
        self.fakes.bus[FASTBOOT] = 1  # fastboot listed, then the headset's own system, then PICO OS
        self.fakes.answers["fastboot devices"] = "FB1\tfastboot\n"
        self.found()
        self.fakes.port = True
        self.fakes.answers["ssh"] = "systemd\nboot\n80\n"
        self.found()
        self.fakes.pico_os()
        self.found()
        words = {w for argv in self.fakes.ran for word in argv for w in word.replace(";", " ").split()}
        self.assertIn("getprop", words)
        for verb in ("reboot", "flash", "erase", "push", "setprop", "unlock", "flashing", "edl", "rm", "dd"):
            self.assertNotIn(verb, words)


if __name__ == "__main__":
    unittest.main()
