import pytest

from simpleaprs import cli, headless
from simpleaprs.config import Config


def test_flags_override_config():
    cfg = Config(callsign="OLD", share_port=8073)
    args = cli.parse(["--headless", "--callsign", "n0call-7", "--port", "9000", "--lat", "30.5", "--no-tx",
                      "--input-device", "USB Audio", "--lead-ms", "800", "--nomad-url", "http://map:8080",
                      "--allow-host", "pi.home", "--allow-host", "aprs.lan"])
    cli.apply_overrides(cfg, args)
    assert (cfg.callsign, cfg.share_port, cfg.lat, cfg.tx_lead_ms) == ("N0CALL-7", 9000, 30.5, 800)
    assert cfg.input_device == "USB Audio" and cfg.nomad_url == "http://map:8080" and not cfg.share_allow_tx
    assert args.allow_host == ["pi.home", "aprs.lan"]


def test_no_flags_leave_config_alone():
    cfg = Config(callsign="N0CALL", comment="hi")
    before = vars(cfg).copy()
    cli.apply_overrides(cfg, cli.parse(["--headless"]))
    assert vars(cfg) == before


def run_setup(monkeypatch, tmp_path, cfg, answers):
    monkeypatch.setattr(headless, "PATH", tmp_path / "config.json")
    monkeypatch.setattr("simpleaprs.config.PATH", tmp_path / "config.json")
    monkeypatch.setattr(headless, "device_names", lambda inputs: ["Mic A", "Mic B"] if inputs else ["Out A"])
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))
    return headless.setup(cfg)


def test_setup_first_time(monkeypatch, tmp_path):
    cfg = run_setup(monkeypatch, tmp_path, Config(), [
        "n0call-7", "30.25", "-97.5", "", "", "", "2", "1", "", "10", "", "", "", "", ""])
    assert cfg.callsign == "N0CALL-7" and (cfg.lat, cfg.lon) == (30.25, -97.5)
    assert (cfg.input_device, cfg.output_device, cfg.beacon_minutes) == ("Mic B", "Out A", 10)
    assert cfg.nomad_url == Config().nomad_url and cfg.share_allow_tx
    assert (tmp_path / "config.json").exists()


def test_setup_again_keeps_values(monkeypatch, tmp_path):
    old = Config(callsign="N0CALL-7", lat=1.5, lon=2.5, input_device="Mic B", output_device="Out A", share_port=9000)
    cfg = run_setup(monkeypatch, tmp_path, old, [""] * 15)  # Enter through every question
    assert (cfg.callsign, cfg.lat, cfg.input_device, cfg.share_port) == ("N0CALL-7", 1.5, "Mic B", 9000)


def test_setup_rejects_bad_input_then_accepts(monkeypatch, tmp_path):
    cfg = run_setup(monkeypatch, tmp_path, Config(), [
        "k1abc", "999", "abc", "40", "-100", "x", "/-", "", "", "7", "0", "", "", "", "", "", "", "", ""])
    assert (cfg.lat, cfg.lon, cfg.symbol) == (40.0, -100.0, "/-")
    assert cfg.input_device == ""


def test_gateway_flags():
    cfg = Config(callsign="N0CALL", igate_enabled=True, igate_to_rf=True, digi_enabled=True)
    cli.apply_overrides(cfg, cli.parse(["--headless", "--no-igate", "--no-igate-to-rf", "--digipeater", "wide"]))
    assert not cfg.igate_enabled and not cfg.igate_to_rf and cfg.digi_enabled and cfg.digi_wide
    cli.apply_overrides(cfg, cli.parse(["--headless", "--igate", "--igate-server", "noc3.aprs2.net:14580", "--igate-passcode", "123",
                                        "--igate-to-rf", "--igate-radius", "30", "--digipeater", "fill"]))
    assert (cfg.igate_enabled, cfg.igate_server, cfg.igate_passcode, cfg.igate_to_rf, cfg.igate_radius_km) == (True, "noc3.aprs2.net:14580", "123", True, 30)
    assert cfg.digi_enabled and not cfg.digi_wide
    cli.apply_overrides(cfg, cli.parse(["--headless", "--digipeater", "off"]))
    assert not cfg.digi_enabled


def test_setup_gateways(monkeypatch, tmp_path):
    # ...callsign to Enter-through, then: iGate yes, default server, calc the passcode, to-RF yes, 40 km, digipeater yes, wide yes
    cfg = run_setup(monkeypatch, tmp_path, Config(callsign="N0CALL-7"), [""] * 13 + ["y", "", "calc", "y", "40", "y", "y"])
    assert cfg.igate_enabled and cfg.igate_passcode == "13023" and cfg.igate_to_rf and cfg.igate_radius_km == 40
    assert cfg.digi_enabled and cfg.digi_wide
