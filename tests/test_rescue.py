# SPDX-License-Identifier: Unlicense
"""tooling/rescue.py: PICO OS back without a backup, from fakes of edl-ng, of the packages and of their pins."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from devices.phoenix.tooling import rescue
from overpressure.api.v1 import lp, qualcomm

#: One partition table as edl-ng's printgpt prints it: LUN 0 and LUN 4, with the parts the rescue needs.
ROWS = {
    0: [("super", 1000, 1000 + (64 << 8) - 1), ("recovery", 600, 699), ("vbmeta_system", 700, 700),
        ("userdata", 20000, 90000), ("metadata", 900, 903), ("misc", 950, 950)],
    1: [("xbl", 10, 30), ("xbl_config", 40, 45)],
    4: [("abl", 10, 19), ("boot", 100, 199), ("dtbo", 200, 209), ("vbmeta", 210, 211), ("tz", 300, 340),
        ("aop", 350, 360), ("hyp", 370, 380)],
}  # fmt: skip


def printgpt(serial: str = "ABCDEF12") -> str:
    """Return printgpt's output for ROWS, with the serial a first session names."""
    lines = [f"[INFO]  Serial Number: {serial}"] if serial else []
    for lun, rows in ROWS.items():
        lines += [f"[INFO]  --- GPT Header LUN {lun} ---", f"[INFO]  --- Partitions LUN {lun} ---"]
        for name, first, last in rows:
            lines += [
                f"[INFO]    Name: {name}",
                "[INFO]      Type: x",
                f"[INFO]      LBA:  {first}-{last} (Size: 1 MiB)",
            ]
    return "\n".join(lines) + "\n"


def pin(data: bytes) -> dict:
    """Return @p data's pin."""
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def chain_of(build: str, *, ddr5: bool = False) -> dict[str, bytes]:
    """Return a boot chain whose members carry @p build's name (DDR5 on request), padded as partitions are."""
    return {
        part: f"{build}:{members[1 if ddr5 and len(members) > 1 else 0]}".encode() + bytes(64)
        for part, members in rescue.BOOT_CHAIN.items()
    }


def pinned(build: str) -> dict[str, dict]:
    """Return @p build's pins of its boot chain members."""
    return {name: pin(f"{build}:{name}".encode()) for members in rescue.BOOT_CHAIN.values() for name in members}


BUILDS = {name: pinned(name) for name in ("5.13.3-global-seko", "5.13.7-global-seko", "5.13.7-cn-seko")}


class FakeEdl:
    """edl-ng's sessions: printgpt and read-part of the boot chain, from the fakes above."""

    def __init__(self, chain: dict[str, bytes], serial: str = "ABCDEF12") -> None:
        self.chain, self.serial, self.ran = chain, serial, []

    def __call__(self, argv: list[str], _timeout: float, log: Path | None = None) -> str:
        self.ran.append(argv)
        if "printgpt" in argv:
            output = printgpt(self.serial)
            self.serial = ""  # later sessions name none
        elif "read-sector" in argv:
            first, sectors = int(argv[argv.index("read-sector") + 1]), int(argv[argv.index("read-sector") + 2])
            lun = int(argv[argv.index("--lun") + 1])
            name = next(n for n, a, _b in ROWS[lun] if a == first)
            data = self.chain[name] if self.chain is not None else b""
            Path(argv[argv.index("read-sector") + 3]).write_bytes(data.ljust(sectors * rescue.SECTOR, b"\0"))
            output = "Successfully read"
        else:
            output = ""
        if log is not None:
            log.write_text(output, encoding="utf-8")
        return output


class LookTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name)

    def edl(self, fake: FakeEdl) -> rescue.Edl:
        return rescue.Edl("edl-ng", "loader.elf", fake)

    def test_the_partition_table_by_name(self):
        table = rescue.partition_table(printgpt())
        self.assertEqual(table["super"], rescue.Partition(0, 1000, 1000 + (64 << 8) - 1))
        self.assertEqual(table["super"].size, 64 << 20)
        self.assertEqual(table["abl"].lun, 4)

    def test_the_boot_chain_names_the_build(self):
        self.assertEqual(rescue.identify(chain_of("5.13.7-cn-seko"), BUILDS), "5.13.7-cn-seko")
        with self.assertRaisesRegex(rescue.RescueError, "Pro or Enterprise"):
            rescue.identify(chain_of("5.13.7-global-seko", ddr5=True), BUILDS)
        with self.assertRaisesRegex(rescue.RescueError, "no PICO OS build"):
            rescue.identify(chain_of("5.14.0-global-seko"), BUILDS)
        mixed = {**chain_of("5.13.7-global-seko"), "tz": chain_of("5.13.3-global-seko")["tz"]}
        with self.assertRaisesRegex(rescue.RescueError, "no PICO OS build"):
            rescue.identify(mixed, BUILDS)  # one build's boot chain, or none

    def test_the_look_writes_nothing_and_records_the_headset(self):
        fake = FakeEdl(chain_of("5.13.7-global-seko"))
        found = rescue.look(self.edl(fake), self.work, "abcdef12", BUILDS)
        self.assertEqual((found["build"], found["serial"]), ("5.13.7-global-seko", "ABCDEF12"))
        self.assertEqual(json.loads((self.work / "look.json").read_text(encoding="utf-8")), found)
        words = {word for argv in fake.ran for word in argv}
        self.assertIn("printgpt", words)
        self.assertFalse(any(word in argv for argv in fake.ran for word in ("write-part", "write-sector", "reset")))

    def test_a_used_loader_is_freed_once_then_named_by_its_fresh_session(self):
        fake = FakeEdl(chain_of("5.13.7-global-seko"), serial="")
        freed = []

        def free() -> None:
            freed.append(True)
            fake.serial = "ABCDEF12"  # the headset is back in a fresh EDL session

        found = rescue.look(self.edl(fake), self.work, "", BUILDS, free)
        self.assertEqual((freed, found["serial"]), ([True], "ABCDEF12"))
        fake.serial = ""
        with self.assertRaisesRegex(rescue.RescueError, "names no serial"):
            rescue.look(self.edl(fake), self.work, "", BUILDS, lambda: None)  # freed, still none: by hand

    def test_another_headset_or_an_earlier_sessions_loader_is_refused(self):
        with self.assertRaisesRegex(rescue.RescueError, "not the one .env names"):
            rescue.look(self.edl(FakeEdl(chain_of("5.13.7-global-seko"))), self.work, "OTHER", BUILDS)
        with self.assertRaisesRegex(rescue.RescueError, "names no serial"):
            rescue.look(self.edl(FakeEdl(chain_of("5.13.7-global-seko"), serial="")), self.work, "", BUILDS)


class PinTest(unittest.TestCase):
    def test_the_pins_go_into_firmware_json_beside_its_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "firmware.json"
            path.write_text(json.dumps({"_comment": ["x"], "builds": {"b": {"url": "u", "zip_size": 1}}}, indent=2))
            rescue.write_pins(path, "b", {"boot.img": pin(b"boot")})
            rescue.write_pins(path, "b", {"tz.mbn": pin(b"tz")})
            text = path.read_text(encoding="utf-8")
        catalog = json.loads(text)
        self.assertEqual(catalog["builds"]["b"]["url"], "u")
        self.assertEqual(set(catalog["builds"]["b"]["rescue"]), {"boot.img", "tz.mbn"})
        self.assertTrue(text.endswith("}\n"))
        self.assertIn('\n  "builds"', text)  # firmware.json's own indent

    def test_every_member_the_rescue_reads_is_pinned(self):
        members = rescue.needed("add_group g 0\nadd system g\nresize system 4096\n")
        self.assertIn("firmware-update/xbl_ddr5.elf", members)  # the refusal needs the DDR5 one too
        self.assertIn("system.new.dat.br", members)
        self.assertIn(rescue.OP_LIST, members)
        self.assertTrue(set(rescue.IMAGES.values()) <= set(members))


class PlanTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name)
        self.members: list[str] = []  # the package's members, as catalog() pins them
        self.found = {
            "build": "5.13.7-global-seko",
            "partitions": {n: [lun, a, b] for lun, rows in ROWS.items() for n, a, b in rows},
        }

    def package(self) -> None:
        """Stand in for ota.extract: each image a sector, the dynamic partitions three, the members downloaded."""
        folder = self.work / "package"
        folder.mkdir(exist_ok=True)
        for member in self.members:
            path = folder / "download" / member
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(member.encode())
        for part in rescue.IMAGES:
            (folder / f"{part}.img").write_bytes(bytes(rescue.SECTOR))
        for name in ("first", "second"):
            (folder / f"{name}.img").write_bytes(bytes(3 * rescue.SECTOR))

    def catalog(self, op_list: str) -> dict:
        """Return a catalog whose one build pins @p op_list and every member as package() writes it."""
        self.members = [m for m in rescue.needed(op_list) if m != rescue.OP_LIST]
        pins = {m: pin(m.encode()) for m in self.members}
        return {"5.13.7-global-seko": {"url": "x", "rescue": {**pins, rescue.OP_LIST: pin(op_list.encode())}}}

    def test_prepare_lays_super_out_at_the_disks_alignment_and_checks_the_sizes(self):
        op_list = "remove_all_groups\nadd_group main 67108864\nadd first main\nadd second main\n"
        op_list += "resize first 8388608\nresize second 4194304\n"
        catalog = self.catalog(op_list)
        with (
            mock.patch.object(rescue, "fastest", return_value="x"),
            mock.patch.object(rescue, "_op_list", return_value=op_list),
            mock.patch.object(rescue.ota, "extract", side_effect=lambda *_a, **_k: self.package()),
        ):
            prepared = rescue.prepare(self.found, self.work, catalog, log=lambda _m: None)
        region = (self.work / "package" / "super-metadata.bin").read_bytes()
        extents = lp.partitions(io.BytesIO(region))
        self.assertEqual({n: [e[0].start, e[0].size] for n, e in extents.items()}, prepared["extents"])
        for start, _size in prepared["extents"].values():  # on the disk's 512 KiB boundaries
            self.assertEqual((1000 * rescue.SECTOR + start) % rescue.ALIGNMENT, 0)

    def test_a_member_that_is_not_its_pin_or_a_build_without_pins_is_refused(self):
        op_list = "add_group main 0\nadd first main\nresize first 4096\n"
        catalog = self.catalog(op_list)

        def changed(*_a: object, **_k: object) -> None:
            self.package()
            (self.work / "package" / "download" / "boot.img").write_bytes(b"another boot image")

        with (
            mock.patch.object(rescue, "fastest", return_value="x"),
            mock.patch.object(rescue, "_op_list", return_value=op_list),
            mock.patch.object(rescue.ota, "extract", side_effect=changed),
            self.assertRaisesRegex(rescue.RescueError, "boot.img is not the file firmware.json pins"),
        ):
            rescue.prepare(self.found, self.work, catalog, log=lambda _m: None)
        bare = {"5.13.7-global-seko": {"url": "x", "rescue": {rescue.OP_LIST: pin(op_list.encode())}}}
        with (
            mock.patch.object(rescue, "fastest", return_value="x"),
            mock.patch.object(rescue, "_op_list", return_value=op_list),
            mock.patch.object(rescue.ota, "extract") as extract,
            self.assertRaisesRegex(rescue.RescueError, "not pinned"),
        ):
            rescue.prepare(self.found, self.work, bare, log=lambda _m: None)
        extract.assert_not_called()  # nothing downloaded for a build without pins
        with (
            mock.patch.object(rescue, "fastest", return_value="x"),
            mock.patch.object(rescue, "_op_list", return_value=op_list + "resize first 8192\n"),
            self.assertRaisesRegex(rescue.RescueError, "dynamic_partitions_op_list is not the file"),
        ):
            rescue.prepare(self.found, self.work, catalog, log=lambda _m: None)

    def test_an_image_larger_than_its_partition_is_refused(self):
        op_list = "add_group main 0\nadd first main\nresize first 4096\n"
        catalog = self.catalog(op_list)

        def big(*_a: object, **_k: object) -> None:
            self.package()
            (self.work / "package" / "first.img").write_bytes(bytes(2 * rescue.SECTOR))

        with (
            mock.patch.object(rescue, "fastest", return_value="x"),
            mock.patch.object(rescue, "_op_list", return_value=op_list),
            mock.patch.object(rescue.ota, "extract", side_effect=big),
            self.assertRaisesRegex(rescue.RescueError, "does not fit"),
        ):
            rescue.prepare(self.found, self.work, catalog, log=lambda _m: None)

    def test_the_writes_cover_super_the_images_and_the_wipes_never_the_boot_chain(self):
        self.package()
        (self.work / "package" / "super-metadata.bin").write_bytes(bytes(1 << 20))
        prepared = {"extents": {"first": [3 << 20, 3 * rescue.SECTOR], "second": [8 << 20, 3 * rescue.SECTOR]}}
        writes = rescue.plan(self.found, prepared, self.work / "package")
        by_label = {w.label: w for w in writes}
        self.assertEqual(by_label["super's metadata"].start, 1000)
        self.assertEqual(by_label["first in super"].start, 1000 + (3 << 20) // rescue.SECTOR)
        self.assertEqual(by_label["boot"].start, 100)
        self.assertEqual(by_label["boot"].size, rescue.SECTOR)
        self.assertIsNone(by_label["userdata"].image)
        self.assertEqual(by_label["userdata"].size, qualcomm.WIPE_BYTES)
        self.assertEqual(by_label["misc"].size, rescue.SECTOR)
        self.assertFalse(set(rescue.BOOT_CHAIN) & set(by_label))
        self.assertFalse({"persist", "modemst1", "modemst2", "fsg", "devinfo"} & set(by_label))

    def test_the_write_checks_it_is_still_the_same_headset(self):
        chain = chain_of("5.13.7-global-seko")
        (self.work / "xbl.bin").write_bytes(chain["xbl"].ljust(21 * rescue.SECTOR, bytes(1)))  # its 21 sectors, whole
        edl = rescue.Edl("edl-ng", "loader.elf", FakeEdl(chain, serial=""))
        rescue.same_headset(self.found, edl, self.work)
        edl = rescue.Edl("edl-ng", "loader.elf", FakeEdl(chain_of("5.13.3-global-seko"), serial=""))
        with self.assertRaisesRegex(rescue.RescueError, "another XBL"):
            rescue.same_headset(self.found, edl, self.work)


if __name__ == "__main__":
    unittest.main()
