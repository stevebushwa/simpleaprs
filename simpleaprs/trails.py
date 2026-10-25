"""Where a station has been: a short list of positions per station, drawn as a green track on the map.

Packets off the air are sometimes wrong (a flipped digit puts a station 1000 km away for one beacon), so a
jump no vehicle could make is not believed until the next packet agrees with it."""
from __future__ import annotations

import math

MIN_MOVE_M = 15       # GPS jitter and a beacon repeated from one spot don't make a track
MAX_POINTS = 300      # per station; the oldest go first
MAX_SPEED_MS = 350    # about 1250 km/h: faster than that between two packets is a bad packet, not a vehicle
AGREE_M = 5000        # two jumps this close together are the same new place
SPIKE_M = 100000      # a point this far from where the station was, and then is again, was a wrong packet (a flipped
                      # degree digit in the latitude is 111 km or 1100 km off; a commute or a short trip is under this)


class Trail(list):
    """Points as [lat, lon, time]; time is missing in what older versions saved."""
    pending: tuple[float, float] | None = None  # a jump we didn't believe yet


def metres(a, b) -> float:
    (la1, lo1), (la2, lo2) = a[:2], b[:2]
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 12742000 * math.asin(math.sqrt(h))


def extend(trail: Trail, lat: float, lon: float, t: float | None = None) -> bool:
    """Add the position unless it is no real move from the last one, or a jump nobody could make
    (until a second packet says the same). True if the trail grew."""
    if trail:
        if t is not None and len(trail[-1]) > 2 and t <= trail[-1][2]:
            return False  # older than what we have: a server replaying its backlog at us
        d = metres(trail[-1], (lat, lon))
        if d < MIN_MOVE_M:
            trail.pending = None
            return False
        if len(trail) >= 2 and d > SPIKE_M and metres(trail[-2], (lat, lon)) < AGREE_M / 5:
            trail.pop()  # it went out there and straight back: that point was a wrong packet
            trail.pending = None
            return False
        if t is not None and len(trail[-1]) > 2 and d / max(abs(t - trail[-1][2]), 1) > MAX_SPEED_MS:
            if trail.pending is None or metres(trail.pending, (lat, lon)) > AGREE_M:
                trail.pending = (lat, lon)  # probably one corrupted packet
                return False
            trail.clear()  # it really is somewhere else now: what we had was the bad part
    trail.pending = None
    trail.append([round(lat, 6), round(lon, 6)] + ([int(t)] if t is not None else []))
    del trail[:-MAX_POINTS]
    return True


def clean(raw) -> Trail:
    """A trail read from a file or a server, or an empty one if it isn't one. Lone points far from both
    neighbours, which are themselves in the same place, are packets that were wrong: dropped."""
    pts: list[list[float]] = []
    try:
        for p in raw:
            lat, lon = float(p[0]), float(p[1])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                pts.append([lat, lon] + ([int(p[2])] if len(p) > 2 else []))
    except (TypeError, ValueError, IndexError):
        return Trail()
    pts = pts[-MAX_POINTS:]
    keep = [p for i, p in enumerate(pts)
            if not (0 < i < len(pts) - 1 and metres(pts[i - 1], pts[i + 1]) < MIN_MOVE_M
                    and metres(pts[i - 1], p) > SPIKE_M)]
    out = Trail()
    for p in keep:  # what is left of a spike is the same place twice
        if not out or metres(out[-1], p) >= MIN_MOVE_M:
            out.append(p)
    return out


def line(trail) -> list[list[float]]:
    """What the page draws: nothing until the station has actually moved."""
    return [p[:2] for p in trail] if len(trail) >= 2 else []
