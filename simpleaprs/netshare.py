"""Share the radio over HTTP. (The server itself, the event streams and the Host/Origin checks are piwebkit's.)

GET  /api/events  Server-Sent Events: a hello (who this station is), a snapshot of the stations the
                  server remembers, a replay of the last month of traffic, a {"sync": true} marker,
                  then everything heard or sent from then on, and {"level": 0..1} every so often
                  while audio comes in (the radio's RX level, for a meter; not part of the replay).
POST /api/tx      Ask the server to beacon, or send a message or bulletin, through its radio (token always required).

Clients get frames, not decoded packets, so they run the same code on them as the radio would. A client is
replayed the last month of traffic; a packet older than that is no longer something it can look back on.

A server can also carry a web page (see webui.py): GET /web/events is the same kind of stream but of
decoded events, and the page itself and its map files are served from the same port. The page is guarded
by a login (a session cookie, see piwebkit/auth.py) while desktop apps keep using the token on /api/*. Either way,
requests to such a server must look like they came from a page on it (right Host, same Origin, JSON body)
so a web page elsewhere can't make your browser use it. The older "open access" mode skips the login and the
token both, for a LAN.
"""
from __future__ import annotations

import hmac
import json
import re

from piwebkit.hub import Hub
from piwebkit.server import WebServer

_CALL = re.compile(r"^[A-Z0-9]{1,6}(-\d{1,2})?$")
_BULLETIN = re.compile(r"^BLN[0-9X]$")  # BLN0-9, and BLNX for urgent ones


def validate_tx(d) -> tuple[dict | None, str]:
    """Check a TX request and reduce it to the few things a client may ask for."""
    if not isinstance(d, dict):
        return None, "bad request"
    kind = d.get("type")
    if kind == "beacon":
        return {"type": "beacon"}, ""
    if kind in ("message", "bulletin"):  # a bulletin is a message to BLN0-BLN9, sent without a number to ack
        to, text = str(d.get("to", "")).strip().upper(), str(d.get("text", "")).strip()
        if not (_BULLETIN if kind == "bulletin" else _CALL).match(to):
            return None, "bad bulletin number" if kind == "bulletin" else "bad callsign"
        if not 1 <= len(text) <= 67:
            return None, f"{kind} must be 1-67 characters"
        if not all(32 <= ord(c) < 127 for c in text) or any(c in text for c in "|~{"):
            return None, f"{kind} has characters APRS does not allow"
        return {"type": kind, "to": to, "text": text}, ""
    return None, "unknown request type"


class ShareServer(WebServer):
    def __init__(self, hub: Hub, port: int, token: str, require_token: bool, allow_tx: bool, hello, on_tx,
                 host: str = "0.0.0.0", snapshot=lambda: {}, web=None, open_access: bool = False,
                 allowed_hosts: tuple[str, ...] = ()) -> None:
        """hello() -> dict describing this station; snapshot() -> the stations it remembers;
        on_tx(request) is called from a server thread.

        web, if given, adds the browser page: web.hub and web.intro() feed /web/events, and
        web.handle(handler) -> bool serves everything else (the page, map files). open_access
        turns the token off (watching and transmitting) and checks where requests come from instead."""
        self.hub, self.token, self.require_token, self.allow_tx = hub, token, require_token, allow_tx
        self.hello, self.on_tx, self.snapshot = hello, on_tx, snapshot
        super().__init__(port, web, host=host, allowed_hosts=allowed_hosts, open_access=open_access,
                         banner="simpleaprs share server\n", extra_get=self._get, extra_post=self._post)

    def _token_ok(self, h) -> bool:
        if self.open_access:
            return True
        auth = h.headers.get("Authorization", "")
        got = auth[7:] if auth.startswith("Bearer ") else ""
        if bool(self.token) and hmac.compare_digest(got.encode(), self.token.encode()):
            return True
        return bool(self.web and self.web.session_ok(h))  # the logged-in web page

    def _get(self, h) -> bool:
        if h.path != "/api/events":
            return False
        if self.require_token and not self._token_ok(h):
            h.reply(401, {"error": "access token required"})
            return True

        def intro() -> list[dict]:
            snap = self.snapshot()
            return [{"hello": {**self.hello(), "allow_tx": self.allow_tx}}] + ([{"snapshot": snap}] if snap else [])
        h.stream(self.hub, intro)
        return True

    def _post(self, h) -> bool:
        if h.path != "/api/tx":
            return False
        if not self._token_ok(h):  # never open because of the view setting; only open access mode turns the token off
            h.reply(401, {"error": "access token required"})
        elif not self.allow_tx:
            h.reply(403, {"error": "transmitting from clients is turned off on this server"})
        else:
            n = int(h.headers.get("Content-Length", "0"))
            if not 0 < n <= 2048:
                h.reply(400, {"error": "bad request"})
                return True
            req, err = validate_tx(json.loads(h.rfile.read(n)))
            if req is None:
                h.reply(400, {"error": err})
            else:
                self.on_tx(req)
                h.reply(200, {"ok": True})
        return True
