"""Reaching the repeaters, room servers and sensors your node has saved: logging in, asking how they are, running their
command line. Each call goes out over the mesh and waits for the answer, so they take seconds, and can fail simply
because the other node is out of range. Passwords are only passed along, never kept or logged.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from meshcore import EventType

if TYPE_CHECKING:
    from .node import MeshNode

ADMIN_TYPES = (2, 3, 4)
REPLY_WAIT_S = 30
PATH_WAIT_S = 30  # a route request goes out and back over every hop: the node's own estimate is a few seconds, which far nodes overrun
CALL_WAIT_S = 45


class Remote:
    def __init__(self, node: "MeshNode") -> None:
        self.node = node
        self.logged: dict[str, dict] = {}      # key -> {"admin": bool}; for as long as this page server runs
        self._waiting: dict[str, asyncio.Future] = {}
        self._turn: asyncio.Lock | None = None  # one command at a time: replies carry nothing to match them by

    def _error(self, text: str):
        from .node import MeshError
        return MeshError(text)

    def _contact(self, key: str, any_type: bool = False) -> dict:
        n = self.node.store.node(key)
        if not n or not n.get("saved"):
            raise self._error("Save that node on your node first.")
        if not any_type and n.get("type") not in ADMIN_TYPES:
            raise self._error("Only repeaters, room servers and sensors can be managed this way.")
        c = self.node.mc.contacts.get(key) if self.node.mc else None
        if not c:
            raise self._error("Your node doesn't have that contact right now.")
        return c

    def _lock(self) -> asyncio.Lock:
        if self._turn is None:
            self._turn = asyncio.Lock()
        return self._turn

    def reply(self, prefix: str, text: str) -> None:
        """A command's answer arrived (on the node's loop)."""
        fut = self._waiting.get(prefix)
        if fut and not fut.done():
            fut.set_result(text)

    def state(self, key: str) -> dict:
        return {"logged_in": key in self.logged, "admin": bool(self.logged.get(key, {}).get("admin"))}

    # --- what the page asks for ---------------------------------------------------------------------

    def login(self, key: str, password: str) -> dict:
        async def go() -> dict:
            res = await self.node.mc.commands.send_login_sync(self._contact(key), password)
            if res is None or res.type != EventType.LOGIN_SUCCESS:
                raise self._error("No login answer: the password is wrong, or the node is out of range or off.")
            self.logged[key] = {"admin": bool(res.payload.get("is_admin"))}
            return self.state(key)
        return self.node.request(go, CALL_WAIT_S)

    def logout(self, key: str) -> None:
        async def go() -> None:
            await self.node.mc.commands.send_logout(self._contact(key))
            self.logged.pop(key, None)
        self.node.request(go)

    def status(self, key: str) -> dict:
        async def go() -> dict:
            res = await self.node.mc.commands.req_status_sync(self._contact(key))
            if not res:
                raise self._error("No answer. It may be out of range, or want you to log in first.")
            return res
        return self.node.request(go, CALL_WAIT_S)

    def neighbours(self, key: str) -> dict:
        async def go() -> dict:
            res = await self.node.mc.commands.req_neighbours_sync(self._contact(key))
            if not res:
                raise self._error("No answer. It may be out of range, or want you to log in first.")
            return res
        return self.node.request(go, CALL_WAIT_S)

    def telemetry(self, key: str) -> list:
        async def go() -> list:
            res = await self.node.mc.commands.req_telemetry_sync(self._contact(key))
            if res is None:
                raise self._error("No answer. It may be out of range, or not share telemetry with you.")
            return res
        return self.node.request(go, CALL_WAIT_S)

    def acl(self, key: str) -> list:
        async def go() -> list:
            res = await self.node.mc.commands.req_acl_sync(self._contact(key))
            if res is None:
                raise self._error("No answer. Reading the access list needs an admin login.")
            return res
        return self.node.request(go, CALL_WAIT_S)

    def route(self, key: str) -> dict:
        """The path a message takes to this node and back, found by asking the mesh."""
        async def go() -> dict:
            c = self._contact(key, any_type=True)
            res = await self.node.mc.commands.send_path_discovery_sync(c, timeout=PATH_WAIT_S)
            if res is None or res.type == EventType.ERROR:
                if c.get("type") in ADMIN_TYPES:
                    raise self._error(f"No answer in {PATH_WAIT_S} seconds. Repeaters, room servers and sensors tend to answer a route "
                                      "request only from someone logged in to them: log in (Manage) and try again. It may also be out of range.")
                raise self._error(f"No answer in {PATH_WAIT_S} seconds: it may be out of range or offline, or not accept requests.")
            return res.payload
        return self.node.request(go, CALL_WAIT_S)

    def command(self, key: str, text: str) -> str:
        text = text.strip()
        if not text:
            raise self._error("Type a command first.")
        if len(text.encode()) > 140:
            raise self._error("That command is too long for one message.")

        async def go() -> str:
            c = self._contact(key)
            async with self._lock():
                fut = asyncio.get_running_loop().create_future()
                self._waiting[key[:12]] = fut
                try:
                    res = await self.node.mc.commands.send_cmd(c, text)
                    self.node._ok(res, "send that command")
                    return await asyncio.wait_for(fut, REPLY_WAIT_S)
                except asyncio.TimeoutError:
                    raise self._error("No reply. Some commands (reboot, for one) never answer; otherwise it is "
                                      "out of range, or you need to log in as admin first.") from None
                finally:
                    self._waiting.pop(key[:12], None)
        return self.node.request(go, CALL_WAIT_S + REPLY_WAIT_S)
