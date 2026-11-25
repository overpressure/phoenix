#!/usr/bin/env bash
# SPDX-License-Identifier: Unlicense
# Build a checkout of overpressure/linux's PICO 4 branch (phoenix/7.2 or one of its tags): Image.gz,
# the device tree and a record of both.
#
#   build-tree.sh TREE WORKDIR OUTDIR
#
# TREE     a clean checkout of overpressure/linux at a phoenix/ commit: Valve's kernel with the
#          layers, the device tree and arch/arm64/configs/pico4.config, which is merged over
#          Valve's gabe_defconfig.
# WORKDIR  the build's output tree (O=) and state (Linux file system; a few GB).
# OUTDIR   Image.gz, sm8250-pico4-phoenix.dtb, config, version and build.json.
#
# The RGB camera's register table is PICO's data and not part of the tree. While the driver
# compiles it in (TREE/drivers/media/i2c/pico4-imx471-mode.h, from the headset's vendor image by
# imx471_mode.py), a tree without it gets an empty table, which leaves the camera dark;
# build.json's camera_table says which: "none" (the driver needs none), "empty" or the table's hash.
#
# uname -v names the tree's commit (#<12 hex digits>) and carries the commit's time, not the
# build's. The same commit and camera table give the same device tree, config and version with the amd64
# build image on any host; Image.gz differs in each build by the module-signing key Valve's configuration
# generates and builds in.
#
# overpressure's build/build-kernel-tree.sh does the build: reproducibly, in the build container (python -m
# overpressure container run), incrementally, through ccache with CCACHE_DIR (CI's check and push builds use it, a
# tag's own build does not).
# shellcheck disable=SC2034  # DTB, KERNEL_HOST, KERNEL_RECORD, work, out: build-kernel-tree.sh, sourced below, reads them
set -euo pipefail

DTB=sm8250-pico4-phoenix FRAGMENT=pico4.config KERNEL_HOST=pico4

[ $# -eq 3 ] || { sed -n '3,20p' "$0" >&2; exit 2; }
tree=$(realpath "$1")
mkdir -p "$2" "$3"
work=$(realpath "$2") out=$(realpath "$3")
here=$(cd "$(dirname "$0")" && pwd)
table=$tree/drivers/media/i2c/pico4-imx471-mode.h
camera=none
if grep -q 'pico4-imx471-mode\.h' "$tree/drivers/media/i2c/pico4-imx471.c"; then
	if [ ! -f "$table" ]; then
		echo "no camera table in the tree: an empty one, the RGB camera stays dark" >&2
		printf '%s\n' '/* No camera module: an empty table, the RGB camera stays dark. */' \
			'static const struct imx471_reg imx471_mode[] = {' '};' > "$table"
	fi
	camera=$(sha256sum "$table" | cut -d' ' -f1)
	grep -q '^static const struct imx471_reg imx471_mode\[\] = {$' "$table" &&
		[ "$(grep -c . "$table")" -le 3 ] && camera=empty
fi
[ -f "$tree/arch/arm64/configs/$FRAGMENT" ] ||
	{ echo "$tree is no PICO 4 tree: it has no arch/arm64/configs/$FRAGMENT" >&2; exit 1; }
# build.json says which camera table the kernel holds.
KERNEL_RECORD="  \"camera_table\": \"$camera\",
"
# shellcheck source=../../../overpressure/build/build-kernel-tree.sh
source "$here/../../../overpressure/build/build-kernel-tree.sh"
