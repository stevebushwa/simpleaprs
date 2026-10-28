"""The headless server over real HTTP: first-run setup, login, cookies, the desktop token, and settings."""
import http.client
import json
import time

import pytest

from simpleaprs import headless
from piwebkit.auth import Auth
from simpleaprs.config import Config

PW = "correct horse battery"


class FakeRadio:
    def __init__(self):
        self.sent, self.starts = [], []
        self.transmitting = False

    def start(self, inp, out, auto_gain=False):
        self.starts.append((inp, out))
        return True

    def stop(self):
        pass

    def transmit(self, raw, lead, level):
        self.sent.append(raw)


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setattr("simpleaprs.config.PATH", tmp_path / "config.json")
    monkeypatch.setattr(headless, "STATIONS_PATH", tmp_path / "stations.json")
    monkeypatch.setattr(headless, "WEB_EVENTS_PATH", tmp_path / "web-events.jsonl")
    monkeypatch.setattr(headless, "FRAMES_PATH", tmp_path / "frames.jsonl")
    monkeypatch.setattr(headless, "device_names", lambda inputs: ["USB Audio", "Built-in"] if inputs else ["USB Audio"])
    monkeypatch.setattr(headless, "find_device", lambda name, inputs: name)
    s = headless.Server(Config(callsign="N0CALL-7", lat=30.0, lon=-97.0, share_port=0), quiet=True, auth=Auth(tmp_path / "auth.json"))
    s.radio = FakeRadio()
    assert s.start()
    yield s
    s.stop()


def call(s, method, path, body=None, cookie="", headers=None):
    c = http.client.HTTPConnection("127.0.0.1", s.share.port, timeout=5)
    h = {"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {}), **(headers or {})}
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    raw = r.read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = raw
    out = (r.status, data, r.getheaders())
    c.close()
    return out


def cookie_of(headers):
    return next(v for k, v in headers if k == "Set-Cookie").split(";")[0]


def traffic(s, path, cookie=""):
    """The traffic a stream replays to a new client, oldest first, up to its sync marker."""
    c = http.client.HTTPConnection("127.0.0.1", s.share.port, timeout=5)
    c.request("GET", path, headers={"Cookie": cookie} if cookie else {})
    out = []
    for line in c.getresponse():
        if not line.startswith(b"data: "):
            continue
        ev = json.loads(line[6:])
        if ev.get("sync"):
            break
        if "t" in ev:  # the rest of a stream (who this station is, the RX level) is state, not traffic
            out.append(ev.get("raw") or ev.get("text"))
    c.close()
    return out


def setup(s, **extra):
    st, data, hdr = call(s, "POST", "/api/setup", {"username": "alice", "password": PW, **extra})
    assert st == 200, data
    return cookie_of(hdr), hdr


def test_nothing_works_without_a_session(srv):
    st, _, hdr = call(srv, "GET", "/")
    assert st == 302 and ("Location", "/login") in hdr
    assert call(srv, "GET", "/index.html")[0] == 302
    for path in ("/api/settings", "/api/account", "/style.json", "/web/events", "/map.js", "/api/passcode?call=X"):
        assert call(srv, "GET", path)[0] == 401, path
    assert call(srv, "POST", "/api/settings", {"changes": {}})[0] == 401
    assert call(srv, "POST", "/api/tx", {"type": "beacon"})[0] == 401
    assert srv.radio.sent == []


def test_login_page_and_state_are_public(srv):
    st, page, _ = call(srv, "GET", "/login")
    assert st == 200 and b"Remember me" in page
    assert call(srv, "GET", "/api/auth/state")[1] == {"setup": True, "logged_in": False}


def test_first_visitor_creates_the_login_and_is_logged_in(srv):
    cookie, hdr = setup(srv)
    attrs = next(v for k, v in hdr if k == "Set-Cookie")
    assert "HttpOnly" in attrs and "SameSite=Strict" in attrs and "Max-Age" not in attrs  # browser-session cookie
    assert call(srv, "GET", "/api/auth/state", cookie=cookie)[1] == {"setup": False, "logged_in": True}
    assert call(srv, "GET", "/api/settings", cookie=cookie)[0] == 200
    assert call(srv, "GET", "/", cookie=cookie)[0] == 200
    st, data, _ = call(srv, "POST", "/api/setup", {"username": "mallory", "password": "another password"})
    assert st == 409  # it can be done once


def test_setup_checks_the_password(srv):
    assert call(srv, "POST", "/api/setup", {"username": "alice", "password": "short"})[0] == 400
    assert call(srv, "GET", "/api/auth/state")[1]["setup"] is True


def test_login_logout_and_wrong_password(srv):
    setup(srv)
    assert call(srv, "POST", "/api/login", {"username": "alice", "password": "wrong"})[0] == 401
    st, _, hdr = call(srv, "POST", "/api/login", {"username": "alice", "password": PW})
    cookie = cookie_of(hdr)
    assert st == 200 and call(srv, "GET", "/api/settings", cookie=cookie)[0] == 200
    st, _, hdr = call(srv, "POST", "/api/logout", {}, cookie=cookie)
    assert st == 200 and "Max-Age=0" in next(v for k, v in hdr if k == "Set-Cookie")
    assert call(srv, "GET", "/api/settings", cookie=cookie)[0] == 401  # the old cookie is dead server-side too


def test_remember_me_makes_a_long_lived_cookie(srv):
    _, hdr = setup(srv)
    st, _, hdr = call(srv, "POST", "/api/login", {"username": "alice", "password": PW, "remember": True})
    assert f"Max-Age={30 * 86400}" in next(v for k, v in hdr if k == "Set-Cookie")
    cookie = cookie_of(hdr)
    # ...and it is still good after the server restarts
    again = Auth(srv.auth.path)
    assert again.valid(cookie.split("=", 1)[1])


def test_repeated_wrong_passwords_lock_the_address_out(srv):
    setup(srv)
    for _ in range(5):
        call(srv, "POST", "/api/login", {"username": "alice", "password": "wrong"})
    st, data, _ = call(srv, "POST", "/api/login", {"username": "alice", "password": PW})
    assert st == 429 and "Try again" in data["error"]


def test_desktop_apps_use_the_token_not_the_login(srv):
    cookie, _ = setup(srv)
    token = srv.auth.api_token
    assert call(srv, "GET", "/api/events")[0] == 401
    assert call(srv, "GET", "/api/events", headers={"Authorization": "Bearer nope"})[0] == 401
    assert call(srv, "POST", "/api/tx", {"type": "beacon"}, headers={"Authorization": "Bearer nope"})[0] == 401
    assert call(srv, "POST", "/api/tx", {"type": "beacon"}, headers={"Authorization": f"Bearer {token}"})[0] == 200
    assert len(srv.radio.sent) == 1
    assert call(srv, "POST", "/api/tx", {"type": "beacon"}, cookie=cookie)[0] == 200  # the logged-in page can too
    assert len(srv.radio.sent) == 2


def test_token_is_not_usable_before_the_account_exists(srv):
    assert srv.share.token == ""
    assert call(srv, "POST", "/api/tx", {"type": "beacon"}, headers={"Authorization": "Bearer "})[0] == 401
    setup(srv)
    assert srv.share.token == srv.auth.api_token != ""


def test_regenerating_the_token_cuts_off_the_old_one(srv):
    cookie, _ = setup(srv)
    old = srv.auth.api_token
    new = call(srv, "POST", "/api/account/token", {}, cookie=cookie)[1]["api_token"]
    assert new != old and srv.share.token == new
    assert call(srv, "POST", "/api/tx", {"type": "beacon"}, headers={"Authorization": f"Bearer {old}"})[0] == 401
    assert call(srv, "POST", "/api/tx", {"type": "beacon"}, headers={"Authorization": f"Bearer {new}"})[0] == 200


def test_other_sites_cannot_use_a_logged_in_browser(srv):
    cookie, _ = setup(srv)
    assert call(srv, "GET", "/api/settings", cookie=cookie, headers={"Host": "evil.example.com"})[0] == 403
    assert call(srv, "POST", "/api/tx", {"type": "beacon"}, cookie=cookie, headers={"Origin": "http://evil.example.com"})[0] == 403
    assert call(srv, "POST", "/api/settings", {"changes": {"callsign": "K5EVIL"}}, cookie=cookie,
                headers={"Origin": "http://evil.example.com"})[0] == 403
    assert srv.cfg.callsign == "N0CALL-7"


def test_change_password_logs_out_other_devices(srv):
    mine, _ = setup(srv)
    other = cookie_of(call(srv, "POST", "/api/login", {"username": "alice", "password": PW, "remember": True})[2])
    assert call(srv, "POST", "/api/account/password", {"current": "wrong", "new": "a brand new password"}, cookie=mine)[0] == 400
    assert call(srv, "POST", "/api/account/password", {"current": PW, "new": "a brand new password"}, cookie=mine)[0] == 200
    assert call(srv, "GET", "/api/account", cookie=mine)[0] == 200
    assert call(srv, "GET", "/api/account", cookie=other)[0] == 401
    assert call(srv, "POST", "/api/login", {"username": "alice", "password": "a brand new password"})[0] == 200


def test_logout_everywhere(srv):
    a, _ = setup(srv)
    b = cookie_of(call(srv, "POST", "/api/login", {"username": "alice", "password": PW})[2])
    assert call(srv, "POST", "/api/account/logout-all", {}, cookie=a)[0] == 200
    assert call(srv, "GET", "/api/account", cookie=a)[0] == 401 and call(srv, "GET", "/api/account", cookie=b)[0] == 401


def test_the_event_stream_needs_a_session_and_ends_at_logout(srv):
    cookie, _ = setup(srv)
    c = http.client.HTTPConnection("127.0.0.1", srv.share.port, timeout=5)
    c.request("GET", "/web/events", headers={"Cookie": cookie})
    r = c.getresponse()
    assert r.status == 200
    first = json.loads(r.readline()[6:])
    assert first["type"] == "me" and first["call"] == "N0CALL-7"
    call(srv, "POST", "/api/logout", {}, cookie=cookie)
    r.read()  # the server hangs up within a second of the logout
    c.close()


def test_a_page_and_a_client_see_the_last_month_of_traffic(srv):
    """A month of traffic is as far back as a page, or a desktop app following this radio, can look.
    A message is not traffic to be aged out this way: it is kept as long as it fits in the backlog."""
    cookie, _ = setup(srv)
    now, last_month = time.time(), time.time() - 31 * 86400  # a month and a day of traffic
    for kind, t in (("packet", last_month), ("message", last_month), ("packet", now), ("message", now)):
        srv.web_hub.publish({"type": kind, "t": t, "text": f"{kind} ({'last month' if t == last_month else 'now'})"})
    srv.hub.publish({"t": last_month, "raw": "00"})  # the same traffic, as a desktop app follows it
    srv.hub.publish({"t": now, "raw": "01"})
    assert traffic(srv, "/web/events", cookie) == ["message (last month)", "packet (now)", "message (now)"]
    assert traffic(srv, "/api/events", cookie) == ["01"]


def test_a_page_is_sent_the_last_day_and_asks_for_older_packets(srv):
    cookie, _ = setup(srv)
    now = time.time()
    for days in (20, 3, 0.2):
        srv.web_hub.publish({"type": "packet", "t": now - days * 86400, "text": f"{days} days ago"})
    srv.web_hub.publish({"type": "message", "t": now - 25 * 86400, "text": "a message"})
    assert traffic(srv, "/web/events", cookie) == ["0.2 days ago", "a message"]  # a message is not held to the window the packets are
    st, data, _ = call(srv, "GET", f"/web/history?before={now - 86400}&limit=1", cookie=cookie)
    assert st == 200 and [e["text"] for e in data["events"]] == ["3 days ago"] and data["more"] is True
    st, data, _ = call(srv, "GET", f"/web/history?before={now - 4 * 86400}&limit=5", cookie=cookie)
    assert [e["text"] for e in data["events"]] == ["20 days ago"] and data["more"] is False
    assert call(srv, "GET", "/web/history?before=nope", cookie=cookie)[0] == 400
    assert call(srv, "GET", f"/web/history?before={now}")[0] == 401  # and it needs the login like the rest


# --- settings -----------------------------------------------------------------------------------------

def test_settings_view_lists_fields_values_and_devices(srv):
    cookie, _ = setup(srv)
    view = call(srv, "GET", "/api/settings", cookie=cookie)[1]
    keys = {f["key"] for f in view["fields"]}
    assert {"callsign", "input_device", "igate_enabled", "digi_enabled", "nomad_url", "share_allow_tx"} <= keys
    assert view["values"]["callsign"] == "N0CALL-7" and view["status"]["radio_ok"] is True
    dev = next(f for f in view["fields"] if f["key"] == "input_device")
    assert ["USB Audio", "USB Audio"] in dev["options"] and ["", "(system default)"] in dev["options"]


def test_saving_settings_applies_and_persists(srv, tmp_path):
    cookie, _ = setup(srv)
    st, data, _ = call(srv, "POST", "/api/settings", {"changes": {"callsign": "w1aw-9", "comment": "hello", "beacon_minutes": 15}}, cookie=cookie)
    assert st == 200, data
    assert (srv.cfg.callsign, srv.cfg.comment, srv.cfg.beacon_minutes) == ("W1AW-9", "hello", 15)
    assert json.loads((tmp_path / "config.json").read_text())["callsign"] == "W1AW-9"
    assert srv.next_beacon > 0  # the new interval is scheduled


def test_bad_settings_change_nothing(srv):
    cookie, _ = setup(srv)
    st, data, _ = call(srv, "POST", "/api/settings", {"changes": {"callsign": "W1AW", "lat": 500, "input_device": "Nope"}}, cookie=cookie)
    assert st == 400 and set(data["errors"]) == {"lat", "input_device"}
    assert srv.cfg.callsign == "N0CALL-7"


def test_changing_the_sound_card_reopens_the_radio(srv):
    cookie, _ = setup(srv)
    srv.radio.starts.clear()
    assert call(srv, "POST", "/api/settings", {"changes": {"input_device": "USB Audio", "output_device": "USB Audio"}}, cookie=cookie)[0] == 200
    assert srv.radio.starts == [("USB Audio", "USB Audio")]
    srv.radio.starts.clear()
    call(srv, "POST", "/api/settings", {"changes": {"comment": "x"}}, cookie=cookie)
    assert srv.radio.starts == []  # unrelated changes leave the radio alone
    call(srv, "POST", "/api/settings", {"changes": {"auto_gain": True}}, cookie=cookie)
    assert len(srv.radio.starts) == 1 and srv.cfg.auto_gain


def test_a_radio_that_would_not_open_is_reported_and_retried(srv):
    cookie, _ = setup(srv)
    srv.radio.start = lambda i, o, g=False: False
    call(srv, "POST", "/api/settings", {"changes": {"input_device": "USB Audio"}}, cookie=cookie)
    assert srv.status()["radio_ok"] is False and "audio input" in srv.status()["radio"]
    srv.radio.start = lambda i, o, g=False: True  # a card got plugged in
    call(srv, "POST", "/api/settings", {"changes": {"comment": "again"}}, cookie=cookie)
    assert srv.status()["radio_ok"] is True


def test_gateways_start_and_stop_from_settings(srv):
    cookie, _ = setup(srv)
    assert srv.digi is None and srv.igate is None
    assert call(srv, "POST", "/api/settings", {"changes": {"digi_enabled": True, "digi_wide": True}}, cookie=cookie)[0] == 200
    assert srv.digi is not None and srv.digi.wide
    st, data, _ = call(srv, "POST", "/api/settings", {"changes": {"igate_enabled": True}}, cookie=cookie)
    assert st == 400 and "igate_passcode" in data["errors"] and srv.igate is None
    st, _, _ = call(srv, "POST", "/api/settings", {"changes": {"igate_enabled": True, "igate_passcode": "13023", "igate_server": "127.0.0.1:1"}}, cookie=cookie)
    assert st == 200 and srv.igate is not None
    call(srv, "POST", "/api/settings", {"changes": {"digi_enabled": False, "igate_enabled": False}}, cookie=cookie)
    assert srv.digi is None and srv.igate is None


def test_allow_tx_applies_live(srv):
    cookie, _ = setup(srv)
    call(srv, "POST", "/api/settings", {"changes": {"share_allow_tx": False}}, cookie=cookie)
    assert call(srv, "POST", "/api/tx", {"type": "beacon"}, cookie=cookie)[0] == 403
    call(srv, "POST", "/api/settings", {"changes": {"share_allow_tx": True}}, cookie=cookie)
    assert call(srv, "POST", "/api/tx", {"type": "beacon"}, cookie=cookie)[0] == 200


def test_an_unconfigured_server_runs_and_will_not_transmit(tmp_path, monkeypatch):
    monkeypatch.setattr("simpleaprs.config.PATH", tmp_path / "config.json")
    monkeypatch.setattr(headless, "STATIONS_PATH", tmp_path / "stations.json")
    monkeypatch.setattr(headless, "WEB_EVENTS_PATH", tmp_path / "web-events.jsonl")
    monkeypatch.setattr(headless, "FRAMES_PATH", tmp_path / "frames.jsonl")
    s = headless.Server(Config(share_port=0), quiet=True, auth=Auth(tmp_path / "auth.json"))
    s.radio = FakeRadio()
    try:
        assert s.start()
        assert s.status()["configured"] is False
        cookie, _ = setup(s)
        assert call(s, "POST", "/api/tx", {"type": "beacon"}, cookie=cookie)[0] == 200  # accepted, but nothing is sent without a callsign
        assert s.radio.sent == []
        assert call(s, "POST", "/api/settings", {"changes": {"callsign": "N0CALL", "lat": 30, "lon": -97}}, cookie=cookie)[0] == 200
        call(s, "POST", "/api/tx", {"type": "beacon"}, cookie=cookie)
        assert len(s.radio.sent) == 1
    finally:
        s.stop()


def test_forget_inactive_needs_a_session_and_forgets_grey_stations(srv):
    from simpleaprs import aprs
    srv.tracker.on_frame(aprs.make_frame("OLD1", aprs.build_position(41.7, -72.7, "/>", ""), "WIDE1-1").encode(), 1.0)
    srv.tracker.live.clear()  # as if it had been loaded from the file
    assert call(srv, "POST", "/api/forget-inactive", {})[0] == 401
    assert "OLD1" in srv.tracker.stations
    cookie, _ = setup(srv)
    st, data, _ = call(srv, "POST", "/api/forget-inactive", {}, cookie=cookie)
    assert (st, data["removed"]) == (200, 1) and "OLD1" not in srv.tracker.stations


def test_a_plain_http_server_has_no_certificate_to_offer(srv):
    cookie, _ = setup(srv)
    assert call(srv, "GET", "/api/certificate", cookie=cookie)[0] == 404
