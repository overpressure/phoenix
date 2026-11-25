#!/bin/sh
# SPDX-License-Identifier: Unlicense
# The PICO 4's system built on the headset itself (docs/install.md#built-on-the-headset), from the maintenance
# stage 1 (pico4-install build) or from the installed system: overpressure's build/build-on-headset.sh with this
# headset's folder (its fan in scripts/build-on-headset-hook.sh). POSIX sh: stage 1 has no bash.
#
#   build-on-headset.sh BASE
set -eu
base=${1:?specify the build folder}
exec sh "$base/src/overpressure/build/build-on-headset.sh" "$base" "$base/src/devices/phoenix"
