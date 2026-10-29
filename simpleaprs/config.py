"""Settings, stored as JSON in ~/.config/simpleaprs/config.json."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

PATH = Path.home() / ".config" / "simpleaprs" / "config.json"
STATIONS_PATH = PATH.with_name("stations.json")
AUTH_PATH = PATH.with_name("auth.json")  # the web page login (headless server)
TLS_DIR = PATH.with_name("tls")  # the certificate the web page is served with over https
WEB_EVENTS_PATH = PATH.with_name("web-events.jsonl")  # what the web page can look back through, kept across restarts
FRAMES_PATH = PATH.with_name("frames.jsonl")  # the same traffic as raw frames, for desktop apps that follow this server
STATIONS_REMOTE_PATH = PATH.with_name("stations-remote.json")  # kept apart so a server's stations never mix with local ones


@dataclass
class Config:
    callsign: str = ""
    lat: float = 0.0
    lon: float = 0.0
    symbol: str = "/>"
    comment: str = "simpleaprs"
    path: str = "WIDE1-1,WIDE2-1"
    input_device: str = ""   # empty = system default
    output_device: str = ""
    tx_lead_ms: int = 500    # VOX lead-in: extra preamble so VOX is open before data starts
    tx_level: float = 0.5
    auto_gain: bool = False  # keep the sound card's input volume where the radio's audio neither clips nor is weak
    beacon_minutes: int = 0  # 0 = manual only
    alert_device: str = ""   # empty = system default; keep this off the radio's sound card
    alert_message: bool = True
    alert_new_station: bool = False
    alert_sent: bool = False
    alerts_muted: bool = False
    nomad_url: str = ""  # Project N.O.M.A.D. map server, e.g. http://nomad.local:8080; blank = no map
    map_cache: bool = True  # keep map tiles on disk so they load fast and work when the map server is away
    map_cache_mb: int = 500
    map_filter: str = "all"  # all | weather | igate | digi | mobile
    msg_filter: str = "all"  # which messages the Messages tab lists: all | direct | bulletin
    mode: str = "local"  # local: use a radio on this computer; remote: follow another simpleaprs
    remote_url: str = ""
    remote_token: str = ""
    share_enabled: bool = False  # serve this radio to other simpleaprs instances
    share_port: int = 8073
    share_token: str = ""
    share_require_token: bool = True  # off lets anyone on the network watch; transmitting always needs the token
    share_allow_tx: bool = True
    igate_enabled: bool = False  # pass what the radio hears to APRS-IS (needs a local radio)
    igate_server: str = "rotate.aprs2.net:14580"
    igate_passcode: str = ""  # the APRS-IS passcode for the callsign
    igate_to_rf: bool = False  # also transmit internet messages for stations heard directly on RF
    igate_radius_km: int = 50  # how far from us the server looks for those messages
    digi_enabled: bool = False  # repeat packets that ask for it (WIDE1-1, WIDE2-N, or our callsign)
    digi_wide: bool = False  # a wide-area digipeater (WIDE1-1 and WIDE2-N) rather than a fill-in (WIDE1-1 only)
    damaged_mode: str = "list"  # what a packet that arrives with a bad checksum may do: off | list | heard | map
    damaged_copies: int = 3  # damaged copies that must agree before a station nobody has decoded cleanly goes on the map
    damaged_radius_m: int = 150  # "the same spot" for those copies, and the most a damaged position may differ from a known one

    @classmethod
    def load(cls) -> "Config":
        try:
            raw = json.loads(PATH.read_text())
            names = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in raw.items() if k in names})
        except (OSError, ValueError):
            return cls()

    def save(self) -> None:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(asdict(self), indent=2))

    @property
    def configured(self) -> bool:
        return bool(self.callsign)
