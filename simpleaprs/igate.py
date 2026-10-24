"""An APRS-IS gateway: pass what this radio hears on to the internet, and optionally messages back.

RF -> internet is the normal iGate job: every packet heard (that isn't already from the internet, and isn't
asking not to be gated) is sent to an APRS-IS server with a "qAR,<our call>" marker. Internet -> RF is
much more restricted, because it makes us transmit: only *messages*, only to a station we heard directly on
RF in the last half hour, wrapped as a third-party packet, and rate limited.

No Qt in here: the host (the desktop window) supplies callbacks, which are called from the IGate's thread.
"""
from __future__ import annotations

import queue
import socket
import threading
import time

from . import aprs
from .ax25 import Frame

DEFAULT_SERVER = "rotate.aprs2.net:14580"  # 14580 is the port that takes a user filter
HEARD_S = 30 * 60      # a station counts as "on our RF" this long after we last heard it directly
DUPE_S = 30            # the same packet heard again (via another digipeater) inside this is not gated twice
MAX_TO_RF_PER_MIN = 10
BEACON_S = 30 * 60     # how often our own position goes to the server (it is what the server's m/ filter measures from)
KEEPALIVE_S = 60
SILENT_S = 90          # servers send a comment line every 20 s; this long without one means the link is dead
NO_GATE = ("TCPIP", "TCPXX", "NOGATE", "RFONLY")  # heard on RF: from the internet already, or asks not to be gated
NO_GATE_TO_RF = ("TCPXX", "NOGATE", "RFONLY")  # from the internet: TCPIP is normal there, these are not
NOT_STATIONS = ("N0CALL", "NOCALL")
VERSION = "0.1"


def passcode(call: str) -> int:
    """The APRS-IS passcode for a callsign (SSID ignored). It is the long-standing public hash, used by
    every client; servers use it to tell whether a login may send packets."""
    call = call.split("-")[0].upper()
    h = 0x73E2
    for i in range(0, len(call), 2):
        h ^= ord(call[i]) << 8
        if i + 1 < len(call):
            h ^= ord(call[i + 1])
    return h & 0x7FFF


def parse_server(text: str) -> tuple[str, int]:
    host, _, port = text.strip().partition(":")
    return host, int(port) if port.isdigit() else 14580


def rf_to_is(frame: Frame, mycall: str) -> str | None:
    """The APRS-IS line for a packet heard on RF, or None if it must not be gated."""
    src = str(frame.src).rstrip("*")
    info = frame.info
    if not info or src == mycall or src.split("-")[0] in NOT_STATIONS:
        return None
    if info[:1] in (b"}", b"?") or any(c in info for c in (b"\r", b"\n", b"\0")):  # third-party, queries, line breakers
        return None
    for a in frame.path:
        name = a.call.upper()
        if name.startswith(NO_GATE) or (name.startswith("Q") and len(name) == 3):  # already from the internet, or asks not to be
            return None
    via = "".join("," + str(a) for a in frame.path)
    return f"{src}>{frame.dst}{via},qAR,{mycall}:{info.decode('latin-1')}"


def is_to_rf(line: str, mycall: str, heard) -> Frame | None:
    """The packet to transmit for a line from the server, or None. heard(call) says whether we have
    heard that station directly on RF recently. Only messages to such stations qualify."""
    if line.startswith("#"):
        return None
    head, sep, payload = line.partition(":")
    src, arrow, rest = head.partition(">")
    dst, *via = rest.split(",")
    if not (sep and arrow and src and len(payload) >= 11 and payload[0] == ":" and payload[10] == ":"):
        return None
    to = payload[1:10].strip().upper()
    if aprs.is_bulletin(to) or to == mycall or not heard(to):
        return None
    if any(v.upper().startswith(NO_GATE_TO_RF) for v in via):
        return None
    third = f"}}{src}>{dst},TCPIP,{mycall}*:{payload}".encode("latin-1", "replace")
    if len(third) > 250:
        return None
    return aprs.make_frame(mycall, third, "")


class _Fatal(Exception):
    """Not worth retrying until the user changes something."""


class IGate:
    def __init__(self, cfg, on_status, on_to_rf) -> None:
        """on_status(text) reports the connection; on_to_rf(raw_frame) asks the host to transmit. Both
        are called from the gateway's thread."""
        self.call = cfg.callsign.upper()
        self.server, self.passcode = cfg.igate_server.strip() or DEFAULT_SERVER, cfg.igate_passcode.strip()
        self.to_rf, self.radius = cfg.igate_to_rf, cfg.igate_radius_km
        self.lat, self.lon, self.symbol, self.comment = cfg.lat, cfg.lon, cfg.symbol, cfg.comment
        self.on_status, self.on_to_rf = on_status, on_to_rf
        self.gated = self.sent_to_rf = 0
        self._out: queue.Queue[str] = queue.Queue(maxsize=500)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._heard: dict[str, float] = {}
        self._seen: dict[tuple, float] = {}     # RF side: packets already gated
        self._seen_is: dict[str, float] = {}    # internet side (the gateway's thread only): messages already sent to RF
        self._to_rf_times: list[float] = []
        self._online = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # --- from the radio -------------------------------------------------------------------------------

    def heard_direct(self, call: str) -> bool:
        with self._lock:
            t = self._heard.get(call.upper())
        return t is not None and time.monotonic() - t < HEARD_S

    def send_rf(self, frame: Frame) -> None:
        """A frame the radio decoded."""
        now = time.monotonic()
        src = str(frame.src).rstrip("*")
        if not any(a.repeated for a in frame.path):  # no hop used: the sender is in direct range
            with self._lock:
                self._heard[src.upper()] = now
                if len(self._heard) > 2000:
                    self._heard = {c: t for c, t in self._heard.items() if now - t < HEARD_S}
        if not self._online:
            return
        line = rf_to_is(frame, self.call)
        if line is None:
            return
        key = (src, str(frame.dst), frame.info)
        with self._lock:
            if now - self._seen.get(key, -DUPE_S) < DUPE_S:
                return
            self._seen[key] = now
            if len(self._seen) > 1000:
                self._seen = {k: t for k, t in self._seen.items() if now - t < DUPE_S}
        try:
            self._out.put_nowait(line)
        except queue.Full:
            pass

    # --- the connection -------------------------------------------------------------------------------

    def _status(self, text: str) -> None:
        if not self._stop.is_set():  # a gateway that was stopped must not speak for the one that replaced it
            self.on_status(text)

    def _counts(self) -> str:
        return f"{self.gated} gated to the internet" + (f", {self.sent_to_rf} to RF" if self.to_rf else "")

    def _run(self) -> None:
        wait = 5
        while not self._stop.is_set():
            try:
                self._session()
                wait = 5
            except _Fatal as e:
                self._status(f"iGate: {e}")
                return
            except OSError as e:
                self._status(f"iGate: disconnected ({e}); retrying")
            self._online = False
            if self._stop.wait(wait):
                return
            wait = min(wait * 2, 120)

    def _session(self) -> None:
        if not self.passcode or not self.passcode.lstrip("-").isdigit() or self.passcode == "-1":
            raise _Fatal("enter your APRS-IS passcode in Settings")
        host, port = parse_server(self.server)
        self._status(f"iGate: connecting to {host}...")
        with socket.create_connection((host, port), timeout=15) as s:
            s.settimeout(1)
            while not self._out.empty():  # whatever queued up before this connection is stale
                self._out.get_nowait()
            login = f"user {self.call} pass {self.passcode} vers simpleaprs {VERSION}"
            if self.to_rf:
                login += f" filter m/{self.radius}"
            s.sendall(login.encode() + b"\r\n")
            buf, last_rx, last_tx, last_beacon = b"", time.monotonic(), time.monotonic(), float("-inf")
            while not self._stop.is_set():
                try:
                    data = s.recv(4096)
                    if not data:
                        raise OSError("closed by the server")
                    buf, last_rx = buf + data, time.monotonic()
                except socket.timeout:
                    pass
                while b"\n" in buf:
                    raw, _, buf = buf.partition(b"\n")
                    self._on_line(raw.decode("latin-1").rstrip("\r"))
                now = time.monotonic()
                if now - last_rx > SILENT_S:
                    raise OSError("the server went quiet")
                if self._online:
                    if now - last_beacon > BEACON_S and (self.lat or self.lon):
                        last_beacon = now
                        s.sendall(self._own_position().encode("latin-1", "replace") + b"\r\n")
                    sent = False
                    while True:
                        try:
                            s.sendall(self._out.get_nowait().encode("latin-1", "replace") + b"\r\n")
                        except queue.Empty:
                            break
                        self.gated += 1
                        sent = True
                    if sent:
                        last_tx = now
                        self._status(f"iGate: connected to {host} - {self._counts()}")
                if now - last_tx > KEEPALIVE_S:
                    s.sendall(b"#simpleaprs keepalive\r\n")
                    last_tx = now

    def _own_position(self) -> str:
        info = aprs.build_position(self.lat, self.lon, self.symbol, self.comment).decode("latin-1")
        return f"{self.call}>APSMPL,TCPIP*:{info}"

    def _on_line(self, line: str) -> None:
        if line.startswith("# logresp"):
            if "unverified" in line:
                raise _Fatal("the server did not accept the passcode (check it matches your callsign)")
            self._online = True
            self._status(f"iGate: connected to {parse_server(self.server)[0]} - {self._counts()}")
        elif self.to_rf and self._online and not line.startswith("#"):
            self._maybe_to_rf(line)

    def _maybe_to_rf(self, line: str) -> None:
        frame = is_to_rf(line, self.call, self.heard_direct)
        if frame is None:
            return
        now = time.monotonic()
        key = line.partition(":")[2]  # the same message can arrive more than once; send it once
        if now - self._seen_is.get(key, -DUPE_S * 2) < DUPE_S * 2:
            return
        self._seen_is = {k: t for k, t in self._seen_is.items() if now - t < DUPE_S * 2}
        self._seen_is[key] = now
        self._to_rf_times = [t for t in self._to_rf_times if now - t < 60]
        if len(self._to_rf_times) >= MAX_TO_RF_PER_MIN:
            return
        self._to_rf_times.append(now)
        if self._stop.is_set():
            return
        self.sent_to_rf += 1
        self.on_to_rf(frame.encode())
