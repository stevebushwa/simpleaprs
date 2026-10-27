"""Sound-card I/O through PortAudio (sounddevice), wired to the AFSK modem. The no-Qt twin of audio.py,
for the headless server (on Debian/Raspberry Pi OS: sudo apt install libportaudio2)."""
from __future__ import annotations

import math
import queue
import threading

import numpy as np

from .afsk import BAUD, Demodulator, modulate
from .autogain import AutoGain

FS = 48000


def _sd():
    try:
        import sounddevice
    except (ImportError, OSError) as e:  # OSError: the PortAudio library itself is missing
        raise SystemExit(f"Audio needs the sounddevice package and PortAudio ({e}).\n"
                         "Install with:  pip install 'simpleaprs[server]'  and  sudo apt install libportaudio2")
    return sounddevice


def device_names(inputs: bool) -> list[str]:
    key = "max_input_channels" if inputs else "max_output_channels"
    return [d["name"] for d in _sd().query_devices() if d[key] > 0]


def find_device(name: str, inputs: bool) -> str | None:
    """The device a saved name refers to: an exact match, else one whose name contains it (names
    carry hardware suffixes that change). None if there is no such device; "" means the default."""
    names = device_names(inputs)
    if name in names:
        return name
    return next((n for n in names if name.lower() in n.lower()), None)


class SoundRadio:
    def __init__(self, on_frame, on_tx_start=lambda: None, on_gain_message=lambda text: None, on_level=lambda v: None) -> None:
        """on_frame(raw) is called from a worker thread for every frame decoded, on_level(0..1) for every
        tenth of a second of input audio (the same scale as audio.Radio.level)."""
        self.on_frame, self.on_tx_start, self.on_level = on_frame, on_tx_start, on_level
        self._demod = Demodulator(FS)
        self._gain = AutoGain(FS, on_gain_message)
        self._chunks: queue.Queue = queue.Queue(maxsize=200)
        self._in = self._out_dev = None
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self.transmitting = False
        # tenth-second input blocks well above the noise (a signal, not hiss) and how many of those hit full scale
        self.loud = self.clipped = 0
        self._floor = 0.0  # running estimate of the noise level between packets
        self._peak = 0.0   # loudest sample since rx_stats() was last called

    def rx_stats(self) -> dict:
        """Running totals since start: frames decoded, damaged ones heard, and the input clipping counts.
        "peak" is the loudest sample (0..1) since the previous call."""
        peak, self._peak = self._peak, 0.0
        return {**self._demod._hdlc.stats, "loud": self.loud, "clipped": self.clipped, "peak": peak,
                "last_bad": self._demod._hdlc.last_bad}

    def _measure(self, x: np.ndarray) -> None:
        """Count a block as a signal if it is well above the noise floor, and as clipped if it also hit full scale.
        The floor rises slowly and falls at once, so it settles on the quiet between packets even when the
        squelch is open and hiss alone is loud."""
        rms, peak = float(np.sqrt(np.mean(x * x))), float(np.abs(x).max())
        self._floor = min(rms, self._floor * 1.01) if self._floor else rms
        self._peak = max(self._peak, peak)
        if rms > max(0.02, 2.5 * self._floor):
            self.loud += 1
            self.clipped += peak >= 0.98

    def start(self, input_name: str = "", output_name: str = "", auto_gain: bool = False) -> bool:
        """Open the audio input. False if there is no usable input device."""
        self.stop()
        self._gain.configure(auto_gain, input_name)
        sd = _sd()
        self._out_dev = (find_device(output_name, False) or None) if output_name else None
        try:
            self._in = sd.RawInputStream(samplerate=FS, channels=1, dtype="int16", blocksize=FS // 10,
                                         device=(find_device(input_name, True) or None) if input_name else None,
                                         callback=self._on_audio)
            self._in.start()
        except (sd.PortAudioError, ValueError):
            self._in = None
            return False
        self._worker = threading.Thread(target=self._decode, daemon=True)
        self._worker.start()
        return True

    def stop(self) -> None:
        if self._in:
            self._in.stop()
            self._in.close()
            self._in = None
        if self._worker:
            self._chunks.put(None)
            self._worker.join(2)
            self._worker = None

    def _on_audio(self, indata, frames, time_info, status) -> None:
        """PortAudio's thread: hand the samples to the worker and get out of the way."""
        try:
            self._chunks.put_nowait(bytes(indata))
        except queue.Full:
            pass

    def _decode(self) -> None:
        while (data := self._chunks.get()) is not None:
            x = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768
            if x.size:
                self.on_level(min(1.0, float(np.abs(x).max()) * 2))
                if not self.transmitting:
                    self._measure(x)
            if self.transmitting:  # half duplex: don't decode our own audio
                continue
            self._gain.feed(x)
            for fr in self._demod.feed(x):
                self.on_frame(fr)

    def transmit(self, frame: bytes, lead_ms: int = 500, level: float = 0.5) -> None:
        """VOX-friendly: the preamble flags double as the lead-in that opens VOX."""
        with self._lock:
            if self.transmitting:
                return
            self.transmitting = True
        flags = max(30, math.ceil(lead_ms / 1000 * BAUD / 8))
        audio = modulate(frame, FS, amplitude=level, preamble_flags=flags)
        pcm = (audio * 32767).astype(np.int16).tobytes() + bytes(FS // 2 * 2)  # silent tail, played out before stop
        self.on_tx_start()
        threading.Thread(target=self._play, args=(pcm,), daemon=True).start()

    def _play(self, pcm: bytes) -> None:
        sd = _sd()
        try:
            with sd.RawOutputStream(samplerate=FS, channels=1, dtype="int16", device=self._out_dev) as out:
                out.write(pcm)  # blocks until it is all handed over; leaving the block waits for it to play
        except (sd.PortAudioError, ValueError) as e:
            print(f"Could not transmit: {e}", flush=True)
        finally:
            self.transmitting = False
