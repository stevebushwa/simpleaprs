"""A threaded HTTP server for a web page on a LAN.

It serves the page (a `web` object: see webbase.BaseWebUI), a Server-Sent Events stream of what the page should
show at GET /web/events, and whatever an app adds through the extra_get / extra_post hooks. Requests to a server
that has a login (or open access) must look like they came from a page on it (right Host, same Origin, JSON body),
so a web page elsewhere can't make your browser use it.
"""
from __future__ import annotations

import ipaddress
import json
import queue
import secrets
import socket
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlparse

from .hub import Hub

MAX_CLIENTS = 20
KEEPALIVE_S = 15


class _Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        """A client that gave up, or a browser turning back at the certificate warning, is not worth a traceback."""
        if not isinstance(sys.exc_info()[1], OSError):
            super().handle_error(request, client_address)


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


class WebServer:
    def __init__(self, port: int, web=None, host: str = "0.0.0.0", allowed_hosts: tuple[str, ...] = (),
                 open_access: bool = False, banner: str = "",
                 extra_get: Callable[[BaseHTTPRequestHandler], bool] | None = None,
                 extra_post: Callable[[BaseHTTPRequestHandler], bool] | None = None,
                 tls: ssl.SSLContext | None = None) -> None:
        """web: the page (hub, intro(), session_ok(h), protected, handle(h), handle_post(h)), or None.
        extra_get / extra_post(handler) -> True if they answered the request. open_access skips the login
        and checks where requests come from instead. banner is what GET / says when nothing else does. tls: serve
        https on the port (a plain http request to it is sent on to https instead of being refused)."""
        self.web, self.open_access, self.banner, self.tls = web, open_access, banner, tls
        self.allowed_hosts = tuple(h.lower() for h in allowed_hosts)
        self.stopped = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 15  # drop connections that stall while sending their request
            plain = False  # a plain http request that came to the https port

            def log_message(self, *a):
                pass

            def setup(self):
                if outer.tls:  # one port for both: a TLS handshake starts with 0x16, anything else is someone typing http://
                    self.request.settimeout(self.timeout)
                    if self.request.recv(1, socket.MSG_PEEK) == b"\x16":
                        self.request = outer.tls.wrap_socket(self.request, server_side=True)
                    else:
                        self.plain = True
                super().setup()

            def handle(self):
                if self.plain:
                    return self._to_https()
                super().handle()

            def _to_https(self) -> None:
                """Answer a plain http request with where to find the page over https."""
                parts = self.rfile.readline(2048).decode("latin-1").split()
                host = ""
                for _ in range(64):
                    line = self.rfile.readline(2048).decode("latin-1")
                    if line in ("", "\r\n", "\n"):
                        break
                    if line.lower().startswith("host:"):
                        host = line.split(":", 1)[1].strip()
                if len(parts) < 2 or not parts[1].startswith("/") or not host_ok(host, outer.allowed_hosts):
                    return
                self.wfile.write(f"HTTP/1.1 308 Permanent Redirect\r\nLocation: https://{host}{parts[1]}\r\n"
                                 "Content-Length: 0\r\nConnection: close\r\n\r\n".encode("latin-1"))

            @property
            def secure(self) -> bool:
                return isinstance(self.connection, ssl.SSLSocket)

            def reply(self, code: int, obj, ctype: str = "application/json", headers: tuple = ()) -> None:
                body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in headers:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def read_json(self, limit: int = 2048):
                """The request's JSON body, or None after replying with the problem."""
                n = int(self.headers.get("Content-Length", "0"))
                if not 0 < n <= limit:
                    self.reply(400, {"error": "bad request"})
                    return None
                try:
                    return json.loads(self.rfile.read(n))
                except ValueError:
                    self.reply(400, {"error": "bad request"})
                    return None

            def cookie(self, name: str) -> str:
                for part in self.headers.get("Cookie", "").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == name:
                        return v
                return ""

            def _from_here(self, post: bool = False) -> bool:
                """With a login (or in open access mode), only requests that came from a page on this server
                (or no browser at all) get through. Replies with the reason if not."""
                if not (outer.open_access or (outer.web and outer.web.protected)):
                    return True
                if not host_ok(self.headers.get("Host", ""), outer.allowed_hosts):
                    self.reply(403, {"error": "unknown host name: use this server's IP address, or start it with --allow-host"})
                elif self.headers.get("Origin") and urlparse(self.headers["Origin"]).netloc != self.headers.get("Host"):
                    self.reply(403, {"error": "cross-site request"})
                elif post and self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                    self.reply(415, {"error": "send JSON"})
                else:
                    return True
                return False

            def stream(self, hub: Hub, intro: Callable[[], list[dict]], alive: Callable[[], bool] = lambda: True,
                       replay: float | None = None) -> None:
                """Server-Sent Events: intro() first, then hub's replay (the last `replay` seconds of traffic, or all
                of it), a sync marker, then live events. The stream ends when alive() says no (the viewer logged out)."""
                if hub.count() >= MAX_CLIENTS:
                    return self.reply(503, {"error": "too many clients"})
                since = time.time() - replay if replay is not None else None  # the replay and the page's "older" start from the same moment
                q, backlog = hub.subscribe(since)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    for ev in intro():
                        self.send_event(ev)
                    for ev in backlog:
                        self.send_event(ev)
                    self.send_event({"sync": True, **({"since": since} if since is not None else {})})
                    idle = 0
                    while not outer.stopped and hub.subscribed(q) and alive():
                        try:
                            self.send_event(q.get(timeout=1))
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

            def send_event(self, ev: dict) -> None:
                self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
                self.wfile.flush()

            def do_GET(self):
                try:
                    web = outer.web
                    if not self._from_here():
                        return
                    if extra_get and extra_get(self):
                        pass
                    elif self.path == "/web/events" and web:
                        if not web.session_ok(self):
                            return self.reply(401, {"error": "login required"})
                        self.stream(web.hub, web.intro, lambda: web.session_ok(self), getattr(web, "replay", None))
                    elif web and web.handle(self):
                        pass
                    elif self.path == "/" and outer.banner:
                        self.reply(200, outer.banner, "text/plain")
                    else:
                        self.reply(404, {"error": "not found"})
                except (OSError, ValueError):
                    pass

            def do_POST(self):
                try:
                    if not self._from_here(post=True):
                        return
                    if extra_post and extra_post(self):
                        return
                    if outer.web and outer.web.handle_post(self):
                        return
                    self.reply(404, {"error": "not found"})
                except (OSError, ValueError):
                    pass

        self.httpd = _Server((host, port), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.stopped = True
        self.httpd.shutdown()
        self.httpd.server_close()
