# shellcheck shell=bash
# SPDX-License-Identifier: Unlicense
# The PICO 4's part of overpressure's build/build-userspace.sh, sourced by it: its prefix, the units and ELF
# dependencies it leaves out besides the common ones, and its adaptation of the image root $T (docs/userspace.md).
# The image holds no file of PICO OS's: links to where they are on the headset (the data partition's copy, which the
# install writes from the headset's own backup, and PICO OS's modem, dsp and bluetooth partitions, mounted read-only
# at boot: userspace/stock-links.txt, docs/install.md#pico-oss-files). The headset's own factory data is read from
# persist at boot.
# shellcheck disable=SC2034  # build-userspace.sh reads them
PREFIX=pico4
# The super partition's size: the system image's limit (build-system-image.sh).
SUPER_MIB=8192
# The kernel console's getty (the generator adds it for console=ttyMSM0): no one logs in on the UART.
HEADSET_MASKS=(serial-getty@ttyMSM0.service)
# pico4-slamd's bionic programs: PICO OS's libraries they link are bound into the tracker's root at boot.
HEADSET_ELF_OPTIONAL='|^pico4-slamd /usr/lib/pico4-stock-slam/'

# headset_snapshot DIR: the files the build takes besides userspace/: stage 1's, and the prebuilt programs.
headset_snapshot() {
    mkdir -p "$1/initramfs/usr/libexec/pico4" "$1/prebuilt/aarch64"
    cp -r "$source_dir/src" "$1/"
    for f in lib.sh recovery-policy.sh recovery-watch; do
        cp "$source_dir/initramfs/usr/libexec/pico4/$f" "$1/initramfs/usr/libexec/pico4/"
    done
    cp "$source_dir/prebuilt/aarch64/pico4-reboot" "$source_dir/prebuilt/aarch64/pico4-splash" "$1/prebuilt/aarch64/"
}

# headset_files: stage 1's shared files, the prebuilt programs, the controllers' remapping, the links to PICO OS's
# files and the GPU's firmware paths, before the session user.
headset_files() {
    # Shared with stage 1, at the same paths: one recovery policy for both stages. The rest of the PICO 4's
    # files are the project's packages (pico4-base, -power, -audio, -xr, -session, -checks).
    for f in lib.sh recovery-policy.sh recovery-watch; do
        overlay "$project/initramfs/usr/libexec/pico4/$f" "$T/usr/libexec/pico4/$f"
    done
    for f in pico4-reboot pico4-splash; do
        install -o 0 -g 0 -m 0755 "$project/prebuilt/aarch64/$f" "$T/usr/bin/$f"
    done
    # SteamVR reads its own remapping for pico_controller, whatever the profile names: the Frame
    # controller's layout (pico4-session's /usr/share/pico4/frame-controller-remapping.json) joins Valve's
    # layouts there.
    python3 - "$T/opt/steamvr/resources/input/default_pico_controller_remapping.json" \
        "$T/usr/share/pico4/frame-controller-remapping.json" <<'EOF'
import json, sys
path, ours = sys.argv[1], json.load(open(sys.argv[2]))
remapping = json.load(open(path))
remapping["layouts"] = [l for l in remapping["layouts"]
                        if l.get("from_controller_type") != ours["from_controller_type"]] + [ours]
with open(path, "w") as f:
    json.dump(remapping, f, indent=2)
EOF
    # PICO OS's files are not in the image (docs/install.md#pico-oss-files): links point to where they
    # are on the headset (userspace/stock-links.txt), the data partition's copy that the install writes
    # from the headset's own backup (/var/lib/pico4-stock) and PICO OS's modem, dsp and bluetooth
    # partitions, which preinit mounts read-only (/run/pico4/firmware).
    while read -r link target; do
        mkdir -p "$(dirname "$T/$link")"
        ln -sfn "$target" "$T/$link"
    done < <(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$project/userspace/stock-links.txt")
    # A mainline kernel's msm (Valve's) loads the GPU firmware again after a GPU recovery: at its paths,
    # the zap shader where the device tree names it.
    mkdir -p "$T/usr/lib/firmware/qcom/sm8250/pico/phoenix"
    ln -sfn ../a650_sqe.fw "$T/usr/lib/firmware/qcom/a650_sqe.fw"
    ln -sfn ../a650_gmu.bin "$T/usr/lib/firmware/qcom/a650_gmu.bin"
    for f in a650_zap.mdt a650_zap.b00 a650_zap.b01 a650_zap.b02; do
        ln -sfn "../../../../$f" "$T/usr/lib/firmware/qcom/sm8250/pico/phoenix/$f"
    done
}

# headset_system: PICO OS's persist mount point and the zram swap, after the common masks.
headset_system() {
    # PICO OS's mount point of its persist partition (pico4-persist.service, read-only).
    mkdir -p "$T/mnt/vendor/persist"
    # zram-generator's swap unit has no PICO OS priority; pico4-zram-swap.service runs its setup from
    # the Frame's configuration instead.
    mkdir -p "$T/etc/systemd/system-generators"
    ln -sfn /dev/null "$T/etc/systemd/system-generators/zram-generator"
}

# headset_session: Steam Link's compatibility layer for the PICO 4's controllers.
headset_session() {
    # Steam Link's client takes the PICO 4's controllers for the Frame's through pico4-vrlink's
    # compatibility layer (docs/session.md#steam-link), preloaded by its launcher only.
    vrlink_run=$T/opt/steamvr/tools/vrlink/bin/linuxarm64/run_vrlink.sh
    grep -qx '\./vrlink "\$@" &' "$vrlink_run" ||
        { echo "$vrlink_run does not start vrlink as expected; check pico4-vrlink's preload" >&2; exit 1; }
    sed -i 's|^\./vrlink "\$@" &$|LD_PRELOAD=/usr/lib/libpico4-vrlink-compat.so${LD_PRELOAD:+:$LD_PRELOAD} ./vrlink "$@" \&|' "$vrlink_run"
}
