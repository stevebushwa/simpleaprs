"""Fans events out to connected pages and keeps a replay buffer for new ones.

A kept event with a time is traffic a page can look back through, so the buffer only goes so far
back: a page has no use for a year of packets, and KEEP is how long each kind goes on being sent to
a new one (a month, and no longer). A kind not named in KEEP (a message) is kept as long as it fits;
an event with no time (who this station is, the RX level) is what the page sees now, not traffic,
and goes out to every new client.
"""
from __future__ import annotations

import math
import queue
import threading
import time
from collections import deque

BACKLOG = 3000  # events replayed to a new client
MONTH = 30 * 24 * 86400  # how long a packet stays in the replay for a new client
KEEP = {None: MONTH, "packet": MONTH}  # a raw frame (no kind) is a packet a desktop client follows too


class Hub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._log: deque[dict] = deque(maxlen=BACKLOG)
        self._subs: list[queue.Queue] = []

    def publish(self, ev: dict, keep: bool = True) -> None:
        """Send ev to everyone connected. keep=False leaves it out of the replay for new clients."""
        with self._lock:
            if keep:
                self._log.append(ev)
            self._expire()
            for q in list(self._subs):
                try:
                    q.put_nowait(ev)
                except queue.Full:  # a client that stopped reading: drop it rather than grow forever
                    self._subs.remove(q)

    def subscribe(self) -> tuple[queue.Queue, list[dict]]:
        q: queue.Queue = queue.Queue(maxsize=2000)
        with self._lock:
            self._expire()
            self._subs.append(q)
            return q, list(self._log)

    def _expire(self) -> None:
        """Forget traffic older than its KEEP: a new page sees the last month of it, not the last year."""
        now = time.time()
        self._log = deque((ev for ev in self._log
                           if "t" not in ev or now - ev["t"] <= KEEP.get(ev.get("type"), math.inf)),
                          maxlen=BACKLOG)

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
