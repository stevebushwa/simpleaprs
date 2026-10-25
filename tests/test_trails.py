import pytest

from simpleaprs import aprs, trails
from simpleaprs.config import Config
from simpleaprs.tracker import Tracker


def test_a_repeat_from_the_same_spot_is_not_a_move():
    t = trails.Trail()
    assert trails.extend(t, 30.0, -97.0)
    assert not trails.extend(t, 30.00005, -97.0)  # ~5 m: GPS jitter
    assert trails.extend(t, 30.001, -97.0)        # ~110 m
    assert len(t) == 2 and trails.line(t) == t
    assert trails.line(t[:1]) == []               # one point is a place, not a track


def test_trail_is_capped_and_loading_junk_gives_nothing():
    t = trails.Trail()
    for i in range(trails.MAX_POINTS + 50):
        trails.extend(t, 30.0 + i * 0.001, -97.0)
    assert len(t) == trails.MAX_POINTS and t[-1][0] == pytest.approx(30.0 + (trails.MAX_POINTS + 49) * 0.001)
    assert trails.clean("junk") == [] and trails.clean([[1]]) == [] and trails.clean([[95, 0]]) == []
    assert trails.clean(None) == []


@pytest.fixture
def rig(tmp_path):
    events = []
    t = Tracker(Config(callsign="N0CALL-7", lat=30.0, lon=-97.0), lambda ev, keep=True: events.append(ev), lambda i: None,
                tmp_path / "stations.json")
    return t, events, tmp_path


def pos(lat, lon, src="W1AW-9"):
    return aprs.make_frame(src, aprs.build_position(lat, lon, "/>", ""), "WIDE1-1").encode()


def test_tracker_records_a_track_and_keeps_it_across_restarts(rig):
    t, events, tmp = rig
    t.on_frame(pos(41.70, -72.70), 100.0)
    assert [e for e in events if e["type"] == "station"][-1]["trail"] == []  # not moved yet
    t.on_frame(pos(41.71, -72.70), 160.0)
    st = [e for e in events if e["type"] == "station"][-1]
    assert len(st["trail"]) == 2 and st["trail"][1][0] == pytest.approx(41.71, abs=1e-4)
    t.save()
    t2 = Tracker(t.cfg, lambda *a, **k: None, lambda i: None, tmp / "stations.json")
    t2.load()
    assert len(t2.trails["W1AW-9"]) == 2
    assert t2.station_event("W1AW-9")["trail"] == t.station_event("W1AW-9")["trail"]


def test_old_files_without_trails_still_load(rig):
    t, _, tmp = rig
    t.on_frame(pos(41.7, -72.7), 100.0)
    d = t.data(); d.pop("trails")
    (tmp / "stations.json").write_text(__import__("json").dumps(d))
    t2 = Tracker(t.cfg, lambda *a, **k: None, lambda i: None, tmp / "stations.json")
    t2.load()
    assert t2.trails["W1AW-9"] == [[pytest.approx(41.7, abs=0.01), pytest.approx(-72.7, abs=0.01), 100]]  # one point: where it is now


def test_forget_inactive_drops_only_stations_not_heard_this_run(rig):
    t, events, tmp = rig
    t.on_frame(pos(41.7, -72.7, "OLD1"), 100.0)
    t.on_frame(pos(41.8, -72.8, "OLD2"), 100.0)
    t.save()
    t2 = Tracker(t.cfg, lambda ev, keep=True: events.append(ev), lambda i: None, tmp / "stations.json")
    t2.load()  # both remembered, neither live
    t2.on_frame(pos(42.0, -73.0, "NEW"), 200.0)
    events.clear()
    assert t2.forget_inactive() == ["OLD1", "OLD2"]
    assert set(t2.stations) == {"NEW"} and "OLD1" not in t2.trails and "OLD2" not in t2.heard
    assert events == [{"type": "gone", "calls": ["OLD1", "OLD2"]}]
    assert [e for e in t2.intro(True) if e["type"] == "station"][0]["call"] == "NEW"
    assert t2.forget_inactive() == []  # nothing left to forget, nothing announced
    assert "OLD1" not in __import__("json").loads((tmp / "stations.json").read_text())["trails"]


def test_one_corrupted_packet_does_not_draw_a_spike():
    """WX5II-4 really did this: a latitude read as 19.323 instead of 29.323 for one beacon."""
    t = trails.Trail()
    assert trails.extend(t, 29.323, -100.396, 1000)
    assert not trails.extend(t, 19.323, -100.396, 1010)  # 1100 km in ten seconds
    assert not trails.extend(t, 29.323, -100.396, 1020)  # back where it was: no move
    assert len(t) == 1 and t.pending is None
    assert trails.extend(t, 29.333, -100.396, 1080)      # a real move afterwards still counts
    assert len(t) == 2


def test_a_jump_two_packets_agree_on_is_believed():
    t = trails.Trail()
    trails.extend(t, 29.0, -100.0, 1000)
    assert not trails.extend(t, 19.0, -100.0, 1010)   # not yet
    assert trails.extend(t, 19.001, -100.0, 1020)     # the next one agrees: the first point was the bad one
    assert len(t) == 1 and t[0][0] == pytest.approx(19.001)


def test_a_long_gap_makes_a_long_trip_believable():
    t = trails.Trail()
    trails.extend(t, 29.0, -100.0, 1000)
    assert trails.extend(t, 39.0, -100.0, 1000 + 4 * 3600)  # 1100 km in four hours: a drive, or a flight
    assert len(t) == 2


def test_saved_trails_lose_lone_spikes():
    saved = [[29.323, -100.396], [19.323, -100.396], [29.323, -100.396], [29.4, -100.4]]
    assert [p[0] for p in trails.clean(saved)] == [29.323, 29.4]
    assert trails.line(trails.clean(saved[:3])) == []  # all that was left is one place
    assert len(trails.clean([[29.0, -100.0], [30.0, -100.0], [31.0, -100.0]])) == 3  # a real trip stays


def test_tracker_ignores_the_corrupted_beacon(rig):
    t, events, _ = rig
    t.on_frame(pos(29.323, -100.396, "WX5II-4"), 1000.0)
    t.on_frame(pos(19.323, -100.396, "WX5II-4"), 1010.0)
    t.on_frame(pos(29.323, -100.396, "WX5II-4"), 1020.0)
    assert t.station_event("WX5II-4")["trail"] == []


def test_a_wrong_packet_that_looks_possible_is_removed_when_the_station_is_back():
    """The real case: 87 minutes between the wrong beacon and the next, so the speed alone looks like a plane."""
    t = trails.Trail([[29.323, -100.396]])  # a point from an old save: no time
    assert trails.extend(t, 19.323, -100.396, 1792890368)
    assert len(t) == 2
    trails.extend(t, 29.323, -100.396, 1792895574)
    assert [p[:2] for p in t] == [[29.323, -100.396]] and trails.line(t) == []
    t2 = trails.Trail()
    trails.extend(t2, 29.323, -100.396, 1000)
    assert trails.extend(t2, 19.323, -100.396, 1000 + 5000)      # 220 m/s: believable on its own
    trails.extend(t2, 29.323, -100.396, 1000 + 10200)
    assert len(t2) == 1


def test_replayed_history_does_not_double_a_track_back_on_itself():
    t = trails.Trail()
    route = [(30.0 + i * 0.01, -97.0, 1000 + i * 60) for i in range(5)]
    for lat, lon, when in route:
        trails.extend(t, lat, lon, when)
    before = [list(p) for p in t]
    for lat, lon, when in route:  # a server sending its backlog again after we reconnect
        trails.extend(t, lat, lon, when)
    assert t == before


def test_a_real_out_and_back_with_stops_is_kept():
    t = trails.Trail()
    for i, lat in enumerate([29.0, 29.4, 29.8, 29.4, 29.0]):  # driven 45 km north and back, heard at each stop
        trails.extend(t, lat, -100.0, 1000 + i * 3600)
    assert len(t) == 5
    t = trails.Trail()
    for i, lat in enumerate([29.323, 28.323, 29.323]):  # one flipped degree digit: 111 km
        trails.extend(t, lat, -100.396, 1000 + i * 5000)
    assert len(t) == 1
