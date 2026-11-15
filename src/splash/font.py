# SPDX-License-Identifier: BSL-1.0
"""Write pico4-splash-font.h: the outlines of the characters pico4-splash draws, from Inter.

    python src/splash/font.py InterVariable.ttf src/splash/pico4-splash-font.h

The font is Inter 4.1's InterVariable.ttf (github.com/rsms/inter, docs/font-files/ at the commit
below; SIL Open Font License 1.1, OFL.txt here). A variable TrueType font keeps its default instance,
Regular, in its glyf table: those quadratic outlines are what this reads, with the standard library
only. Each outline becomes moves, lines and quadratic curves in font units (y up), with the glyph's
advance; pico4-splash fills them with its own anti-aliased filler, no font engine on the headset.
"""
# ruff: noqa: INP001, T201  # a standalone generator next to the C sources it writes for: no package; print is its output

from __future__ import annotations

import hashlib
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: The font, pinned: Inter's repository at its v4.1 tag, and the file's SHA-256.
SOURCE = "https://github.com/rsms/inter/blob/e3a3d4c57d5ecc01453a575621882a384c1995a3/docs/font-files/InterVariable.ttf"
SHA256 = "4989b125924991b90d05b2d16e0e388c48f7d5bb8b30539bbf9c755278d0ccaf"
#: What pico4-splash writes: the boot indication's name and charger mode's level.
CHARS = "overpressure0123456789%"
#: The path's operations in the header.
MOVE, LINE, QUAD, CLOSE = 0, 1, 2, 3
#: Composite glyph flags (the glyf table's specification).
WORDS, XY, SCALE, MORE, XY_SCALE, TWO_BY_TWO = 0x1, 0x2, 0x8, 0x20, 0x40, 0x80
ON_CURVE, X_SHORT, Y_SHORT, REPEAT, X_SAME, Y_SAME = 0x1, 0x2, 0x4, 0x8, 0x10, 0x20


class FontError(Exception):
    """The font file is not the pinned one, or not a TrueType font this reads."""


@dataclass
class Glyph:
    """One character's contours (lists of (x, y, on-curve) points) and its advance, in font units."""

    advance: int
    contours: list[list[tuple[float, float, bool]]] = field(default_factory=list)


class Font:
    """The tables of a TrueType font that outlines need: head, maxp, hhea, hmtx, cmap, loca and glyf."""

    def __init__(self, data: bytes) -> None:
        """Index @p data's tables."""
        self.data = data
        count = struct.unpack_from(">H", data, 4)[0]
        self.tables = {}
        for i in range(count):
            tag, _, offset, length = struct.unpack_from(">4sIII", data, 12 + 16 * i)
            self.tables[tag.decode("latin-1")] = (offset, length)
        missing = {"head", "maxp", "hhea", "hmtx", "cmap", "loca", "glyf", "OS/2"} - set(self.tables)
        if missing:
            msg = f"not a TrueType font with outlines: no {', '.join(sorted(missing))}"
            raise FontError(msg)
        head = self.tables["head"][0]
        self.units = struct.unpack_from(">H", data, head + 18)[0]
        self.long_loca = struct.unpack_from(">h", data, head + 50)[0] == 1
        self.glyphs = struct.unpack_from(">H", data, self.tables["maxp"][0] + 4)[0]
        self.metrics = struct.unpack_from(">H", data, self.tables["hhea"][0] + 34)[0]
        os2 = self.tables["OS/2"][0]
        self.x_height, self.cap_height = struct.unpack_from(">hh", data, os2 + 86)
        self.cmap = self._cmap()

    def _cmap(self) -> dict[int, int]:
        """Return the Unicode BMP character map (format 4 of platform 3, encoding 1, or platform 0)."""
        base = self.tables["cmap"][0]
        count = struct.unpack_from(">H", self.data, base + 2)[0]
        for i in range(count):
            platform, encoding, offset = struct.unpack_from(">HHI", self.data, base + 4 + 8 * i)
            table = base + offset
            if (platform, encoding) in ((3, 1), (0, 3)) and struct.unpack_from(">H", self.data, table)[0] == 4:  # noqa: PLR2004  # format 4
                return self._format4(table)
        msg = "no Unicode format-4 character map"
        raise FontError(msg)

    def _format4(self, table: int) -> dict[int, int]:
        """Return the character map of the format-4 subtable at @p table."""
        segments = struct.unpack_from(">H", self.data, table + 6)[0] // 2
        ends = struct.unpack_from(f">{segments}H", self.data, table + 14)
        starts_at = table + 16 + 2 * segments
        starts = struct.unpack_from(f">{segments}H", self.data, starts_at)
        deltas = struct.unpack_from(f">{segments}h", self.data, starts_at + 2 * segments)
        ranges_at = starts_at + 4 * segments
        ranges = struct.unpack_from(f">{segments}H", self.data, ranges_at)
        found = {}
        for code in map(ord, CHARS):
            for i in range(segments):
                if starts[i] <= code <= ends[i]:
                    if ranges[i] == 0:
                        found[code] = (code + deltas[i]) & 0xFFFF
                    else:
                        at = ranges_at + 2 * i + ranges[i] + 2 * (code - starts[i])
                        index = struct.unpack_from(">H", self.data, at)[0]
                        found[code] = (index + deltas[i]) & 0xFFFF if index else 0
                    break
        return found

    def _location(self, index: int) -> tuple[int, int]:
        """Return the glyf offset and length of glyph @p index."""
        loca = self.tables["loca"][0]
        if self.long_loca:
            start, end = struct.unpack_from(">II", self.data, loca + 4 * index)
        else:
            start, end = (2 * v for v in struct.unpack_from(">HH", self.data, loca + 2 * index))
        return self.tables["glyf"][0] + start, end - start

    def advance(self, index: int) -> int:
        """Return glyph @p index's advance width."""
        row = min(index, self.metrics - 1)
        return struct.unpack_from(">H", self.data, self.tables["hmtx"][0] + 4 * row)[0]

    def contours(self, index: int) -> list[list[tuple[float, float, bool]]]:
        """Return glyph @p index's contours, a composite's components placed and scaled."""
        at, length = self._location(index)
        if length == 0:
            return []
        count = struct.unpack_from(">h", self.data, at)[0]
        return self._simple(at, count) if count >= 0 else self._composite(at)

    def _simple(self, at: int, count: int) -> list[list[tuple[float, float, bool]]]:
        ends = struct.unpack_from(f">{count}H", self.data, at + 10)
        points = ends[-1] + 1 if ends else 0
        p = at + 10 + 2 * count
        p += 2 + struct.unpack_from(">H", self.data, p)[0]  # past the instructions
        flags: list[int] = []
        while len(flags) < points:
            flag = self.data[p]
            p += 1
            flags.append(flag)
            if flag & REPEAT:
                flags += [flag] * self.data[p]
                p += 1
        xs, p = self._coordinates(flags, p, X_SHORT, X_SAME)
        ys, _ = self._coordinates(flags, p, Y_SHORT, Y_SAME)
        out, start = [], 0
        for end in ends:
            out.append([(xs[i], ys[i], bool(flags[i] & ON_CURVE)) for i in range(start, end + 1)])
            start = end + 1
        return out

    def _coordinates(self, flags: list[int], p: int, short: int, same: int) -> tuple[list[float], int]:
        values, value = [], 0
        for flag in flags:
            if flag & short:
                delta = self.data[p]
                p += 1
                value += delta if flag & same else -delta
            elif not flag & same:
                value += struct.unpack_from(">h", self.data, p)[0]
                p += 2
            values.append(float(value))
        return values, p

    def _composite(self, at: int) -> list[list[tuple[float, float, bool]]]:
        out, p, flags = [], at + 10, MORE
        while flags & MORE:
            flags, index = struct.unpack_from(">HH", self.data, p)
            p += 4
            if flags & WORDS:
                dx, dy = struct.unpack_from(">hh", self.data, p)
                p += 4
            else:
                dx, dy = struct.unpack_from(">bb", self.data, p)
                p += 2
            if not flags & XY:
                msg = "composite glyphs placed by point numbers are not supported"
                raise FontError(msg)
            a = d = 1.0
            b = c = 0.0
            if flags & SCALE:
                a = d = struct.unpack_from(">h", self.data, p)[0] / 16384
                p += 2
            elif flags & XY_SCALE:
                a, d = (v / 16384 for v in struct.unpack_from(">hh", self.data, p))
                p += 4
            elif flags & TWO_BY_TWO:
                a, b, c, d = (v / 16384 for v in struct.unpack_from(">hhhh", self.data, p))
                p += 8
            out += [
                [(a * x + c * y + dx, b * x + d * y + dy, on) for x, y, on in contour]
                for contour in self.contours(index)
            ]
        return out

    def glyph(self, char: str) -> Glyph:
        """Return @p char's glyph."""
        index = self.cmap.get(ord(char), 0)
        if not index:
            msg = f"the font has no {char!r}"
            raise FontError(msg)
        return Glyph(self.advance(index), self.contours(index))


def path(contours: list[list[tuple[float, float, bool]]]) -> list[int]:
    """Return @p contours as the header's operations: MOVE x y, LINE x y, QUAD cx cy x y, CLOSE.

    A TrueType contour's two off-curve points in a row have an implied on-curve point halfway.
    """
    out: list[int] = []
    for contour in contours:
        first = next((i for i, (_, _, on) in enumerate(contour) if on), None)
        if first is None:  # all off-curve: start halfway between the first two
            x0, y0 = (contour[0][0] + contour[1][0]) / 2, (contour[0][1] + contour[1][1]) / 2
            points, start = [*contour[1:], contour[0]], (x0, y0)
        else:
            points = contour[first + 1 :] + contour[: first + 1]
            start = contour[first][:2]
        out += [MOVE, round(start[0]), round(start[1])]
        control = None
        for x, y, on in points:
            if on:
                out += (
                    [QUAD, round(control[0]), round(control[1]), round(x), round(y)]
                    if control
                    else [LINE, round(x), round(y)]
                )
                control = None
            elif control:
                mx, my = (control[0] + x) / 2, (control[1] + y) / 2
                out += [QUAD, round(control[0]), round(control[1]), round(mx), round(my)]
                control = (x, y)
            else:
                control = (x, y)
        if control:
            out += [QUAD, round(control[0]), round(control[1]), round(start[0]), round(start[1])]
        out.append(CLOSE)
    return out


def header(font: Font) -> str:
    """Return pico4-splash-font.h's text for @p font."""
    ops: list[int] = []
    rows = []
    for char in dict.fromkeys(CHARS):
        glyph = font.glyph(char)
        start = len(ops)
        ops += path(glyph.contours)
        rows.append(f"\t{{'{char}', {glyph.advance}, {start}, {len(ops) - start}}},")
    body = "\n".join(f"\t{line}" for line in _split(",".join(str(v) for v in ops)))
    return f"""// SPDX-License-Identifier: OFL-1.1
/*
 * Generated by font.py from Inter 4.1 (InterVariable.ttf, its default instance: Regular).
 * Copyright (c) 2016 The Inter Project Authors (https://github.com/rsms/inter); licensed under the
 * SIL Open Font License, Version 1.1 (OFL.txt). Do not edit: run font.py again.
 *
 * Each glyph's outline in font units (y up): FONT_MOVE x y, FONT_LINE x y, FONT_QUAD cx cy x y, FONT_CLOSE.
 */

#ifndef PICO4_SPLASH_FONT_H
#define PICO4_SPLASH_FONT_H

#define FONT_UNITS {font.units}
#define FONT_X_HEIGHT {font.x_height}
#define FONT_CAP_HEIGHT {font.cap_height}
enum {{ FONT_MOVE = {MOVE}, FONT_LINE = {LINE}, FONT_QUAD = {QUAD}, FONT_CLOSE = {CLOSE} }};

struct font_glyph {{
	char ch;
	short advance;
	unsigned short start, count; /* into font_ops */
}};

static const short font_ops[] = {{
{body}
}};

static const struct font_glyph font_glyphs[] = {{
{chr(10).join(rows)}
}};

#endif
"""


def _split(numbers: str, width: int = 104) -> list[str]:
    """Return the comma-separated @p numbers cut into lines of at most about @p width characters."""
    out, line = [], ""
    for number in numbers.split(","):
        if len(line) + len(number) + 1 > width:
            out.append(line)
            line = ""
        line += number + ","
    return [*out, line] if line else out


def main(argv: list[str]) -> int:
    """Write the header from the pinned font."""
    if len(argv) != 2:  # noqa: PLR2004  # FONT and OUTPUT
        print("usage: font.py InterVariable.ttf pico4-splash-font.h", file=sys.stderr)
        return 2
    data = Path(argv[0]).read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        print(f"error: {argv[0]} is not the pinned InterVariable.ttf ({SOURCE})", file=sys.stderr)
        return 1
    try:
        text = header(Font(data))
    except FontError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    Path(argv[1]).write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
