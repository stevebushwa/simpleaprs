"""A digipeater: repeat packets that ask for it, so stations out of each other's range can still be heard.

This is the "new-N paradigm" most APRS digipeaters follow. A packet's path lists who should repeat it; a
digipeater acts on the first entry that has not been used yet:
  - its own callsign: used up, packet repeated;
  - WIDE1-1 (every digipeater answers this one) or WIDE2-N (only a wide-area digipeater): one hop is
    spent and our callsign is put in front, so the path shows who repeated it.
Anything else is left alone. The same packet is never repeated twice within DUPE_S.

Pure logic with no radio in it: digipeat() returns the frame to send, and the caller transmits it.
"""
from __future__ import annotations

import time

from .ax25 import Address, Frame

DUPE_S = 30
MAX_PATH = 8  # AX.25 allows at most eight digipeaters in a path


class Digipeater:
    def __init__(self, call: str, wide: bool) -> None:
        """wide: also answer WIDE2-N (a wide-area digipeater); otherwise only WIDE1-1 (a fill-in)."""
        self.me = Address.parse(call)
        self.wide = wide
        self._seen: dict[tuple, float] = {}

    def digipeat(self, frame: Frame, now: float | None = None) -> Frame | None:
        """The frame to transmit in answer to one we heard, or None if it isn't ours to repeat."""
        now = time.monotonic() if now is None else now
        if (frame.src.call, frame.src.ssid) == (self.me.call, self.me.ssid):
            return None
        hop = next((i for i, a in enumerate(frame.path) if not a.repeated), None)
        if hop is None:  # nothing left to do
            return None
        a = frame.path[hop]
        mine = Address(self.me.call, self.me.ssid, True)
        if (a.call, a.ssid) == (self.me.call, self.me.ssid):  # asked for us by name
            new = [*frame.path[:hop], mine, *frame.path[hop + 1:]]
        elif (a.call == "WIDE1" and a.ssid == 1) or (self.wide and a.call == "WIDE2" and a.ssid in (1, 2)):
            left = a.ssid - 1
            new = [*frame.path[:hop], mine, *([Address(a.call, left)] if left else []), *frame.path[hop + 1:]]
        else:
            return None
        if len(new) > MAX_PATH:
            return None
        key = (str(frame.src), str(frame.dst), frame.info)
        if now - self._seen.get(key, -DUPE_S) < DUPE_S:
            return None
        self._seen = {k: t for k, t in self._seen.items() if now - t < DUPE_S}
        self._seen[key] = now
        return Frame(frame.src, frame.dst, new, frame.info)
