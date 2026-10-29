"""AX.25 UI frames and HDLC bit-level framing."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .damaged import printable


def crc16_x25(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


@dataclass
class Address:
    call: str
    ssid: int = 0
    repeated: bool = False

    @classmethod
    def parse(cls, text: str) -> "Address":
        rep = text.endswith("*")
        text = text.rstrip("*").upper()
        call, _, ssid = text.partition("-")
        return cls(call, int(ssid or 0), rep)

    def __str__(self) -> str:
        s = self.call + (f"-{self.ssid}" if self.ssid else "")
        return s + ("*" if self.repeated else "")

    def encode(self, last: bool) -> bytes:
        call = self.call.ljust(6)[:6].encode("ascii")
        out = bytearray(c << 1 for c in call)
        out.append(0x60 | (self.ssid & 0xF) << 1 | (0x80 if self.repeated else 0) | int(last))
        return bytes(out)


@dataclass
class Frame:
    src: Address
    dst: Address
    path: list[Address] = field(default_factory=list)
    info: bytes = b""

    def encode(self) -> bytes:
        addrs = [self.dst, self.src, *self.path]
        out = b"".join(a.encode(i == len(addrs) - 1) for i, a in enumerate(addrs))
        return out + b"\x03\xf0" + self.info  # UI frame, no layer 3

    @classmethod
    def decode(cls, data: bytes) -> "Frame":
        if len(data) < 16:
            raise ValueError("frame too short")
        addrs = []
        i = 0
        while True:
            if i + 7 > len(data):
                raise ValueError("unterminated address field")
            raw = data[i:i + 7]
            call = bytes(c >> 1 for c in raw[:6]).decode("ascii", "replace").strip()
            addrs.append(Address(call, (raw[6] >> 1) & 0xF, bool(raw[6] & 0x80)))
            i += 7
            if raw[6] & 1:
                break
        if len(addrs) < 2 or i + 2 > len(data):
            raise ValueError("bad address field")
        return cls(addrs[1], addrs[0], addrs[2:], data[i + 2:])

    def __str__(self) -> str:
        via = "".join("," + str(a) for a in self.path)
        return f"{self.src}>{self.dst}{via}:{self.info.decode('latin-1')}"


FLAG = [0, 1, 1, 1, 1, 1, 1, 0]


def hdlc_bits(frame: bytes, preamble_flags: int = 30, tail_flags: int = 4) -> list[int]:
    """Bits ready for NRZI + AFSK: flags, bit-stuffed data + FCS, flags."""
    body = frame + crc16_x25(frame).to_bytes(2, "little")
    bits: list[int] = FLAG * preamble_flags
    ones = 0
    for byte in body:
        for k in range(8):
            bit = (byte >> k) & 1  # LSB first
            bits.append(bit)
            ones = ones + 1 if bit else 0
            if ones == 5:
                bits.append(0)
                ones = 0
    return bits + FLAG * tail_flags


def plausible_header(data: bytes) -> str:
    """"SRC>DST" if the start of data reads as AX.25 addresses, else "". Random noise essentially never does,
    so a frame that fails its checksum but passes this is a real packet that arrived damaged."""
    if len(data) < 15:
        return ""
    calls = []
    for i in range(0, min(len(data) - 6, 70), 7):
        raw = data[i:i + 7]
        text = bytes(c >> 1 for c in raw[:6])
        if raw[6] & 0x60 != 0x60 or not re.fullmatch(rb"[A-Z0-9]+ *", text):
            return ""
        calls.append(text.decode().strip() + (f"-{(raw[6] >> 1) & 0xF}" if (raw[6] >> 1) & 0xF else ""))
        if raw[6] & 1:
            break
    else:
        return ""
    return f"{calls[1]}>{calls[0]}" if len(calls) >= 2 else ""


def repair_one_bit(data: bytes) -> bytes | None:
    """data (frame plus FCS) with the one flipped bit that makes its checksum pass, or None. A frame with two
    or more bad bits can pass by luck (about 1 in 190 tries), so only offer this for frames that already look
    like AX.25."""
    buf = bytearray(data)
    for i in range(len(buf) * 8):
        buf[i >> 3] ^= 1 << (i & 7)
        if crc16_x25(bytes(buf[:-2])).to_bytes(2, "little") == bytes(buf[-2:]):
            return bytes(buf)
        buf[i >> 3] ^= 1 << (i & 7)
    return None


def damaged_line(data: bytes, whole: bool) -> str:
    """A packet that failed its checksum as one line for the raw packets list: the addresses, then the text with anything
    that is not printable shown as a dot and the trailing junk cut off. whole: the bytes ended on a clean flag, so the last
    two are the checksum and not text. The text can have wrong characters in it, mostly near the end, which the line says."""
    body = data[:-2] if whole else data
    try:
        frame = Frame.decode(body)
    except ValueError:
        return f"{plausible_header(data) or '?'}: (addresses only)   <-- DAMAGED, checksum failed"
    text = printable(frame.info)
    via = "".join("," + str(a) for a in frame.path)
    return f"{frame.src}>{frame.dst}{via}:{text}   <-- DAMAGED, checksum failed: parts of it may be wrong"


class HdlcDecoder:
    """Feed it NRZI-decoded bits; yields frames whose FCS checks out, or that one flipped bit makes check out.
    stats counts what else was heard: fixed = ok frames that needed that one bit put right (also counted in ok),
    corrupt = a packet whose checksum failed, slip = one that lost or gained a bit; last_bad is who it looked
    like it came from. damaged collects those packets themselves as (bytes, whole) for take_damaged(). A frame
    ends at a flag or, when the signal faded before one came, at the run of ones that stands in for it."""

    MAX_BITS = 3000  # longer than any frame; what is still open past this was noise

    def __init__(self) -> None:
        self.stats = {"ok": 0, "fixed": 0, "corrupt": 0, "slip": 0}
        self.last_bad = ""
        self.damaged: list[tuple[bytes, bool]] = []
        self._reset()

    def _reset(self) -> None:
        self.ones = 0
        self.cur: list[int] = []
        self.active = False

    def take_damaged(self) -> list[tuple[bytes, bool]]:
        out, self.damaged = self.damaged, []
        return out

    def _end(self) -> bytes | None:
        """The frame in cur, whose last seven bits were the flag (or the start of an abort); None if it is not one."""
        body = self.cur[:-7]  # drop the flag's leading 0 and six 1s
        if not self.active or len(body) < 8 * 17:
            return None
        data = bytes(sum(b << k for k, b in enumerate(body[i:i + 8])) for i in range(0, len(body) - 7, 8))
        whole = len(body) % 8 == 0
        if whole and crc16_x25(data[:-2]).to_bytes(2, "little") == data[-2:]:
            self.stats["ok"] += 1
            return data[:-2]
        if who := plausible_header(data):
            fixed = repair_one_bit(data) if whole else None
            if fixed:
                self.stats["ok"] += 1
                self.stats["fixed"] += 1
                return fixed[:-2]
            self.stats["corrupt" if whole else "slip"] += 1
            self.last_bad = who
            self.damaged.append((data, whole))
        return None

    def feed(self, bit: int) -> bytes | None:
        if bit:
            self.ones += 1
            if self.ones > 6:  # abort, or a flag whose closing 0 was lost
                result = self._end()
                self._reset()
                return result
            self.cur.append(1)
            if len(self.cur) > self.MAX_BITS:
                self._reset()
            return None
        ones, self.ones = self.ones, 0
        if ones == 6:  # flag
            result = self._end()
            self.cur = []
            self.active = True
            return result
        if ones == 5:  # stuffed zero: drop it
            return None
        self.cur.append(0)
        return None
