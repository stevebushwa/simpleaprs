"""Settings, stored as JSON in ~/.config/simplemesh/config.json."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

DIR = Path.home() / ".config" / "simplemesh"
PATH = DIR / "config.json"
AUTH_PATH = DIR / "auth.json"
NODES_PATH = DIR / "nodes.json"
MESSAGES_PATH = DIR / "messages.json"


@dataclass
class Config:
    serial_port: str = ""    # empty = the first USB serial device found
    baud: int = 115200
    web_port: int = 8074
    nomad_url: str = ""      # Project N.O.M.A.D. map server; blank = whatever simpleaprs on this machine uses
    map_cache: bool = True   # the tile cache is shared with simpleaprs, so both work offline on the same tiles
    map_cache_mb: int = 500

    @classmethod
    def load(cls) -> "Config":
        try:
            raw = json.loads(PATH.read_text())
            names = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in raw.items() if k in names})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        os.replace(tmp, PATH)


def simpleaprs_settings() -> dict:
    """What simpleaprs on this machine is set to, if anything: its map server and where the station is."""
    try:
        raw = json.loads((Path.home() / ".config" / "simpleaprs" / "config.json").read_text())
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def map_server(cfg: Config) -> str:
    return cfg.nomad_url or str(simpleaprs_settings().get("nomad_url") or "")


def home_position() -> tuple[float, float] | None:
    s = simpleaprs_settings()
    try:
        lat, lon = float(s.get("lat", 0)), float(s.get("lon", 0))
    except (TypeError, ValueError):
        return None
    return (lat, lon) if (lat or lon) else None
