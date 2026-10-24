from simpleaprs.aprs import build_message, classify, build_position, make_frame, parse, route, digipeaters, parse_text
from simpleaprs.ax25 import Address, Frame


def rt(info):
    return parse(Frame.decode(make_frame("N0CALL-9", info).encode()))


def test_bulletin_has_no_message_number():
    raw = build_message("BLN1", "net at 8")
    assert raw == b":BLN1     :net at 8"
    p = rt(raw)
    assert (p.kind, p.to, p.text, p.msg_id) == ("message", "BLN1", "net at 8", "")


def test_is_bulletin():
    from simpleaprs.aprs import is_bulletin
    assert all(is_bulletin(x) for x in ("BLN0", "BLN9", "BLN1WX", "BLNA", "BLN3GROUP"))
    assert not any(is_bulletin(x) for x in ("BLN", "BLN1TOOLONG", "N0CALL-7", "XBLN1", ""))


def test_position_roundtrip():
    p = rt(build_position(49.0583, -72.0292, "/>", "hello"))
    assert p.kind == "position" and abs(p.lat - 49.0583) < 1e-3 and abs(p.lon + 72.0292) < 1e-3
    assert p.comment == "hello" and p.symbol == "/>"


def test_southern_hemisphere():
    p = rt(build_position(-33.86, 151.21))
    assert p.lat < 0 and p.lon > 0


def test_message():
    p = rt(build_message("W1AW", "hi there", "42"))
    assert (p.kind, p.to, p.text, p.msg_id) == ("message", "W1AW", "hi there", "42")


def test_compressed():
    f = Frame.decode(make_frame("A", b"!/5L!!<*e7>{?G").encode())
    p = parse(f)
    assert p.kind == "position" and abs(p.lat - 49.5) < 0.01 and abs(p.lon + 72.75) < 0.01


def mice(dst, info):
    f = make_frame("N0CALL-9", info.encode("latin-1"))
    f.dst = Address.parse(dst)
    return parse(Frame.decode(f.encode()))


def test_mice_west_longitude():
    # 29 12.03 N, 98 42.55 W: dest digits 291203 with N (byte 4) and W (byte 6) flags set
    p = mice("291R0S", "`~FS123>/")
    assert abs(p.lat - 29.2005) < 1e-3 and abs(p.lon + 98.7092) < 1e-3
    assert p.symbol == "/>"


def test_mice_east_longitude():
    p = mice("291R03", "`~FS123>/")  # byte 6 is a digit: east
    assert p.lon > 0


def test_object():
    p = rt(b";CAMP     *092345z2912.03N/09842.55W-Field day")
    assert (p.kind, p.source, p.symbol, p.comment) == ("position", "CAMP", "/-", "Field day")
    assert abs(p.lat - 29.2005) < 1e-3 and abs(p.lon + 98.7092) < 1e-3


def test_item():
    p = rt(b")AID#2!2912.03N/09842.55WaFirst aid")
    assert (p.kind, p.source, p.comment) == ("position", "AID#2", "First aid")


def test_position_ambiguity():
    p = rt(b"!2912.  N/09842.  W>")
    assert abs(p.lat - 29.2) < 1e-3 and abs(p.lon + 98.7) < 1e-3


def test_weather_with_position():
    p = rt(b"!2912.03N/09842.55W_220/004g005t077r000p000P000h50b09720")
    assert p.kind == "position" and p.weather["Temp"] == "77 °F" and p.weather["Wind"] == "4 mph from 220°"
    assert p.weather["Humidity"] == "50%" and p.weather["Pressure"] == "972.0 hPa" and p.weather["Gust"] == "5 mph"


def test_positionless_weather():
    p = rt(b"_10090556c220s004g005t-05r001p002P003h00b10132")
    assert p.kind == "weather" and p.lat is None
    assert p.weather["Temp"] == "-5 °F" and p.weather["Humidity"] == "100%" and p.weather["Wind"] == "4 mph from 220°"
    assert p.weather["Rain 24h"] == "0.02 in"


def test_classify():
    assert classify("/_") == ("weather", "wx", "Weather station")
    assert classify("/>", weather=True)[1] == "wx"  # a car that also reports weather is a weather station
    assert classify("I&")[:2] == ("igate", "igate")
    assert classify("/>")[:2] == ("car", "other") and classify("\\>")[0] == "car"
    assert classify("/'")[0] == "plane" and classify("/-")[0] == "house"
    assert classify("/?")[0] == "house"  # unknown symbol: fixed station


def test_route():
    pos = {"W5SRC": (29.0, -98.0), "KD5DIG-1": (29.5, -98.5), "W5HI": (30.0, -99.0)}
    f = make_frame("W5SRC", b"!", "KD5DIG-1*,NOPOS*,WIDE2-1")
    segs, text = route(f, pos.get, (29.2, -98.7))
    assert text == "KD5DIG-1 → NOPOS (no position) → you"
    assert [s["dashed"] for s in segs] == [False, True]  # src->digi solid, digi->(gap)->you dashed
    assert segs[0]["coords"] == [[-98.0, 29.0], [-98.5, 29.5]]
    segs, text = route(make_frame("W5SRC", b"!"), pos.get, (29.2, -98.7))
    assert text == "heard directly"
    assert len(segs) == 1 and not segs[0]["dashed"]
    f = make_frame("W5SRC", b"!", "WIDE1-1*,WIDE2-1")  # alias substituted by nobody: a gap
    assert route(f, pos.get, (29.2, -98.7))[0][0]["dashed"]


def test_digipeaters_ignores_aliases_and_unrepeated():
    f = make_frame("W5SRC", b"!", "KD5DIG-1*,WIDE1*,K5NEXT,WIDE2-1")
    assert digipeaters(f) == ["KD5DIG-1"]


def test_third_party_from_internet():
    info = b"}W5FRIEND>APRS,TCPIP,N0GATE*::N0CALL   :hello{12"
    p = rt(info)
    assert p.gated and p.inner.kind == "message" and p.inner.to == "N0CALL"
    assert (p.inner.source, p.inner.text, p.inner.msg_id) == ("W5FRIEND", "hello", "12")


def test_third_party_over_rf_is_not_an_igate():
    p = rt(b"}W5FRIEND>APRS,WIDE1-1:!2912.03N/09842.55W>")
    assert p.inner and not p.gated


def test_parse_text_garbage():
    assert parse_text("no header here") is None


def test_classify_roles():
    assert classify("/>", roles={"digi"}) == ("tower", "digi", "Digipeater")
    assert classify("/>", roles={"igate"}) == ("igate", "igate", "iGate")
    assert classify("/>", roles={"igate", "digi"})[2] == "iGate + digipeater"
    assert classify("/#")[:2] == ("tower", "digi")  # claimed by symbol
    assert classify("/_", roles={"digi"}) == ("weather", "wx", "Weather station + digipeater")


def test_kinds():
    from simpleaprs.aprs import kinds
    assert kinds("/_") == {"weather"} and kinds("/>", weather=True) == {"weather", "mobile"}
    assert kinds("I&") == {"igate"} and kinds("/-") == set()
    assert kinds("/>", roles={"digi", "igate"}) == {"digi", "igate", "mobile"}


def test_symbol_choices_all_classify():
    from simpleaprs.aprs import SYMBOLS
    for label, sym in SYMBOLS:
        assert len(sym) == 2
        icon, _, kind = classify(sym)
        assert icon != "house" or sym in ("/-", "/y"), (label, sym)  # an unmapped code would silently fall back to a house
    assert classify("/r")[0] == "tower" and classify("/[")[0] == "person"
