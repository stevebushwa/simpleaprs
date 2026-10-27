"""The map view's side of HTTP: the bundled page/JS, and a proxy for the Project N.O.M.A.D. map
server so the browser sees everything as same-origin (N.O.M.A.D. sends no CORS headers).

MapProxy does the work for whatever server hands it a request; MapServer is one that serves only
the desktop window, on a local port.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import icons
from .tilecache import MAX_ENTRY, TileCache

WEB = Path(__file__).parent / "web"
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}
PASS_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "ETag")


class MapProxy:
    def __init__(self, nomad_url: str, cache: TileCache | None = None, web: tuple[Path, ...] = (WEB,)) -> None:
        self.nomad = nomad_url.rstrip("/")
        self.cache = cache
        self.roots = web  # directories the page's files come from, searched in order

    def find(self, name: str) -> Path | None:
        for root in self.roots:
            f = (root / name).resolve()
            if root.resolve() in f.parents and f.is_file():
                return f
        return None

    def handle(self, h, base: str, index: str = "index.html") -> bool:
        """Answer a GET for the map page's files. base is how the browser reached us (it goes into the
        map style, which must point back here). False if the path isn't ours."""
        path = h.path.split("?")[0]
        try:
            if path.startswith("/nomad/"):
                self._proxy(h)
            elif path == "/style.json":
                self._style(h, base)
            elif path == "/glyphs.js":
                body = f"const GLYPHS = {json.dumps(icons.GLYPHS)}; const SVG_ATTRS = {json.dumps(icons.SVG_ATTRS)};"
                self._send(h, 200, body.encode(), "text/javascript")
            elif path == "/" or self.find(path.lstrip("/")):
                self._static(h, index)
            else:
                return False
        except (BrokenPipeError, ConnectionResetError):
            pass
        return True


    def _send(self, h, code: int, body: bytes, ctype: str) -> None:
        h.send_response(code)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    def _static(self, h, index: str) -> None:
        name = h.path.split("?")[0].lstrip("/") or index
        f = self.find(name)
        if not f:
            return self._send(h, 404, b"not found", "text/plain")
        self._send(h, 200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))

    def _style(self, h, base: str) -> None:
        path = "/api/maps/styles"
        if not self.nomad:  # no map server set
            return self._send(h, 502, b"{}", "application/json")
        try:
            with urllib.request.urlopen(f"{self.nomad}{path}", timeout=10) as r:
                body = r.read().decode()
            if self.cache:
                self.cache.put(path, "", 200, {}, body.encode())
        except OSError:
            hit = self.cache.get(path) if self.cache else None  # offline: the last style we saw
            if not hit:
                return self._send(h, 502, b"{}", "application/json")
            body = hit[2].decode()
        body = body.replace(self.nomad, f"{base}/nomad")
        self._send(h, 200, body.encode(), "application/json")

    def _from_cache(self, h, hit: tuple[int, dict, bytes]) -> None:
        status, headers, body = hit
        h.send_response(status)
        for k, v in headers.items():
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(body)

    def _proxy(self, h) -> None:
        if not self.nomad:
            return self._send(h, 502, b"", "text/plain")
        path, rng = h.path[len("/nomad"):], h.headers.get("Range", "")
        # The first bytes of a PMTiles file (header + root directory) are always asked of the server
        # first: that is how we notice the file changed. Everything else is served from disk if we have it.
        if self.cache and not rng.startswith("bytes=0-"):
            hit = self.cache.get(path, rng)
            if hit:
                return self._from_cache(h, hit)
        req = urllib.request.Request(self.nomad + path)
        if rng:
            req.add_header("Range", rng)
        try:
            r = urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError as e:
            r = e
        except OSError:
            hit = self.cache.get(path, rng) if self.cache else None
            if hit:
                return self._from_cache(h, hit)
            return self._send(h, 502, b"", "text/plain")
        status = r.status if hasattr(r, "status") else r.code
        headers = {k: r.headers[k] for k in PASS_HEADERS if r.headers.get(k)}
        length = int(headers.get("Content-Length", -1))
        if self.cache and status in (200, 206) and 0 <= length <= MAX_ENTRY:
            body = r.read()
            self.cache.put(path, rng, status, headers, body)
            return self._from_cache(h, (status, headers, body))
        h.send_response(status)
        for k, v in headers.items():
            h.send_header(k, v)
        h.end_headers()
        while chunk := r.read(65536):
            h.wfile.write(chunk)


class MapServer(MapProxy):
    def __init__(self, nomad_url: str, cache: TileCache | None = None) -> None:
        super().__init__(nomad_url, cache)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def do_GET(self):
                if not outer.handle(self, outer.base):
                    outer._send(self, 404, b"not found", "text/plain")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.httpd.shutdown()
