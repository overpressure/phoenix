# SPDX-License-Identifier: Unlicense
"""The PICO 4's downloads, on this computer before the install writes the headset (docs/install.md#downloads-first).

    python -m overpressure --device phoenix dev prefetch    into cache/headset/phoenix, each file checked

overpressure's prefetch does the downloading and checking: overpressure/packages at the pinned commit
(userspace/packages.json), its release's packages, Holo's rootfs for the build host with the build host's python
(userspace/build-host.json), Valve's package databases and every upstream package headsets/phoenix/packages.lock
names. The PICO 4's build takes one file of the pinned commit besides: the installed system's fan program
(pico4-fan), which the build runs where no fan service does (stage 1).
"""

from __future__ import annotations

from pathlib import Path

from overpressure.api.v1 import REPO, prefetch
from overpressure.api.v1.prefetch import HOST, MANIFEST, PACKAGE_CACHE, local

HERE = Path(__file__).resolve().parents[1]
PIN = HERE / "userspace" / "packages.json"
BUILD_HOST = HERE / "userspace" / "build-host.json"
#: This computer's copy, laid out as the headset's build folder (BASE in build-on-headset.sh).
CACHE = REPO / "cache" / "headset" / "phoenix"
#: The pinned commit's files the PICO 4's build takes besides the common ones: the fan program.
EXTRA = (("downloads/pico4-fan-{commit}", "phoenix/pico4-power/pico4-fan"),)
__all__ = ["HOST", "MANIFEST", "PACKAGE_CACHE", "local"]


def prefetcher() -> prefetch.Prefetch:
    """Return the PICO 4's prefetch (overpressure's, with its pins, its cache and its extra file)."""
    return prefetch.Prefetch(codename="phoenix", cache=CACHE, pin=PIN, build_host=BUILD_HOST, extra=EXTRA)


def manifest(cache: Path = CACHE) -> list[str]:
    """Return the files the last prefetch left for the pin, or none: what the install sends."""
    return prefetch.manifest(cache)
