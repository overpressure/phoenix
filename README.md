# PICO 4

**Linux and Valve's SteamVR on the PICO 4 (2022, Snapdragon XR2), installed with
[overpressure](https://github.com/overpressure/overpressure).** It replaces PICO OS with a system built on Valve's
Steam Frame software: SteamVR, the Steam client and its games run on the headset itself, without a PC. The way back
to PICO OS is part of it.

| | |
| --- | --- |
| **Status** | Beta: for owners who are fine with an early release and can report what they find |
| **Newest release** | [1.0 beta](https://github.com/overpressure/phoenix/releases) |
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
| Lens distance (IPD) | ✅ | The wheel moves the lenses; the picture follows |
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
| Battery and charging | ✅ | Plugged in while off, the lenses show the battery |
| Sleep | ✅ | |
| Battery life | ❔ | Not measured yet ([#31](https://github.com/overpressure/phoenix/issues/31)) |

### Games and apps

| Feature | | Notes |
| --- | --- | --- |
| Steam store and library | ✅ | Sign in with your Steam account |
| Linux games | ✅ | |
| Windows games (Proton) | 🟡 | They run; heavy games are limited by the headset's GPU |
| Android apps | ✅ | Through Valve's Lepton, as on the Steam Frame |
| PC VR over Steam Link | ✅ | Picture, sound, tracking and controllers |
| Video playback | 🟡 | Most videos play; HEVC files with B-frames show errors ([#29](https://github.com/overpressure/phoenix/issues/29)) |

### Installing

| Feature | | Notes |
| --- | --- | --- |
| Install | ✅ | From Windows and Linux; macOS is not tested yet ([#33](https://github.com/overpressure/phoenix/issues/33)) |
| Upgrade to a new release | ✅ | Keeps your data, Wi-Fi, room and Steam sign-in |
| Back to PICO OS | ✅ | From the backup the install made |

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
controller.

## If something goes wrong

The window says what state the headset is in and what to do next; starting it again goes on where it stopped. If
the headset doesn't answer, hold its power button for about 10 seconds until PICO OS or the system starts, then
start the window again. To report a problem, [open an issue](https://github.com/overpressure/phoenix/issues/new/choose)
and attach the bundle `python -m overpressure dev support-bundle` makes; it leaves out your headset's serial
numbers.

## For developers

How it works, how it is built and how it is tested: [docs/](docs/), starting with
[docs/development.md](docs/development.md).
