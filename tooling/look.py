# SPDX-License-Identifier: Unlicense
"""The PICO 4 on USB, looked at without changing it (Headset.assess, headset interface 1.11).

The quickest first, each bounded by seconds: the USB bus for EDL mode (enumeration only: an EDL loader's session
would have to be ended by hand), fastboot when the bus holds the headset in fastboot mode (which bootloader, locked
or not), PICO OS over adb (its build, the model, the bootloader's lock, the battery), and the headset's own system
on its USB network (SSH's port, then its first process, over this computer's key). Where the bus cannot be read,
fastboot is asked last. Nothing is
written, restarted or started on the headset; on this computer adb's server may start, and on Linux it is stopped
again before fastboot looks (cycle.Headset.release_adb). What fits each state is a workflow of the installer.
"""

from __future__ import annotations

import contextlib
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from overpressure.api.v1 import headset as headsets
from overpressure.api.v1 import install, ssh
from overpressure.api.v1.headset import Found
from overpressure.api.v1.process import run
from overpressure.api.v1.qualcomm import usb_count

from . import device
from .cycle import Headset as CycleHeadset
from .devloop import INSTALLED

if TYPE_CHECKING:
    from pathlib import Path

    from overpressure.api.v1.process import Timing
    from overpressure.api.v1.usbaccess import UsbMode

    from .localconfig import Settings

#: The headset's own system on its USB network (devloop.HOST), and how long its SSH port may take to answer.
HOST = "172.16.42.1"
PORT_SECONDS = 1.5
#: What PICO OS is asked over adb, one line each, in this order.
PROPERTIES = (
    "ro.build.fingerprint",
    "ro.oem.state",
    "ro.boot.flash.locked",
    *device.MODEL_PROPERTIES.values(),
)
#: Its battery, after the properties: dumpsys's level line.
BATTERY = "dumpsys battery | grep -m1 'level:'"
#: What the headset's own system is asked over SSH: its first process (systemd: the installed system), whether
#: it was started from recovery (the maintenance system), and its battery.
OWN_SYSTEM = (
    "cat /proc/1/comm; grep -qw pico4.part=recovery /proc/cmdline && echo recovery || echo boot; "
    "cat /sys/class/power_supply/battery/capacity"
)
#: What to do in each state that offers nothing, in the owner's words.
ALLOW_DEBUGGING = "Put the headset on and allow USB debugging for this computer, then look again."
REBOOT_FROM_RECOVERY = "The headset is in PICO OS's recovery: choose Reboot system now on the headset."
EDL_WAY_OUT = "Hold the power button for about 10 s to restart it."
NOT_ANSWERING = "The headset is on USB but does not answer yet. Wait a moment; if it stays so, restart it."
NO_KEY = "This computer did not install the system on it and has no key for it."


@dataclass
class Look:
    """One look at the headset @p settings name; tests pass fakes for the programs, the port and the bus."""

    settings: Settings
    work: Path  #: the dev loop's folder: this computer's SSH key for the headset
    out: Path  #: the installer's records (install.OUT): what this computer installed
    codename: str
    runner: Callable[..., str] = run
    port_open: Callable[[str, int, float], bool] | None = None
    bus: Callable[[int, int], int | None] = usb_count
    clock: Timing = time

    def found(self, modes: tuple[UsbMode, ...], edl: UsbMode) -> Found:
        """Return what runs on the headset, and the workflows that fit (tooling's keys: install, upgrade, ...)."""
        if self.count(edl):
            return Found(headsets.EDL, "Emergency download mode", workflows=("restore",), note=EDL_WAY_OUT)
        fastboot = next((m for m in modes if m.name.startswith("fastboot")), None)
        in_fastboot = self.count(fastboot) if fastboot is not None else 0  # None: the bus cannot tell here
        # adb answers at once; the headset's own system may take the port's timeout.
        probes: list[Callable[[], Found | None]] = [self.pico_os, self.own_system]
        if in_fastboot:
            probes.insert(0, self.bootloader)
        elif in_fastboot is None:
            probes.append(self.bootloader)
        for probe in probes:
            found = probe()
            if found is not None:
                return found
        if any(self.count(mode) for mode in modes):
            return Found(headsets.UNKNOWN, note=NOT_ANSWERING)
        return Found(headsets.ABSENT)

    def count(self, mode: UsbMode) -> int | None:
        """Return how many devices of @p mode the USB bus holds; None when the bus cannot be read here."""
        return self.bus(int(mode.vendor, 16), int(mode.product, 16))

    # -- the headset's own system -------------------------------------------------------------
    def own_system(self) -> Found | None:
        """Return the headset's own system when it answers on its USB network: installed, or the maintenance one."""
        port_open = self.port_open or _port_open
        if not port_open(HOST, 22, PORT_SECONDS):
            return None
        release = install.installed_release(self.codename, self.out)
        ours = (self.work / INSTALLED).is_file()  # what the upgrade and the check need (Headset.workflow_problem)
        version = "" if not release or release == "development checkout" else release.split()[0]
        argv = ssh.argv(
            self.settings.ssh, self.work / "id_ed25519", self.work / "look_known_hosts", f"root@{HOST}", OWN_SYSTEM
        )
        (self.work / "look_known_hosts").unlink(missing_ok=True)  # the maintenance system makes new keys each boot
        try:
            lines = self.runner(argv, 10, check=False).split()
        except (OSError, subprocess.SubprocessError):
            lines = []
        if len(lines) < 2:  # noqa: PLR2004  # its first process and where it started
            return Found(headsets.OVERPRESSURE, "overpressure", version, workflows=("restore",), note=NO_KEY)
        facts = []
        if lines[2:3] and lines[2].isdigit():
            facts.append(("Battery", f"{lines[2]} %"))
        if lines[0] == "systemd" and lines[1] == "boot":
            facts.insert(0, ("Running", "Installed system"))
            workflows: tuple[str, ...] = ("upgrade", "check", "restore")
        else:
            facts.insert(0, ("Running", "Maintenance system"))
            workflows = ("install", "restore")
        if ours:
            facts.append(("Installed by", "This computer"))
        return Found(headsets.OVERPRESSURE, "overpressure", version, tuple(facts), workflows)

    # -- PICO OS --------------------------------------------------------------------------------
    def pico_os(self) -> Found | None:
        """Return PICO OS when adb lists the headset: its build, whether it is supported, and what fits."""
        adb = self.settings.tool("adb")
        try:
            rows = self.adb_rows(adb)
        except (OSError, subprocess.SubprocessError):
            return None
        states = set(rows.values())
        if "recovery" in states or "sideload" in states:
            return Found(headsets.RECOVERY, "PICO OS recovery", note=REBOOT_FROM_RECOVERY)
        ready = [serial for serial, state in rows.items() if state == "device"]
        if not ready:
            if "unauthorized" in states:
                return Found(headsets.MAKER, "PICO OS", note=ALLOW_DEBUGGING)
            return None
        serial = self.settings.adb_serial if self.settings.adb_serial in ready else ready[0]
        command = "; ".join([*(f"getprop {p}" for p in PROPERTIES), BATTERY])
        try:
            lines = self.runner([adb, "-s", serial, "shell", command], 15, check=False).splitlines()
        except (OSError, subprocess.SubprocessError):
            return Found(headsets.MAKER, "PICO OS", note=NOT_ANSWERING)
        return maker_found(lines)

    def adb_rows(self, adb: str) -> dict[str, str]:
        """Return adb's devices by serial with their state (device, unauthorized, recovery, sideload, ...)."""
        output = self.runner([adb, "devices"], 15, check=False)
        rows = {}
        for line in output.splitlines()[1:]:
            words = line.split()
            if len(words) >= 2:  # noqa: PLR2004  # the serial and the state
                rows[words[0]] = words[1]
        return rows

    # -- fastboot -------------------------------------------------------------------------------
    def bootloader(self) -> Found | None:
        """Return the headset in fastboot: the engineering bootloader (and its lock) or PICO's stock one."""
        headset = CycleHeadset(self.settings, self.work, self.runner, self.clock)
        with contextlib.suppress(OSError, subprocess.SubprocessError, RuntimeError):
            abl = headset.fastboot_abl()
            if abl is not None:
                engineering, unlocked = abl
                which = ("Engineering, unlocked" if unlocked else "Engineering, locked") if engineering else "Stock"
                return Found(headsets.BOOTLOADER, "Bootloader", "", (("Bootloader", which),), ("install", "restore"))
        return None


def maker_found(lines: list[str]) -> Found:
    """Return the look at PICO OS from its answers (PROPERTIES, then the battery's line)."""
    values = [line.strip() for line in lines]
    values += [""] * (len(PROPERTIES) + 1 - len(values))
    fingerprint, oem_state, locked, model, memory, battery = values[: len(PROPERTIES) + 1]
    parts = fingerprint.split("/")
    version = parts[3] if len(parts) > 3 else ""  # noqa: PLR2004  # Pico/<product>/<device>:<sdk>/<version>/...
    facts = [("Model", model or "Unknown"), ("Bootloader", "Locked" if locked == "1" else "Unlocked")]
    level = battery.rpartition(":")[2].strip()
    if level.isdigit():
        facts.append(("Battery", f"{level} %"))
    if (model, memory) != (device.VERIFIED_MODEL["model"], device.VERIFIED_MODEL["ddr_type"]):
        note = f"This is a {model or 'headset PICO OS does not name'}: only the original PICO 4 is supported."
        return Found(headsets.MAKER, "PICO OS", version, tuple(facts), note=note)
    region = device.firmware_region(fingerprint)
    try:
        device.check_firmware(version, oem_state, region)
    except ValueError:
        note = f"PICO OS {version} ({region}) is not supported yet: nothing can be installed on it."
        return Found(headsets.MAKER, "PICO OS", version, tuple(facts), note=note)
    return Found(headsets.MAKER, "PICO OS", version, tuple(facts), ("install",))


def _port_open(host: str, port: int, seconds: float) -> bool:
    """Tell whether @p host accepts a TCP connection on @p port within @p seconds."""
    try:
        with socket.create_connection((host, port), timeout=seconds):
            return True
    except OSError:
        return False
