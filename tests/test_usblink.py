# SPDX-License-Identifier: Unlicense
"""The headset's USB link on a Linux PC: a link up without the headset's address is named with its fix."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

from devices.phoenix.tooling import doctor as checks
from devices.phoenix.tooling import install, usblink
from overpressure.api.v1.doctor import MISSING, OK, Host
from overpressure.api.v1.process import Final

if TYPE_CHECKING:
    from collections.abc import Sequence

MAC = "06:aa:bb:cc:dd:ee"


class Sysfs:
    """A fake /sys/class/net with USB devices behind some interfaces."""

    def __init__(self, root: Path) -> None:
        self.net = root / "net"
        self.net.mkdir()
        self.usb: dict[str, Path] = {}

    def add(self, name: str, ids: tuple[str, str, str] | None, mac: str = MAC) -> None:
        folder = self.net / name
        folder.mkdir()
        (folder / "address").write_text(mac + "\n", encoding="utf-8")
        if ids:
            usb = self.net.parent / f"usb-{name}"
            usb.mkdir()
            for file, value in zip(("idVendor", "idProduct", "product"), ids):
                (usb / file).write_text(value + "\n", encoding="utf-8")
            self.usb[name] = usb

    def device(self, interface: Path) -> Path:
        if interface.name not in self.usb:
            raise FileNotFoundError(interface)
        return self.usb[interface.name]


def runner(addresses: dict[str, list[tuple[str, int]]], profiles: dict[str, str]) -> usblink.Run:
    """Answer ip -j and nmcli as they do on a PC with @p addresses and NetworkManager's @p profiles."""

    def run(argv: Sequence[str]) -> tuple[int, str]:
        name = argv[-1]
        if argv[0] == "ip":
            rows = [{"ifname": name, "addr_info": [{"local": a, "prefixlen": n} for a, n in addresses.get(name, [])]}]
            return 0, json.dumps(rows)
        if argv[0] == "nmcli":
            return (0, profiles[name] + "\n") if name in profiles else (10, "")
        return 127, ""

    return run


class UsbLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sysfs = Sysfs(Path(self.tmp.name))
        self.sysfs.add("enp3s0", None)  # the PC's own port: no USB device
        self.sysfs.add("enp6s0f3u4", (usblink.VENDOR, usblink.PRODUCT, usblink.LABEL))
        self.sysfs.add("usb1", (usblink.VENDOR, usblink.PRODUCT, "another gadget"))

    def links(self, addresses: dict, profiles: dict) -> list[usblink.Link]:
        return usblink.links(self.sysfs.net, runner(addresses, profiles), self.sysfs.device)

    def test_only_the_headsets_gadget_is_its_link(self):
        found = self.links({}, {})
        self.assertEqual([link.name for link in found], ["enp6s0f3u4"])
        self.assertEqual(found[0].mac, MAC)

    def test_a_link_with_the_headsets_lease_is_no_problem(self):
        found = self.links({"enp6s0f3u4": [("172.16.42.2", 24)]}, {"enp6s0f3u4": "Wired connection 2"})
        self.assertTrue(found[0].has_lease())
        self.assertEqual(usblink.problem(found, platform="linux", nmcli=True), "")

    def test_a_profile_with_a_fixed_address_is_named_with_a_profile_of_the_headsets_own(self):
        # As reported: "Wired connection 1", a fixed address on the LAN's network, took the headset's interface.
        found = self.links({"enp6s0f3u4": [("192.0.2.2", 24)]}, {"enp6s0f3u4": "Wired connection 1"})
        problem = usblink.problem(found, platform="linux", nmcli=True)
        self.assertIn("(enp6s0f3u4), but has 192.0.2.2/24 instead of 172.16.42.2", problem)
        self.assertIn('NetworkManager put its profile "Wired connection 1" on it', problem)
        commands = problem.split("\n\n")[1].splitlines()
        self.assertEqual(
            commands,
            [
                (
                    f"nmcli connection add type ethernet con-name overpressure-phoenix ethernet.mac-address {MAC} "
                    "connection.autoconnect-priority 100 ipv4.method auto ipv4.never-default yes "
                    "ipv4.ignore-auto-dns yes ipv6.method disabled"
                ),
                "nmcli connection up overpressure-phoenix",
            ],
        )

    def test_without_networkmanager_the_address_is_given_by_hand(self):
        found = self.links({}, {})
        problem = usblink.problem(found, platform="linux", nmcli=False)
        self.assertIn("has no address instead of 172.16.42.2", problem)
        self.assertIn("it did not take the headset's lease", problem)
        self.assertTrue(
            problem.endswith(
                "sudo ip address flush dev enp6s0f3u4 && sudo ip address add 172.16.42.2/24 dev enp6s0f3u4"
            )
        )

    def test_no_problem_without_the_headset_or_off_linux(self):
        self.assertEqual(usblink.problem([], platform="linux", nmcli=True), "")
        found = self.links({}, {})
        self.assertEqual(usblink.problem(found, platform="win32", nmcli=True), "")

    def test_an_unreadable_ip_answer_is_no_address(self):
        self.assertEqual(usblink.addresses("x", lambda _argv: (0, "not json")), ())
        self.assertEqual(usblink.addresses("x", lambda _argv: (1, "")), ())


class DoctorTests(unittest.TestCase):
    def link(self, *addresses: str) -> usblink.Link:
        return usblink.Link("enp6s0f3u4", MAC, addresses, "Wired connection 1")

    def test_the_doctor_names_the_fix(self):
        host = Host(platform="linux", which=lambda name: f"/usr/bin/{name}")
        (check,) = checks.link_checks(host, [self.link("192.0.2.2/24")])
        self.assertEqual(check.status, MISSING)
        self.assertIn('"Wired connection 1"', check.detail)
        self.assertTrue(check.fix.startswith("nmcli connection add type ethernet con-name overpressure-phoenix"))
        self.assertIn(" && nmcli connection up overpressure-phoenix", check.fix)

    def test_the_doctor_passes_a_link_with_the_lease_and_skips_without_one(self):
        host = Host(platform="linux", which=lambda name: f"/usr/bin/{name}")
        (check,) = checks.link_checks(host, [self.link("172.16.42.2/24")])
        self.assertEqual(check.status, OK)
        self.assertEqual(checks.link_checks(host, []), [])
        self.assertEqual(checks.link_checks(Host(platform="win32"), [self.link()]), [])


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class Ctx:
    def __init__(self) -> None:
        self.clock = Clock()
        self.asks: list = []
        self.logs: list[str] = []

    def log(self, text: str) -> None:
        self.logs.append(text)

    def ask(self, ask: object) -> None:
        self.asks.append(ask)


class InstallerWaitTests(unittest.TestCase):
    def wait(self, problem: str) -> Ctx:
        ctx = Ctx()
        tries = []

        def reach() -> str:
            tries.append(ctx.clock.now)
            if not ctx.asks:
                msg = "ssh failed (255): ssh: connect to host 172.16.42.1 port 22: No route to host"
                raise RuntimeError(msg)
            return "answered"

        self.assertEqual(
            install.replug_until(ctx, "the maintenance system", 120, reach, link=lambda: problem),  # ty: ignore[invalid-argument-type]  # a fake context
            "answered",
        )
        return ctx

    def test_a_link_without_the_headsets_address_asks_for_its_fix_not_a_replug(self):
        ctx = self.wait("The headset's USB link is up on this computer (enp6s0f3u4), ...\n\nnmcli connection up x")
        (ask,) = ctx.asks
        self.assertEqual((ask.id, ask.kind), ("usb-link", "action"))
        self.assertIn("nmcli connection up x\n\nThen press Done", ask.text)
        self.assertIn("The headset's USB link is up on this computer (enp6s0f3u4), ...", ctx.logs)

    def test_a_headset_that_is_not_there_asks_for_a_replug(self):
        (ask,) = self.wait("").asks
        self.assertEqual(ask.id, "replug")

    def test_a_final_error_ends_the_wait_without_asking(self):
        class StopError(RuntimeError, Final):
            pass

        def stop() -> None:
            raise StopError

        ctx = Ctx()
        with self.assertRaises(StopError):
            install.replug_until(ctx, "x", 5, stop, link=lambda: "never asked")  # ty: ignore[invalid-argument-type]  # a fake context
        self.assertEqual(ctx.asks, [])


if __name__ == "__main__":
    unittest.main()
