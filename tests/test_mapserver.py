import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from simpleaprs.mapserver import MapProxy, MapServer


@pytest.fixture
def nomad():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"tiles": f"http://127.0.0.1:{srv.server_address[1]}/tiles/{{z}}"}).encode() if self.path == "/api/maps/styles" else b"tile"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(m, path):
    try:
        with urllib.request.urlopen(m.base + path, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_serves_page_files_and_proxies_the_map(nomad):
    m = MapServer(nomad)
    try:
        assert b"map.js" in get(m, "/")[1]
        assert get(m, "/map.js")[0] == 200 and get(m, "/map.css")[0] == 200
        assert get(m, "/glyphs.js")[1].startswith(b"const GLYPHS")
        assert get(m, "/nomad/tiles/1") == (200, b"tile")
        style = json.loads(get(m, "/style.json")[1])
        assert style["tiles"] == f"{m.base}/nomad/tiles/{{z}}"  # points back at us, not at the map server
    finally:
        m.stop()


def test_unknown_and_escaping_paths_are_not_found(nomad):
    m = MapServer(nomad)
    try:
        assert get(m, "/nope.html")[0] == 404
        assert get(m, "/..%2Fconfig.py")[0] == 404
    finally:
        m.stop()


def test_no_map_server_set_is_a_clean_failure():
    from simpleaprs.config import Config
    assert Config().nomad_url == ""  # nobody's private address is baked in
    sent = []
    p = MapProxy("")
    p.send = lambda h, code, body, ctype: sent.append(code)
    p._style(None, "http://x")
    p._proxy(type("H", (), {"path": "/nomad/x.pmtiles", "headers": {}})())
    assert sent == [502, 502]
