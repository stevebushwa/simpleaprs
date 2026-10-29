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


def test_a_station_is_direct_when_no_digipeater_repeated_its_packet(rig):
    t, events, _, _ = rig
    t.on_frame(position("W1AW-9", path="WIDE1-1,WIDE2-1"))  # asks to be repeated, but nobody has yet
    t.on_frame(position("K1ABC", path="K5WH-2*,WIDE2-1"))  # K5WH-2 repeated it
    obj = aprs.make_frame("W1AW-9", b";WX1OBJ   *111111z" + aprs.build_position(41.7, -72.7, "/_", "")[1:], "WIDE1-1").encode()
    t.on_frame(obj)  # an object is somewhere else; whoever sent it is not what is plotted
    direct = {e["call"]: e["direct"] for e, _ in events if e["type"] == "station"}
    assert direct == {"W1AW-9": True, "K1ABC": False, "WX1OBJ": False}


def test_damaged_packet_goes_to_the_raw_list_only(rig):
    t, events, _, _ = rig
    raw = position("W1AW-9", "hello")
    t.on_damaged(raw + b"\x01\xfe", False)
    assert [ev["type"] for ev, _ in events] == ["packet"]
    ev = events[0][0]
    assert ev["bad"] is True and ev["text"].startswith("W1AW-9>") and "DAMAGED" in ev["text"]
    assert not t.stations and not t.heard  # nothing from it reaches the map or the station list


# --- packets that arrive damaged --------------------------------------------------------------------

WX = b"!2935.50N/09815.97W_269/021g023t081r000p001P001h65b10164tRSW"


def hurt(raw: bytes) -> bytes:
    """A packet as it comes out when the last bytes are lost: the text with junk after it, no checksum to check."""
    return raw + b"\x01\xfe\x00"


def damaged_lines(events):
    return [ev["text"] for ev, _ in events if ev["type"] == "packet" and ev.get("bad")]


def stationed(events):
    return [ev for ev, _ in events if ev["type"] == "station"]


def set_mode(rig, mode, **kw):
    t = rig[0]
    t.cfg.damaged_mode = mode
    for k, v in kw.items():
        setattr(t.cfg, k, v)
    return t


def test_damaged_off_shows_nothing(rig):
    t = set_mode(rig, "off")
    t.on_damaged(hurt(frame("W5HKR", WX)), False)
    assert rig[1] == []


def test_damaged_list_only_lists(rig):
    t = set_mode(rig, "list")
    t.on_frame(position("W1AW-9"))
    rig[1].clear()
    t.on_damaged(hurt(position("W1AW-9")), False)
    assert len(damaged_lines(rig[1])) == 1 and "counted" not in damaged_lines(rig[1])[0] and not stationed(rig[1])


def test_heard_refreshes_a_known_station_and_says_so(rig):
    t = set_mode(rig, "heard")
    t.on_frame(position("W1AW-9"), 100.0)
    rig[1].clear()
    t.on_damaged(hurt(position("W1AW-9")), False, 500.0)
    assert t.heard["W1AW-9"] == 500.0 and "counted as heard" in damaged_lines(rig[1])[0] and len(stationed(rig[1])) == 1
    assert t.stations["W1AW-9"].lat == 41.7 and "W1AW-9" not in t.unverified


def test_heard_ignores_a_position_that_disagrees_with_the_known_one(rig):
    t = set_mode(rig, "heard")
    t.on_frame(position("W1AW-9"), 100.0)
    rig[1].clear()
    far = frame("W1AW-9", aprs.build_position(41.9, -72.7, "/>", "hello"))  # 20 km off: a flipped digit, not a move
    t.on_damaged(hurt(far), False, 500.0)
    assert t.heard["W1AW-9"] == 100.0 and "counted" not in damaged_lines(rig[1])[0] and not stationed(rig[1])


def test_heard_does_not_place_a_station_nobody_decoded(rig):
    t = set_mode(rig, "heard")
    for i in range(5):
        t.on_damaged(hurt(frame("W5HKR", WX)), False, 100.0 + i)
    assert "W5HKR" not in t.stations


def test_map_places_a_station_only_after_enough_copies_agree(rig):
    t = set_mode(rig, "map", damaged_copies=3)
    for i in range(2):
        t.on_damaged(hurt(frame("W5HKR", WX)), False, 100.0 + i)
    assert "W5HKR" not in t.stations and not stationed(rig[1])
    t.on_damaged(hurt(frame("W5HKR", WX)), False, 103.0)
    ev, = stationed(rig[1])
    assert ev["call"] == "W5HKR" and ev["unverified"] is True and ev["info"][0][0] == "Unverified" and not ev["stale"]
    assert "put on the map, unverified" in damaged_lines(rig[1])[-1]
    # it is a guess: never saved, never drawn into a route, never counted as heard directly
    assert "W5HKR" not in t.raw and all(s["raw"] != "" for s in t.data()["stations"]) and not t.data()["stations"]
    assert t._pos("W5HKR") is None and ev["direct"] is False


def test_map_needs_the_copies_to_agree_on_where(rig):
    t = set_mode(rig, "map", damaged_copies=3)
    for i, lat in enumerate(("2935.50N", "2945.50N", "2955.50N", "3005.50N")):  # four different places
        t.on_damaged(hurt(frame("W5HKR", WX.replace(b"2935.50N", lat.encode()))), False, 100.0 + i)
    assert "W5HKR" not in t.stations


def test_map_never_places_a_moving_station(rig):
    t = set_mode(rig, "map", damaged_copies=1)
    mice = aprs.make_frame("N5ZR-9", b"`~0YlqU>/`\"8@}441.725MHz T123 +500_%", "WIDE1-1").encode()
    t.on_damaged(hurt(mice), False, 100.0)
    assert "N5ZR-9" not in t.stations


def test_map_refuses_a_digipeater_nobody_decoded_and_text_malformed_in_the_middle(rig):
    t = set_mode(rig, "map", damaged_copies=1)
    t.on_damaged(hurt(frame("W5HKR", WX, "QX5II-11*,WIDE2-1")), False, 100.0)
    t.on_damaged(hurt(frame("W5HKR", WX.replace(b"r000p001", b"r(0p;kL"))), False, 101.0)  # damage with plenty of text after it
    assert "W5HKR" not in t.stations
    t.known.add("WX5II-11")
    t.on_damaged(hurt(frame("W5HKR", WX, "WX5II-11*,WIDE2-1")), False, 102.0)
    assert "W5HKR" in t.stations


def test_a_clean_packet_takes_over_from_a_guess(rig):
    t = set_mode(rig, "map", damaged_copies=1)
    t.on_damaged(hurt(frame("W5HKR", WX)), False, 100.0)
    assert "W5HKR" in t.unverified
    t.on_frame(frame("W5HKR", aprs.build_position(29.5917, -98.2662, "/_", "wx")), 200.0)
    assert "W5HKR" not in t.unverified and "unverified" not in stationed(rig[1])[-1] and "W5HKR" in t.raw


def test_turning_the_map_level_off_takes_the_guesses_away(rig):
    t = set_mode(rig, "map", damaged_copies=1)
    t.on_damaged(hurt(frame("W5HKR", WX)), False, 100.0)
    rig[1].clear()
    t.drop_unverified()
    assert "W5HKR" not in t.stations and rig[1] == [({"type": "gone", "calls": ["W5HKR"]}, False)]


def test_a_damaged_message_is_never_answered(rig):
    t, events, replies, _ = rig
    set_mode(rig, "map", damaged_copies=1)
    t.known.add("W1AW-9")
    t.on_damaged(hurt(frame("W1AW-9", aprs.build_message(ME, "hello", "42"))), False, 100.0)
    assert replies == [] and types(events) == ["packet"]
