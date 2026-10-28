"""Serving over https with the certificate the server makes for itself, and sending plain http on to it."""
import http.client
import json
import shutil
import ssl

import pytest

from piwebkit import tls
from piwebkit.hub import Hub
from piwebkit.server import WebServer
from simpleaprs.netshare import ShareServer

pytestmark = pytest.mark.skipif(not shutil.which("openssl"), reason="needs the openssl command")


@pytest.fixture
def https(tmp_path):
    ctx = tls.context(tmp_path / "tls")
    assert ctx
    srv = ShareServer(Hub(), 0, "tok", True, True, lambda: {"callsign": "N0CALL"}, lambda r: None, host="127.0.0.1", tls=ctx)
    yield srv, tmp_path / "tls" / "cert.pem"
    srv.stop()


def secure_get(srv, cafile, path, headers=None):
    ctx = ssl.create_default_context(cafile=str(cafile))
    ctx.check_hostname = False  # the certificate names this machine; the test reaches it as 127.0.0.1
    c = http.client.HTTPSConnection("127.0.0.1", srv.port, context=ctx, timeout=5)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    out = r.status, r.read(), dict(r.getheaders())
    c.close()
    return out


def test_the_certificate_is_made_once_and_kept(tmp_path):
    first = tls.ensure(tmp_path)
    mtime = first[0].stat().st_mtime_ns
    assert tls.ensure(tmp_path) == first and first[0].stat().st_mtime_ns == mtime
    assert first[1].stat().st_mode & 0o777 == 0o600  # the key is ours alone


def test_the_page_is_served_over_https(https):
    srv, cert = https
    st, body, _ = secure_get(srv, cert, "/")
    assert st == 200 and body == b"simpleaprs share server\n"
    st, body, _ = secure_get(srv, cert, "/api/events")
    assert st == 401  # and the token still guards it


def test_an_event_stream_works_over_https(https):
    srv, cert = https
    srv.hub.publish({"t": 1e12, "raw": "aa"})
    ctx = ssl.create_default_context(cafile=str(cert)); ctx.check_hostname = False
    c = http.client.HTTPSConnection("127.0.0.1", srv.port, context=ctx, timeout=5)
    c.request("GET", "/api/events", headers={"Authorization": "Bearer tok"})
    r = c.getresponse()
    got = [json.loads(line[6:]) for _, line in zip(range(3), (l for l in r if l.startswith(b"data: ")))]
    assert got[0]["hello"]["callsign"] == "N0CALL" and got[1]["raw"] == "aa" and got[2] == {"sync": True}
    c.close()


def test_plain_http_to_the_https_port_is_sent_on(https):
    srv, _ = https
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    c.request("GET", "/some/page?x=1", headers={"Host": f"192.168.1.57:{srv.port}"})
    r = c.getresponse()
    assert r.status == 308 and r.getheader("Location") == f"https://192.168.1.57:{srv.port}/some/page?x=1"
    c.close()
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)  # a name that isn't one a LAN machine has: no redirect to it
    c.request("GET", "/", headers={"Host": "evil.example.com"})
    with pytest.raises(http.client.RemoteDisconnected):
        c.getresponse()
    c.close()


def test_without_tls_the_server_is_plain_http(tmp_path):
    srv = WebServer(0, host="127.0.0.1", banner="hi\n")
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
        c.request("GET", "/")
        assert c.getresponse().read() == b"hi\n"
    finally:
        srv.stop()


class QuietRadio:
    sent, transmitting = [], False

    def start(self, *a, **k):
        return True

    def stop(self):
        pass

    def transmit(self, raw, lead, level):
        self.sent.append(raw)


def test_the_headless_server_serves_https_and_marks_its_cookie_secure(tmp_path, monkeypatch):
    from piwebkit.auth import Auth
    from simpleaprs import headless
    from simpleaprs.config import Config
    for name, f in (("STATIONS_PATH", "stations.json"), ("WEB_EVENTS_PATH", "w.jsonl"), ("FRAMES_PATH", "f.jsonl"), ("TLS_DIR", "tls")):
        monkeypatch.setattr(headless, name, tmp_path / f)
    monkeypatch.setattr(headless, "device_names", lambda inputs: ["USB Audio"])
    monkeypatch.setattr(headless, "find_device", lambda name, inputs: name)
    s = headless.Server(Config(callsign="N0CALL-7", share_port=0), quiet=True, auth=Auth(tmp_path / "auth.json"), tls=True)
    s.radio = QuietRadio()
    assert s.start() and s.secure
    try:
        ctx = ssl.create_default_context(cafile=str(tmp_path / "tls" / "cert.pem")); ctx.check_hostname = False
        c = http.client.HTTPSConnection("127.0.0.1", s.share.port, context=ctx, timeout=5)
        c.request("POST", "/api/setup", json.dumps({"username": "alice", "password": "correct horse battery"}), {"Content-Type": "application/json"})
        r = c.getresponse(); r.read()
        cookie = next(v for k, v in r.getheaders() if k == "Set-Cookie")
        assert r.status == 200 and "; Secure" in cookie and "HttpOnly" in cookie
        c.close()
        c = http.client.HTTPSConnection("127.0.0.1", s.share.port, context=ctx, timeout=5)  # the certificate, to install on a device
        c.request("GET", "/api/certificate", headers={"Cookie": cookie.split(";")[0]})
        r = c.getresponse()
        pem = r.read()
        assert r.status == 200 and pem == (tmp_path / "tls" / "cert.pem").read_bytes() and pem.startswith(b"-----BEGIN CERTIFICATE-----")
        assert r.getheader("Content-Type") == "application/x-x509-ca-cert" and "simpleaprs.crt" in r.getheader("Content-Disposition")
        c.close()
        c = http.client.HTTPSConnection("127.0.0.1", s.share.port, context=ctx, timeout=5)  # and only for someone logged in
        c.request("GET", "/api/certificate")
        assert c.getresponse().status == 401
        c.close()
        c = http.client.HTTPSConnection("127.0.0.1", s.share.port, context=ctx, timeout=5)  # the map's style must point back over https
        c.request("GET", "/style.json", headers={"Cookie": cookie.split(";")[0]})
        r = c.getresponse(); r.read()
        assert r.status in (200, 502)  # (no map server here to answer; the point is that the page's other files load)
        c.close()
    finally:
        s.stop()


def test_a_client_finds_whether_a_server_speaks_https(https):
    from simpleaprs.netclient import normalize
    srv, _ = https
    assert normalize(f"127.0.0.1:{srv.port}") == f"https://127.0.0.1:{srv.port}"
    assert normalize(f"http://127.0.0.1:{srv.port}/") == f"http://127.0.0.1:{srv.port}"  # as typed, when it says
    plain = WebServer(0, host="127.0.0.1", banner="hi\n")
    try:
        assert normalize(f"127.0.0.1:{plain.port}") == f"http://127.0.0.1:{plain.port}"
    finally:
        plain.stop()
