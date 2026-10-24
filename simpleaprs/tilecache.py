"""Disk cache for the proxied map server.

Entries are keyed by (path, Range header), which is exactly what the PMTiles client asks for, so a
tile fetched once is served from disk afterwards, including when the map server is unreachable.
A file changing on the server (ETag or total size differs) drops everything cached for that path.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path

DEFAULT_DIR = Path.home() / ".cache" / "simpleaprs" / "tiles"
MAX_ENTRY = 16 * 1024 * 1024  # larger responses are streamed through, not cached


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:24]


class TileCache:
    def __init__(self, root: Path = DEFAULT_DIR, max_bytes: int = 500 * 1024 * 1024) -> None:
        self.root, self.max_bytes = Path(root), max_bytes
        self._lock = threading.Lock()
        self._versions: dict[str, str] = {}
        try:
            self._versions = json.loads((self.root / "versions.json").read_text())
        except (OSError, ValueError):
            pass
        self.size = self._scan()

    def _scan(self) -> int:
        return sum(f.stat().st_size for f in self.root.glob("*/*") if f.is_file()) if self.root.is_dir() else 0

    def _file(self, path: str, rng: str) -> Path:
        return self.root / _h(path.split("?")[0]) / _h(path + "|" + rng)

    def get(self, path: str, rng: str = "") -> tuple[int, dict, bytes] | None:
        f = self._file(path, rng)
        try:
            raw = f.read_bytes()
            meta, _, body = raw.partition(b"\n")
            m = json.loads(meta)
            self._touch(f)  # most recently used
            return m["status"], m["headers"], body
        except (OSError, ValueError, KeyError):
            return None

    @staticmethod
    def _touch(f: Path) -> None:
        # Explicit nanosecond time: the filesystem's own coarse clock can tie with a neighbour written a moment ago.
        t = time.time_ns()
        try:
            os.utime(f, ns=(t, t))
        except OSError:
            pass

    def put(self, path: str, rng: str, status: int, headers: dict, body: bytes) -> None:
        total = headers.get("Content-Range", "").rpartition("/")[2] or headers.get("Content-Length", "")
        version = f"{headers.get('ETag', '')}|{total}" if rng else headers.get("ETag", "")
        f = self._file(path, rng)
        with self._lock:
            key = _h(path.split("?")[0])
            old = self._versions.get(key)
            if old is not None and old != version:  # the file changed on the server: nothing cached for it is valid
                shutil.rmtree(self.root / key, ignore_errors=True)
                self.size = self._scan()
            if old != version:
                self._versions[key] = version
                self._save_versions()
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                tmp = f.with_name(f.name + ".tmp")
                data = json.dumps({"status": status, "headers": headers}).encode() + b"\n" + body
                prev = f.stat().st_size if f.exists() else 0
                tmp.write_bytes(data)
                os.replace(tmp, f)
                self._touch(f)
                self.size += len(data) - prev
            except OSError:
                return
            if self.size > self.max_bytes:
                self._evict()

    def _save_versions(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "versions.json").write_text(json.dumps(self._versions))
        except OSError:
            pass

    def _evict(self) -> None:
        """Delete least recently used entries until we are at 90% of the limit."""
        files = []
        for f in self.root.glob("*/*"):
            try:
                st = f.stat()
                files.append((st.st_mtime, st.st_size, f))
            except OSError:
                pass
        files.sort()
        target = self.max_bytes * 0.9
        total = sum(s for _, s, _ in files)
        for _, sz, f in files:
            if total <= target:
                break
            try:
                f.unlink()
                total -= sz
            except OSError:
                pass
        self.size = total

    def trim(self) -> None:
        with self._lock:
            if self.size > self.max_bytes:
                self._evict()

    def clear(self) -> None:
        with self._lock:
            shutil.rmtree(self.root, ignore_errors=True)
            self._versions.clear()
            self.size = 0
