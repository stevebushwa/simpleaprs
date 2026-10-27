"""The map view's side of HTTP for simpleaprs: piwebkit's map proxy with simpleaprs's own page files and the APRS
symbol glyphs added, and MapServer, which serves only the desktop window, on a local port.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from piwebkit import maps
from piwebkit.tilecache import TileCache

from . import icons

WEB = Path(__file__).parent / "web"
TILES_DIR = Path.home() / ".cache" / "simpleaprs" / "tiles"


def tile_cache(max_mb: int) -> TileCache:
    return TileCache(TILES_DIR, max_bytes=max_mb * 1024 * 1024)


def glyphs() -> tuple[bytes, str]:
    return f"const GLYPHS = {json.dumps(icons.GLYPHS)}; const SVG_ATTRS = {json.dumps(icons.SVG_ATTRS)};".encode(), "text/javascript"


class MapProxy(maps.MapProxy):
    def __init__(self, nomad_url: str, cache: TileCache | None = None) -> None:
        super().__init__(nomad_url, cache, web=(WEB, maps.WEB), extras={"/glyphs.js": glyphs})


class MapServer(MapProxy):
    def __init__(self, nomad_url: str, cache: TileCache | None = None) -> None:
        super().__init__(nomad_url, cache)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def do_GET(self):
                if not outer.handle(self, outer.base):
                    outer.send(self, 404, b"not found", "text/plain")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.httpd.shutdown()
