"""simplemesh: the node wrapper against a pretend MeshCore, the store, and the page's server over real HTTP."""
import asyncio
import http.client
import json
import time

import pytest

pytest.importorskip("meshcore")

from meshcore import EventType  # noqa: E402

from simplemesh.config import Config  # noqa: E402
from simplemesh.demo import FakeMesh, contact, key_for  # noqa: E402
from simplemesh.node import MeshError, MeshNode, battery_percent  # noqa: E402
from simplemesh.store import MAX_MESSAGES, Store  # noqa: E402
from simplemesh.webui import MeshWeb, WEB  # noqa: E402
from piwebkit.auth import Auth  # noqa: E402
from piwebkit.hub import Hub  # noqa: E402
from piwebkit.maps import WEB as SHARED_WEB  # noqa: E402
from piwebkit.maps import MapProxy  # noqa: E402
from piwebkit.server import WebServer  # noqa: E402

ANNA = contact("Anna", 1, 30.01, -97.01, hops=1, path="ab")
HILL = contact("Hilltop", 2, 30.05, -97.05, hops=0)
ROOM = contact("Ranger Room", 3)


def until(fn, what="condition", t=5.0):
    end = time.time() + t
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


class Rig:
    def __init__(self, tmp_path, fake=None, ports=lambda: ["fake"], connector=None, **kw):
        self.fake = fake or FakeMesh("Pi Node", 30.0, -97.0)
        for c in (ANNA, HILL, ROOM):
            self.fake.contacts[c["public_key"]] = dict(c)
        self.fake.channels.update({1: "#hiking"})
        self.hub, self.cfg = Hub(), Config()
        self.store = Store(tmp_path / "nodes.json", tmp_path / "messages.json")

        async def default(port, baud):
            return self.fake
        self.node = MeshNode(self.cfg, self.hub, self.store, connector=connector or default, ports=ports, retry_s=0.05, **kw)
        self.q, _ = self.hub.subscribe()

    def start(self):
        self.node.start()
        return self

    def connect(self):
        self.start()
        until(lambda: self.node._status["connected"], "the node to connect")
        return self

    def emit(self, et, payload=None, attributes=None):
        asyncio.run_coroutine_threadsafe(self.fake.emit(et, payload, attributes), self.node.loop).result(5)

    def events(self):
        out = []
        while not self.q.empty():
            out.append(self.q.get_nowait())
        return out

    def intro(self, key):
        return next(e[key] for e in self.node.intro() if key in e)


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path).connect()
    yield r
    r.node.stop()


def test_connecting_loads_what_the_page_needs(rig):
    st = rig.intro("status")
    assert st["connected"] and "fake" in st["text"] and st["battery"] == battery_percent(3900)
    me = rig.intro("self")
    assert (me["name"], me["freq"], me["sf"], me["model"]) == ("Pi Node", 910.525, 7, "Heltec V3")
    assert [c["name"] for c in rig.intro("channels")] == ["Public", "#hiking"]
    nodes = {n["name"]: n for n in rig.intro("nodes")}
    assert set(nodes) == {"Anna", "Hilltop", "Ranger Room"}
    assert nodes["Anna"]["saved"] and nodes["Anna"]["hops"] == 1 and nodes["Anna"]["lat"] == 30.01
    assert nodes["Ranger Room"]["lat"] is None  # 0, 0 means no position
    assert ("set_time",) in rig.fake.commands.sent  # the node has no clock of its own


def test_a_direct_message_arrives_once(rig):
    p = {"pubkey_prefix": ANNA["public_key"][:12], "text": "hello", "txt_type": 0, "sender_timestamp": int(time.time()),
         "path_len": 255, "SNR": 5.5}
    rig.emit(EventType.CONTACT_MSG_RECV, p)
    rig.emit(EventType.CONTACT_MSG_RECV, p)  # the same packet again (a retry)
    m, = rig.intro("msgs")
    assert (m["kind"], m["peer"], m["from"], m["text"], m["hops"], m["snr"], m["dir"]) == \
        ("dm", ANNA["public_key"][:12], "Anna", "hello", 0, 5.5, "in")
    assert abs(m["ts"] - time.time()) < 5
    rig.emit(EventType.CONTACT_MSG_RECV, {**p, "txt_type": 1, "text": "cli reply", "sender_timestamp": 1})  # a command reply
    assert len(rig.intro("msgs")) == 1


def test_a_message_the_node_held_keeps_the_time_it_was_sent(rig):
    sent = int(time.time()) - 3600
    rig.emit(EventType.CONTACT_MSG_RECV, {"pubkey_prefix": ANNA["public_key"][:12], "text": "late", "txt_type": 0,
                                          "sender_timestamp": sent, "path_len": 1})
    assert rig.intro("msgs")[0]["ts"] == sent
    rig.emit(EventType.CONTACT_MSG_RECV, {"pubkey_prefix": ANNA["public_key"][:12], "text": "clock wrong", "txt_type": 0,
                                          "sender_timestamp": 5, "path_len": 1})
    assert abs(rig.intro("msgs")[1]["ts"] - time.time()) < 5


def test_a_channel_message_names_its_sender(rig):
    rig.emit(EventType.CHANNEL_MSG_RECV, {"channel_idx": 1, "text": "Marcus: at the tower: all good", "path_len": 2,
                                          "txt_type": 0, "sender_timestamp": int(time.time())})
    m, = rig.intro("msgs")
    assert (m["kind"], m["chan"], m["from"], m["text"], m["hops"]) == ("chan", 1, "Marcus", "at the tower: all good", 2)
    rig.emit(EventType.CHANNEL_MSG_RECV, {"channel_idx": 0, "text": "no sender here", "path_len": 255, "txt_type": 0,
                                          "sender_timestamp": int(time.time())})
    assert rig.intro("msgs")[1]["from"] == "" and rig.intro("msgs")[1]["hops"] == 0


def test_a_message_from_a_stranger_shows_their_key(rig):
    rig.emit(EventType.CONTACT_MSG_RECV, {"pubkey_prefix": "0123456789ab", "text": "hi", "txt_type": 0,
                                          "sender_timestamp": int(time.time()), "path_len": 1})
    assert rig.intro("msgs")[0]["from"] == "0123456789ab"


def advert(name, key, **extra):
    return {"snr": 4.0, "rssi": -90, "payload_typename": "ADVERT", "route_typename": "FLOOD", "path_len": 1, "path": "ab",
            "path_hash_size": 1, "payload_length": 101, "recv_time": int(time.time()), "adv_key": key, "adv_name": name,
            "adv_type": 2, "adv_timestamp": 1_800_000_000, **extra}


def test_an_advert_from_an_unknown_node_is_listed_as_heard(rig):
    key = key_for("Far Repeater")
    rig.emit(EventType.RX_LOG_DATA, advert("Far Repeater", key, adv_lat=30.5, adv_lon=-97.5))
    n = next(n for n in rig.intro("nodes") if n["key"] == key)
    assert (n["name"], n["type"], n["lat"], n["snr"]) == ("Far Repeater", 2, 30.5, 4.0) and not n.get("saved")
    rx, = rig.intro("rxs")
    assert rx["kind"] == "ADVERT" and rx["name"] == "Far Repeater" and rx["hops"] == 1
    rig.emit(EventType.RX_LOG_DATA, advert("", key_for("Nameless")))  # nothing worth listing
    assert not any(n["key"] == key_for("Nameless") for n in rig.intro("nodes"))
    rig.emit(EventType.RX_LOG_DATA, {"snr": 1.0, "rssi": -100, "payload_typename": "GRP_TXT", "route_typename": "FLOOD",
                                     "path_len": 0, "path": "", "path_hash_size": 1, "payload_length": 30, "chan_name": "Public"})
    assert rig.intro("rxs")[-1]["chan"] == "Public"


def test_a_known_nodes_advert_updates_when_we_heard_it(rig):
    before = next(n for n in rig.intro("nodes") if n["name"] == "Anna")
    assert not before.get("heard")
    rig.emit(EventType.ADVERTISEMENT, {"public_key": ANNA["public_key"]})
    after = next(n for n in rig.intro("nodes") if n["name"] == "Anna")
    assert abs(after["heard"] - time.time()) < 5 and after["saved"]


def test_a_new_contact_waits_to_be_added(rig):
    c = contact("Newcomer", 1, 30.2, -97.2)
    rig.fake.pending_contacts[c["public_key"]] = c
    rig.emit(EventType.NEW_CONTACT, c)
    n = next(n for n in rig.intro("nodes") if n["name"] == "Newcomer")
    assert n["pending"] and not n.get("saved")
    rig.node.add_node(c["public_key"])
    assert ("add", c["public_key"]) in rig.fake.commands.sent
    n = next(n for n in rig.intro("nodes") if n["name"] == "Newcomer")
    assert n["saved"] and not n["pending"]


def test_sending_a_direct_message(rig):
    msg = rig.node.send_dm(ANNA["public_key"][:12], "on my way")
    assert msg["dir"] == "out" and msg["peer"] == ANNA["public_key"][:12]
    until(lambda: rig.intro("msgs")[0]["status"] == "acked", "the ack")
    assert ("dm", ANNA["public_key"], "on my way") in rig.fake.commands.sent
    rig.fake.commands.ack = False
    rig.node.send_dm(ANNA["public_key"][:12], "anyone?")
    until(lambda: rig.intro("msgs")[1]["status"] == "failed", "the failure")


def test_direct_messages_that_cant_be_sent(rig):
    for to, text, why in ((HILL["public_key"][:12], "hi", "companion"), ("f" * 12, "hi", "isn't saved"),
                          (ANNA["public_key"][:12], "   ", "Type a message"), (ANNA["public_key"][:12], "x" * 200, "too long")):
        with pytest.raises(MeshError, match=why):
            rig.node.send_dm(to, text)
    assert rig.intro("msgs") == []


def test_sending_to_a_channel(rig):
    msg = rig.node.send_channel(1, "meet at 5")
    assert msg["status"] == "sent" and msg["from"] == "Pi Node"
    assert ("chan", 1, "meet at 5") in rig.fake.commands.sent and rig.intro("msgs")[0]["text"] == "meet at 5"
    with pytest.raises(MeshError, match="No such channel"):
        rig.node.send_channel(7, "hi")
    with pytest.raises(MeshError, match="too long"):  # the channel message carries our name too
        rig.node.send_channel(1, "x" * 145)
    rig.fake.commands.refuse.add("chan")
    with pytest.raises(MeshError, match="refused"):
        rig.node.send_channel(1, "again")
    assert len(rig.intro("msgs")) == 1  # a refused message is not shown as sent


def test_advertising_and_profile(rig):
    rig.node.advertise(False)
    rig.node.advertise(True)
    assert [s for s in rig.fake.commands.sent if s[0] == "advert"] == [("advert", False), ("advert", True)]
    rig.node.set_profile("Pi Base", 30.25, -97.5)
    me = rig.intro("self")
    assert (me["name"], me["lat"], me["lon"]) == ("Pi Base", 30.25, -97.5)
    for args in (("", 0, 0), ("x" * 40, 0, 0), ("ok", 95, 0), ("ok", 0, 200)):
        with pytest.raises(MeshError):
            rig.node.set_profile(*args)


def test_removing_a_node_and_forgetting_its_route(rig):
    rig.node.reset_path(ANNA["public_key"])
    until(lambda: next(n for n in rig.intro("nodes") if n["name"] == "Anna")["hops"] == -1, "the route to be forgotten")
    rig.node.remove_node(ANNA["public_key"])
    assert not any(n["name"] == "Anna" for n in rig.intro("nodes"))
    assert ANNA["public_key"] not in rig.fake.contacts
    assert {"gone": [ANNA["public_key"]]} in rig.events()


def test_a_full_contact_list_is_reported(rig):
    rig.events()
    rig.emit(EventType.CONTACTS_FULL)
    assert any("full" in e.get("notice", "") for e in rig.events())


def test_battery_percentage():
    assert battery_percent(4200) == 100 and battery_percent(3300) == 0 and battery_percent(3750) == 50
    assert battery_percent(5000) == 100 and battery_percent(100) == 0


def test_nothing_plugged_in_says_so(tmp_path):
    r = Rig(tmp_path, ports=lambda: []).start()
    try:
        until(lambda: "No USB serial device" in r.node._status["text"], "the status")
        with pytest.raises(MeshError, match="isn't connected"):
            r.node.advertise(False)
    finally:
        r.node.stop()


def test_a_device_that_does_not_answer_says_why(tmp_path):
    async def silent(port, baud):
        return None
    r = Rig(tmp_path, connector=silent).start()
    try:
        until(lambda: "did not answer" in r.node._status["text"] and "companion" in r.node._status["text"], "the status")
    finally:
        r.node.stop()


def test_a_port_that_wont_open_says_why(tmp_path):
    async def broken(port, baud):
        raise OSError("Permission denied")
    r = Rig(tmp_path, connector=broken).start()
    try:
        until(lambda: "Permission denied" in r.node._status["text"], "the status")
    finally:
        r.node.stop()


def test_the_chosen_port_is_the_only_one_tried(tmp_path):
    tried = []
    fake = FakeMesh()

    async def connector(port, baud):
        tried.append((port, baud))
        return fake if port == "/dev/ttyACM1" else None
    r = Rig(tmp_path, fake=fake, connector=connector, ports=lambda: ["/dev/ttyACM0", "/dev/ttyACM1"])
    r.cfg.serial_port, r.cfg.baud = "/dev/ttyACM1", 9600
    try:
        r.connect()
        assert tried == [("/dev/ttyACM1", 9600)]
    finally:
        r.node.stop()


def test_the_next_device_is_tried_when_one_is_not_a_node(tmp_path):
    tried = []
    fake = FakeMesh()

    async def connector(port, baud):
        tried.append(port)
        return fake if port == "/dev/ttyACM1" else None
    r = Rig(tmp_path, fake=fake, connector=connector, ports=lambda: ["/dev/ttyACM0", "/dev/ttyACM1"])
    try:
        r.connect()
        assert tried == ["/dev/ttyACM0", "/dev/ttyACM1"] and "ttyACM1" in r.node._status["text"]
    finally:
        r.node.stop()


def test_it_reconnects_after_the_node_goes_away(tmp_path):
    fakes = []

    async def connector(port, baud):
        fakes.append(FakeMesh())
        return fakes[-1]
    r = Rig(tmp_path, connector=connector).connect()
    try:
        asyncio.run_coroutine_threadsafe(fakes[0].emit(EventType.DISCONNECTED, {"reason": "serial_disconnect"}), r.node.loop).result(5)
        until(lambda: len(fakes) == 2 and r.node._status["connected"], "the second connection")
        assert fakes[0].disconnected
    finally:
        r.node.stop()


def test_stopping_does_not_wait_for_the_retry_delay(tmp_path):
    r = Rig(tmp_path, ports=lambda: []).start()
    until(lambda: "No USB" in r.node._status["text"])
    r.node.retry_s = 30
    t = time.time()
    r.node.stop()
    assert time.time() - t < 3


# --- the store ---------------------------------------------------------------------------------------

def test_the_store_survives_a_restart(tmp_path):
    s = Store(tmp_path / "n.json", tmp_path / "m.json")
    s.put_node("aa" * 32, {"name": "A", "saved": True, "heard": 5})
    s.add_message({"id": "1", "text": "hi"})
    s.save()
    s2 = Store(tmp_path / "n.json", tmp_path / "m.json")
    assert s2.node("aa" * 32)["name"] == "A" and s2.recent_messages()[0]["text"] == "hi"
    assert not s2.add_message({"id": "1", "text": "again"})  # still known as seen


def test_the_store_ignores_damaged_files(tmp_path):
    (tmp_path / "n.json").write_text("{not json")
    (tmp_path / "m.json").write_text('"a string"')
    s = Store(tmp_path / "n.json", tmp_path / "m.json")
    assert s.all_nodes() == [] and s.recent_messages() == []


def test_the_store_keeps_a_bounded_history(tmp_path):
    s = Store(tmp_path / "n.json", tmp_path / "m.json")
    for i in range(MAX_MESSAGES + 10):
        s.add_message({"id": str(i)})
    assert len(s.messages) == MAX_MESSAGES and s.messages[0]["id"] == "10"
    assert s.add_message({"id": "0"})  # forgotten ids can come back


def test_a_key_prefix_must_pick_one_node(tmp_path):
    s = Store(tmp_path / "n.json", tmp_path / "m.json")
    s.put_node("ab01" + "0" * 60, {"name": "one"})
    s.put_node("ab02" + "0" * 60, {"name": "two"})
    assert s.find("ab01")["name"] == "one" and s.find("ab") is None and s.find("cd") is None


# --- the page, over HTTP --------------------------------------------------------------------------------

PW = "correct horse battery"


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr("simplemesh.config.PATH", tmp_path / "config.json")
    r = Rig(tmp_path).connect()
    proxy = MapProxy("", None, web=(WEB, SHARED_WEB))
    ui = MeshWeb(r.hub, r.node, proxy, Auth(tmp_path / "auth.json"), r.cfg)
    srv = WebServer(0, ui)
    r.srv = srv  # type: ignore[attr-defined]
    yield r
    srv.stop()
    r.node.stop()


def call(r, method, path, body=None, cookie=""):
    c = http.client.HTTPConnection("127.0.0.1", r.srv.port, timeout=5)
    c.request(method, path, json.dumps(body) if body is not None else None,
              {"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})})
    resp = c.getresponse()
    raw = resp.read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = raw
    out = (resp.status, data, resp.getheaders())
    c.close()
    return out


def login(r):
    st, data, hdr = call(r, "POST", "/api/setup", {"username": "alice", "password": PW})
    assert st == 200, data
    return next(v for k, v in hdr if k == "Set-Cookie").split(";")[0]


def test_the_page_needs_a_login(web):
    st, _, hdr = call(web, "GET", "/")
    assert st == 302 and ("Location", "/login") in hdr
    for path in ("/api/settings", "/style.json", "/web/events", "/mesh.js", "/api/account"):
        assert call(web, "GET", path)[0] == 401, path
    for path in ("/api/send", "/api/advert", "/api/profile", "/api/node/add", "/api/settings"):
        assert call(web, "POST", path, {})[0] == 401, path
    assert web.fake.commands.sent.count(("advert", False)) == 0
    st, page, _ = call(web, "GET", "/login")
    assert st == 200 and b"simplemesh" in page and b"simpleaprs" not in page


def test_the_session_cookie_does_not_collide_with_simpleaprs(web):
    cookie = login(web)
    assert cookie.startswith("simplemesh_session=")


def test_the_page_and_its_files_are_served_after_login(web):
    cookie = login(web)
    for path in ("/", "/mesh.js", "/meshmap.js", "/maplibre-gl.js", "/pmtiles.js", "/maplibre-gl.css"):
        st, body, _ = call(web, "GET", path, cookie=cookie)
        assert st == 200 and body, path
    assert b"simplemesh" in call(web, "GET", "/", cookie=cookie)[1]
    assert call(web, "GET", "/../config.py", cookie=cookie)[0] == 404


def test_sending_over_http(web):
    cookie = login(web)
    st, data, _ = call(web, "POST", "/api/send", {"kind": "chan", "chan": 0, "text": "hello mesh"}, cookie)
    assert st == 200 and data["msg"]["text"] == "hello mesh" and ("chan", 0, "hello mesh") in web.fake.commands.sent
    st, data, _ = call(web, "POST", "/api/send", {"kind": "dm", "to": ANNA["public_key"][:12], "text": "hi anna"}, cookie)
    assert st == 200
    until(lambda: ("dm", ANNA["public_key"], "hi anna") in web.fake.commands.sent, "the message to be sent")
    st, data, _ = call(web, "POST", "/api/send", {"kind": "dm", "to": HILL["public_key"][:12], "text": "hi"}, cookie)
    assert st == 409 and "companion" in data["error"]


def test_bad_requests_are_refused(web):
    cookie = login(web)
    for body in ({}, {"kind": "carrier-pigeon"}, {"kind": "chan"}, {"kind": "chan", "chan": "x", "text": "a"},
                 {"kind": "dm", "to": "xyz", "text": "a"}, {"kind": "dm", "to": "../../etc", "text": "a"}):
        assert call(web, "POST", "/api/send", body, cookie)[0] == 400, body
    for path in ("/api/node/add", "/api/node/remove", "/api/node/reset-path"):
        assert call(web, "POST", path, {"key": "short"}, cookie)[0] == 400
        assert call(web, "POST", path, {"key": "g" * 64}, cookie)[0] == 400
    assert call(web, "POST", "/api/profile", {"name": "a"}, cookie)[0] == 400
    assert call(web, "POST", "/api/forget-inactive", {}, cookie)[0] == 404  # simpleaprs's, not ours


def test_the_node_can_be_asked_to_advertise_over_http(web):
    cookie = login(web)
    assert call(web, "POST", "/api/advert", {"flood": True}, cookie)[0] == 200
    assert ("advert", True) in web.fake.commands.sent


def test_a_refusal_from_the_node_comes_back_as_text(web):
    cookie = login(web)
    web.fake.commands.refuse.add("set_name")
    st, data, _ = call(web, "POST", "/api/profile", {"name": "X", "lat": 1, "lon": 2}, cookie)
    assert st == 409 and "refused" in data["error"]


def test_settings_validate_and_apply(web):
    cookie = login(web)
    st, view, _ = call(web, "GET", "/api/settings", cookie=cookie)
    assert st == 200 and view["values"]["baud"] == 115200 and 115200 in view["bauds"]
    ok = {"serial_port": "/dev/ttyACM0", "baud": 57600, "nomad_url": "http://pi.local:8080"}
    for bad in ({**ok, "serial_port": "ttyACM0"}, {**ok, "baud": 1234}, {**ok, "nomad_url": "ftp://x"}, {**ok, "nomad_url": "javascript:1"}):
        assert call(web, "POST", "/api/settings", bad, cookie)[0] == 409, bad
    assert call(web, "POST", "/api/settings", ok, cookie)[0] == 200
    assert (web.cfg.serial_port, web.cfg.baud, web.cfg.nomad_url) == ("/dev/ttyACM0", 57600, "http://pi.local:8080")
    assert Config.load().baud == 57600  # saved
    until(lambda: web.fake.disconnected, "the node to be dropped so it reconnects on the new port")


def test_the_event_stream_starts_with_everything(web):
    cookie = login(web)
    c = http.client.HTTPConnection("127.0.0.1", web.srv.port, timeout=5)
    c.request("GET", "/web/events", headers={"Cookie": cookie})
    r = c.getresponse()
    assert r.status == 200 and r.getheader("Content-Type") == "text/event-stream"
    seen = []
    while True:
        line = r.fp.readline().decode()
        if line.startswith("data: "):
            seen.append(json.loads(line[6:]))
            if seen[-1].get("sync"):
                break
    c.close()
    kinds = [next(iter(e)) for e in seen]
    assert kinds[:6] == ["status", "self", "channels", "nodes", "msgs", "rxs"] and kinds[-1] == "sync"
