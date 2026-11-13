"""Per-earbud battery and Find (ring) for earbuds with Google Fast Pair.

Windows only learns one battery number from earbuds (the hands-free
profile's). Fast Pair earbuds also open an RFCOMM "message stream"
(service df21fe2c-2515-4fdb-8886-f12c4d67927c) to whatever they're connected
to, and report the left bud, right bud and case on it; that's how Android
shows all three. This keeps one message stream open per connected pair of
Fast Pair earbuds, through WinRT (the `winrt` packages), and can send the
stream's ring command.

Message format: group (1 byte), code (1), data length (2, big endian), data.
  0x03 0x01  model ID (3 bytes)            0x03 0x03  battery: left, right, case
  0x03 0x05  active components request     0x03 0x06  active components: bit 0 right, bit 1 left
  0x03 0x09  firmware version (text)       0x04 0x01  ring: bit 0 right, bit 1 left, 0 = stop
  0xFF 0x01  ACK (group, code)             0xFF 0x02  NAK (reason, group, code)
A battery byte is the level in bits 0-6 (0x7F = unknown) and charging in bit 7.

All WinRT work runs on one asyncio loop in a background thread; the plugin
reads snapshot() and calls ring(), which are thread-safe.
"""

import asyncio
import sys
import threading
import time
import uuid

FP_SERVICE = uuid.UUID("df21fe2c-2515-4fdb-8886-f12c4d67927c")
PACKAGES = {   # import name: (pip name, download MB)
    "winrt.runtime": ("winrt-runtime", 0.6),
    "winrt.windows.foundation": ("winrt-Windows.Foundation", 0.3),
    "winrt.windows.foundation.collections": ("winrt-Windows.Foundation.Collections", 0.1),
    "winrt.windows.devices.bluetooth": ("winrt-Windows.Devices.Bluetooth", 0.3),
    "winrt.windows.devices.bluetooth.rfcomm": ("winrt-Windows.Devices.Bluetooth.Rfcomm", 0.1),
    "winrt.windows.devices.enumeration": ("winrt-Windows.Devices.Enumeration", 0.2),
    "winrt.windows.networking": ("winrt-Windows.Networking", 0.1),
    "winrt.windows.networking.sockets": ("winrt-Windows.Networking.Sockets", 0.2),
    "winrt.windows.storage.streams": ("winrt-Windows.Storage.Streams", 0.2),
}
RING = {"right": 0x01, "left": 0x02, "both": 0x03, "stop": 0x00}
RING_LIMIT = 20          # seconds; the ring is stopped after this if nobody does
SCAN_EVERY = 15          # seconds between looks for newly connected earbuds
NAK_REASONS = {0: "these earbuds don't support it", 1: "the earbuds are busy",
               2: "not possible right now (are they in the case?)",
               3: "the earbuds want a phone that set them up with Fast Pair",
               4: "it's already doing that"}


def battery(b):
    """{"level": 0..100 or None, "charging": bool} from a battery byte."""
    level = b & 0x7F
    if level > 100:   # 0x7F: unknown (a bud or the case that isn't reporting)
        return {"level": None, "charging": False}
    return {"level": level, "charging": bool(b & 0x80)}


def message(group, code, data=b""):
    return bytes([group, code]) + len(data).to_bytes(2, "big") + bytes(data)


class _Session:
    """One open message stream."""

    def __init__(self, address, name):
        self.address = address
        self.name = name
        self.writer = None
        self.socket = None
        self.ring_stop = None    # asyncio handle that stops a ring after RING_LIMIT


class FastPair:
    def __init__(self, on_change):
        """on_change() is called from the background thread whenever
        anything in snapshot() changes."""
        self.on_change = on_change
        self._lock = threading.Lock()
        self._state = {}         # address -> dict (see _blank)
        self._sessions = {}      # address -> _Session
        self._loop = None
        self._wake = None

    # Called from the plugin's thread --------------------------------------------

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def snapshot(self):
        """Every pair of earbuds seen since start, newest values; connected or not."""
        with self._lock:
            return [dict(s, left=dict(s["left"]), right=dict(s["right"]), case=dict(s["case"]))
                    for s in self._state.values()]

    def refresh(self):
        """Looks for newly connected earbuds now instead of within SCAN_EVERY."""
        if self._loop:
            self._loop.call_soon_threadsafe(self._wake.set)

    def ring(self, address, which):
        """which: left, right, both or stop."""
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._ring(address, which), self._loop)

    # Background thread ----------------------------------------------------------

    def _run(self):
        asyncio.run(self._main())

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        while True:
            try:
                await self._scan()
            except Exception as e:   # WinRT errors while the radio is off etc.
                print(f"fastpair scan: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            try:
                await asyncio.wait_for(self._wake.wait(), SCAN_EVERY)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def _blank(self, address, name):
        return {"address": address, "name": name, "model": None, "firmware": None,
                "left": battery(0x7F), "right": battery(0x7F), "case": battery(0x7F),
                "active": None, "ringing": 0, "connected": False, "error": "",
                "updated": None}

    def _update(self, address, **fields):
        with self._lock:
            s = self._state.get(address)
            if s is None:
                return
            s.update(fields)
        self.on_change()

    async def _scan(self):
        from winrt.windows.devices.bluetooth import BluetoothConnectionStatus, BluetoothDevice
        from winrt.windows.devices.enumeration import DeviceInformation
        infos = await DeviceInformation.find_all_async_aqs_filter(
            BluetoothDevice.get_device_selector_from_pairing_state(True))
        for info in infos:
            dev = await BluetoothDevice.from_id_async(info.id)
            if dev is None:
                continue
            address = f"{dev.bluetooth_address:012X}"
            if dev.connection_status != BluetoothConnectionStatus.CONNECTED:
                continue
            if address in self._sessions:
                continue
            asyncio.ensure_future(self._open(dev, address))

    async def _open(self, dev, address):
        from winrt.windows.devices.bluetooth import BluetoothCacheMode
        from winrt.windows.devices.bluetooth.rfcomm import RfcommServiceId
        from winrt.windows.networking.sockets import StreamSocket
        from winrt.windows.storage.streams import DataReader, DataWriter, InputStreamOptions
        session = _Session(address, dev.name)
        self._sessions[address] = session
        try:
            found = await dev.get_rfcomm_services_for_id_with_cache_mode_async(
                RfcommServiceId.from_uuid(FP_SERVICE), BluetoothCacheMode.CACHED)
            if found.services.size == 0:
                # Not Fast Pair. Keep the session entry so it isn't asked again
                # until it reconnects.
                session.socket = None
                await self._wait_disconnect(dev)
                return
            service = found.services[0]
            sock = StreamSocket()
            await sock.connect_async(service.connection_host_name,
                                     service.connection_service_name)
            session.socket = sock
            session.writer = DataWriter(sock.output_stream)
            with self._lock:
                old = self._state.get(address)
                self._state[address] = dict(old or self._blank(address, dev.name),
                                            name=dev.name, connected=True, error="")
            self.on_change()
            await self._send(session, message(0x03, 0x05))   # which buds are active?
            reader = DataReader(sock.input_stream)
            reader.input_stream_options = InputStreamOptions.PARTIAL
            buf = b""
            while True:
                n = await reader.load_async(512)
                if n == 0:
                    break
                chunk = bytearray(n)
                reader.read_bytes(chunk)
                buf += bytes(chunk)
                while len(buf) >= 4 and len(buf) >= 4 + int.from_bytes(buf[2:4], "big"):
                    size = int.from_bytes(buf[2:4], "big")
                    self._handle(session, buf[0], buf[1], buf[4:4 + size])
                    buf = buf[4 + size:]
        except Exception as e:
            print(f"fastpair {address}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        finally:
            if session.ring_stop:
                session.ring_stop.cancel()
            if session.socket:
                session.socket.close()
            self._sessions.pop(address, None)
            self._update(address, connected=False, ringing=0, active=None)

    async def _wait_disconnect(self, dev):
        from winrt.windows.devices.bluetooth import BluetoothConnectionStatus
        while dev.connection_status == BluetoothConnectionStatus.CONNECTED:
            await asyncio.sleep(SCAN_EVERY)

    def _handle(self, session, group, code, data):
        a = session.address
        now = time.time()
        if group == 0x03 and code == 0x03 and len(data) >= 2:
            fields = {"left": battery(data[0]), "right": battery(data[1]), "updated": now}
            if len(data) >= 3:
                fields["case"] = battery(data[2])
            self._update(a, **fields)
        elif group == 0x03 and code == 0x01:
            self._update(a, model=data.hex().upper())
        elif group == 0x03 and code == 0x09:
            self._update(a, firmware=data.decode("utf-8", "ignore").strip("\0"))
        elif group == 0x03 and code == 0x06 and data:
            self._update(a, active={"right": bool(data[0] & 1), "left": bool(data[0] & 2)})
        elif group == 0x04 and code == 0x01 and data:
            self._update(a, ringing=data[0], error="")
            if data[0] == 0 and session.ring_stop:
                session.ring_stop.cancel()
                session.ring_stop = None
        elif group == 0xFF and code == 0x02 and len(data) >= 3 and data[1] == 0x04:
            self._update(a, ringing=0, error="Couldn't ring: " +
                         NAK_REASONS.get(data[0], f"refused (reason {data[0]})"))

    async def _send(self, session, data):
        session.writer.write_bytes(data)
        await session.writer.store_async()

    async def _ring(self, address, which):
        session = self._sessions.get(address)
        if not session or not session.writer:
            self._update(address, error="Connect the earbuds to this PC to ring them.")
            return
        try:
            if which != "stop":
                await self._send(session, message(0x03, 0x05))
            await self._send(session, message(0x04, 0x01, [RING[which]]))
            if session.ring_stop:
                session.ring_stop.cancel()
                session.ring_stop = None
            if which != "stop":
                session.ring_stop = self._loop.call_later(
                    RING_LIMIT, lambda: asyncio.ensure_future(self._ring(address, "stop")))
        except Exception as e:
            self._update(address, ringing=0, error=f"Couldn't ring: {e}")
