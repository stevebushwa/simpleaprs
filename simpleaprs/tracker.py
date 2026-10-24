"""What the server knows about the stations it hears, and the decoded events the web page draws from.

This is the no-Qt counterpart of what MainWindow keeps for the desktop window: remembered stations on
disk (same file), who is a digipeater or iGate, weather, routes, messages. Everything leaves as plain
dicts through emit(), so the page needs no APRS code of its own.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import aprs
from .ax25 import Frame
from .config import Config


class Tracker:
    def __init__(self, cfg: Config, emit, reply, store: Path) -> None:
        """emit(event, keep=True) gets every event; reply(info) transmits (acks); store is the stations file."""
        self.cfg, self.emit, self.reply, self.store = cfg, emit, reply, store
        self.lock = threading.RLock()  # frames arrive on the audio thread, web clients connect on server threads
        self.stations: dict[str, aprs.Packet] = {}
        self.raw: dict[str, bytes] = {}
        self.heard: dict[str, float] = {}
        self.wx: dict[str, dict] = {}
        self.roles: dict[str, set[str]] = {}
        self.note: dict[str, str] = {}  # station -> the text its table row shows
        self.live: set[str] = set()  # heard this run; the rest are remembered ones, drawn grey
        self.bulletins: dict[tuple[str, str], str] = {}  # (sender, BLN address) -> last text logged: senders repeat them
        self.dirty = False

    # --- persistence (the file the desktop app uses for its local stations) -------------------------

    def load(self) -> None:
        saved: dict
        try:
            saved = json.loads(self.store.read_text())
        except (OSError, ValueError):
            return
        if isinstance(saved, list):  # first version of the file: just the stations
            saved = {"stations": saved}
        with self.lock:
            for c, r in saved.get("roles", {}).items():
                self.roles[c] = set(r)
            for rec in saved.get("stations", []):
                try:
                    raw = bytes.fromhex(rec["raw"])
                    p = aprs.parse(Frame.decode(raw))
                    if p.lat is None:
                        continue
                    self.stations[p.source], self.raw[p.source] = p, raw
                    self.heard[p.source] = float(rec["heard"])
                    self.wx[p.source] = rec.get("wx") or {}
                    self.note[p.source] = p.comment
                except (KeyError, ValueError, TypeError, AttributeError):
                    continue

    def data(self) -> dict:
        """What we remember, in the form stored on disk and sent to desktop clients."""
        with self.lock:
            recent = sorted(self.raw, key=lambda c: self.heard.get(c, 0), reverse=True)[:500]
            return {"stations": [{"raw": self.raw[c].hex(), "heard": self.heard.get(c, 0), "wx": dict(self.wx.get(c, {}))} for c in recent],
                    "roles": {c: sorted(r) for c, r in self.roles.items()}}

    def save(self) -> None:
        if not self.dirty:
            return
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            self.store.write_text(json.dumps(self.data()))
            self.dirty = False
        except OSError:
            pass

    # --- events ---------------------------------------------------------------------------------------

    def _me(self) -> tuple[float, float] | None:
        c = self.cfg
        return (c.lat, c.lon) if c.lat or c.lon else None

    def _pos(self, call: str) -> tuple[float, float] | None:
        p = self.stations.get(call)
        return (p.lat, p.lon) if p else None

    def me_event(self, allow_tx: bool) -> dict:
        c = self.cfg
        icon, _, kind = aprs.classify(c.symbol)
        return {"type": "me", "call": c.callsign, "lat": c.lat, "lon": c.lon, "icon": icon, "allow_tx": allow_tx,
                "info": [["Position", f"{c.lat:.5f}, {c.lon:.5f}"], ["Symbol", f"{c.symbol} ({kind})"],
                         ["Comment", c.comment], ["Path", c.path], ["", "The radio you are connected to"]]}

    def station_event(self, call: str) -> dict:
        p = self.stations[call]
        wx, roles = self.wx.get(call, {}), self.roles.get(call, set())
        icon, cls, kind = aprs.classify(p.symbol, bool(wx), roles)
        info = [["Position", f"{p.lat:.5f}, {p.lon:.5f}"], ["Type", kind], ["Symbol", p.symbol], ["Comment", p.comment],
                ["Via", ",".join(str(a) for a in p.frame.path)], ["Sent by", str(p.frame.src) if p.obj else ""],
                *[[k, v] for k, v in wx.items()]]
        segs: list[dict] = []
        if not p.obj:  # an object's path is its sender's, not its own
            segs, hops = aprs.route(p.frame, self._pos, self._me())
            info.append(["Route", hops])
        info.append(["Raw", str(p.frame)])
        return {"type": "station", "call": call, "lat": p.lat, "lon": p.lon, "icon": icon, "cls": cls,
                "kinds": sorted(aprs.kinds(p.symbol, bool(wx), roles)), "info": info, "segs": segs,
                "temp": wx.get("Temp", ""), "heard": self.heard.get(call, 0), "stale": call not in self.live,
                "note": self.note.get(call, "")}

    def intro(self, allow_tx: bool) -> list[dict]:
        """What a web client needs before it can follow along: who we are and every station we know."""
        with self.lock:
            return [self.me_event(allow_tx)] + [self.station_event(c) for c in self.stations]

    def _station(self, call: str) -> None:
        self.emit(self.station_event(call), False)  # the intro carries current state, no need to replay these

    def _packet(self, t: float, text: str, tx: bool = False) -> None:
        self.emit({"type": "packet", "t": t, "text": text, "tx": tx})

    def _message(self, t: float, text: str, kind: str = "direct") -> None:
        self.emit({"type": "message", "kind": kind, "t": t, "text": text})

    # --- traffic --------------------------------------------------------------------------------------

    def on_frame(self, raw: bytes, now: float | None = None) -> None:
        try:
            frame = Frame.decode(raw)
        except ValueError:
            return
        now = now if now is not None else time.time()
        p = aprs.parse(frame)
        with self.lock:
            for digi in aprs.digipeaters(frame):
                self._role(digi, "digi")
            if p.gated:
                self._role(p.source, "igate")
            if str(frame.src).rstrip("*") == self.cfg.callsign and any(a.repeated for a in frame.path):
                digi = next(str(a).rstrip("*") for a in reversed(frame.path) if a.repeated)  # a digipeater repeated our packet
                return self._packet(now, f"{frame}   <-- OUR PACKET, repeated by {digi}")
            self._packet(now, str(frame))
            if p.inner:  # third-party: a message for us can arrive this way, relayed by an iGate
                if p.inner.kind == "message":
                    return self._on_message(p.inner, now, via=p.source)
                p.comment = f"third-party: {p.inner.frame}"
            if p.kind == "message":
                return self._on_message(p, now)
            if p.weather:
                self.wx[p.source] = p.weather
            if p.kind == "position":
                self.stations[p.source], self.raw[p.source] = p, raw
                self.note[p.source] = p.comment
            elif p.kind == "weather":
                self.note[p.source] = ", ".join(f"{k} {v}" for k, v in p.weather.items())
            if p.kind == "position" or (p.kind == "weather" and p.source in self.stations):
                self.heard[p.source] = now
                self.live.add(p.source)
                self.dirty = True
                self._station(p.source)  # a weather report updates the popup of the station's last position
                if p.kind == "position":  # stations whose route passes through this one can now draw that hop
                    for q in list(self.stations.values()):
                        if q is not self.stations[p.source] and any(str(a).rstrip("*") == p.source for a in q.frame.path):
                            self._station(q.source)

    def on_tx(self, raw: bytes, now: float | None = None, relay: bool = False) -> None:
        """A frame we just transmitted (beacon, message, ack). relay: someone else's packet we passed on."""
        try:
            frame = Frame.decode(raw)
        except ValueError:
            return
        now = now if now is not None else time.time()
        self._packet(now, f"TX {frame}" + ("   (relayed)" if relay else ""), tx=True)
        p = aprs.parse(frame)
        if p.kind == "message" and not p.text.startswith("ack") and not relay:
            self._message(now, f"me -> {p.to}: {p.text}", "bulletin" if aprs.is_bulletin(p.to) else "direct")

    def _on_message(self, p: aprs.Packet, now: float, via: str = "") -> None:
        if aprs.is_bulletin(p.to):
            if self.bulletins.get((p.source, p.to)) != p.text:  # the same one again is not news
                self.bulletins[p.source, p.to] = p.text
                self._message(now, f"{p.to} from {p.source}: {p.text}" + (f"   (via iGate {via})" if via else ""), "bulletin")
            return
        if p.to != self.cfg.callsign:
            return
        self._message(now, f"{p.source}: {p.text}" + (f"   (via iGate {via})" if via else ""))
        if p.msg_id:
            self.reply(aprs.build_message(p.source, f"ack{p.msg_id}"))

    def _role(self, call: str, role: str) -> None:
        """Remember what a station has been seen doing, and redraw it if that changes its icon."""
        have = self.roles.setdefault(call, set())
        if role not in have:
            have.add(role)
            self.dirty = True
            if call in self.stations:
                self._station(call)
