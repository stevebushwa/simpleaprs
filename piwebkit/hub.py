"""Fans events out to connected pages and keeps a replay buffer for new ones.

A kept event with a time is traffic a page can look back through, so the buffer goes back by date,
not by count: KEEP is how long each kind goes on being sent to a new page (a month, and no longer).
A message is not traffic to age out: there are few of them, so they are kept for good. An event
with no time (who this station is, the RX level) is what the page sees now, not traffic, and goes
out to every new client.

A hub can also keep its replay on disk (load), so a restart doesn't empty it. It is an append-only
file of one JSON event per line, written SAVE_EVERY events at a time (a message, which is rare and
worth more, is written at once) and by flush() when the server stops or on a timer. Once the file
passes a size, it is rewritten from what the hub still holds, which drops what has aged out.
"""
from __future__ import annotations

import json
import math
import os
import queue
import threading
import time
from pathlib import Path

MONTH = 30 * 86400  # how long a packet stays in the replay for a new client
KEEP = {None: MONTH, "packet": MONTH}  # a raw frame (no kind) is a packet a desktop client follows too
FOREVER = {"message"}  # kinds that stay in the replay for good
SAVE_EVERY = 10  # events held back before they are appended to the file
MAX_FILE = 2 * 1024 * 1024  # bytes the file may grow to before it is rewritten smaller (more, if what it holds is already half that)
SWEEP = 60  # seconds between looks for what has aged out, so a month of packets isn't gone through for every new one
RETRY_MAX = 1000  # events kept to try writing again after a failed write


class Hub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._log: list[dict] = []
        self._subs: list[queue.Queue] = []
        self._path: Path | None = None
        self._pending: list[dict] = []  # kept events the file doesn't have yet
        self._size, self._limit = 0, MAX_FILE
        self._swept = 0.0

    def publish(self, ev: dict, keep: bool = True) -> None:
        """Send ev to everyone connected. keep=False leaves it out of the replay for new clients."""
        with self._lock:
            if keep:
                self._log.append(ev)
            self._expire()
            if keep and self._path:
                self._pending.append(ev)
                if len(self._pending) >= SAVE_EVERY or ev.get("type") in FOREVER:
                    self._save()
            for q in list(self._subs):
                try:
                    q.put_nowait(ev)
                except queue.Full:  # a client that stopped reading: drop it rather than grow forever
                    self._subs.remove(q)

    def subscribe(self, since: float | None = None) -> tuple[queue.Queue, list[dict]]:
        """A queue of what comes next, and what happened before. since: only traffic from that time on (a page asks
        for the rest with older()); messages are always all of them."""
        q: queue.Queue = queue.Queue(maxsize=2000)
        with self._lock:
            self._expire(force=True)
            self._subs.append(q)
            if since is None:
                return q, list(self._log)
            return q, [ev for ev in self._log if ev.get("type") in FOREVER or "t" not in ev or ev["t"] >= since]

    def older(self, before: float, limit: int) -> tuple[list[dict], bool]:
        """Up to limit traffic events from before that time (oldest first), and whether there are still older ones."""
        with self._lock:
            found: list[dict] = []
            for ev in reversed(self._log):
                if ev.get("type") in FOREVER or "t" not in ev or ev["t"] >= before:
                    continue
                if len(found) == limit:
                    return found[::-1], True
                found.append(ev)
            return found[::-1], False

    def _expire(self, force: bool = False) -> None:
        """Forget traffic older than its KEEP: a new page sees none older than a month. Messages stay."""
        now = time.time()
        if not force and now - self._swept < SWEEP:
            return
        self._swept = now
        self._log = [ev for ev in self._log if "t" not in ev or now - ev["t"] <= KEEP.get(ev.get("type"), math.inf)]

    def load(self, path: Path) -> None:
        """Start keeping the replay in path, and pick up what an earlier run left there."""
        events: list[dict] = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except ValueError:  # a line cut short by a crash
                        continue
                    if isinstance(ev, dict):
                        events.append(ev)
        except OSError:
            pass
        with self._lock:
            self._path = Path(path)
            self._log = events + self._log
            self._expire(force=True)
            self._pending = []
            self._rewrite()  # starts the file clean: no aged-out events, no torn last line

    def flush(self) -> None:
        """Write what is waiting for the file now (the server is stopping, or a timer says it has waited long enough)."""
        with self._lock:
            if self._pending:
                self._save()

    def _save(self) -> None:
        assert self._path
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = "".join(json.dumps(ev, separators=(",", ":")) + "\n" for ev in self._pending).encode()
            fd = os.open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)  # readable by us alone
            with os.fdopen(fd, "ab") as f:
                f.write(data)
        except OSError:  # try again with the next batch, but don't let a full disk grow this without end
            del self._pending[:-RETRY_MAX]
            return
        self._size += len(data)
        self._pending = []
        if self._size > self._limit:
            self._rewrite()

    def _rewrite(self) -> None:
        """Replace the file with what the hub holds now."""
        assert self._path
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = "".join(json.dumps(ev, separators=(",", ":")) + "\n" for ev in self._log).encode()
            tmp = self._path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, self._path)
        except OSError:
            return
        self._pending = []
        self._size, self._limit = len(data), max(MAX_FILE, 2 * len(data))  # a lot of messages must not mean a rewrite every time

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def subscribed(self, q: queue.Queue) -> bool:
        with self._lock:
            return q in self._subs

    def count(self) -> int:
        with self._lock:
            return len(self._subs)
