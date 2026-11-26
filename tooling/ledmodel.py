# SPDX-License-Identifier: Unlicense
"""Nominal LED models of the PICO 4 controllers, from PICO OS's controller tracking library.

PICO OS tracks each controller by a ring of 20 infrared LEDs. Its tracking library
(`/system/lib64/libpico_controller_tracking.pxr.so` in the system image) carries a nominal
model of each ring as 20 points (x, y, z float32, metres) and hands the left one, then
the right one, to its pose predictor, together with each LED's unit normal (x, y, z
float64; the left normals are the right ones with y negated), which decides from where an
LED can be seen. The assets command (tooling/assets.py) reads both from the
stock firmware; the values are vendor data and stay out of the repository.

Each build of the library keeps them at its own addresses: LAYOUTS knows them by the library's
SHA-256. PICO OS 5.13.7 and 5.13.8 ship the same library; 5.13.3's holds the same values elsewhere,
loaded by its code the same way.

    python -m devices.phoenix.tooling.ledmodel LIBRARY OUTPUT.json
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

LEDS = 20
#: Plausibility: every LED within this distance of the model's origin (metres).
MIN_RADIUS_M, MAX_RADIUS_M = 0.005, 0.15
#: The left ring is the right one mirrored in y, point by point, within this (metres).
MIRROR_TOLERANCE_M = 0.005
#: The normals are stored as unit vectors, within this.
NORMAL_TOLERANCE = 1e-5
#: A point has three coordinates.
VECTOR = 3
#: ELF header values: e_ident's class and data encoding, and the program header type of a loadable segment.
ELFCLASS64, ELFDATA2LSB, PT_LOAD = 2, 1, 1
#: The command line: LIBRARY OUTPUT.json.
ARGUMENTS = ("LIBRARY", "OUTPUT.json")


class LedModelError(ValueError):
    """The library holds no plausible LED model."""


@dataclass(frozen=True)
class Layout:
    """Where one build of the library keeps the models: their virtual addresses and the right normals'."""

    builds: str
    models: dict[str, int]
    right_normals: int


#: The known builds of the library, by its SHA-256.
LAYOUTS = {
    "749b655313ac161ec0f8a7b081ec33bef8f3d5d04c271e9f11b066c87d30bf60": Layout(
        "PICO OS 5.13.7 and 5.13.8", {"left": 0xB47FF0, "right": 0xB480E0}, 0xB4CC20
    ),
    "10d9cd91acfac85630948289db99c7092169838853951ef2ee3ed4803d545791": Layout(
        "PICO OS 5.13.3", {"left": 0xB46340, "right": 0xB46430}, 0xB4AE90
    ),
}


def layout(elf: bytes) -> Layout:
    """Return the layout of this build of the library; refuse a build whose addresses are not known."""
    digest = hashlib.sha256(elf).hexdigest()
    try:
        return LAYOUTS[digest]
    except KeyError:
        known = ", ".join(layout.builds for layout in LAYOUTS.values())
        msg = f"this build of the library (SHA-256 {digest[:12]}) is not one whose LED models are known ({known})"
        raise LedModelError(msg) from None


def read_virtual(elf: bytes, address: int, size: int) -> bytes:
    """Bytes at a virtual address of a 64-bit little-endian ELF, from its file-backed PT_LOAD segments."""
    if elf[:4] != b"\x7fELF" or elf[4] != ELFCLASS64 or elf[5] != ELFDATA2LSB:
        msg = "not a 64-bit little-endian ELF file"
        raise LedModelError(msg)
    (phoff,) = struct.unpack_from("<Q", elf, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", elf, 0x36)
    for i in range(phnum):
        kind, _, offset, vaddr, _, filesz, _, _ = struct.unpack_from("<IIQQQQQQ", elf, phoff + i * phentsize)
        if kind == PT_LOAD and vaddr <= address and address + size <= vaddr + filesz:
            start = offset + address - vaddr
            return elf[start : start + size]
    msg = f"address {address:#x} is not in a file-backed segment"
    raise LedModelError(msg)


def models(elf: bytes, where: Layout | None = None) -> dict[str, list[list[float]]]:
    """Both nominal models, validated, at @p where (by default this build's layout)."""
    where = where or layout(elf)
    result = {}
    for side, address in where.models.items():
        values = struct.unpack(f"<{3 * LEDS}f", read_virtual(elf, address, 3 * LEDS * 4))
        result[side] = [list(values[3 * i : 3 * i + 3]) for i in range(LEDS)]
    validate(result)
    return result


def normals(
    elf: bytes, result: dict[str, list[list[float]]], where: Layout | None = None
) -> dict[str, list[list[float]]]:
    """Both sides' LED normals, checked against the models of `result`, at @p where (by default this build's)."""
    where = where or layout(elf)
    values = struct.unpack(f"<{3 * LEDS}d", read_virtual(elf, where.right_normals, 3 * LEDS * 8))
    right = [list(values[3 * i : 3 * i + 3]) for i in range(LEDS)]
    out = {"right": right, "left": [[x, -y, z] for x, y, z in right]}
    for side in ("left", "right"):
        if any(not all(math.isfinite(v) for v in n) or abs(math.hypot(*n) - 1.0) > NORMAL_TOLERANCE for n in out[side]):
            msg = f"{side}: the LED normals are not unit vectors"
            raise LedModelError(msg)
    # The LEDs face away from the ring's centre: most normals share the sign of y with their LED.
    centre_y = sum(p[1] for p in result["right"]) / LEDS
    agree = sum(1 for n, p in zip(right, result["right"]) if n[1] * (p[1] - centre_y) > 0)
    if agree < LEDS // 2:
        msg = "the LED normals do not belong to these models"
        raise LedModelError(msg)
    return out


def validate(result: dict[str, list[list[float]]]) -> None:
    """Refuse anything implausible: a different library version puts other data at these addresses."""
    for side in ("left", "right"):
        points = result.get(side) or []
        if len(points) != LEDS or any(len(p) != VECTOR or not all(math.isfinite(v) for v in p) for p in points):
            msg = f"{side}: expected {LEDS} finite points"
            raise LedModelError(msg)
        radii = [math.hypot(*p) for p in points]
        if min(radii) < MIN_RADIUS_M or max(radii) > MAX_RADIUS_M:
            msg = f"{side}: LEDs {min(radii):.3f}-{max(radii):.3f} m from the origin, not a controller ring"
            raise LedModelError(msg)
    worst = max(math.dist((x, -y, z), r) for (x, y, z), r in zip(result["left"], result["right"]))
    if worst > MIRROR_TOLERANCE_M:
        msg = f"the left model is not the right one mirrored in y ({worst * 1000:.1f} mm off)"
        raise LedModelError(msg)


def to_json(result: dict[str, list[list[float]]], normal: dict[str, list[list[float]]], library: str) -> str:
    """Render the models and normals as the extracted JSON file."""
    return (
        json.dumps(
            {
                "schema": 1,
                "source": library,
                "units": "m",
                "frame": "the nominal model frame of PICO OS's controller tracking",
                "left": result["left"],
                "right": result["right"],
                "normals": normal,
            },
            indent=1,
        )
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    """Run the extractor's command line."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != len(ARGUMENTS):
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    library, output = Path(args[0]), Path(args[1])
    try:
        data = library.read_bytes()
        result = models(data)
        normal = normals(data, result)
    except (OSError, LedModelError) as exc:
        print(f"{library}: {exc}", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(to_json(result, normal, library.name).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
