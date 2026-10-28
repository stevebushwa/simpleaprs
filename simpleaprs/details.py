"""What a station says about itself in a packet, beyond where it is: power, antenna, range, speed, frequency, contact.

APRS has fixed codes for some of it (PHG, DFS, RNG, /A= altitude, a speed and course, Mic-E's own speed and course, a
frequency spec) and people write the rest as they like ("25W", "6dBi", "ant 40ft AGL", "me@example.com"). The codes are
read as the spec says; the free text only for the few forms that are hard to mistake for anything else, and never over
what a code already said. The result is a list of (label, value) rows, in the order a reader wants them.
"""
from __future__ import annotations

import re

from . import aprs

_PHG = re.compile(r"PHG(\d)([\d:;<=>?@A-O])(\d)(\d)")
_DFS = re.compile(r"DFS(\d)(\d)(\d)(\d)")
_RNG = re.compile(r"RNG(\d{4})")
_ALT = re.compile(r"/A=(-\d{5}|\d{6})")
_COURSE = re.compile(r"^(\d{3})/(\d{3})")
_FREQ = re.compile(r"(?<![\d.])(\d{2,3}\.\d{2,5})\s?MHz", re.I)
_TONE = re.compile(r"(?<![A-Za-z\d])(T\d{2,3}|C\d{3}|Toff)(?![A-Za-z\d])")  # CTCSS tone, DCS code
_OFFSET = re.compile(r"(?<![\d.])([+-])(\d{3})(?!\d)")  # in 10 kHz
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://[^\s<>\"']+|www\.[^\s<>\"']+\.[a-z]{2,}[^\s<>\"']*", re.I)
_VOLTS = re.compile(r"(?<![\w.])(\d{1,2}(?:\.\d{1,2})?)\s?[Vv](?![\w])")
_WATTS = re.compile(r"(?<![\w.])(\d{1,4}(?:\.\d)?)\s?(?:W|watts?)(?![A-Za-z\d])", re.I)
_GAIN = re.compile(r"(?<![\w.])(\d{1,2}(?:\.\d)?)\s?dB([id])?(?![A-Za-z\d])", re.I)
_HEIGHT = [  # a height only when it says what it is the height of; "2m" alone is a band
    re.compile(r"(?<![\w.])(\d{1,4})\s?(ft|feet|foot|m|meters?)\s?(?:AGL|HAAT|up|high)\b", re.I),
    re.compile(r"\b(?:ant(?:enna)?|hgt|height|HAAT)\s?(?:height)?\s?[:=]?\s?(\d{1,4})\s?(ft|feet|foot|m|meters?)\b", re.I),
]

_DIRECTION = {0: "omni", 1: "northeast", 2: "east", 3: "southeast", 4: "south", 5: "southwest", 6: "west", 7: "northwest", 8: "north"}


def _feet(n: float) -> str:
    return f"{n:,.0f} ft ({n * 0.3048:,.0f} m)"


def _height_code(c: str) -> int:
    """PHG/DFS height: 10 feet doubled once per step of the code (0 = 10 ft, 1 = 20 ft ...); letters carry on past 9."""
    return 10 * 2 ** (ord(c) - 48)


def _mice_motion(info: str) -> tuple[int, int] | None:
    """A Mic-E packet's speed (knots) and course, which ride in three bytes after the position."""
    if len(info) < 7 or info[0] not in "`'":
        return None
    sp, dc, se = (ord(c) - 28 for c in info[4:7])
    speed, course = sp * 10 + dc // 10, dc % 10 * 100 + se
    speed, course = speed - 800 if speed >= 800 else speed, course - 400 if course >= 400 else course
    return (speed, course) if 0 <= course <= 360 and 0 <= speed < 800 else None


def extract(p: aprs.Packet) -> list[tuple[str, str]]:
    """The rows for a station's details, from its last position packet."""
    text, rows = p.comment, []
    info = p.frame.info.decode("latin-1")

    def add(label: str, value: str) -> None:
        if not any(k == label for k, _ in rows):
            rows.append((label, value))

    if m := _PHG.search(text):
        power, height, gain, direction = m.groups()
        add("Power", f"{int(power) ** 2} W")
        add("Antenna height", _feet(_height_code(height)))
        add("Antenna gain", f"{gain} dB")
        if d := _DIRECTION.get(int(direction)):
            add("Antenna direction", d)
    if m := _DFS.search(text):
        strength, height, gain, direction = m.groups()
        add("Signal strength", f"S{strength}")
        add("Antenna height", _feet(_height_code(height)))
        add("Antenna gain", f"{gain} dB")
        if d := _DIRECTION.get(int(direction)):
            add("Antenna direction", d)
    if m := _RNG.search(text):
        add("Range", f"{int(m.group(1))} mi")

    motion = _mice_motion(info)
    if motion:
        speed, course = motion
        if speed or course:
            add("Speed", f"{speed} kn ({speed * 1.15078:.0f} mph)")
            add("Course", f"{course}°")
    elif not p.weather and (m := _COURSE.match(text)) and (m.group(1), m.group(2)) != ("000", "000") and int(m.group(1)) <= 360:
        speed = int(m.group(2))
        add("Speed", f"{speed} kn ({speed * 1.15078:.0f} mph)")
        add("Course", f"{int(m.group(1))}°")
    if m := _ALT.search(text):
        add("Altitude", _feet(int(m.group(1))))

    if m := _FREQ.search(text):
        add("Frequency", f"{float(m.group(1)):g} MHz")
        after = text[m.end():]
        if t := _TONE.search(after):
            add("Tone", t.group(1))
        if o := _OFFSET.search(after):
            add("Offset", f"{o.group(1)}{int(o.group(2)) / 100:g} MHz")

    # free text: only where a code didn't already say it
    if m := _WATTS.search(text):
        add("Power", f"{float(m.group(1)):g} W")
    if m := _GAIN.search(text):
        add("Antenna gain", f"{float(m.group(1)):g} dB" + {"i": "i", "d": "d"}.get((m.group(2) or "").lower(), ""))
    for pat in _HEIGHT:
        if m := pat.search(text):
            n, unit = int(m.group(1)), m.group(2).lower()
            add("Antenna height", _feet(n if unit.startswith("f") else n / 0.3048))
            break
    if m := _VOLTS.search(text):
        add("Battery", f"{float(m.group(1)):g} V")
    if m := _EMAIL.search(text):
        add("Email", m.group(0))
    if m := _URL.search(text):
        add("Web", m.group(0).rstrip(".,;)"))
    return rows
