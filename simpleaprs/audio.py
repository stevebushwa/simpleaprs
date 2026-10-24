"""Sound-card I/O through Qt Multimedia, wired to the AFSK modem."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QAudioSource, QMediaDevices

from .afsk import BAUD, Demodulator, modulate

FS = 48000


def device_names(inputs: bool) -> list[str]:
    devs = QMediaDevices.audioInputs() if inputs else QMediaDevices.audioOutputs()
    return [d.description() for d in devs]


def _find(name: str, inputs: bool):
    devs = QMediaDevices.audioInputs() if inputs else QMediaDevices.audioOutputs()
    for d in devs:
        if d.description() == name:
            return d
    return QMediaDevices.defaultAudioInput() if inputs else QMediaDevices.defaultAudioOutput()


def _format() -> QAudioFormat:
    f = QAudioFormat()
    f.setSampleRate(FS)
    f.setChannelCount(1)
    f.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    return f


class Radio(QObject):
    frame_received = Signal(bytes)
    level = Signal(float)       # RX audio level 0..1, for the setup meter
    tx_started = Signal()
    tx_finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._demod = Demodulator(FS)
        self._src: QAudioSource | None = None
        self._io: QIODevice | None = None
        self._sink: QAudioSink | None = None
        self._txbuf: QBuffer | None = None
        self.transmitting = False

    def start(self, input_name: str = "", output_name: str = "") -> bool:
        """Open the audio input. False if there is no usable input device."""
        self.stop()
        self._in_dev, self._out_dev = _find(input_name, True), _find(output_name, False)
        self._src = QAudioSource(self._in_dev, _format())
        self._io = self._src.start()
        if self._io is None:
            self._src = None
            return False
        self._io.readyRead.connect(self._on_audio)
        return True

    def stop(self) -> None:
        if self._src:
            self._src.stop()
            self._src = None

    def _on_audio(self) -> None:
        data = bytes(self._io.readAll())
        if not data:
            return
        x = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768
        self.level.emit(float(min(1.0, np.abs(x).max() * 2)))
        if self.transmitting:  # half duplex: don't decode our own audio
            return
        for fr in self._demod.feed(x):
            self.frame_received.emit(fr)

    def transmit(self, frame: bytes, lead_ms: int = 500, level: float = 0.5) -> None:
        """VOX-friendly: the preamble flags double as the lead-in that opens VOX."""
        if self.transmitting:
            return
        flags = max(30, math.ceil(lead_ms / 1000 * BAUD / 8))
        audio = modulate(frame, FS, amplitude=level, preamble_flags=flags)
        pcm = (audio * 32767).astype(np.int16).tobytes() + bytes(FS // 2 * 2)  # silent tail, played out before stop
        self._txbuf = QBuffer()
        self._txbuf.setData(QByteArray(pcm))
        self._txbuf.open(QIODevice.OpenModeFlag.ReadOnly)
        self._sink = QAudioSink(self._out_dev, _format())
        self.transmitting = True
        self.tx_started.emit()
        self._sink.start(self._txbuf)
        # Idle only means the data reached the sink's buffer, and stop() discards what is still
        # buffered, so finish on the clock instead of on the state change.
        QTimer.singleShot(int(len(pcm) / 2 / FS * 1000) + 300, self._tx_done)

    def _tx_done(self) -> None:
        if self.transmitting:
            self.transmitting = False
            self._sink.stop()
            self.tx_finished.emit()
