"""The browser page: what ShareServer needs to serve it (see netshare.py's `web` argument).

With an Auth, the page and everything behind it needs a login (a session cookie): the first visitor makes the
account, after that /login asks for it. Without one the page is open, as it used to be. `app` is the server
the page configures: it supplies settings_view(), settings_apply(changes) and the rest (see headless.Server).
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from . import igate
from .auth import Auth
from .mapserver import WEB, MapProxy
from .netshare import Hub
from .tracker import Tracker

COOKIE = "simpleaprs_session"


class WebUI:
    def __init__(self, hub: Hub, tracker: Tracker, proxy: MapProxy, allow_tx: bool,
                 auth: Auth | None = None, app=None) -> None:
        self.hub, self.tracker, self.proxy, self.allow_tx = hub, tracker, proxy, allow_tx
        self.auth, self.app = auth, app

    @property
    def protected(self) -> bool:
        return self.auth is not None

    def session_ok(self, h) -> bool:
        return self.auth is None or self.auth.valid(h.cookie(COOKIE))

    def intro(self) -> list[dict]:
        return self.tracker.intro(self.allow_tx) + ([self.app.status_event()] if self.app else [])

    # --- GET ----------------------------------------------------------------------------------------

    def handle(self, h) -> bool:
        """Serve the page and its map files. The map style has to point back at whatever address the
        browser used to reach us, so it comes from the request."""
        url = urlparse(h.path)
        path = url.path
        if self.auth:
            if path == "/login":
                self.proxy._send(h, 200, (WEB / "login.html").read_bytes(), "text/html")
                return True
            if path == "/api/auth/state":
                h._reply(200, {"setup": not self.auth.configured, "logged_in": self.session_ok(h)})
                return True
            if not self.session_ok(h):
                if path == "/" or path.endswith(".html"):
                    h.send_response(302)
                    h.send_header("Location", "/login")
                    h.send_header("Content-Length", "0")
                    h.end_headers()
                else:
                    h._reply(401, {"error": "login required"})
                return True
        if self.app and path == "/api/settings":
            h._reply(200, self.app.settings_view())
        elif self.auth and path == "/api/account":
            h._reply(200, {"username": self.auth.username, "api_token": self.auth.api_token})
        elif path == "/api/passcode":
            call = parse_qs(url.query).get("call", [""])[0].strip()
            h._reply(200, {"passcode": str(igate.passcode(call)) if call else ""})
        else:
            return self.proxy.handle(h, f"http://{h.headers.get('Host', '')}", index="app.html")
        return True

    # --- POST ---------------------------------------------------------------------------------------

    def handle_post(self, h) -> bool:
        if h.path == "/api/forget-inactive":  # the map's button: forget the stations not heard since the server started
            if self.auth and not self.session_ok(h):
                h._reply(401, {"error": "login required"})
            elif not self.auth and not self.allow_tx:
                h._reply(403, {"error": "turned off on this server"})
            else:
                h._reply(200, {"ok": True, "removed": len(self.tracker.forget_inactive())})
            return True
        if not self.auth:
            return False
        public = {"/api/setup": self._setup, "/api/login": self._login}
        private = {"/api/logout": self._logout, "/api/settings": self._settings, "/api/account/password": self._password,
                   "/api/account/token": self._new_token, "/api/account/logout-all": self._logout_all}
        if h.path in public:
            public[h.path](h)
        elif h.path in private:
            if self.session_ok(h):
                private[h.path](h)
            else:
                h._reply(401, {"error": "login required"})
        else:
            return False
        return True

    def _start(self, h, remember: bool) -> None:
        token, max_age = self.auth.start_session(remember)
        cookie = f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict" + (f"; Max-Age={max_age}" if max_age else "")
        h._reply(200, {"ok": True}, headers=(("Set-Cookie", cookie),))

    def _setup(self, h) -> None:
        d = h.read_json()
        if not isinstance(d, dict):
            return
        if self.auth.configured:
            return h._reply(409, {"error": "A login has already been set up."})
        problem = self.auth.create(str(d.get("username", "")), str(d.get("password", "")))
        if problem:
            return h._reply(409 if self.auth.configured else 400, {"error": problem})  # configured now: someone else got there first
        if self.app:
            self.app.api_token_changed(self.auth.api_token)
            self.app._publish_status()
        self._start(h, bool(d.get("remember")))

    def _login(self, h) -> None:
        d = h.read_json()
        if not isinstance(d, dict):
            return
        ok, wait = self.auth.check(str(d.get("username", "")), str(d.get("password", "")), h.client_address[0])
        if ok:
            return self._start(h, bool(d.get("remember")))
        h._reply(429 if wait else 401, {"error": f"Too many wrong passwords. Try again in {wait} seconds." if wait
                                        else "Wrong username or password."})

    def _logout(self, h) -> None:
        self.auth.end(h.cookie(COOKIE))
        h._reply(200, {"ok": True}, headers=(("Set-Cookie", f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"),))

    def _logout_all(self, h) -> None:
        self.auth.end_all()
        h._reply(200, {"ok": True}, headers=(("Set-Cookie", f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"),))

    def _password(self, h) -> None:
        d = h.read_json()
        if not isinstance(d, dict):
            return
        problem = self.auth.change_password(str(d.get("current", "")), str(d.get("new", "")), keep=h.cookie(COOKIE))
        h._reply(400 if problem else 200, {"error": problem} if problem else {"ok": True})

    def _new_token(self, h) -> None:
        token = self.auth.new_api_token()
        if self.app:
            self.app.api_token_changed(token)
        h._reply(200, {"api_token": token})

    def _settings(self, h) -> None:
        d = h.read_json(16384)
        if not isinstance(d, dict) or not self.app:
            return
        errors = self.app.settings_apply(d.get("changes"))
        h._reply(400 if errors else 200, {"errors": errors} if errors else {"ok": True, "status": self.app.status()})
