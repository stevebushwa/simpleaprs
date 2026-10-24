"""APRS payload parsing and building (position, compressed, Mic-E, message, status)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ax25 import Address, Frame

_POS = re.compile(r"^([!=/@])(?:\d{6}[hz/])?(\d{2})([\d ]{2}\.[\d ]{2})([NS])(.)(\d{3})([\d ]{2}\.[\d ]{2})([EW])(.)(.*)$", re.S)
_MSG = re.compile(r"^:(.{9}):(.*?)(?:\{(\w{1,5}))?$", re.S)


@dataclass
class Packet:
    frame: Frame
    kind: str = "other"  # position | message | status | other
    lat: float | None = None
    lon: float | None = None
    symbol: str = "/>"  # table + code
    comment: str = ""
    to: str = ""
    text: str = ""
    msg_id: str = ""
    weather: dict = field(default_factory=dict)  # label -> value with units, when the packet carries weather
    inner: "Packet | None" = None  # the packet carried inside a third-party ('}') one
    gated: bool = False  # third-party packet that came from the internet (TCPIP/TCPXX/qA in its path)
    obj: str = ""  # object/item name; it is what gets plotted, the frame source only transmitted it

    @property
    def source(self) -> str:
        return self.obj or str(self.frame.src).rstrip("*")


def _num(m: "re.Match | None", scale: float = 1, nd: int = 0) -> float | None:
    return round(int(m.group(1)) * scale, nd) if m else None


def weather(text: str) -> dict:
    """Decode the weather fields of a positioned or positionless weather report. Empty if none found."""
    w: dict[str, str] = {}
    wind = re.match(r"^(\d{3})/(\d{3})", text)
    d = re.search(r"(?<![A-Za-z])c(\d{3})", text)
    sp = re.search(r"s(\d{3})", text)
    if wind:
        w["Wind"] = f"{int(wind.group(2))} mph from {int(wind.group(1))}°"
    elif d and sp:
        w["Wind"] = f"{int(sp.group(1))} mph from {int(d.group(1))}°"
    g = re.search(r"g(\d{3})", text)
    if g:
        w["Gust"] = f"{int(g.group(1))} mph"
    t = re.search(r"t(-\d{2}|\d{3})", text)
    if t:
        w["Temp"] = f"{int(t.group(1))} °F"
    for key, label, unit in (("r", "Rain 1h", "in"), ("p", "Rain 24h", "in"), ("P", "Rain today", "in")):
        v = _num(re.search(key + r"(\d{3})", text), 0.01, 2)
        if v is not None:
            w[label] = f"{v:.2f} {unit}"
    h = re.search(r"h(\d{2})", text)
    if h:
        w["Humidity"] = f"{100 if h.group(1) == '00' else int(h.group(1))}%"
    b = re.search(r"b(\d{5})", text)
    if b:
        w["Pressure"] = f"{int(b.group(1)) / 10:.1f} hPa"
    return w


# APRS symbol code -> (icon, description). The same code on the alternate table (overlays) means
# the same thing for everything here, so the table character is ignored.
_ICONS = {
    ">": ("car", "Car"), "k": ("truck", "Truck"), "u": ("truck", "Truck"), "v": ("car", "Van"),
    "j": ("car", "Jeep"), "U": ("bus", "Bus"), "R": ("car", "RV"), "f": ("truck", "Fire truck"),
    "a": ("car", "Ambulance"), "P": ("car", "Police"), "<": ("bike", "Motorcycle"), "b": ("bike", "Bicycle"),
    "[": ("person", "Person"), "'": ("plane", "Small aircraft"), "^": ("plane", "Aircraft"),
    "X": ("plane", "Helicopter"), "s": ("boat", "Ship"), "Y": ("boat", "Yacht"),
    "-": ("house", "House"), "y": ("house", "House with yagi"), "r": ("tower", "Antenna"), "#": ("tower", "Digipeater"), "&": ("igate", "iGate"), "_": ("weather", "Weather station"),
}


# Symbols offered in Settings: (label, table+code)
SYMBOLS = [
    ("House", "/-"), ("House with yagi", "/y"), ("Antenna", "/r"), ("Person / handheld", "/["),
    ("Bicycle", "/b"), ("Motorcycle", "/<"), ("Car", "/>"), ("Van", "/v"), ("Truck", "/k"), ("RV", "/R"),
    ("Jeep", "/j"), ("Boat", "/s"), ("Aircraft", "/'"), ("Digipeater", "/#"), ("iGate", "/&"),
    ("Weather station", "/_"),
]

_MOBILE = {"car", "truck", "bus", "plane", "boat", "bike", "person"}


def kinds(symbol: str, weather: bool = False, roles: "set[str] | frozenset[str]" = frozenset()) -> set[str]:
    """Every filter category a station belongs to: weather, igate, digi, mobile."""
    code = symbol[1:2]
    k = set()
    if weather or code == "_":
        k.add("weather")
    if "igate" in roles or code == "&":
        k.add("igate")
    if "digi" in roles or code == "#":
        k.add("digi")
    if _ICONS.get(code, ("house",))[0] in _MOBILE:
        k.add("mobile")
    return k


def classify(symbol: str, weather: bool = False, roles: "set[str] | frozenset[str]" = frozenset()) -> tuple[str, str, str]:
    """(icon, colour class, description).

    Weather stations are blue, iGates green, digipeaters orange; vehicles and people get their own
    icon; anything unrecognised is treated as a fixed station and gets a house. roles holds what we
    have observed the station doing ("digi": seen repeating packets, "igate": gating the internet
    to RF); the symbol's own claim ('#' digipeater, '&' iGate) counts too.
    """
    code = symbol[1:2]
    icon, desc = _ICONS.get(code, ("house", "Station"))
    found = set(roles) | ({"digi"} if code == "#" else set()) | ({"igate"} if code == "&" else set())
    names = [n for r, n in (("igate", "iGate"), ("digi", "digipeater")) if r in found]
    label = " + ".join(names)
    if weather or code == "_":
        return "weather", "wx", "Weather station" + (f" + {label}" if label else "")
    if "igate" in found:
        return "igate", "igate", label
    if "digi" in found:
        return "tower", "digi", label.capitalize()
    return icon, "other", desc


_ALIASES = ("WIDE", "RELAY", "TRACE", "TEMP", "GATE", "ECHO", "HOPn")


def digipeaters(frame: Frame) -> list[str]:
    """Stations seen repeating this packet: the starred path entries, minus generic aliases."""
    return [n for a in frame.path if a.repeated and not (n := str(a).rstrip("*")).startswith(_ALIASES)]


def parse_text(text: str) -> "Packet | None":
    """Parse 'SRC>DST,VIA,VIA*:info' (the TNC2 form carried inside third-party packets)."""
    head, sep, info = text.partition(":")
    src, arrow, rest = head.partition(">")
    if not (sep and arrow and src):
        return None
    dst, *via = rest.split(",")
    try:
        frame = Frame(Address.parse(src), Address.parse(dst), [Address.parse(v) for v in via if v], info.encode("latin-1"))
    except ValueError:
        return None
    return parse(frame)


def route(frame: Frame, lookup, me: tuple[float, float] | None) -> tuple[list[dict], str]:
    """The path a packet took to reach us, as map line segments plus a text summary.

    Only digipeaters that have repeated the packet (starred) count. lookup(call) gives a
    (lat, lon) for a digipeater we have heard a position from, else None; a hop with no
    position, or a generic alias like WIDE1, leaves a gap that is drawn dashed.
    Each segment is {"coords": [[lon, lat], [lon, lat]], "dashed": bool}.
    """
    src = lookup(str(frame.src).rstrip("*"))
    last = max((i for i, a in enumerate(frame.path) if a.repeated), default=-1)
    hops = [str(a).rstrip("*") for a in frame.path[:last + 1] if a.repeated]
    points = [(str(frame.src).rstrip("*"), src)]
    for h in hops:
        points.append((h, None if h.startswith(_ALIASES) else lookup(h)))
    if me:
        points.append(("you", me))
    segs: list[dict] = []
    prev, gap = None, False
    for _, pos in points:
        if pos is None:
            gap = True
            continue
        if prev is not None:
            segs.append({"coords": [[prev[1], prev[0]], [pos[1], pos[0]]], "dashed": gap})
        prev, gap = pos, False
    text = " → ".join(n + ("" if pos or n == "you" else " (no position)") for n, pos in points[1:])
    return segs, text if hops else "heard directly"


def _b91(s: str) -> int:
    v = 0
    for c in s:
        v = v * 91 + ord(c) - 33
    return v


def _mice(dst: str, info: str) -> tuple[float, float] | None:
    if len(dst) < 6 or len(info) < 9:
        return None
    d = []
    for c in dst[:6]:
        if c.isdigit():
            d.append(int(c))
        elif "A" <= c <= "J":
            d.append(ord(c) - 65)
        elif "P" <= c <= "Y":
            d.append(ord(c) - 80)
        elif c in "KLZ":
            d.append(0)
        else:
            return None
    lat = d[0] * 10 + d[1] + (d[2] * 10 + d[3] + (d[4] * 10 + d[5]) / 100) / 60
    if dst[3] <= "L" and not dst[3].isdigit() or dst[3].isdigit():
        lat = -lat if not ("P" <= dst[3] <= "Z") else lat
    lon_off = 100 if "P" <= dst[4] <= "Z" else 0
    deg = ord(info[1]) - 28 + lon_off
    if deg >= 190:
        deg -= 190
    elif deg >= 180:
        deg -= 80
    mn = ord(info[2]) - 28
    if mn >= 60:
        mn -= 60
    hun = ord(info[3]) - 28
    lon = deg + (mn + hun / 100) / 60
    if "P" <= dst[5] <= "Z":  # byte 6: P-Z = west, 0-9/L = east
        lon = -lon
    return lat, lon


def _plain_position(p: Packet, info: str) -> bool:
    """Fill p from a plain or compressed position report (info starts with ! = / @)."""
    m = _POS.match(info)
    if m:
        _, d, mn, ns, tbl, d2, mn2, ew, code, rest = m.groups()
        p.kind = "position"
        p.lat = (int(d) + float(mn.replace(" ", "0")) / 60) * (1 if ns == "N" else -1)
        p.lon = (int(d2) + float(mn2.replace(" ", "0")) / 60) * (1 if ew == "E" else -1)
        p.symbol, p.comment = tbl + code, rest
        return True
    if info[0] in "!=/@":
        body = re.sub(r"^[!=]|^[/@]\d{6}[hz/]", "", info)
        if len(body) >= 13 and body[0] in "/\\" + "ABCDEFGHIJ" + "abcdefghij" + "0123456789":
            try:
                p.lat = 90 - _b91(body[1:5]) / 380926
                p.lon = -180 + _b91(body[5:9]) / 190463
                p.kind, p.symbol, p.comment = "position", body[0] + body[9], body[13:]
                return True
            except Exception:
                pass
    return False


def parse(frame: Frame) -> Packet:
    info = frame.info.decode("latin-1")
    p = Packet(frame)
    if not info:
        return p
    if info[0] == "}":  # third-party: }SRC>DST,TCPIP,IGATE*:payload
        p.inner = parse_text(info[1:])
        if p.inner:
            p.gated = any(a.call.startswith(("TCPIP", "TCPXX", "qA")) for a in p.inner.frame.path)
        return p
    if info[0] in "`'":
        pos = _mice(frame.dst.call, info)
        if pos:
            p.kind, (p.lat, p.lon) = "position", pos
            p.symbol = info[8:9] + info[7:8] if len(info) > 8 else "/>"
            p.comment = info[9:]
        return p
    if _plain_position(p, info):
        if p.symbol[1:] == "_":  # weather station symbol
            p.weather = weather(p.comment)
        return p
    if info[0] == "_":  # positionless weather: _MMDDHHMM<fields>
        p.weather = weather(info[9:])
        if p.weather:
            p.kind = "weather"
        return p
    if info[0] == ";" and len(info) >= 18:  # object: ;NAME_____*DDHHMMz<position>
        if _plain_position(p, "!" + info[18:]):
            p.obj = info[1:10].strip()
        return p
    if info[0] == ")":  # item: )NAME!<position>
        m = re.match(r"^\)([^!_]{3,9})[!_](.*)$", info, re.S)
        if m and _plain_position(p, "!" + m.group(2)):
            p.obj = m.group(1).strip()
        return p
    m = _MSG.match(info)
    if m:
        p.kind, p.to, p.text, p.msg_id = "message", m.group(1).strip(), m.group(2), m.group(3) or ""
        return p
    if info[0] == ">":
        p.kind, p.text = "status", info[1:]
    return p


def build_position(lat: float, lon: float, symbol: str = "/>", comment: str = "") -> bytes:
    def fmt(v: float, deg_w: int, pos: str, neg: str) -> str:
        h = pos if v >= 0 else neg
        v = abs(v)
        d = int(v)
        return f"{d:0{deg_w}d}{(v - d) * 60:05.2f}{h}"
    return f"!{fmt(lat, 2, 'N', 'S')}{symbol[0]}{fmt(lon, 3, 'E', 'W')}{symbol[1]}{comment}".encode("latin-1")


_BULLETIN = re.compile(r"^BLN[0-9A-Z]\w{0,5}$")


def is_bulletin(to: str) -> bool:
    """Is a message addressed to a bulletin: BLN plus a number (or announcement letter), then an optional group name."""
    return bool(_BULLETIN.match(to))


def build_message(to: str, text: str, msg_id: str = "") -> bytes:
    """Also how a bulletin is built: to="BLN1" and no msg_id, so nobody is asked to ack it."""
    return f":{to.upper().ljust(9)[:9]}:{text}{'{' + msg_id if msg_id else ''}".encode("latin-1")


def make_frame(call: str, info: bytes, path: str = "WIDE1-1,WIDE2-1") -> Frame:
    via = [Address.parse(x) for x in path.split(",") if x]
    return Frame(Address.parse(call), Address.parse("APSMPL"), via, info)
