"""The browser page: what ShareServer needs to serve it (see netshare.py's `web` argument)."""
from __future__ import annotations

from .mapserver import MapProxy
from .netshare import Hub
from .tracker import Tracker


class WebUI:
    def __init__(self, hub: Hub, tracker: Tracker, proxy: MapProxy, allow_tx: bool) -> None:
        self.hub, self.tracker, self.proxy, self.allow_tx = hub, tracker, proxy, allow_tx

    def intro(self) -> list[dict]:
        return self.tracker.intro(self.allow_tx)

    def handle(self, h) -> bool:
        """Serve the page and its map files. The map style has to point back at whatever address the
        browser used to reach us, so it comes from the request."""
        return self.proxy.handle(h, f"http://{h.headers.get('Host', '')}", index="app.html")
