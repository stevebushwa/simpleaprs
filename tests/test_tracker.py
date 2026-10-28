import json

import pytest

from simpleaprs import aprs
from simpleaprs.config import Config
from simpleaprs.tracker import Tracker

ME = "N0CALL-7"


@pytest.fixture
def rig(tmp_path):
    events, replies = [], []
    cfg = Config(callsign=ME, lat=30.0, lon=-97.0)
    t = Tracker(cfg, lambda ev, keep=True: events.append((ev, keep)), replies.append, tmp_path / "stations.json")
    return t, events, replies, tmp_path


def frame(src, info, path="WIDE1-1"):
    return aprs.make_frame(src, info, path).encode()


def position(src="W1AW-9", comment="hello", path="WIDE1-1"):
    return frame(src, aprs.build_position(41.7, -72.7, "/>", comment), path)


def types(events):
    return [ev["type"] for ev, _ in events]


def test_position_makes_packet_and_station_events(rig):
    t, events, _, _ = rig
    t.on_frame(position(), 100.0)
    assert types(events) == ["packet", "station"]
    st, keep = events[1]
    assert keep is False  # stations aren't replayed, the intro has current state
    assert (st["call"], st["note"], st["stale"], st["heard"]) == ("W1AW-9", "hello", False, 100.0)
    assert st["lat"] == pytest.approx(41.7, abs=0.01) and ["Comment", "hello"] in st["info"]


def test_intro_has_me_first_then_every_station(rig):
    t, _, _, _ = rig
    t.on_frame(position("W1AW-9"))
    t.on_frame(position("K1ABC"))
    intro = t.intro(allow_tx=False)
    assert intro[0]["type"] == "me" and intro[0]["call"] == ME and intro[0]["allow_tx"] is False
    assert [e["call"] for e in intro[1:]] == ["W1AW-9", "K1ABC"]


def test_message_for_us_is_shown_and_acked(rig):
    t, events, replies, _ = rig
    t.on_frame(frame("W1AW-9", aprs.build_message(ME, "hi there", "12"), ""), 50.0)
    msgs = [ev for ev, _ in events if ev["type"] == "message"]
    assert msgs == [{"type": "message", "kind": "direct", "t": 50.0, "text": "W1AW-9: hi there", "peer": "W1AW-9"}]  # peer: whose folder it goes in
    assert replies == [aprs.build_message("W1AW-9", "ack12")]


def test_message_for_someone_else_is_ignored(rig):
    t, events, replies, _ = rig
    t.on_frame(frame("W1AW-9", aprs.build_message("K1ABC", "hi", "1"), ""))
    assert "message" not in types(events) and replies == []


def test_own_packet_repeated_is_flagged_not_plotted(rig):
    t, events, _, _ = rig
    t.on_frame(frame(ME, aprs.build_position(30.0, -97.0, "/>", "me"), "DIGI1*"))
    assert types(events) == ["packet"] and "OUR PACKET, repeated by DIGI1" in events[0][0]["text"]
    assert t.stations == {}


def test_digipeater_role_changes_icon_of_known_station(rig):
    t, events, _, _ = rig
    t.on_frame(position("DIGI1"))
    t.on_frame(position("K1ABC", path="DIGI1*,WIDE2-1"))
    stations = [ev for ev, _ in events if ev["type"] == "station"]
    digi = [s for s in stations if s["call"] == "DIGI1"][-1]
    assert "digi" in digi["kinds"] and digi["cls"] == "digi"


def test_route_redrawn_when_a_hop_gets_a_position(rig):
    t, events, _, _ = rig
    t.on_frame(position("K1ABC", path="DIGI1*"))
    events.clear()
    t.on_frame(position("DIGI1"))
    assert [ev["call"] for ev, _ in events if ev["type"] == "station"].count("K1ABC") == 1


def test_weather_updates_known_station(rig):
    t, events, _, _ = rig
    t.on_frame(frame("W1AW-9", b"!4142.00N/07242.00W_220/004g005t077r000p000P000h50b09720", ""))
    st = [ev for ev, _ in events if ev["type"] == "station"][-1]
    assert st["temp"] == "77 °F" and "weather" in st["kinds"]


def test_tx_shows_packet_and_own_messages_but_not_acks(rig):
    t, events, _, _ = rig
    t.on_tx(frame(ME, aprs.build_message("W1AW-9", "hello", "7")), 10.0)
    t.on_tx(frame(ME, aprs.build_message("W1AW-9", "ack3")), 11.0)
    assert [(ev["type"], ev.get("tx")) for ev, _ in events] == [("packet", True), ("message", None), ("packet", True)]
    assert events[1][0]["text"] == "me -> W1AW-9: hello" and events[1][0]["peer"] == "W1AW-9"


def test_tx_bulletin_shows_in_messages(rig):
    t, events, _, _ = rig
    t.on_tx(frame(ME, aprs.build_message("BLN1", "net at 8")), 10.0)
    assert events[1][0] == {"type": "message", "kind": "bulletin", "t": 10.0, "text": "me -> BLN1: net at 8"}  # a bulletin is with nobody


def test_received_bulletin_is_logged_once_per_text(rig):
    t, events, replies, _ = rig
    def msgs():
        return [(ev["kind"], ev["text"]) for ev, _ in events if ev["type"] == "message"]
    t.on_frame(frame("W1AW-9", aprs.build_message("BLN1", "net at 8")), 10.0)
    t.on_frame(frame("W1AW-9", aprs.build_message("BLN1", "net at 8")), 20.0)  # the sender repeats it
    assert msgs() == [("bulletin", "BLN1 from W1AW-9: net at 8")] and replies == []  # nobody acks a bulletin
    t.on_frame(frame("W1AW-9", aprs.build_message("BLN1", "net moved to 9")), 30.0)
    t.on_frame(frame("K5XYZ", aprs.build_message("BLN1", "net at 8")), 40.0)  # same text, other sender
    assert [m[1] for m in msgs()] == ["BLN1 from W1AW-9: net at 8", "BLN1 from W1AW-9: net moved to 9", "BLN1 from K5XYZ: net at 8"]


def test_direct_message_is_tagged_direct_and_others_ignored(rig):
    t, events, _, _ = rig
    t.on_frame(frame("W1AW-9", aprs.build_message(ME, "hi", "3")), 10.0)
    t.on_frame(frame("W1AW-9", aprs.build_message("K5XYZ", "not for us", "4")), 11.0)
    assert [(ev["kind"], ev["text"]) for ev, _ in events if ev["type"] == "message"] == [("direct", "W1AW-9: hi")]


def test_stations_survive_a_restart_as_stale(rig):
    t, _, _, tmp = rig
    t.on_frame(position(), 100.0)
    t.save()
    again = Tracker(Config(callsign=ME), lambda *a: None, lambda b: None, tmp / "stations.json")
    again.load()
    st = again.intro(True)[1]
    assert (st["call"], st["stale"], st["heard"]) == ("W1AW-9", True, 100.0)
    assert json.loads((tmp / "stations.json").read_text())["stations"][0]["heard"] == 100.0
