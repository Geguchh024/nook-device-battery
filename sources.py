"""Battery readings from three places, all without installing anything:

- pnp_batteries(): the DEVPKEY_Bluetooth_Battery property that Windows keeps
  on Bluetooth device nodes (hands-free headsets and earbuds such as AirPods
  and Galaxy Buds report through it; so do many BLE devices once paired).
  Read through SetupAPI with ctypes.
- xinput_batteries(): Xbox / XInput controllers (and a headset plugged into
  one) through XInputGetBatteryInformation. XInput only reports four steps
  (empty, low, medium, full), so those levels are approximate.
- gatt_batteries(): the standard BLE Battery Service (0x180F, characteristic
  0x2A19) read through WinRT from PowerShell (gatt.ps1), for BLE devices that
  don't fill in the property above. Slow (a second or more), so it runs on a
  background thread and rarely.

Each returns a list of dicts: {"key", "name", "level" (0..100), "approx",
"connected", "source", "kind"}."""

import base64
import ctypes
import json
import os
import subprocess
from ctypes import POINTER, byref, c_ubyte, c_ulong, c_ushort, c_void_p, c_wchar_p, wintypes

HERE = os.path.dirname(os.path.abspath(__file__))


class GUID(ctypes.Structure):
    _fields_ = [("d1", c_ulong), ("d2", c_ushort), ("d3", c_ushort), ("d4", c_ubyte * 8)]

    def __init__(self, text=None):
        super().__init__()
        if text:
            ctypes.windll.ole32.CLSIDFromString(c_wchar_p("{" + text.strip("{}") + "}"), byref(self))

    def __str__(self):
        buf = ctypes.create_unicode_buffer(40)
        ctypes.windll.ole32.StringFromGUID2(byref(self), buf, 40)
        return buf.value


class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", c_ulong)]


def _key(fmtid, pid):
    k = DEVPROPKEY()
    k.fmtid = GUID(fmtid)
    k.pid = pid
    return k


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", GUID), ("DevInst", wintypes.DWORD),
                ("Reserved", ctypes.c_size_t)]


KEY_BATTERY = _key("104EA319-6EE2-4701-BD47-8DDBF425BBE5", 2)     # DEVPKEY_Bluetooth_Battery (byte)
KEY_CONNECTED = _key("83DA6326-97A6-4088-9453-A1923F573B29", 15)  # DEVPKEY_Device_IsConnected (bool)
KEY_FRIENDLY = _key("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)   # DEVPKEY_Device_FriendlyName
KEY_CONTAINER = _key("8C7ED206-3F8A-4827-B3AB-AE9E1FAEFC6C", 2)   # DEVPKEY_Device_ContainerId (guid)
KEY_INSTANCE = _key("78C34FC8-104A-4ACA-9EA4-524D52996E57", 256)  # DEVPKEY_Device_InstanceId

DIGCF_PRESENT, DIGCF_ALLCLASSES = 0x2, 0x4
INVALID_HANDLE = c_void_p(-1).value
ROOT_CONTAINER = "{00000000-0000-0000-FFFF-FFFFFFFFFFFF}"

_setupapi = None


def _api():
    global _setupapi
    if _setupapi is None:
        s = ctypes.WinDLL("setupapi", use_last_error=True)
        s.SetupDiGetClassDevsW.argtypes = [c_void_p, c_wchar_p, c_void_p, wintypes.DWORD]
        s.SetupDiGetClassDevsW.restype = c_void_p
        s.SetupDiEnumDeviceInfo.argtypes = [c_void_p, wintypes.DWORD, POINTER(SP_DEVINFO_DATA)]
        s.SetupDiGetDevicePropertyW.argtypes = [c_void_p, POINTER(SP_DEVINFO_DATA), POINTER(DEVPROPKEY),
                                                POINTER(c_ulong), c_void_p, wintypes.DWORD,
                                                POINTER(wintypes.DWORD), wintypes.DWORD]
        s.SetupDiDestroyDeviceInfoList.argtypes = [c_void_p]
        _setupapi = s
    return _setupapi


def _prop(h, data, key):
    """Returns (type, raw bytes) or None."""
    s = _api()
    ptype, need = c_ulong(), wintypes.DWORD()
    buf = ctypes.create_string_buffer(512)
    if not s.SetupDiGetDevicePropertyW(h, byref(data), byref(key), byref(ptype), buf, 512, byref(need), 0):
        return None
    return ptype.value, buf.raw[:need.value]


def _string(raw):
    return raw[1].decode("utf-16-le", "ignore").rstrip("\0") if raw else ""


NAME_SUFFIXES = (" Hands-Free AG Audio", " Hands-Free AG", " Hands-Free", " Avrcp Transport", " Stereo",
                 " A2DP SNK", " LE")


def clean_name(name):
    for suffix in NAME_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()


def kind_of(name):
    n = name.lower()
    if any(w in n for w in ("airpods", "buds", "earbud", "earphone", "jabra elite", "wf-", "freebuds", "pods")):
        return "earbuds"
    if any(w in n for w in ("headset", "headphone", "wh-", "bose", "beats", "arctis", "hyperx", "qc")):
        return "headphones"
    if any(w in n for w in ("controller", "gamepad", "xbox", "dualsense", "dualshock", "joy-con")):
        return "controller"
    if "mouse" in n or "mx " in n:
        return "mouse"
    if "keyboard" in n or "keys" in n:
        return "keyboard"
    if "pen" in n or "stylus" in n:
        return "pen"
    return "other"


def pnp_batteries():
    s = _api()
    h = s.SetupDiGetClassDevsW(None, None, None, DIGCF_PRESENT | DIGCF_ALLCLASSES)
    if not h or h == INVALID_HANDLE:
        return []
    found = {}          # container -> reading
    names = {}          # container -> shortest friendly name seen on any of its nodes
    connected = {}      # container -> any node says connected
    try:
        data = SP_DEVINFO_DATA()
        data.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
        i = 0
        while s.SetupDiEnumDeviceInfo(h, i, byref(data)):
            i += 1
            cont = _prop(h, data, KEY_CONTAINER)
            container = str(GUID.from_buffer_copy(cont[1][:16])) if cont and len(cont[1]) >= 16 else None
            if container == ROOT_CONTAINER:   # shared by every built-in device
                container = None
            bat = _prop(h, data, KEY_BATTERY)
            if not container and not bat:
                continue
            key = container or _string(_prop(h, data, KEY_INSTANCE))
            name = clean_name(_string(_prop(h, data, KEY_FRIENDLY)))
            if name and (key not in names or len(name) < len(names[key])):
                names[key] = name
            conn = _prop(h, data, KEY_CONNECTED)
            if conn and conn[1]:
                connected[key] = connected.get(key, False) or conn[1][0] != 0
            if bat and bat[1]:
                level = bat[1][0]
                if level <= 100:
                    found[key] = level
    finally:
        s.SetupDiDestroyDeviceInfoList(h)
    out = []
    for key, level in found.items():
        name = names.get(key, "Bluetooth device")
        out.append({"key": "bt:" + key, "name": name, "level": level, "approx": False,
                    "connected": connected.get(key, True), "source": "Bluetooth", "kind": kind_of(name)})
    return out


class XINPUT_BATTERY_INFORMATION(ctypes.Structure):
    _fields_ = [("BatteryType", c_ubyte), ("BatteryLevel", c_ubyte)]


XINPUT_LEVELS = {0: (5, "Empty"), 1: (15, "Low"), 2: (50, "Medium"), 3: (100, "Full")}
_xinput = False


def xinput_batteries():
    global _xinput
    if _xinput is False:
        _xinput = None
        for dll in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                _xinput = ctypes.WinDLL(dll)
                break
            except OSError:
                continue
    if not _xinput or not hasattr(_xinput, "XInputGetBatteryInformation"):
        return []
    out = []
    for user in range(4):
        for devtype, label in ((0, "Controller"), (1, "Controller headset")):
            info = XINPUT_BATTERY_INFORMATION()
            if _xinput.XInputGetBatteryInformation(user, devtype, byref(info)) != 0:
                break
            if info.BatteryType == 0:          # disconnected
                continue
            name = f"{label} {user + 1}"
            if info.BatteryType == 1:          # wired: no battery to worry about
                out.append({"key": f"xi:{user}:{devtype}", "name": name, "level": None, "approx": True,
                            "connected": True, "source": "XInput · wired", "kind": "controller"})
                continue
            level, word = XINPUT_LEVELS.get(info.BatteryLevel, (None, "Unknown"))
            out.append({"key": f"xi:{user}:{devtype}", "name": name, "level": level, "approx": True,
                        "word": word, "connected": True, "source": "XInput", "kind": "controller"})
    return out


def gatt_batteries(timeout=25):
    """Runs gatt.ps1 (passed as an encoded command, so the script execution
    policy doesn't matter); returns [] if PowerShell or WinRT isn't there."""
    with open(os.path.join(HERE, "gatt.ps1"), encoding="utf-8") as f:
        script = base64.b64encode(f.read().encode("utf-16-le")).decode("ascii")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", script],
                           capture_output=True, timeout=timeout, creationflags=0x08000000)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in r.stdout.decode("utf-8", "ignore").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and isinstance(d.get("level"), int) and 0 <= d["level"] <= 100:
            name = clean_name(d.get("name") or "BLE device")
            out.append({"key": "ble:" + str(d.get("id", name)), "name": name, "level": d["level"],
                        "approx": False, "connected": bool(d.get("connected", True)),
                        "source": "Bluetooth LE", "kind": kind_of(name)})
    return out
