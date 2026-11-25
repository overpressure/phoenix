# SPDX-License-Identifier: Unlicense
# shellcheck shell=dash
# The PICO 4's part of overpressure's build/build-on-headset.sh, sourced by it (POSIX sh: stage 1 has no bash).
# before_build starts the fan where nothing drives it; after_build stops it. The variables are the build's:
# base, host, repository and commit (the overpressure/packages pin), and fetch (fetch.sh).
# shellcheck disable=SC2154  # base, host, repository, commit: build-on-headset.sh's, which sources this
fan=""
before_build() {
	# The fan, with the installed system's program and policy (pico4-fan, PICO OS's table), where nothing
	# drives it: the maintenance stage 1 runs no fan service, and the build's compression took the headset
	# to 88 °C without it. The installed system's service drives it there. Stopped, it keeps its duty.
	if [ -e /sys/class/tty/ttyHS1 ] && ! pgrep -f "pico4-fan run" > /dev/null; then
		if [ -s "$base/downloads/pico4-fan-$commit" ]; then
			cp "$base/downloads/pico4-fan-$commit" "$host/run/pico4-fan-run"
		else
			fetch "https://raw.githubusercontent.com/$repository/$commit/phoenix/pico4-power/pico4-fan" \
				"$host/run/pico4-fan-run" || die "could not fetch $repository's pico4-fan at $commit"
		fi
		mkdir -p "$host/run/pico4-fan"
		# Its last-resort power-off reaches no systemd from the build host: it signals PID 1 instead, which
		# powers off on SIGUSR2 in the maintenance stage 1 (pico4-init) and on SIGRTMIN+4 under systemd.
		poweroff_signal=SIGUSR2
		[ ! -d /run/systemd/system ] || poweroff_signal=SIGRTMIN+4
		# A plain command, so that $! is the program itself (chroot and env exec it): it holds this
		# namespace's mounts, the data partition among them, until it ends.
		chroot "$host" /usr/bin/env -i PATH=/usr/bin PICO4_FAN_POWEROFF_SIGNAL=$poweroff_signal \
			python3 /run/pico4-fan-run run > "$base/fan.log" 2>&1 &
		fan=$!
		log "the fan runs with PICO OS's policy (pico4-fan, log in $base/fan.log)"
	fi
}
after_build() {
	if [ -n "$fan" ]; then
		kill "$fan" 2>/dev/null || true
		wait "$fan" 2>/dev/null || true
	fi
	fan=""
}
