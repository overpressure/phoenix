# PICO 4

[![Newest release](https://img.shields.io/github/v/release/overpressure/phoenix?include_prereleases&label=release&color=2a66ec)](https://github.com/overpressure/phoenix/releases)
[![CI on main](https://github.com/overpressure/phoenix/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/overpressure/phoenix/actions/workflows/ci.yml?query=branch%3Amain)
[![Chat: the PICO 4 room on Matrix](https://img.shields.io/badge/chat-PICO%204%20room-2a66ec?logo=matrix&logoColor=white)](https://matrix.to/#/#overpressure-phoenix:matrix.org)

**Linux and Valve's SteamVR on the PICO 4 (2022, Snapdragon XR2), installed with
[overpressure](https://github.com/overpressure/overpressure).** It replaces PICO OS with a system built on Valve's
Steam Frame software: SteamVR, the Steam client and its games run on the headset itself, without a PC. The way back
to PICO OS is part of it.

| | |
| --- | --- |
| **Status** | Beta: for owners who are fine with an early release and can report what they find |
| **Newest release** | [2026.11.0 beta 1](https://github.com/overpressure/phoenix/releases) |
| **Your headset** | The original PICO 4, not the PICO 4 Pro or Ultra; PICO OS global build 5.13.3, 5.13.7 or 5.13.8, not a China build ([why](docs/device-preparation.md#other-pico-os-builds)) |
| **Your computer** | Windows ✅ · Linux ✅ · macOS ❔ |
| **Tested on** | 1 headset |

> [!WARNING]
> Installing erases PICO OS's apps and data, and what is left of the warranty. The installer makes a full backup
> first: it is your way back, so keep it safe. After going back, PICO OS starts like after a factory reset: you set
> it up again and download your apps again.

## What works

✅ Works · 🟡 Works, with limits · ❌ Doesn't work yet · ❔ Not tested yet · ➖ Not on this headset

### Display and comfort

| Feature | | Notes |
| --- | --- | --- |
| Both displays | ✅ | 2160 × 2160 per eye at 90 or 72 Hz, switchable in SteamVR's settings |
| Lens distance (IPD) | ✅ | Overpressure Settings moves the lenses, 58 to 72 mm as on PICO OS; the picture follows |
| Standby when taken off | ✅ | Dark when taken off, back within seconds when put on |
| Cooling | ✅ | The fan runs as on PICO OS |

### Tracking

| Feature | | Notes |
| --- | --- | --- |
| Head tracking (6DoF) | ✅ | PICO OS's own tracker; rooms come back after a restart |
| Controller tracking | ✅ | |
| Hand tracking | ❌ | Not available yet ([#37](https://github.com/overpressure/phoenix/issues/37)) |
| Eye tracking | ➖ | Only the PICO 4 Pro has it |
| Room setup and boundary | ✅ | Kept across restarts |
| Passthrough (room view) | ✅ | In colour |

### Controllers

| Feature | | Notes |
| --- | --- | --- |
| Buttons, sticks and triggers | ✅ | |
| Vibration | ✅ | |
| Battery level | ✅ | In steps of 20 % |
| Pairing | ✅ | In the first-run setup and in the Overpressure Settings tab |

### Sound

| Feature | | Notes |
| --- | --- | --- |
| Speakers and volume | ✅ | Including the volume keys |
| Microphone | ✅ | |
| Headphone jack | ➖ | The PICO 4 has none |

### Connections and power

| Feature | | Notes |
| --- | --- | --- |
| Wi-Fi | ✅ | Set up in the first-run setup |
| Bluetooth | ✅ | A Nintendo Switch Pro controller pairs; headphones are not tested yet |
| Battery and charging | ✅ | Plugged in while off, the lenses show the battery; USB PD chargers at up to 9 V |
| Sleep | ✅ | |
| Battery life | ❔ | Not measured yet ([#31](https://github.com/overpressure/phoenix/issues/31)) |
| USB-C docks and hubs | ✅ | The headset charges through the dock; USB 2 and USB 3 devices and the dock's Ethernet work |
| Monitor over USB-C | 🟡 | A monitor on a dock's HDMI is detected, but nothing shows on it yet, as on the Steam Frame ([#42](https://github.com/overpressure/phoenix/issues/42)) |

### Games and apps

| Feature | | Notes |
| --- | --- | --- |
| Steam store and library | ✅ | Sign in with your Steam account |
| Linux games | ✅ | |
| Windows games (Proton) | 🟡 | They run; heavy games are limited by the headset's GPU |
| Android apps | ✅ | Through Valve's Lepton, as on the Steam Frame |
| PC VR over Steam Link | ✅ | Picture, sound, tracking and controllers |
| Video playback | 🟡 | Most videos play; HEVC files with B-frames show errors ([#29](https://github.com/overpressure/phoenix/issues/29)) |
| Desktop | ✅ | KDE Plasma in a window in VR, as on the Steam Frame: Steam's + → Launch Program → Desktop; one controller is the whole mouse |

### Installing

| Feature | | Notes |
| --- | --- | --- |
| Install | ✅ | From Windows and Linux; macOS is not tested yet ([#33](https://github.com/overpressure/phoenix/issues/33)) |
| Upgrade to a new release | ✅ | Keeps your data, Wi-Fi, room and Steam sign-in |
| Back to PICO OS | ✅ | From the backup the install made |
| Back to PICO OS without a backup | ✅ | From PICO's own downloads, the build the headset's firmware has; run once, on Windows |

Everything that is still open: [the issues](https://github.com/overpressure/phoenix/issues).

## What you need

- The PICO 4, charged, with PICO OS 5.13.3, 5.13.7 or 5.13.8 (global build).
- A USB-C cable that carries data, not only power.
- A computer with Windows, Linux or macOS, an internet connection and about 10 GB free.
- About half an hour.

## Install

1. Download [overpressure](https://github.com/overpressure/overpressure/releases) and unpack it.
2. Start it: double-click `Install.cmd` on Windows or `Install.command` on macOS, or run `sh install.sh` on Linux.
3. Accept the disclaimer, choose the PICO 4 and click **Download**.
4. Click **Install** and follow the window. It checks your computer and the headset, backs the headset up, then
   installs. In between you switch on developer mode and USB debugging in PICO OS; the headset builds its new
   system itself, over your computer's internet connection.
5. When the window says so, unplug the headset and put it on.

**Upgrade** installs a newer release and keeps your data. **Restore to stock** brings PICO OS back from the
backup; wear the headset on the cable through PICO OS's setup, whose first start may update the lens and fan
controller. Without a backup, **Rescue PICO OS** brings it back from PICO's own downloads
([docs/install.md](docs/install.md#without-a-backup-the-rescue)).

## If something goes wrong

The window says what state the headset is in and what to do next; starting it again goes on where it stopped. If
the headset doesn't answer, hold its power button for about 10 seconds until PICO OS or the system starts, then
start the window again. To report a problem, click **Save logs…** on the PICO 4's page in the window and attach
the file to [an issue](https://github.com/overpressure/phoenix/issues/new/choose); it leaves out your headset's
serial numbers.

Questions and quick help: the [PICO 4 room](https://matrix.to/#/#overpressure-phoenix:matrix.org) and the
[Help room](https://matrix.to/#/#overpressure-help:matrix.org) of the project's Matrix space.

## For developers

How it works, how it is built and how it is tested: [docs/](docs/), starting with
[docs/development.md](docs/development.md).
