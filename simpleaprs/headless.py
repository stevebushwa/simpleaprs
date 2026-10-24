"""Run simpleaprs from a terminal, with no window and no Qt: the radio is shared as a network server.

Meant for a Pi attached to a radio. Open the web page it prints from any browser on the network, or
point another simpleaprs at it (Settings > "Connect to another simpleaprs"). There is no token: it is
for a LAN.
"""
from __future__ import annotations

import random
import signal
import sys
import threading
import time

from . import aprs
from .ax25 import Frame
from .config import PATH, STATIONS_PATH, Config
from .digipeater import Digipeater
from .igate import IGate, passcode
from .mapserver import MapProxy
from .netshare import Hub, ShareServer, lan_ip
from .soundio import SoundRadio, device_names, find_device
from .tilecache import TileCache
from .tracker import Tracker
from .webui import WebUI


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {msg}", flush=True)


# --- first-time setup -----------------------------------------------------------------------------

def ask(prompt: str, default: str = "") -> str:
    shown = f" [{default}]" if default != "" else ""
    return input(f"{prompt}{shown}: ").strip() or default


def ask_float(prompt: str, default: float, lo: float, hi: float) -> float:
    while True:
        try:
            v = float(ask(prompt, f"{default:g}"))
        except ValueError:
            print("  Enter a number.")
            continue
        if lo <= v <= hi:
            return v
        print(f"  Must be between {lo:g} and {hi:g}.")


def ask_int(prompt: str, default: int, lo: int, hi: int) -> int:
    return int(ask_float(prompt, default, lo, hi))


def ask_yes(prompt: str, default: bool) -> bool:
    while True:
        a = ask(prompt + (" (Y/n)" if default else " (y/N)")).lower()
        if not a:
            return default
        if a in ("y", "yes", "n", "no"):
            return a[0] == "y"


def ask_device(label: str, names: list[str], current: str) -> str:
    """Pick from a numbered list; Enter keeps the current choice, 0 is the system default."""
    print(f"\n{label}:")
    print("   0) (system default)")
    for i, n in enumerate(names, 1):
        print(f"  {i:2d}) {n}")
    keep = f"{names.index(current) + 1}" if current in names else "0"
    while True:
        a = ask("Number", keep)
        if a == "0":
            return ""
        if a.isdigit() and 1 <= int(a) <= len(names):
            return names[int(a) - 1]
        print("  Pick a number from the list.")


def ask_passcode(cfg: Config) -> str:
    while True:
        v = ask("APRS-IS passcode for your callsign (type calc to work it out from the callsign)", cfg.igate_passcode)
        if v.lower() == "calc":
            v = str(passcode(cfg.callsign))
            print(f"  Passcode {v}")
        if v:
            return v
        print("  A passcode is required to send packets to APRS-IS.")


def setup(cfg: Config) -> Config:
    """Walk through the settings a server needs. Existing values are the defaults, so re-running
    it to change one thing is just a matter of pressing Enter through the rest."""
    print("simpleaprs setup - press Enter to keep the value in [brackets].\n")
    while True:
        cfg.callsign = ask("Callsign (e.g. N0CALL-7)", cfg.callsign).upper()
        if cfg.callsign:
            break
        print("  A callsign is required.")
    cfg.lat = ask_float("Latitude (decimal degrees, south is negative)", cfg.lat, -90, 90)
    cfg.lon = ask_float("Longitude (west is negative)", cfg.lon, -180, 180)
    while True:
        cfg.symbol = ask("Map symbol, table then code (/> car, /- house, /# digipeater)", cfg.symbol)
        if len(cfg.symbol) == 2:
            break
        print("  Two characters, like /-.")
    cfg.comment = ask("Beacon comment", cfg.comment)
    cfg.path = ask("Path for your packets", cfg.path)
    cfg.input_device = ask_device("Radio audio in (the sound card the radio's speaker/data port feeds)",
                                  device_names(True), cfg.input_device)
    cfg.output_device = ask_device("Radio audio out (the sound card feeding the radio's mic/data port)",
                                   device_names(False), cfg.output_device)
    cfg.tx_lead_ms = ask_int("\nVOX lead-in in ms (raise it if the start of your packets gets cut off)", cfg.tx_lead_ms, 100, 3000)
    cfg.beacon_minutes = ask_int("Auto beacon every N minutes (0 = only when a client asks)", cfg.beacon_minutes, 0, 120)
    cfg.share_port = ask_int("Network port", cfg.share_port, 1024, 65535)
    cfg.nomad_url = ask("Map server (Project N.O.M.A.D. address, blank for no map)", cfg.nomad_url)
    cfg.share_allow_tx = ask_yes("Let the web page and other clients transmit through this radio", cfg.share_allow_tx)
    cfg.igate_enabled = ask_yes("\nAct as an iGate (pass what this radio hears to the internet, APRS-IS)", cfg.igate_enabled)
    if cfg.igate_enabled:
        cfg.igate_server = ask("APRS-IS server (host:port)", cfg.igate_server)
        cfg.igate_passcode = ask_passcode(cfg)
        cfg.igate_to_rf = ask_yes("Also transmit internet messages for stations heard directly here (the radio will transmit on its own)", cfg.igate_to_rf)
        if cfg.igate_to_rf:
            cfg.igate_radius_km = ask_int("How far from you, in km, to look for those messages", cfg.igate_radius_km, 1, 500)
    cfg.digi_enabled = ask_yes("\nAct as a digipeater (repeat packets that ask for it; the radio will transmit on its own)", cfg.digi_enabled)
    if cfg.digi_enabled:
        cfg.digi_wide = ask_yes("Wide-area (also answer WIDE2-1/WIDE2-2) rather than fill-in (WIDE1-1 only)", cfg.digi_wide)
    cfg.save()
    print(f"\nSaved to {PATH}\n")
    return cfg


# --- the server -----------------------------------------------------------------------------------

class Server:
    def __init__(self, cfg: Config, quiet: bool = False, allowed_hosts: tuple[str, ...] = ()) -> None:
        self.cfg, self.quiet, self.allowed_hosts = cfg, quiet, allowed_hosts
        self.hub = Hub()      # raw frames, for desktop clients
        self.web_hub = Hub()  # decoded events, for browsers
        self.tracker = Tracker(cfg, self._emit, self.transmit, STATIONS_PATH)
        self.radio = SoundRadio(self.on_frame)
        self.share: ShareServer | None = None
        self.igate: IGate | None = None
        self.digi: Digipeater | None = None
        self._igate_state = ""
        self.stopping = False
        self.next_beacon = self.next_save = 0.0

    def start(self) -> bool:
        c = self.cfg
        for label, name, inputs in (("input", c.input_device, True), ("output", c.output_device, False)):
            if name and find_device(name, inputs) is None:  # the desktop app quietly uses the default; a server nobody watches shouldn't
                print(f"Audio {label} device '{name}' not found. Run --list-devices to see what is available.", file=sys.stderr)
                return False
        if c.igate_enabled and not c.igate_passcode:
            print("The iGate needs the APRS-IS passcode for your callsign. Run again with --setup, or give --igate-passcode.", file=sys.stderr)
            return False
        self.tracker.load()
        cache = TileCache(max_bytes=c.map_cache_mb * 1024 * 1024) if c.map_cache else None
        web = WebUI(self.web_hub, self.tracker, MapProxy(c.nomad_url, cache), c.share_allow_tx)
        try:
            self.share = ShareServer(self.hub, c.share_port, "", False, c.share_allow_tx, self._hello, self.remote_tx,
                                     snapshot=self.tracker.data, web=web, open_access=True, allowed_hosts=self.allowed_hosts)
        except OSError as e:
            print(f"Cannot listen on port {c.share_port}: {e}", file=sys.stderr)
            return False
        if not self.radio.start(c.input_device, c.output_device):
            print("No audio input found. Run again with --setup to pick a device, or --list-devices to see them.", file=sys.stderr)
            self.share.stop()
            return False
        if c.digi_enabled:
            self.digi = Digipeater(c.callsign, c.digi_wide)
        if c.igate_enabled:
            self.igate = IGate(c, self._igate_status, self.igate_to_rf)
            self.igate.start()
        addr = f"{lan_ip()}:{self.share.port}"
        print(f"simpleaprs server for {c.callsign} listening for packets")
        print(f"  Web page       http://{addr}/")
        print(f"  Desktop app    Settings > Connect to another simpleaprs > {addr} (no token)")
        print(f"  Transmitting   " + ("allowed from the web page and clients" if c.share_allow_tx else "off"))
        if self.igate:
            print(f"  iGate          to {c.igate_server}" + (f", and messages for stations heard here back to RF (within {c.igate_radius_km} km)" if c.igate_to_rf else ""))
        if self.digi:
            print("  Digipeater     " + ("wide-area (WIDE1-1, WIDE2-N)" if c.digi_wide else "fill-in (WIDE1-1)") + f" and {c.callsign}")
        print("  Anyone who can reach this port can use it: keep it on your LAN or a VPN.")
        print("  Ctrl+C to stop\n", flush=True)
        now = time.monotonic()
        self.next_save = now + 30
        self.next_beacon = now + c.beacon_minutes * 60 if c.beacon_minutes else 0.0
        return True

    def tick(self) -> None:
        """Called about once a second: auto beacon and saving what we have heard."""
        now = time.monotonic()
        if self.next_beacon and now >= self.next_beacon:
            self.next_beacon = now + self.cfg.beacon_minutes * 60
            self.beacon()
        if now >= self.next_save:
            self.next_save = now + 30
            self.tracker.save()

    def stop(self) -> None:
        self.stopping = True
        if self.igate:
            self.igate.stop()
        self.tracker.save()
        self.radio.stop()
        if self.share:
            self.share.stop()

    def _hello(self) -> dict:
        c = self.cfg
        return {"callsign": c.callsign, "lat": c.lat, "lon": c.lon, "symbol": c.symbol, "comment": c.comment, "path": c.path}

    def _emit(self, ev: dict, keep: bool = True) -> None:
        self.web_hub.publish(ev, keep)
        if ev["type"] == "message" or (ev["type"] == "packet" and not self.quiet):
            log(("MESSAGE " if ev["type"] == "message" else "") + ev["text"])

    def on_frame(self, raw: bytes) -> None:
        now = time.time()
        self.hub.publish({"t": now, "raw": raw.hex()})
        self.tracker.on_frame(raw, now)
        if self.igate or self.digi:
            try:
                self._relay(Frame.decode(raw))
            except ValueError:
                pass

    def _relay(self, frame: Frame) -> None:
        """A frame we just heard: offer it to the iGate, and repeat it if we are a digipeater and it asks for that."""
        if self.igate:
            self.igate.send_rf(frame)
        out = self.digi.digipeat(frame) if self.digi else None
        if out:  # a little random wait, so two digipeaters that heard the same packet don't key up together
            threading.Timer(random.uniform(0.3, 1.5), self.send_frame, (out, True)).start()

    def _igate_status(self, text: str) -> None:
        head = text.split(" - ")[0]  # the part before the running count: only a change of state is worth a line
        if head != self._igate_state:
            self._igate_state = head
            log(text)

    def igate_to_rf(self, raw: bytes) -> None:
        """The iGate wants a message from the internet put on the air."""
        try:
            self.send_frame(Frame.decode(raw), True)
        except ValueError:
            pass

    def transmit(self, info: bytes) -> None:
        self.send_frame(aprs.make_frame(self.cfg.callsign, info, self.cfg.path))

    def send_frame(self, frame: Frame, relay: bool = False) -> None:
        """Put a frame on the air. relay: it is someone else's packet that we are passing on (digipeating or
        gating), which clients following this radio must not show as our own message."""
        if self.stopping:
            return
        c = self.cfg
        raw = frame.encode()
        self.hub.publish({"t": time.time(), "raw": raw.hex(), "tx": True, **({"relay": True} if relay else {})})
        self.tracker.on_tx(raw, relay=relay)
        self.radio.transmit(raw, c.tx_lead_ms, c.tx_level)

    def beacon(self) -> None:
        c = self.cfg
        if c.lat or c.lon:
            self.transmit(aprs.build_position(c.lat, c.lon, c.symbol, c.comment))

    def remote_tx(self, req: dict) -> None:
        """A client or the web page asked this radio to transmit (already validated)."""
        if req["type"] == "beacon":
            log("client asked for a beacon")
            self.beacon()
        else:
            log(f"client {req['type']} -> {req['to']}: {req['text']}")
            msg_id = "" if req["type"] == "bulletin" else str(int(time.time()) % 100000)
            self.transmit(aprs.build_message(req["to"], req["text"], msg_id))


def run(cfg: Config, quiet: bool = False, allowed_hosts: tuple[str, ...] = ()) -> int:
    server = Server(cfg, quiet, allowed_hosts)
    if not server.start():
        return 1
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    while not stop.wait(1):
        server.tick()
    print("\nStopping...")
    server.stop()
    return 0
