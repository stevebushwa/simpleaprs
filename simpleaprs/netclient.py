"""Client side of netshare: follow another simpleaprs instance's feed instead of a local radio."""
from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, Signal

# A server's https certificate is one it made for itself, so there is nothing to check it against: the connection is private
# from anyone listening in, but it is no proof who is at the other end (neither was plain http).
_ANY_CERT = ssl.create_default_context()
_ANY_CERT.check_hostname = False
_ANY_CERT.verify_mode = ssl.CERT_NONE


def normalize(url: str) -> str:
    """The address as typed, with a scheme: https if the server answers to it, else http (a server that is older, or run with --http)."""
    url = url.strip().rstrip("/")
    if "://" in url:
        return url
    try:
        urllib.request.urlopen("https://" + url + "/", timeout=5, context=_ANY_CERT).close()
    except urllib.error.HTTPError:  # it answered, whatever it said
        return "https://" + url
    except (OSError, ValueError):
        return "http://" + url
    return "https://" + url


class RemoteClient(QObject):
    event = Signal(dict)    # one decoded feed event
    state = Signal(str)     # human-readable connection state
    tx_result = Signal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self._gen = 0  # bumped on every start/stop so a superseded connection thread quits quietly
        self._url = self._token = ""

    def start(self, url: str, token: str) -> None:
        self._gen += 1
        self._url, self._token = url.strip().rstrip("/"), token
        threading.Thread(target=self._run, args=(self._gen, self._url, token), daemon=True).start()

    def stop(self) -> None:
        self._gen += 1

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _run(self, gen: int, url: str, token: str) -> None:
        wait = 2
        while gen == self._gen:
            self.state.emit(f"Connecting to {url}...")
            url = normalize(url)  # here and not in start(): finding out whether it speaks https can take a few seconds
            if gen == self._gen:
                self._url = url
            try:
                req = urllib.request.Request(url + "/api/events", headers=self._headers(token))
                with urllib.request.urlopen(req, timeout=40, context=_ANY_CERT) as r:  # the server sends a keepalive every 15 s
                    wait = 2
                    for line in r:
                        if gen != self._gen:
                            return
                        line = line.decode("utf-8", "replace").rstrip("\r\n")
                        if line.startswith("data: "):
                            self.event.emit(json.loads(line[6:]))
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    self.state.emit("Server refused the access token - check Settings")
                    return
                self.state.emit(f"Server error {e.code}; retrying")
            except (OSError, ValueError) as e:
                if gen == self._gen:
                    self.state.emit(f"Disconnected ({e}); retrying")
            for _ in range(wait * 10):  # sleep in slices so stop() takes effect promptly
                if gen != self._gen:
                    return
                time.sleep(0.1)
            wait = min(wait * 2, 30)

    def send(self, payload: dict) -> None:
        """Ask the server to transmit. The result arrives on tx_result."""
        url, token = self._url, self._token

        def post() -> None:
            req = urllib.request.Request(normalize(url) + "/api/tx", data=json.dumps(payload).encode(), method="POST",
                                         headers={**self._headers(token), "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10, context=_ANY_CERT):
                    self.tx_result.emit(True, "sent")
            except urllib.error.HTTPError as e:
                try:
                    msg = json.loads(e.read()).get("error", f"error {e.code}")
                except ValueError:
                    msg = f"error {e.code}"
                self.tx_result.emit(False, msg)
            except OSError as e:
                self.tx_result.emit(False, f"could not reach the server ({e})")

        threading.Thread(target=post, daemon=True).start()
