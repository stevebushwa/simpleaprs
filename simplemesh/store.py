"""What the node has told us, kept across restarts: the nodes we know of (with when we last heard them, which the
node's own clock can't be trusted for) and the message history. Plain JSON files, written at most every few seconds.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

MAX_MESSAGES = 2000
MAX_NODES = 1000


class Store:
    def __init__(self, nodes_path: Path, messages_path: Path) -> None:
        self.nodes_path, self.messages_path = Path(nodes_path), Path(messages_path)
        self.lock = threading.RLock()
        self.nodes: dict[str, dict] = {}   # public key (hex) -> node
        self.messages: list[dict] = []     # oldest first
        self._ids: set[str] = set()
        self._dirty = False
        for path, load in ((self.nodes_path, self._load_nodes), (self.messages_path, self._load_messages)):
            try:
                load(json.loads(path.read_text()))
            except (OSError, ValueError):
                pass

    def _load_nodes(self, raw) -> None:
        if isinstance(raw, dict):
            self.nodes = {k: v for k, v in raw.items() if isinstance(v, dict)}

    def _load_messages(self, raw) -> None:
        if isinstance(raw, list):
            self.messages = [m for m in raw if isinstance(m, dict) and "id" in m]
            self._ids = {m["id"] for m in self.messages}

    # --- nodes --------------------------------------------------------------------------------------

    def put_node(self, key: str, fields: dict) -> dict:
        """Merge fields into the node and return the result."""
        with self.lock:
            node = self.nodes.setdefault(key, {"key": key})
            node.update(fields)
            if len(self.nodes) > MAX_NODES:  # forget the ones not heard for longest, unsaved ones first
                worst = sorted(self.nodes.values(), key=lambda n: (n.get("saved", False), n.get("heard", 0)))
                for n in worst[:len(self.nodes) - MAX_NODES]:
                    del self.nodes[n["key"]]
            self._dirty = True
            return dict(node)

    def drop_node(self, key: str) -> bool:
        with self.lock:
            self._dirty = True
            return self.nodes.pop(key, None) is not None

    def node(self, key: str) -> dict | None:
        with self.lock:
            n = self.nodes.get(key)
            return dict(n) if n else None

    def find(self, prefix: str) -> dict | None:
        """The one node whose key starts with prefix, or None if there is no such node or it is ambiguous."""
        prefix = prefix.lower()
        with self.lock:
            hits = [n for k, n in self.nodes.items() if k.startswith(prefix)]
            return dict(hits[0]) if len(hits) == 1 else None

    def all_nodes(self) -> list[dict]:
        with self.lock:
            return [dict(n) for n in self.nodes.values()]

    # --- messages -----------------------------------------------------------------------------------

    def add_message(self, msg: dict) -> bool:
        """Keep msg. False if one with the same id is already there (the same packet heard twice)."""
        with self.lock:
            if msg["id"] in self._ids:
                return False
            self.messages.append(dict(msg))
            self._ids.add(msg["id"])
            if len(self.messages) > MAX_MESSAGES:
                for m in self.messages[:len(self.messages) - MAX_MESSAGES]:
                    self._ids.discard(m["id"])
                del self.messages[:len(self.messages) - MAX_MESSAGES]
            self._dirty = True
            return True

    def update_message(self, mid: str, **fields) -> dict | None:
        with self.lock:
            for m in reversed(self.messages):
                if m["id"] == mid:
                    m.update(fields)
                    self._dirty = True
                    return dict(m)
        return None

    def recent_messages(self, n: int = 400) -> list[dict]:
        with self.lock:
            return [dict(m) for m in self.messages[-n:]]

    # --- disk ---------------------------------------------------------------------------------------

    def save(self, force: bool = False) -> None:
        with self.lock:
            if not (self._dirty or force):
                return
            nodes, messages = json.dumps(self.nodes), json.dumps(self.messages)
            self._dirty = False
        for path, text in ((self.nodes_path, nodes), (self.messages_path, messages)):
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(text)
                os.replace(tmp, path)
            except OSError:
                self._dirty = True
