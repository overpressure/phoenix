#!/bin/bash
# SPDX-License-Identifier: Unlicense
# The installed system's read-only image for super (docs/install.md): overpressure's build/build-system-image.sh
# with this headset's folder (PREFIX and SUPER_MIB in userspace/build.sh). As root in the build container (the
# window's "System image for super" step) or in the headset's build host:
#   scripts/build-system-image.sh out/userspace/pico4-userspace.tar.zst out/userspace/pico4-system.img [--keep]
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
exec bash "$here/../../overpressure/build/build-system-image.sh" "$here" "$@"
