# SPDX-License-Identifier: Unlicense
"""The PICO 4's device tool: the unlock token, EDL backups, the ABL stage and restore, the unlock, against fakes."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from devices.phoenix.tooling import device, hardware_test, localconfig, unlockfiles
from overpressure.api.v1 import process, tools


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class ProtocolTests(unittest.TestCase):
    def test_vectors(self):
        # 1234567 = 0x12d687, masked to 0x12d206; max ID masks to 0xf7f3f37f.
        for number, token in ((0, "pico0"), (1, "picoX"), (1234567, "picoXDHD0B"), (0xFFFFFFFF, "picoMBM9M9BM")):
            self.assertEqual(device.unlock_token(number), token)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.identity = self.root / "device.json"
        self.record: dict[str, Any] = {
            "schema": 1,
            "serial": "ABC",
            "chip_id": 1234567,
            "fingerprint": "Pico/Phoenix_ovs/PICOA8110:10/5.13.7/build:user/dev-keys",
            "oem_state": "true",
        }
        self.identity.write_text(json.dumps(self.record))
        self.backup = self.root / "backup"
        self.backup.mkdir()
        (self.backup / "abl.bin").write_bytes(b"original stock ABL")
        self.manifest = {
            "schema": 1,
            "identity": self.record,
            "files": {"abl.bin": device.digest(self.backup / "abl.bin")},
        }
        (self.backup / "manifest.json").write_text(json.dumps(self.manifest))
        self.loader = self.root / "loader.elf"
        self.loader.write_bytes(b"test loader")
        self.image = self.root / "engineering.elf"
        self.image.write_bytes(b"engineering")
        self.readback = self.root / "readback.bin"

    def args(self, command: str) -> list[str]:
        args = [command, "--identity", str(self.identity), "--backup", str(self.backup)]
        if command != "unlock":
            args += ["--loader", str(self.loader)]
        if command in ("stage", "restore-abl"):
            args += ["--partition", "abl", "--readback", str(self.readback)]
        if command == "stage":
            args += ["--image", str(self.image), "--sha256", device.digest(self.image)["sha256"]]
        return args

    def call(self, args: list[str]) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return device.main(args)

    def test_previews_never_contact_tools(self):
        with patch.object(device, "run") as run:
            for command in ("backup", "stage", "unlock", "restore-abl"):
                self.assertEqual(self.call(self.args(command)), 0)
            run.assert_not_called()

    def test_python_edl_entrypoint_keeps_arguments_separate(self):
        script = self.root / "tool with spaces" / "edl.py"
        args = device.parser().parse_args(
            [*self.args("backup"), "--edl-script", str(script), "--edl-python", "venv/python.exe"]
        )
        command = device.edl_command(args, "rl", str(self.backup), "--genxml")
        # Relative tool paths become absolute: CreateProcess rejects "venv/python.exe".
        self.assertEqual(command[:3], [str(Path("venv/python.exe").resolve()), str(script.resolve()), "rl"])
        self.assertIn("--memory=ufs", command)

    def test_native_backend_requires_explicit_luns_and_keeps_commands_read_only(self):
        base = [*self.args("backup"), "--edl-backend", "edl-ng", "--edl", "native tool.exe"]
        self.assertEqual(self.call(base), 1)
        self.assertEqual(self.call([*base, "--edl-luns", "0", "0"]), 1)
        args = device.parser().parse_args([*base, "--edl-luns", "4", "0"])
        commands = device.backup_commands(args)
        self.assertEqual(len(commands), 2)
        for command, lun in zip(commands, (4, 0)):
            self.assertEqual(command[:3], ["native tool.exe", "--memory", "Ufs"])
            self.assertEqual(command[5], "dump-rawprogram")
            self.assertEqual(Path(command[6]), (self.backup / f"lun{lun}").resolve())
            # Android's cache and the crash dumps are never kept (no install or restore needs them).
            self.assertEqual(command[7:], ["--lun", str(lun), "--skip", "cache,rawdump,logdump,userdata"])
        args.include_userdata = True
        self.assertEqual(device.backup_commands(args)[0][-2:], ["--skip", "cache,rawdump,logdump"])
        self.assertEqual(device.edl_command(args, "r", "abl", "read.bin")[5:], ["read-part", "abl", "read.bin"])
        self.assertEqual(device.edl_command(args, "w", "abl", "write.bin")[5:], ["write-part", "abl", "write.bin"])

    def test_a_resumed_backup_skips_what_the_stopped_dump_read_whole(self):
        lun = self.backup / "lun4"
        lun.mkdir(parents=True)
        (lun / "rawprogram4.xml").write_text(
            '<data><program filename="abl.bin" label="abl" num_partition_sectors="1" SECTOR_SIZE_IN_BYTES="4096" '
            'physical_partition_number="4" /><program filename="xbl.bin" label="xbl" num_partition_sectors="1" '
            'SECTOR_SIZE_IN_BYTES="4096" physical_partition_number="4" /></data>'
        )
        (lun / "abl.bin").write_bytes(b"x" * 4096)
        base = [*self.args("backup"), "--edl-backend", "edl-ng", "--edl", "edl-ng", "--edl-luns", "4", "0"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(device.main([*base, "--resume"]), 0)
        skips = [line.split("--skip ", 1)[1] for line in out.getvalue().splitlines() if "--skip" in line]
        self.assertEqual(skips, ["cache,rawdump,logdump,userdata,abl", "cache,rawdump,logdump,userdata"])

    def test_the_dump_shows_its_partitions_and_fails_on_an_error(self):
        script = "import sys; sys.stdout.write('Dumping aop: 50.0% (1 / 2 MiB)\\rDumping aop: 100.0%\\n')"
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertIn("Dumping aop: 100.0%", device.run_dump([sys.executable, "-c", script]))
        self.assertEqual(out.getvalue().splitlines(), ["progress: reading aop (50%)", "progress: reading aop (100%)"])
        with self.assertRaisesRegex(process.CommandFailed, "failed"), contextlib.redirect_stderr(io.StringIO()):
            device.run_dump([sys.executable, "-c", "print('ERROR: no answer')"])
        noise = "print('libusb: warning [darwin_transfer_status] transfer error: timed out'); print('Dump completed')"
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIn("Dump completed", device.run_dump([sys.executable, "-c", noise]))
        stalled = "print('libusb: error [submit_bulk_transfer] bulk transfer failed (dir = In): pipe is stalled')"
        with self.assertRaises(process.CommandFailed), contextlib.redirect_stderr(io.StringIO()):
            device.run_dump([sys.executable, "-c", stalled])
        # The error keeps the output's end; the backup's edl.log takes all of it from the error.
        script = "print('first line'); print('x' * 2000); print('ERROR: QUD write to COM5 timed out')"
        with self.assertRaises(process.CommandFailed) as caught, contextlib.redirect_stderr(io.StringIO()):
            device.run_dump([sys.executable, "-c", script])
        self.assertIn("first line", caught.exception.output)
        self.assertNotIn("first line", str(caught.exception))

    def test_backup_requires_recovery_files_and_does_not_overwrite(self):
        args = [*self.args("backup"), "--execute", "--confirm-device", "ABC"]
        with patch.object(device, "run_dump") as run:
            self.assertEqual(self.call(args), 1)
            run.assert_not_called()
        fresh = self.root / "fresh"
        args[args.index("--backup") + 1] = str(fresh)

        def fake(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # device.run_dump's signature
            for name in ("abl.bin", "boot.bin", "devinfo.bin", "gpt_main0.bin", "rawprogram0.xml"):
                (fresh / name).write_bytes(b"test recovery data")
            return "OK"

        with patch.object(device, "run_dump", side_effect=fake):
            self.assertEqual(self.call(args), 0)
        self.assertEqual(len(device.verified_backup(fresh)["files"]), 5)
        incomplete = self.root / "incomplete"
        args[args.index("--backup") + 1] = str(incomplete)
        with patch.object(device, "run_dump", return_value="OK"):
            self.assertEqual(self.call(args), 1)
        self.assertFalse((incomplete / "manifest.json").exists())

    def test_stage_validates_image_partition_and_readback_before_write(self):
        base = [*self.args("stage"), "--execute", "--confirm-device", "ABC"]
        with patch.object(device, "run") as run:
            for flag, value in (
                ("--sha256", "0" * 64),
                ("--partition", "boot"),
                ("--partition", "abl_a"),
                ("--readback", str(self.backup / "new.bin")),
            ):
                args = base.copy()
                args[args.index(flag) + 1] = value
                self.assertEqual(self.call(args), 1)
            self.image.write_bytes(b"oversize" * 100)
            self.assertEqual(self.call(base), 1)
            run.assert_not_called()

    def test_tampered_backup_blocks_every_write(self):
        (self.backup / "abl.bin").write_bytes(b"tampered")
        with patch.object(device, "run") as run:
            for command in ("stage", "unlock", "restore-abl"):
                self.assertEqual(self.call([*self.args(command), "--execute", "--confirm-device", "ABC"]), 1)
            run.assert_not_called()

    def test_manifest_cannot_escape_backup(self):
        self.manifest["files"] = {"../loader.elf": device.digest(self.loader)}
        (self.backup / "manifest.json").write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError):
            device.verified_backup(self.backup)

    def test_wrong_device_and_missing_wipe_ack(self):
        with patch.object(device, "run") as run:
            self.assertEqual(self.call([*self.args("unlock"), "--execute", "--confirm-device", "OTHER"]), 1)
            self.assertEqual(self.call([*self.args("unlock"), "--execute", "--confirm-device", "ABC"]), 1)
            run.assert_not_called()

    def test_unknown_firmware_blocks_execution(self):
        self.record["fingerprint"] = self.record["fingerprint"].replace("5.13.7", "5.14.0")
        self.identity.write_text(json.dumps(self.record))
        with patch.object(device, "run") as run:
            self.assertEqual(self.call(self.args("unlock")), 1)
            run.assert_not_called()

    def test_the_global_builds_with_the_verified_code_pass(self):
        same = "the verified build's code before the kernel"
        for version, state, support in (
            ("5.13.7", "true", "verified"),
            ("5.13.3", "true", same),
            ("5.13.8", "true", same),
            # sek: the seko build again with another AVB key; the install keeps the headset's own vbmeta.
            ("5.13.3", "", same),
            ("5.13.7", "", same),
            ("5.13.8", "", same),
            ("5.13.7", "false", same),  # as firmware.json and match call an empty state
        ):
            self.record["fingerprint"] = f"Pico/Phoenix_ovs/PICOA8110:10/{version}/build:user/dev-keys"
            self.record["oem_state"] = state
            self.identity.write_text(json.dumps(self.record))
            with self.subTest(version=version, state=state):
                # The saved record stays as it is: backups compare it as a whole.
                self.assertEqual(device.read_identity(self.identity), self.record)
                self.assertEqual(device.firmware_support(self.record), support)

    def test_the_one_china_build_the_install_has_run_on_passes(self):
        # The whole fingerprint, not 5.13.7/cn/true: that names every China build of the release.
        self.record["fingerprint"] = "Pico/Phoenix/PICOA8110:10/5.13.7/smartcm.1761755159:user/dev-keys"
        self.identity.write_text(json.dumps(self.record))
        # The saved record stays as it is: backups compare it as a whole.
        self.assertEqual(device.read_identity(self.identity), self.record)
        self.assertEqual(
            device.firmware_support(self.record),
            "accepted: the install and the installed system have run on this build",
        )
        for fingerprint in (
            "Pico/Phoenix/PICOA8110:10/5.13.7/smartcm.1761755159:user/release-keys",  # another tags value
            "Pico/Phoenix/PICOA8110:10/5.13.7/other.1761755159:user/dev-keys",  # another build id
            "Pico/Phoenix/PICOA8110:10/5.13.8/smartcm.1761755159:user/dev-keys",  # another version
        ):
            self.record["fingerprint"] = fingerprint
            self.identity.write_text(json.dumps(self.record))
            with self.subTest(fingerprint=fingerprint):
                with self.assertRaisesRegex(ValueError, "China builds run another XBL and AOP"):
                    device.read_identity(self.identity)

    def test_every_other_build_is_refused_with_its_reason(self):
        for product, version, state, reason in (
            ("Phoenix", "5.13.7", "true", "China builds run another XBL and AOP"),
            ("phoenix", "5.13.7", "false", "China builds"),
            ("Phoenix_ovs", "5.13.9", "true", "has not been compared"),
            ("Phoenix_ovs", "5.12.0", "true", "has not been compared"),
        ):
            self.record["fingerprint"] = f"Pico/{product}/PICOA8110:10/{version}/build:user/dev-keys"
            self.record["oem_state"] = state
            self.identity.write_text(json.dumps(self.record))
            with self.subTest(product=product, version=version, state=state):
                with self.assertRaisesRegex(ValueError, reason):
                    device.read_identity(self.identity)
                with self.assertRaisesRegex(ValueError, "Nothing was changed"):
                    device.read_identity(self.identity)

    def test_other_pico_headsets_are_refused(self):
        for model in (
            "Pico/Phoenix_ovs/PICOA9210:10/5.13.7/x:user/dev-keys",  # another model number
            "Pico/Sparrow/PICOA8110:10/5.13.7/x:user/dev-keys",  # another codename
            "Pico/Hermes/A7H10:10/5.13.7/x:user/dev-keys",
        ):
            self.record["fingerprint"] = model
            self.identity.write_text(json.dumps(self.record))
            with self.assertRaisesRegex(ValueError, "original PICO 4"):
                device.read_identity(self.identity)

    def test_the_pico_4_pro_and_enterprise_are_refused(self):
        # They share the PICO 4's fingerprint; PICO OS tells them apart by model and memory.
        for model, ddr in (
            ("PICO 4 Pro", "DDR5"),
            ("PICO 4 Enterprise", "DDR5"),
            ("PICO 4 Pro", "DDR4"),
            ("PICO 4", "DDR5"),
        ):
            self.record.update(model=model, ddr_type=ddr)
            self.identity.write_text(json.dumps(self.record))
            with self.assertRaisesRegex(ValueError, "Pro and Enterprise are not verified"):
                device.read_identity(self.identity)
        # A probe that got no answer is refused too; the PICO 4 itself passes.
        self.record.update(model="", ddr_type="DDR4")
        self.identity.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(ValueError, "as nothing"):
            device.read_identity(self.identity)
        self.record.update(model="PICO 4", ddr_type="DDR4")
        self.identity.write_text(json.dumps(self.record))
        self.assertEqual(device.read_identity(self.identity)["model"], "PICO 4")
        # An identity saved before the probe read them stays valid.
        del self.record["model"], self.record["ddr_type"]
        self.identity.write_text(json.dumps(self.record))
        device.read_identity(self.identity)

    def test_the_probe_records_model_and_memory(self):
        answers = {
            "ro.build.fingerprint": "Pico/Phoenix_ovs/PICOA8110:10/5.13.7/x:user/dev-keys",
            "ro.oem.state": "true",
            "pxr.vendorhw.product.model": "PICO 4",
            "ro.vendor.ddr_type": "DDR4",
        }

        def run(argv: list[str], *_: object, **__: object) -> str:
            return answers.get(argv[-1], "0x1234")

        output = self.identity.with_name("probed.json")
        with (
            patch.object(device, "run", side_effect=run),
            patch.object(device.android, "select_device", return_value="ABC"),
        ):
            self.call(["probe", "--adb", "adb", "--output", str(output)])
        record = json.loads(output.read_text())
        self.assertEqual((record["model"], record["ddr_type"]), ("PICO 4", "DDR4"))

    def test_the_probe_runs_the_settings_adb_unless_one_is_named(self):
        # With platform-tools fetched and none on PATH, as docs/device-preparation.md runs it.
        fetched = self.root / "cache" / "tools" / "platform-tools" / "adb"
        settings = localconfig.load(self.root / ".env", {})
        seen: list[str] = []

        def run(argv: list[str], *_: object, **__: object) -> str:
            seen.append(argv[0])
            return "0x1234"

        output = self.identity.with_name("probed.json")
        with (
            patch.object(device, "run", side_effect=run),
            patch.object(device.android, "select_device", return_value="ABC") as select,
            patch.object(device.localconfig, "load", return_value=settings),
            patch.object(tools, "program", return_value=fetched),
            patch.dict(os.environ, {"PATH": ""}),
        ):
            self.call(["probe", "--output", str(output)])
            self.assertEqual(select.call_args.args[0], str(fetched))
            self.assertEqual(set(seen), {str(fetched)})
            seen.clear()
            self.call(["probe", "--adb", "/opt/adb", "--output", str(output)])
            self.assertEqual(set(seen), {"/opt/adb"})

    def test_hwtest_runs_the_settings_adb_and_fastboot_unless_named(self):
        class ParsedError(Exception):
            pass

        given: list[tuple[object, object]] = []

        def stop(args: object) -> None:
            given.append((getattr(args, "adb", None), getattr(args, "fastboot", "-")))
            raise ParsedError

        preflight = ["preflight", "--rootfs", "x.tar"]
        flash = ["flash-run", "--serial", "A", "--image", "i", "--backup", "b", "--confirm-device", "A"]
        with patch.object(device, "default_tools", side_effect=stop):
            for argv in (preflight, flash, [*flash, "--adb", "/opt/adb"]):
                with self.assertRaises(ParsedError):
                    hardware_test.main(argv)
        self.assertEqual(given, [(None, "-"), (None, None), ("/opt/adb", None)])

    def test_the_panel_decides(self):
        sharp = "console=x msm_drm.dsi_display0=qcom,mdss_dsi_sharp_ls026b3sa_90_video: overpressure.part=recovery"
        self.assertEqual(device.check_panel(sharp), "sharp_ls026b3sa")
        with self.assertRaisesRegex(ValueError, "nt57900.*not verified"):
            device.check_panel("msm_drm.dsi_display0=qcom,mdss_dsi_innolux_nt57900_90_video:")
        with self.assertRaisesRegex(ValueError, "names no panel"):
            device.check_panel("console=x")

    def test_the_panel_in_pico_os(self):
        # PANEL_PROBE's output on PICO OS 5.13.7, where the shell may not read the command line.
        denied = "cat: /proc/cmdline: Permission denied\n"
        log = (
            "11-09 11:07:36.430   927  1623 I Dpps    : DppsFactoryImpSDM::GetLtmXmlParser():842 "
            "panel_name {}_90hz_video_mode_dsi_panel\n"
        )
        shown = 'Display 1234 (HWC display 0): port=129 pnpId=QCM displayName="{}"\n'
        sharp = denied + log.format("sharp_ls026b3sa") + shown.format("sharp ls026b3")
        self.assertEqual(device.check_panel_in_pico_os(sharp), "sharp_ls026b3sa")
        # The log has rotated past the boot: SurfaceFlinger's short name.
        self.assertEqual(device.check_panel_in_pico_os(denied + shown.format("sharp ls026b3")), "sharp_ls026b3sa")
        # The other panel, by either source.
        with self.assertRaisesRegex(ValueError, "nt57900_innolux is not verified"):
            device.check_panel_in_pico_os(denied + log.format("nt57900_innolux"))
        with self.assertRaisesRegex(ValueError, "innolux_nt57 is not verified"):
            device.check_panel_in_pico_os(denied + shown.format("innolux nt57"))
        # Nothing readable: refused.
        for output in (denied, denied + shown.format("")):
            with self.assertRaisesRegex(ValueError, "names no display panel"):
                device.check_panel_in_pico_os(output)
        # A readable command line decides, as before.
        cmdline = "msm_drm.dsi_display0=qcom,mdss_dsi_innolux_nt57900_90_video:\n" + shown.format("sharp ls026b3")
        with self.assertRaisesRegex(ValueError, "nt57900 is not verified"):
            device.check_panel_in_pico_os(cmdline)

    def test_the_unlock_files_are_checked_before_they_are_written(self):
        root = self.root

        class Settings:
            def path(self, name: str) -> Path:
                return root / "assets" / f"{name}.elf"

        with self.assertRaisesRegex(ValueError, "not the known file"):
            unlockfiles.fetch(Settings(), log=lambda _: None, read=lambda _: b"other")
        self.assertFalse((self.root / "assets" / "engineering_abl.elf").exists())
        self.assertEqual({f.setting for f in unlockfiles.FILES}, {"engineering_abl", "loader"})
        for item in unlockfiles.FILES:
            self.assertTrue(item.url.startswith("https://"))
            self.assertRegex(item.sha256, "^[0-9a-f]{64}$")

    def test_write_and_readback(self):
        commands = []

        def fake(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # device.run's signature
            commands.append(command)
            if command[1] == "r":
                Path(command[3]).write_bytes(self.image.read_bytes() + b"padding")
            return "OK"

        with patch.object(device, "run", side_effect=fake):
            self.assertEqual(self.call([*self.args("stage"), "--execute", "--confirm-device", "ABC"]), 0)
        self.assertEqual([c[1] for c in commands], ["w", "r"])
        self.assertEqual((self.backup / "abl.bin").read_bytes(), b"original stock ABL")

    def test_bad_readback_is_failure(self):
        def fake(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # device.run's signature
            if command[1] == "r":
                Path(command[3]).write_bytes(b"bad")
            return "OK"

        with patch.object(device, "run", side_effect=fake):
            self.assertEqual(self.call([*self.args("stage"), "--execute", "--confirm-device", "ABC"]), 1)

    def test_restore_uses_original(self):
        commands = []

        def fake(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # device.run's signature
            commands.append(command)
            if command[1] == "r":
                Path(command[3]).write_bytes((self.backup / "abl.bin").read_bytes())
            return "OK"

        with patch.object(device, "run", side_effect=fake):
            self.assertEqual(self.call([*self.args("restore-abl"), "--execute", "--confirm-device", "ABC"]), 0)
        self.assertEqual(Path(commands[0][3]), (self.backup / "abl.bin").resolve())

    def restarting_headset(self, commands: list[list[str]], gone: int = 2) -> Any:  # noqa: ANN401  # device.run's fake
        """Fake fastboot for a headset that leaves USB for @p gone listings after each restart into its bootloader."""
        state = {"gone": 0}

        def fake(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # device.run's signature
            commands.append(command)
            if command[-1] == "devices":
                if state["gone"]:
                    state["gone"] -= 1
                    return ""
                return "ABC fastboot"
            if command[-2:] == ["reboot", "bootloader"]:
                state["gone"] = gone
            if command[-1] == "device-info":
                return "Device unlocked: true\nDevice critical unlocked: true"
            return "OKAY"

        return fake

    def test_unlock_checks_after_reboot_and_stops_on_failure(self):
        commands = []
        fake = self.restarting_headset(commands)
        args = [*self.args("unlock"), "--execute", "--confirm-device", "ABC", "--accept-data-loss"]
        with patch.object(device, "run", side_effect=fake), patch.object(device, "CLOCK", FakeClock()):
            self.assertEqual(self.call(args), 0)
        sent = [command[3:] for command in commands if command[-1] != "devices"]
        # A fresh bootloader session first: a locked headset is in the fastboot its failed boot left,
        # where the unlock flags cannot be saved. The OEM authorization belongs to the new session.
        self.assertEqual(sent[0], ["reboot", "bootloader"])
        self.assertEqual(sent[1][0], "oem")
        self.assertEqual(sent[1][-1], "unlock")
        # Real engineering ABL rejected persistent unlock writes until permissive
        # mode was enabled. Normal unlock must also precede critical unlock.
        self.assertEqual(
            sent[2:5],
            [
                ["oem", "setenforce", "0"],
                ["flashing", "unlock"],
                ["flashing", "unlock_critical"],
            ],
        )
        self.assertEqual(sent[-2], ["reboot", "bootloader"])
        self.assertEqual(sent[-1], ["oem", "device-info"])
        with patch.object(device, "run", side_effect=["ABC fastboot", ValueError("failure")]) as run:
            self.assertEqual(self.call(args), 1)
            self.assertEqual(run.call_count, 2)

        # Unlocked before (a new install after the way back): the bootloader refuses the unlock as
        # done, as the PICO 4's does; the check after the reboot still decides.
        def unlocked_before(command: list[str], timeout: float = 60) -> str:  # noqa: ARG001  # device.run's signature
            if command[-2:] in (["flashing", "unlock"], ["flashing", "unlock_critical"]):
                msg = "Command failed (1): FAILED (remote: '\\tDevice already : unlocked!')"
                raise ValueError(msg)
            return fake(command)

        commands.clear()
        with patch.object(device, "run", side_effect=unlocked_before), patch.object(device, "CLOCK", FakeClock()):
            self.assertEqual(self.call(args), 0)
        self.assertEqual(commands[-1][-2:], ["oem", "device-info"])
        # Any other refusal still stops it.
        with patch.object(
            device,
            "run",
            side_effect=lambda c, **_: (
                "ABC fastboot" if c[-1] == "devices" else (_ for _ in ()).throw(ValueError("FAILED (remote: 'locked')"))
            ),
        ):
            self.assertEqual(self.call(args), 1)
        commands.clear()
        locked = self.restarting_headset(commands)

        def still_locked(command: list[str], timeout: float = 60) -> str:
            return "Device unlocked: false" if command[-1] == "device-info" else locked(command, timeout)

        with patch.object(device, "run", side_effect=still_locked), patch.object(device, "CLOCK", FakeClock()):
            self.assertEqual(self.call(args), 1)

    def unlock(self, fake: Any, clock: FakeClock) -> int:  # noqa: ANN401  # device.run's fake
        args = [*self.args("unlock"), "--execute", "--confirm-device", "ABC", "--accept-data-loss"]
        with patch.object(device, "run", side_effect=fake), patch.object(device, "CLOCK", clock):
            return self.call(args)

    def test_unlock_sends_nothing_after_a_restart_until_the_headset_is_back(self):
        # macOS: fastboot's reboot returns while the headset is still listed; a command sent then fails
        # writing to the leaving headset ("could not clear input pipe").
        commands = []
        self.assertEqual(self.unlock(self.restarting_headset(commands, gone=3), FakeClock()), 0)
        restarts = [i for i, command in enumerate(commands) if command[-2:] == ["reboot", "bootloader"]]
        self.assertEqual(len(restarts), 2)
        for i in restarts:
            # Not listed three times (it left), then listed again (back), then the next command.
            self.assertEqual([command[-1] for command in commands[i + 1 : i + 5]], ["devices"] * 4)
            self.assertNotEqual(commands[i + 5][-1], "devices")

    def test_unlock_goes_on_after_a_restart_too_quick_to_be_seen(self):
        commands, clock = [], FakeClock()
        self.assertEqual(self.unlock(self.restarting_headset(commands, gone=0), clock), 0)
        self.assertEqual(commands[-1][-2:], ["oem", "device-info"])
        # Each of the two restarts waited out its bound once, and no longer.
        self.assertGreaterEqual(clock.now, 2 * device.LEAVE_FASTBOOT_SECONDS)
        self.assertLess(clock.now, 2 * device.LEAVE_FASTBOOT_SECONDS + 2)

    def test_unlock_stops_when_the_headset_does_not_come_back(self):
        commands, clock = [], FakeClock()
        self.assertEqual(self.unlock(self.restarting_headset(commands, gone=10**6), clock), 1)
        # Nothing after the first restart but listings: no OEM command to a headset that is not there.
        first = next(i for i, command in enumerate(commands) if command[-2:] == ["reboot", "bootloader"])
        self.assertTrue(all(command[-1] == "devices" for command in commands[first + 1 :]))
        self.assertGreaterEqual(clock.now, device.BACK_IN_FASTBOOT_SECONDS)


SECTOR = 4096
SECTORS = 64  # the fake headset's one LUN


class RestoreAllTests(unittest.TestCase):
    """device restore-all: a one-LUN backup written back through a fake edl-ng that holds the LUN's bytes."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.identity = self.root / "device.json"
        record = {
            "schema": 1,
            "serial": "ABC",
            "chip_id": 1234567,
            "fingerprint": "Pico/Phoenix_ovs/PICOA8110:10/5.13.7/build:user/dev-keys",
            "oem_state": "true",
        }
        self.identity.write_text(json.dumps(record))
        self.backup = self.root / "backup"
        folder = self.backup / "lun0"
        folder.mkdir(parents=True)
        rows = [
            ("PrimaryGPT", "gpt_main0.bin", "0", 6),
            ("BackupGPT", "gpt_backup0.bin", "NUM_DISK_SECTORS-5.", 5),
            ("abl", "abl.bin", "6", 4),
            ("userdata", "userdata.bin", "10", 40),
        ]
        programs = "".join(
            f'<program filename="{name}" label="{label}" start_sector="{start}" num_partition_sectors="{n}" '
            f'SECTOR_SIZE_IN_BYTES="{SECTOR}" physical_partition_number="0" />'
            for label, name, start, n in rows
        )
        (folder / "rawprogram0.xml").write_text(f"<data>{programs}</data>")
        header = b"EFI PART" + bytes(24) + (SECTORS - 1).to_bytes(8, "little")
        files = {
            "gpt_main0.bin": bytes(SECTOR) + header.ljust(SECTOR, b"\0") + bytes(4 * SECTOR),
            "gpt_backup0.bin": b"B" * 5 * SECTOR,
            "abl.bin": b"A" * 4 * SECTOR,
        }  # userdata is never kept: it gets zeros
        for name, data in files.items():
            (folder / name).write_bytes(data)
        manifest = {
            "schema": 1,
            "identity": record,
            "luns": [0],
            "files": {f"lun0/{name}": device.digest(folder / name) for name in files},
        }
        (self.backup / "manifest.json").write_text(json.dumps(manifest))
        self.loader = self.root / "loader.elf"
        self.loader.write_bytes(b"test loader")
        self.disk = bytearray(b"\xff" * SECTORS * SECTOR)
        self.storage = f'"total_blocks": {SECTORS}\nUFS Current LUN Number: = 0x0'
        self.serial = "Serial Number: EDL1"
        self.commands: list[list[str]] = []

    def edl(self, argv: list[str], _timeout: float = 60) -> str:
        self.commands.append(argv)
        if "printgpt" in argv:
            return f"{self.serial}\n{self.storage}"
        at = argv.index("write-sector") if "write-sector" in argv else argv.index("read-sector")
        start = int(argv[at + 1]) * SECTOR
        if argv[at] == "write-sector":
            data = Path(argv[at + 2]).read_bytes()
            self.disk[start : start + len(data)] = data
        else:
            Path(argv[at + 3]).write_bytes(bytes(self.disk[start : start + int(argv[at + 2]) * SECTOR]))
        return "OK"

    def call(self, *extra: str) -> tuple[int, str]:
        args = [
            "restore-all",
            *("--identity", str(self.identity), "--backup", str(self.backup), "--loader", str(self.loader)),
            *("--edl-backend", "edl-ng", "--edl", "edl-ng", "--work", str(self.root / "work")),
            *extra,
        ]
        out = io.StringIO()
        with (
            patch.object(device, "run", side_effect=self.edl),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(out),
        ):
            code = device.main(args)
        return code, out.getvalue()

    def execute(self, *extra: str) -> tuple[int, str]:
        return self.call("--execute", "--confirm-device", "ABC", "--edl-serial", "EDL1", *extra)

    def test_the_preview_lists_every_write_and_contacts_nothing(self):
        code, out = self.call()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.commands, [])
        self.assertEqual(out.count("write-sector"), 4)
        self.assertIn(f"write-sector {SECTORS - 5} ", out)  # the backup GPT at the LUN's end
        self.assertIn("4 writes", out)

    def test_every_partition_goes_back_and_userdata_gets_zeros_at_its_start(self):
        code, out = self.execute()
        self.assertEqual(code, 0, out)
        disk = bytes(self.disk)
        self.assertEqual(disk[6 * SECTOR : 10 * SECTOR], b"A" * 4 * SECTOR)
        self.assertEqual(disk[(SECTORS - 5) * SECTOR :], b"B" * 5 * SECTOR)
        self.assertEqual(disk[10 * SECTOR : 50 * SECTOR], bytes(40 * SECTOR))
        self.assertEqual(disk[50 * SECTOR : (SECTORS - 5) * SECTOR], b"\xff" * 9 * SECTOR)  # outside any write
        words = [next(w for w in c if w in ("printgpt", "write-sector", "read-sector")) for c in self.commands]
        self.assertEqual(words, ["printgpt", *["write-sector", "read-sector"] * 4])  # each write read back

    def test_nothing_is_written_to_another_headset_a_stale_session_or_other_luns(self):
        for serial, storage, message in (
            ("Serial Number: EDL2", self.storage, "not the configured headset"),
            ("Detected device mode: Firehose", self.storage, "earlier session"),
            (self.serial, f'"total_blocks": {SECTORS * 2}\nUFS Current LUN Number: = 0x0', "128 sectors"),
            (self.serial, '"total_blocks": 8\nUFS Current LUN Number: = 0x1', "LUNs"),
        ):
            self.serial, self.storage, self.commands = serial, storage, []
            code, out = self.execute()
            self.assertEqual(code, 1, message)
            self.assertIn(message, out)
            self.assertEqual([c for c in self.commands if "write-sector" in c], [])

    def test_execution_needs_the_serial_edl_ng_and_a_work_folder_outside_the_backup(self):
        for extra, message in (
            (("--execute", "--confirm-device", "ABC"), "--edl-serial"),
            (("--execute", "--confirm-device", "XYZ", "--edl-serial", "EDL1"), "--confirm-device"),
        ):
            code, out = self.call(*extra)
            self.assertEqual(code, 1, message)
            self.assertIn(message, out)
        code, out = self.execute("--work", str(self.backup / "work"))
        self.assertEqual(code, 1)
        self.assertIn("outside the backup", out)
        code, out = self.execute("--edl-backend", "bkerler")
        self.assertEqual(code, 1)
        self.assertIn("edl-ng", out)
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
