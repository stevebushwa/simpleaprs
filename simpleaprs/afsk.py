"""Bell 202 AFSK 1200 baud modem (mark 1200 Hz, space 2200 Hz)."""
from __future__ import annotations

import numpy as np

from .ax25 import HdlcDecoder, hdlc_bits

BAUD = 1200
MARK = 1200.0
SPACE = 2200.0


def modulate(frame: bytes, fs: int = 48000, amplitude: float = 0.5, **kw) -> np.ndarray:
    """AX.25 frame bytes -> float32 audio samples."""
    bits = hdlc_bits(frame, **kw)
    tone = MARK
    symbols = []
    for b in bits:  # NRZI: 0 = toggle, 1 = hold
        if b == 0:
            tone = SPACE if tone == MARK else MARK
        symbols.append(tone)
    spb = fs / BAUD
    n = int(len(symbols) * spb)
    freq = np.repeat(symbols, 1)[np.minimum((np.arange(n) / spb).astype(int), len(symbols) - 1)]
    phase = 2 * np.pi * np.cumsum(freq) / fs
    return (amplitude * np.sin(phase)).astype(np.float32)


class Demodulator:
    """Streaming demodulator. feed() audio chunks, get decoded frame bytes back."""

    def __init__(self, fs: int = 48000) -> None:
        self.fs = fs
        self.spb = fs / BAUD
        self.win = int(round(self.spb))
        t = np.arange(self.win) / fs
        self._ref = {f: np.exp(-2j * np.pi * f * t) for f in (MARK, SPACE)}
        self._tail = np.zeros(self.win - 1, dtype=np.float32)
        self._hdlc = HdlcDecoder()
        self._prev_sym = 1
        self._prev_tone = 0  # sign of previous decision, for edge detection
        self._phase = 0.0  # position within current bit, in samples
        self._acc = 0.0

    def _decision(self, x: np.ndarray) -> np.ndarray:
        """Positive = mark, negative = space, one value per input sample."""
        x = np.concatenate([self._tail, x.astype(np.float32)])
        self._tail = x[-(self.win - 1):]
        out = []
        for f in (MARK, SPACE):
            # correlate with the bit-length complex reference
            re = np.correlate(x, self._ref[f].real, "valid")
            im = np.correlate(x, self._ref[f].imag, "valid")
            out.append(np.hypot(re, im))
        return out[0] - out[1]

    def take_damaged(self) -> list[tuple[bytes, bool]]:
        """Packets heard since the last call that looked like AX.25 but failed their checksum: (bytes, whole)."""
        return self._hdlc.take_damaged()

    def feed(self, samples: np.ndarray) -> list[bytes]:
        d = self._decision(np.asarray(samples, dtype=np.float32))
        frames: list[bytes] = []
        spb = self.spb
        phase = self._phase
        prev = self._prev_tone
        sym_prev = self._prev_sym
        half = spb / 2
        for v in d:
            tone = 1 if v > 0 else -1
            if tone != prev:  # edges should land at phase 0; pull the clock toward that
                phase -= 0.3 * (phase if phase < half else phase - spb)
                prev = tone
            old = phase
            phase += 1
            if old < half <= phase:  # middle of the bit: take a sample
                bit = 1 if tone == sym_prev else 0  # NRZI decode
                sym_prev = tone
                frame = self._hdlc.feed(bit)
                if frame is not None:
                    frames.append(frame)
            if phase >= spb:
                phase -= spb
        self._phase, self._prev_tone, self._prev_sym = phase, prev, sym_prev
        return frames
