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
        "n0call-7", "30.25", "-97.5", "", "", "", "2", "1", "", "10", "", "", ""])
    assert cfg.callsign == "N0CALL-7" and (cfg.lat, cfg.lon) == (30.25, -97.5)
    assert (cfg.input_device, cfg.output_device, cfg.beacon_minutes) == ("Mic B", "Out A", 10)
    assert cfg.nomad_url == Config().nomad_url and cfg.share_allow_tx
    assert (tmp_path / "config.json").exists()


def test_setup_again_keeps_values(monkeypatch, tmp_path):
    old = Config(callsign="N0CALL-7", lat=1.5, lon=2.5, input_device="Mic B", output_device="Out A", share_port=9000)
    cfg = run_setup(monkeypatch, tmp_path, old, [""] * 13)  # Enter through every question
    assert (cfg.callsign, cfg.lat, cfg.input_device, cfg.share_port) == ("N0CALL-7", 1.5, "Mic B", 9000)


def test_setup_rejects_bad_input_then_accepts(monkeypatch, tmp_path):
    cfg = run_setup(monkeypatch, tmp_path, Config(), [
        "k1abc", "999", "abc", "40", "-100", "x", "/-", "", "", "7", "0", "", "", "", "", "", ""])
    assert (cfg.lat, cfg.lon, cfg.symbol) == (40.0, -100.0, "/-")
    assert cfg.input_device == ""
