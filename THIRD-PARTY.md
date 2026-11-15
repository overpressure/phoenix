# Third-party material and other licenses

The project's own code is under the Unlicense ([LICENSE](LICENSE)). This file lists what in this
repository is not: the project's own code that is built into another project and takes its license.
Each source file names its license in its `SPDX-License-Identifier` header; `tests/test_licenses.py` checks
the headers.

## The project's own code under another license

| Path | License | Note |
| --- | --- | --- |
| `src/splash/` | BSL-1.0 | |
| `src/splash/pico4-splash-font.h` | OFL-1.1 | outlines of Inter 4.1 (The Inter Project Authors), written by `src/splash/font.py`; the license is `src/splash/OFL.txt` |
| `prebuilt/aarch64/pico4-splash` | BSL-1.0, OFL-1.1 | built from `src/splash/`, with Inter's outlines |

`prebuilt/aarch64/pico4-reboot` is built from `src/reboot/` (Unlicense). Both prebuilt programs are
freestanding and link no library.

## Not in the repository

The build downloads and builds other projects (Valve's packages, Alpine's packages and others) and
overpressure's host tools fetch adb, fastboot and edl-ng. None of them is in the repository or published by
the project. Where each vendor file comes from is in [docs/distribution.md](docs/distribution.md).

## The packages

The project's packages (Monado with the PICO 4's driver, Basalt, gamescope, hexagonrpc, PipeWire's ALSA
plugin, GTK 2, MangoHud and others) and the PICO 4's image definition are their own repository,
[overpressure/packages](https://github.com/overpressure/packages), whose THIRD-PARTY.md lists the material
from other projects and the project's own code under their licenses. `userspace/packages.json` pins its
release `2026.11.15.1`, which carries the complete sources of its GPL and LGPL packages.

## The kernel

Valve's Linux kernel with the project's changes is its own repository,
[overpressure/linux](https://github.com/overpressure/linux) (GPL-2.0-only, unless a file states another
license). `kernel/release.json` pins its release `phoenix/7.2-1`, whose binaries the project publishes there
with their source: Valve's commit `509b71a1bdcd` and the project's commits on it.
