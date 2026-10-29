"""The settings the web page can change: what they are, how they are grouped, and what counts as valid.

fields() is sent to the page, which draws its settings form from it; validate() is what checks a submitted
form, so the two can't disagree. No side effects here: the server applies what validate() accepts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import aprs

GROUPS = ["Station", "Radio", "Receive", "Map", "iGate", "Digipeater", "Access"]

_CALL = re.compile(r"^[A-Z0-9]{1,6}(-\d{1,2})?$")
_HOP = re.compile(r"^[A-Z0-9]{1,6}(-\d{1,2})?\*?$")
_HOST = re.compile(r"^[A-Za-z0-9.-]+(:\d{1,5})?$")
_PASSCODE = re.compile(r"^-?\d{1,6}$")


@dataclass
class Field:
    key: str
    label: str
    kind: str          # text | number | bool | select
    group: str
    hint: str = ""
    lo: float = 0
    hi: float = 0
    step: float = 1
    options: list = field(default_factory=list)  # select: [[value, label], ...]
    unit: str = ""
    placeholder: str = ""
    shows_if: str = ""  # only relevant while this boolean setting is on


def fields(inputs: list[str], outputs: list[str], symbol: str) -> list[Field]:
    """inputs/outputs: the audio devices this machine has. symbol: the current one, so a custom symbol stays selectable."""
    symbols = [[s, f"{label}  ({s})"] for label, s in aprs.SYMBOLS]
    if symbol and symbol not in [s for s, _ in symbols]:
        symbols.append([symbol, f"Custom  ({symbol})"])

    def devices(names: list[str]) -> list[list[str]]:
        return [["", "(system default)"]] + [[n, n] for n in names]

    return [
        Field("callsign", "Callsign", "text", "Station", "e.g. N0CALL-7", placeholder="N0CALL-7"),
        Field("lat", "Latitude", "number", "Station", "Decimal degrees, south is negative", -90, 90, 0.00001),
        Field("lon", "Longitude", "number", "Station", "Decimal degrees, west is negative", -180, 180, 0.00001),
        Field("symbol", "Map symbol", "select", "Station", options=symbols),
        Field("comment", "Beacon comment", "text", "Station", "Up to 43 characters"),
        Field("path", "Path for your packets", "text", "Station", "Who should repeat them, e.g. WIDE1-1,WIDE2-1"),
        Field("beacon_minutes", "Auto beacon", "number", "Station", "0 = only when you press Beacon now", 0, 120, 1, unit="minutes"),
        Field("input_device", "Radio audio in", "select", "Radio", "The sound card the radio's speaker/data port feeds", options=devices(inputs)),
        Field("output_device", "Radio audio out", "select", "Radio", "The sound card feeding the radio's mic/data port", options=devices(outputs)),
        Field("tx_lead_ms", "VOX lead-in", "number", "Radio", "Raise it until the start of packets stops getting cut off", 100, 3000, 50, unit="ms"),
        Field("tx_level", "Transmit audio level", "number", "Radio", "Lower it if the radio sounds distorted", 0.05, 1, 0.05),
        Field("auto_gain", "Automatic input volume", "bool", "Radio",
              "Turns the sound card's input volume down when the radio's audio clips and up when it stays weak (PulseAudio/PipeWire)."),
        Field("damaged_list", "Show packets that arrive damaged in the Raw packets tab", "bool", "Receive",
              "Packets heard with a bad checksum are never used as if they were good. The next two let them do a little more; "
              "with all three off they are ignored."),
        Field("damaged_heard", "Count stations we know as heard", "bool", "Receive",
              "Refreshes the last-heard time of a station decoded cleanly before, when the callsigns are ones decoded cleanly and the position agrees"),
        Field("damaged_map", "Put stations on the map, dimmed, when enough copies agree", "bool", "Receive",
              "Only for stations nobody has decoded cleanly, and never for moving ones. A clean packet from the station replaces the guess"),
        Field("damaged_copies", "Copies that must agree", "number", "Receive",
              "A station that has never been decoded cleanly goes on the map only after this many damaged packets from it agree on where it is",
              1, 10, 1, shows_if="damaged_map"),
        Field("damaged_radius_m", "Same spot means within", "number", "Receive",
              "Also the most a damaged position may differ from where a station is known to be before it is ignored", 20, 5000, 10, unit="m"),
        Field("nomad_url", "N.O.M.A.D. server", "text", "Map", "Project N.O.M.A.D. address; blank for no map", placeholder="http://nomad.local:8080"),
        Field("map_cache", "Cache map tiles on this machine", "bool", "Map"),
        Field("map_cache_mb", "Cache size limit", "number", "Map", lo=50, hi=20000, step=50, unit="MB", shows_if="map_cache"),
        Field("igate_enabled", "Gate what the radio hears to the internet (APRS-IS)", "bool", "iGate",
              "Needs your callsign and the APRS-IS passcode for it. Packets that already came from the internet, or ask not to be gated, are never passed on."),
        Field("igate_server", "Server", "text", "iGate", placeholder="rotate.aprs2.net:14580", shows_if="igate_enabled"),
        Field("igate_passcode", "Passcode", "text", "iGate", "Press Calculate to work it out from your callsign", shows_if="igate_enabled"),
        Field("igate_to_rf", "Also transmit internet messages for stations heard directly here", "bool", "iGate",
              "Uses your radio on its own: only messages, only for stations heard directly in the last 30 minutes, at most 10 a minute.", shows_if="igate_enabled"),
        Field("igate_radius_km", "Message range", "number", "iGate", lo=1, hi=500, step=1, unit="km", shows_if="igate_to_rf"),
        Field("digi_enabled", "Repeat packets that ask for it", "bool", "Digipeater",
              "This transmits on its own whenever a packet asks to be repeated, so it only helps if your radio reaches ground other digipeaters don't."),
        Field("digi_wide", "Wide-area: also answer WIDE2-1 and WIDE2-2", "bool", "Digipeater",
              "Off is a fill-in digipeater, which answers WIDE1-1 and your own callsign.", shows_if="digi_enabled"),
        Field("share_allow_tx", "Let the web page and desktop apps transmit through this radio", "bool", "Access"),
    ]


def _text(v, limit: int = 200) -> str:
    if not isinstance(v, str):
        raise ValueError("must be text")
    v = v.strip()
    if len(v) > limit or not all(32 <= ord(c) < 127 for c in v):
        raise ValueError("plain characters only" if len(v) <= limit else "too long")
    return v


def _num(v, lo: float, hi: float, integer: bool):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("must be a number")
    if not lo <= v <= hi:
        raise ValueError(f"must be between {lo:g} and {hi:g}")
    return int(v) if integer else float(v)


def _check(key: str, v, inputs: list[str], outputs: list[str]):
    """The clean value for one setting, or ValueError saying what is wrong."""
    if key == "callsign":
        v = _text(v).upper()
        if not _CALL.match(v):
            raise ValueError("a callsign like N0CALL or N0CALL-7")
        return v
    if key in ("lat", "lon"):
        return _num(v, *((-90, 90) if key == "lat" else (-180, 180)), False)
    if key == "symbol":
        v = _text(v)
        if len(v) != 2:
            raise ValueError("two characters: the table (/ or \\) then the code")
        return v
    if key == "comment":
        return _text(v, 43)
    if key == "path":
        v = _text(v).upper().replace(" ", "")
        hops = [h for h in v.split(",") if h]
        if len(hops) > 8 or not all(_HOP.match(h) for h in hops):
            raise ValueError("up to 8 comma-separated hops, like WIDE1-1,WIDE2-1")
        return ",".join(hops)
    if key == "beacon_minutes":
        return _num(v, 0, 120, True)
    if key in ("input_device", "output_device"):
        v = _text(v)
        if v and v not in (inputs if key == "input_device" else outputs):
            raise ValueError("no audio device with that name on this machine")
        return v
    if key == "tx_lead_ms":
        return _num(v, 100, 3000, True)
    if key == "tx_level":
        return _num(v, 0.05, 1, False)
    if key == "nomad_url":
        v = _text(v).rstrip("/")
        if v and not re.match(r"^https?://[^\s/]+", v):
            raise ValueError("an address starting with http:// or https://")
        return v
    if key == "damaged_copies":
        return _num(v, 1, 10, True)
    if key == "damaged_radius_m":
        return _num(v, 20, 5000, True)
    if key == "map_cache_mb":
        return _num(v, 50, 20000, True)
    if key == "igate_server":
        v = _text(v)
        if not v or not _HOST.match(v) or (":" in v and not 1 <= int(v.split(":")[1]) <= 65535):
            raise ValueError("host or host:port, like rotate.aprs2.net:14580")
        return v
    if key == "igate_passcode":
        v = _text(v)
        if v and not _PASSCODE.match(v):
            raise ValueError("a number")
        return v
    if key == "igate_radius_km":
        return _num(v, 1, 500, True)
    if key in ("auto_gain", "map_cache", "igate_enabled", "igate_to_rf", "digi_enabled", "digi_wide", "share_allow_tx",
               "damaged_list", "damaged_heard", "damaged_map"):
        if not isinstance(v, bool):
            raise ValueError("must be on or off")
        return v
    raise KeyError(key)


def validate(changes, current, inputs: list[str], outputs: list[str]) -> tuple[dict, dict]:
    """(clean values, {setting: problem}). Only the settings in changes are looked at; whether the
    combination works (a gateway needs a callsign and passcode) is checked against current plus changes."""
    clean: dict = {}
    errors: dict = {}
    if not isinstance(changes, dict):
        return {}, {"": "expected a set of settings"}
    for key, v in changes.items():
        try:
            clean[key] = _check(key, v, inputs, outputs)
        except KeyError:
            errors[key] = "unknown setting"
        except ValueError as e:
            errors[key] = str(e)
    merged = {**{f.key: getattr(current, f.key) for f in fields([], [], "")}, **clean}
    if not errors:
        if not merged["callsign"] and (merged["igate_enabled"] or merged["digi_enabled"]):
            errors["callsign"] = "needed before the iGate or digipeater can run"
        if merged["igate_enabled"] and not merged["igate_passcode"]:
            errors["igate_passcode"] = "needed to gate to the internet (press Calculate)"
    return clean, errors
