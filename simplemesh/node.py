"""The MeshCore node on the serial port, and everything the web page shows about it.

MeshNode runs the (asyncio) MeshCore client on a thread of its own, keeps the nodes and messages in a Store, and
publishes what changes to a Hub as small JSON events the page applies in order. Every event is an upsert, so a page
that sees one twice (once in its snapshot, once live) ends up the same. The page's own requests (send a message,
advertise, ...) come in from the HTTP threads and are handed to the loop.

Events: {"status"}, {"self"}, {"channels"}, {"nodes": [...]}, {"gone": [keys]}, {"msgs": [...]}, {"rxs": [...]},
{"flash"} (a firmware update's progress) and {"notice": text}.
"""
from __future__ import annotations

import asyncio
import glob
import hashlib
import os
import re
import threading
import time
import uuid
from collections import deque
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Callable, Coroutine

from meshcore import EventType, MeshCore

from piwebkit.hub import Hub

from .config import Config
from .firmware import Updater
from .remote import Remote
from .store import Store

TYPE_NAMES = {0: "unknown", 1: "companion", 2: "repeater", 3: "room", 4: "sensor"}
MAX_TEXT = 150            # bytes the firmware takes in one message; a channel message also carries our name
HEARTBEAT_S = 30
CONTACT_RESYNC_S = 900
RX_BACKLOG = 150
MAX_MESSAGES_SCAN = 2000  # how far back the messages are looked through for people you have messaged
PURGE_MAX = 400  # contacts removed from the node in one go
PURGE_EVERY_S = 300  # the least often the node is tidied by itself
NEW_CONTACT_S = 7 * 86400  # a contact someone added by hand is left alone this long
PAUSED = "Paused while the firmware is updated."
CLOCK_SANE = 1_700_000_000  # a clock before late 2023 hasn't been set
BANDWIDTHS = (7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125.0, 250.0, 500.0)  # kHz the radio chip can do
PRESETS = [  # what the MeshCore community runs in each region; check what the mesh near you uses before relying on one
    {"name": "USA / Canada", "freq": 910.525, "bw": 62.5, "sf": 7, "cr": 5},
    {"name": "EU / UK (narrow)", "freq": 869.618, "bw": 62.5, "sf": 8, "cr": 8},
    {"name": "Australia", "freq": 915.8, "bw": 250.0, "sf": 10, "cr": 5},
    {"name": "New Zealand", "freq": 917.375, "bw": 250.0, "sf": 11, "cr": 5}]


class MeshError(Exception):
    """A request the node can't do; the text is shown to the person."""


def candidate_ports() -> list[str]:
    """USB serial devices that could be a node. The Pi's own UART pins (/dev/serial0 and friends) are never guessed,
    since something else may own them: pick one in Settings if the node is wired there."""
    by_id = sorted(glob.glob("/dev/serial/by-id/*"))
    return by_id or sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))


def all_ports() -> list[str]:
    return candidate_ports() + [p for p in sorted(glob.glob("/dev/serial0") + glob.glob("/dev/ttyAMA*")) if p not in candidate_ports()]


def battery_percent(mv: int) -> int:
    """A rough LiPo curve: 3.3 V empty, 4.2 V full."""
    return max(0, min(100, round((mv - 3300) / 9)))


def _hops(plen) -> int | None:
    """Hops a packet took. The library reports 255 for 'direct': no repeater in between."""
    if plen is None or plen < 0:
        return None
    return 0 if plen == 255 else plen


def _pos(lat, lon) -> dict:
    ok = (lat or lon) and abs(lat) <= 90 and abs(lon) <= 180
    return {"lat": lat, "lon": lon} if ok else {"lat": None, "lon": None}


class MeshNode:
    def __init__(self, cfg: Config, hub: Hub, store: Store,
                 connector: Callable[[str, int], Coroutine] | None = None,
                 ports: Callable[[], list[str]] = candidate_ports, retry_s: float = 5.0,
                 fetch=None, flasher=None) -> None:
        self.cfg, self.hub, self.store = cfg, hub, store
        self.connector = connector or self._serial
        self.ports, self.retry_s = ports, retry_s
        self.loop: asyncio.AbstractEventLoop | None = None
        self.mc: MeshCore | None = None
        self._thread: threading.Thread | None = None
        self._tasks: set[asyncio.Task] = set()
        self._lost: asyncio.Event | None = None
        self._stopping = False
        self._lock = threading.RLock()
        self._status: dict = {"connected": False, "text": "Starting..."}
        self._self: dict = {}
        self._channels: dict[int, str] = {}
        self._rx: deque[dict] = deque(maxlen=RX_BACKLOG)
        self._last_purge = 0.0
        self._contacts_changed: asyncio.Event | None = None
        self._lastmod = 0
        self._port = ""
        self._secrets: dict[int, str] = {}
        self._held = False        # a firmware update has the serial port
        self._connecting = False
        self.remote = Remote(self)
        self.firmware = Updater(self, hub, fetch=fetch, flasher=flasher)

    # --- lifecycle ----------------------------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True, name="meshnode")
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        if self.loop and self.loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop).result(5)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        self.store.save(force=True)

    def reconnect(self) -> None:
        """Drop the connection and look again (the port or speed was changed)."""
        if self.loop and self._lost:
            self.loop.call_soon_threadsafe(self._lost.set)

    def pause(self, timeout: float = 15.0) -> None:
        """Let go of the serial port (a firmware update needs it) and stay away until resume()."""
        self._held = True
        self.reconnect()
        end = time.time() + timeout
        while time.time() < end:
            if self.mc is None and not self._connecting:
                return
            time.sleep(0.05)
        self._held = False
        raise MeshError("Couldn't let go of the node's serial port.")

    def resume(self) -> None:
        self._held = False

    def serial_device(self) -> str:
        """The port the node is (or was last) on, for the firmware updater."""
        port = self._port or self.cfg.serial_port or next(iter(self.ports()), "")
        if not port:
            raise MeshError("No USB serial device found. Plug the node into this machine first.")
        return port

    async def _shutdown(self) -> None:
        if self._lost:
            self._lost.set()
        if self.mc:
            try:
                await asyncio.wait_for(self.mc.disconnect(), 3)
            except Exception:
                pass

    async def _serial(self, port: str, baud: int) -> MeshCore | None:
        return await MeshCore.create_serial(port, baud, only_error=True)

    async def _main(self) -> None:
        self.loop = asyncio.get_running_loop()
        asyncio.create_task(self._saver())
        while not self._stopping:
            mc = await self._connect()
            if mc is not None and self._held:  # the update asked for the port while we were still opening it
                await self._close(mc)
                mc = None
            if mc is None:
                await asyncio.sleep(self.retry_s)
                continue
            self.mc = mc
            self._lost = asyncio.Event()
            try:
                await self._setup(mc)
                self._set_status(True, f"Connected on {self._port}")
                await self._watch(mc)
            except Exception as e:  # a node that vanishes mid-setup lands here; the loop reconnects
                self._set_status(False, f"Lost the node: {e or type(e).__name__}")
            if not self._stopping:
                self._set_status(False, PAUSED if self._held else "Lost the node. Looking for it again...")
            await self._shutdown()
            self.mc = None
            for t in list(self._tasks):
                t.cancel()
            if not self._stopping:
                await asyncio.sleep(self.retry_s)

    @staticmethod
    async def _close(mc: MeshCore) -> None:
        try:
            await asyncio.wait_for(mc.disconnect(), 3)
        except Exception:
            pass

    async def _connect(self) -> MeshCore | None:
        if self._held:
            self._set_status(False, PAUSED)
            return None
        self._connecting = True
        try:
            return await self._try_ports()
        finally:
            self._connecting = False

    async def _try_ports(self) -> MeshCore | None:
        wanted = [self.cfg.serial_port] if self.cfg.serial_port else self.ports()
        if not wanted:
            self._set_status(False, "No USB serial device found. Plug the node into the Pi, or choose its port in Settings.")
            return None
        for port in wanted:
            self._set_status(False, f"Connecting to {port}...")
            try:
                mc = await self.connector(port, self.cfg.baud)
            except Exception as e:
                self._set_status(False, f"Could not open {port}: {e}")
                continue
            if mc is not None:
                self._port = port
                return mc
            self._set_status(False, f"{port} did not answer. The node needs the companion (USB serial) firmware.")
        return None

    async def _saver(self) -> None:
        while True:
            await asyncio.sleep(5)
            await asyncio.get_running_loop().run_in_executor(None, self.store.save)

    # --- setting up a connection --------------------------------------------------------------------

    async def _setup(self, mc: MeshCore) -> None:
        for et, fn in ((EventType.CONTACT_MSG_RECV, self._on_dm), (EventType.CHANNEL_MSG_RECV, self._on_chan),
                       (EventType.RX_LOG_DATA, self._on_rx), (EventType.NEW_CONTACT, self._on_new_contact),
                       (EventType.ADVERTISEMENT, self._on_advert), (EventType.PATH_UPDATE, self._on_advert),
                       (EventType.BATTERY, self._on_battery), (EventType.DISCONNECTED, self._on_disconnect),
                       (EventType.CONTACTS_FULL, self._on_full)):
            mc.subscribe(et, fn)
        self._contacts_changed = asyncio.Event()
        self._lastmod = 0
        self._publish_self(mc.self_info)
        info = await mc.commands.send_device_query()
        if info and info.type != EventType.ERROR:
            self._self.update(model=info.payload.get("model", ""), version=info.payload.get("ver", ""),
                              build=info.payload.get("fw_build", ""))
            self._publish_self(mc.self_info, max_channels=info.payload.get("max_channels", 8), max_contacts=info.payload.get("max_contacts"))
        if time.time() > CLOCK_SANE:  # the node has no clock of its own; other nodes ignore messages that look old
            await mc.commands.set_time(int(time.time()))
        await self._read_channels(mc)
        await self._sync_contacts(full=True)
        await mc.commands.get_bat()
        await mc.start_auto_message_fetching()
        self._spawn(self._contact_loop())

    def _publish_self(self, si: dict, max_channels: int | None = None, max_contacts: int | None = None) -> None:
        with self._lock:
            if max_channels:
                self._self["max_channels"] = max_channels
            if max_contacts:
                self._self["max_contacts"] = max_contacts
            self._self.update({
                "name": si.get("name", ""), "key": si.get("public_key", ""), "type": si.get("adv_type", 0),
                "tx_power": si.get("tx_power"), "max_tx_power": si.get("max_tx_power"),
                "freq": si.get("radio_freq"), "bw": si.get("radio_bw"), "sf": si.get("radio_sf"), "cr": si.get("radio_cr"),
                "manual_add": bool(si.get("manual_add_contacts")), "loc_policy": si.get("adv_loc_policy", 0),
                "tel_base": si.get("telemetry_mode_base", 0), "tel_loc": si.get("telemetry_mode_loc", 0),
                "tel_env": si.get("telemetry_mode_env", 0),
                **_pos(si.get("adv_lat") or 0, si.get("adv_lon") or 0)})
            ev = {"self": dict(self._self)}
        self.hub.publish(ev, keep=False)

    async def _read_channels(self, mc: MeshCore) -> None:
        found, secrets = {}, {}
        for i in range(int(self._self.get("max_channels") or 8)):
            res = await mc.commands.get_channel(i)
            if res and res.type == EventType.CHANNEL_INFO and res.payload.get("channel_name"):
                found[i] = res.payload["channel_name"]
                secrets[i] = bytes(res.payload.get("channel_secret") or b"").hex()
        with self._lock:
            self._channels, self._secrets = found, secrets
        self.hub.publish(self._channels_event(), keep=False)

    def _channels_event(self) -> dict:
        return {"channels": [{"idx": i, "name": n, "secret": self._secrets.get(i, "")} for i, n in sorted(self._channels.items())]}

    def _spawn(self, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _watch(self, mc: MeshCore) -> None:
        """Stay until the node stops answering (or says it is gone)."""
        misses = 0
        while not self._stopping:
            try:
                await asyncio.wait_for(self._lost.wait(), HEARTBEAT_S)
                return
            except asyncio.TimeoutError:
                pass
            res = await mc.commands.get_bat()
            misses = misses + 1 if not res or res.type == EventType.ERROR else 0
            if misses >= 2:
                return

    # --- contacts and nodes -------------------------------------------------------------------------

    async def _sync_contacts(self, full: bool) -> None:
        mc = self.mc
        res = await mc.commands.get_contacts(lastmod=0 if full else self._lastmod)
        if not res or res.type == EventType.ERROR:
            return
        self._lastmod = res.attributes.get("lastmod", self._lastmod)
        changed = []
        for c in mc.contacts.values():
            fields = {
                "name": c.get("adv_name") or c["public_key"][:8], "type": c.get("type", 0), "saved": True,
                "hops": c.get("out_path_len", -1), "path": c.get("out_path", ""),
                "hash_size": max(c.get("out_path_hash_mode", 0), 0) + 1, "advert": c.get("last_advert", 0),
                **_pos(c.get("adv_lat") or 0, c.get("adv_lon") or 0)}
            known = self.store.node(c["public_key"]) or {}
            if fields["hops"] >= 0:  # a route the node has learned (0: direct): remember it for when it loses it
                if (known.get("last_hops"), known.get("last_path"), known.get("last_hash")) != (fields["hops"], fields["path"], fields["hash_size"]):
                    fields.update(last_hops=fields["hops"], last_path=fields["path"], last_hash=fields["hash_size"], last_path_at=time.time())
            n, did = self._put(c["public_key"], fields)
            if did:
                changed.append(n)
        if full:  # the node forgot some (or you removed them in another app): keep them as nodes we merely heard
            for n in self.store.all_nodes():
                if n.get("saved") and n["key"] not in mc.contacts:
                    changed.append(self.store.put_node(n["key"], {"saved": False}))
        if changed:
            self.hub.publish({"nodes": changed}, keep=False)
        await self._auto_purge()

    def _put(self, key: str, fields: dict) -> tuple[dict, bool]:
        before = self.store.node(key)
        node = self.store.put_node(key, fields)
        return node, node != before

    async def _contact_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._contacts_changed.wait(), CONTACT_RESYNC_S)
                await asyncio.sleep(1.5)  # a burst of adverts is one sync
                full = False
            except asyncio.TimeoutError:
                full = True
            self._contacts_changed.clear()
            await self._sync_contacts(full)

    def _touch(self, key: str, **extra) -> None:
        node, _ = self._put(key, {"heard": time.time(), **extra})
        self.hub.publish({"nodes": [node]}, keep=False)

    async def _on_advert(self, event) -> None:
        key = event.payload.get("public_key", "")
        if key:
            self._touch(key)
        self._contacts_changed.set()

    async def _on_new_contact(self, event) -> None:
        """A node we haven't saved advertised itself (the node is set to ask before adding)."""
        c = event.payload
        self._touch(c["public_key"], name=c.get("adv_name") or c["public_key"][:8], type=c.get("type", 0),
                    advert=c.get("last_advert", 0), pending=True, **_pos(c.get("adv_lat") or 0, c.get("adv_lon") or 0))

    async def _on_full(self, event) -> None:
        if await self._auto_purge():
            return
        self._notice("The node's contact list is full: new nodes can't be saved until you remove some.")

    def _notice(self, text: str) -> None:
        self.hub.publish({"notice": text}, keep=False)

    # --- what arrives -------------------------------------------------------------------------------

    def _sender(self, prefix: str) -> str:
        n = self.store.find(prefix)
        return n["name"] if n else prefix

    async def _on_dm(self, event) -> None:
        p = event.payload
        prefix = p["pubkey_prefix"]
        if p.get("txt_type") == 1:  # a repeater's reply to a command we sent (Remote waits for it)
            self.remote.reply(prefix, p.get("text", ""))
            return
        text = p.get("text", "")
        sender = self._sender(prefix)
        if p.get("txt_type") == 2:  # a room post: the room relays it, the author is only named by a 4 byte tag
            author = self.store.find(p.get("signature", ""))
            sender = author["name"] if author else p.get("signature", "") or sender
        self._add_message({
            "id": self._mid("dm", prefix, p.get("sender_timestamp", 0), text), "kind": "dm", "peer": prefix,
            "dir": "in", "from": sender, "text": text, "snr": p.get("SNR"), "hops": _hops(p.get("path_len")),
            "ts": self._when(p.get("sender_timestamp", 0))})
        self._touch_prefix(prefix)

    async def _on_chan(self, event) -> None:
        p = event.payload
        full = p.get("text", "")
        sender, sep, text = full.partition(": ")  # channel messages arrive as "name: text"
        if not sep:
            sender, text = "", full
        idx = p.get("channel_idx", 0)
        self._add_message({
            "id": self._mid("chan", idx, p.get("sender_timestamp", 0), full), "kind": "chan", "chan": idx,
            "dir": "in", "from": sender, "text": text, "snr": p.get("SNR"), "hops": _hops(p.get("path_len")),
            "ts": self._when(p.get("sender_timestamp", 0))})

    def _touch_prefix(self, prefix: str) -> None:
        n = self.store.find(prefix)
        if n:
            self._touch(n["key"])

    @staticmethod
    def _when(sent: int) -> float:
        """When a message was sent: the sender's clock if it is close to ours (a message the node held for us is older
        than when it reaches us), else now."""
        t = time.time()
        return float(sent) if t - 3 * 86400 <= sent <= t + 60 else t

    @staticmethod
    def _mid(*parts) -> str:
        return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]

    def _add_message(self, m: dict) -> dict | None:
        m = {"ts": time.time(), **m}
        if not self.store.add_message(m):
            return None
        self.hub.publish({"msgs": [m]}, keep=False)
        return m

    async def _on_rx(self, event) -> None:
        d = event.payload
        kind = d.get("payload_typename")
        if not kind:
            return
        rx = {"t": d.get("recv_time") or time.time(), "snr": d.get("snr"), "rssi": d.get("rssi"), "kind": kind,
              "route": d.get("route_typename", ""), "hops": d.get("path_len", 0), "path": d.get("path", ""),
              "hash_size": d.get("path_hash_size", 1), "size": d.get("payload_length", 0)}
        if d.get("chan_name"):
            rx["chan"] = d["chan_name"]
        if d.get("adv_key"):
            rx["key"] = d["adv_key"]
            rx["name"] = d.get("adv_name", "")
            fields = {"heard": rx["t"], "snr": d.get("snr"), "rssi": d.get("rssi"), "advert": d.get("adv_timestamp", 0),
                      "heard_hops": d.get("path_len")}  # the SNR above is of the last hop: the node's own only when this is 0
            if d.get("adv_name"):
                fields.update(name=d["adv_name"], type=d.get("adv_type", 0))
            if "adv_lat" in d:
                fields.update(_pos(d["adv_lat"], d.get("adv_lon", 0)))
            known = self.store.node(d["adv_key"])
            if known or d.get("adv_name"):  # an advert with no name and no record isn't worth listing
                node, _ = self._put(d["adv_key"], fields)
                self.hub.publish({"nodes": [node]}, keep=False)
        self._rx.append(rx)
        self.hub.publish({"rxs": [rx]}, keep=False)

    async def _on_battery(self, event) -> None:
        mv = event.payload.get("level", 0)
        with self._lock:
            self._status.update(battery_mv=mv, battery=battery_percent(mv) if mv else None)
            ev = {"status": dict(self._status)}
        self.hub.publish(ev, keep=False)

    async def _on_disconnect(self, event) -> None:
        if self._lost:
            self._lost.set()

    # --- what the page asks for ---------------------------------------------------------------------

    def _set_status(self, connected: bool, text: str) -> None:
        with self._lock:
            if connected and not self._status.get("connected"):
                self._status["since"] = time.time()  # nodes not heard since then show grey on the page
            self._status.update(connected=connected, text=text, port=self._port)
            if not connected:
                self._status.update(battery=None, battery_mv=None)
            ev = {"status": dict(self._status)}
        self.hub.publish(ev, keep=False)

    def intro(self) -> list[dict]:
        """Everything a page needs to draw itself from nothing."""
        with self._lock:
            evs: list[dict] = [{"status": dict(self._status)}, {"self": dict(self._self)}, self._channels_event()]
        evs.append({"nodes": self.store.all_nodes()})
        evs.append({"msgs": self.store.recent_messages()})
        evs.append({"rxs": list(self._rx)})
        evs.append({"flash": self.firmware.state()})
        return evs

    def request(self, factory: Callable[[], Coroutine], timeout: float = 20):
        """Run a coroutine on the node's loop from another thread and wait for its result."""
        if not self.loop or not self.mc or not self._status["connected"]:
            raise MeshError("The node isn't connected right now.")
        try:
            return asyncio.run_coroutine_threadsafe(factory(), self.loop).result(timeout)
        except (asyncio.TimeoutError, FutureTimeout):
            raise MeshError("The node didn't answer in time.") from None

    @staticmethod
    def _ok(res, what: str) -> None:
        if res is None or res.type == EventType.ERROR:
            reason = (res.payload or {}).get("reason") if res is not None else None
            raise MeshError(f"The node refused to {what}" + (f" ({reason})." if reason else "."))

    def send_dm(self, prefix: str, text: str) -> dict:
        node = self.store.find(prefix)
        if not node or not node.get("saved"):
            raise MeshError("That node isn't saved on your node, so it can't be messaged. Add it first.")
        if node.get("type") not in (1, 3):
            raise MeshError("Only companions and room servers can be messaged from here.")
        self._check_text(text, 0)
        msg = {"id": uuid.uuid4().hex[:12], "kind": "dm", "peer": node["key"][:12], "dir": "out",
               "from": self._self.get("name", ""), "text": text, "status": "sending", "ts": time.time()}

        async def go() -> None:
            self._add_message(msg)
            self._spawn(self._deliver(msg["id"], node["key"], text))
        self.request(go)
        return msg

    async def _deliver(self, mid: str, key: str, text: str) -> None:
        try:
            res = await self.mc.commands.send_msg_with_retry(key, text, max_attempts=3, flood_after=2)
        except asyncio.CancelledError:
            raise
        except Exception:
            res = None
        m = self.store.update_message(mid, status="acked" if res else "failed")
        if m:
            self.hub.publish({"msgs": [m]}, keep=False)

    def send_channel(self, idx: int, text: str) -> dict:
        if idx not in self._channels:
            raise MeshError("No such channel on the node.")
        self._check_text(text, len(self._self.get("name", "").encode()) + 2)
        msg = {"id": uuid.uuid4().hex[:12], "kind": "chan", "chan": idx, "dir": "out",
               "from": self._self.get("name", ""), "text": text, "status": "sent", "ts": time.time()}

        async def go() -> None:
            self._ok(await self.mc.commands.send_chan_msg(idx, text), "send that")
            self._add_message(msg)
        self.request(go)
        return msg

    @staticmethod
    def _check_text(text: str, reserve: int) -> None:
        if not text.strip():
            raise MeshError("Type a message first.")
        if len(text.encode()) > MAX_TEXT - reserve:
            raise MeshError(f"That message is too long: at most {MAX_TEXT - reserve} bytes fit.")

    def advertise(self, flood: bool) -> None:
        async def go() -> None:
            self._ok(await self.mc.commands.send_advert(flood), "advertise")
        self.request(go)

    def set_profile(self, name: str, lat: float, lon: float) -> None:
        name = name.strip()
        if not 1 <= len(name.encode()) <= 31:
            raise MeshError("The node's name is 1 to 31 bytes.")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise MeshError("That position isn't on Earth.")

        async def go() -> None:
            self._ok(await self.mc.commands.set_name(name), "change its name")
            self._ok(await self.mc.commands.set_coords(lat, lon), "change its position")
            await self._refresh_self()
        self.request(go)

    async def _refresh_self(self) -> None:
        await self.mc.commands.send_appstart()
        self._publish_self(self.mc.self_info)

    def add_node(self, key: str) -> None:
        node = self.store.node(key)
        if not node or not node.get("name"):
            raise MeshError("Unknown node.")

        async def go() -> None:
            pending = self.mc.pending_contacts.get(key)
            last = node.get("last_hops") is not None  # the route it had when the node last knew it
            c = pending or {"public_key": key, "type": node.get("type", 1), "flags": 0,
                            "out_path_len": node["last_hops"] if last else -1, "out_path": node.get("last_path", "") if last else "",
                            "out_path_hash_mode": node.get("last_hash", 1) - 1 if last else -1,
                            "adv_name": node["name"].encode()[:31].decode(errors="ignore"),  # a contact's name is at most 31 bytes
                            "last_advert": int(node.get("advert") or time.time()),
                            "adv_lat": node.get("lat") or 0.0, "adv_lon": node.get("lon") or 0.0}
            self._ok(await self.mc.commands.add_contact(c), "save that node")
            self.mc.pop_pending_contact(key)
            await self._sync_contacts(full=False)
            n, _ = self._put(key, {"saved": True, "pending": False, "added_at": time.time()})
            self.hub.publish({"nodes": [n]}, keep=False)
        self.request(go)

    def remove_node(self, key: str, forget: bool = False) -> None:
        """Take a node off the node's contact list. It stays here, where its key, position and last route are kept so it can
        be added back (forget: drop it from here too)."""
        node = self.store.node(key)
        if not node:
            raise MeshError("Unknown node.")

        async def go() -> None:
            if node.get("saved"):
                self._ok(await self.mc.commands.remove_contact(key), "remove that node")
            self.mc.contacts.pop(key, None)
            if forget:
                self.store.drop_node(key)
                self.hub.publish({"gone": [key]}, keep=False)
            else:
                n, _ = self._put(key, {"saved": False, "hops": -1, "path": ""})
                self.hub.publish({"nodes": [n]}, keep=False)
        self.request(go)

    def purge_nodes(self, keys: list[str]) -> int:
        """Free space on the node: remove these contacts from it (the ones it has), keeping all of them here."""
        keys = [k for k in dict.fromkeys(keys) if (self.store.node(k) or {}).get("saved")][:PURGE_MAX]
        if not keys:
            return 0

        return self.request(lambda: self._take_off(keys), 20 + len(keys))

    async def _take_off(self, keys: list[str]) -> int:
        done = []
        for k in keys:
            res = await self.mc.commands.remove_contact(k)
            if res is None or res.type == EventType.ERROR:
                continue
            self.mc.contacts.pop(k, None)
            done.append(self._put(k, {"saved": False, "hops": -1, "path": ""})[0])
        if done:
            self.hub.publish({"nodes": done}, keep=False)
        return len(done)

    async def _auto_purge(self) -> int:
        """When the node's contact list is nearly full, take off the contacts it has heard from least recently, down to the
        low mark. Favourites, anyone you have messaged and contacts added by hand lately stay. Everything is kept here."""
        cfg, cap = self.cfg, int(self._self.get("max_contacts") or 0)
        if not (cfg.auto_purge and cap and self.mc) or time.time() - self._last_purge < PURGE_EVERY_S:
            return 0
        used = len(self.mc.contacts)
        if used < cap * cfg.purge_high / 100:
            return 0
        talked = {m["peer"] for m in self.store.recent_messages(MAX_MESSAGES_SCAN) if m.get("kind") == "dm"}
        now = time.time()

        def spare(k: str, c: dict) -> bool:
            return bool(c.get("flags", 0) & 1) or any(k.startswith(p) for p in talked) or now - (self.store.node(k) or {}).get("added_at", 0) < NEW_CONTACT_S
        pool = sorted((k for k, c in self.mc.contacts.items() if not spare(k, c)), key=lambda k: (self.store.node(k) or {}).get("heard") or 0)
        take = pool[:max(0, used - int(cap * cfg.purge_low / 100))]
        if not take:
            return 0
        self._last_purge = now
        n = await self._take_off(take)
        if n:
            self._notice(f"The node's contact list was {used * 100 // cap}% full, so the {n} contacts it had heard from least recently came "
                         "off it. They are kept here, grey, and can be added back.")
        return n

    def restore_route(self, key: str) -> None:
        """Give the node back the route it used to have to a contact (it has lost it, or reset it after failed sends)."""
        node = self.store.node(key)
        if not node or node.get("last_hops") is None:
            raise MeshError("No earlier route to that node is remembered.")

        async def go() -> None:
            c = self.mc.contacts.get(key)
            if not c:
                raise MeshError("Your node doesn't have that contact right now. Add it first.")
            self._ok(await self.mc.commands.change_contact_path(c, node.get("last_path", ""), path_hash_mode=node.get("last_hash", 1) - 1),
                     "set that route")
            await self._sync_contacts(full=False)
        self.request(go)

    def reset_path(self, key: str) -> None:
        async def go() -> None:
            self._ok(await self.mc.commands.reset_path(key), "forget the route to that node")
            await self._sync_contacts(full=False)
        self.request(go)

    # --- radio, privacy, channels -------------------------------------------------------------------

    def set_radio(self, freq: float, bw: float, sf: int, cr: int, tx_power: int) -> None:
        if not 150 <= freq <= 960:
            raise MeshError("The frequency is between 150 and 960 MHz (the radio chip's range).")
        if bw not in BANDWIDTHS:
            raise MeshError("Pick one of the listed bandwidths.")
        if not (5 <= sf <= 12 and 5 <= cr <= 8):
            raise MeshError("Spreading factor is 5 to 12 and coding rate 5 to 8 (4/5 to 4/8).")
        top = int(self._self.get("max_tx_power") or 22)
        if not 1 <= tx_power <= top:
            raise MeshError(f"This node's power is 1 to {top} dBm.")

        async def go() -> None:
            # the library turns MHz into whole kHz with int(); the nudge keeps 910.525 from landing on 910524
            self._ok(await self.mc.commands.set_radio(round(freq, 3) + 1e-9, bw + 1e-9, sf, cr), "change the radio settings")
            self._ok(await self.mc.commands.set_tx_power(tx_power), "change the transmit power")
            await self._refresh_self()
        self.request(go)

    def set_privacy(self, manual_add: bool, tel_base: int, tel_loc: int, tel_env: int, loc_policy: int) -> None:
        if not all(0 <= v <= 2 for v in (tel_base, tel_loc, tel_env, loc_policy)):
            raise MeshError("That setting has three choices, 0 to 2.")

        async def go() -> None:
            res = await self.mc.commands.send_appstart()
            self._ok(res, "read its settings")
            infos = dict(res.payload)
            infos.update(manual_add_contacts=manual_add, telemetry_mode_base=tel_base, telemetry_mode_loc=tel_loc,
                         telemetry_mode_env=tel_env, adv_loc_policy=loc_policy)
            self._ok(await self.mc.commands.set_other_params_from_infos(infos), "change those settings")
            await self._refresh_self()
        self.request(go)

    async def _read_channel(self, idx: int) -> None:
        res = await self.mc.commands.get_channel(idx)
        with self._lock:
            if res and res.type == EventType.CHANNEL_INFO and res.payload.get("channel_name"):
                self._channels[idx] = res.payload["channel_name"]
                self._secrets[idx] = bytes(res.payload.get("channel_secret") or b"").hex()
            else:
                self._channels.pop(idx, None)
                self._secrets.pop(idx, None)
        self.hub.publish(self._channels_event(), keep=False)

    def set_channel(self, idx: int, name: str, secret: str = "") -> None:
        """Create or change a channel. A #name derives its key from the name; otherwise the key is the one given, the
        one the channel already has, or a new random one (a private channel)."""
        name = name.strip()
        if not 1 <= len(name.encode()) <= 31 or any(ord(c) < 32 for c in name):
            raise MeshError("A channel name is 1 to 31 characters.")
        if not 0 <= idx < int(self._self.get("max_channels") or 8):
            raise MeshError("The node has no such channel slot.")
        secret = secret.strip().lower()
        if secret and not re.fullmatch(r"[0-9a-f]{32}", secret):
            raise MeshError("A channel's secret is 32 hex characters.")
        have = secret or self._secrets.get(idx, "")
        key = bytes.fromhex(have) if len(have) == 32 else os.urandom(16)

        async def go() -> None:
            self._ok(await self.mc.commands.set_channel(idx, name, key), "save that channel")
            await self._read_channel(idx)
        self.request(go)

    def remove_channel(self, idx: int) -> None:
        if idx == 0:
            raise MeshError("Channel 0 is the node's Public channel; it stays.")
        if idx not in self._channels:
            raise MeshError("No such channel.")

        async def go() -> None:
            self._ok(await self.mc.commands.set_channel(idx, "", bytes(16)), "remove that channel")
            await self._read_channel(idx)
        self.request(go)

    # --- the node itself ----------------------------------------------------------------------------

    def reboot(self) -> None:
        async def go() -> None:
            try:
                await asyncio.wait_for(self.mc.commands.reboot(), 3)  # it restarts instead of answering
            except asyncio.TimeoutError:
                pass
        self.request(go)
        self._set_status(False, "The node is restarting...")
        self.reconnect()

    def export_key(self) -> str:
        async def go() -> str:
            res = await self.mc.commands.export_private_key()
            if res is not None and res.type == EventType.DISABLED:
                raise MeshError("This firmware has exporting the key turned off.")
            self._ok(res, "export its key")
            return res.payload["private_key"].hex()
        return self.request(go)

    def import_key(self, key: str) -> None:
        key = key.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{128}", key):
            raise MeshError("A node's key is 128 hex characters (64 bytes), as the export gave it.")

        async def go() -> None:
            self._ok(await self.mc.commands.import_private_key(bytes.fromhex(key)), "use that key")
        self.request(go)
        self.reboot()

    def stats(self) -> dict:
        async def go() -> dict:
            out = {}
            for name, fn in (("core", self.mc.commands.get_stats_core), ("radio", self.mc.commands.get_stats_radio),
                             ("packets", self.mc.commands.get_stats_packets)):
                res = await fn()
                if res is not None and res.type != EventType.ERROR:
                    out[name] = res.payload
            return out
        return self.request(go)

    # --- contact links ------------------------------------------------------------------------------

    def export_contact(self, key: str | None) -> str:
        if key and not (self.store.node(key) or {}).get("saved"):
            raise MeshError("Only nodes saved on your node can be exported.")

        async def go() -> str:
            res = await self.mc.commands.export_contact(key)
            if res is None or res.type != EventType.CONTACT_URI:
                self._ok(res, "export that contact")
                raise MeshError("The node gave no contact link.")
            return res.payload["uri"]
        return self.request(go)

    def import_contact(self, uri: str) -> None:
        m = re.fullmatch(r"meshcore://([0-9a-fA-F]{20,})", uri.strip())
        if not m or len(m.group(1)) % 2:
            raise MeshError("That isn't a contact link: it starts with meshcore:// and is followed by hex digits.")

        async def go() -> None:
            self._ok(await self.mc.commands.import_contact(bytes.fromhex(m.group(1))), "import that contact")
            await self._sync_contacts(full=True)
        self.request(go)

    def share_contact(self, key: str) -> None:
        if not (self.store.node(key) or {}).get("saved"):
            raise MeshError("Only nodes saved on your node can be shared.")

        async def go() -> None:
            self._ok(await self.mc.commands.share_contact(key), "share that contact")
        self.request(go)
