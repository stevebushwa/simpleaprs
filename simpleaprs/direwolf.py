"""Dire Wolf as a second decoder. It runs as a child process (receive only: no transmit, no digipeating), is fed the
same sound-card audio over its stdin, and returns the frames it decodes as KISS over a local TCP port. Its
demodulator runs several decoders in parallel and recovers more packets from weak signals than ours does; the
built-in one stays on as the fallback and for the receive diagnostics.

On Debian/Raspberry Pi OS:  sudo apt install direwolf   (it is found on PATH; SIMPLEAPRS_DIREWOLF=0 turns it off)
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD

CONFIG = """\
ADEVICE stdin null
ARATE {rate}
ACHANNELS 1
CHANNEL 0
MYCALL N0CALL
MODEM 1200 E+
FIX_BITS 1
KISSPORT {port}
AGWPORT 0
"""


def available() -> str | None:
    """Path of the direwolf program, or None if it is not installed or switched off."""
    if os.environ.get("SIMPLEAPRS_DIREWOLF", "1") == "0":
        return None
    return shutil.which("direwolf")


class KissParser:
    """Bytes in, the data frames (port 0) of the KISS stream out."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._in_frame = False
        self._esc = False

    def feed(self, data: bytes) -> list[bytes]:
        frames = []
        for b in data:
            if b == FEND:
                if self._in_frame and len(self._buf) > 1 and self._buf[0] == 0x00:
                    frames.append(bytes(self._buf[1:]))
                self._buf.clear()
                self._in_frame, self._esc = True, False
            elif not self._in_frame:
                continue
            elif self._esc:
                self._buf.append(FEND if b == TFEND else FESC if b == TFESC else b)
                self._esc = False
            elif b == FESC:
                self._esc = True
            else:
                self._buf.append(b)
        return frames


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Direwolf:
    def __init__(self, on_frame, rate: int = 48000) -> None:
        """on_frame(raw) is called from a reader thread for every frame Dire Wolf decodes."""
        self.on_frame, self.rate = on_frame, rate
        self._proc: subprocess.Popen | None = None
        self._dir: str | None = None
        self._reader: threading.Thread | None = None
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        exe = available()
        if not exe:
            return False
        self.stop()
        port = _free_port()
        self._dir = tempfile.mkdtemp(prefix="simpleaprs-direwolf-")
        conf = os.path.join(self._dir, "direwolf.conf")
        with open(conf, "w") as f:
            f.write(CONFIG.format(rate=self.rate, port=port))
        try:
            self._proc = subprocess.Popen([exe, "-c", conf, "-t", "0", "-q", "hd"], stdin=subprocess.PIPE,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=self._dir)
        except OSError:
            self._proc = None
            return False
        time.sleep(0.3)
        if not self.running:  # a missing library, for one
            self.stop()
            return False
        self._stopping = False
        self._reader = threading.Thread(target=self._read, args=(port,), daemon=True)
        self._reader.start()
        return True

    def feed(self, pcm: bytes) -> None:
        """16-bit mono samples at the rate given to the constructor."""
        if not self.running:
            return
        try:
            self._proc.stdin.write(pcm)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass

    def stop(self) -> None:
        self._stopping = True
        if self._proc:
            try:
                self._proc.stdin.close()
            except (OSError, ValueError):
                pass
            self._proc.terminate()
            try:
                self._proc.wait(2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None

    def _read(self, port: int) -> None:
        sock = None
        for _ in range(100):  # it takes a moment to open its port
            if self._stopping or not self.running:
                return
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        if sock is None:
            return
        parser = KissParser()
        with sock:
            sock.settimeout(1)
            while not self._stopping and self.running:
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not data:
                    return
                for fr in parser.feed(data):
                    self.on_frame(fr)
