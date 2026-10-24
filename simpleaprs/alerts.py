"""Alert sounds, played on their own output so they never go out through the radio."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaDevices, QSoundEffect

from .audio import device_names  # noqa: F401  (re-exported for the settings dialog)

SOUNDS = Path(__file__).parent / "sounds"


class Alerts:
    def __init__(self) -> None:
        self.device_name = ""
        self.muted = False
        self._fx: dict[str, QSoundEffect] = {}

    def set_device(self, name: str) -> None:
        self.device_name = name
        dev = next((d for d in QMediaDevices.audioOutputs() if d.description() == name),
                   QMediaDevices.defaultAudioOutput())
        for kind in ("message", "station", "sent"):
            fx = QSoundEffect()
            fx.setAudioDevice(dev)
            fx.setSource(QUrl.fromLocalFile(str(SOUNDS / f"{kind}.wav")))
            self._fx[kind] = fx

    def play(self, kind: str) -> None:
        fx = self._fx.get(kind)
        if fx and not self.muted:
            fx.play()
