"""The browser page: what ShareServer needs to serve it (see netshare.py's `web` argument).

The login, logout and password endpoints are piwebkit's (see webbase.py); this adds what is about APRS. `app` is the
server the page configures: it supplies settings_view(), settings_apply(changes) and the rest (see headless.Server).
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from piwebkit.auth import Auth
from piwebkit.hub import Hub
from piwebkit.webbase import BaseWebUI

from . import igate
from .mapserver import MapProxy
from .tracker import Tracker

COOKIE = "simpleaprs_session"


class WebUI(BaseWebUI):
    cookie_name = COOKIE  # another server on the same address (simplemesh) needs its own, or each login would end the other's
    app_name = "simpleaprs"
    index = "app.html"

    def __init__(self, hub: Hub, tracker: Tracker, proxy: MapProxy, allow_tx: bool,
                 auth: Auth | None = None, app=None) -> None:
        super().__init__(hub, proxy, auth)
        self.tracker, self.allow_tx, self.app = tracker, allow_tx, app
        self.private_posts = {"/api/settings": self._settings, "/api/account/token": self._new_token}

    def intro(self) -> list[dict]:
        return self.tracker.intro(self.allow_tx) + ([self.app.status_event()] if self.app else [])

    def get(self, h, path: str) -> bool:
        if self.app and path == "/api/settings":
            h.reply(200, self.app.settings_view())
        elif path == "/api/passcode":
            call = parse_qs(urlparse(h.path).query).get("call", [""])[0].strip()
            h.reply(200, {"passcode": str(igate.passcode(call)) if call else ""})
        else:
            return False
        return True

    def account_view(self) -> dict:
        return {"username": self.auth.username, "api_token": self.auth.api_token}

    def after_setup(self) -> None:
        if self.app:
            self.app.api_token_changed(self.auth.api_token)
            self.app._publish_status()

    def post(self, h) -> bool:
        if h.path != "/api/forget-inactive":  # the map's button: forget the stations not heard since the server started
            return False
        if self.auth and not self.session_ok(h):
            h.reply(401, {"error": "login required"})
        elif not self.auth and not self.allow_tx:
            h.reply(403, {"error": "turned off on this server"})
        else:
            h.reply(200, {"ok": True, "removed": len(self.tracker.forget_inactive())})
        return True

    def _new_token(self, h) -> None:
        token = self.auth.new_api_token()
        if self.app:
            self.app.api_token_changed(token)
        h.reply(200, {"api_token": token})

    def _settings(self, h) -> None:
        d = h.read_json(16384)
        if not isinstance(d, dict) or not self.app:
            return
        errors = self.app.settings_apply(d.get("changes"))
        h.reply(400 if errors else 200, {"errors": errors} if errors else {"ok": True, "status": self.app.status()})
