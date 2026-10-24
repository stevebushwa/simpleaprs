import json
import urllib.error
import urllib.request

import pytest

from simpleaprs.netshare import Hub, ShareServer, validate_tx


@pytest.fixture
def make_server():
    servers = []

    def make(token="secret", require_token=True, allow_tx=True):
        hub, sent = Hub(), []
        srv = ShareServer(hub, 0, token, require_token, allow_tx, lambda: {"callsign": "N0CALL"}, sent.append, host="127.0.0.1")
        servers.append(srv)
        return hub, srv, sent

    yield make
    for s in servers:
        s.stop()


def url(srv, path):
    return f"http://127.0.0.1:{srv.port}{path}"


def events(srv, token=None, n=4):
    req = urllib.request.Request(url(srv, "/api/events"), headers={"Authorization": f"Bearer {token}"} if token else {})
    out = []
    with urllib.request.urlopen(req, timeout=5) as r:
        for line in r:
            if line.startswith(b"data: "):
                out.append(json.loads(line[6:]))
                if len(out) == n:
                    return out


def post(srv, body, token=None):
    req = urllib.request.Request(url(srv, "/api/tx"), data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_replay_then_sync_then_live(make_server):
    hub, srv, _ = make_server()
    hub.publish({"t": 1.0, "raw": "aa"})
    hub.publish({"t": 2.0, "raw": "bb", "tx": True})
    got = events(srv, "secret", n=4)
    assert got[0]["hello"]["callsign"] == "N0CALL" and got[0]["hello"]["allow_tx"] is True
    assert [g.get("raw") for g in got[1:3]] == ["aa", "bb"] and got[3] == {"sync": True}


def test_live_events_after_sync(make_server):
    import threading, time
    hub, srv, _ = make_server()
    threading.Timer(0.5, hub.publish, [{"t": 3.0, "raw": "cc"}]).start()
    got = events(srv, "secret", n=3)  # hello, sync, live
    assert got[2]["raw"] == "cc"


def test_view_needs_token_by_default(make_server):
    _, srv, _ = make_server()
    for tok in (None, "wrong"):
        with pytest.raises(urllib.error.HTTPError) as e:
            events(srv, tok)
        assert e.value.code == 401


def test_view_without_auth_when_turned_off(make_server):
    _, srv, _ = make_server(require_token=False)
    assert events(srv, None, n=2)[1] == {"sync": True}


def test_tx_always_needs_token_even_when_viewing_is_open(make_server):
    _, srv, sent = make_server(require_token=False)
    assert post(srv, {"type": "beacon"})[0] == 401
    assert post(srv, {"type": "beacon"}, "wrong")[0] == 401
    assert sent == []
    assert post(srv, {"type": "beacon"}, "secret") == (200, {"ok": True})
    assert sent == [{"type": "beacon"}]


def test_tx_can_be_disabled(make_server):
    _, srv, sent = make_server(allow_tx=False)
    assert post(srv, {"type": "beacon"}, "secret")[0] == 403 and sent == []


def test_empty_token_never_authenticates(make_server):
    _, srv, sent = make_server(token="")
    assert post(srv, {"type": "beacon"}, "")[0] == 401 and sent == []


def test_validate_tx():
    assert validate_tx({"type": "message", "to": "w5abc-7", "text": " hi "}) == ({"type": "message", "to": "W5ABC-7", "text": "hi"}, "")
    for bad in ({"type": "frame", "raw": "00"}, {"type": "message", "to": "NOT A CALL", "text": "x"},
                {"type": "message", "to": "W5ABC", "text": ""}, {"type": "message", "to": "W5ABC", "text": "x" * 68},
                {"type": "message", "to": "W5ABC", "text": "a|b"}, {"type": "message", "to": "W5ABC", "text": "tab\there"}, [], "x"):
        assert validate_tx(bad)[0] is None, bad


def test_validate_tx_bulletin():
    assert validate_tx({"type": "bulletin", "to": "bln3", "text": " net at 8 "}) == ({"type": "bulletin", "to": "BLN3", "text": "net at 8"}, "")
    assert validate_tx({"type": "bulletin", "to": "blnx", "text": "evacuate"})[0] == {"type": "bulletin", "to": "BLNX", "text": "evacuate"}
    for bad in ({"type": "bulletin", "to": "W5ABC", "text": "x"}, {"type": "bulletin", "to": "BLNY", "text": "x"}, {"type": "bulletin", "to": "BLN", "text": "x"},
                {"type": "bulletin", "to": "BLN12", "text": "x"}, {"type": "bulletin", "to": "BLN1", "text": ""},
                {"type": "bulletin", "to": "BLN1", "text": "x" * 68}, {"type": "bulletin", "to": "BLN1", "text": "a{1"}):
        assert validate_tx(bad)[0] is None, bad


def test_post_rejects_junk(make_server):
    _, srv, sent = make_server()
    assert post(srv, {"type": "message", "to": "W5ABC", "text": "a{b"}, "secret")[0] == 400 and sent == []


def test_snapshot_sent_after_hello(make_server):
    hub, srv, _ = make_server()
    srv.stop()
    hub2 = Hub()
    srv2 = ShareServer(hub2, 0, "secret", True, True, lambda: {"callsign": "N0CALL"}, lambda r: None,
                       host="127.0.0.1", snapshot=lambda: {"stations": [{"raw": "aa", "heard": 1.0}], "roles": {"W5X": ["digi"]}})
    try:
        got = events(srv2, "secret", n=3)
        assert "hello" in got[0] and got[1]["snapshot"]["roles"] == {"W5X": ["digi"]} and got[2] == {"sync": True}
    finally:
        srv2.stop()


# --- open access mode and the web stream (what the browser page uses) -------------------------------

from simpleaprs.netshare import host_ok


class FakeWeb:
    protected = False

    def __init__(self):
        self.hub = Hub()

    def session_ok(self, h):
        return True

    def handle_post(self, h):
        return False

    def intro(self):
        return [{"type": "me", "call": "N0CALL"}]

    def handle(self, h):
        if h.path != "/":
            return False
        h.send_response(200)
        h.send_header("Content-Length", "4")
        h.end_headers()
        h.wfile.write(b"page")
        return True


@pytest.fixture
def open_server():
    web = FakeWeb()
    sent = []
    srv = ShareServer(Hub(), 0, "", False, True, lambda: {"callsign": "N0CALL"}, sent.append, host="127.0.0.1",
                      web=web, open_access=True)
    yield srv, web, sent
    srv.stop()


def req(srv, path, body=None, headers=None):
    r = urllib.request.Request(url(srv, path), data=json.dumps(body).encode() if body is not None else None,
                               method="POST" if body is not None else "GET", headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_open_access_needs_no_token(open_server):
    srv, _, sent = open_server
    assert req(srv, "/api/tx", {"type": "beacon"}) == (200, b'{"ok": true}') and sent == [{"type": "beacon"}]
    assert req(srv, "/")[1] == b"page"
    assert events(srv, None, n=2)[1] == {"sync": True}  # /api/events open too


def test_web_stream_intro_replay_and_live(open_server):
    srv, web, _ = open_server
    web.hub.publish({"type": "packet", "t": 1.0, "text": "kept"})
    web.hub.publish({"type": "station", "call": "W1AW"}, keep=False)  # live only, the intro carries current state
    r = urllib.request.urlopen(url(srv, "/web/events"), timeout=5)
    got = [json.loads(line[6:]) for _, line in zip(range(3), (l for l in r if l.startswith(b"data: ")))]
    assert got == [{"type": "me", "call": "N0CALL"}, {"type": "packet", "t": 1.0, "text": "kept"}, {"sync": True}]


def test_open_access_refuses_other_sites(open_server):
    srv, _, sent = open_server
    for headers in ({"Host": "evil.example.com"}, {"Host": "evil.example.com:8073"}):
        assert req(srv, "/api/tx", {"type": "beacon"}, headers)[0] == 403
        assert req(srv, "/web/events", None, headers)[0] == 403
    assert req(srv, "/api/tx", {"type": "beacon"}, {"Origin": "http://evil.example.com"})[0] == 403  # cross-site page, right Host
    assert sent == []
    assert req(srv, "/api/tx", {"type": "beacon"}, {"Origin": f"http://127.0.0.1:{srv.port}"})[0] == 200  # our own page


def test_open_access_wants_json(open_server):
    srv, _, sent = open_server
    r = urllib.request.Request(url(srv, "/api/tx"), data=b'{"type": "beacon"}', method="POST", headers={"Content-Type": "text/plain"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(r, timeout=5)
    assert e.value.code == 415 and sent == []


def test_guards_are_off_unless_open_access(make_server):
    _, srv, sent = make_server()
    assert req(srv, "/api/tx", {"type": "beacon"}, {"Host": "evil.example.com", "Authorization": "Bearer secret"})[0] == 200


def test_host_ok():
    for good in ("192.168.1.5:8073", "[::1]:8073", "localhost", "localhost:8073", "raspberrypi", "pi.local:8073", "10.0.0.2"):
        assert host_ok(good), good
    for bad in ("evil.example.com", "evil.example.com:8073", "", "a.b"):
        assert not host_ok(bad), bad
    assert host_ok("pi.home.arpa:8073", ("pi.home.arpa",))
