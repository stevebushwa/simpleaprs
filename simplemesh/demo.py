"""A pretend MeshCore node: the same calls and events as meshcore's MeshCore, with nothing behind them.

FakeMesh is what the tests drive. DemoMesh adds a small made-up mesh that chatters on its own, for `simplemesh --demo`:
a way to see the page, or work on it, without a radio.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time

from meshcore import EventType
from meshcore.events import Event

from .firmware import DOWNLOADS, RELEASES


def key_for(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


ANNA_KEY = key_for("Anna")


def contact(name: str, kind: int, lat: float = 0.0, lon: float = 0.0, hops: int = -1, path: str = "") -> dict:
    return {"public_key": key_for(name), "type": kind, "flags": 0, "out_path_len": hops, "out_path_hash_mode": 0 if hops >= 0 else -1,
            "out_path": path, "adv_name": name, "last_advert": int(time.time()) - 600, "adv_lat": lat, "adv_lon": lon,
            "lastmod": int(time.time())}


CLI_REPLIES = {"ver": "MeshCore v1.18.0 (demo)", "clock": "12:00 - 27/10/2026 UTC", "get name": "> Demo Repeater",
               "neighbors": "Hilltop Repeater:12:-3", "get radio": "> 910.525,62.5,7,5"}


class FakeCommands:
    def __init__(self, mc: "FakeMesh") -> None:
        self.mc = mc
        self.sent: list[tuple] = []      # what the page asked the node to do, in order
        self.ack = True                  # does a direct message get acknowledged?
        self.refuse: set[str] = set()    # command names that answer with an error
        self.password = "hunter2"        # what a repeater accepts at login ("" lets guests in)
        self.replies = dict(CLI_REPLIES)  # what a repeater answers to a command; no entry = no answer
        self.silent = False              # remote nodes never answer
        self.max_contacts = 350          # how many contacts the node holds

    def _answer(self, name: str, payload=None, kind=EventType.OK) -> Event:
        self.sent.append((name,))
        if name in self.refuse:
            return Event(EventType.ERROR, {"reason": "refused"})
        return Event(kind, payload or {})

    async def send_device_query(self):
        return self._answer("device_query", {"model": "Heltec V3", "ver": "v1.9.0", "fw_build": "test", "max_channels": 4, "max_contacts": self.max_contacts,
                             "path_hash_mode": self.mc.path_hash_mode},
                            EventType.DEVICE_INFO)

    async def send_appstart(self):
        return self._answer("appstart", self.mc.self_info, EventType.SELF_INFO)

    async def set_time(self, t):
        return self._answer("set_time")

    async def get_bat(self):
        await self.mc.emit(EventType.BATTERY, {"level": 3900})  # the real client announces it as an event too
        return self._answer("bat", {"level": 3900}, EventType.BATTERY)

    async def get_channel(self, i):
        name = self.mc.channels.get(i, "")
        return Event(EventType.CHANNEL_INFO, {"channel_idx": i, "channel_name": name,
                                              "channel_secret": self.mc.channel_keys.get(i, b"\0" * 16)})

    async def set_channel(self, idx, name, secret=None):
        self.sent.append(("set_channel", idx, name, secret))
        if "set_channel" in self.refuse:
            return Event(EventType.ERROR, {"reason": "refused"})
        if name.startswith("#") or secret is None:
            secret = hashlib.sha256(name.encode()).digest()[:16]
        if name:
            self.mc.channels[idx], self.mc.channel_keys[idx] = name, secret
        else:
            self.mc.channels.pop(idx, None)
            self.mc.channel_keys.pop(idx, None)
        return Event(EventType.OK, {})

    async def set_radio(self, freq, bw, sf, cr, repeat=None):
        self.sent.append(("set_radio", freq, bw, sf, cr))
        if "set_radio" in self.refuse:
            return Event(EventType.ERROR, {"reason": "refused"})
        self.mc.self_info.update(radio_freq=int(float(freq) * 1000) / 1000, radio_bw=int(float(bw) * 1000) / 1000,
                                 radio_sf=sf, radio_cr=cr)
        return Event(EventType.OK, {})

    async def set_tx_power(self, val):
        self.sent.append(("set_tx_power", val))
        self.mc.self_info["tx_power"] = val
        return Event(EventType.OK, {})

    async def set_other_params_from_infos(self, infos):
        self.sent.append(("set_other_params", dict(infos)))
        self.mc.self_info.update({k: infos[k] for k in ("manual_add_contacts", "telemetry_mode_base", "telemetry_mode_loc",
                                                        "telemetry_mode_env", "adv_loc_policy") if k in infos})
        return Event(EventType.OK, {})

    async def set_path_hash_mode(self, mode):
        self.sent.append(("set_path_hash_mode", mode))
        self.mc.path_hash_mode = mode
        return Event(EventType.OK, {})

    async def reboot(self):
        self.sent.append(("reboot",))
        return None

    async def export_private_key(self):
        self.sent.append(("export_key",))
        if "export_key" in self.refuse:
            return Event(EventType.DISABLED, {"reason": "private_key_export_disabled"})
        return Event(EventType.PRIVATE_KEY, {"private_key": bytes(range(64))})

    async def import_private_key(self, key):
        self.sent.append(("import_key", bytes(key)))
        return Event(EventType.OK, {})

    async def get_stats_core(self):
        return Event(EventType.STATS_CORE, {"battery_mv": 3900, "uptime_secs": 3700, "errors": 0, "queue_len": 1})

    async def get_stats_radio(self):
        return Event(EventType.STATS_RADIO, {"noise_floor": -112, "last_rssi": -95, "last_snr": 6.5, "tx_air_secs": 12,
                                             "rx_air_secs": 340})

    async def get_stats_packets(self):
        return Event(EventType.STATS_PACKETS, {"recv": 120, "sent": 14, "flood_tx": 10, "direct_tx": 4, "flood_rx": 100,
                                               "direct_rx": 20, "recv_errors": 2})

    async def export_contact(self, key=None):
        self.sent.append(("export_contact", key))
        return Event(EventType.CONTACT_URI, {"uri": "meshcore://" + (key or self.mc.self_info["public_key"])[:40]})

    async def import_contact(self, card):
        self.sent.append(("import_contact", bytes(card)))
        return Event(EventType.OK, {})

    async def share_contact(self, key):
        self.sent.append(("share_contact", key))
        return Event(EventType.OK, {})

    # remote nodes -----------------------------------------------------------------------------------

    async def send_login_sync(self, dst, pwd, timeout=0, min_timeout=0):
        self.sent.append(("login", dst["public_key"], pwd))
        if self.silent or pwd != self.password:
            return None  # a wrong password gets no answer worth waiting for
        return Event(EventType.LOGIN_SUCCESS, {"permissions": 1, "is_admin": True, "pubkey_prefix": dst["public_key"][:12]})

    async def send_logout(self, dst):
        self.sent.append(("logout", dst["public_key"]))
        return Event(EventType.OK, {})

    async def req_status_sync(self, contact, timeout=0, min_timeout=0):
        self.sent.append(("status", contact["public_key"]))
        if self.silent:
            return None
        return {"pubkey_pre": contact["public_key"][:12], "bat": 4012, "tx_queue_len": 0, "noise_floor": -110, "last_rssi": -80,
                "nb_recv": 900, "nb_sent": 310, "airtime": 1200, "uptime": 90061, "sent_flood": 100, "sent_direct": 210,
                "recv_flood": 700, "recv_direct": 200, "full_evts": 0, "last_snr": 7.25, "direct_dups": 3, "flood_dups": 40}

    async def req_neighbours_sync(self, contact, **kw):
        self.sent.append(("neighbours", contact["public_key"]))
        if self.silent:
            return None
        return {"neighbours_count": 2, "results_count": 2, "neighbours": [
            {"pubkey": ANNA_KEY[:8], "secs_ago": 60, "snr": 8.5}, {"pubkey": "deadbeef", "secs_ago": 4000, "snr": -3.0}]}

    async def req_telemetry_sync(self, contact, timeout=0, min_timeout=0):
        self.sent.append(("telemetry", contact["public_key"]))
        return None if self.silent else [{"channel": 1, "type": "voltage", "value": 4.01},
                                         {"channel": 2, "type": "temperature", "value": 21.5}]

    async def req_acl_sync(self, contact, timeout=0, min_timeout=0):
        self.sent.append(("acl", contact["public_key"]))
        return None if self.silent else [{"key": ANNA_KEY[:12], "perm": 1}]

    async def send_path_discovery_sync(self, dst, timeout=0, min_timeout=0):
        self.sent.append(("route", dst["public_key"]))
        self.route_timeout = timeout  # how long the caller was willing to wait
        if self.silent:
            return None
        return Event(EventType.PATH_RESPONSE, {"pubkey_pre": dst["public_key"][:12], "out_path_len": 1, "out_path_hash_len": 1,
                                               "out_path": "ab", "in_path_len": 1, "in_path_hash_len": 1, "in_path": "cd"})

    async def send_cmd(self, dst, cmd, **kw):
        self.sent.append(("cmd", dst["public_key"], cmd))
        if "cmd" in self.refuse:
            return Event(EventType.ERROR, {"reason": "refused"})
        if not self.silent and cmd in self.replies:
            prefix, text = dst["public_key"][:12], self.replies[cmd]
            loop = asyncio.get_running_loop()
            loop.call_soon(lambda: loop.create_task(self.mc.emit(EventType.CONTACT_MSG_RECV, {
                "pubkey_prefix": prefix, "text": text, "txt_type": 1, "SNR": 5.0, "path_len": 0, "sender_timestamp": 0})))
        return Event(EventType.MSG_SENT, {"expected_ack": b"1234", "suggested_timeout": 4000})

    async def get_contacts(self, lastmod=0, timeout=5):
        self.sent.append(("get_contacts", lastmod))
        return Event(EventType.CONTACTS, self.mc.contacts, {"lastmod": int(time.time())})

    async def send_msg_with_retry(self, key, text, **kw):
        self.sent.append(("dm", key, text))
        await asyncio.sleep(0)
        return Event(EventType.MSG_SENT, {"expected_ack": b"1234"}) if self.ack else None

    async def send_chan_msg(self, idx, text):
        if "chan" in self.refuse:
            return Event(EventType.ERROR, {"reason": "refused"})
        return self._record("chan", idx, text)

    def _record(self, *what) -> Event:
        self.sent.append(what)
        return Event(EventType.OK, {})

    async def send_advert(self, flood=False):
        return self._record("advert", flood)

    async def set_name(self, name):
        self.mc.self_info["name"] = name
        return self._answer("set_name")

    async def set_coords(self, lat, lon):
        self.mc.self_info.update(adv_lat=lat, adv_lon=lon)
        return self._answer("set_coords")

    async def add_contact(self, c):
        self.sent.append(("add", c["public_key"]))
        self.mc.contacts[c["public_key"]] = {**c, "lastmod": int(time.time())}
        return Event(EventType.OK, {})

    async def remove_contact(self, key):
        self.sent.append(("remove", key))
        self.mc.contacts.pop(key, None)
        return Event(EventType.OK, {})

    async def change_contact_path(self, contact, path, path_hash_mode=None):
        self.sent.append(("set_path", contact["public_key"], path, path_hash_mode))
        size = (path_hash_mode or 0) + 1
        self.mc.contacts[contact["public_key"]].update(out_path=path, out_path_len=len(path) // (2 * size), out_path_hash_mode=path_hash_mode or 0)
        return Event(EventType.OK, {})

    async def reset_path(self, key):
        self.sent.append(("reset_path", key))
        if key in self.mc.contacts:
            self.mc.contacts[key].update(out_path_len=-1, out_path="")
        return Event(EventType.OK, {})


class FakeMesh:
    def __init__(self, name: str = "Pi Node", lat: float = 0.0, lon: float = 0.0) -> None:
        self.path_hash_mode = 0
        self.self_info = {"adv_type": 1, "tx_power": 20, "max_tx_power": 22, "public_key": key_for(name), "adv_lat": lat,
                          "adv_lon": lon, "radio_freq": 910.525, "radio_bw": 62.5, "radio_sf": 7, "radio_cr": 5, "name": name,
                          "multi_acks": 0, "adv_loc_policy": 1, "telemetry_mode_base": 0, "telemetry_mode_loc": 0,
                          "telemetry_mode_env": 0, "manual_add_contacts": False}
        self.contacts: dict[str, dict] = {}
        self.pending_contacts: dict[str, dict] = {}
        self.channels: dict[int, str] = {0: "Public"}
        self.channel_keys: dict[int, bytes] = {}
        self.commands = FakeCommands(self)
        self.subs: list[tuple] = []
        self.disconnected = False

    def subscribe(self, event_type, callback, attribute_filters=None):
        self.subs.append((event_type, callback))

    async def emit(self, event_type, payload=None, attributes=None) -> None:
        ev = Event(event_type, payload or {}, attributes or {})
        for et, cb in list(self.subs):
            if et == event_type:
                await cb(ev)

    async def start_auto_message_fetching(self):
        return None

    async def disconnect(self):
        self.disconnected = True

    def pop_pending_contact(self, key):
        return self.pending_contacts.pop(key, None)


# --- the made-up mesh ----------------------------------------------------------------------------------

NODES = [  # name, type, (north, east) metres from the home position
    ("Hilltop Repeater", 2, (2600, 900)), ("Water Tower Repeater", 2, (-1800, 1500)), ("Ridge Repeater", 2, (4200, -2800)),
    ("Church Steeple", 2, (300, -700)), ("Ranger Room", 3, (1200, 2400)), ("Weather Mast", 4, (-600, -1900)),
    ("Anna", 1, (500, 300)), ("Marcus", 1, (-2500, -400)), ("Trailhead 3", 1, (3400, 2900)), ("Delia's Truck", 1, (-900, 3300))]
CHATTER = [("Anna", "Anyone up on the ridge today?"), ("Marcus", "Just got to the water tower, signal is great"),
           ("Trailhead 3", "Gate is open, parking lot half full"), ("Delia's Truck", "Heading in from the east road"),
           ("Anna", "Copy. Checking in at 5."), ("Marcus", "Battery at 60 percent, all good")]


def offset(lat: float, lon: float, north: float, east: float) -> tuple[float, float]:
    import math
    return lat + north / 111_320, lon + east / (111_320 * math.cos(math.radians(lat)))


class DemoMesh(FakeMesh):
    def __init__(self, lat: float, lon: float) -> None:
        super().__init__("Pi Node", *offset(lat, lon, 0, 0))
        self.channels.update({1: "#hiking", 2: "#emergency"})
        for name, kind, (n, e) in NODES:
            la, lo = offset(lat, lon, n, e)
            hops = {2: 0, 1: random.choice([-1, 1, 2]), 3: 1, 4: 1}[kind]
            self.contacts[key_for(name)] = contact(name, kind, la, lo, hops, "ab" * max(hops, 0))
        self.commands.ack = True
        self.task: asyncio.Task | None = None

    async def start_auto_message_fetching(self):
        self.task = asyncio.get_running_loop().create_task(self._chatter())

    async def disconnect(self):
        await super().disconnect()
        if self.task:
            self.task.cancel()

    async def _chatter(self) -> None:
        names = [n for n, _, _ in NODES]
        i = 0
        while True:
            await asyncio.sleep(random.uniform(2, 6))
            who = random.choice(names)
            c = self.contacts[key_for(who)]
            kind = random.random()
            snr, rssi = round(random.uniform(-12, 10), 2), random.randint(-120, -70)
            hops = max(c["out_path_len"], 0)
            if kind < 0.4:  # somebody's advert, as any node in range would hear it
                await self.emit(EventType.RX_LOG_DATA, {
                    "snr": snr, "rssi": rssi, "payload_typename": "ADVERT", "route_typename": "FLOOD", "path_len": hops,
                    "path": "ab" * hops, "path_hash_size": 1, "payload_length": 101, "recv_time": int(time.time()),
                    "adv_key": c["public_key"], "adv_name": who, "adv_type": c["type"], "adv_lat": c["adv_lat"],
                    "adv_lon": c["adv_lon"], "adv_timestamp": int(time.time())})
                await self.emit(EventType.ADVERTISEMENT, {"public_key": c["public_key"]})
            elif kind < 0.8:
                who, text = CHATTER[i % len(CHATTER)]
                i += 1
                idx = random.choice([0, 0, 1])
                await self.emit(EventType.RX_LOG_DATA, {
                    "snr": snr, "rssi": rssi, "payload_typename": "GRP_TXT", "route_typename": "FLOOD", "path_len": hops,
                    "path": "ab" * hops, "path_hash_size": 1, "payload_length": 40 + len(text), "recv_time": int(time.time()),
                    "chan_name": self.channels[idx]})
                await self.emit(EventType.CHANNEL_MSG_RECV, {"channel_idx": idx, "text": f"{who}: {text}", "SNR": snr,
                                                             "path_len": hops or 255, "txt_type": 0,
                                                             "sender_timestamp": int(time.time() * 1000) % 2**31})
            elif c["type"] == 1:
                await self.emit(EventType.CONTACT_MSG_RECV, {"pubkey_prefix": c["public_key"][:12], "text": "Hey, got a minute?",
                                                             "SNR": snr, "path_len": hops or 255, "txt_type": 0,
                                                             "sender_timestamp": int(time.time() * 1000) % 2**31})


# --- firmware: a release list and a flasher that need no internet and no board ---------------------------

DEMO_IMAGE = bytes([0xE9]) + bytes(100_000)
DEMO_BOARDS = ("Heltec_Wireless_Paper", "Heltec_v3", "Heltec_v4", "LilyGo_T3S3_sx1262", "Station_G2")


def demo_release(version: str = "v1.18.0") -> bytes:
    sha = hashlib.sha256(DEMO_IMAGE).hexdigest()
    assets = [{"name": f"{b}_companion_radio_usb-{version}-81dbae5-merged.bin", "size": len(DEMO_IMAGE), "digest": "sha256:" + sha,
               "browser_download_url": f"{DOWNLOADS}companion-{version}/{b}_companion_radio_usb-{version}-81dbae5-merged.bin"}
              for b in DEMO_BOARDS]
    assets.append({"name": "Heltec_Wireless_Paper_companion_radio_ble-v1.18.0-81dbae5-merged.bin", "size": 5,
                   "browser_download_url": DOWNLOADS + "x"})
    return json.dumps([{"tag_name": "repeater-" + version, "assets": []},
                       {"tag_name": "companion-" + version, "html_url": "https://example.invalid/release", "assets": assets}]).encode()


def demo_fetch(url: str) -> bytes:
    return demo_release() if url == RELEASES else DEMO_IMAGE


def demo_flasher(cmd: list[str], on_line) -> int:
    on_line("Connected to ESP32-S3 on " + cmd[1])
    if "write-flash" in cmd:
        for pct in range(0, 101, 10):
            on_line(f"Writing at 0x{pct * 1000:08x} {pct}.0% ")
            time.sleep(0.15)
        on_line("Hash of data verified.")
    else:
        time.sleep(0.3)
    return 0
