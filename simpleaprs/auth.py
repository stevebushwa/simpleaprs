"""The web page's login: one account, passwords hashed with scrypt, sessions kept across restarts.

The first visitor to a server with no account creates it. After that the page needs a session cookie, which a
login hands out; "remember me" makes it last 30 days (renewed whenever it is used), otherwise it is a browser
session cookie that the server also forgets after 12 hours. Only hashes of session tokens are stored.

Also holds the access token that desktop apps use to follow the server, since they can't log in through a page.
Traffic is plain HTTP, so none of this protects against someone who can watch your network: it keeps other
people on it, and other web pages, out.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

SESSION_S = 12 * 3600
REMEMBER_S = 30 * 86400
MIN_PASSWORD = 8
MAX_FAILS = 5          # wrong passwords from one address before it is locked out...
LOCKOUT_S = 30         # ...for this long
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._fails: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}
        self._data: dict = {}
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data, dict):
                self._data = data
        except (OSError, ValueError):
            pass

    # --- storage ------------------------------------------------------------------------------------

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # readable by us alone
        with os.fdopen(fd, "w") as f:
            json.dump(self._data, f)
        os.replace(tmp, self.path)

    @property
    def configured(self) -> bool:
        return bool(self._data.get("username") and self._data.get("hash"))

    @property
    def username(self) -> str:
        return self._data.get("username", "")

    @property
    def api_token(self) -> str:
        """What a desktop app gives as the server token. Created with the account."""
        return self._data.get("api_token", "")

    # --- the account --------------------------------------------------------------------------------

    @staticmethod
    def password_problem(password: str) -> str:
        return "" if len(password) >= MIN_PASSWORD else f"The password needs at least {MIN_PASSWORD} characters."

    def create(self, username: str, password: str) -> str:
        """Make the account. Returns a problem to show, or "" on success. Only works while there is none."""
        username = username.strip()
        if not 1 <= len(username) <= 32 or not username.isprintable():
            return "Pick a username of 1 to 32 characters."
        if (problem := self.password_problem(password)):
            return problem
        with self._lock:
            if self.configured:
                return "A login has already been set up."
            salt = secrets.token_bytes(16)
            self._data = {"username": username, "salt": salt.hex(), "hash": _hash(password, salt).hex(),
                          "api_token": secrets.token_urlsafe(16), "sessions": {}}
            self._save()
        return ""

    def reset(self) -> None:
        """Forget the account (and with it every session): the next visitor to the page sets a new one."""
        with self._lock:
            self._data = {}
            try:
                self.path.unlink()
            except OSError:
                pass

    def _password_ok(self, password: str) -> bool:
        d = self._data
        salt = bytes.fromhex(d["salt"]) if d.get("salt") else b"\0" * 16  # still do the work if there is no account
        good = hmac.compare_digest(_hash(password, salt), bytes.fromhex(d["hash"]) if d.get("hash") else b"\0" * 32)
        return good and self.configured

    def check(self, username: str, password: str, who: str) -> tuple[bool, int]:
        """Is this the login? who is the client's address, for lockouts. Returns (ok, seconds to wait if locked out)."""
        now = time.time()
        with self._lock:
            if (wait := self._locked.get(who, 0) - now) > 0:
                return False, int(wait) + 1
            ok = self._password_ok(password) & hmac.compare_digest(username.strip().encode(), self.username.encode())
            if ok:
                self._fails.pop(who, None)
                return True, 0
            fails = [t for t in self._fails.get(who, []) if now - t < 300] + [now]
            self._fails[who] = fails
            if len(fails) >= MAX_FAILS:
                self._locked[who], self._fails[who] = now + LOCKOUT_S, []
            return False, 0

    def change_password(self, current: str, new: str, keep: str = "") -> str:
        """Returns a problem, or "". Every session except keep (the caller's token) is ended."""
        if (problem := self.password_problem(new)):
            return problem
        with self._lock:
            if not self._password_ok(current):
                return "The current password is wrong."
            salt = secrets.token_bytes(16)
            self._data.update(salt=salt.hex(), hash=_hash(new, salt).hex())
            self._end_all_but(keep)
            self._save()
        return ""

    def new_api_token(self) -> str:
        with self._lock:
            self._data["api_token"] = secrets.token_urlsafe(16)
            self._save()
            return self._data["api_token"]

    def token_ok(self, token: str) -> bool:
        return bool(token) and bool(self.api_token) and hmac.compare_digest(token.encode(), self.api_token.encode())

    # --- sessions -----------------------------------------------------------------------------------

    def start_session(self, remember: bool) -> tuple[str, int | None]:
        """A new session: (cookie value, cookie lifetime in seconds, None for a browser-session cookie)."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            sessions = {k: v for k, v in self._data.setdefault("sessions", {}).items() if v["exp"] > now}
            sessions[_sha(token)] = {"exp": now + (REMEMBER_S if remember else SESSION_S), "remember": remember}
            self._data["sessions"] = sessions
            self._save()
        return token, REMEMBER_S if remember else None

    def valid(self, token: str) -> bool:
        if not token:
            return False
        now = time.time()
        with self._lock:
            s = self._data.get("sessions", {}).get(_sha(token))
            if not s or s["exp"] <= now:
                return False
            if s["remember"] and s["exp"] - now < REMEMBER_S - 86400:  # in use: keep it going, saving at most daily
                s["exp"] = now + REMEMBER_S
                self._save()
            return True

    def end(self, token: str) -> None:
        with self._lock:
            if self._data.get("sessions", {}).pop(_sha(token), None):
                self._save()

    def _end_all_but(self, keep: str) -> None:
        sessions = self._data.get("sessions", {})
        self._data["sessions"] = {k: v for k, v in sessions.items() if keep and k == _sha(keep)}

    def end_all(self) -> None:
        with self._lock:
            self._end_all_but("")
            self._save()
