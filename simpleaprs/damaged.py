"""Deciding how far a packet that failed its checksum can be trusted.

A damaged packet is never treated as real on its own. These checks look for the damage it can't hide: a field that is no
longer well-formed, a callsign nobody has decoded cleanly, a position that disagrees with where the station is known to be.
The tracker uses them to decide what a damaged packet may do (see Tracker.on_damaged).
"""
from __future__ import annotations

import math
import re

ALIAS = re.compile(r"^(WIDE|TRACE|RELAY|ECHO|GATE|TCPIP|qA|CQ)")  # path words that are not a station's callsign
TAIL = 12  # bytes at the end of a packet where the damage usually is; a fault before that is damage in the middle

_TOKEN = re.compile(r"[cgtrpPhblLsF#][\d.\- ]{2,5}")
_SOFTWARE = re.compile(r"[A-Za-z0-9 _-]{0,10}$")  # what follows the weather fields: a short software tag
_POSITION = re.compile(r"[!=/@](\d{6}[zh/])?(\d{4}\.\d{2}[NS])(.)(\d{5}\.\d{2}[EW])(.)")
_COMPRESSED = re.compile(r"[!=/@](\d{6}[zh/])?[/\\A-Za-z][\x20-\x7b]{12}")
_WIND = re.compile(r"(\d{3}|\.{3}| {3})/(\d{3}|\.{3}| {3})|c\d{3}s\d{3}")


def printable(info: bytes) -> str:
    """The text of a packet with anything that is not printable shown as a dot and the trailing junk cut off."""
    return "".join(chr(b) if 32 <= b < 127 else "." for b in info).rstrip(". ")


def broken_at(text: str) -> int | None:
    """Where the shown text stops being well-formed APRS (a position, a weather report), or None if it never does.
    Free text, formats this doesn't check, and anything it can't tell are None: this only reports faults it can see."""
    if not text or text.startswith("!!"):  # "!!" is a weather logger's own format
        return None
    kind = text[0]
    if kind in "!=/@":
        if _COMPRESSED.match(text):
            return None
        m = _POSITION.match(text)
        if not m:
            return 0
        if m.group(5) != "_":  # a plain position: what follows is free text
            return None
        pos = m.end()
        wind = _WIND.match(text, pos)
        if not wind:  # PHG/RNG data or free text on a weather symbol
            return None
        pos = wind.end()
    elif kind == "_":  # weather with no position: _MMDDHHMM then the fields
        if not re.match(r"_\d{8}", text):
            return 0
        pos = 9
        if wind := _WIND.match(text, pos):
            pos = wind.end()
    else:
        return None
    while m := _TOKEN.match(text, pos):
        pos = m.end()
    return None if _SOFTWARE.match(text, pos) else pos


def metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot((a[0] - b[0]) * 111000, (a[1] - b[1]) * 111000 * math.cos(math.radians(a[0])))
