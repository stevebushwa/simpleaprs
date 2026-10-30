"""The page's side of a server, minus whatever the app is about.

With an Auth, the page and everything behind it needs a login (a session cookie): the first visitor makes the account,
after that /login asks for it. Without one the page is open. An app subclasses BaseWebUI and fills in:

  intro()           the events a page needs to draw itself from nothing
  replay            how far back (seconds) the traffic a page is sent on connecting goes; hub.older() serves the rest
  get(h, path)      its own GET routes, after the login check (True if it answered)
  post(h)           its own POST routes that decide for themselves about login (True if it answered)
  private_posts     {path: handler(h)} for POST routes that need a login (json_route() makes a handler)
  account_view()    what /api/account says
  after_setup()     called once the first visitor has made the account
"""
from __future__ import annotations

from typing import Callable
from urllib.parse import urlparse

from .auth import Auth
from .hub import Hub
from .maps import WEB, MapProxy


ICON_FILES = ("/icon.svg", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png", "/manifest.webmanifest", "/sw.js")  # the login page shows them too


class BaseWebUI:
    cookie_name = "session"  # servers on one address need names of their own, or each login would end the others'
    app_name = "app"         # shown on the login page
    index = "index.html"     # the page itself, served at /
    replay: float | None = None  # seconds of traffic a page is sent when it connects (None: all); the rest is fetched on demand

    def __init__(self, hub: Hub, proxy: MapProxy, auth: Auth | None = None) -> None:
        self.hub, self.proxy, self.auth = hub, proxy, auth
        self.private_posts: dict[str, Callable] = {}

    @property
    def protected(self) -> bool:
        return self.auth is not None

    def session_ok(self, h) -> bool:
        return self.auth is None or self.auth.valid(h.cookie(self.cookie_name))

    # --- for the app to fill in ---------------------------------------------------------------------

    def intro(self) -> list[dict]:
        raise NotImplementedError

    def get(self, h, path: str) -> bool:
        return False

    def post(self, h) -> bool:
        return False

    def account_view(self) -> dict:
        return {"username": self.auth.username}

    def after_setup(self) -> None:
        pass

    def json_route(self, fn: Callable[[dict], dict | None], limit: int = 4096, refuse: tuple[type, ...] = ()) -> Callable:
        """A POST handler for fn(body) -> reply: 200 with the reply (or {"ok": true}), 409 with the reason if fn
        raises one of `refuse`, 400 if the body is wrong."""
        def handler(h) -> None:
            d = h.read_json(limit)
            if not isinstance(d, dict):
                return
            try:
                h.reply(200, fn(d) or {"ok": True})
            except refuse as e:
                h.reply(409, {"error": str(e)})
            except (KeyError, TypeError, ValueError):
                h.reply(400, {"error": "bad request"})
        return handler

    # --- GET ----------------------------------------------------------------------------------------

    def login_page(self) -> bytes:
        return (WEB / "login.html").read_bytes().replace(b"__APP__", self.app_name.encode())

    def handle(self, h) -> bool:
        """Serve the page and its map files. The map style has to point back at whatever address the
        browser used to reach us, so it comes from the request."""
        path = urlparse(h.path).path
        if path in ICON_FILES and self.proxy.find(path.lstrip("/")):  # browsers fetch these without the session cookie
            return self.proxy.handle(h, "", index=self.index)
        if self.auth:
            if path == "/login":
                self.proxy.send(h, 200, self.login_page(), "text/html")
                return True
            if path == "/api/auth/state":
                h.reply(200, {"setup": not self.auth.configured, "logged_in": self.session_ok(h)})
                return True
            if not self.session_ok(h):
                if path == "/" or path.endswith(".html"):
                    h.send_response(302)
                    h.send_header("Location", "/login")
                    h.send_header("Content-Length", "0")
                    h.end_headers()
                else:
                    h.reply(401, {"error": "login required"})
                return True
        if self.get(h, path):
            return True
        if self.auth and path == "/api/account":
            h.reply(200, self.account_view())
            return True
        return self.proxy.handle(h, f"{'https' if h.secure else 'http'}://{h.headers.get('Host', '')}", index=self.index)

    # --- POST ---------------------------------------------------------------------------------------

    def handle_post(self, h) -> bool:
        if self.post(h):
            return True
        if not self.auth:
            return False
        public = {"/api/setup": self._setup, "/api/login": self._login}
        private = {"/api/logout": self._logout, "/api/account/password": self._password,
                   "/api/account/logout-all": self._logout_all, **self.private_posts}
        if h.path in public:
            public[h.path](h)
        elif h.path in private:
            if self.session_ok(h):
                private[h.path](h)
            else:
                h.reply(401, {"error": "login required"})
        else:
            return False
        return True

    def _start(self, h, remember: bool) -> None:
        token, max_age = self.auth.start_session(remember)
        cookie = f"{self.cookie_name}={token}; Path=/; HttpOnly; SameSite=Strict" + ("; Secure" if h.secure else "") + (f"; Max-Age={max_age}" if max_age else "")
        h.reply(200, {"ok": True}, headers=(("Set-Cookie", cookie),))

    def _end(self, h) -> None:
        h.reply(200, {"ok": True}, headers=(("Set-Cookie", f"{self.cookie_name}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0" + ("; Secure" if h.secure else "")),))

    def _setup(self, h) -> None:
        d = h.read_json()
        if not isinstance(d, dict):
            return
        if self.auth.configured:
            return h.reply(409, {"error": "A login has already been set up."})
        problem = self.auth.create(str(d.get("username", "")), str(d.get("password", "")))
        if problem:
            return h.reply(409 if self.auth.configured else 400, {"error": problem})  # configured now: someone else got there first
        self.after_setup()
        self._start(h, bool(d.get("remember")))

    def _login(self, h) -> None:
        d = h.read_json()
        if not isinstance(d, dict):
            return
        ok, wait = self.auth.check(str(d.get("username", "")), str(d.get("password", "")), h.client_address[0])
        if ok:
            return self._start(h, bool(d.get("remember")))
        h.reply(429 if wait else 401, {"error": f"Too many wrong passwords. Try again in {wait} seconds." if wait
                                       else "Wrong username or password."})

    def _logout(self, h) -> None:
        self.auth.end(h.cookie(self.cookie_name))
        self._end(h)

    def _logout_all(self, h) -> None:
        self.auth.end_all()
        self._end(h)

    def _password(self, h) -> None:
        d = h.read_json()
        if not isinstance(d, dict):
            return
        problem = self.auth.change_password(str(d.get("current", "")), str(d.get("new", "")), keep=h.cookie(self.cookie_name))
        h.reply(400 if problem else 200, {"error": problem} if problem else {"ok": True})
