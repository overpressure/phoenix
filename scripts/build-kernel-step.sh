#!/usr/bin/env bash
# SPDX-License-Identifier: Unlicense
# Valve's kernel for the PICO 4, built here from the commit kernel/release.json pins: the window's "build the
# kernel yourself" step, and how anyone checks a release (overpressure's build/build-kernel-step.sh with this
# headset's folder). Runs in the build container (python -m overpressure container run).
#
#   build-kernel-step.sh [WORKDIR]
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
exec bash "$here/../../overpressure/build/build-kernel-step.sh" "$here" "${1:-/var/tmp/pico4-kernel-build}"
