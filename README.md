# Device Battery

Battery levels for the things you wear and hold, with **left bud, right
bud and case** for Fast Pair earbuds, and **Find** to ring them.

## Earbuds: each bud, the case, and Find

Windows only gets one battery number from earbuds (through the hands-free
profile). Earbuds with Google **Fast Pair** (realme, Pixel Buds, JBL, Sony,
Jabra and many others: if Android shows each bud separately, they likely
have it) also open a *message stream* to whatever they're connected to and
report each bud and the case on it. This plugin reads that stream, so the
page shows:

- **Left, Right and Case** as rings, with *Charging* when they are, *Not in
  use* for a bud that's in the case, and the case's level while a bud is
  docked in it (a case can't report on its own).
- **Find**: ring the left bud, the right one, or both. Take them out of
  your ears first. They ring until you press **Stop ringing**, or for 20
  seconds at most.
- The model's firmware version.

It needs a one-time **2 MB** download (Windows Bluetooth bindings, the
`winrt` packages), installed with `pip install --target` into this plugin's
data folder when you press **Download and set up**. Nothing changes in your
Python install and nothing is downloaded before you press it. It also needs
the earbuds connected to this PC; earbuds without Fast Pair keep showing
Windows' single number.

## On Fenn's taskbar

With Fenn's taskbar on, the plugin puts its readings beside the tray:

- **Each bud and the case** as their own segments for Fast Pair earbuds
  (`L 80%`, `R 76%`, the case), and one segment for every other device
  (mouse, keyboard, headset, controller), each with a level bar that turns
  orange and red as it runs down. A charging bud gets a filled icon.
- **Hover** for a card with a ring per bud and a bar per device.
- **Click** for the same device cards as the page, Find included.
- **Right click**: Check again, Bluetooth settings, Hide from taskbar.

Settings → Taskbar → *Show* picks every device or only the lowest one.

## Everything else

| Source | Devices | How it's read |
|---|---|---|
| Fast Pair message stream | Fast Pair earbuds: each bud, the case, ring | RFCOMM through WinRT (`fastpair.py`), while connected |
| Bluetooth battery property | most earbuds and headsets (hands-free profile); many paired BLE devices | `DEVPKEY_Bluetooth_Battery` on the device node, through SetupAPI with `ctypes` |
| BLE Battery Service | mice, keyboards, trackers and other BLE devices with the standard GATT service (0x180F) | WinRT from PowerShell (`gatt.ps1`), only for connected devices |
| XInput | Xbox and other XInput controllers, and a headset plugged into one | `XInputGetBatteryInformation`; only empty, low, medium and full |

## Tile

Drawn to the tile's exact size, so it fills and centres in any size:

- **Small**: one dial with the lowest level; for earbuds also `L40 R30`
  and the name.
- **Wide**: for earbuds, Left and Right dials with the name and case;
  otherwise the lowest device.
- **Large**: earbuds as Left / Right / Case dials, other devices as bars
  below.
- While earbuds ring, the tile and the collapsed pill show a speaker.

Anything **below 20%** turns red and puts a warning glyph on the collapsed
pill. In **Settings** on the page you can move that to 10% or 30%, have the
page pop open when a device drops below it, and show paired devices that
aren't connected (with their last known level).

Bluetooth properties and controllers are re-read every 30 seconds and the
slower BLE read every 5 minutes and when the page opens. Fast Pair earbuds
send their own updates when a level changes.

## Files

| File | What it is |
|---|---|
| `main.py` | Merging the sources, the tile and page views, events |
| `fastpair.py` | The Fast Pair message stream: battery, active buds, firmware, ring |
| `sources.py`, `gatt.ps1` | Bluetooth property, BLE GATT and XInput readings |
| `setup_flow.py`, `deps.py` | The one-time download card (shared with Background Remover) |
| `fenn.py` | The Fenn helper |

Needs Python 3.9+ as `python` for the Fast Pair part (3.8+ for the rest)
and Windows PowerShell (built in).
