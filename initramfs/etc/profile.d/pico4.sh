# SPDX-License-Identifier: Unlicense
# shellcheck shell=dash
# PICO 4 Linux shell environment (all login shells: ssh, telnet, serial); overpressure's runtime stops the
# recovery deadline in its own profile.d/overpressure.sh.

PS1='\[\033[1;32m\]pico4\[\033[0m\]:\w\$ '
alias fastboot-mode='pico4-reboot bootloader'
alias android='pico4-reboot'

if [ -z "$OP_BANNER_SHOWN" ] && [ -t 1 ]; then
	export OP_BANNER_SHOWN=1
	pico4-info
fi
