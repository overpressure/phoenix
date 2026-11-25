# SPDX-License-Identifier: Unlicense
"""The headset's USB network link on a Linux PC: a link that is up without the headset's address, and its fix.

The headset's own systems (the maintenance stage 1 and the installed system) are one composite gadget on USB,
whose network function hands the PC one DHCP lease, HOST_IP (docs/install.md#the-usb-link-on-linux).
NetworkManager can put a profile of its own on that interface instead: a wired profile with a fixed address that
is bound to no network port takes every new one. The link is then up, but the PC has no address in the headset's
network, and SSH finds no route to it. The fix is a profile for the headset's interface alone, which takes the
lease and leaves the PC's own routes and DNS alone; overpressure changes nothing on the PC itself, it names
the commands.
"""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

#: The headset's network, its own address and the one lease it hands out.
NETWORK = ipaddress.ip_network("172.16.42.0/24")
HOST_IP = "172.16.42.2"
#: The composite gadget of the headset's own systems: generic IDs, narrowed by the product string.
VENDOR, PRODUCT, LABEL = "1d6b", "0104", "PICO 4 Linux"
#: NetworkManager's profile for the headset's link, which the fix adds.
PROFILE = "overpressure-phoenix"
SYS_NET = Path("/sys/class/net")
#: Runs a program and returns its exit status and output; tests pass a fake.
Run = Callable[[Sequence[str]], "tuple[int, str]"]


def run_quietly(argv: Sequence[str]) -> tuple[int, str]:
    """Run a query (ip, nmcli) for at most 10 s; return its exit status and output, (127, "") when it cannot run."""
    if not shutil.which(argv[0]):
        return 127, ""
    try:
        done = subprocess.run(  # noqa: S603  # fixed queries, no shell
            list(argv), check=False, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return done.returncode, done.stdout


@dataclass(frozen=True)
class Link:
    """The headset's network interface on this PC: its name, its MAC, its IPv4 addresses, NetworkManager's profile."""

    name: str
    mac: str
    addresses: tuple[str, ...]
    profile: str  #: the profile NetworkManager put on it; empty when none, or without NetworkManager

    def has_lease(self) -> bool:
        """Tell whether the interface has an address in the headset's network."""
        return any(ipaddress.ip_interface(a).ip in NETWORK for a in self.addresses)


def usb_device(interface: Path) -> Path:
    """Return the USB device folder of the network interface folder @p interface (its device is a USB interface)."""
    return (interface / "device").resolve().parent


def interfaces(net: Path = SYS_NET, device: Callable[[Path], Path] = usb_device) -> list[str]:
    """Return the network interfaces of the headset's gadget, by name (Linux; none elsewhere)."""
    found = []
    try:
        names = sorted(p.name for p in net.iterdir())
    except OSError:
        return []
    for name in names:
        try:
            usb = device(net / name)  # the gadget's USB interface (3-4:1.0) is its device; the IDs are its parent's
            ids = tuple((usb / f).read_text(encoding="utf-8").strip() for f in ("idVendor", "idProduct", "product"))
        except OSError:
            continue
        if ids == (VENDOR, PRODUCT, LABEL):
            found.append(name)
    return found


def addresses(name: str, run: Run) -> tuple[str, ...]:
    """Return @p name's IPv4 addresses with their prefix (ip -j), empty when there are none or ip cannot tell."""
    status, output = run(["ip", "-j", "-4", "address", "show", "dev", name])
    if status:
        return ()
    try:
        rows = json.loads(output or "[]")
        return tuple(f"{a['local']}/{a['prefixlen']}" for row in rows for a in row.get("addr_info", ()))
    except (ValueError, KeyError, TypeError, AttributeError):
        return ()


def profile(name: str, run: Run) -> str:
    """Return the NetworkManager profile active on @p name, empty when none or without NetworkManager."""
    status, output = run(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", name])
    return output.strip() if not status else ""


def links(net: Path = SYS_NET, run: Run = run_quietly, device: Callable[[Path], Path] = usb_device) -> list[Link]:
    """Return the headset's network interfaces on this PC, each with its addresses and profile."""
    found = []
    for name in interfaces(net, device):
        try:
            mac = (net / name / "address").read_text(encoding="utf-8").strip()
        except OSError:
            mac = ""
        found.append(Link(name, mac, addresses(name, run), profile(name, run)))
    return found


def fix(link: Link, *, nmcli: bool) -> str:
    """Return the commands that give @p link the headset's lease, for a terminal."""
    if not nmcli:
        return f"sudo ip address flush dev {link.name} && sudo ip address add {HOST_IP}/24 dev {link.name}"
    match = f"ethernet.mac-address {link.mac}" if link.mac else f"connection.interface-name {link.name}"
    return (
        f"nmcli connection add type ethernet con-name {PROFILE} {match} connection.autoconnect-priority 100 "
        "ipv4.method auto ipv4.never-default yes ipv4.ignore-auto-dns yes ipv6.method disabled\n"
        f"nmcli connection up {PROFILE}"
    )


def problem(found: Sequence[Link] | None = None, *, platform: str = sys.platform, nmcli: bool | None = None) -> str:
    """Return why the headset's USB link is up without its address, and the fix, in the owner's words; else empty.

    Empty off Linux, without the headset's interface (the headset is not there yet) and once it has the lease.
    """
    if platform != "linux":
        return ""
    for link in links() if found is None else found:
        if link.has_lease():
            continue
        has = ", ".join(link.addresses) or "no address"
        why = (
            f'NetworkManager put its profile "{link.profile}" on it'
            if link.profile and link.profile != PROFILE
            else "it did not take the headset's lease"
        )
        use_nmcli = (shutil.which("nmcli") is not None) if nmcli is None else nmcli
        own = (
            "Give the headset's link a profile of its own, which takes the headset's address and leaves this "
            "computer's routes and DNS alone"
            if use_nmcli
            else "Give the headset's link its address (until the headset is unplugged)"
        )
        return (
            f"The headset's USB link is up on this computer ({link.name}), but has {has} instead of {HOST_IP}, which "
            f"the headset hands out: {why}, so this computer has no route to the headset. {own}, in a terminal:\n\n"
            f"{fix(link, nmcli=use_nmcli)}"
        )
    return ""
