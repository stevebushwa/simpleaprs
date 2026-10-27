"""Fans events out to connected pages and keeps a replay buffer for new ones."""
from __future__ import annotations

import queue
import threading
from collections import deque

BACKLOG = 3000  # events replayed to a new client


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
            for q in list(self._subs):
                try:
                    q.put_nowait(ev)
                except queue.Full:  # a client that stopped reading: drop it rather than grow forever
                    self._subs.remove(q)

    def subscribe(self) -> tuple[queue.Queue, list[dict]]:
        q: queue.Queue = queue.Queue(maxsize=2000)
        with self._lock:
            self._subs.append(q)
            return q, list(self._log)

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
