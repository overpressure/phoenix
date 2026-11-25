# SPDX-License-Identifier: Unlicense
"""Unattended acceptance cycle that ends on stock boot and stock ABL (docs/hardware-testing.md).

  python -m overpressure cycle [--skip-stage] [--keep-abl | --restore-only]

1. Engineering ABL: ADB to EDL, write and read back (python -m overpressure device stage), EDL
   reset, PICO OS. --skip-stage when it is already staged (after the dev loop). The cycle needs a
   headset unlocked before: on a locked one the engineering ABL does not start PICO OS.
2. python -m overpressure hwtest flash-run with the stage-1 image for PICO4_FIRMWARE. The
   runner restores stock boot itself; if it could not, the device deadline returns the headset
   to fastboot and the runner's recorded commands flash the pinned stock image.
3. ADB to EDL: read boot back and compare it with the backup, then restore stock ABL
   (python -m overpressure device restore-abl, read back). --keep-abl stops before this.
4. EDL reset; PICO OS must finish booting.

--restore-only does steps 3 and 4 (after "python -m overpressure dev restore"). The cycle stops
at the first failure and says where the headset is. Evidence goes to out/cycle-<time>/.
Settings come from .env (devices/phoenix/tooling/localconfig.py).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, TypeVar

from overpressure.api.v1 import REPO, android, qualcomm, usbaccess
from overpressure.api.v1 import backup as backups
from overpressure.api.v1.fastboot import NOT_IN_FASTBOOT
from overpressure.api.v1.process import Final, run, until
from overpressure.api.v1.qualcomm import edl_devices

from . import ADB_MODE, EDL_RESTART, FASTBOOT_MODE, USB_MODES, device, localconfig

if TYPE_CHECKING:
    from overpressure.api.v1.process import Timing

ROOT = REPO
T = TypeVar("T")
#: Why a headset in fastboot with PICO's stock ABL is not staged: EDL needs the owner's hands.
EDL_FROM_FASTBOOT = (
    "the headset waits in fastboot with its stock bootloader: put it into EDL mode by hand (power it off, then hold "
    "both volume buttons while plugging in the cable), then start this again"
)
#: How long a just-started adb server may list no device before none counts (Headset.adb_devices).
ADB_LIST_SECONDS = 5
#: The engineering ABL's unlock state in oem device-info; the stock ABL writes DeviceUnlocked= instead.
ENGINEERING_LOCK = re.compile(
    r"^\s*(?:\(bootloader\)\s*)?Device unlocked\s*:\s*(?:yes|no|true|false)\s*$", re.IGNORECASE | re.MULTILINE
)


class StoppedError(RuntimeError, Final):
    """A wait that cannot end well any more: it stops at once, with the reason (process.until)."""


class Headset:
    """Waits and ADB/EDL steps shared by the cycle and the dev loop."""

    def __init__(
        self,
        settings: localconfig.Settings,
        folder: Path,
        runner: Callable[..., str] = run,
        clock: Timing = time,
        edl_count: Callable[[], int] = edl_devices,
    ) -> None:
        """Work on the headset @p settings name, with evidence in @p folder; tests pass fakes."""
        self.s = settings
        self.folder = folder
        # adb and fastboot meeting a device Linux does not let this user open: NoAccessError with the fix.
        self.run = usbaccess.guard(runner, USB_MODES, {"adb": ADB_MODE, "fastboot": FASTBOOT_MODE}, clock)
        self.clock = clock
        self.edl_count = edl_count
        self.usb_reader: Callable[[], bool] | None = None  #: tests: what reads the EDL device's pipe

    def until(self, name: str, seconds: float, operation: Callable[[], T]) -> T:
        """Retry @p operation until it succeeds, for at most @p seconds."""
        return until(name, seconds, operation, self.clock)

    def wait_pico_os(self, seconds: float = 300) -> str:
        """Wait until PICO OS has finished booting; return its build fingerprint."""
        return android.wait_booted(
            self.s.tool("adb"), self.s.adb_serial, self.run, self.clock, seconds=seconds, system="PICO OS"
        )

    def enter_edl(self) -> None:
        """Bring the headset from PICO OS to EDL, unless it is the one EDL device already.

        PICO OS is left only once it is through with the lens and fan MCU (device.wait_mcu).
        """
        if self.edl_count() != 1:
            device.wait_mcu(self.s.tool("adb"), self.s.adb_serial, self.run, self.clock)
        qualcomm.enter_edl(
            self.s.tool("adb"), self.s.adb_serial, self.run, self.clock, edl_count=self.edl_count, modes=USB_MODES
        )

    def edl(self, *words: str | Path, log: str, timeout: float = 600) -> str:
        """Run edl-ng with the headset's loader; its output goes to @p log.log in the evidence folder.

        A device that does not answer (a stale EDL session) fails in plain words, with the way out.
        """
        path = self.folder / f"{log}.log"
        try:
            return self.run(qualcomm.edl_ng(self.s.edl_program(), self.s.path("loader"), *words), timeout, log=path)
        except RuntimeError as exc:
            try:
                output = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                output = ""
            if qualcomm.unanswered(output + str(exc)):
                msg = f"{qualcomm.NO_ANSWER} Nothing was written to it. {EDL_RESTART}"
                raise RuntimeError(msg) from exc
            raise

    def reset_to_pico_os(self, log: str) -> str:
        """Reset from EDL (edl-ng's plain reset mode), then wait for PICO OS.

        Once the headset stayed off after the final reset, with nothing on USB, and only a hard
        reset by the user brought PICO OS back: the cycle then stops and says so instead of
        retrying anything.
        """
        self.edl("reset", "--mode", "Reset", log=log)
        self.drain()  # Linux: the loader resets only once its last message is read
        try:
            return self.wait_pico_os()
        except RuntimeError as exc:
            where = "still in EDL" if self.edl_count() else "no ADB or EDL device"
            msg = (
                f"PICO OS did not come up after the EDL reset ({where}): hard reset needed; "
                "hold the power button until the headset restarts, nothing is retried"
            )
            raise RuntimeError(msg) from exc

    def release_adb(self) -> None:
        """Stop the adb server (not on Windows), so that fastboot reaches the headset.

        It starts with the first adb command and keeps running; on Linux it can keep hold of the
        headset when it comes back in fastboot mode, and fastboot then does not list it (root's
        fastboot does). The next adb command starts it again; Windows' drivers keep the two apart.
        """
        android.release_usb(self.s.tool("adb"), lambda argv: self.run(argv, 15, check=False))

    def drain(self) -> None:
        """After a power command, read the headset's USB pipe until it leaves (qualcomm.drain_until_gone)."""
        qualcomm.drain_until_gone(self.clock, reader=self.usb_reader)

    def check_edl_serial(self, output: str) -> None:
        """edl-ng reports the device serial when it connects: only the configured headset may be written."""
        qualcomm.check_edl_serial(output, self.s.edl_serial)

    def identify(self) -> None:
        """Identify the EDL device in a read-only session before anything is written: only the configured headset.

        A headset that still runs the Firehose loader of an earlier session names no serial: it is
        restarted into a fresh EDL session (no storage written) and identified again. The sessions
        that follow in the same step need no check: on Linux they would name no serial either.
        """
        output = self.edl("printgpt", log="edl-identify", timeout=120)
        if not qualcomm.serial_shown(output) and not qualcomm.unanswered(output):
            print(
                "   the EDL device runs an earlier session's loader: restarting it into a fresh EDL session", flush=True
            )
            self.run(
                qualcomm.fresh_edl_argv(self.s.edl_program(), self.s.path("loader")),
                60,
                log=self.folder / "edl-fresh.log",
            )
            self.drain()
            qualcomm.wait_fresh_edl(self.edl_count, self.clock)
            usbaccess.require(qualcomm.EDL_MODE, USB_MODES, self.clock)
            output = self.edl("printgpt", log="edl-identify-fresh", timeout=120)
        self.check_edl_serial(output)

    def device_args(self) -> list[str | Path]:
        """Return the device command's arguments for the ABL with this headset's backup and loader."""
        return [
            "--identity",
            self.s.path("identity"),
            "--backup",
            self.s.path("backup"),
            "--loader",
            self.s.path("loader"),
            "--edl-backend",
            "edl-ng",
            "--edl",
            self.s.edl_program(),
            "--partition",
            "abl",
        ]

    def identity_chip(self) -> int | None:
        """Return the SoC ID the headset's identity record holds, None without a readable one."""
        try:
            return qualcomm.chip_id(str(json.loads(self.s.path("identity").read_text(encoding="utf-8"))["chip_id"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def adb_devices(self) -> list[str]:
        """Return the serials adb lists ready ("device"), oldest first as adb lists them.

        On Linux and macOS the fastboot check stops adb's server (release_adb); the next adb command starts a new
        one, which lists no device in its first moments. An empty list is only taken as such after a few seconds.
        """
        adb = self.s.tool("adb")
        deadline = self.clock.monotonic() + ADB_LIST_SECONDS
        while True:
            output = self.run([adb, "devices"], 15, check=False)
            serials = list(device.device_rows(output, ("device",)))
            if serials or self.clock.monotonic() >= deadline:
                return serials
            self.clock.sleep(1)

    def pico_os_booted(self) -> str:
        """Return PICO OS's build fingerprint once it has finished booting on this headset; raise while not (one look).

        The headset is the adb device whose SoC ID is its identity's, whatever its serial: under the engineering ABL
        PICO OS may report another one than under the stock ABL, and another Android device on this computer is never
        taken for it. Without an identity record only the saved serial counts.
        """
        adb, saved = self.s.tool("adb"), self.s.adb_serial
        chip = self.identity_chip()
        serials = self.adb_devices()
        for serial in sorted(serials, key=lambda s: s != saved):  # the saved serial first
            if chip is None and serial != saved:
                continue
            lines = self.run(
                [
                    adb,
                    "-s",
                    serial,
                    "shell",
                    "getprop sys.boot_completed; cat /sys/devices/soc0/serial_number; getprop ro.build.fingerprint",
                ],
                15,
                check=False,
            ).splitlines()
            try:
                soc = qualcomm.chip_id(lines[1]) if len(lines) > 1 else None
            except ValueError:
                soc = None
            if chip is not None and soc != chip:
                continue  # another device, or one that does not answer yet
            if not lines or lines[0].strip() != "1":
                break
            if serial != saved:
                # Not the serial itself: the logs mask only the serials .env names.
                print("   PICO OS answers with another adb serial than the saved one (its SoC ID matches)", flush=True)
            return lines[2].strip() if len(lines) > 2 else ""  # noqa: PLR2004  # the third line: the fingerprint
        msg = "PICO OS has not finished booting"
        raise RuntimeError(msg)

    def fastboot_abl(self) -> tuple[bool, bool] | None:
        """Return (whether the engineering ABL runs, whether it is unlocked) for a headset in fastboot; else None.

        The engineering ABL's oem device-info says "Device unlocked: no|yes". Any other answer is another ABL:
        PICO's stock one writes "DeviceUnlocked=..." or refuses oem commands without the unlock token's
        authority. A headset not in fastboot, or one that does not answer yet, is None. Without a fastboot
        serial (.env has none before the install's unlock), the one device in fastboot is the headset; the
        unlock step learns its serial.
        """
        self.release_adb()  # Linux: adb's server would keep hold of a headset in fastboot
        fastboot = self.s.tool("fastboot")
        output = self.run([fastboot, "devices"], 30, check=False)
        rows = list(device.device_rows(output, ("fastboot",)))
        serial = self.s.fastboot_serial or (rows[0] if len(rows) == 1 else "")
        if not serial or serial not in rows:
            return None
        state = self.run([fastboot, "-s", serial, "oem", "device-info"], 30, check=False)
        if ENGINEERING_LOCK.search(state):
            return True, android.unlocked(state)
        return (False, False) if state.strip() else None

    def fastboot_lock(self) -> bool | None:
        """Return whether the engineering ABL waits in fastboot with the bootloader locked; None when it does not."""
        found = self.fastboot_abl()
        return None if found is None or not found[0] else not found[1]

    def other_abl_in_fastboot(self) -> bool:
        """Tell whether the headset waits in fastboot with another ABL than the engineering one (PICO's stock ABL).

        Writing the engineering ABL then needs EDL mode, which the owner enters by hand: adb needs PICO OS, and
        the stock ABL's EDL command needs the unlock token's authority.
        """
        found = self.fastboot_abl()
        return found is not None and not found[0]

    def reset_after_stage(self, log: str) -> bool:
        """Reset from EDL after the engineering ABL was written; return whether PICO OS started.

        A headset unlocked before starts PICO OS on it. A locked one cannot: the engineering ABL refuses
        PICO OS's signing key (vbmeta, boot state red) and falls back to fastboot, where the unlock goes
        on. Anything else within the deadline (nothing on USB, or fastboot on an unlocked headset) stops
        as reset_to_pico_os does.
        """
        self.edl("reset", "--mode", "Reset", log=log)
        self.drain()  # Linux: the loader resets only once its last message is read

        def started() -> bool:
            try:
                print(f"   PICO OS with engineering ABL: {self.pico_os_booted()}", flush=True)
            except (RuntimeError, subprocess.TimeoutExpired):
                pass  # not (yet) in PICO OS: fastboot?
            else:
                return True
            lock = self.fastboot_lock()
            if lock:
                print(
                    "   fastboot, the bootloader locked: the engineering ABL does not start PICO OS on a locked "
                    "headset; the unlock goes on from here",
                    flush=True,
                )
                return False
            if lock is False:
                msg = "the headset is in fastboot with its bootloader unlocked, but PICO OS did not start"
                raise StoppedError(msg)
            msg = "neither PICO OS nor the engineering ABL's fastboot answers yet"
            raise RuntimeError(msg)

        try:
            return self.until("PICO OS or fastboot", 300, started)
        except RuntimeError as exc:
            if isinstance(exc, StoppedError):
                raise
            where = "still in EDL" if self.edl_count() else "no ADB, fastboot or EDL device"
            msg = (
                f"neither PICO OS nor fastboot came up after the EDL reset ({where}): hard reset needed; "
                "hold the power button until the headset restarts, nothing is retried"
            )
            raise RuntimeError(msg) from exc

    def staged_and_locked(self) -> bool:
        """Tell whether the engineering ABL waits in fastboot already, the bootloader locked.

        An earlier stage left it there on a locked headset (its fastboot menu loops): the ABL was written and
        read back then, and the unlock goes on from here, without EDL. A headset in PICO OS or in EDL is not.
        """
        return self.edl_count() != 1 and bool(self.fastboot_lock())

    def stage_abl(self) -> bool:
        """Engineering ABL through EDL (hash and readback checked), then PICO OS or, on a locked headset, fastboot.

        Return whether PICO OS started (reset_after_stage). A headset whose engineering ABL waits in fastboot,
        locked, from an earlier run is staged already (staged_and_locked).
        """
        self.s.require("adb_serial", "edl_serial", "engineering_abl_sha256")
        print(">> engineering ABL", flush=True)
        if self.staged_and_locked():
            print(
                "   the engineering ABL runs already, in fastboot with the bootloader locked: the unlock goes on "
                "from here",
                flush=True,
            )
            return False
        if self.edl_count() != 1 and self.other_abl_in_fastboot():
            raise StoppedError(EDL_FROM_FASTBOOT)
        self.enter_edl()
        # A read-only first session identifies the device before anything is written.
        self.identify()
        self.run(
            [
                sys.executable,
                "-m",
                "overpressure",
                "--device",
                "phoenix",
                "device",
                "stage",
                *self.device_args(),
                "--image",
                self.s.path("engineering_abl"),
                "--sha256",
                self.s.engineering_abl_sha256,
                "--readback",
                self.folder / "engineering-abl-readback.bin",
                "--execute",
                "--confirm-device",
                self.s.adb_serial,
            ],
            900,
            log=self.folder / "stage-abl.log",
        )
        return self.reset_after_stage("reset-stage")

    def stock_partition(self, name: str) -> tuple[Path, dict]:
        """Return a partition's image in the backup and its manifest entry."""
        return backups.partition_image(self.s.path("backup"), name)

    def stock_boot(self) -> tuple[Path, dict]:
        """Return the stock boot image of the backup and its manifest entry."""
        return self.stock_partition("boot")


class Cycle(Headset):
    """The acceptance cycle's steps: stage the ABL, the runner's flash-run, verify and restore."""

    def stage_unlocked(self) -> None:
        """Stage the engineering ABL; the cycle goes on in PICO OS, so a locked headset stops it."""
        if not self.stage_abl():
            msg = "the cycle needs a headset unlocked before (python -m overpressure device unlock first)"
            raise RuntimeError(msg)

    def flash_run(self) -> int:
        """Run the acceptance runner's flash-run; restore stock boot if it could not; wait for PICO OS."""
        print(">> hardware acceptance (flash-run)", flush=True)
        image = ROOT / "out" / f"pico4-stage1-{self.s.firmware}.img"
        if not image.exists():
            msg = f"missing {image}: run python -m overpressure build --firmware {self.s.firmware}"
            raise ValueError(msg)
        argv = [
            sys.executable,
            "-m",
            "overpressure",
            "--device",
            "phoenix",
            "hwtest",
            "flash-run",
            "--serial",
            self.s.adb_serial,
            "--confirm-device",
            self.s.adb_serial,
            "--fastboot-serial",
            self.s.fastboot_serial,
            "--backup",
            self.s.path("backup"),
            "--image",
            image,
            "--adb",
            self.s.tool("adb"),
            "--fastboot",
            self.s.tool("fastboot"),
            "--ssh",
            self.s.ssh,
            "--ssh-keygen",
            self.s.ssh_keygen,
            "--out",
            self.folder / "run",
        ]
        ncm = self.s.ncm()
        if ncm:
            argv += ["--windows-ncm-python", ncm]
        try:
            self.run(argv, 4 * 3600, log=self.folder / "run.log")
            code = 0
        except RuntimeError:
            code = 1
        result = json.loads((self.folder / "run" / "result.json").read_text(encoding="utf-8"))
        print(f"   runner: {result.get('status')} {result.get('error', '')}".rstrip(), flush=True)
        if result.get("boot_restore_required"):
            self.restore_boot(result)
        print(f"   PICO OS after the run: {self.wait_pico_os()}", flush=True)
        return code

    def restore_boot(self, result: dict) -> None:
        """Flash stock boot with the runner's recorded commands once its deadline brings fastboot."""
        print("   boot restore pending: waiting for fastboot", flush=True)

        def in_fastboot() -> None:
            self.release_adb()
            if self.s.fastboot_serial not in self.run([self.s.tool("fastboot"), "devices"], 30, check=False):
                raise RuntimeError(NOT_IN_FASTBOOT)

        self.until("fastboot", 45 * 60, in_fastboot)
        saved = result["stock_boot"]
        if backups.digest(Path(saved["path"])) != {"size": saved["size"], "sha256": saved["sha256"]}:
            msg = "the pinned stock boot image changed; headset left in fastboot"
            raise RuntimeError(msg)
        with (self.folder / "manual-restore.log").open("w", encoding="utf-8") as log:
            log.writelines(self.run(command, 300) + "\n" for command in result["manual_restore_commands"])
        print("   stock boot flashed by the cycle", flush=True)

    def restore_abl(self) -> None:
        """Stock boot verified by a full EDL readback, then stock ABL, then PICO OS."""
        self.s.require("adb_serial", "edl_serial")
        print(">> verify stock boot, restore stock ABL", flush=True)
        stock, expected = self.stock_boot()
        self.enter_edl()
        self.identify()
        self.edl("read-part", "boot", self.folder / "boot-readback.bin", log="boot-readback")
        if backups.digest(self.folder / "boot-readback.bin")["sha256"] != expected["sha256"]:
            msg = f"boot differs from {stock.name} in the backup (headset in EDL)"
            raise RuntimeError(msg)
        self.run(
            [
                sys.executable,
                "-m",
                "overpressure",
                "--device",
                "phoenix",
                "device",
                "restore-abl",
                *self.device_args(),
                "--readback",
                self.folder / "stock-abl-readback.bin",
                "--execute",
                "--confirm-device",
                self.s.adb_serial,
            ],
            900,
            log=self.folder / "restore-abl.log",
        )
        print(f"   final PICO OS: {self.reset_to_pico_os('reset-final')}", flush=True)


def main(argv: list[str] | None = None) -> int:
    """Run the acceptance cycle; it stops at the first failure and says where the headset is."""
    ap = argparse.ArgumentParser(
        prog="python -m overpressure cycle", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--skip-stage", action="store_true", help="the engineering ABL is already staged")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--keep-abl", action="store_true", help="stop before restoring the stock ABL")
    mode.add_argument("--restore-only", action="store_true", help="only verify stock boot and restore stock ABL")
    args = ap.parse_args(argv)
    settings = localconfig.load()
    folder = ROOT / "out" / ("cycle-" + dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S"))
    folder.mkdir(parents=True)
    cycle = Cycle(settings, folder)
    code = 0
    try:
        if not args.restore_only:
            # Only the flash-run uses fastboot: the restore goes through PICO OS and EDL (restore_abl), and an
            # install that stopped before its unlock never learnt the fastboot serial.
            settings.require("adb_serial", "fastboot_serial")
            if not args.skip_stage:
                cycle.stage_unlocked()
            code = cycle.flash_run()
        if not args.keep_abl:
            cycle.restore_abl()
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"STOPPED: {exc}\nevidence: {folder}", file=sys.stderr)
        return 1
    print(f"{'PASSED' if code == 0 else 'FAILED'}: evidence in {folder}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
