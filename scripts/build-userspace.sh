#!/bin/bash
# SPDX-License-Identifier: Unlicense
# The PICO 4's userspace (docs/userspace.md): overpressure's build/build-userspace.sh with this headset's folder,
# its part in userspace/build.sh. As root in the build container (the window's "Userspace archive" step, or
# python -m overpressure container run -- bash devices/phoenix/scripts/build-userspace.sh ...) or in the headset's
# build host (build-on-headset.sh):
#   scripts/build-userspace.sh BUILD_ROOT OUTPUT_DIR [--packages DIR] [--build-packages] [--refresh]
#                              [--update-lock] [--databases DIR]
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
exec bash "$here/../../overpressure/build/build-userspace.sh" "$here" "$@"
