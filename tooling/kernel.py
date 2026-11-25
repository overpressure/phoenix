# SPDX-License-Identifier: Unlicense
"""Valve's kernel for the PICO 4: the release of overpressure/linux this repository pins.

    python -m overpressure --device phoenix kernel [fetch]       the pinned release's files into out/kernel
    python -m overpressure --device phoenix kernel build [WORK]  the pinned commit built here instead, checked
    python -m overpressure --device phoenix kernel check DIR     DIR's files against the pin
    python -m overpressure --device phoenix kernel contracts     the names and paths the kernel and this
                                                                 repository share, against the pinned commit

The kernel's source is overpressure/linux (phoenix/7.2 and its layers); kernel/release.json pins one
of its tags: the commit, the build image it was built with and every file's SHA-256. The installer
downloads those files from the tag's release and checks them. `kernel build` builds the same commit
here instead (scripts/build-kernel-step.sh, in the build image the release was built with) and checks
the result against the same hashes: how an owner verifies a release. Only overpressure/linux's CI
builds; this repository's CI downloads the pinned release and checks it.

The contracts are what no build checks: a name or a path one side uses and the other provides (a
firmware file, a V4L2 control, a sysfs name). Each reads the pinned commit's files from GitHub, without
its history, and this repository's own or, for Monado's side, those of the overpressure/packages commit
that userspace/packages.json pins; a renamed side fails before anything is installed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable

from overpressure.api.v1 import REPO, kernelpin
from overpressure.api.v1.kernelpin import BUILD_DIFFERS, IN_PACKAGES, Contract, Opener, Release, mismatches, sha256

Log = Callable[[str], None]
__all__ = ["BUILD_DIFFERS", "IN_PACKAGES", "Contract", "Release", "mismatches", "sha256"]

#: The pin: overpressure/linux's tag, its commit, the build image and its files' hashes.
RELEASE = Path(__file__).resolve().parents[1] / "kernel" / "release.json"
#: Where the files go, for the boot image (dev boot-image).
OUT = REPO / "out" / "kernel"
#: The pinned commit's files the contracts read, kept per commit.
CACHE = REPO / "cache" / "kernel"
#: The overpressure/packages pin: Monado's side of the camera and panel contracts.
PACKAGES = Path(__file__).resolve().parents[1] / "userspace" / "packages.json"
#: The self-build's folder, on the Linux side (the build container's volume).
WORK = "/var/tmp/pico4-kernel-build"  # noqa: S108  # a root-owned build folder, not a temporary file


def release(path: Path = RELEASE) -> Release:
    """Return the kernel pin in @p path."""
    return kernelpin.release(path)


def packages_release(path: Path = PACKAGES) -> Release:
    """Return the overpressure/packages pin in @p path, for its files' URLs."""
    return kernelpin.release(path)


def current(folder: Path = OUT, pin: Release | None = None) -> bool:
    """Tell whether @p folder holds the pinned kernel's files."""
    return kernelpin.current(folder, pin or release())


def fetch(
    folder: Path = OUT, pin: Release | None = None, *, log: Log = print, opener: Opener | None = None
) -> list[str]:
    """Download the pinned release's files into @p folder, each checked by hash; return their names."""
    return kernelpin.fetch(folder, pin or release(), log=log, opener=opener)


#: The kernel build's step in the build container: fetch the pinned commit, build it, check it, install it.
STEP = "devices/phoenix/scripts/build-kernel-step.sh"


def build_argv(work: str = WORK) -> list[str]:
    """Return the command line that builds the pinned commit here (in the build container)."""
    return kernelpin.build_argv(STEP, work)


def build(work: str = WORK, pin: Release | None = None, call: Callable[..., int] = subprocess.call) -> int:
    """Build the pinned commit in the image the release was built with; return the build's exit status."""
    return kernelpin.build(STEP, work, pin or release(), call)


# -- the contracts ------------------------------------------------------------------------------
#: Where the kernel's names are used: the image's files, its build, Monado's cameras and panels.
_PHOENIX = f"{IN_PACKAGES}phoenix"
_COMMON = f"{IN_PACKAGES}common"
_SM8250 = f"{IN_PACKAGES}sm8250"
#: The system image's links to PICO OS's files on the headset, which the build script makes.
_LINKS = "devices/phoenix/userspace/stock-links.txt"
_MONADO = f"{IN_PACKAGES}phoenix/pico4-monado"
_CAM = f"{_MONADO}/pico4_cam.c"
#: The scripts that read the power supplies.
_POWER_READERS = tuple(
    f"{_PHOENIX}/{name}"
    for name in ("pico4-power/battery-guard", "pico4-base/charger", "pico4-checks/pico4-userspace-check")
)
CONTRACTS = (
    Contract(
        "the RGB camera's register table: where the driver loads it, where the image links it",
        (("drivers/media/i2c/pico4-imx471.c", r'#define IMX471_MODE_FIRMWARE\s+"([^"]+)"'),),
        ((_LINKS, r"usr/lib/firmware/(qcom/sm8250/pico4/imx471-mode\.bin)"),),
    ),
    Contract(
        "the audio calibration: where the driver loads it, where the image links it",
        (("sound/soc/qcom/qdsp6/q6calibration-pico4.c", r'#define CAL_FIRMWARE\s+"([^"]+)"'),),
        ((_LINKS, r"usr/lib/firmware/(qcom/sm8250/pico4/audio-calibration\.bin)"),),
    ),
    Contract(
        "the audio calibration file's magic: what pico4-audiocal writes, what the driver takes",
        (("sound/soc/qcom/qdsp6/q6calibration-pico4.c", r"#define CAL_MAGIC\s+(0x[0-9a-fA-F]+)"),),
        ((f"{_PHOENIX}/pico4-audio/pico4-audiocal", r'^MAINLINE_MAGIC = b"(\w+)"'),),
        kernel_value=lambda value: int(value, 16).to_bytes(4, "little").decode("ascii"),
    ),
    Contract(
        "the audio calibration file's version",
        (("sound/soc/qcom/qdsp6/q6calibration-pico4.c", r"#define CAL_VERSION\s+(\d+)"),),
        ((f"{_PHOENIX}/pico4-audio/pico4-audiocal", r"^MAINLINE_VERSION = (\d+)"),),
    ),
    Contract(
        "the RGB camera's width: the mode imx471_mode.py takes and Monado's frames, the size the driver gives",
        (("drivers/media/i2c/pico4-imx471.c", r"#define IMX471_WIDTH\s+(\d+)"),),
        (
            ("devices/phoenix/kernel/imx471_mode.py", r"^WIDTH, HEIGHT, \w+, \w+, \w+, \w+ = (\d+),"),
            (f"{_MONADO}/pico4_cam.h", r"#define PICO4_CAM_RGB_WIDTH (\d+)"),
        ),
    ),
    Contract(
        "the RGB camera's height",
        (("drivers/media/i2c/pico4-imx471.c", r"#define IMX471_HEIGHT\s+(\d+)"),),
        (
            ("devices/phoenix/kernel/imx471_mode.py", r"^WIDTH, HEIGHT, \w+, \w+, \w+, \w+ = \d+, (\d+),"),
            (f"{_MONADO}/pico4_cam.h", r"#define PICO4_CAM_RGB_HEIGHT (\d+)"),
        ),
    ),
    Contract(
        "the RGB camera's longest exposure: the mode's frame length less 10 lines",
        (("drivers/media/i2c/pico4-imx471.c", r"#define IMX471_MAX_LINES\s+(\d+)"),),
        (
            (
                "devices/phoenix/kernel/imx471_mode.py",
                r"^WIDTH, HEIGHT, \w+, \w+, \w+, \w+ = \d+, \d+, \d+, \d+, (\d+),",
            ),
        ),
        here_value=lambda value: str(int(value) - 10),
    ),
    Contract(
        "the panels' backlight Monado switches for standby",
        (("drivers/gpu/drm/panel/panel-pico4-ls026b3sa.c", r'devm_backlight_device_register\(dev, "([^"]+)"'),),
        ((f"{_MONADO}/pico4_hmd.c", r"/sys/class/backlight/([\w-]+)/"),),
    ),
    Contract(
        "the RGB camera's paused control: its id in the driver and in Monado",
        (
            (
                "drivers/media/i2c/pico4-imx471.c",
                r"#define V4L2_CID_PICO4_PAUSED\s+\(V4L2_CID_USER_BASE \| (0x[0-9a-fA-F]+)\)",
            ),
        ),
        ((_CAM, r"#define V4L_CID_RGB_PAUSED \(V4L2_CID_USER_BASE \| (0x[0-9a-fA-F]+)\)"),),
        kernel_value=str.lower,
        here_value=str.lower,
    ),
    Contract(
        "the CAMSS entities Monado links the camera pipelines by",
        (
            ("drivers/media/platform/qcom/camss/camss-csiphy.c", r'#define MSM_CSIPHY_NAME "([^"]+)"'),
            ("drivers/media/platform/qcom/camss/camss-csid.c", r'#define MSM_CSID_NAME "([^"]+)"'),
            ("drivers/media/platform/qcom/camss/camss-vfe.c", r'#define MSM_VFE_NAME "([^"]+)"'),
        ),
        ((_CAM, r'snprintf\(\w+, sizeof\(\w+\), "(msm_[a-z]+?)%d'),),
    ),
    Contract(
        "the GPU's workqueues kthread-priorities raises, with their sysfs attributes",
        (
            ("drivers/gpu/drm/msm/msm_kms.h", r'alloc_ordered_workqueue\("([^"]+)", WQ_SYSFS'),
            ("drivers/gpu/drm/msm/dsi/dsi_host.c", r'alloc_ordered_workqueue\("([^"]+)", WQ_SYSFS'),
            ("drivers/gpu/drm/msm/msm_ringbuffer.c", r'msm_gem_object_set_name\(ring->bo, "([^"]+)"'),
        ),
        ((f"{_PHOENIX}/pico4-session/kthread-priorities", r"^for wq in ([^;]+);"),),
        here_value=kernelpin.numbered,
    ),
    Contract(
        "the power supplies the scripts read",
        (("drivers/power/supply/pico4-pm8150b-battery.c", r'^\t\.name = "([^"]+)"'),),
        tuple((path, r"power_supply/(\w+)/") for path in _POWER_READERS),
    ),
    Contract(
        "the power supplies' attributes the scripts read",
        (("drivers/power/supply/pico4-pm8150b-battery.c", r"^\tPOWER_SUPPLY_PROP_(\w+),$"),),
        tuple((path, r"power_supply/\w+/(\w+)") for path in _POWER_READERS),
        kernel_value=str.lower,
    ),
    Contract(
        "the USB gadget functions the system's gadget creates",
        (("arch/arm64/configs/pico4.config", r"^CONFIG_USB_CONFIGFS_(\w+)=y$"),),
        ((f"{_COMMON}/overpressure-base/usb-link", r"functions/(\w+)\."),),
        kernel_value=str.lower,
    ),
    Contract(
        "the video codec's firmware: the name the device tree gives, the file the image's probe waits for",
        (("arch/arm64/boot/dts/qcom/sm8250-pico4-phoenix.dts", r'firmware-name = "(venus\.mdt)";'),),
        ((f"{_SM8250}/sm8250-base/sm8250-venus-probe.service", r"/usr/lib/firmware/(venus\.mdt)$"),),
    ),
    Contract(
        "the device tree the boot image takes",
        (("arch/arm64/boot/dts/qcom/Makefile", r"\+= (sm8250-pico4-[\w-]+\.dtb)$"),),
        (("devices/phoenix/kernel/recovery_image.py", r'^DTB = "([^"]+)"'),),
    ),
)


def kernel_file(path: str, pin: Release | None = None, *, cache: Path = CACHE, opener: Opener | None = None) -> str:
    """Return the pinned commit's file @p path, kept in @p cache per commit."""
    return kernelpin.source_file(path, pin or release(), cache=cache, opener=opener)


def packages_file(path: str, *, cache: Path = CACHE, opener: Opener | None = None) -> str:
    """Return the pinned overpressure/packages commit's file @p path, kept in @p cache per commit."""
    return kernelpin.source_file(path, packages_release(), cache=cache, opener=opener)


def check(
    contracts: tuple[Contract, ...] = CONTRACTS,
    read: Callable[[str], str] = kernel_file,
    root: Path = REPO,
    packages: Callable[[str], str] = packages_file,
) -> list[str]:
    """Return the broken contracts, each as one line that names both sides."""
    return kernelpin.check(contracts, read, root, packages)


def main(argv: list[str] | None = None) -> int:
    """Run the kernel command line."""
    ap = argparse.ArgumentParser(
        prog="python -m overpressure --device phoenix kernel", description=__doc__.split("\n\n")[0]
    )
    sub = ap.add_subparsers(dest="action")
    sub.add_parser("fetch", help="the pinned release's files into out/kernel (the default)")
    built = sub.add_parser("build", help="the pinned commit built here, its device tree, config and version checked")
    built.add_argument("work", nargs="?", default=WORK, help=f"the build folder in the container (default {WORK})")
    checked = sub.add_parser("check", help="a folder's kernel files against the pin")
    checked.add_argument("folder", type=Path)
    checked.add_argument(
        "--built", action="store_true", help="a build of the pinned commit: its Image.gz has its own signing key"
    )
    sub.add_parser("contracts", help="the names and paths the kernel and this repository share")
    args = ap.parse_args(argv)
    pin = release()
    if args.action == "build":
        return build(args.work, pin)
    if args.action == "check":
        found = mismatches(args.folder, pin, built=args.built)
        for line in found:
            print(f"error: {line}", file=sys.stderr)
        if not found:
            print(f"{args.folder}: the files of {pin.tag} ({pin.commit[:12]})")
        return 1 if found else 0
    if args.action == "contracts":
        broken = check(read=lambda path: kernel_file(path, pin))
        for contract in CONTRACTS:
            print(f"{'broken' if any(b.startswith(contract.name) for b in broken) else 'ok':6} {contract.name}")
        for line in broken:
            print(f"error: {line}", file=sys.stderr)
        return 1 if broken else 0
    try:
        fetch(OUT, pin)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{OUT}: the files of {pin.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
