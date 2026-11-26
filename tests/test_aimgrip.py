# SPDX-License-Identifier: Unlicense
"""The controllers' aim and grip transforms, read from PICO OS's library (synthetic data here)."""

from __future__ import annotations

import hashlib
import json
import math
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from devices.phoenix.tooling import aimgrip

BASE = 0x70000  # the synthetic library's one loadable segment starts here


def block(
    angle_deg: float,
    axis: tuple[float, float, float] = (1.0, 0.0, 0.0),
    position: tuple[float, float, float] = (0.01, 0.02, 0.03),
) -> list[float]:
    """Return a synthetic transform: a position and a rotation about `axis` (not the real values)."""
    half = math.radians(angle_deg) / 2
    n = math.sqrt(sum(a * a for a in axis))
    return list(position) + [math.cos(half)] + [math.sin(half) * a / n for a in axis]


#: The real builds' entries (builds, address); each synthetic library places its doubles at one of them.
BUILDS = list(aimgrip.DEFAULTS.values())


def library(
    values: list[float] | None = None, *, mapped: bool = True, build: tuple[str, int] = BUILDS[0], known: bool = True
) -> bytes:
    """Build a minimal ELF64 with one PT_LOAD segment holding 14 doubles at @p build's address.

    With @p known it is registered as that build (by its SHA-256, in the test's copy of DEFAULTS).
    """
    values = values if values is not None else block(20) + block(-10, position=(-0.01, 0.02, 0.03))
    data_offset, size = 0x1000, 0x10000
    data = bytearray(size)
    struct.pack_into(f"<{len(values)}d", data, build[1] - BASE, *values)
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", header, 0x20, 64)
    struct.pack_into("<HH", header, 0x36, 56, 1)
    vaddr = BASE if mapped else 0x200000
    phdr = struct.pack("<IIQQQQQQ", 1, 4, data_offset, vaddr, vaddr, size, size, 0x1000)
    image = bytearray(data_offset) + data
    image[:64] = header
    image[64 : 64 + len(phdr)] = phdr
    if known:
        aimgrip.DEFAULTS[hashlib.sha256(image).hexdigest()] = build
    return bytes(image)


class AimGripTests(unittest.TestCase):
    def setUp(self):
        defaults = patch.dict(aimgrip.DEFAULTS)  # the synthetic libraries' entries go when the test ends
        defaults.start()
        self.addCleanup(defaults.stop)

    def test_reads_the_left_then_the_right_block_where_each_build_keeps_them(self):
        want = {"left": block(20), "right": block(-10, position=(-0.01, 0.02, 0.03))}
        for build in BUILDS:
            with self.subTest(build[0]):
                result = aimgrip.transforms(library(build=build))
                for hand in aimgrip.HANDS:
                    self.assertEqual(result[hand]["position"], want[hand][:3])
                    self.assertEqual(result[hand]["orientation"], want[hand][3:])

    def test_refuses_a_build_whose_address_is_not_known(self):
        unknown = library(known=False)
        with self.assertRaisesRegex(aimgrip.AimGripError, "not one whose transforms are known .*5.13.3"):
            aimgrip.transforms(unknown)
        self.assertEqual(aimgrip.transforms(unknown, BUILDS[0][1])["left"]["position"], block(20)[:3])

    def test_refuses_data_that_is_not_a_transform(self):
        cases: dict[str, dict[str, Any]] = {
            "unmapped": {"mapped": False},
            "not a unit quaternion": {"values": [*block(20)[:3], 1, 1, 0, 0, *block(20)]},
            "too far": {"values": block(20, position=(0.0, 0.0, 0.25)) + block(20)},
            "not finite": {"values": [math.nan, *block(20)[1:], *block(20)]},
            "zeros": {"values": [0.0] * 14},
        }
        for name, kwargs in cases.items():
            with self.subTest(name), self.assertRaises(aimgrip.AimGripError):
                aimgrip.transforms(library(**kwargs))
        with self.assertRaises(aimgrip.AimGripError):
            aimgrip.transforms(b"not an elf file at all" * 4)

    def test_command_writes_the_transforms(self):
        with tempfile.TemporaryDirectory() as temp:
            lib, out = Path(temp) / "libpvrtrackingservice.so", Path(temp) / "controllers/aim-to-grip.json"
            lib.write_bytes(library())
            self.assertEqual(aimgrip.main([str(lib), str(out)]), 0)
            value = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual((value["schema"], value["units"], value["source"]), (1, "m", lib.name))
            self.assertEqual(value["left"]["orientation"], block(20)[3:])
            self.assertEqual(len(value["right"]["position"]), 3)
            lib.write_bytes(library(mapped=False))
            self.assertEqual(aimgrip.main([str(lib), str(Path(temp) / "other.json")]), 1)


if __name__ == "__main__":
    unittest.main()
