import json
import time
import urllib.error
import urllib.request

import pytest

from piwebkit.hub import Hub
from simpleaprs.netshare import ShareServer, validate_tx


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
    now = time.time()  # a backlog replays the last month of traffic: a test frame carries a time in it
    hub.publish({"t": now, "raw": "aa"})
    hub.publish({"t": now, "raw": "bb", "tx": True})
    got = events(srv, "secret", n=4)
    assert got[0]["hello"]["callsign"] == "N0CALL" and got[0]["hello"]["allow_tx"] is True
    assert [g.get("raw") for g in got[1:3]] == ["aa", "bb"] and got[3] == {"sync": True}


def test_packets_age_out_after_a_month_but_messages_stay(monkeypatch):
    from piwebkit import hub as hubmod
    clock = [time.time()]
    monkeypatch.setattr(hubmod.time, "time", lambda: clock[0])
    hub, start = Hub(), clock[0]
    hub.publish({"type": "message", "t": start, "text": "first"})
    for i in range(5000):  # no count limit: a quiet month and a busy one are kept the same
        hub.publish({"type": "packet", "t": start, "text": str(i)})
    assert len(hub.subscribe()[1]) == 5001
    clock[0] = start + hubmod.MONTH - 1
    assert len(hub.subscribe()[1]) == 5001
    clock[0] = start + hubmod.MONTH + 1
    assert [e["text"] for e in hub.subscribe()[1]] == ["first"]


def test_a_replay_window_sends_recent_traffic_and_older_comes_on_request():
    hub, now = Hub(), time.time()
    hub.publish({"type": "message", "t": now - 20 * 86400, "text": "old message"})
    for i, days in enumerate((10, 5, 2, 0.5, 0.1)):
        hub.publish({"type": "packet", "t": now - days * 86400, "text": f"p{i}"})
    assert [e["text"] for e in hub.subscribe(since=now - 86400)[1]] == ["old message", "p3", "p4"]  # messages always come
    assert len(hub.subscribe()[1]) == 6  # no window: everything
    got, more = hub.older(now - 86400, 2)
    assert [e["text"] for e in got] == ["p1", "p2"] and more is True  # the two just before, oldest first
    got, more = hub.older(got[0]["t"], 2)
    assert [e["text"] for e in got] == ["p0"] and more is False  # and no message among them
    assert hub.older(now - 30 * 86400, 5) == ([], False)


def test_the_replay_is_saved_in_batches_and_survives_a_restart(tmp_path):
    from piwebkit import hub as hubmod
    path, now = tmp_path / "events.jsonl", time.time()
    hub = Hub()
    hub.load(path)
    for i in range(hubmod.SAVE_EVERY - 1):
        hub.publish({"type": "packet", "t": now, "text": str(i)})
    assert path.read_text() == ""  # held back until there are enough to be worth a write
    hub.publish({"type": "packet", "t": now, "text": "9"})
    assert len(path.read_text().splitlines()) == hubmod.SAVE_EVERY
    hub.publish({"type": "packet", "t": now, "text": "10"})
    hub.publish({"type": "message", "t": now, "text": "hi"})  # a message doesn't wait for a batch
    assert path.read_text().splitlines()[-1] == json.dumps({"type": "message", "t": now, "text": "hi"}, separators=(",", ":"))
    hub.publish({"type": "packet", "t": now, "text": "11"})
    hub.publish({"type": "station", "call": "W1AW"}, keep=False)  # live only, never written
    hub.flush()
    assert "W1AW" not in path.read_text() and len(path.read_text().splitlines()) == hubmod.SAVE_EVERY + 3
    again = Hub()
    again.load(path)
    assert again.subscribe()[1] == hub.subscribe()[1]


def test_a_saved_file_drops_what_has_aged_out_and_what_is_torn(tmp_path):
    path, now = tmp_path / "events.jsonl", time.time()
    path.write_text(json.dumps({"type": "packet", "t": now - 40 * 86400, "text": "old"}) + "\n"
                    + json.dumps({"type": "message", "t": now - 400 * 86400, "text": "kept"}) + "\n"
                    + json.dumps({"type": "packet", "t": now, "text": "new"}) + "\n" + '{"type": "pack')
    hub = Hub()
    hub.load(path)
    assert [e["text"] for e in hub.subscribe()[1]] == ["kept", "new"]
    assert [json.loads(line)["text"] for line in path.read_text().splitlines()] == ["kept", "new"]  # and the file is clean again


def test_the_file_is_rewritten_smaller_once_it_is_too_big(tmp_path, monkeypatch):
    from piwebkit import hub as hubmod
    clock = [time.time()]
    monkeypatch.setattr(hubmod.time, "time", lambda: clock[0])
    monkeypatch.setattr(hubmod, "MAX_FILE", 2000)
    path, start = tmp_path / "events.jsonl", clock[0]
    hub = Hub()
    hub.load(path)
    hub.publish({"type": "message", "t": start, "text": "kept"})
    for i in range(30):
        hub.publish({"type": "packet", "t": start, "text": f"packet number {i}"})
    hub.flush()
    assert "packet number 29" in path.read_text()  # all still inside the month
    clock[0] = start + hubmod.MONTH + 1  # and now every packet has aged out
    for i in range(100):
        hub.publish({"type": "packet", "t": clock[0], "text": f"later {i}"})
    hub.flush()
    texts = [json.loads(line)["text"] for line in path.read_text().splitlines()]
    assert texts == ["kept"] + [f"later {i}" for i in range(100)] and path.stat().st_mode & 0o777 == 0o600


def test_live_events_after_sync(make_server):
    import threading
    hub, srv, _ = make_server()
    threading.Timer(0.5, hub.publish, [{"t": time.time(), "raw": "cc"}]).start()
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

from piwebkit.server import host_ok


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
    now = time.time()  # the page is replayed the last month of traffic, so a packet carries its time
    web.hub.publish({"type": "packet", "t": now, "text": "kept"})
    web.hub.publish({"type": "station", "call": "W1AW"}, keep=False)  # live only, the intro carries current state
    r = urllib.request.urlopen(url(srv, "/web/events"), timeout=5)
    got = [json.loads(line[6:]) for _, line in zip(range(3), (l for l in r if l.startswith(b"data: ")))]
    assert got == [{"type": "me", "call": "N0CALL"}, {"type": "packet", "t": now, "text": "kept"}, {"sync": True}]


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
