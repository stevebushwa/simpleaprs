"""Firmware updates for an ESP32 node (Heltec, LilyGO, Seeed XIAO ESP32 and the like) over the serial port it is plugged
into: the same thing the MeshCore web flasher does, without needing a browser with WebSerial.

The images come from MeshCore's GitHub releases (the "companion radio usb" builds, in their single-file "merged" form
that is written at address 0), or from a file you pick, for when the Pi has no internet. The node's connection is
dropped while esptool has the port and picked up again afterwards. nRF52 boards (RAK4631, T114...) update through a
different bootloader and are not handled here.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from piwebkit.hub import Hub

    from .node import MeshNode

REPO = "meshcore-dev/MeshCore"
RELEASES = f"https://api.github.com/repos/{REPO}/releases?per_page=12"
DOWNLOADS = f"https://github.com/{REPO}/releases/download/"
IMAGE = re.compile(r"^(?P<board>.+)_companion_radio_usb-(?P<ver>v\d+(?:\.\d+)*)-(?P<sha>[0-9a-f]+)-merged\.bin$")
ESP_MAGIC = 0xE9                      # the first byte of every ESP32 image
MIN_BYTES, MAX_BYTES = 64 * 1024, 4 * 1024 * 1024
LISTING_BYTES = 24 * 1024 * 1024
FLASH_BAUD = 460800
FLASH_TIMEOUT_S = 240
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


class FirmwareError(Exception):
    """Something the person can act on; the text is shown to them."""


def http_get(url: str) -> bytes:
    """A GET, refusing anything bigger than expected: the release list is a few MB, an image under MAX_BYTES."""
    limit = LISTING_BYTES if url == RELEASES else MAX_BYTES
    req = urllib.request.Request(url, headers={"User-Agent": "simplemesh", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise OSError("the reply is larger than expected")
    return data


def run_esptool(cmd: list[str], on_line: Callable[[str], None]) -> int:
    """Run an esptool command, handing each line (or progress update) of its output on. Returns its exit code."""
    if importlib.util.find_spec("esptool") is None:
        raise FirmwareError("esptool isn't installed here:  pip install 'simpleaprs[mesh]'  (the mesh extra)")
    p = subprocess.Popen([sys.executable, "-m", "esptool", *cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    timer = threading.Timer(FLASH_TIMEOUT_S, p.kill)
    timer.start()
    try:
        buf = b""
        while True:
            chunk = p.stdout.read1(512)
            if not chunk:
                break
            buf += chunk
            *lines, buf = re.split(rb"[\r\n]", buf)
            for line in lines:
                if line.strip():
                    on_line(line.decode("utf-8", "replace").strip())
        if buf.strip():
            on_line(buf.decode("utf-8", "replace").strip())
        return p.wait()
    finally:
        timer.cancel()


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", (v or "").split("-")[0]))


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class Updater:
    def __init__(self, node: "MeshNode", hub: "Hub", fetch: Callable[[str], bytes] | None = None,
                 flasher: Callable[[list[str], Callable[[str], None]], int] | None = None) -> None:
        self.node, self.hub = node, hub
        self.fetch = fetch or http_get
        self.flasher = flasher or run_esptool
        self._release: dict | None = None
        self._lock = threading.Lock()
        self._state: dict = {"state": "idle", "percent": 0, "text": "", "log": []}

    # --- progress -----------------------------------------------------------------------------------

    def state(self) -> dict:
        with self._lock:
            return {**self._state, "log": list(self._state["log"])}

    def _set(self, **kw) -> None:
        with self._lock:
            self._state.update(kw)
            ev = {"flash": {**self._state, "log": list(self._state["log"])}}
        self.hub.publish(ev, keep=False)

    def _log(self, line: str) -> None:
        with self._lock:
            self._state["log"] = (self._state["log"] + [line])[-30:]

    @property
    def busy(self) -> bool:
        return self._state["state"] in ("starting", "downloading", "flashing")

    # --- what is available --------------------------------------------------------------------------

    def check(self) -> dict:
        """The newest companion release and how it compares to the firmware on the node. Needs internet."""
        try:
            releases = json.loads(self.fetch(RELEASES))
        except Exception as e:
            raise FirmwareError(f"Couldn't reach GitHub ({e}). Without internet, pick a firmware file instead.") from None
        rel = next((r for r in releases if str(r.get("tag_name", "")).startswith("companion-v")
                    and not r.get("draft") and not r.get("prerelease")), None)
        if not rel:
            raise FirmwareError("No companion firmware release was found.")
        images = []
        for a in rel.get("assets", []):
            m = IMAGE.match(a.get("name", ""))
            url = a.get("browser_download_url", "")
            if m and url.startswith(DOWNLOADS):
                digest = str(a.get("digest") or "")
                images.append({"name": a["name"], "board": m["board"].replace("_", " "), "url": url, "size": a.get("size", 0),
                               "sha256": digest[7:] if digest.startswith("sha256:") else ""})
        images.sort(key=lambda i: i["board"].lower())
        version = rel["tag_name"][len("companion-"):]
        model = norm(self.node._self.get("model", ""))
        current = self.node._self.get("version", "")
        mine = next((i["name"] for i in images if model and norm(i["board"]) == model), "")
        with self._lock:
            self._release = {"images": {i["name"]: i for i in images}}
        return {"tag": rel["tag_name"], "version": version, "url": rel.get("html_url", ""), "images": images,
                "current": current, "model": self.node._self.get("model", ""), "suggested": mine,
                "newer": (version_tuple(version) > version_tuple(current)) if current else None}

    # --- doing it -----------------------------------------------------------------------------------

    def start(self, image: str = "", upload: tuple[str, str] | None = None, erase: bool = False) -> None:
        """Begin an update from a release image (by name) or an uploaded file (name, base64). Returns at once;
        progress arrives as {"flash"} events."""
        if self.busy:
            raise FirmwareError("An update is already running.")
        if upload:
            try:
                data = base64.b64decode(upload[1], validate=True)
            except ValueError:
                raise FirmwareError("That file didn't arrive intact.") from None
            label, source = upload[0] or "your file", None
            self._check_image(data)
        else:
            with self._lock:
                known = ((self._release or {}).get("images") or {}).get(image)
            if not known:
                raise FirmwareError("Check for updates first, then pick one of the images listed.")
            label, source, data = known["name"], known, None
        self._set(state="starting", percent=0, text=f"Preparing {label}...", log=[], image=label)
        threading.Thread(target=self._run, args=(source, data, erase), daemon=True, name="firmware").start()

    @staticmethod
    def _check_image(data: bytes) -> None:
        if not MIN_BYTES <= len(data) <= MAX_BYTES:
            raise FirmwareError("That doesn't look like a firmware image: wrong size.")
        if data[0] != ESP_MAGIC:
            raise FirmwareError("That isn't an ESP32 firmware image. Use a '-merged.bin' file.")

    def _run(self, source: dict | None, data: bytes | None, erase: bool) -> None:
        paused = False
        try:
            if data is None:
                self._set(state="downloading", text="Downloading the firmware...")
                try:
                    data = self.fetch(source["url"])
                except Exception as e:
                    raise FirmwareError(f"The download failed ({e}).") from None
                if source.get("size") and len(data) != source["size"]:
                    raise FirmwareError("The download is the wrong size; try again.")
                if source.get("sha256") and hashlib.sha256(data).hexdigest() != source["sha256"]:
                    raise FirmwareError("The download doesn't match its checksum; try again.")
                self._check_image(data)
            port = self.node.serial_device()
            with tempfile.TemporaryDirectory(prefix="simplemesh-fw-") as tmp:
                path = Path(tmp) / "firmware.bin"
                path.write_bytes(data)
                self._set(state="flashing", text="Letting go of the node...")
                self.node.pause()
                paused = True
                steps = ([("Erasing the node", ["--port", port, "erase-flash"])] if erase else []) + \
                        [("Writing the firmware", ["--port", port, "--baud", str(FLASH_BAUD), "write-flash", "0x0", str(path)])]
                for text, cmd in steps:
                    self._set(text=text + "...", percent=0)
                    rc = self.flasher(cmd, self._on_line)
                    if rc != 0:
                        raise FirmwareError(self._failure())
            self._set(state="done", percent=100,
                      text="Done. The node is restarting and should reconnect in a few seconds.")
        except FirmwareError as e:
            self._set(state="failed", text=str(e))
        except Exception as e:  # MeshError from pause(), or esptool vanishing
            self._set(state="failed", text=str(e) or type(e).__name__)
        finally:
            if paused or self.node._held:
                self.node.resume()

    def _on_line(self, line: str) -> None:
        m = PERCENT.search(line)
        if m:  # a progress update, not worth a line of the log
            pct = min(100, int(float(m.group(1))))
            if pct != self._state["percent"]:
                self._set(percent=pct)
            return
        self._log(line)

    def _failure(self) -> str:
        tail = " ".join(self.state()["log"][-3:])
        hint = ""
        low = tail.lower()
        if "failed to connect" in low or "no serial data" in low or "timed out" in low:
            hint = " Try again; if it keeps failing, hold the node's BOOT button while plugging it in."
        elif "permission" in low:
            hint = " This user can't open the serial port (is it in the dialout group?)."
        return f"esptool couldn't finish: {tail[-220:] or 'no output'}.{hint}"
