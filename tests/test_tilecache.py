import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from simpleaprs.mapserver import MapServer
from simpleaprs.tilecache import TileCache


class Origin:
    """A stand-in map server: one PMTiles-like file with Range support, plus a style."""

    def __init__(self):
        self.data = bytes(range(256)) * 400
        self.etag = '"v1"'
        self.hits = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits.append((self.path, self.headers.get("Range", "")))
                if self.path == "/api/maps/styles":
                    body = ('{"url":"pmtiles://http://%s/pmtiles/a.pmtiles"}' % outer.host).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return self.wfile.write(body)
                rng = self.headers.get("Range", "")
                a, b = (int(x) for x in rng.split("=")[1].split("-"))
                chunk = outer.data[a:b + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {a}-{a + len(chunk) - 1}/{len(outer.data)}")
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header("ETag", outer.etag)
                self.end_headers()
                self.wfile.write(chunk)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown(); self.httpd.server_close()


@pytest.fixture
def rig(tmp_path):
    origin = Origin()
    cache = TileCache(tmp_path / "c", max_bytes=10_000_000)
    ms = MapServer(f"http://{origin.host}", cache)
    yield origin, cache, ms
    ms.stop()
    try:
        origin.stop()
    except Exception:
        pass


def get(ms, path, rng=None):
    req = urllib.request.Request(ms.base + "/nomad" + path, headers={"Range": rng} if rng else {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read()


P = "/pmtiles/a.pmtiles"


def test_second_read_comes_from_disk(rig):
    origin, _, ms = rig
    first = get(ms, P, "bytes=1000-1099")
    n = len(origin.hits)
    assert get(ms, P, "bytes=1000-1099") == first and first[0] == 206 and len(first[1]) == 100
    assert len(origin.hits) == n  # no second request to the server


def test_header_range_always_asks_server(rig):
    origin, _, ms = rig
    get(ms, P, "bytes=0-99"); get(ms, P, "bytes=0-99")
    assert len(origin.hits) == 2


def test_works_with_server_gone(rig):
    origin, _, ms = rig
    get(ms, P, "bytes=0-99"); get(ms, P, "bytes=500-599")
    origin.stop()
    assert get(ms, P, "bytes=500-599")[1] == origin.data[500:600]
    assert get(ms, P, "bytes=0-99")[1] == origin.data[0:100]  # even the header, as a fallback
    with pytest.raises(urllib.error.HTTPError) as e:
        get(ms, P, "bytes=700-799")  # never cached
    assert e.value.code == 502


def test_changed_file_invalidates(rig):
    origin, _, ms = rig
    get(ms, P, "bytes=0-99"); get(ms, P, "bytes=500-599")
    origin.data = bytes(reversed(origin.data)); origin.etag = '"v2"'
    assert get(ms, P, "bytes=0-99")[1] == origin.data[0:100]  # header request notices the new ETag
    n = len(origin.hits)
    assert get(ms, P, "bytes=500-599")[1] == origin.data[500:600]  # not the stale v1 bytes
    assert len(origin.hits) == n + 1  # had to be fetched again


def test_style_falls_back_to_cached(rig):
    origin, _, ms = rig
    with urllib.request.urlopen(ms.base + "/style.json", timeout=5) as r:
        a = r.read()
    origin.stop()
    with urllib.request.urlopen(ms.base + "/style.json", timeout=5) as r:
        assert r.read() == a and b"/nomad/pmtiles/a.pmtiles" in a


def test_eviction_keeps_recent(tmp_path):
    c = TileCache(tmp_path, max_bytes=3000)
    for i in range(5):
        c.put("/f", f"bytes={i}-", 206, {"Content-Range": "bytes 0-1/9"}, bytes(1000))
        c.get("/f", "bytes=0-")  # keep the first one hot
        time.sleep(0.03)  # file times need to differ for "least recently used" to mean anything
    assert c.size <= 3000 and c.get("/f", "bytes=0-") is not None and c.get("/f", "bytes=1-") is None


def test_clear_and_persisted_size(tmp_path):
    c = TileCache(tmp_path, max_bytes=10**6)
    c.put("/f", "bytes=1-2", 206, {}, b"xy")
    assert TileCache(tmp_path).size == c.size > 0
    c.clear()
    assert c.size == 0 and c.get("/f", "bytes=1-2") is None
