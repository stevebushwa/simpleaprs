"""Automatic capture volume for the radio's sound card.

A signal that clips or is lost in the noise at the sound card can't be fixed afterwards (the demodulator
compares tone strengths, so scaling the samples later changes nothing), so this moves the sound card's own
input volume: down when the audio clips, up when what is heard stays quiet. It talks to PulseAudio/PipeWire
through pactl or wpctl, whichever is installed.

GainController decides (no I/O, so it is testable); AutoGain applies the decision.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time

import numpy as np

CLIP = 0.97       # a peak this high is clipping: turn down
LOW = 0.25        # if even the loudest audio of a window stays under this, turn up
ACTIVE_RMS = 0.01  # quieter than this is a closed squelch, not a signal, and says nothing about the volume
WINDOW = 20.0     # seconds of audio judged together before turning up
MIN_ACTIVE = 0.5  # seconds of non-silent audio needed in a window to turn up
COOLDOWN = 1.5    # seconds after any change before judging again, so the new level has settled
STEP = 5          # percent per change
FLOOR = 5         # never turn the volume down past this percent


class GainController:
    def __init__(self) -> None:
        self._reset(0.0)

    def _reset(self, now: float) -> None:
        self._start, self._active, self._peak, self._hold = now, 0.0, 0.0, now + COOLDOWN

    def feed(self, x: np.ndarray, fs: int, now: float | None = None) -> int:
        """One chunk of float audio (-1..1). Returns -1 (turn down), +1 (turn up) or 0."""
        if not len(x):
            return 0
        now = time.monotonic() if now is None else now
        if not self._start:
            self._reset(now)
            return 0
        if now < self._hold:
            return 0
        peak = float(np.abs(x).max())
        if peak >= CLIP:
            self._reset(now)
            return -1
        if float(np.sqrt(np.mean(x * x))) >= ACTIVE_RMS:
            self._active += len(x) / fs
            self._peak = max(self._peak, peak)
        if now - self._start < WINDOW:
            return 0
        raise_it = self._active >= MIN_ACTIVE and self._peak < LOW
        self._reset(now)
        if not raise_it:
            self._hold = now  # a window that changed nothing needs no settling time
        return 1 if raise_it else 0


def _run(*cmd: str) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _matches(want: str, *have: str) -> bool:
    """Is want (an audio device name as the app knows it) one of these? Names carry hardware suffixes,
    e.g. 'USB Audio Device: - (hw:1,0)' for what PulseAudio calls 'USB Audio Device Mono'."""
    want = want.lower()
    base = want.split(":")[0].strip()
    return any(want == h.lower() or (base and base in h.lower()) for h in have)


class Pactl:
    name = "pactl"

    def find(self, device: str) -> str | None:
        if not device:
            return "@DEFAULT_SOURCE@"
        out = _run("pactl", "list", "sources") or ""
        for block in out.split("Source #")[1:]:
            name = re.search(r"^\s*Name: (.+)$", block, re.M)
            desc = re.search(r"^\s*Description: (.+)$", block, re.M)
            if name and not name.group(1).endswith(".monitor") and _matches(device, name.group(1), desc.group(1) if desc else ""):
                return name.group(1)
        return None

    def get(self, src: str) -> int | None:
        m = re.search(r"(\d+)%", _run("pactl", "get-source-volume", src) or "")
        return int(m.group(1)) if m else None

    def set(self, src: str, pct: int) -> bool:
        return _run("pactl", "set-source-volume", src, f"{pct}%") is not None


class Wpctl:
    name = "wpctl"

    def find(self, device: str) -> str | None:
        if not device:
            return "@DEFAULT_AUDIO_SOURCE@"
        in_sources = False
        for line in (_run("wpctl", "status") or "").splitlines():
            if re.search(r"[├└]─ \w", line):
                in_sources = line.rstrip().endswith("Sources:")
            elif in_sources:
                m = re.search(r"(\d+)\.\s+(.+?)(?:\s+\[vol:.*)?$", line)
                if m and _matches(device, m.group(2)):
                    return m.group(1)
        return None

    def get(self, src: str) -> int | None:
        m = re.search(r"Volume: ([\d.]+)", _run("wpctl", "get-volume", src) or "")
        return round(float(m.group(1)) * 100) if m else None

    def set(self, src: str, pct: int) -> bool:
        return _run("wpctl", "set-volume", src, f"{pct / 100:.2f}") is not None


def _backend(device: str):
    """(backend, source) for the first tool that is installed and knows this device, else (None, reason)."""
    reason = "neither pactl nor wpctl found"
    for b in (Pactl(), Wpctl()):
        if not shutil.which(b.name):
            continue
        src = b.find(device)
        if src is not None:
            return b, src
        reason = f"{b.name} doesn't know the input '{device}'"
    return None, reason


class AutoGain:
    """Feed it the audio the radio hears (any thread); it nudges the sound card's input volume."""

    def __init__(self, fs: int, on_message=lambda text: None) -> None:
        self.fs, self.on_message = fs, on_message
        self.enabled, self.device = False, ""
        self._ctl = GainController()
        self._busy = threading.Lock()
        self._warned = False

    def configure(self, enabled: bool, device: str) -> None:
        self.enabled, self.device, self._warned = enabled, device, False
        self._ctl = GainController()

    def feed(self, x: np.ndarray) -> None:
        if not self.enabled:
            return
        step = self._ctl.feed(x, self.fs)
        if step and self._busy.acquire(blocking=False):  # the tools take a moment: not on the audio thread
            threading.Thread(target=self._apply, args=(step,), daemon=True).start()

    def _apply(self, step: int) -> None:
        try:
            backend, src = _backend(self.device)
            if backend is None:
                if not self._warned:
                    self._warned = True
                    self.on_message(f"Auto gain can't change the input volume: {src}")
                return
            now = backend.get(src)
            if now is None:
                return
            new = max(FLOOR, min(100, now + step * STEP))
            if new != now and backend.set(src, new):
                self.on_message(f"Auto gain: input volume {now}% -> {new}% ({'clipping' if step < 0 else 'signal weak'})")
        finally:
            self._busy.release()
