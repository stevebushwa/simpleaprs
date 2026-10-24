"""Share the radio over HTTP.

GET  /api/events  Server-Sent Events: a hello (who this station is), a snapshot of the stations the
                  server remembers, a replay of recent traffic, a {"sync": true} marker, then everything
                  heard or sent from then on.
POST /api/tx      Ask the server to beacon, or send a message or bulletin, through its radio (token always required).

Clients get frames, not decoded packets, so they run the same code on them as the radio would.

A server can also carry a web page (see webui.py): GET /web/events is the same kind of stream but of
decoded events, and the page itself and its map files are served from the same port. Such a server runs
in "open access" mode, for a LAN: no token, but requests must look like they came from a page on this
server (right Host, same Origin, JSON body) so a web page elsewhere can't make your browser use it.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import queue
import re
import secrets
import socket
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BACKLOG = 3000  # events replayed to a new client
MAX_CLIENTS = 20
KEEPALIVE_S = 15
_CALL = re.compile(r"^[A-Z0-9]{1,6}(-\d{1,2})?$")
_BULLETIN = re.compile(r"^BLN\d$")


def new_token() -> str:
    return secrets.token_urlsafe(12)


def lan_ip() -> str:
    """The address other machines on the network would use (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def host_name(host_header: str) -> str:
    """The name part of a Host header: no port, no IPv6 brackets."""
    if host_header.startswith("["):
        return host_header[1:].split("]")[0]
    return host_header.rsplit(":", 1)[0] if host_header.count(":") == 1 else host_header


def host_ok(host_header: str, extra: tuple[str, ...] = ()) -> bool:
    """Is this a name a LAN machine would be reached by? An address, localhost, this machine's hostname,
    a .local or one-word name, or one the user listed. Anything else is a web site pointing at us
    (DNS rebinding), not a person on the network."""
    name = host_name(host_header).lower()
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    return bool(name) and ("." not in name or name.endswith(".local") or name == "localhost"
                           or name == socket.gethostname().lower() or name in extra)


def validate_tx(d) -> tuple[dict | None, str]:
    """Check a TX request and reduce it to the few things a client may ask for."""
    if not isinstance(d, dict):
        return None, "bad request"
    kind = d.get("type")
    if kind == "beacon":
        return {"type": "beacon"}, ""
    if kind in ("message", "bulletin"):  # a bulletin is a message to BLN0-BLN9, sent without a number to ack
        to, text = str(d.get("to", "")).strip().upper(), str(d.get("text", "")).strip()
        if not (_BULLETIN if kind == "bulletin" else _CALL).match(to):
            return None, "bad bulletin number" if kind == "bulletin" else "bad callsign"
        if not 1 <= len(text) <= 67:
            return None, f"{kind} must be 1-67 characters"
        if not all(32 <= ord(c) < 127 for c in text) or any(c in text for c in "|~{"):
            return None, f"{kind} has characters APRS does not allow"
        return {"type": kind, "to": to, "text": text}, ""
    return None, "unknown request type"


class Hub:
    """Fans events out to connected clients and keeps a replay buffer for new ones."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._log: deque[dict] = deque(maxlen=BACKLOG)
        self._subs: list[queue.Queue] = []

    def publish(self, ev: dict, keep: bool = True) -> None:
        """Send ev to everyone connected. keep=False leaves it out of the replay for new clients."""
        with self._lock:
            if keep:
                self._log.append(ev)
            for q in list(self._subs):
                try:
                    q.put_nowait(ev)
                except queue.Full:  # a client that stopped reading: drop it rather than grow forever
                    self._subs.remove(q)

    def subscribe(self) -> tuple[queue.Queue, list[dict]]:
        q: queue.Queue = queue.Queue(maxsize=2000)
        with self._lock:
            self._subs.append(q)
            return q, list(self._log)

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def subscribed(self, q: queue.Queue) -> bool:
        with self._lock:
            return q in self._subs

    def count(self) -> int:
        with self._lock:
            return len(self._subs)


class ShareServer:
    def __init__(self, hub: Hub, port: int, token: str, require_token: bool, allow_tx: bool, hello, on_tx,
                 host: str = "0.0.0.0", snapshot=lambda: {}, web=None, open_access: bool = False,
                 allowed_hosts: tuple[str, ...] = ()) -> None:
        """hello() -> dict describing this station; snapshot() -> the stations it remembers;
        on_tx(request) is called from a server thread.

        web, if given, adds the browser page: web.hub and web.intro() feed /web/events, and
        web.handle(handler) -> bool serves everything else (the page, map files). open_access
        turns the token off (watching and transmitting) and checks where requests come from instead."""
        self.hub, self.token, self.require_token, self.allow_tx = hub, token, require_token, allow_tx
        self.open_access, self.allowed_hosts = open_access, tuple(h.lower() for h in allowed_hosts)
        self.stopped = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 15  # drop connections that stall while sending their request

            def log_message(self, *a):
                pass

            def _token_ok(self) -> bool:
                if outer.open_access:
                    return True
                auth = self.headers.get("Authorization", "")
                got = auth[7:] if auth.startswith("Bearer ") else ""
                return bool(outer.token) and hmac.compare_digest(got.encode(), outer.token.encode())

            def _reply(self, code: int, obj, ctype: str = "application/json") -> None:
                body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _from_here(self, post: bool = False) -> bool:
                """In open access mode, only requests that came from a page on this server (or no browser
                at all) get through. Replies with the reason if not."""
                if not outer.open_access:
                    return True
                if not host_ok(self.headers.get("Host", ""), outer.allowed_hosts):
                    self._reply(403, {"error": "unknown host name: use this server's IP address, or start it with --allow-host"})
                elif self.headers.get("Origin") and urlparse(self.headers["Origin"]).netloc != self.headers.get("Host"):
                    self._reply(403, {"error": "cross-site request"})
                elif post and self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                    self._reply(415, {"error": "send JSON"})
                else:
                    return True
                return False

            def do_GET(self):
                try:
                    if not self._from_here():
                        return
                    if self.path == "/api/events":
                        self._events()
                    elif self.path == "/web/events" and web:
                        self._stream(web.hub, web.intro)
                    elif web and web.handle(self):
                        pass
                    elif self.path == "/":
                        self._reply(200, "simpleaprs share server\n", "text/plain")
                    else:
                        self._reply(404, {"error": "not found"})
                except (OSError, ValueError):
                    pass

            def _events(self) -> None:
                if outer.require_token and not self._token_ok():
                    return self._reply(401, {"error": "access token required"})

                def intro() -> list[dict]:
                    snap = snapshot()
                    return [{"hello": {**hello(), "allow_tx": outer.allow_tx}}] + ([{"snapshot": snap}] if snap else [])
                self._stream(outer.hub, intro)

            def _stream(self, hub: Hub, intro) -> None:
                """Server-Sent Events: intro() first, then hub's replay, a sync marker, then live events."""
                if hub.count() >= MAX_CLIENTS:
                    return self._reply(503, {"error": "too many clients"})
                q, backlog = hub.subscribe()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    for ev in intro():
                        self._send(ev)
                    for ev in backlog:
                        self._send(ev)
                    self._send({"sync": True})
                    idle = 0
                    while not outer.stopped and hub.subscribed(q):
                        try:
                            self._send(q.get(timeout=1))
                            idle = 0
                        except queue.Empty:
                            idle += 1
                            if idle >= KEEPALIVE_S:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                                idle = 0
                except OSError:
                    pass
                finally:
                    hub.unsubscribe(q)

            def _send(self, ev: dict) -> None:
                self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
                self.wfile.flush()

            def do_POST(self):
                try:
                    if not self._from_here(post=True):
                        return
                    if self.path != "/api/tx":
                        return self._reply(404, {"error": "not found"})
                    if not self._token_ok():  # never open because of the view setting; only open access mode turns the token off
                        return self._reply(401, {"error": "access token required"})
                    if not outer.allow_tx:
                        return self._reply(403, {"error": "transmitting from clients is turned off on this server"})
                    n = int(self.headers.get("Content-Length", "0"))
                    if not 0 < n <= 2048:
                        return self._reply(400, {"error": "bad request"})
                    req, err = validate_tx(json.loads(self.rfile.read(n)))
                    if req is None:
                        return self._reply(400, {"error": err})
                    on_tx(req)
                    self._reply(200, {"ok": True})
                except (OSError, ValueError):
                    pass

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.stopped = True
        self.httpd.shutdown()
        self.httpd.server_close()
