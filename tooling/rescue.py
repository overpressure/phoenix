# SPDX-License-Identifier: Unlicense
"""PICO OS back on a headset without a backup, from PICO's own public downloads, over EDL.

    python -m overpressure rescue look [--work DIR]      # read-only: which PICO 4 and which PICO OS build
    python -m overpressure rescue prepare [--work DIR]   # that build's package, its images, a new super's layout
    python -m overpressure rescue write [--work DIR] [--execute]   # each write read back; a preview without it
    python -m overpressure rescue pin [BUILD...] [--small]          # firmware.json's pins from PICO's packages

The boot ROM's EDL mode always answers, and PICO's public PICO Neo 3 package has the loader. What our install
never writes tells the headset's build: its boot chain (XBL and its configuration, TrustZone, AOP, the hypervisor)
is byte for byte one build's members, every build's signed anew. firmware.json pins, per build, the size and
SHA-256 of each member the rescue reads or writes (hashes, not PICO's files, so they can be published): the look
names the build by them without a download, and every downloaded member must match its pin before anything is
rebuilt or written. The DDR4 XBL is the original PICO 4's; the PICO 4 Pro's and Enterprise's DDR5 one is refused,
and so is a boot chain no pinned build has. The same build goes back, so the system fits the firmware it starts
on, and nothing of the boot chain is written: the ABL, boot, recovery, dtbo, both vbmeta and super, which is laid
out anew from the package's dynamic_partitions_op_list (lp.build) with each logical partition at its extent;
userdata and metadata get zeros at their start and misc is cleared, as the restore does. The headset's own
partitions (persist, the modem's, devinfo, keys) are never written: no package has them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from overpressure.api.v1 import REPO, lp, ota, payload, qualcomm
from overpressure.api.v1.process import run

from . import devloop, localconfig
from .image import CATALOG

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Where the rescue keeps its look, the package and its images (git-ignored out/).
WORK = REPO / "out" / "rescue"
#: The UFS sector edl-ng's partition tables count in.
SECTOR = 4096
#: The boot chain our install never writes, partition -> its members (the original PICO 4's first): its build.
BOOT_CHAIN: dict[str, tuple[str, ...]] = {
    "xbl": ("firmware-update/xbl.elf", "firmware-update/xbl_ddr5.elf"),
    "xbl_config": ("firmware-update/xbl_config.elf", "firmware-update/xbl_config_ddr5.elf"),
    "tz": ("firmware-update/tz.mbn",),
    "aop": ("firmware-update/aop.mbn",),
    "hyp": ("firmware-update/hyp.mbn",),
}
#: What goes back as it is, partition -> the package's member.
IMAGES: dict[str, str] = {
    "abl": "firmware-update/abl.elf",
    "boot": "boot.img",
    "recovery": "recovery.img",
    "dtbo": "firmware-update/dtbo.img",
    "vbmeta": "firmware-update/vbmeta.img",
    "vbmeta_system": "firmware-update/vbmeta_system.img",
}
OP_LIST = "dynamic_partitions_op_list"
#: super's alignment on the disk, as the PICO 4's stock super metadata records it.
ALIGNMENT = 512 << 10
#: Cleared: the bootloader's message, so that PICO OS starts and not its recovery.
CLEARED = ("misc",)
#: The headset is refused, in the owner's words.
PRO = "This is a PICO 4 Pro or Enterprise (its boot chain is the DDR5 one): only the original PICO 4 is supported."
UNKNOWN_BUILD = (
    "The headset's boot chain is no PICO OS build the installer knows (firmware.json's pins), so it cannot tell "
    "which system fits it. Nothing was written."
)
NOT_FRESH = (
    "The headset in EDL mode runs an earlier session's loader and names no serial: nothing was written. Hold the "
    "power button for about 10 s, then hold both volume buttons while plugging the cable in, and look again."
)


class RescueError(RuntimeError):
    """The rescue stops: why, in plain words; nothing was written unless it says so."""


@dataclass(frozen=True)
class Partition:
    """A partition of the headset's GPT as edl-ng's printgpt shows it."""

    lun: int
    first: int  #: its first sector (SECTOR bytes)
    last: int

    @property
    def size(self) -> int:
        """Return its size in bytes."""
        return (self.last - self.first + 1) * SECTOR


def partition_table(output: str) -> dict[str, Partition]:
    """Return each partition edl-ng's printgpt lists, by name: its LUN and sectors (the first of a name wins)."""
    found: dict[str, Partition] = {}
    lun, name = None, ""
    for line in output.splitlines():
        header = re.search(r"---\s*Partitions\s+LUN\s+(\d+)\s*---", line)
        if header:
            lun = int(header.group(1))
            continue
        named = re.search(r"\bName:\s*(\S+)\s*$", line)
        if named:
            name = named.group(1)
            continue
        lba = re.search(r"\bLBA:\s*(\d+)-(\d+)", line)
        if lba and name and lun is not None:
            found.setdefault(name, Partition(lun, int(lba.group(1)), int(lba.group(2))))
            name = ""
    return found


#: A member's pin: its size and SHA-256 (firmware.json's builds' "rescue").
Pin = dict


def pins(catalog: Mapping[str, Mapping]) -> dict[str, dict[str, Pin]]:
    """Return each build's pinned members (firmware.json's "rescue"), by build."""
    return {name: dict(build.get("rescue", {})) for name, build in catalog.items()}


def identify(chain: Mapping[str, bytes], pinned: Mapping[str, Mapping[str, Pin]]) -> str:
    """Return the build whose boot chain members @p chain's partitions begin with, by their pinned SHA-256s.

    A partition holds its member and padding after it. Raises RescueError for the DDR5 boot chain (PRO) and for
    one that is no pinned build's (UNKNOWN_BUILD).
    """
    for name, members in pinned.items():
        kinds = []
        for part, candidates in BOOT_CHAIN.items():
            found = [i for i, m in enumerate(candidates) if m in members and _begins(chain[part], members[m])]
            if not found:
                break
            kinds.append(found[0])
        else:
            if any(kinds):
                raise RescueError(PRO)
            return name
    raise RescueError(UNKNOWN_BUILD)


def _begins(data: bytes, pin: Pin) -> bool:
    size = pin["size"]
    return len(data) >= size and hashlib.sha256(data[:size]).hexdigest() == pin["sha256"]


def needed(op_list: str) -> list[str]:
    """Return every member the rescue reads of a package whose op list is @p op_list, the boot chain's first."""
    blocks = [f"{name}{end}" for name, _size in lp.op_list(op_list)[2] for end in (".new.dat.br", ".transfer.list")]
    return [*(m for members in BOOT_CHAIN.values() for m in members), *IMAGES.values(), OP_LIST, *blocks]


@dataclass(frozen=True)
class Edl:
    """edl-ng with the headset's loader; a runner that tests replace.

    Before each session what the loader still sends is read (@p quiet: qualcomm.drain_session's reader): bytes
    left from an earlier session, which edl-ng would take for its answers, now and then are there on Windows.
    """

    program: str | Path
    loader: str | Path
    runner: Callable[..., str] = run
    quiet: Callable[[], int] | None = None

    def __call__(self, *words: str | Path, timeout: float = 300, log: Path | None = None) -> str:
        """Run edl-ng with @p words, the port read empty first; its output also goes to @p log."""
        self.empty()
        argv = [str(a) for a in qualcomm.edl_ng(self.program, self.loader, *words)]
        return self.runner(argv, timeout, log=log)

    def empty(self) -> None:
        """Read what waits on the device's pipe until it stays quiet for a second (nothing is sent)."""
        if self.quiet is not None:
            qualcomm.drain_session(self.quiet, time, quiet=1.0, seconds=120)


def look(
    edl: Edl,
    work: Path,
    serial: str,
    pinned: Mapping[str, Mapping[str, Pin]],
    free: Callable[[], None] | None = None,
) -> dict:
    """Look at the headset in EDL mode without writing to it: its serial, partition table and build.

    The first session after EDL mode was entered names the serial; it must be @p serial when .env has one. A
    loader an earlier session used names none, and one cut off mid-transfer answers nothing useful: @p free
    restarts it and enters EDL mode afresh (cycle.Headset.free_edl), once. The boot chain's partitions are read
    by sector, one read a session (two in one session can arrive as one chunk, which edl-ng 1.6 misreads on
    Windows), into @p work. Return the look, also written to work/look.json.
    """
    work.mkdir(parents=True, exist_ok=True)
    output = _first_session(edl, work)
    shown = re.search(qualcomm.HANDSHAKE_SERIAL + r"(\S+)", output)
    if not shown and free is not None:
        free()
        output = _first_session(edl, work)
        shown = re.search(qualcomm.HANDSHAKE_SERIAL + r"(\S+)", output)
    if not shown:
        raise RescueError(NOT_FRESH)
    if serial and shown.group(1).lower() != serial.lower():
        msg = "The headset in EDL mode is not the one .env names (PICO4_EDL_SERIAL). Nothing was written."
        raise RescueError(msg)
    table = partition_table(output)
    missing = [p for p in (*BOOT_CHAIN, *IMAGES, "super", *qualcomm.WIPED, *CLEARED) if p not in table]
    if missing:
        msg = f"The headset's partition table has no {', '.join(missing)}: it is not a PICO 4 as the rescue knows it."
        raise RescueError(msg)
    chain = {part: read(edl, table[part], work / f"{part}.bin", work / f"look-{part}.log") for part in BOOT_CHAIN}
    build = identify(chain, pinned)
    found = {
        "schema": 1,
        "serial": shown.group(1),
        "build": build,
        "partitions": {name: [p.lun, p.first, p.last] for name, p in table.items()},
    }
    (work / "look.json").write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8")
    return found


def _first_session(edl: Edl, work: Path) -> str:
    """Return the partition table at edl-ng's debug level; a failed session's output, for the serial's check."""
    try:
        return edl("--loglevel", "Debug", "printgpt", log=work / "look-printgpt.log")
    except RuntimeError as exc:
        return qualcomm.failed_output(exc)


def read(edl: Edl, partition: Partition, path: Path, log: Path) -> bytes:
    """Read @p partition into @p path by its sectors (read-sector, one read in its session); return the bytes."""
    path.unlink(missing_ok=True)
    sectors = partition.last - partition.first + 1
    edl("read-sector", str(partition.first), str(sectors), path, "--lun", str(partition.lun), log=log)
    data = path.read_bytes()
    if len(data) != sectors * SECTOR:
        msg = f"the read of {path.stem} gave {len(data)} bytes, not {sectors * SECTOR}; nothing was written"
        raise RescueError(msg)
    return data


def table_of(found: Mapping) -> dict[str, Partition]:
    """Return a look's partition table."""
    return {name: Partition(*row) for name, row in found["partitions"].items()}


def prepare(found: Mapping, work: Path, catalog: Mapping[str, Mapping], log: Callable[[str], None] = print) -> dict:
    """Download the look's build, check it against its pins, rebuild its images and lay super out anew.

    Every member the rescue reads must be pinned in firmware.json and match its pin before anything is rebuilt.
    The package's members stay in work/package/download and are fetched again only when missing or broken
    (ota.extract). Each image must fit its partition. super's metadata goes to work/package/super-metadata.bin.
    Return what to write, also in work/prepared.json.
    """
    build = found["build"]
    pinned = pins(catalog)[build]
    url = fastest(catalog[build], log)
    op_list = _op_list(url, log)
    _check(OP_LIST, op_list.encode("ascii"), pinned)
    missing = [m for m in needed(op_list) if m not in pinned]
    if missing:
        msg = (
            f"PICO OS {build}'s package is not pinned in firmware.json ({len(missing)} members, such as {missing[0]}): "
            f"python -m overpressure rescue pin {build}. Nothing was written."
        )
        raise RescueError(msg)
    group, maximum, dynamic = lp.op_list(op_list)
    folder = work / "package"
    copied = {f"{part}.img": member for part, member in IMAGES.items()}
    blocks = {f"{name}.img": name for name, _size in dynamic}
    ota.extract(build, url, folder, copied, blocks, log=log)
    for member in ota.members_needed(copied, blocks):
        _check(member, folder / "download" / member, pinned)
    table = table_of(found)
    for part in IMAGES:
        _fits(folder / f"{part}.img", table[part].size, part)
    for name, size in dynamic:
        _fits(folder / f"{name}.img", size, name)
    super_ = table["super"]
    layout = lp.Layout(super_.size, ALIGNMENT, (-super_.first * SECTOR) % ALIGNMENT)
    region, extents = lp.build(layout, group, maximum, dynamic)
    (folder / "super-metadata.bin").write_bytes(region)
    prepared = {
        "schema": 1,
        "build": build,
        "extents": {name: [e[0].start, e[0].size] for name, e in extents.items() if e},
    }
    (work / "prepared.json").write_text(json.dumps(prepared, indent=2) + "\n", encoding="utf-8")
    return prepared


def _check(member: str, data: bytes | Path, pinned: Mapping[str, Pin]) -> None:
    """Raise RescueError unless @p data (bytes or a file) is @p member as firmware.json pins it."""
    pin = pinned.get(member)
    size, digest = _digest(data)
    if pin is None or (size, digest) != (pin["size"], pin["sha256"]):
        msg = f"{member} is not the file firmware.json pins (SHA-256 {digest}): nothing was written"
        raise RescueError(msg)


def _digest(data: bytes | Path) -> tuple[int, str]:
    if isinstance(data, bytes):
        return len(data), hashlib.sha256(data).hexdigest()
    h = hashlib.sha256()
    with data.open("rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return data.stat().st_size, h.hexdigest()


def fastest(entry: Mapping, log: Callable[[str], None]) -> str:
    """Return the fastest host's URL of a catalog build's archive: its url or a mirror (ota.fastest)."""
    log(">> the fastest of PICO's hosts for this package")
    return ota.fastest(ota.sources(entry), entry["zip_size"], log)


def _op_list(url: str, log: Callable[[str], None]) -> str:
    return ota.read_member(payload.open_source(url, log), OP_LIST, log).decode("ascii")


def _fits(path: Path, room: int, name: str) -> None:
    size = path.stat().st_size
    if size % SECTOR or size > room:
        msg = f"{name}'s image ({size} bytes) does not fit its partition ({room} bytes) in whole sectors"
        raise RescueError(msg)


def plan(found: Mapping, prepared: Mapping, folder: Path) -> list[qualcomm.Write]:
    """Return the rescue's writes: super's metadata and logical partitions, the images, the wipes, misc cleared."""
    table = table_of(found)
    super_ = table["super"]
    writes = [
        qualcomm.Write(
            super_.lun, "super's metadata", super_.first, (1 << 20) // SECTOR, SECTOR, folder / "super-metadata.bin"
        )
    ]
    for name, (start, _size) in prepared["extents"].items():
        image = folder / f"{name}.img"
        writes.append(
            qualcomm.Write(
                super_.lun,
                f"{name} in super",
                super_.first + start // SECTOR,
                image.stat().st_size // SECTOR,
                SECTOR,
                image,
            )
        )
    for part in IMAGES:
        image = folder / f"{part}.img"
        p = table[part]
        writes.append(qualcomm.Write(p.lun, part, p.first, image.stat().st_size // SECTOR, SECTOR, image))
    for part in qualcomm.WIPED:
        p = table[part]
        writes.append(qualcomm.Write(p.lun, part, p.first, min(qualcomm.WIPE_BYTES, p.size) // SECTOR, SECTOR, None))
    for part in CLEARED:
        p = table[part]
        writes.append(qualcomm.Write(p.lun, part, p.first, p.size // SECTOR, SECTOR, None))
    return writes


def same_headset(found: Mapping, edl: Edl, work: Path) -> None:
    """Check that the EDL device is still the looked-at headset: its partition table and its XBL.

    Later sessions name no serial; the table and the boot chain's first partition say it is the same PICO 4.
    """
    output = edl("printgpt", log=work / "write-printgpt.log")
    if partition_table(output) != table_of(found):
        msg = "The headset in EDL mode has another partition table than the one looked at. Nothing was written."
        raise RescueError(msg)
    again = read(edl, table_of(found)["xbl"], work / "xbl-again.bin", work / "write-xbl.log")
    if again != (work / "xbl.bin").read_bytes():
        msg = "The headset in EDL mode is not the one looked at (another XBL). Nothing was written."
        raise RescueError(msg)


def pin(  # noqa: PLR0913  # the build, the catalog and the cache; what to take keyword-only
    build: str,
    catalog: Mapping[str, Mapping],
    cache: Path,
    *,
    small: bool = False,
    local: Path | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Pin]:
    """Return @p build's pins: each member the rescue reads, downloaded (CRC-32 checked) and hashed.

    The dynamic partitions' block images (gigabytes) only without @p small. A member in @p local (a package's
    download folder, such as prepare's) that matches the zip's CRC-32 is taken from there; the others are
    downloaded into @p cache/BUILD, where a later run finds them.
    """
    src = payload.open_source(fastest(catalog[build], log), log)
    infos = ota.zip_infos(src)
    op_list = ota.read_member(src, OP_LIST, log).decode("ascii")
    found = {}
    for member in needed(op_list):
        if small and member.endswith(".new.dat.br"):
            continue
        path = local / member if local is not None else cache / build / member
        if not ota.member_ok(path, infos[member]):
            path = cache / build / member
            ota.download_member(src, infos[member], path, log)
        size, digest = _digest(path)
        found[member] = {"size": size, "sha256": digest}
    return found


def write_pins(path: Path, build: str, found: Mapping[str, Pin]) -> None:
    """Merge @p found into firmware.json's @p build, keeping its other fields and the file's layout."""
    catalog = json.loads(path.read_text(encoding="utf-8"))
    entry = catalog["builds"][build]
    entry["rescue"] = {**entry.get("rescue", {}), **found}
    path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    """Run the rescue's look, prepare or write."""
    ap = argparse.ArgumentParser(prog="python -m overpressure rescue", description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("look", "prepare", "write", "pin"))
    ap.add_argument("builds", nargs="*", metavar="BUILD", help="pin: firmware.json's builds (default: all)")
    ap.add_argument("--work", type=Path, default=WORK, help="the rescue's folder (default out/rescue)")
    ap.add_argument("--execute", action="store_true", help="write: write; without it the writes are only listed")
    ap.add_argument("--small", action="store_true", help="pin: all but the dynamic partitions' block images")
    ap.add_argument("--from", dest="local", type=Path, help="pin: a package's download folder to take members from")
    args = ap.parse_intermixed_args(argv)  # the builds after pin's options too
    catalog = ota.load_catalog(CATALOG)
    if args.command == "pin":
        for build in args.builds or list(catalog):
            found = pin(build, catalog, REPO / "cache" / "rescue", small=args.small, local=args.local)
            write_pins(CATALOG, build, found)
            print(f"{build}: pinned")
        return 0
    settings = localconfig.load()
    reader = qualcomm.ComReader() if sys.platform == "win32" else qualcomm.UsbReader().queued
    edl = Edl(settings.edl_program(), settings.path("loader"), quiet=reader)

    try:
        if args.command == "look":
            dev = devloop.Dev(settings)
            dev.folder = args.work
            found = look(edl, args.work, settings.edl_serial, pins(catalog), lambda: dev.free_edl("look-free"))
            print(f"PICO 4, PICO OS {found['build']}: the rescue writes this build back")
            return 0
        found = json.loads((args.work / "look.json").read_text(encoding="utf-8"))
        if args.command == "prepare":
            prepare(found, args.work, catalog)
            return 0
        prepared = json.loads((args.work / "prepared.json").read_text(encoding="utf-8"))
        writes = plan(found, prepared, args.work / "package")
        size = sum(w.size for w in writes) / (1 << 30)
        if not args.execute:
            for w in writes:
                print(f"LUN {w.lun} sector {w.start}: {w.label} ({w.size} bytes{'' if w.image else ', zeros'})")
            print(f"{len(writes)} writes, {size:.2f} GiB, each read back (preview: nothing was written)")
            return 0
        same_headset(found, edl, args.work)

        def session(argv: list[str | Path], timeout: float) -> str:
            edl.empty()  # each write and each read back in a session of its own, the port empty first
            return run([str(w) for w in argv], int(timeout))

        qualcomm.write_back(writes, edl.program, edl.loader, args.work / "write", session)
        print(
            f"Wrote {len(writes)} partitions ({size:.2f} GiB), each read back; the headset is in EDL mode, not reset."
        )
    except (RescueError, lp.LpError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
