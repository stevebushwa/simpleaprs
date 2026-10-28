from simpleaprs import details
from simpleaprs.aprs import build_position, make_frame, parse
from simpleaprs.ax25 import Frame


def rows(comment, symbol="/-"):
    info = build_position(30.25, -97.75, symbol, comment)
    return dict(details.extract(parse(Frame.decode(make_frame("N0CALL-9", info).encode()))))


def test_phg_is_power_height_gain_and_direction():
    r = rows("PHG5360 Austin")  # 5 -> 25 W, 3 -> 80 ft, 6 -> 6 dB, 0 -> omni
    assert r["Power"] == "25 W" and r["Antenna height"].startswith("80 ft") and r["Antenna gain"] == "6 dB" and r["Antenna direction"] == "omni"
    assert rows("PHG2132")["Antenna direction"] == "east" and rows("PHG9138")["Antenna direction"] == "north"
    assert rows("PHG0000")["Power"] == "0 W"


def test_a_code_wins_over_free_text():
    r = rows("PHG5360 running 100W, 3dBi gain")
    assert r["Power"] == "25 W" and r["Antenna gain"] == "6 dB"


def test_range_altitude_and_speed():
    assert rows("RNG0050")["Range"] == "50 mi"
    assert rows("comment /A=001234")["Altitude"].startswith("1,234 ft (376 m)")
    r = rows("090/025 moving")
    assert r["Course"] == "90°" and r["Speed"].startswith("25 kn (29 mph)")
    assert "Speed" not in rows("000/000 parked")
    assert "Speed" not in rows("090/025 wind", symbol="/_")  # a weather station's is the wind


def test_mic_e_speed_and_course():
    # speed 25 kn, course 123: the three bytes after the position are (tens of knots, units and hundreds of degrees, the rest) + 28
    info = "`" + "abc" + chr(28 + 2) + chr(28 + 51) + chr(28 + 23) + ">/"
    assert details._mice_motion(info) == (25, 123)
    assert details._mice_motion("`abc" + chr(28 + 80) + chr(28) + chr(28) + ">/") == (0, 0)  # 800 and over means 0 and over, minus 800
    assert details._mice_motion(">status") is None


def test_frequency_tone_and_offset():
    r = rows("146.520MHz T107 -060")
    assert r["Frequency"] == "146.52 MHz" and r["Tone"] == "T107" and r["Offset"] == "-0.6 MHz"


def test_free_text_power_gain_height_battery_and_contact():
    r = rows("QRP 5W, 3dBi vertical, 40ft AGL, 13.8V, me@example.com www.example.org/x.")
    assert r["Power"] == "5 W" and r["Antenna gain"] == "3 dBi" and r["Antenna height"].startswith("40 ft")
    assert r["Battery"] == "13.8 V" and r["Email"] == "me@example.com" and r["Web"] == "www.example.org/x"
    assert rows("antenna height 12 m")["Antenna height"] == "39 ft (12 m)"


def test_things_that_look_like_these_but_are_not():
    assert rows("2m FM net, W5ABC repeater, 146.52 simplex, N2W") == {}
    assert rows("WX station t072 h45") == {}
