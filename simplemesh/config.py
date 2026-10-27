"""Settings, stored as JSON in ~/.config/simplemesh/config.json."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

DIR = Path.home() / ".config" / "simplemesh"
PATH = DIR / "config.json"
AUTH_PATH = DIR / "auth.json"
TILES_DIR = Path.home() / ".cache" / "simplemesh" / "tiles"
NODES_PATH = DIR / "nodes.json"
MESSAGES_PATH = DIR / "messages.json"


@dataclass
class Config:
    serial_port: str = ""    # empty = the first USB serial device found
    baud: int = 115200
    web_port: int = 8074
    nomad_url: str = "http://localhost:8080"  # Project N.O.M.A.D. map server (its usual port); blank = no map
    map_cache: bool = True   # keep map tiles on disk so they load fast and work when the map server is away
    map_cache_mb: int = 500
    home_lat: float = 0.0    # where the map starts when nothing on it has a position; 0, 0 = unset
    home_lon: float = 0.0

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

    @property
    def home(self) -> tuple[float, float] | None:
        return (self.home_lat, self.home_lon) if (self.home_lat or self.home_lon) else None
