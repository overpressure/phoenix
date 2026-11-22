# SPDX-License-Identifier: Unlicense
"""The PICO 4's installer plan against fakes: tasks, done checks, serials, refusals and the Wi-Fi secret."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

from devices.phoenix import tooling
from devices.phoenix.tooling import HEADSET, devloop, localconfig
from devices.phoenix.tooling import doctor as checks
from devices.phoenix.tooling import install as plan
from overpressure.api.v1 import backup as backups
from overpressure.api.v1 import doctor, qualcomm, tools, usbaccess
from overpressure.api.v1.doctor import Check
from overpressure.api.v1.headset import BackupSet
from overpressure.api.v1.install import Context, Engine, Stage, Stopped, Task

if TYPE_CHECKING:
    from collections.abc import Callable

#: Made-up values: no real headset's.
ADB, FASTBOOT, EDL = "ADBFAKE1", "FBFAKE1", "0A1B2C3D"
PASSWORD = "correct horse battery"  # noqa: S105  # a made-up secret the tests look for
FINGERPRINT = "Pico/Phoenix_ovs/PICOA8110:10/{}/test:user/dev-keys"
SHARP = "console=ttyMSM0 msm_drm.dsi_display0=qcom,mdss_dsi_sharp_ls026b3sa_2160_video: other=1"
INNOLUX = "msm_drm.dsi_display0=qcom,mdss_dsi_nt57900_innolux_2160_video:"
PRINTGPT = f"""[INFO]  Serial Number: {EDL}
[INFO]  --- GPT Header LUN 0 ---
[INFO]  --- Partitions LUN 0 ---
[INFO]  --- GPT Header LUN 1 ---
[INFO]  --- Partitions LUN 1 ---
[INFO]  --- GPT Header LUN 2 ---
"""


#: The install's and the upgrade's last check: none where the check is a workflow of its own (#74).
END_CHECK = [] if tooling.CHECK_WORKFLOW else ["check"]


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class Recorder:
    def __init__(self, answers: dict[str, dict]):
        self.events: list[dict] = []
        self.answers = answers

    def start(self, stop: Callable[[], None]) -> None:
        pass

    def emit(self, event: dict) -> None:
        self.events.append(event)

    def answer(self, ask: plan.Ask) -> dict | None:
        answer = self.answers.get(ask.id)
        if isinstance(answer, list):  # one answer per ask, then none (declined)
            return answer.pop(0) if answer else None
        return answer

    def finished(self):
        return next(e for e in self.events if e["event"] == "finished")


class PlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patcher = mock.patch.object(tools, "CACHE", self.root / "cache" / "tools")
        patcher.start()
        self.addCleanup(patcher.stop)
        # The installer's own waits for the headset (into fastboot, the maintenance system's login) on a stand-in.
        patcher = mock.patch.object(plan, "AskingDev")
        self.dev = patcher.start()
        self.addCleanup(patcher.stop)
        self.dev.return_value.other_abl_in_fastboot.return_value = False  # PICO OS, not its bootloader
        self.env = self.root / ".env"
        self.identity = self.root / "backup" / "device.json"
        self.backup = self.root / "backup" / "stock"
        self.env.write_text(
            "\n".join(
                [
                    f"PICO4_IDENTITY={self.identity.as_posix()}",
                    f"PICO4_BACKUP={self.backup.as_posix()}",
                    f"PICO4_LOADER={(self.root / 'firehose.elf').as_posix()}",
                    f"PICO4_ENGINEERING_ABL={(self.root / 'abl.elf').as_posix()}",
                    f"PICO4_EDL={(self.root / 'edl-ng').as_posix()}",
                    f"PICO4_PLATFORM_TOOLS={(self.root / 'pt').as_posix()}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        mine = {k: v for k, v in os.environ.items() if not k.startswith("PICO4_")}  # .env's, not this host's
        environ = mock.patch.dict(os.environ, mine, clear=True)
        environ.start()
        self.addCleanup(environ.stop)
        self.version = "5.13.7"
        self.model = "PICO 4"
        self.cmdline = SHARP
        self.mcu = ["1\n"]  # PICO OS's answers to the MCU probe, the last one repeated
        self.running: str | None = None  # the PICO OS version the headset runs now; None: it does not answer that
        self.edl = 0
        self.restarting: list[int] = []  # the EDL count's next answers while a restart into EDL runs
        self.fastboot = False
        self.spawned: list[tuple[list[str], bytes | None, bool]] = []
        self.captured: list[list[str]] = []
        self.checks: list[Check] = []
        self.plan = plan.Plan(
            HEADSET,
            root=self.root,
            work=self.root / "out" / "dev",
            computer=lambda: self.checks,
            edl_count=lambda: self.restarting.pop(0) if self.restarting else self.edl,
        )

    # -- the fakes -----------------------------------------------------------------------------
    def spawn(self, argv: list[str], stdin: bytes | None, writes: bool, line: Callable[[str], None]) -> int:  # noqa: FBT001  # the Spawn signature
        self.spawned.append((argv, stdin, writes))
        if "probe" in argv:
            output = Path(argv[argv.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "schema": 1,
                "serial": argv[argv.index("--serial") + 1],
                "fingerprint": FINGERPRINT.format(self.version),
                "oem_state": "true",
                "chip_id": 1234567,
                "model": self.model,
                "ddr_type": "DDR4",
            }
            output.write_text(json.dumps(record), encoding="utf-8")
        line("ok")
        return 0

    def capture(self, argv: list[str], _timeout: float) -> tuple[int, str]:  # noqa: C901, PLR0911  # one answer per command
        self.captured.append(argv)
        text = " ".join([Path(argv[0]).stem, *argv[1:]])
        if text == "adb devices":
            return 0, f"List of devices attached\n{ADB}\tdevice\n"
        if "cat /proc/cmdline" in text:
            return 0, self.cmdline
        if "getprop sys.pxr.init.status" in text:
            return 0, self.mcu.pop(0) if len(self.mcu) > 1 else self.mcu[0]
        if text.endswith("reboot edl"):
            self.edl = 1
            return 0, ""
        if "reset --mode Edl" in text:
            self.restarting = [1, 1, 1, 0, 0]  # still there a while, then gone, then back (as the PICO 4)
            return 0, ""
        if "reset --mode Reset" in text:  # out of EDL, into PICO OS
            self.edl = 0
            return 0, ""
        if text.endswith("shell getprop ro.build.fingerprint"):  # not the boot check, which asks for it too
            return 0, FINGERPRINT.format(self.running) if self.running else ""
        if "getprop sys.boot_completed" in text:
            return 0, "1\nPico/Phoenix_ovs/PICOA8110:10/5.13.7/x:user/dev-keys"
        if "printgpt" in text:
            return 0, PRINTGPT
        if text.endswith("reboot bootloader"):
            self.fastboot = True
            return 0, ""
        if text == "fastboot devices":
            return 0, f"{FASTBOOT}\tfastboot\n" if self.fastboot else ""
        return 0, ""

    def run_stages(self, ids: tuple[str, ...], answers: dict[str, dict], workflow: str = "install") -> Recorder:
        plans = {"install": self.plan.stages, "check": self.plan.check_stages}
        stages = [s for s in plans[workflow]() if s.id in ids]
        ui = Recorder(answers)
        engine = Engine(
            HEADSET.info,
            stages,
            ui,
            root=self.root / "out" / "install",
            env=self.env,
            spawn=self.spawn,
            capture=self.capture,
            clock=FakeClock(),
        )
        engine.run()
        return ui

    def ctx(self) -> Context:
        engine = Engine(HEADSET.info, (), Recorder({}), root=self.root / "out" / "install", env=self.env)
        task = Task("t", "T", lambda _ctx: None)
        return Context(engine, Stage("s", "S", "", (task,)), task)

    def settings(self) -> localconfig.Settings:
        return localconfig.load(self.env, {})

    def commands(self) -> list[str]:
        return [" ".join(argv[argv.index("phoenix") + 1 :][:2]) for argv, _, _ in self.spawned if "phoenix" in argv]

    @staticmethod
    def yes(*ids: str) -> dict[str, dict]:
        return {i: {"answer": i, "accepted": True, "values": {}} for i in ids}

    # -- the tests -------------------------------------------------------------------------------
    def test_the_stages_and_their_tasks(self):
        stages = self.plan.stages()
        self.assertEqual([s.id for s in stages], ["computer", "connect", "prepare", "install"])
        images = ["stage1", "kernel", "dev-image", "boot-image"]
        self.assertEqual(
            [[t.id for t in s.tasks] for s in stages],
            [
                ["tools", "unlock-files", "doctor"],
                ["find", "identity", "panel"],
                # The images take the backup's boot and recovery: after the backup, before the flash.
                ["consent", "backup", "stage-abl", "unlock", *images, "flash", "maintenance"],
                # The checks are a workflow of their own, which the finished page offers (#74); with an older
                # overpressure the install checks at its end.
                ["wifi", "ssh-key", "install", "installed", "wifi-profile", *END_CHECK, "ready"],
            ],
        )
        writes = [t.id for s in stages for t in s.tasks if t.writes]
        self.assertEqual(writes, ["stage-abl", "unlock", "flash", "install", "wifi-profile"])
        self.assertTrue(all(t.done is not None for t in stages[2].tasks if t.id in images))
        self.assertTrue(all(t.where for s in stages[1:] for t in s.tasks if t.id not in {"ready", "wifi", "ssh-key"}))
        reset = plan.Plan(HEADSET, factory_reset=True).stages()[2]
        self.assertEqual([t.id for t in reset.tasks][3:5], ["unlock", "reset"])
        self.assertTrue(HEADSET.install_plan())

    def test_the_builds_run_the_steps_command_lines_and_the_headset_builds_the_system(self):
        argv = self.plan.argv(self.ctx(), "stage1")
        self.assertEqual(argv[-4:], ["phoenix", "build", "--firmware", "5.13.7-global-seko"])
        # Neither the install nor the upgrade needs the build container: the headset builds the system itself.
        for stages in (self.plan.stages(), self.plan.upgrade_stages()):
            linux = [
                t.id for s in stages for t in s.tasks if t.id in self.plan.steps and self.plan.steps[t.id].linux_root
            ]
            self.assertEqual(linux, [])
        self.assertIn("--on-headset", self.plan.argv(self.ctx(), "install"))

    def test_build_done_checks_look_at_the_outputs(self):
        ctx = self.ctx()
        out = self.root / "out"
        done = {t.id: t.done for t in self.plan.image_tasks("")}
        self.assertFalse(any(check(ctx) for check in done.values() if check))
        out.mkdir(parents=True, exist_ok=True)
        (out / "pico4-stage1-5.13.7-global-seko.img").write_text("x")
        self.assertTrue(done["stage1"](ctx))  # ty: ignore[call-non-callable]  # every build has one

    def test_connect_and_prepare_find_the_serials_and_write_them_to_env(self):
        reads = []  # the loader's last message, read after the restart into PICO OS (on Linux it waits for that)
        self.plan.usb_reader = lambda: reads.append(1) or True
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        self.assertEqual(len(reads), 1)
        self.assertEqual(ui.finished()["status"], "done", ui.finished())
        s = self.settings()
        self.assertEqual((s.adb_serial, s.edl_serial, s.fastboot_serial), (ADB, EDL, FASTBOOT))
        self.assertEqual(
            self.commands(),
            [
                "device probe",
                "device backup",
                "device verify-backup",
                "dev stage-abl",
                "device unlock",
                "build --firmware",
                "kernel fetch",
                "dev image",
                "dev boot-image",
                "dev flash",
            ],
        )
        # Into fastboot before the flash and the maintenance system's login run here, where a wait asks for a replug.
        self.dev.return_value.to_fastboot.assert_called()
        self.dev.return_value.connect.assert_called_once_with()
        # After the backup the headset goes back to PICO OS: the bootloader's step enters EDL afresh from there.
        shown = [" ".join(map(str, c)) for c in self.captured]
        self.assertTrue(any(c.endswith("reset --mode Reset") for c in shown))
        backup = next(argv for argv, _, _ in self.spawned if "backup" in argv and "--execute" in argv)
        luns = backup[backup.index("--edl-luns") + 1 : backup.index("--execute")]
        self.assertEqual(luns, ["1", "2", "0"])
        self.assertEqual(backup[backup.index("--confirm-device") + 1], ADB)
        unlock = next(argv for argv, _, _ in self.spawned if "unlock" in argv)
        self.assertIn("--accept-data-loss", unlock)
        self.assertEqual(unlock[unlock.index("--serial") + 1], FASTBOOT)
        self.assertIn(
            ["pt/adb", "-s", ADB, "reboot", "edl"], [[_tail(a) for a in c][:1] + c[1:] for c in self.captured]
        )
        text = self.env.read_text(encoding="utf-8")
        self.assertEqual(text.count("PICO4_ADB_SERIAL="), 1)
        self.assertIn(f"PICO4_IDENTITY={self.identity.as_posix()}", text)  # the other lines stay

    def test_pico_os_is_left_only_once_it_is_through_with_the_mcu(self):
        self.mcu = ["", "0\n", "1\n"]  # not set yet, still checking, then through
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        self.assertEqual(ui.finished()["status"], "done", ui.finished())
        texts = [" ".join(c) for c in self.captured]
        first_probe = next(i for i, t in enumerate(texts) if "sys.pxr.init.status" in t)
        self.assertLess(first_probe, next(i for i, t in enumerate(texts) if t.endswith("reboot edl")))
        notes = [e["text"] for e in ui.events if e["event"] == "note"]
        self.assertEqual(sum("lens and fan controller" in n for n in notes), 1)  # said once

    def test_a_failed_mcu_update_keeps_the_headset_in_pico_os(self):
        self.mcu = ["1\n11-13 10:00:00.000  901  944 E PxrInitService: FW/OTA FAILED BECAUSE OF SYSTEM SUSPEND!\n"]
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        finished = ui.finished()
        self.assertEqual((finished["status"], finished["task"]), ("failed", "backup"))
        self.assertIn("Restart the headset", finished["error"])
        self.assertIn("SYSTEM SUSPEND", finished["error"])
        self.assertEqual(finished["where"], plan.STILL_PICO_OS)
        self.assertFalse(any("reboot" in " ".join(c) for c in self.captured))

    def test_an_unsupported_firmware_is_refused_before_any_write(self):
        self.version = "5.13.9"
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        finished = ui.finished()
        self.assertEqual((finished["status"], finished["task"]), ("failed", "identity"))
        self.assertIn("5.13.9", finished["error"])
        self.assertIn("Nothing was changed", finished["where"])
        self.assertEqual(self.commands(), ["device probe"])
        self.assertFalse(any("reboot" in " ".join(c) for c in self.captured))
        self.assertNotIn("consent", [e.get("id") for e in ui.events if e["event"] == "ask"])

    def test_a_pico_4_pro_is_refused_before_any_write(self):
        self.model = "PICO 4 Pro"
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        finished = ui.finished()
        self.assertEqual((finished["status"], finished["task"]), ("failed", "identity"))
        self.assertIn("PICO 4 Pro", finished["error"])
        self.assertEqual(self.commands(), ["device probe"])
        self.assertNotIn("consent", [e.get("id") for e in ui.events if e["event"] == "ask"])

    def test_an_unverified_panel_is_refused_before_any_write(self):
        self.cmdline = INNOLUX
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        self.assertEqual((ui.finished()["status"], ui.finished()["task"]), ("failed", "panel"))
        self.assertIn("nt57900", ui.finished()["error"])
        self.assertEqual(self.commands(), ["device probe"])
        self.cmdline = "cat: /proc/cmdline: Permission denied"
        self.spawned.clear()
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        self.assertEqual(ui.finished()["task"], "panel")  # a panel that cannot be read is refused too

    def test_the_panel_from_surfaceflinger_where_the_command_line_is_closed(self):
        # PICO OS's adb shell may not read /proc/cmdline; SurfaceFlinger names the panel.
        self.cmdline = 'cat: /proc/cmdline: Permission denied\nDisplay 1 (HWC display 0): displayName="sharp ls026b3"'
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        self.assertEqual(ui.finished()["status"], "done")
        self.assertTrue(any("panel sharp_ls026b3sa, verified" in e.get("line", "") for e in ui.events))

    def test_another_headsets_identity_is_not_overwritten(self):
        self.identity.parent.mkdir(parents=True)
        self.identity.write_text(json.dumps({"schema": 1, "serial": "SOMEONE"}))
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        self.assertEqual(ui.finished()["task"], "identity")
        self.assertIn("another headset", ui.finished()["error"])
        self.assertEqual(self.commands(), [])

    def test_a_saved_identity_of_this_headset_is_not_probed_again(self):
        self.identity.parent.mkdir(parents=True)
        self.identity.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "serial": ADB,
                    "fingerprint": FINGERPRINT.format("5.13.7"),
                    "oem_state": "true",
                    "chip_id": 7,
                }
            )
        )
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        self.assertEqual(ui.finished()["status"], "done")
        self.assertEqual(self.commands(), [])
        statuses = [(e["task"], e["status"]) for e in ui.events if e["event"] == "task"]
        self.assertIn(("identity", "skipped"), statuses)

    def test_the_check_workflow_runs_each_part_and_shows_its_warnings(self):
        # #74: the headset on a table, then one row per part; #59: the controllers off only warn.
        run = self.root / "out" / "dev" / "check-20261118-090000-session"
        run.mkdir(parents=True)
        warning = "SteamVR saw no controllers: turn them on"
        (run / "result.json").write_text(json.dumps({"status": "passed", "warnings": [warning]}))
        ui = self.run_stages(("check",), self.yes("put-down"), workflow="check")
        self.assertEqual(ui.finished()["status"], "done", ui.finished())
        self.assertEqual(self.commands(), ["dev installed", "dev check", "dev check", "dev check"])
        parts = [argv[-3:] for argv, _, _ in self.spawned if "check" in argv]
        self.assertEqual(parts, [["--installer", "--part", p] for p in ("system", "imu", "session")])
        notes = [e for e in ui.events if e["event"] == "note" and e["text"] == warning]
        self.assertEqual([n.get("level") for n in notes], ["warning"])

    def test_the_checks_start_only_with_the_headset_put_down(self):
        answers = {"put-down": {"answer": "put-down", "accepted": False, "values": {}}}
        ui = self.run_stages(("check",), answers, workflow="check")
        self.assertEqual(ui.finished()["status"], "stopped")
        self.assertEqual(self.commands(), [])

    def saved_identity(self, version: str) -> None:
        self.identity.parent.mkdir(parents=True, exist_ok=True)
        record = {"schema": 1, "serial": ADB, "fingerprint": FINGERPRINT.format(version), "oem_state": "true"}
        self.identity.write_text(json.dumps(record | {"chip_id": 7}))

    def test_a_saved_identity_of_the_build_the_headset_still_runs_is_kept(self):
        self.saved_identity("5.13.7")
        self.running = "5.13.7"
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        self.assertEqual(ui.finished()["status"], "done")
        self.assertEqual(self.commands(), [])
        self.assertEqual(self.settings().identity, self.identity.as_posix())

    def test_pico_os_updated_since_the_record_gets_a_new_record_and_keeps_the_old(self):
        # #66: an owner updated PICO OS after the first backup; the old record and backup describe the old build.
        self.saved_identity("5.13.7")
        old = self.identity.read_text()
        self.running = self.version = "5.13.8"
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        self.assertEqual(ui.finished()["status"], "done", ui.finished())
        self.assertEqual(self.commands(), ["device probe"])
        new = Path(self.settings().identity)
        self.assertNotEqual(new, self.identity)
        self.assertTrue(new.name.startswith("device-"), new)
        self.assertIn("/5.13.8/", json.loads(new.read_text())["fingerprint"])
        self.assertEqual(self.identity.read_text(), old)  # a record is never overwritten
        notes = [e["text"] for e in ui.events if e["event"] == "note"]
        self.assertTrue(any("5.13.7 to 5.13.8" in n for n in notes), notes)

    def test_a_backup_of_the_old_build_no_longer_counts_after_an_update(self):
        self.saved_identity("5.13.7")
        self.running = self.version = "5.13.8"
        with mock.patch.object(plan.checks, "backup_check", return_value=mock.Mock(status=plan.doctor.OK)):
            self.backup.mkdir(parents=True)
            manifest = {"identity": json.loads(self.identity.read_text())}
            (self.backup / "manifest.json").write_text(json.dumps(manifest))
            self.env.write_text(self.env.read_text() + f"PICO4_EDL_SERIAL={EDL}\n")
            ctx = self.ctx()
            self.assertTrue(self.plan.backup_there(ctx))
            self.run_stages(("connect",), self.yes("plug-in"))
            self.assertFalse(self.plan.backup_there(self.ctx()))

    def test_a_headset_whose_pico_os_does_not_answer_keeps_its_record(self):
        # In its bootloader after the engineering bootloader, or another system runs: nothing to compare.
        self.saved_identity("5.13.7")
        self.running = None
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        self.assertEqual(ui.finished()["status"], "done")
        self.assertEqual(self.commands(), [])

    def test_the_restore_refuses_a_backup_of_another_build_than_pico_os_runs(self):
        self.saved_identity("5.13.7")
        self.backup.mkdir(parents=True)
        (self.backup / "manifest.json").write_text(json.dumps({"identity": json.loads(self.identity.read_text())}))
        ctx = self.ctx()
        with (
            mock.patch.object(ctx, "run"),
            mock.patch.object(self.plan, "backup_there", return_value=True),
            mock.patch.object(self.plan, "running_build", return_value=FINGERPRINT.format("5.13.8")),
            self.assertRaisesRegex(plan.Failed, "5.13.8 runs on the headset, but the backup .* is of 5.13.7"),
        ):
            self.plan.check_backup(ctx)
        # PICO OS that does not answer (a stuck headset) or runs the backup's build: the restore goes on.
        for running in (None, FINGERPRINT.format("5.13.7")):
            with (
                mock.patch.object(ctx, "run"),
                mock.patch.object(self.plan, "backup_there", return_value=True),
                mock.patch.object(self.plan, "running_build", return_value=running),
            ):
                self.plan.check_backup(ctx)

    def test_several_devices_or_none_end_in_words(self):
        original = self.capture

        def crowded(argv: list[str], timeout: float) -> tuple[int, str]:
            if Path(argv[0]).stem == "adb" and argv[1:] == ["devices"]:
                return 0, f"{ADB}\tdevice\nOTHER\tdevice\n"
            return original(argv, timeout)

        self.capture = crowded  # ty: ignore[invalid-assignment]  # a method replaced by a fake
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        self.assertEqual(ui.finished()["task"], "find")
        self.assertIn("More than one Android device", ui.finished()["error"])
        notes = [e["text"] for e in ui.events if e["event"] == "note"]
        self.assertEqual(notes, ["More than one Android device is connected: unplug the others."])  # said once

    def test_a_stop_ends_the_wait_for_a_headset_that_is_never_plugged_in(self):
        original, polls, clock = self.capture, [], FakeClock()

        class Stoppable(Recorder):
            def start(self, stop: Callable[[], None]) -> None:
                self.stop = stop

        ui = Stoppable(self.yes("plug-in"))

        def empty(argv: list[str], timeout: float) -> tuple[int, str]:
            if Path(argv[0]).stem == "adb" and argv[1:] == ["devices"]:
                polls.append(clock.now)
                if len(polls) == 3:  # the window's Stop, while it waits
                    ui.stop()
                return 0, "List of devices attached\n"
            return original(argv, timeout)

        stages = [s for s in self.plan.stages() if s.id == "connect"]
        engine = Engine(
            HEADSET.info, stages, ui, root=self.root / "out" / "install", env=self.env, capture=empty, clock=clock
        )
        self.assertEqual(engine.run(), "stopped")
        self.assertEqual(len(polls), 3)
        self.assertLess(clock.now, plan.PLUG_IN_SECONDS / 10)  # not the ten minutes it waits at most
        self.assertEqual((ui.finished()["task"], ui.finished()["where"]), ("find", plan.UNCHANGED))
        self.assertNotIn("PICO4_ADB_SERIAL", self.env.read_text(encoding="utf-8"))

    # -- Linux: a headset this user may not open (no udev rule) ----------------------------------
    def listing(self, tool: str, reply: Callable[[], str]) -> None:
        """Answer TOOL devices with REPLY from now on."""
        original = self.capture

        def capture(argv: list[str], timeout: float) -> tuple[int, str]:
            if Path(argv[0]).stem == tool and argv[1:] == ["devices"]:
                self.captured.append(argv)
                return 0, reply()
            return original(argv, timeout)

        self.capture = capture  # ty: ignore[invalid-assignment]  # the test's own capture in the method's place

    def test_adb_without_access_stops_the_search_with_the_fix(self):
        self.listing("adb", lambda: f"List of devices attached\n{ADB}\tno permissions (missing udev rules?); see [x]\n")
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        finished = ui.finished()
        self.assertEqual((finished["status"], finished["task"]), ("failed", "find"))
        self.assertIn("may not use the headset in PICO OS with USB debugging", finished["error"])
        self.assertIn(plan.NOTHING_CHANGED, finished["where"])
        self.assertNotIn("PICO4_ADB_SERIAL", self.env.read_text(encoding="utf-8"))

    def test_access_that_udev_grants_a_moment_late_is_waited_for(self):
        replies = iter([f"List of devices attached\n{ADB}\tno permissions; see [x]\n"])
        self.listing("adb", lambda: next(replies, f"List of devices attached\n{ADB}\tdevice\n"))
        ui = self.run_stages(("connect",), self.yes("plug-in"))
        self.assertEqual(ui.finished()["status"], "done", ui.finished())
        self.assertEqual(self.settings().adb_serial, ADB)

    def test_fastboot_without_access_stops_the_unlock_before_anything_else(self):
        self.listing("fastboot", lambda: f"{FASTBOOT}\tno permissions; see [x]\n")
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        finished = ui.finished()
        self.assertEqual((finished["status"], finished["task"]), ("failed", "unlock"))
        self.assertIn("may not use the headset in fastboot mode", finished["error"])
        self.assertEqual(finished["where"], plan.FASTBOOT_WHERE)
        self.assertNotIn("device unlock", self.commands())
        # It is in fastboot already: no restart into the bootloader that adb could not send anyway.
        self.assertFalse(any(" ".join(c).endswith("reboot bootloader") for c in self.captured))

    def test_fastboot_that_leaves_out_a_device_it_may_not_read_stops_the_unlock_too(self):
        # fastboot does not list a device it may not even open read-only; the bus still shows it (Linux).
        self.listing("fastboot", lambda: "")
        with mock.patch.object(
            usbaccess, "denied", lambda mode: [Path("/dev/bus/usb/001/006")] if mode.product == "d00d" else []
        ):
            ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        finished = ui.finished()
        self.assertEqual((finished["status"], finished["task"]), ("failed", "unlock"))
        self.assertIn("may not use the headset in fastboot mode", finished["error"])

    def test_a_listing_is_unusable_only_without_a_usable_device(self):
        with mock.patch.object(usbaccess, "denied", return_value=[Path("/dev/bus/usb/001/006")]):
            self.assertTrue(plan.unusable("", tooling.FASTBOOT_MODE))
            self.assertFalse(plan.unusable(f"{FASTBOOT}\tfastboot\n", tooling.FASTBOOT_MODE))
        self.assertTrue(plan.unusable(f"{FASTBOOT}\tno permissions; see [x]\n", tooling.FASTBOOT_MODE))
        with mock.patch.object(usbaccess, "denied", return_value=[]):
            self.assertFalse(plan.unusable("", tooling.FASTBOOT_MODE))

    def test_edl_without_access_stops_the_backup_before_edl_ng_runs(self):
        self.plan.edl_denied = lambda: True
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        finished = ui.finished()
        self.assertEqual((finished["status"], finished["task"]), ("failed", "backup"))
        self.assertIn("may not use the headset in EDL mode", finished["error"])
        self.assertEqual(finished["where"], plan.EDL_WHERE)
        self.assertFalse(any("printgpt" in " ".join(map(str, c)) for c in self.captured))
        self.assertNotIn("device backup", self.commands())

    # -- a stale EDL session: the headset in EDL mode no longer answers ------------------------------
    UNANSWERED = (
        "[ERROR] An unexpected error occurred in 'printgpt': "
        "Cannot proceed: Device mode is Unknown or could not be determined."
    )

    def test_a_stale_edl_session_at_the_partition_table_says_how_to_start_afresh(self):
        original = self.capture

        def capture(argv: list[str], timeout: float) -> tuple[int, str]:
            if "printgpt" in argv:
                self.captured.append(argv)
                return 1, "Passive read timed out (no initial data from device).\n" + self.UNANSWERED
            return original(argv, timeout)

        self.capture = capture  # ty: ignore[invalid-assignment]  # the test's own capture in the method's place
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        finished = ui.finished()
        self.assertEqual((finished["status"], finished["task"]), ("failed", "backup"))
        self.assertEqual((finished["error"], finished["where"]), (qualcomm.NO_ANSWER, plan.STALE_EDL_WHERE))
        self.assertIn("power button", finished["where"])
        self.assertNotIn("device backup", self.commands())

    def test_a_loader_left_running_is_restarted_into_a_fresh_session_for_the_serial(self):
        original, replies = (
            self.capture,
            iter(["[INFO]  Detected device mode: Firehose\n" + PRINTGPT.split("Serial", maxsplit=1)[0]]),
        )

        def capture(argv: list[str], timeout: float) -> tuple[int, str]:
            if "printgpt" in argv:
                self.captured.append(argv)
                return 0, next(replies, PRINTGPT)
            return original(argv, timeout)

        self.capture = capture  # ty: ignore[invalid-assignment]  # the test's own capture in the method's place
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        self.assertEqual(ui.finished()["status"], "done", ui.finished())
        texts = [" ".join(map(str, c)) for c in self.captured]
        fresh = [n for n, t in enumerate(texts) if "reset --mode Edl" in t]
        self.assertEqual(len(fresh), 1)
        self.assertIn("printgpt", texts[fresh[0] + 1])  # identified again in the fresh session
        self.assertEqual(self.settings().edl_serial, EDL)

    def test_a_stale_edl_session_in_the_dump_says_the_same(self):
        original = self.spawn

        def spawn(argv: list[str], stdin: bytes | None, writes: bool, line: Callable[[str], None]) -> int:  # noqa: FBT001  # the Spawn signature
            if "backup" in argv and "--execute" in argv:
                self.spawned.append((argv, stdin, writes))
                line("error: Command failed: " + self.UNANSWERED)
                return 1
            return original(argv, stdin, writes, line)

        self.spawn = spawn  # the test's own spawn in the method's place
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        finished = ui.finished()
        self.assertEqual((finished["task"], finished["error"]), ("backup", qualcomm.NO_ANSWER))
        self.assertEqual(finished["where"], plan.STALE_EDL_WHERE)
        self.assertNotIn("device verify-backup", self.commands())

    def test_an_unfinished_backup_folder_stays_and_the_new_one_goes_elsewhere(self):
        self.backup.mkdir(parents=True)
        (self.backup / "edl.log").write_text("half a dump")
        self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        new = self.settings().backup
        self.assertTrue(new.startswith("backup/stock-"), new)
        self.assertTrue((self.backup / "edl.log").exists())

    def test_this_headsets_unfinished_backup_goes_on_where_it_stopped(self):
        self.saved_identity("5.13.7")
        self.backup.mkdir(parents=True)
        record = {"identity": json.loads(self.identity.read_text())}
        (self.backup / qualcomm.UNFINISHED).write_text(json.dumps(record))
        self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        self.assertEqual(self.settings().backup, self.backup.as_posix())
        backup = next(argv for argv, _, _ in self.spawned if "backup" in argv and "--execute" in argv)
        self.assertIn("--resume", backup)
        # Another headset's unfinished backup stays as it is: this one goes elsewhere, from the start.
        record["identity"]["serial"] = "SOMEONE"
        (self.backup / qualcomm.UNFINISHED).write_text(json.dumps(record))
        self.spawned.clear()
        self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        self.assertTrue(self.settings().backup.startswith("backup/stock-"), self.settings().backup)
        backup = next(argv for argv, _, _ in self.spawned if "backup" in argv and "--execute" in argv)
        self.assertNotIn("--resume", backup)

    def test_a_link_that_timed_out_mid_backup_says_to_restart_and_that_it_goes_on(self):
        original = self.spawn

        def spawn(argv: list[str], stdin: bytes | None, writes: bool, line: Callable[[str], None]) -> int:  # noqa: FBT001  # the Spawn signature
            if "backup" in argv and "--execute" in argv:
                self.spawned.append((argv, stdin, writes))
                line(r"error: Command failed: \.\COM5 timed out.")
                return 1
            return original(argv, stdin, writes, line)

        self.spawn = spawn  # the test's own spawn in the method's place
        ui = self.run_stages(("connect", "prepare"), self.yes("plug-in", "consent"))
        finished = ui.finished()
        self.assertEqual((finished["task"], finished["error"]), ("backup", qualcomm.LINK_LOST))
        self.assertEqual(finished["where"], plan.LINK_LOST_WHERE)
        self.assertIn("goes on from where it stopped", plan.LINK_LOST_WHERE)
        # The port or the cable is almost always the cause: the hint starts with them.
        self.assertTrue(plan.LINK_LOST_WHERE.split(". ")[1].startswith("Plug the cable into another USB port"))
        self.assertIn("another USB-C data cable", plan.LINK_LOST_WHERE)

    def test_declining_the_consent_writes_nothing(self):
        answers = self.yes("plug-in")
        answers["consent"] = {"answer": "consent", "accepted": False, "values": {}}
        ui = self.run_stages(("connect", "prepare"), answers)
        self.assertEqual(ui.finished()["status"], "stopped")
        self.assertEqual(self.commands(), ["device probe"])

    def test_the_wifi_password_reaches_the_headset_on_stdin_only(self):
        answers = {"wifi": {"answer": "wifi", "accepted": True, "values": {"ssid": "Home Net", "password": PASSWORD}}}
        answers |= self.yes("ssh-key")
        ui = self.run_stages(("install",), answers)
        self.assertEqual(ui.finished()["status"], "done", ui.finished())
        self.assertEqual(self.commands(), ["dev install", "dev installed", *(["dev check"] if END_CHECK else [])])
        ssh = [(argv, data) for argv, data, _ in self.spawned if data is not None]
        self.assertEqual(len(ssh), 1)
        argv, data = ssh[0]
        self.assertIn(plan.WIFI_FILE, argv[-1])
        self.assertTrue(all(PASSWORD not in word and "Home Net" not in word for word in argv))
        self.assertIn(f"psk={PASSWORD}".encode(), data or b"")
        self.assertIn(b"ssid=Home Net", data or b"")
        self.assertNotIn(PASSWORD, json.dumps(ui.events))
        self.assertNotIn("Home Net", json.dumps(ui.events))
        for path in (self.root / "out" / "install" / "phoenix").iterdir():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(PASSWORD, text, path.name)
            self.assertNotIn("Home Net", text, path.name)
        self.assertNotIn(PASSWORD, self.env.read_text(encoding="utf-8"))
        self.assertIn(plan.READY, [e["text"] for e in ui.events if e["event"] == "note"])

    def test_no_wifi_writes_no_profile(self):
        ui = self.run_stages(("install",), self.yes("wifi", "ssh-key"))
        self.assertEqual(ui.finished()["status"], "done")
        self.assertFalse([1 for _, data, _ in self.spawned if data is not None])

    def test_a_skipped_wifi_is_not_asked_again_in_a_later_run(self):
        ui = self.run_stages(("install",), self.yes("wifi", "ssh-key"))
        self.assertEqual(ui.finished()["status"], "done")
        state = self.root / "out" / "install" / "phoenix" / "state.json"
        record = json.loads(state.read_text(encoding="utf-8"))
        record["tasks"].pop("wifi-profile")  # a later run, after a stop before the profile
        state.write_text(json.dumps(record), encoding="utf-8")
        ui = self.run_stages(("install",), {})  # nothing answers: an ask would stop the run
        self.assertEqual(ui.finished()["status"], "done", ui.finished())
        self.assertFalse([e for e in ui.events if e["event"] == "ask"])

    def test_the_owners_ssh_key_goes_to_env_for_the_install(self):
        key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExampleExampleExampleExample me@laptop"
        answers = self.yes("wifi")
        answers["ssh-key"] = {"answer": "ssh-key", "accepted": True, "values": {"key": key}}
        ui = self.run_stages(("install",), answers)
        self.assertEqual(ui.finished()["status"], "done", ui.finished())
        settings = self.settings()
        self.assertEqual(settings.path("ssh_public_key").read_text(encoding="utf-8"), key + "\n")
        self.assertEqual(self.commands()[0], "dev install")  # which enrolls it (devloop.enrolled_keys)

    def test_no_private_key_is_taken_for_a_public_one(self):
        answers: dict = self.yes("wifi")
        private = {"answer": "ssh-key", "accepted": True, "values": {"key": "-----BEGIN OPENSSH PRIVATE KEY-----"}}
        answers["ssh-key"] = [private, {"answer": "ssh-key", "accepted": True, "values": {}}]
        ui = self.run_stages(("install",), answers)
        self.assertEqual(ui.finished()["status"], "done", ui.finished())
        self.assertIn(plan.NO_PUBLIC_KEY, [e["text"] for e in ui.events if e["event"] == "note"])
        self.assertEqual(self.settings().ssh_public_key, "")

    def test_the_doctor_ignores_what_the_installer_provides(self):
        self.checks = [
            Check("PICO4_ADB_SERIAL", doctor.HEADSET, doctor.MISSING, "empty"),
            Check("EDL backup", doctor.HEADSET, doctor.MISSING, "none"),
            Check(
                "build container image",
                doctor.BUILDS,
                doctor.MISSING,
                "not pulled",
                "python -m overpressure container pull",
            ),
            Check("git config", "build", doctor.WARNING, "none", "git config"),
            # Every stage 1 the installer starts runs on Valve's kernel: no NCM initialization, no UsbDk.
            Check("NCM Python and UsbDk", checks.STOCK_KERNEL_LINK, doctor.MISSING, "no UsbDk", "winget"),
        ]
        ui = self.run_stages(("computer",), {})
        doctor_task = [e for e in ui.events if e["event"] == "task" and e["task"] == "doctor"]
        self.assertEqual(doctor_task[-1]["status"], "done")
        self.assertIn("git config: none (git config)", [e["text"] for e in ui.events if e["event"] == "note"])
        # Every check it looked at is in the details, also the ones that passed.
        logged = [e["line"] for e in ui.events if e["event"] == "log" and e.get("task") == "doctor"]
        self.assertIn("warning: git config (build): none", logged)
        self.assertFalse(any("PICO4_ADB_SERIAL" in line for line in logged))
        self.assertFalse(any("build container" in line for line in logged))  # the headset builds the system
        self.assertFalse(any("UsbDk" in line for line in logged))
        self.checks.append(Check("ssh", doctor.HEADSET, doctor.MISSING, "not on PATH", "OpenSSH"))
        (self.root / "out" / "install" / "phoenix" / "state.json").unlink()  # a fresh start: the doctor runs again
        ui = self.run_stages(("computer",), {})
        self.assertEqual(ui.finished()["task"], "doctor")
        self.assertIn("- ssh (working on the headset): not on PATH\n  How: OpenSSH", ui.finished()["error"])


class ParserTests(unittest.TestCase):
    def test_edl_ng_output(self):
        self.assertEqual(plan.edl_serial(PRINTGPT), EDL)
        self.assertEqual(plan.edl_serial("no device"), "")
        self.assertEqual(plan.edl_luns(PRINTGPT), [1, 2, 0])

    def test_device_lists(self):
        adb = "* daemon started successfully\nList of devices attached\nA\tdevice\nB\tunauthorized\n\n"
        self.assertEqual(plan.device_rows(adb), {"A": "device", "B": "unauthorized"})
        self.assertEqual(plan.device_rows("F\tfastboot\n", ("fastboot",)), {"F": "fastboot"})

    def test_wifi_answers(self):
        self.assertEqual(plan.wifi_problem("", ""), "")
        self.assertEqual(plan.wifi_problem("net", ""), "")  # an open network
        self.assertEqual(plan.wifi_problem("net", "12345678"), "")
        self.assertEqual(plan.wifi_problem("net", "ab" * 32), "")  # the hexadecimal key
        self.assertIn("network name too", plan.wifi_problem("", "12345678"))
        self.assertIn("8 to 63", plan.wifi_problem("net", "short"))
        self.assertIn("32 bytes", plan.wifi_problem("n" * 33, ""))
        self.assertIn("control", plan.wifi_problem("net\n", "12345678"))

    def test_keyfile(self):
        text = plan.keyfile(" back\\slash", "pass word!", "uuid-1").decode()
        self.assertIn("id=\\sback\\\\slash\n", text)
        self.assertIn("[wifi-security]\nkey-mgmt=wpa-psk\npsk=pass word!\n", text)
        self.assertIn("uuid=uuid-1\n", text)
        self.assertNotIn("wifi-security", plan.keyfile("open", "", "u").decode())


class AfterInstallTests(unittest.TestCase):
    def test_the_finished_page_offers_the_upgrade_the_way_back_and_help(self):
        after = HEADSET.after_install()
        self.assertEqual(after.title, "Unplug it and put it on")
        self.assertIn("sign in to Steam", after.text)
        self.assertEqual(
            [(a.label, a.step, a.workflow) for a in after.actions],
            [
                *([("Check the headset", "", "check")] if tooling.CHECK_WORKFLOW else []),
                ("Upgrade later", "", "upgrade"),
                ("Restore to stock", "", "restore"),
                ("Get help", "support-bundle", ""),
            ],
        )
        self.assertEqual(HEADSET.finished_page("check").title, "Checked")
        if tooling.CHECK_WORKFLOW:
            self.assertIn("check", [a.workflow for a in HEADSET.finished_page("upgrade").actions])
        keys = {step.key for step in HEADSET.steps()}
        workflows = {w.key for w in HEADSET.workflows()}
        for workflow in workflows:
            for action in HEADSET.finished_page(workflow).actions:
                self.assertTrue(action.step in keys or action.workflow in workflows, action)
        self.assertEqual(HEADSET.finished_page("restore").title, "Back on PICO OS")
        self.assertIn("unlocked", HEADSET.finished_page("restore").text)


class BackupSetTests(unittest.TestCase):
    def test_the_export_carries_what_the_way_back_needs_and_an_import_names_it(self):
        settings = localconfig.Settings(adb_serial="ADB1", fastboot_serial="", edl_serial="EDL1")
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(localconfig, "load", return_value=settings):
            work = Path(folder)
            with mock.patch.object(devloop, "WORK", work):
                # Right after the backup: no key yet, as the install makes it with the maintenance system.
                self.assertEqual(HEADSET.backup_set().files, {})
                for name in ("id_ed25519", "id_ed25519.pub"):
                    (work / name).write_text("key", encoding="utf-8")
                here = HEADSET.backup_set()
                self.assertEqual(sorted(here.files), ["id_ed25519", "id_ed25519.pub"])
                (work / devloop.INSTALLED).write_text("{}", encoding="utf-8")
                self.assertIn(devloop.INSTALLED, HEADSET.backup_set().files)
            self.assertEqual(here.settings, {"PICO4_ADB_SERIAL": "ADB1", "PICO4_EDL_SERIAL": "EDL1"})
            self.assertEqual(here.folder, settings.path("backup"))
            saved = {}
            imported = BackupSet(
                settings.path("backup").with_name("stock-2"),
                settings.path("identity"),
                {},
                {"PICO4_ADB_SERIAL": "OTHER", "PICO4_FASTBOOT_SERIAL": "FB1", "PICO4_NOPE": "x"},
            )
            with mock.patch("overpressure.localconfig.update", side_effect=saved.update):
                HEADSET.adopt_backup(imported)
        self.assertEqual(saved["PICO4_BACKUP"], "backup/stock-2")
        self.assertEqual(saved["PICO4_FASTBOOT_SERIAL"], "FB1")  # .env had none
        self.assertNotIn("PICO4_ADB_SERIAL", saved)  # .env's own stays
        self.assertNotIn("PICO4_NOPE", saved)

    def test_a_backup_made_before_the_ssh_key_exports_and_imports(self):
        # An install that stopped after its backup: Export backup had refused the missing key.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stock, identity = root / "backup" / "stock-1", root / "backup" / "device.json"
            (stock / "lun4").mkdir(parents=True)
            (stock / "lun4" / "abl.bin").write_bytes(b"abl" * 100)
            ident = {"serial": ADB, "chip_id": "0x1"}
            files = {"lun4/abl.bin": backups.digest(stock / "lun4" / "abl.bin")}
            (stock / "manifest.json").write_text(json.dumps({"schema": 1, "identity": ident, "files": files}))
            identity.write_text(json.dumps(ident), encoding="utf-8")
            settings = localconfig.Settings(adb_serial=ADB, edl_serial=EDL, backup=str(stock), identity=str(identity))
            with (
                mock.patch.object(localconfig, "load", return_value=settings),
                mock.patch.object(devloop, "WORK", root / "out" / "dev"),
            ):
                bundle = HEADSET.backup_set()
            archive = root / "phoenix.overpressure-backup.tar"
            backups.export_archive(bundle, "phoenix", archive, say=lambda _line: None)
            elsewhere = BackupSet(root / "other" / "stock-1", root / "other" / "device.json", {}, {})
            (root / "other").mkdir()
            imported = backups.import_archive(archive, "phoenix", elsewhere, say=lambda _line: None)
            self.assertTrue((imported.folder / "lun4" / "abl.bin").is_file())
            self.assertEqual(imported.files, {})


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.plan = plan.Plan(HEADSET, root=Path("/nowhere"), computer=list)

    def test_with_an_older_overpressure_the_install_and_upgrade_check_at_their_end(self):
        # Without Workflow.changes (interface 1.3) the check is no workflow of its own: it would erase the
        # install's record when it finished.
        with (
            mock.patch.object(tooling, "CHECK_WORKFLOW", new=False),
            mock.patch.object(plan, "CHECK_WORKFLOW", new=False),
        ):
            self.assertNotIn("check", [w.key for w in HEADSET.workflows()])
            self.assertNotIn("check", [a.workflow for a in HEADSET.after_install().actions])
            install = next(s for s in self.plan.stages() if s.id == "install")
            self.assertEqual([t.id for t in install.tasks][-2:], ["check", "ready"])
            upgrade = next(s for s in self.plan.upgrade_stages() if s.id == "install")
            self.assertEqual([t.id for t in upgrade.tasks][-2:], ["check", "ready"])
        if tooling.CHECK_WORKFLOW:  # an overpressure with Workflow.changes
            self.assertIn("check", [w.key for w in HEADSET.workflows()])
            self.assertFalse(next(w for w in HEADSET.workflows() if w.key == "check").changes)

    def test_the_workflows_in_order_with_their_plans(self):
        check = ["check"] if tooling.CHECK_WORKFLOW else []
        self.assertEqual([w.key for w in HEADSET.workflows()], ["install", "upgrade", "restore", *check])
        self.assertEqual([w.erases for w in HEADSET.workflows()], [True, False, True, *[False for _ in check]])
        self.assertEqual([s.id for s in self.plan.check_stages()], ["check"])
        # Nothing of the check writes to the headset.
        self.assertFalse(any(t.writes for s in self.plan.check_stages() for t in s.tasks))
        self.assertEqual([s.id for s in self.plan.upgrade_stages()], ["computer", "build", "install"])
        self.assertEqual([s.id for s in self.plan.restore_stages()], ["backup", "restore", "pico-os", "bootloader"])

    def test_every_task_that_can_fail_says_where_the_headset_is_and_what_next(self):
        # Its where is what a failure shows unless the failure says more; only the closing notes have none.
        plans = (self.plan.stages(), self.plan.upgrade_stages(), self.plan.restore_stages(), self.plan.check_stages())
        for stages in plans:
            for task in (t for s in stages for t in s.tasks if t.id not in ("ready", "restored", "checked")):
                self.assertTrue(task.where, task.id)
                self.assertTrue("again" in task.where or "get help" in task.where, (task.id, task.where))

    def test_the_upgrade_rebuilds_the_system_and_keeps_the_data(self):
        stages = {s.id: s for s in self.plan.upgrade_stages()}
        self.assertNotIn("unlock-files", [t.id for t in stages["computer"].tasks])
        build = {t.id: t for t in stages["build"].tasks}
        self.assertEqual({k for k, t in build.items() if t.done is None}, {"kernel", "boot-image"})
        tasks = [t.id for t in stages["install"].tasks]
        # This version's maintenance system is written first: the headset's build and the write need its tools.
        self.assertEqual(tasks, ["plug-in", "flash", "maintenance", "install", "installed", *END_CHECK, "ready"])
        self.assertNotIn("--format-data", " ".join(self.plan.steps["install"].argv))

    def test_the_upgrade_waits_for_fastboot_and_its_maintenance_system_here_where_a_wait_asks_for_a_replug(self):
        install = {t.id: t for t in next(s for s in self.plan.upgrade_stages() if s.id == "install").tasks}
        self.assertTrue(install["flash"].writes)
        ctx = mock.Mock()
        order = []
        with mock.patch.object(plan, "AskingDev") as dev:
            dev.return_value.to_fastboot.side_effect = lambda: order.append("fastboot")
            ctx.run.side_effect = lambda argv: order.append(" ".join(argv[-2:]))
            with mock.patch.object(self.plan, "argv", side_effect=lambda _ctx, key: ["dev", key]):
                install["flash"].run(ctx)
                install["maintenance"].run(ctx)
        self.assertEqual(order, ["fastboot", "dev flash"])  # dev flash then finds the headset in fastboot
        dev.return_value.connect.assert_called_once_with()  # the login's waits here too, not in a child

    def test_the_restore_asks_once_and_writes_only_after_the_backup_is_checked(self):
        stages = self.plan.restore_stages()
        tasks = [t for s in stages for t in s.tasks]
        writes = [t.id for t in tasks if t.writes]
        # Each part its own task, so a retry goes on from the part that failed.
        self.assertEqual(writes, ["system", "data", "recovery", "bootloader-first", "restore-abl"])
        ids = [t.id for t in tasks]
        self.assertLess(ids.index("verify-backup"), ids.index("consent"))
        self.assertLess(ids.index("consent"), ids.index("maintenance"))
        self.assertLess(ids.index("maintenance"), ids.index("system"))
        self.assertEqual(self.plan.steps["restore-abl"].argv, ("cycle", "--restore-only"))

    def test_the_restore_takes_the_installed_path_unless_pico_os_runs(self):
        ctx = mock.Mock()
        ctx.settings.return_value = mock.Mock(adb_serial="SERIAL", tool=mock.Mock(return_value="adb"))
        ctx.output.return_value = "List of devices attached\n"
        with mock.patch.object(plan, "AskingDev") as dev:
            self.plan.restore_maintenance(ctx)
            self.assertEqual(ctx.ask.call_args.args[0].id, "plug-in")
            dev.return_value.recovery.assert_called_once_with()
            ctx.reset_mock()
            dev.reset_mock()
            ctx.output.return_value = "List of devices attached\nSERIAL\tdevice\n"
            self.plan.restore_maintenance(ctx)
        dev.assert_not_called()
        self.assertIn("only its bootloader", ctx.note.call_args.args[0])
        ctx.remember.assert_called_once_with(plan.PICO_OS_RAN)
        ctx.recall.return_value, ctx.completed.return_value = plan.PICO_OS_RAN, False
        self.assertTrue(self.plan.restore_written("system")(ctx))

    def test_a_restore_after_an_install_that_stopped_early_writes_only_the_bootloader(self):
        # The install stopped in the engineering bootloader's step: PICO OS's system is still there.
        ctx = mock.Mock()
        ctx.settings.return_value = mock.Mock(adb_serial="SERIAL", tool=mock.Mock(return_value="adb"))
        ctx.output.return_value = "List of devices attached\n"  # in its bootloader, not in PICO OS
        with tempfile.TemporaryDirectory() as folder:
            restore = plan.Plan(HEADSET, root=Path(folder), computer=list, edl_count=lambda: 0)
            record = Path(folder, "out", "install", HEADSET.info.codename, "state.json")
            record.parent.mkdir(parents=True)

            def stopped(tasks: dict, **more: object) -> None:
                record.write_text(json.dumps({"schema": 1, "tasks": tasks, **more}), encoding="utf-8")

            stopped({"backup": {"status": "done"}, "stage-abl": {"status": "failed"}})
            with (
                mock.patch.object(plan, "AskingDev") as dev,
                mock.patch.object(restore, "back_in_fastboot", return_value=False),  # PICO OS starts
            ):
                restore.restore_maintenance(ctx)
            dev.return_value.start_from_fastboot.assert_called_once_with()
            dev.return_value.recovery.assert_not_called()
            ctx.remember.assert_called_once_with(plan.PICO_OS_RAN)  # the system, data and recovery are skipped
            self.assertIn("still on the headset", ctx.note.call_args.args[0])

            # Once the install wrote the maintenance system, or an install ever finished, the system was replaced.
            for tasks, more in (
                ({"flash": {"status": "done"}, "install": {"status": "failed"}}, {}),
                ({"stage-abl": {"status": "failed"}}, {"completed": {"release": "1.0-beta", "at": "2026"}}),
            ):
                stopped(tasks, **more)
                ctx.reset_mock()
                with mock.patch.object(plan, "AskingDev") as dev:
                    restore.restore_maintenance(ctx)
                dev.return_value.recovery.assert_called_once_with()
                ctx.remember.assert_called_once_with("")

    def stuck_restore(self, folder: str, edl: list[int]) -> plan.Plan:
        """Return a restore on this computer after an install that stopped early; @p edl[0] counts EDL devices."""
        restore = plan.Plan(HEADSET, root=Path(folder), computer=list, edl_count=lambda: edl[0])
        record = Path(folder, "out", "install", HEADSET.info.codename, "state.json")
        record.parent.mkdir(parents=True)
        tasks = {"backup": {"status": "done"}, "stage-abl": {"status": "failed"}}
        record.write_text(json.dumps({"schema": 1, "tasks": tasks}), encoding="utf-8")
        return restore

    def test_the_engineering_bootloader_from_the_stock_bootloader_in_fastboot_goes_through_edl_by_hand(self):
        # adb needs PICO OS and the stock bootloader's EDL command the unlock token: the owner's hands it is.
        ctx = mock.Mock()
        ctx.clock = FakeClock()
        edl = [0]
        install_plan = plan.Plan(HEADSET, root=Path("/nowhere"), computer=list, edl_count=lambda: edl[0])
        task = next(t for s in install_plan.stages() for t in s.tasks if t.id == "stage-abl")
        self.assertTrue(task.writes)
        ctx.ask.side_effect = lambda ask: edl.__setitem__(0, 1) if ask.id == "edl" else None
        order: list[str] = []
        with (
            mock.patch.object(plan, "AskingDev") as dev,
            mock.patch.object(install_plan, "run_step", side_effect=lambda _ctx, key: order.append(key)),
        ):
            dev.return_value.other_abl_in_fastboot.return_value = True
            task.run(ctx)
            self.assertEqual(ctx.note.call_args.args[0], plan.STOCK_FASTBOOT)
            self.assertEqual(ctx.ask.call_args.args[0].id, "edl")
            self.assertEqual(order, ["stage-abl"])

            # From PICO OS, or in EDL already: no ask, the step goes on as before.
            for in_edl, in_fastboot in ((0, False), (1, True)):
                ctx.reset_mock(side_effect=True)
                edl[0] = in_edl
                order.clear()
                dev.return_value.other_abl_in_fastboot.return_value = in_fastboot
                task.run(ctx)
                ctx.ask.assert_not_called()
                self.assertEqual(order, ["stage-abl"])

    def test_a_headset_that_boots_back_into_fastboot_gets_its_stock_bootloader_first_from_edl(self):
        # #39: after an early stop every start ends in fastboot, so PICO OS's setup would wait for ever.
        ctx = mock.Mock()
        ctx.clock = FakeClock()
        ctx.settings.return_value = mock.Mock(adb_serial="SERIAL", tool=mock.Mock(return_value="adb"))
        ctx.output.return_value = "List of devices attached\n"
        edl = [0]
        with tempfile.TemporaryDirectory() as folder:
            restore = self.stuck_restore(folder, edl)
            with (
                mock.patch.object(plan, "AskingDev") as dev,
                mock.patch.object(restore, "back_in_fastboot", return_value=True),
            ):
                restore.restore_maintenance(ctx)
            ctx.remember.assert_called_once_with(plan.PICO_OS_STUCK)
            self.assertEqual(ctx.note.call_args.args[0], plan.STUCK)
            dev.return_value.recovery.assert_not_called()
            ctx.recall.side_effect = lambda task: plan.PICO_OS_STUCK if task == "maintenance" else ""
            ctx.completed.return_value = False
            tasks = {t.id: t for s in restore.restore_stages() for t in s.tasks}

            def done(task: str) -> bool:
                check = tasks[task].done
                assert check is not None
                return check(ctx)

            # The system's writes stay skipped; the bootloader goes first, the last stage needs no second write.
            self.assertTrue(all(done(t) for t in ("maintenance", "system", "data", "recovery")))
            self.assertFalse(done("bootloader-first"))
            self.assertFalse(done("restore-abl"))

            ctx.ask.side_effect = lambda ask: edl.__setitem__(0, 1) if ask.id == "edl" else None
            order: list[str] = []
            ctx.remember.side_effect = lambda choice: order.append(f"remember {choice}")
            with mock.patch.object(restore, "run_step", side_effect=lambda _ctx, key: order.append(key)):
                tasks["bootloader-first"].run(ctx)
            self.assertEqual(ctx.ask.call_args.args[0].id, "edl")
            self.assertEqual(order, ["restore-abl", f"remember {plan.BOOTLOADER_WRITTEN}"])
            ctx.recall.side_effect = lambda task: {
                "maintenance": plan.PICO_OS_STUCK,
                "bootloader-first": "written",
            }.get(task, "")
            self.assertTrue(done("restore-abl"))

            # A headset the owner put into EDL already counts as stuck, without starting it.
            ctx.reset_mock(side_effect=True)
            ctx.clock = FakeClock()
            edl[0] = 1
            with mock.patch.object(plan, "AskingDev") as dev:
                restore.restore_maintenance(ctx)
            dev.return_value.start_from_fastboot.assert_not_called()
            ctx.remember.assert_called_once_with(plan.PICO_OS_STUCK)

    def test_a_headset_is_stuck_when_it_comes_back_to_fastboot_and_starts_when_pico_os_answers(self):
        ctx = mock.Mock()
        ctx.clock = FakeClock()
        restore = plan.Plan(HEADSET, root=Path("/nowhere"), computer=list, edl_count=lambda: 0)
        dev = mock.Mock()

        def answers(fastboot: list[bool], adb: list[bool]) -> bool:
            dev.fastboot_listed.side_effect = iter(fastboot)
            with mock.patch.object(restore, "pico_os_running", side_effect=iter(adb)):
                return restore.back_in_fastboot(ctx, dev)

        # It leaves fastboot, then shows up there again: it cannot start PICO OS.
        self.assertTrue(answers([True, False, False, True], [False, False]))
        # It leaves fastboot, then PICO OS answers on adb.
        self.assertFalse(answers([False, False], [False, True]))
        # It never leaves fastboot.
        self.assertTrue(answers([True] * 100, []))
        # Neither comes (PICO OS's setup, USB debugging not on yet): it starts.
        self.assertFalse(answers([False] * 100, [False] * 100))
        dev.release_adb.assert_called()  # before each look: an adb server can hold it in fastboot

    def test_the_installer_writes_a_fastboot_serial_it_learns_to_env(self):
        ctx = mock.Mock()
        ctx.settings.return_value = localconfig.Settings(adb_serial=ADB, fastboot_serial="", edl_serial=EDL)
        with tempfile.TemporaryDirectory() as folder:
            dev = plan.AskingDev(ctx, Path(folder, "dev"))  # the dev loop makes its work folder
        dev.learn_fastboot_serial(FASTBOOT)
        self.assertEqual(dev.s.fastboot_serial, FASTBOOT)
        ctx.set_setting.assert_called_once_with(plan.KEY("fastboot_serial"), FASTBOOT)

    def test_each_part_of_the_restore_runs_on_its_own_and_recovery_reaches_fastboot_here(self):
        ctx = mock.Mock()
        with (
            mock.patch.object(self.plan, "argv", side_effect=lambda _ctx, key: ["python", "dev", key]),
            mock.patch.object(plan, "AskingDev") as dev,
        ):
            self.plan.restore_part("system")(ctx)
            dev.assert_not_called()
            self.plan.restore_part("recovery")(ctx)
        dev.return_value.to_fastboot.assert_called_once_with()
        argvs = [c.args[0] for c in ctx.run.call_args_list]
        self.assertEqual([a[-3:] for a in argvs], [["--installed", "--only", p] for p in ("system", "recovery")])

    def test_a_wait_that_runs_out_asks_for_a_replug_and_waits_again(self):
        clock = FakeClock()
        ctx = mock.Mock(clock=clock)
        there_at = 200.0  # after the first wait's 180 s

        def probe() -> str:
            if clock.now < there_at:
                msg = "not yet"
                raise RuntimeError(msg)
            return "SERIAL"

        self.assertEqual(plan.replug_until(ctx, "fastboot mode", 180, probe), "SERIAL")
        self.assertEqual([c.args[0].id for c in ctx.ask.call_args_list], ["replug"])
        self.assertIn("fastboot mode", ctx.ask.call_args.args[0].text)
        # Cancel stops the run (the ask raises Stopped); an error waiting does not cure ends it without an ask.
        clock.now = 0.0
        ctx.ask.side_effect = Stopped("declined")
        with self.assertRaises(Stopped):
            plan.replug_until(ctx, "fastboot mode", 10, probe)
        ctx.ask.reset_mock()

        def refused() -> str:
            msg = "no access"
            raise usbaccess.NoAccessError(msg)

        with self.assertRaises(usbaccess.NoAccessError):
            plan.replug_until(ctx, "fastboot mode", 10, refused)
        ctx.ask.assert_not_called()

    def test_the_restore_waits_for_pico_os_s_mcu_check_after_its_setup(self):
        ctx = mock.Mock(clock=FakeClock())
        ctx.settings.return_value = mock.Mock(adb_serial="SERIAL", tool=mock.Mock(return_value="adb"))
        ctx.output.side_effect = lambda argv, *_a, **_k: (
            "0\n" if "sys.pxr.init.status" in " ".join(argv) else "List of devices attached\nSERIAL\tdevice\n"
        )
        with self.assertRaises(plan.Failed) as failed:
            self.plan.pico_os_setup(ctx)
        self.assertEqual(ctx.ask.call_args.args[0].id, "pico-os")
        self.assertIn("timed out waiting for PICO OS's check", str(failed.exception))
        self.assertIn("keep the headset on your head", ctx.note.call_args.args[0].lower())

    def test_an_upgrade_needs_an_installed_system_and_a_restore_a_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            with mock.patch.object(devloop, "WORK", work):
                self.assertIn("install it first", HEADSET.workflow_problem("upgrade"))
                (work / devloop.INSTALLED).write_text("{}", encoding="utf-8")
                self.assertEqual(HEADSET.workflow_problem("upgrade"), "")
            backup = work / "backup"
            with mock.patch.object(HEADSET, "backup_set", return_value=mock.Mock(folder=backup)):
                self.assertIn("import", HEADSET.workflow_problem("restore"))
                backup.mkdir()
                (backup / "manifest.json").write_text("{}", encoding="utf-8")
                self.assertEqual(HEADSET.workflow_problem("restore"), "")
        self.assertEqual(HEADSET.workflow_problem("install"), "")


def _tail(word: str) -> str:
    """Return the last two parts of a tool path (pt/adb), whatever the host's separators and suffix."""
    path = Path(word)
    return f"{path.parent.name}/{path.stem}"


if __name__ == "__main__":
    unittest.main()
