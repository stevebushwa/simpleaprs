from simpleaprs.config import Config
from simpleaprs.webconfig import GROUPS, fields, validate

INS, OUTS = ["USB Audio", "Built-in"], ["USB Audio"]


def check(changes, **cfg):
    return validate(changes, Config(callsign="N0CALL-7", **cfg), INS, OUTS)


def test_every_field_belongs_to_a_group_and_is_a_config_setting():
    cfg = Config()
    for f in fields(INS, OUTS, "/>"):
        assert f.group in GROUPS and hasattr(cfg, f.key), f.key
        assert f.kind in ("text", "number", "bool", "select")
        if f.shows_if:
            assert any(o.key == f.shows_if and o.kind == "bool" for o in fields(INS, OUTS, "/>")), f.key


def test_every_field_accepts_its_own_current_value():
    cfg = Config(callsign="N0CALL-7", igate_passcode="123")
    clean, errors = validate({f.key: getattr(cfg, f.key) for f in fields(INS, OUTS, cfg.symbol)}, cfg, INS, OUTS)
    assert errors == {} and clean["callsign"] == "N0CALL-7"


def test_valid_values_are_cleaned():
    clean, errors = check({"callsign": " w1aw-9 ", "lat": 30.25, "lon": -97, "path": "wide1-1, wide2-1", "beacon_minutes": 10,
                           "nomad_url": "http://map:8080/", "tx_level": 0.5, "input_device": "USB Audio", "igate_server": "noc3.aprs2.net"})
    assert errors == {}
    assert clean["callsign"] == "W1AW-9" and clean["path"] == "WIDE1-1,WIDE2-1" and clean["nomad_url"] == "http://map:8080"
    assert clean["lon"] == -97.0 and isinstance(clean["lon"], float) and isinstance(clean["beacon_minutes"], int)


def test_bad_values_are_refused_with_a_reason():
    bad = {"callsign": "not a call", "lat": 91, "lon": "west", "symbol": "/", "comment": "x" * 44, "path": "A,B,C,D,E,F,G,H,I",
           "beacon_minutes": -1, "tx_lead_ms": 5, "tx_level": 0, "nomad_url": "ftp://x", "map_cache": "yes", "auto_gain": "on", "map_cache_mb": 10,
           "igate_server": "bad host!", "igate_passcode": "abc", "igate_radius_km": 0, "input_device": "No Such Card", "bogus": 1}
    clean, errors = check(bad)
    assert clean.keys() == set() and errors.keys() == bad.keys(), set(bad) - set(errors)
    assert errors["bogus"] == "unknown setting"


def test_booleans_and_numbers_are_strict_about_type():
    assert check({"map_cache": 1})[1] and check({"digi_enabled": "true"})[1]
    assert check({"lat": True})[1] and check({"beacon_minutes": "5"})[1]
    assert check({"map_cache": False})[1] == {}


def test_not_a_dict():
    assert check([1, 2])[1] and check(None)[1]


def test_gateways_need_their_prerequisites():
    assert "igate_passcode" in check({"igate_enabled": True})[1]
    assert check({"igate_enabled": True, "igate_passcode": "13023"})[1] == {}
    assert check({"igate_enabled": True}, igate_passcode="13023")[1] == {}  # passcode already saved
    _, errors = validate({"callsign": "N0CALL"}, Config(), INS, OUTS)
    assert errors == {}
    _, errors = validate({"digi_enabled": True}, Config(callsign=""), INS, OUTS)
    assert "callsign" in errors


def test_custom_symbol_stays_selectable():
    sym = next(f for f in fields(INS, OUTS, "I&") if f.key == "symbol")
    assert ["I&", "Custom  (I&)"] in sym.options
    assert not any("Custom" in label for _, label in next(f for f in fields(INS, OUTS, "/>") if f.key == "symbol").options)


def test_damaged_packet_settings():
    new = {"damaged_list": False, "damaged_heard": True, "damaged_map": True, "damaged_copies": 5, "damaged_radius_m": 300}
    clean, errors = check(new)
    assert errors == {} and clean == new
    _, errors = check({"damaged_list": "yes", "damaged_copies": 0, "damaged_radius_m": 10})
    assert set(errors) == {"damaged_list", "damaged_copies", "damaged_radius_m"}


def test_the_first_version_of_the_damaged_setting_is_carried_over(tmp_path, monkeypatch):
    import json
    from simpleaprs import config
    for old, flags in (("off", (0, 0, 0)), ("list", (1, 0, 0)), ("heard", (1, 1, 0)), ("map", (1, 1, 1))):
        (tmp_path / "c.json").write_text(json.dumps({"callsign": "N0CALL", "damaged_mode": old, "damaged_copies": 2}))
        monkeypatch.setattr(config, "PATH", tmp_path / "c.json")
        c = config.Config.load()
        assert (c.damaged_list, c.damaged_heard, c.damaged_map) == tuple(map(bool, flags)) and c.damaged_copies == 2
