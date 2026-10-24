"""AX.25 UI frames and HDLC bit-level framing."""
from __future__ import annotations

from dataclasses import dataclass, field


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


class HdlcDecoder:
    """Feed it NRZI-decoded bits; yields frames whose FCS checks out."""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self.ones = 0
        self.cur: list[int] = []
        self.active = False

    def feed(self, bit: int) -> bytes | None:
        if bit:
            self.ones += 1
            if self.ones > 6:  # abort
                self._reset()
                return None
            self.cur.append(1)
            return None
        ones, self.ones = self.ones, 0
        if ones == 6:  # flag
            result = None
            if self.active and len(self.cur) >= 7:
                body = self.cur[:-7]  # drop the flag's leading 0 and six 1s
                if len(body) % 8 == 0 and len(body) >= 8 * 17:
                    data = bytes(
                        sum(b << k for k, b in enumerate(body[i:i + 8]))
                        for i in range(0, len(body), 8)
                    )
                    if crc16_x25(data[:-2]).to_bytes(2, "little") == data[-2:]:
                        result = data[:-2]
            self.cur = []
            self.active = True
            return result
        if ones == 5:  # stuffed zero: drop it
            return None
        self.cur.append(0)
        return None
