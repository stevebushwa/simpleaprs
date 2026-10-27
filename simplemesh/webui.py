"""The page, as netshare.ShareServer serves it (its `web` argument): the same login, map proxy and event stream as
simpleaprs's page, with MeshCore's own requests on top. Replies are JSON; a request the node can't do answers 409
with the reason, which the page shows.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from simpleaprs.mapserver import WEB as APRS_WEB
from simpleaprs.mapserver import MapProxy
from simpleaprs.netshare import Hub
from simpleaprs.webui import WebUI

from .config import Config, home_position, map_server
from .firmware import FirmwareError
from .node import BANDWIDTHS, PRESETS, MeshError, MeshNode, all_ports, candidate_ports

WEB = Path(__file__).parent / "web"
KEY = re.compile(r"^[0-9a-f]{64}$")
PREFIX = re.compile(r"^[0-9a-f]{12}$")
BAUDS = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
UPLOAD_JSON_BYTES = 6 * 1024 * 1024  # a firmware file of up to 4 MiB, as base64 in JSON


class MeshWeb(WebUI):
    cookie_name = "simplemesh_session"

    def __init__(self, hub: Hub, node: MeshNode, proxy: MapProxy, auth, cfg: Config) -> None:
        super().__init__(hub, None, proxy, True, auth)  # type: ignore[arg-type]  # no APRS tracker here
        self.node, self.cfg = node, cfg
        self.posts = {"/api/send": self._send, "/api/advert": self._advert, "/api/profile": self._profile,
                      "/api/node/add": self._node_add, "/api/node/remove": self._node_remove,
                      "/api/node/reset-path": self._node_reset, "/api/settings": self._save_settings,
                      "/api/radio": self._radio, "/api/privacy": self._privacy, "/api/node/reboot": self._reboot,
                      "/api/stats": lambda d: self.node.stats(),
                      "/api/channel/set": self._channel_set, "/api/channel/remove": self._channel_remove,
                      "/api/identity/export": lambda d: {"key": self.node.export_key()},
                      "/api/identity/import": lambda d: self.node.import_key(str(d["key"])),
                      "/api/contact/export": self._contact_export, "/api/contact/import": self._contact_import,
                      "/api/contact/share": lambda d: self.node.share_contact(self._key(d)),
                      "/api/admin/state": lambda d: self.node.remote.state(self._key(d)),
                      "/api/admin/login": lambda d: self.node.remote.login(self._key(d), str(d.get("password", ""))),
                      "/api/admin/logout": lambda d: self.node.remote.logout(self._key(d)),
                      "/api/admin/status": lambda d: {"status": self.node.remote.status(self._key(d))},
                      "/api/admin/neighbours": lambda d: {"neighbours": self.node.remote.neighbours(self._key(d))},
                      "/api/admin/telemetry": lambda d: {"telemetry": self.node.remote.telemetry(self._key(d))},
                      "/api/admin/acl": lambda d: {"acl": self.node.remote.acl(self._key(d))},
                      "/api/admin/route": lambda d: {"route": self.node.remote.route(self._key(d))},
                      "/api/admin/command": lambda d: {"reply": self.node.remote.command(self._key(d), str(d.get("text", "")))},
                      "/api/firmware/check": lambda d: {"release": self.node.firmware.check()},
                      "/api/firmware/flash": self._flash, "/api/firmware/upload": self._flash_upload}
        self.limits = {"/api/firmware/upload": UPLOAD_JSON_BYTES}

    def intro(self) -> list[dict]:
        return self.node.intro()

    # --- GET ----------------------------------------------------------------------------------------

    def handle(self, h) -> bool:
        path = urlparse(h.path).path
        if path == "/login":
            self.proxy._send(h, 200, (APRS_WEB / "login.html").read_bytes().replace(b"simpleaprs", b"simplemesh"), "text/html")
        elif path == "/api/auth/state":
            h._reply(200, {"setup": not self.auth.configured, "logged_in": self.session_ok(h)})
        elif not self.session_ok(h):
            if path == "/" or path.endswith(".html"):
                h.send_response(302)
                h.send_header("Location", "/login")
                h.send_header("Content-Length", "0")
                h.end_headers()
            else:
                h._reply(401, {"error": "login required"})
        elif path == "/api/settings":
            h._reply(200, self.settings_view())
        elif path == "/api/account":
            h._reply(200, {"username": self.auth.username})
        else:
            return self.proxy.handle(h, f"http://{h.headers.get('Host', '')}", index="index.html")
        return True

    def settings_view(self) -> dict:
        home = home_position()
        return {"values": {"serial_port": self.cfg.serial_port, "baud": self.cfg.baud, "nomad_url": self.cfg.nomad_url},
                "ports": all_ports(), "detected": candidate_ports(), "bauds": BAUDS, "map_server": self.proxy.nomad,
                "home": list(home) if home else None, "presets": PRESETS, "bandwidths": BANDWIDTHS}

    # --- POST ---------------------------------------------------------------------------------------

    def handle_post(self, h) -> bool:
        fn = self.posts.get(h.path)
        if not fn:
            if h.path == "/api/forget-inactive":  # simpleaprs's, which needs its tracker
                return False
            return super().handle_post(h)  # the login, logout and password endpoints
        if not self.session_ok(h):
            h._reply(401, {"error": "login required"})
            return True
        d = h.read_json(self.limits.get(h.path, 4096))
        if isinstance(d, dict):
            try:
                h._reply(200, fn(d) or {"ok": True})
            except (MeshError, FirmwareError) as e:
                h._reply(409, {"error": str(e)})
            except (KeyError, TypeError, ValueError):
                h._reply(400, {"error": "bad request"})
        return True

    @staticmethod
    def _key(d: dict, pattern: re.Pattern = KEY, name: str = "key") -> str:
        v = str(d.get(name, "")).lower()
        if not pattern.match(v):
            raise ValueError(name)
        return v

    def _send(self, d: dict) -> dict:
        text = str(d.get("text", ""))
        if d.get("kind") == "chan":
            return {"msg": self.node.send_channel(int(d["chan"]), text)}
        if d.get("kind") == "dm":
            return {"msg": self.node.send_dm(self._key(d, PREFIX, "to"), text)}
        raise ValueError("kind")

    def _advert(self, d: dict) -> None:
        self.node.advertise(bool(d.get("flood")))

    def _profile(self, d: dict) -> None:
        self.node.set_profile(str(d["name"]), float(d["lat"]), float(d["lon"]))

    def _node_add(self, d: dict) -> None:
        self.node.add_node(self._key(d))

    def _node_remove(self, d: dict) -> None:
        self.node.remove_node(self._key(d))

    def _node_reset(self, d: dict) -> None:
        self.node.reset_path(self._key(d))

    def _radio(self, d: dict) -> None:
        self.node.set_radio(float(d["freq"]), float(d["bw"]), int(d["sf"]), int(d["cr"]), int(d["tx_power"]))

    def _privacy(self, d: dict) -> None:
        self.node.set_privacy(bool(d["manual_add"]), int(d["tel_base"]), int(d["tel_loc"]), int(d["tel_env"]), int(d["loc_policy"]))

    def _reboot(self, d: dict) -> None:
        self.node.reboot()

    def _channel_set(self, d: dict) -> None:
        self.node.set_channel(int(d["idx"]), str(d["name"]), str(d.get("secret", "")))

    def _channel_remove(self, d: dict) -> None:
        self.node.remove_channel(int(d["idx"]))

    def _contact_export(self, d: dict) -> dict:
        return {"uri": self.node.export_contact(self._key(d) if d.get("key") else None)}

    def _contact_import(self, d: dict) -> None:
        self.node.import_contact(str(d["uri"]))

    def _flash(self, d: dict) -> None:
        self.node.firmware.start(image=str(d["image"]), erase=bool(d.get("erase")))

    def _flash_upload(self, d: dict) -> None:
        self.node.firmware.start(upload=(str(d.get("name", ""))[:80], str(d["data"])), erase=bool(d.get("erase")))

    def _save_settings(self, d: dict) -> None:
        port, baud, url = str(d.get("serial_port", "")).strip(), int(d.get("baud", 0)), str(d.get("nomad_url", "")).strip()
        if port and not port.startswith("/dev/"):
            raise MeshError("The serial port is a device path such as /dev/ttyACM0, or blank to find it automatically.")
        if baud not in BAUDS:
            raise MeshError("Pick one of the listed baud rates (the companion firmware uses 115200).")
        if url and not re.match(r"^https?://[^\s/]+", url):
            raise MeshError("The map server is a web address, like http://localhost:8080.")
        moved = (port, baud) != (self.cfg.serial_port, self.cfg.baud)
        self.cfg.serial_port, self.cfg.baud, self.cfg.nomad_url = port, baud, url
        self.cfg.save()
        self.proxy.nomad = map_server(self.cfg).rstrip("/")
        if moved:
            self.node.reconnect()
