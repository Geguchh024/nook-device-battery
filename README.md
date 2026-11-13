# Device Battery

> Battery levels for your earbuds, headsets, mice, keyboards and Xbox
> controllers in one place. Fast Pair earbuds show the **left bud, right bud
> and case**, and you can **ring them** to find them.

![Platform](https://img.shields.io/badge/platform-Windows-0078D6)
![Python](https://img.shields.io/badge/python-3.8%2B-3776AB)
![Version](https://img.shields.io/badge/version-2.0.0-7C5CFF)
![Plugin for](https://img.shields.io/badge/plugin%20for-Nook%20%2F%20Fenn-111)

A plugin for **Nook**, the floating notch for Windows (and its successor
**Fenn**).

| | |
|---|---|
| **Tile** | small (wide and large also supported) |
| **Page** | 600 × 420 |
| **Low battery warning** | below 20% (10% or 30% in Settings) |
| **Optional download** | 2 MB, for per-bud Fast Pair readings |

## Highlights

- **Per-bud battery** for Google Fast Pair earbuds (realme, Pixel Buds, JBL,
  Sony, Jabra and more): left, right and case, with charging state.
- **Find my earbuds.** Ring the left bud, the right bud, or both.
- **Bluetooth and BLE devices:** headsets, mice, keyboards and trackers.
- **Xbox and XInput controllers**, including a headset plugged into one.
- **Low battery alerts.** The tile turns red, the pill shows a warning, and
  the page can pop open when a device runs low.

## Install

Clone the repo into your plugins folder. The folder name must be `device-battery`:

```powershell
# Nook
git clone https://github.com/Geguchh024/nook-device-battery "$env:APPDATA\Nook\plugins\device-battery"
# Fenn
git clone https://github.com/Geguchh024/nook-device-battery "$env:APPDATA\Fenn\plugins\device-battery"
```

Most devices work right away. For per-bud readings and Find on Fast Pair
earbuds, open the page and press **Download and set up**.

## Earbuds: each bud, the case, and Find

Windows only gets one battery number from earbuds, through the hands-free
profile. Earbuds with Google **Fast Pair** also open a *message stream* to
whatever they're connected to and report each bud and the case on it. If
Android shows each bud separately, your earbuds likely have it. This plugin
reads that stream, so the page shows:

- **Left, Right and Case** as rings. You'll see *Charging* when they are, and
  *Not in use* for a bud that's in the case. The case's level only shows
  while a bud is docked in it, because a case can't report on its own.
- **Find:** ring the left bud, the right one, or both. Take them out of your
  ears first. They ring until you press **Stop ringing**, or for 20 seconds
  at most.
- The model's firmware version.

This needs a one-time **2 MB** download of the Windows Bluetooth bindings
(the `winrt` packages). They are installed with `pip install --target` into
this plugin's data folder when you press **Download and set up**. Nothing
changes in your Python install, and nothing is downloaded before you press
it. The earbuds must be connected to this PC. Earbuds without Fast Pair keep
showing Windows' single number.

## Everything else

| Source | Devices | How it's read |
|---|---|---|
| Fast Pair message stream | Fast Pair earbuds: each bud, the case, ring | RFCOMM through WinRT (`fastpair.py`), while connected |
| Bluetooth battery property | most earbuds and headsets (hands-free profile); many paired BLE devices | `DEVPKEY_Bluetooth_Battery` on the device node, through SetupAPI with `ctypes` |
| BLE Battery Service | mice, keyboards, trackers and other BLE devices with the standard GATT service (0x180F) | WinRT from PowerShell (`gatt.ps1`), only for connected devices |
| XInput | Xbox and other XInput controllers, and a headset plugged into one | `XInputGetBatteryInformation`; reports empty, low, medium or full only |

## The tile

The tile is drawn to its exact size, so it fills and centres at any size:

| Size | Shows |
|---|---|
| **Small** | One dial with the lowest level; for earbuds also `L40 R30` and the name |
| **Wide** | For earbuds, Left and Right dials with the name and case; otherwise the lowest device |
| **Large** | Earbuds as Left / Right / Case dials, other devices as bars below |

While earbuds ring, the tile and the collapsed pill show a speaker.

Anything **below 20%** turns red and puts a warning glyph on the collapsed
pill. In **Settings** on the page you can:

- move the threshold to 10% or 30%
- have the page pop open when a device drops below it
- show paired devices that aren't connected, with their last known level

## How often it checks

- Bluetooth properties and controllers: every 30 seconds
- BLE GATT: every 5 minutes, and when the page opens
- Fast Pair earbuds: they push their own updates when a level changes

## Files

| File | What it is |
|---|---|
| `main.py` | Merging the sources, the tile and page views, events |
| `fastpair.py` | The Fast Pair message stream: battery, active buds, firmware, ring |
| `sources.py`, `gatt.ps1` | Bluetooth property, BLE GATT and XInput readings |
| `setup_flow.py`, `deps.py` | The one-time download card (shared with Background Remover) |
| `nook.py` | The Nook plugin helper |
| `plugin.toml` | Plugin manifest |

## Requirements

- Windows 10 or 11 with Bluetooth
- Python 3.8+ on `PATH` as `python` (3.9+ for the Fast Pair features)
- Windows PowerShell (built in)
